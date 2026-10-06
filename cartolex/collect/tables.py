# SPDX-License-Identifier: MIT
"""Source writers: collected records → the six source tables, rebuilt from the raw records.

Collection never writes a table row directly. Every finder stores what it
received in the slot's raw folder, ``sources/<slot>/raw/<kind>/<run id>.jsonl.gz``
(gzip-compressed JSON lines: a header line, then one record per line, written
whole or not at all; a run written before is a plain ``.jsonl``, read the same
way), and
:func:`rebuild_sources` turns every raw record of every slot into the tables of
``docs/format/sources.md``. Rebuilding twice from the same raw records gives
the same bytes.

**Ids.** Texts, people and organisations get ids ``t000001``, ``p000001``,
``o000001`` once, from natural keys (a DOI, a service record, an imported row),
and keep them: the :class:`IdRegistry` of each slot, ``sources/<slot>/raw/ids.json``,
remembers every key it has seen and never gives a number twice, so merging a
new collection into the tables never renumbers anything. Rows whose ids no
registry gave (a project written by another tool, the demo project) are kept
as they are.

**Readers.** Each kind of raw folder has a reader (:data:`Reader`) that feeds a
:class:`SourceBuilder`; :func:`default_readers` lists cartolex's, and a new
finder adds its own.
"""

from __future__ import annotations

import contextlib
import gzip
import heapq
import io
import itertools
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from cartolex.project.files import replace_path
from cartolex.project.layout import SOURCE_TABLES, ProjectLayout
from cartolex.project.models import ProjectFile
from cartolex.project.tables import (
    ROW_GROUP,
    read_decision_csv,
    source_key,
    write_source_rows,
)
from cartolex.scale import Budget

if TYPE_CHECKING:
    from .workstore import WorkStore

__all__ = [
    "IDS_FORMAT",
    "LEGACY_IDS_FORMAT",
    "RAW_FORMAT",
    "RAW_SUFFIX",
    "IdRegistry",
    "RawRun",
    "RawWriter",
    "Reader",
    "RebuildReport",
    "SourceBuilder",
    "default_readers",
    "new_run_id",
    "open_run",
    "raw_folder",
    "read_runs",
    "rebuild_sources",
]

RAW_FORMAT = "cartolex-raw/1"
#: A run file's name after its id: gzip-compressed JSON lines. Runs written before are plain
#: ``.jsonl`` files, read the same way.
RAW_SUFFIX = ".jsonl.gz"
_RUN_SUFFIXES = (RAW_SUFFIX, ".jsonl")
IDS_FORMAT = "cartolex-ids/2"
#: The format of a registry written before (``ids.json``): read, and replaced when saved.
LEGACY_IDS_FORMAT = "cartolex-ids/1"
#: The tables whose rows get ids, and their prefix.
ID_PREFIX = {"texts": "t", "people": "p", "organisations": "o"}
ID_WIDTH = 6


def new_run_id(now: datetime | None = None) -> str:
    """``20260928T101200123456Z-3f2a1c``: the UTC time to the microsecond, and a random tail."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{secrets.token_hex(3)}"


def raw_folder(layout: ProjectLayout, slot: str) -> Path:
    """``sources/<slot>/raw/``: everything a slot's tables are rebuilt from."""
    return layout.slot(slot) / "raw"


def iso(ts: datetime) -> str:
    """A UTC time as stored in raw records: ``2026-09-28T10:12:00.123456+00:00``."""
    return ts.astimezone(timezone.utc).isoformat()


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    ts = datetime.fromisoformat(text)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


# ── raw runs ─────────────────────────────────────────────────────────────────


def run_files(folder: Path) -> list[tuple[str, Path]]:
    """The run files of a kind's folder, compressed or plain, with their ids, in id order."""
    out = []
    for path in folder.iterdir() if folder.is_dir() else ():
        name = path.name
        if name.startswith("."):  # a run being written
            continue
        suffix = next((s for s in _RUN_SUFFIXES if name.endswith(s)), None)
        if suffix is not None and path.is_file():
            out.append((name[: -len(suffix)], path))
    return sorted(out)


def open_run(path: Path) -> IO[str]:
    """A run file opened for reading as text, decompressed when it is compressed."""
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


