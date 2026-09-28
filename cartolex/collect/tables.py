# SPDX-License-Identifier: MIT
"""Source writers: collected records → the six source tables, rebuilt from the raw records.

Collection never writes a table row directly. Every finder stores what it
received in the slot's raw folder, ``sources/<slot>/raw/<kind>/<run id>.jsonl``
(a header line, then one record per line, written whole or not at all), and
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
import json
import os
import secrets
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa

from cartolex.project.files import atomic_write_bytes
from cartolex.project.layout import SOURCE_TABLES, ProjectLayout
from cartolex.project.models import ProjectFile
from cartolex.project.tables import (
    SOURCE_SCHEMAS,
    read_decision_csv,
    read_source_table,
    write_source_table,
)

__all__ = [
    "IDS_FORMAT",
    "RAW_FORMAT",
    "IdRegistry",
    "RawRun",
    "RawWriter",
    "Reader",
    "RebuildReport",
    "SourceBuilder",
    "default_readers",
    "new_run_id",
    "raw_folder",
    "read_runs",
    "rebuild_sources",
]

RAW_FORMAT = "cartolex-raw/1"
IDS_FORMAT = "cartolex-ids/1"
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


class RawWriter:
    """Writes one raw run: a header line, then records; nothing is visible until :meth:`close`.

    Records go to a temporary file in the target folder; :meth:`close` writes
    the header (which may still change until then, :attr:`header`) and the
    records into place in one rename, so a cancelled or failed job leaves no
    partial run behind. Use it as a context manager: an exception discards the run.
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
        latest = max((p.stem for p in self.folder.glob("*.jsonl")), default="")
        if self.run_id <= latest:
            self.run_id = latest + "0"
        self.path = self.folder / f"{self.run_id}.jsonl"
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".body", dir=self.folder)
        self._body = Path(tmp)
        self._fh = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
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

    def close(self) -> Path:
        """Write the header and the records into place; returns the run's path."""
        if self._fh.closed and not self._body.exists():
            return self.path  # discarded: nothing to write
        self._fh.close()
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
                out.write(self._line(self.header))
                with open(self._body, encoding="utf-8") as body:
                    for line in body:
                        out.write(line)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        finally:
            self._body.unlink(missing_ok=True)
        return self.path

    def discard(self) -> None:
        with contextlib.suppress(Exception):
            self._fh.close()
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
    """One raw run file: its slot, kind, id, header, and its records (read on demand)."""

    path: Path
    slot: str
    kind: str
    run_id: str
    header: dict[str, Any]

    def records(self) -> Iterator[dict[str, Any]]:
        with open(self.path, encoding="utf-8") as fh:
            next(fh, None)
            for n, line in enumerate(fh, start=2):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{self.path}, line {n}: not valid JSON ({exc})") from exc


def read_runs(layout: ProjectLayout, slot: str, kind: str | None = None) -> list[RawRun]:
    """The runs of *slot* (of one *kind*, or all), in time order."""
    root = raw_folder(layout, slot)
    if not root.is_dir():
        return []
    kinds = [kind] if kind else sorted(p.name for p in root.iterdir() if p.is_dir())
    runs: list[RawRun] = []
    for k in kinds:
        folder = root / k
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.jsonl")):
            with open(path, encoding="utf-8") as fh:
                first = fh.readline()
            try:
                header = json.loads(first)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}: the header line is not valid JSON ({exc})") from exc
            if header.get("format") != RAW_FORMAT:
                raise ValueError(f"{path}: not a raw run (format {header.get('format')!r})")
            runs.append(RawRun(path, slot, k, path.stem, header))
    return sorted(runs, key=lambda r: (r.run_id, r.kind))


# ── ids ──────────────────────────────────────────────────────────────────────