class RawWriter:
    """Writes one raw run: a header line, then records; nothing is visible until :meth:`close`.

    Records go, compressed, to a temporary file in the target folder; :meth:`close`
    writes the header (which may still change until then, :attr:`header`) and the
    records into place in one rename, so a cancelled or failed job leaves no
    partial run behind. The header is a gzip member of its own and the records' member
    is copied after it as it is: the file reads as one stream. Use it as a context
    manager: an exception discards the run.
    """

    def __init__(
        self,
        layout: ProjectLayout,
        slot: str,
        kind: str,
        header: Mapping[str, Any],
        *,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        self.run_id = run_id or new_run_id(now)
        self.folder = raw_folder(layout, slot) / kind
        self.folder.mkdir(parents=True, exist_ok=True)
        # Runs are read in the order of their ids: a new run always comes after the others,
        # even when the clock gives the same time twice or goes back.
        latest = max((run_id for run_id, _path in run_files(self.folder)), default="")
        if self.run_id <= latest:
            self.run_id = latest + "0"
        self.path = self.folder / f"{self.run_id}{RAW_SUFFIX}"
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".body", dir=self.folder)
        self._body = Path(tmp)
        self._raw = os.fdopen(fd, "wb")
        self._fh = io.TextIOWrapper(
            gzip.GzipFile(fileobj=self._raw, mode="wb", compresslevel=6, mtime=0),
            encoding="utf-8",
            newline="\n",
        )
        self.count = 0
        #: The header line, written when the run is closed.
        self.header: dict[str, Any] = {
            "format": RAW_FORMAT,
            "kind": kind,
            "run_id": self.run_id,
            **header,
        }

    @staticmethod
    def _line(obj: Mapping[str, Any]) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"

    def add(self, record: Mapping[str, Any]) -> None:
        """Append one record."""
        self._fh.write(self._line(record))
        self.count += 1

    def add_line(self, line: str) -> None:
        """Append one record already written as :meth:`line` writes it (in a worker)."""
        self._fh.write(line)
        self.count += 1

    @staticmethod
    def line(record: Mapping[str, Any]) -> str:
        """A record as a run holds it: one line of JSON, keys sorted."""
        return RawWriter._line(record)

    def close(self) -> Path:
        """Write the header and the records into place; returns the run's path."""
        if self._fh.closed and not self._body.exists():
            return self.path  # discarded: nothing to write
        self._fh.close()
        self._raw.close()
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.folder)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(gzip.compress(self._line(self.header).encode("utf-8"), 6, mtime=0))
                with open(self._body, "rb") as body:
                    shutil.copyfileobj(body, out, 1 << 20)
                out.flush()
                os.fsync(out.fileno())
            replace_path(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        finally:
            self._body.unlink(missing_ok=True)
        return self.path

    def discard(self) -> None:
        with contextlib.suppress(Exception):
            self._fh.close()
        with contextlib.suppress(Exception):
            self._raw.close()
        self._body.unlink(missing_ok=True)

    def __enter__(self) -> RawWriter:
        return self

    def __exit__(self, exc_type: object, *rest: object) -> None:
        if exc_type is None and self._body.exists():
            self.close()
        else:
            self.discard()


@dataclass(frozen=True)
class RawRun:
    """One raw run file: its slot, kind, id, header, and its records (read on demand).

    With a *digests* cache (:class:`cartolex.collect.digests.DigestCache`), the
    records of a digested kind come from the run's digest.
    """

    path: Path
    slot: str
    kind: str
    run_id: str
    header: dict[str, Any]
    digests: Any = field(default=None, compare=False, repr=False)

    def records(self) -> Iterator[dict[str, Any]]:
        if self.digests is not None:
            from .digests import DIGESTERS

            if self.kind in DIGESTERS:
                return self.digests.records(self)
        return self.raw_records()

    def raw_records(self) -> Iterator[dict[str, Any]]:
        """The records as the run holds them."""
        with open_run(self.path) as fh:
            next(fh, None)
            for n, line in enumerate(fh, start=2):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{self.path}, line {n}: not valid JSON ({exc})") from exc


def read_runs(
    layout: ProjectLayout, slot: str, kind: str | None = None, *, digests: Any = None
) -> list[RawRun]:
    """The runs of *slot* (of one *kind*, or all), in time order; with *digests*, the records
    of digested kinds are read from their digests."""
    root = raw_folder(layout, slot)
    if not root.is_dir():
        return []
    kinds = [kind] if kind else sorted(p.name for p in root.iterdir() if p.is_dir())
    runs: list[RawRun] = []
    for k in kinds:
        folder = root / k
        if not folder.is_dir():
            continue
        for run_id, path in run_files(folder):
            with open_run(path) as fh:
                first = fh.readline()
            try:
                header = json.loads(first)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}: the header line is not valid JSON ({exc})") from exc
            if header.get("format") != RAW_FORMAT:
                raise ValueError(f"{path}: not a raw run (format {header.get('format')!r})")
            runs.append(RawRun(path, slot, k, run_id, header, digests))
    return sorted(runs, key=lambda r: (r.run_id, r.kind))


# ── ids ──────────────────────────────────────────────────────────────────────

_IDS_SCHEMA = pa.schema([("table", pa.string()), ("key", pa.string()), ("id", pa.string())])
_REG_TABLE = (
    "CREATE TABLE IF NOT EXISTS reg_keys (tbl TEXT NOT NULL, key TEXT NOT NULL,"
    " slot TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY (tbl, key, slot)) WITHOUT ROWID"
)


def id_number(table: str, value: str) -> int | None:
    """The number of an id of the registry's form (``t000042`` → 42), else ``None``."""
    digits = value[1:]
    if value[:1] != ID_PREFIX[table] or not (digits.isascii() and digits.isdigit()):
        return None
    number = int(digits)
    return number if f"{number:0{ID_WIDTH}d}" == digits else None