class IdRegistry:
    """The ids of every slot's registry: natural keys → ids, given once, never reused.

    Texts belong to one slot, so a text key is looked up in its slot's registry
    only; people and organisations are shared by every slot. A new id takes the
    next number after every number any registry ever gave, skipping ids already
    present in the tables.
    """

    def __init__(self, layout: ProjectLayout, slots: Sequence[str]) -> None:
        self.layout = layout
        self.slots = list(slots)
        self._data: dict[str, dict[str, Any]] = {}
        self._changed: set[str] = set()
        for slot in self.slots:
            path = self.path(slot)
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("format") != IDS_FORMAT:
                    raise ValueError(f"{path}: not an id registry (format {data.get('format')!r})")
            else:
                data = {"format": IDS_FORMAT, "next": {}, "keys": {}}
            data.setdefault("next", {})
            data.setdefault("keys", {})
            for table in ID_PREFIX:
                data["next"].setdefault(table, 1)
                data["keys"].setdefault(table, {})
            self._data[slot] = data
        self._given: dict[str, set[str]] = {
            t: {i for d in self._data.values() for i in d["keys"][t].values()} for t in ID_PREFIX
        }

    def path(self, slot: str) -> Path:
        return raw_folder(self.layout, slot) / "ids.json"

    def given(self, table: str) -> set[str]:
        """Every id of *table* some registry gave."""
        return self._given[table]

    def lookup(self, table: str, keys: Iterable[str], slot: str) -> str | None:
        """The id one of *keys* already has, or ``None``."""
        scopes = [slot] if table == "texts" else self.slots
        for key in keys:
            for s in scopes:
                found = self._data[s]["keys"][table].get(key)
                if found:
                    return found
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
            number = max(d["next"][table] for d in self._data.values())
            while True:
                found = f"{ID_PREFIX[table]}{number:0{ID_WIDTH}d}"
                number += 1
                if found not in taken and found not in self._given[table]:
                    break
            for d in self._data.values():
                d["next"][table] = max(d["next"][table], number)
            self._changed.update(self._data)
            self._given[table].add(found)
        own = self._data[slot]["keys"][table]
        for key in keys:
            if own.get(key) != found and key not in own:
                own[key] = found
                self._changed.add(slot)
        return found

    def save(self) -> None:
        """Write the registries that changed."""
        for slot in sorted(self._changed):
            data = self._data[slot]
            data["keys"] = {t: dict(sorted(data["keys"][t].items())) for t in sorted(data["keys"])}
            data["next"] = dict(sorted(data["next"].items()))
            text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
            atomic_write_bytes(self.path(slot), text.encode("utf-8"))
        self._changed.clear()


# ── the builder ──────────────────────────────────────────────────────────────


def _pairs(mapping: Mapping[str, Any] | None) -> list[tuple[str, Any]]:
    return sorted((str(k), v) for k, v in (mapping or {}).items())


@dataclass
class _Org:
    fields: dict[str, Any]
    parent_keys: list[str] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)


class SourceBuilder:
    """Collects rows for the six tables while the readers run, then merges and checks them."""

    def __init__(
        self,
        layout: ProjectLayout,
        config: ProjectFile,
        registry: IdRegistry,
        existing: Mapping[str, pa.Table] | None = None,
    ) -> None:
        self.layout = layout
        self.config = config
        self.registry = registry
        self.existing = dict(existing or {})
        key_columns = {"texts": "text_id", "people": "person_id", "organisations": "org_id"}
        #: Ids present in the old tables: a new id never takes one of them.
        self._taken = {
            t: set(self.existing[t][c].to_pylist()) if t in self.existing else set()
            for t, c in key_columns.items()
        }
        #: People of the old tables that no registry gave an id to (kept as they are).
        self.foreign_people = self._taken["people"] - registry.given("people")
        self.people: dict[str, dict[str, Any]] = {}
        self.orgs: dict[str, _Org] = {}
        self.texts: dict[str, dict[str, Any]] = {}
        self.parts: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.authorships: dict[tuple[str, str], dict[str, Any]] = {}
        self.affiliations: dict[tuple[str, str, str], list[Any]] = {}
        self.warnings: list[str] = []
        self.counts: dict[str, int] = {}

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
        oid = self.registry.assign("organisations", keys, slot, taken=self._taken["organisations"])
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
        if org is None:
            self.orgs[oid] = _Org(fields, list(parent_keys))
        else:
            ids_merged = {**org.fields["ids"], **fields["ids"]}
            org.fields.update({k: v for k, v in fields.items() if v is not None})
            org.fields["ids"] = ids_merged
            org.parent_keys += [k for k in parent_keys if k not in org.parent_keys]
        return oid

    def affiliated_orgs(self, person_id: str) -> list[tuple[str, str, str]]:
        """``(org_id, name, source)`` of every organisation *person_id* is affiliated with so far."""
        names = {oid: (o.fields["name"], o.fields["source"]) for oid, o in self.orgs.items()}
        if "organisations" in self.existing:
            for row in (
                self.existing["organisations"].select(["org_id", "name", "source"]).to_pylist()
            ):
                names.setdefault(row["org_id"], (row["name"], row["source"]))
        oids = {oid for (pid, oid, _src) in self.affiliations if pid == person_id}
        if "affiliations" in self.existing:
            for row in self.existing["affiliations"].select(["person_id", "org_id"]).to_pylist():
                if row["person_id"] == person_id:
                    oids.add(row["org_id"])
        return sorted((oid, *names[oid]) for oid in oids if oid in names)

    def org_by_name(self, name: str) -> str | None:
        """The one organisation named *name* (case, accents and punctuation aside), if any."""
        from .names import words

        wanted = words(name)
        if not wanted:
            return None
        hits = {
            oid
            for oid, org in self.orgs.items()
            if words(org.fields["name"]) == wanted
            or words(org.fields.get("acronym") or "") == wanted
        }
        if "organisations" in self.existing:
            given = self.registry.given("organisations")
            for row in (
                self.existing["organisations"].select(["org_id", "name", "acronym"]).to_pylist()
            ):
                if row["org_id"] in given:
                    continue
                if words(row["name"]) == wanted or words(row["acronym"] or "") == wanted:
                    hits.add(row["org_id"])
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
        key = (person_id, org_id, source)
        span = self.affiliations.get(key)
        if span is None:
            self.affiliations[key] = [start, end]
            return
        if start is not None:
            span[0] = start if span[0] is None else min(span[0], start)
        if end is not None:
            span[1] = end if span[1] is None else max(span[1], end)

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
        """A text row; a text already built from another key keeps its id and gains the values
        this record has (a value it lacks is kept from the earlier record)."""
        tid = self.registry.assign("texts", keys, slot, taken=self._taken["texts"])
        fields = {
            "text_id": tid,
            "slot": slot,
            "year": year,
            "date": date,
            "doc_type": doc_type,
            "title": title,
            "doi": doi.lower() if doi else None,
            "ids": dict(ids or {}),
            "version_of": version_of,
            "n_authors": n_authors,
            "source": source,
            "retrieved_at": retrieved_at,
        }
        row = self.texts.get(tid)
        if row is None:
            self.texts[tid] = fields
        else:
            merged_ids = {**row["ids"], **fields["ids"]}
            row.update({k: v for k, v in fields.items() if v is not None})
            row["ids"] = merged_ids
        return tid

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
    ) -> None:
        """One part of a text (title, abstract, body or full); empty content is skipped, and the
        first record of a part wins (readers give the newest first when it matters)."""
        if not content or (text_id, part, language, provider) in self.parts:
            return
        self.parts[(text_id, part, language, provider)] = {
            "text_id": text_id,
            "part": part,
            "language": language,
            "provider": provider,
            "format": format,
            "content": content,
            "retrieved_at": retrieved_at,
        }

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
        if (text_id, person_id) in self.authorships:
            return
        self.authorships[(text_id, person_id)] = {
            "text_id": text_id,
            "person_id": person_id,
            "position": position,
            "orgs": sorted(set(orgs)),
            "last": last,
            "corresponding": corresponding,
        }

    # ── finishing ──
    def _resolve_parents(self) -> None:
        for org in self.orgs.values():
            parents = list(org.parents)
            for key in org.parent_keys:
                pid = self.registry.lookup("organisations", [key], "")
                if pid and pid != org.fields["org_id"] and pid not in parents:
                    parents.append(pid)
            org.fields["parents"] = parents

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

    def finish(self) -> dict[str, pa.Table]:
        """The six tables: rows built here, plus every row of the old tables no registry owns."""
        self._resolve_parents()
        built: dict[str, list[dict[str, Any]]] = {
            "texts": list(self.texts.values()),
            "text_parts": list(self.parts.values()),
            "people": [dict(p, aliases=list(p["aliases"])) for p in self.people.values()],
            "organisations": [o.fields for o in self.orgs.values()],
            "affiliations": [
                {
                    "person_id": pid,
                    "org_id": oid,
                    "start_year": span[0],
                    "end_year": span[1],
                    "source": source,
                }
                for (pid, oid, source), span in self.affiliations.items()
            ],
            "authorships": list(self.authorships.values()),
        }
        registered = {t: self.registry.given(t) for t in ID_PREFIX}
        owned_text = registered["texts"]
        kept: dict[str, list[dict[str, Any]]] = {name: [] for name in SOURCE_TABLES}
        for name, table in self.existing.items():
            for row in table.to_pylist():
                if name in ("texts", "text_parts", "authorships"):
                    owned = row["text_id"] in owned_text
                elif name == "people":
                    owned = row["person_id"] in registered["people"]
                elif name == "organisations":
                    owned = row["org_id"] in registered["organisations"]
                else:
                    owned = (
                        row["person_id"] in registered["people"]
                        or row["org_id"] in registered["organisations"]
                        or (row["person_id"], row["org_id"], row["source"]) in self.affiliations
                    )
                if not owned:
                    kept[name].append(row)
        people_all = {p["person_id"]: p for p in kept["people"] + built["people"]}
        self._aliases(people_all)
        # Positions: a slot's texts in year order (unknown years last), then by id.
        texts = kept["texts"] + built["texts"]
        by_slot: dict[str, list[dict[str, Any]]] = {}
        for row in built["texts"]:
            by_slot.setdefault(row["slot"], []).append(row)
        for rows in by_slot.values():
            rows.sort(
                key=lambda r: (r["year"] is None, r["year"] or 0, r["date"] or "", r["text_id"])
            )
            for i, row in enumerate(rows):
                row["position"] = i
        tables: dict[str, pa.Table] = {}
        for name in SOURCE_TABLES:
            rows = texts if name == "texts" else kept[name] + built[name]
            tables[name] = _to_table(name, rows)
        return tables