class IdRegistry:
    """The ids of every slot's registry: natural keys → ids, given once, never reused.

    Texts belong to one slot, so a text key is looked up in its slot's registry
    only; people and organisations are shared by every slot. A new id takes the
    next number after every number any registry ever gave, skipping ids already
    present in the tables.

    A slot's registry is ``sources/<slot>/raw/ids.parquet`` (``cartolex-ids/2``): one row
    per key (``table``, ``key``, ``id``) sorted by table and key, the next numbers in the
    file's metadata. While the registry is open its keys live in an SQLite table (*db*:
    a rebuild's scratch database, or one in memory), each table's loaded when first
    needed: millions of keys cost disk, not memory. A registry of the first format
    (``ids.json``) is read, and replaced when the registry is saved.
    """

    def __init__(
        self,
        layout: ProjectLayout,
        slots: Sequence[str],
        *,
        db: sqlite3.Connection | None = None,
    ) -> None:
        from .workstore import connect

        self.layout = layout
        self.slots = list(slots)
        self._db = db if db is not None else connect(":memory:")
        self._db.execute(_REG_TABLE)
        self._next: dict[str, dict[str, int]] = {s: dict.fromkeys(ID_PREFIX, 1) for s in self.slots}
        self._loaded: set[tuple[str, str]] = set()
        self._changed: set[str] = set()
        self._given: dict[str, bytearray] = {}
        self._given_other: dict[str, set[str]] = {t: set() for t in ID_PREFIX}
        for slot in self.slots:
            path = self.path(slot)
            if path.exists():
                meta = pq.read_schema(path).metadata or {}
                fmt = meta.get(b"format", b"").decode()
                if fmt != IDS_FORMAT:
                    raise ValueError(f"{path}: not an id registry (format {fmt!r})")
                self._advance(slot, json.loads(meta.get(b"next") or b"{}"))
            elif self.legacy_path(slot).exists():
                self._load_legacy(slot)

    def path(self, slot: str) -> Path:
        return raw_folder(self.layout, slot) / "ids.parquet"

    def legacy_path(self, slot: str) -> Path:
        """A registry of the first format (``ids.json``), replaced when the registry is saved."""
        return raw_folder(self.layout, slot) / "ids.json"

    def _advance(self, slot: str, numbers: Mapping[str, Any]) -> None:
        for table, n in numbers.items():
            if table in ID_PREFIX:
                self._next[slot][table] = max(self._next[slot][table], int(n))

    def _insert(self, rows: Iterable[tuple[str, str, str, str]]) -> int:
        cur = self._db.executemany("INSERT OR IGNORE INTO reg_keys VALUES (?, ?, ?, ?)", rows)
        return cur.rowcount

    def _load_legacy(self, slot: str) -> None:
        path = self.legacy_path(slot)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != LEGACY_IDS_FORMAT:
            raise ValueError(f"{path}: not an id registry (format {data.get('format')!r})")
        self._advance(slot, data.get("next") or {})
        for table, keys in (data.get("keys") or {}).items():
            if table in ID_PREFIX:
                self._insert((table, k, slot, v) for k, v in keys.items())
        self._loaded.update((slot, t) for t in ID_PREFIX)
        self._changed.add(slot)  # written in the new format at the next save

    def _ensure(self, table: str) -> None:
        """Load *table*'s keys of every slot, the first time they are needed."""
        for slot in self.slots:
            if (slot, table) in self._loaded:
                continue
            self._loaded.add((slot, table))
            path = self.path(slot)
            if not path.exists():
                continue
            pf = pq.ParquetFile(path)
            for i in range(pf.num_row_groups):
                stats = pf.metadata.row_group(i).column(0).statistics
                if stats is not None and stats.has_min_max and not stats.min <= table <= stats.max:
                    continue
                group = pf.read_row_group(i, columns=["table", "key", "id"])
                group = group.filter(pc.equal(group["table"], table))
                keys, ids = group["key"].to_pylist(), group["id"].to_pylist()
                self._insert((table, k, slot, v) for k, v in zip(keys, ids, strict=True))

    def _mark(self, table: str, value: str) -> None:
        number = id_number(table, value)
        if number is None:
            self._given_other[table].add(value)
            return
        mask = self._given[table]
        if number >= len(mask):
            mask.extend(bytes(max(number + 1 - len(mask), len(mask))))
        mask[number] = 1

    def is_given(self, table: str, value: str) -> bool:
        """Whether some registry gave id *value* of *table*."""
        if table not in self._given:
            self._ensure(table)
            self._given[table] = bytearray(1024)
            for (given,) in self._db.execute("SELECT id FROM reg_keys WHERE tbl = ?", (table,)):
                self._mark(table, given)
        number = id_number(table, value)
        if number is None:
            return value in self._given_other[table]
        mask = self._given[table]
        return number < len(mask) and bool(mask[number])

    def given(self, table: str) -> set[str]:
        """Every id of *table* some registry gave (a set: see :meth:`is_given` for one id)."""
        self._ensure(table)
        rows = self._db.execute("SELECT DISTINCT id FROM reg_keys WHERE tbl = ?", (table,))
        return {v for (v,) in rows}

    def lookup(self, table: str, keys: Iterable[str], slot: str) -> str | None:
        """The id one of *keys* already has, or ``None``."""
        self._ensure(table)
        scopes = [slot] if table == "texts" else self.slots
        for key in keys:
            rows = self._db.execute(
                "SELECT slot, id FROM reg_keys WHERE tbl = ? AND key = ?", (table, key)
            ).fetchall()
            if rows:
                found = dict(rows)
                for s in scopes:
                    if found.get(s):
                        return found[s]
        return None

    def assign(
        self,
        table: str,
        keys: Sequence[str],
        slot: str,
        *,
        taken: set[str] | frozenset = frozenset(),
    ) -> str:
        """The id of the thing *keys* name: the one it has, or a new one; all keys are remembered."""
        if not keys:
            raise ValueError(f"a {table} record needs at least one key")
        found = self.lookup(table, keys, slot)
        if found is None:
            number = max(n[table] for n in self._next.values())
            while True:
                found = f"{ID_PREFIX[table]}{number:0{ID_WIDTH}d}"
                number += 1
                if found not in taken and not self.is_given(table, found):
                    break
            for n in self._next.values():
                n[table] = max(n[table], number)
            self._changed.update(self.slots)
            self._mark(table, found)
        if self._insert((table, key, slot, found) for key in keys):
            self._changed.add(slot)
        return found

    def save(self) -> None:
        """Write the registries that changed (in the new format, the old file removed)."""
        for slot in sorted(self._changed):
            for table in ID_PREFIX:
                self._ensure(table)
            self._write(slot)
            self.legacy_path(slot).unlink(missing_ok=True)
        self._changed.clear()

    def _write(self, slot: str) -> None:
        path = self.path(slot)
        path.parent.mkdir(parents=True, exist_ok=True)
        numbers = json.dumps(dict(sorted(self._next[slot].items())))
        schema = _IDS_SCHEMA.with_metadata({"format": IDS_FORMAT, "next": numbers})
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        os.close(fd)
        try:
            with pq.ParquetWriter(tmp, schema, compression="zstd") as writer:
                rows = self._db.execute(
                    "SELECT tbl, key, id FROM reg_keys WHERE slot = ? ORDER BY tbl, key", (slot,)
                )
                while batch := rows.fetchmany(ROW_GROUP):
                    columns = list(zip(*batch, strict=True))
                    writer.write_table(
                        pa.table([pa.array(c, pa.string()) for c in columns], schema=schema)
                    )
            with open(tmp, "rb") as fh:
                os.fsync(fh.fileno())
            replace_path(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


# ── the builder ──────────────────────────────────────────────────────────────


def _pairs(mapping: Mapping[str, Any] | None) -> list[tuple[str, Any]]:
    return sorted((str(k), v) for k, v in (mapping or {}).items())


def _flag(value: bool | None) -> int | None:
    return None if value is None else int(bool(value))


def _unflag(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def pack_text(content: str) -> bytes:
    """A part's content as the scratch database keeps it (compressed)."""
    return zlib.compress(content.encode("utf-8"), 1)


def unpack_text(blob: bytes) -> str:
    return zlib.decompress(blob).decode("utf-8")


@dataclass
class _Org:
    fields: dict[str, Any]
    parent_keys: list[str] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)


_UPSERT_AFFILIATION = """
INSERT INTO affiliations (pid, oid, source, start_year, end_year) VALUES (?, ?, ?, ?, ?)
ON CONFLICT (pid, oid, source) DO UPDATE SET
    start_year = CASE WHEN excluded.start_year IS NULL THEN start_year
                      WHEN start_year IS NULL THEN excluded.start_year
                      ELSE MIN(start_year, excluded.start_year) END,
    end_year = CASE WHEN excluded.end_year IS NULL THEN end_year
                    WHEN end_year IS NULL THEN excluded.end_year
                    ELSE MAX(end_year, excluded.end_year) END
"""


class SourceBuilder:
    """Collects rows for the six tables while the readers run, then merges and writes them.

    What grows with the records lives in the rebuild's scratch database (*store*, a
    :class:`~cartolex.collect.workstore.WorkStore`): every record of each text, the
    texts' parts, the authorships and the affiliations. People and organisations stay in
    memory. A part or an authorship stated twice keeps its first statement.
    """

    def __init__(
        self,
        layout: ProjectLayout,
        config: ProjectFile,
        registry: IdRegistry,
        store: WorkStore | None = None,
        *,
        jobs: int = 1,
    ) -> None:
        from .workstore import WorkStore

        self.layout = layout
        #: Worker processes a reader may use for a heavy run.
        self.jobs = jobs
        self.config = config
        self.registry = registry
        self.store = store if store is not None else WorkStore()
        self.db = self.store.db
        #: The tables as they were before this rebuild (their files), read when needed.
        self.existing: dict[str, Path] = {
            name: layout.table(name) for name in SOURCE_TABLES if layout.table(name).exists()
        }
        key_columns = {"texts": "text_id", "people": "person_id", "organisations": "org_id"}
        #: Ids of the old tables that no registry gave (rows written by another tool): kept as
        #: they are, and never given to a new row.
        self._taken: dict[str, set[str]] = {t: self._foreign(t, c) for t, c in key_columns.items()}
        #: People of the old tables that no registry gave an id to (kept as they are).
        self.foreign_people = self._taken["people"]
        self.people: dict[str, dict[str, Any]] = {}
        self.orgs: dict[str, _Org] = {}
        self._org_ids: dict[tuple[str, ...], str] = {}
        self._org_calls: dict[tuple[Any, ...], str] = {}
        self._org_last: dict[str, tuple[Any, ...]] = {}
        #: What readers keep between records, by name (an institution read once).
        self.memo: dict[str, dict[Any, Any]] = {}
        self.warnings: list[str] = []
        self.counts: dict[str, int] = {}
        #: The links finders stated between texts (``version_of_doi``).
        self.text_links: list[tuple[str, str, str]] = []
        self._orgs_version = 0
        self._words_version = -1
        self._words_index: dict[tuple[str, ...], set[str]] = {}
        self._existing_idx: dict[str, Any] | None = None

    def _foreign(self, table: str, column: str) -> set[str]:
        if table not in self.existing:
            return set()
        values = pq.read_table(self.existing[table], columns=[column])[column].to_pylist()
        return {v for v in values if v is not None and not self.registry.is_given(table, v)}

    def count(self, what: str, n: int = 1) -> None:
        self.counts[what] = self.counts.get(what, 0) + n

    def known_person(self, person_id: str) -> bool:
        """Whether *person_id* is a person of the tables (built here or kept from before)."""
        return person_id in self.people or person_id in self.foreign_people

    # ── rows ──
    def person(
        self,
        *,
        slot: str,
        keys: Sequence[str],
        last_name: str,
        first_name: str | None,
        source: str,
        retrieved_at: datetime,
        orcid: str | None = None,
        ids: Mapping[str, Sequence[str]] | None = None,
        columns: Mapping[str, str] | None = None,
        aliases: Sequence[Mapping[str, str | None]] = (),
    ) -> str:
        """A person row; returns its id (the same one for the same keys, run after run)."""
        pid = self.registry.assign("people", keys, slot, taken=self._taken["people"])
        row = self.people.get(pid)
        new_ids = {k: sorted(set(v)) for k, v in (ids or {}).items() if v}
        if row is None:
            self.people[pid] = {
                "person_id": pid,
                "last_name": last_name,
                "first_name": first_name,
                "orcid": orcid,
                "ids": new_ids,
                "source": source,
                "columns": dict(columns or {}),
                "aliases": [dict(a) for a in aliases],
                "retrieved_at": retrieved_at,
            }
        else:
            row.update(last_name=last_name, first_name=first_name, retrieved_at=retrieved_at)
            row["orcid"] = orcid or row["orcid"]
            for k, v in new_ids.items():
                row["ids"][k] = sorted(set(row["ids"].get(k, [])) | set(v))
            row["columns"].update(columns or {})
            row["aliases"] += [dict(a) for a in aliases if dict(a) not in row["aliases"]]
        return pid

    def organisation(
        self,
        *,
        slot: str,
        keys: Sequence[str],
        name: str,
        source: str,
        retrieved_at: datetime,
        acronym: str | None = None,
        level: str | None = None,
        parent_keys: Sequence[str] = (),
        ids: Mapping[str, str] | None = None,
        country: str | None = None,
        location: Mapping[str, float] | None = None,
    ) -> str:
        """An organisation row; *parent_keys* name its parents by their keys."""
        call = (slot, tuple(keys), name, source, acronym, level, tuple(parent_keys),
                tuple(sorted((ids or {}).items())), country,
                tuple(sorted((location or {}).items())))  # fmt: skip
        oid = self._org_calls.get(call)
        if oid is not None and self._org_last.get(oid) == call:
            # Stated again as it last was (every authorship names its organisations): only
            # the time it was retrieved changes.
            self.orgs[oid].fields["retrieved_at"] = retrieved_at
            return oid
        known = (slot, *keys)
        oid = self._org_ids.get(known)
        if oid is None:  # the registry is asked once per key
            oid = self.registry.assign(
                "organisations", keys, slot, taken=self._taken["organisations"]
            )
            self._org_ids[known] = oid
        fields = {
            "org_id": oid,
            "name": name,
            "acronym": acronym,
            "level": level,
            "ids": dict(ids or {}),
            "country": country,
            "location": dict(location) if location else None,
            "source": source,
            "retrieved_at": retrieved_at,
        }
        org = self.orgs.get(oid)
        self._orgs_version += 1
        if org is None:
            self.orgs[oid] = _Org(fields, list(parent_keys))
        else:
            ids_merged = {**org.fields["ids"], **fields["ids"]}
            org.fields.update({k: v for k, v in fields.items() if v is not None})
            org.fields["ids"] = ids_merged
            org.parent_keys += [k for k in parent_keys if k not in org.parent_keys]
        self._org_calls[call] = oid
        self._org_last[oid] = call
        return oid

    def _existing_index(self) -> dict[str, Any]:
        """The old tables' organisations and affiliations, indexed once (they never change)."""
        if self._existing_idx is None:
            from .names import words

            names: dict[str, tuple[str, str]] = {}
            by_words: dict[tuple[str, ...], set[str]] = defaultdict(set)
            if "organisations" in self.existing:
                table = pq.read_table(
                    self.existing["organisations"], columns=["org_id", "name", "acronym", "source"]
                )
                for row in table.to_pylist():
                    names.setdefault(row["org_id"], (row["name"], row["source"]))
                    if self.registry.is_given("organisations", row["org_id"]):
                        continue
                    for text in (row["name"], row["acronym"] or ""):
                        key = tuple(words(text))
                        if key:
                            by_words[key].add(row["org_id"])
            if "affiliations" in self.existing:
                pf = pq.ParquetFile(self.existing["affiliations"])
                for batch in pf.iter_batches(batch_size=65_536, columns=["person_id", "org_id"]):
                    pairs = zip(
                        batch.column(0).to_pylist(), batch.column(1).to_pylist(), strict=True
                    )
                    self.db.executemany(
                        "INSERT OR IGNORE INTO old_affiliations VALUES (?, ?)", pairs
                    )
            self._existing_idx = {"names": names, "words": by_words}
        return self._existing_idx

    def affiliated_orgs(self, person_id: str) -> list[tuple[str, str, str]]:
        """``(org_id, name, source)`` of every organisation *person_id* is affiliated with so far."""
        old = self._existing_index()
        oids = {
            o
            for (o,) in self.db.execute("SELECT oid FROM affiliations WHERE pid = ?", (person_id,))
        }
        oids |= {
            o
            for (o,) in self.db.execute(
                "SELECT oid FROM old_affiliations WHERE pid = ?", (person_id,)
            )
        }
        out = []
        for oid in oids:
            org = self.orgs.get(oid)
            if org is not None:
                out.append((oid, org.fields["name"], org.fields["source"]))
            elif oid in old["names"]:
                out.append((oid, *old["names"][oid]))
        return sorted(out)

    def org_by_name(self, name: str) -> str | None:
        """The one organisation named *name* (case, accents and punctuation aside), if any."""
        from .names import words

        wanted = tuple(words(name))
        if not wanted:
            return None
        if self._words_version != self._orgs_version:
            by_words: dict[tuple[str, ...], set[str]] = defaultdict(set)
            for oid, org in self.orgs.items():
                for text in (org.fields["name"], org.fields.get("acronym") or ""):
                    key = tuple(words(text))
                    if key:
                        by_words[key].add(oid)
            self._words_index, self._words_version = by_words, self._orgs_version
        hits = set(self._words_index.get(wanted, ())) | self._existing_index()["words"].get(
            wanted, set()
        )
        return hits.pop() if len(hits) == 1 else None

    def organisation_parents(self, oid: str, parents: Sequence[str]) -> None:
        """Parents given by id rather than by key."""
        org = self.orgs[oid]
        org.parents += [p for p in parents if p not in org.parents]

    def affiliation(
        self, person_id: str, org_id: str, start: int | None, end: int | None, source: str
    ) -> None:
        """Person *person_id* belonged to *org_id* from *start* to *end* (inclusive, open if None).

        Rows with the same person, organisation and source are joined into one span.
        """
        self.db.execute(_UPSERT_AFFILIATION, (person_id, org_id, source, start, end))

    def text(
        self,
        *,
        slot: str,
        keys: Sequence[str],
        title: str,
        doc_type: str,
        source: str,
        retrieved_at: datetime,
        year: int | None = None,
        date: str | None = None,
        doi: str | None = None,
        ids: Mapping[str, str] | None = None,
        version_of: str | None = None,
        n_authors: int = 0,
    ) -> str:
        """A text's record; returns the text's id. Every record of a text is kept: when the
        tables are written, the merge (:mod:`cartolex.collect.merge`) gives the text its
        fields from them all."""
        tid = self.registry.assign("texts", keys, slot, taken=self._taken["texts"])
        record = {
            "slot": slot,
            "year": year,
            "date": date,
            "doc_type": doc_type,
            "title": title,
            "doi": doi.lower() if doi else None,
            "ids": dict(ids or {}),
            "version_of": version_of,
            "source": source,
            "retrieved_at": retrieved_at.isoformat(),
            "keys": list(keys),
        }
        self.db.execute(
            "INSERT INTO records (tid, n_authors, data) VALUES (?, ?, ?)",
            (tid, n_authors, json.dumps(record, ensure_ascii=False, separators=(",", ":"))),
        )
        return tid

    def set_n_authors(self, text_id: str, n: int) -> None:
        """The number of authors of *text_id*, known only once its records are read."""
        self.db.execute("UPDATE records SET n_authors = ? WHERE tid = ?", (n, text_id))

    def link(self, text_id: str, relation: str, value: str) -> None:
        """A link a finder or provider states: ``version_of_doi`` (this text is a preprint whose
        published version has that DOI)."""
        self.text_links.append((text_id, relation, value))

    def part(
        self,
        text_id: str,
        *,
        part: str,
        language: str,
        provider: str,
        content: str,
        retrieved_at: datetime,
        format: str = "plain",
        replace: bool = False,
    ) -> None:
        """One part of a text (title, abstract, body or full); empty content is skipped, and the
        first record of a part wins (readers give the newest first when it matters), unless
        *replace* is set: a text provider's part replaces the one the same service gave as a
        finder, and its runs are read oldest first, so the newest wins."""
        if not content:
            return
        verb = "REPLACE" if replace else "IGNORE"
        self.db.execute(
            f"INSERT OR {verb} INTO parts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                text_id,
                part,
                language,
                provider,
                format,
                pack_text(content),
                retrieved_at.isoformat() if retrieved_at else None,
            ),
        )

    def authorship(
        self,
        text_id: str,
        person_id: str,
        *,
        position: int,
        orgs: Sequence[str] = (),
        last: bool | None = None,
        corresponding: bool | None = None,
    ) -> None:
        """Person *person_id* wrote *text_id*, at rank *position* (the first statement wins)."""
        self.db.execute(
            "INSERT OR IGNORE INTO authorships VALUES (?, ?, ?, ?, ?, ?)",
            (
                text_id,
                person_id,
                position,
                json.dumps(sorted(set(orgs))),
                _flag(last),
                _flag(corresponding),
            ),
        )

    # ── finishing ──
    def _resolve_parents(self) -> None:
        for org in self.orgs.values():
            parents = list(org.parents)
            for key in org.parent_keys:
                pid = self.registry.lookup("organisations", [key], "")
                if pid and pid != org.fields["org_id"] and pid not in parents:
                    parents.append(pid)
            org.fields["parents"] = parents

    def _confirmed_ids(self, people: dict[str, dict[str, Any]]) -> None:
        """An idHAL confirmed as a person's record (``hal:<idHAL>`` in ``decisions/people.csv``)
        joins their ``ids``, where the next HAL collection looks for it."""
        if not self.layout.people_csv.exists():
            return
        for row in read_decision_csv(self.layout.people_csv, "people"):
            person = people.get(row["person_id"])
            if person is None or row["identity"] not in ("confirmed", "auto"):
                continue
            idhal = [r.split(":", 1)[1] for r in row["records"].split(";") if r.startswith("hal:")]
            if not idhal:
                continue
            ids = dict(person["ids"] or {})
            ids["idhal"] = sorted(set(ids.get("idhal") or []) | set(idhal))
            person["ids"] = ids

    def _aliases(self, people: dict[str, dict[str, Any]]) -> None:
        """The name forms of rows merged into another join that person's aliases."""
        if not self.layout.people_csv.exists():
            return
        for row in read_decision_csv(self.layout.people_csv, "people"):
            target, merged = row["merged_into"], row["person_id"]
            if not target or target not in people or merged not in people:
                continue
            src = people[merged]
            alias = {
                "last_name": src["last_name"],
                "first_name": src["first_name"],
                "source": src["source"],
            }
            dest = people[target]
            same = (alias["last_name"], alias["first_name"]) == (
                dest["last_name"],
                dest["first_name"],
            )
            if not same and alias not in dest["aliases"]:
                dest["aliases"].append(alias)

    def _kept(self, name: str) -> Iterator[dict[str, Any]]:
        """The rows of the old table *name* that no registry owns, in key order."""
        if name not in self.existing:
            return
        pf = pq.ParquetFile(self.existing[name])
        try:
            for batch in pf.iter_batches(batch_size=ROW_GROUP):
                for row in pa.Table.from_batches([batch]).to_pylist():
                    if not self._owned(name, row):
                        yield row
        finally:
            pf.close()

    def _owned(self, name: str, row: Mapping[str, Any]) -> bool:
        """Whether a row of the old table *name* is one this rebuild makes again."""
        given = self.registry.is_given
        if name in ("texts", "text_parts", "authorships"):
            return given("texts", row["text_id"])
        if name == "people":
            return given("people", row["person_id"])
        if name == "organisations":
            return given("organisations", row["org_id"])
        return (
            given("people", row["person_id"])
            or given("organisations", row["org_id"])
            or self.db.execute(
                "SELECT 1 FROM affiliations WHERE pid = ? AND oid = ? AND source = ?",
                (row["person_id"], row["org_id"], row["source"]),
            ).fetchone()
            is not None
        )

    def _built_texts(self) -> Iterator[dict[str, Any]]:
        """The texts the merge kept, in id order, with their positions: a slot's texts in
        year order (unknown years last), then by id."""
        self.db.execute("DELETE FROM positions")
        order = self.store.rows(
            "SELECT tid, slot FROM texts_out ORDER BY slot, year IS NULL, COALESCE(year, 0),"
            " COALESCE(date, ''), tid"
        )
        batch: list[tuple[str, int]] = []
        current, i = None, 0
        for tid, slot in order:
            if slot != current:
                current, i = slot, 0
            batch.append((tid, i))
            i += 1
            if len(batch) >= ROW_GROUP:
                self.db.executemany("INSERT INTO positions VALUES (?, ?)", batch)
                batch = []
        self.db.executemany("INSERT INTO positions VALUES (?, ?)", batch)
        rows = self.store.rows(
            "SELECT t.data, p.position FROM texts_out t JOIN positions p USING (tid) ORDER BY t.tid"
        )
        for data, position in rows:
            row = json.loads(data)
            row["retrieved_at"] = parse_time(row["retrieved_at"])
            row["position"] = position
            yield row

    def _built_parts(self) -> Iterator[dict[str, Any]]:
        for tid, part, language, provider, fmt, content, at in self.store.rows(
            "SELECT tid, part, language, provider, format, content, retrieved_at FROM parts"
            " ORDER BY tid, part, language, provider"
        ):
            yield {
                "text_id": tid,
                "part": part,
                "language": language,
                "provider": provider,
                "format": fmt,
                "content": unpack_text(content),
                "retrieved_at": parse_time(at),
            }

    def _built_authorships(self) -> Iterator[dict[str, Any]]:
        for tid, pid, position, orgs, last, corresponding in self.store.rows(
            "SELECT tid, pid, position, orgs, last, corresponding FROM authorships"
            " ORDER BY tid, pid"
        ):
            yield {
                "text_id": tid,
                "person_id": pid,
                "position": position,
                "orgs": json.loads(orgs),
                "last": _unflag(last),
                "corresponding": _unflag(corresponding),
            }

    def _built_affiliations(self) -> Iterator[dict[str, Any]]:
        def row(r: tuple) -> dict[str, Any]:
            pid, oid, source, start, end = r
            return {
                "person_id": pid,
                "org_id": oid,
                "start_year": start,
                "end_year": end,
                "source": source,
            }

        rows = self.store.rows(
            "SELECT pid, oid, source, start_year, end_year FROM affiliations ORDER BY pid, oid, source"
        )
        for _key, group in itertools.groupby(rows, key=lambda r: (r[0], r[1])):
            yield from sorted(map(row, group), key=lambda r: source_key("affiliations", r))

    def write_tables(self) -> dict[str, int]:
        """Write the six tables: rows built here, plus every row of the old tables no registry
        owns; returns the rows of each. Call after :func:`~cartolex.collect.merge.merge_texts`."""
        self._resolve_parents()
        people = {p["person_id"]: p for p in self._kept("people")}
        people.update({pid: dict(p, aliases=list(p["aliases"])) for pid, p in self.people.items()})
        self._aliases(people)
        self._confirmed_ids(people)
        organisations = {o["org_id"]: o for o in self._kept("organisations")}
        organisations.update({oid: o.fields for oid, o in self.orgs.items()})
        built = {
            "texts": self._built_texts(),
            "text_parts": self._built_parts(),
            "people": iter(sorted(people.values(), key=lambda r: r["person_id"])),
            "organisations": iter(sorted(organisations.values(), key=lambda r: r["org_id"])),
            "affiliations": self._built_affiliations(),
            "authorships": self._built_authorships(),
        }
        rows: dict[str, int] = {}
        for name in SOURCE_TABLES:
            if name in ("people", "organisations"):
                ordered: Iterable[dict[str, Any]] = built[name]
            else:
                ordered = heapq.merge(
                    self._kept(name), built[name], key=lambda r, n=name: source_key(n, r)
                )
            rows[name] = write_source_rows(self.layout.table(name), name, ordered)
        return rows