def _to_table(name: str, rows: list[dict[str, Any]]) -> pa.Table:
    schema = SOURCE_SCHEMAS[name]
    columns: dict[str, list[Any]] = {}
    for fld in schema:
        values = [r.get(fld.name) for r in rows]
        if pa.types.is_map(fld.type):
            values = [_pairs(v) if isinstance(v, Mapping) else v for v in values]
        elif pa.types.is_list(fld.type):
            values = [list(v) if v is not None else None for v in values]
        columns[fld.name] = values
    return pa.table({k: pa.array(v, type=schema.field(k).type) for k, v in columns.items()})


# ── rebuilding ───────────────────────────────────────────────────────────────

#: A reader turns the runs of one kind of one slot, in time order, into rows.
Reader = Callable[[list[RawRun], SourceBuilder], None]


def default_readers() -> dict[str, Reader]:
    """cartolex's readers, by raw folder name, in the order they run."""
    from .harvest import read_openalex_runs, read_orcid_runs
    from .people_import import read_corpus_runs, read_folder_runs, read_people_runs

    return {
        "people": read_people_runs,
        "corpus": read_corpus_runs,
        "folder": read_folder_runs,
        "openalex": read_openalex_runs,
        "orcid": read_orcid_runs,
        # Resolution proposals are kept for the record; no table is built from them.
        "resolve": lambda runs, builder: None,
    }


@dataclass
class RebuildReport:
    """What a rebuild read and wrote."""

    runs: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped_kinds: list[str] = field(default_factory=list)


def rebuild_sources(
    layout: ProjectLayout,
    config: ProjectFile,
    *,
    readers: Mapping[str, Reader] | None = None,
) -> RebuildReport:
    """Rebuild the six source tables from every slot's raw runs and write them.

    Rows no registry gave an id to (tables written by another tool) are kept.
    Slots are read in the project's order, each slot's kinds in the readers'
    order, runs in time order. Rebuilding from the same raw records and the same
    kept rows writes the same bytes.
    """
    readers = dict(readers or default_readers())
    slots = [s.id for s in config.slots]
    registry = IdRegistry(layout, slots)
    existing = {
        name: read_source_table(layout.table(name), name)
        for name in SOURCE_TABLES
        if layout.table(name).exists()
    }
    builder = SourceBuilder(layout, config, registry, existing)
    report = RebuildReport()
    order = list(readers)
    for slot in slots:
        runs = read_runs(layout, slot)
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
    tables = builder.finish()
    registry.save()
    for name, table in tables.items():
        write_source_table(layout.table(name), name, table)
        report.rows[name] = table.num_rows
    report.counts = dict(builder.counts)
    report.warnings = list(builder.warnings)
    return report