# ── rebuilding ───────────────────────────────────────────────────────────────

#: A reader turns the runs of one kind of one slot, in time order, into rows.
Reader = Callable[[list[RawRun], SourceBuilder], None]


def default_readers() -> dict[str, Reader]:
    """cartolex's readers, by raw folder name, in the order they run."""
    from .hal import read_hal_runs
    from .harvest import read_openalex_runs, read_orcid_runs
    from .institutions import read_institution_runs
    from .people_import import read_corpus_runs, read_folder_runs, read_people_runs
    from .providers import read_improve_runs
    from .scielo import read_scielo_runs
    from .snowball import read_snowball_runs

    return {
        "people": read_people_runs,
        "corpus": read_corpus_runs,
        "folder": read_folder_runs,
        # People taken from institutions come before the works harvested for them.
        "institution": read_institution_runs,
        "snowball": read_snowball_runs,
        "openalex": read_openalex_runs,
        "orcid": read_orcid_runs,
        # Resolution proposals are kept for the record; no table is built from them.
        "resolve": lambda runs, builder: None,
        "hal": read_hal_runs,
        "scielo": read_scielo_runs,
        # Provider runs come after every finder: they improve texts already found.
        "improve": read_improve_runs,
        # Candidates found by a name are proposals: no table is built from them.
        "hal_candidates": lambda runs, builder: None,
        "scielo_candidates": lambda runs, builder: None,
        # Failures are kept for the coverage report: no table is built from them.
        "failures": lambda runs, builder: None,
        # An institution's proposal: people enter only when taken (``institution`` runs).
        "institution_proposals": lambda runs, builder: None,
    }


@dataclass
class RebuildReport:
    """What a rebuild read and wrote."""

    runs: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped_kinds: list[str] = field(default_factory=list)
    #: Texts merged across finders, per rule (see ``sources/merges.json``).
    merges: dict[str, int] = field(default_factory=dict)
    #: Runs read whole and digested by this rebuild (the others were read from their digests).
    digested: int = 0


#: A rebuild reading more raw records than this (bytes on disk, every slot) runs in a
#: process of its own: what it holds goes back to the computer when it ends, and the
#: process that asked for it (the app) keeps its memory as it was.
ISOLATE_BYTES = 256 << 20


def _raw_bytes(layout: ProjectLayout, config: ProjectFile) -> int:
    total = 0
    for slot in config.slots:
        folder = layout.slot(slot.id) / "raw"
        if folder.is_dir():
            total += sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    return total


def rebuild_sources(
    layout: ProjectLayout,
    config: ProjectFile,
    *,
    readers: Mapping[str, Reader] | None = None,
    finder_priority: Sequence[str] | None = None,
    incremental: bool = True,
    jobs: int | None = None,
    scratch: Path | None = None,
    isolate: bool | None = None,
) -> RebuildReport:
    """Rebuild the six source tables from every slot's raw runs and write them.

    Rows no registry gave an id to (tables written by another tool) are kept.
    Slots are read in the project's order, each slot's kinds in the readers'
    order, runs in time order. Texts found by several finders are then merged
    (:mod:`cartolex.collect.merge`, fields filled by *finder_priority*) and the
    merges listed in ``sources/merges.json``. Rebuilding from the same raw
    records and the same kept rows writes the same tables.

    The rows are kept in a scratch database while the readers run
    (:mod:`cartolex.collect.workstore`), in a folder of *scratch* (by default the
    project's ``cache/``), removed at the end: memory does not grow with the
    project, and the tables are written a row group at a time.

    With *incremental* (the default), a harvest's runs are read from their
    digests in ``cache/sources/`` (:mod:`cartolex.collect.digests`): only the
    runs not digested yet are read whole, in *jobs* worker processes when they
    are many, and a run superseded for everyone it names is not read at all.

    With *isolate* (by default when the raw records exceed :data:`ISOLATE_BYTES` and
    the readers are the default ones), the rebuild runs in a process of its own, which
    gives back its report, or raises what it raised.
    """
    if isolate is None:
        isolate = readers is None and _raw_bytes(layout, config) > ISOLATE_BYTES
    if isolate:
        return _rebuild_in_child(
            layout,
            config,
            finder_priority=finder_priority,
            incremental=incremental,
            jobs=jobs,
            scratch=scratch,
        )
    from .digests import DIGESTERS, DigestCache
    from .harvest import current_runs
    from .merge import FINDER_PRIORITY, merge_texts
    from .workstore import WorkStore

    readers = dict(readers or default_readers())
    digests = DigestCache(layout, jobs=jobs) if incremental else None
    slots = [s.id for s in config.slots]
    report = RebuildReport()
    with WorkStore(scratch if scratch is not None else layout.cache) as store:
        registry = IdRegistry(layout, slots, db=store.db)
        workers = jobs if jobs is not None else Budget.for_machine().workers
        builder = SourceBuilder(layout, config, registry, store, jobs=workers)
        order = list(readers)
        every_run: list[RawRun] = []
        by_slot = {slot: read_runs(layout, slot, digests=digests) for slot in slots}
        if digests is not None:
            needed = []
            for runs in by_slot.values():
                every_run += runs
                for kind in DIGESTERS:
                    of_kind = [r for r in runs if r.kind == kind]
                    current = current_runs(of_kind)
                    needed += [r for r in of_kind if r.run_id in current]
            digests.prepare(needed)
        for slot in slots:
            runs = by_slot[slot]
            report.runs += len(runs)
            kinds = sorted(
                {r.kind for r in runs},
                key=lambda k: (k not in order, order.index(k) if k in order else 0, k),
            )
            for kind in kinds:
                reader = readers.get(kind)
                if reader is None:
                    report.skipped_kinds.append(f"{slot}/{kind}")
                    continue
                reader([r for r in runs if r.kind == kind], builder)
        merged = merge_texts(builder, priority=finder_priority or FINDER_PRIORITY)
        report.rows = builder.write_tables()
        registry.save()
        merged.write_log(layout)
        if digests is not None:
            digests.save(every_run)
            report.digested = digests.digested
        report.counts = dict(builder.counts)
        report.warnings = list(builder.warnings)
        report.merges = merged.counts()
    return report


def _rebuild_in_child(layout: ProjectLayout, config: ProjectFile, **kwargs: Any) -> RebuildReport:
    """:func:`rebuild_sources` in a child process (spawned: the same on every system)."""
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    here, there = context.Pipe(duplex=False)
    child = context.Process(
        target=_rebuild_child,
        args=(there, str(layout.root), config, kwargs),
        name="cartolex-rebuild",
    )
    child.start()
    there.close()
    try:
        message = here.recv()
    except EOFError:
        message = None
    finally:
        here.close()
        child.join()
    if message is None:
        code = child.exitcode
        why = (
            f"was stopped by the system (signal {-code}), most often for want of memory"
            if code is not None and code < 0
            else f"ended without its report (exit code {code})"
        )
        raise RuntimeError(f"the rebuild of the source tables {why}")
    kind, payload = message
    if kind == "error":
        raise payload
    return payload


def _rebuild_child(conn: Any, root: str, config: ProjectFile, kwargs: dict[str, Any]) -> None:
    try:
        report = rebuild_sources(ProjectLayout(Path(root)), config, isolate=False, **kwargs)
        conn.send(("done", report))
    except BaseException as exc:  # noqa: BLE001 - raised again in the process that asked
        try:
            conn.send(("error", exc))
        except Exception:  # an exception that cannot be sent: its words
            conn.send(("error", RuntimeError(f"{type(exc).__name__}: {exc}")))
    finally:
        conn.close()
