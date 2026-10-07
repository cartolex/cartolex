# SPDX-License-Identifier: MIT
"""The OpenAlex snapshot: the whole index, read from the files a user downloaded.

From a national size up, asking the API person by person costs days of its
daily budget; the snapshot (CC0, see ``docs/collection.md``) holds the same
records. Its layout::

    <folder>/data/jsonl/<entity>/manifest.json
    <folder>/data/jsonl/<entity>/updated_date=YYYY-MM-DD/part_NNNN.gz
    <folder>/data/jsonl/works/deleted_ids.csv.gz          (work_id,deleted_date)

(``<folder>/data/<entity>/…``, the layout of snapshots before 2026, is read too,
and so is a folder that holds the entity folders directly.) Each part is
gzip-compressed JSON lines, one entity per line in the API's shape; a record
sits in the partition of the date it last changed, and when a copy synced
without deletions holds it twice, the newest partition wins.

:class:`Snapshot` **streams** the partitions: a part is read line by line, each
line is tested against what is wanted (author, institution or work ids, DOIs)
on its raw bytes, and only the lines that may match are parsed, so memory
holds the matches, never a partition. With an index of the snapshot
(:mod:`cartolex.collect.snapshot_index`), a query reads only the members of the
parts that may hold what it asks, tested the same way: the same records come
back for a fraction of the reading. Deleted works are dropped, the deletion
log being read the same way. :class:`SnapshotSource` answers the requests the
finders make of OpenAlex (:class:`cartolex.collect.openalex.OpenAlexSource`)
from it, so that the harvest, the institutions' proposal and the collaborators'
rounds write the same raw runs, hence the same tables, from the snapshot as from
the API. A record's retrieval time is the snapshot's release date.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zlib
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .http import Cancelled, Fetched, Page
from .openalex import (
    INSTITUTION_WORK_FIELDS,
    PER_PAGE,
    WORK_FIELDS,
    Step,
    Years,
    bare_doi,
    fit_reading,
    short_id,
)

__all__ = [
    "ENTITIES",
    "EntityCheck",
    "Partition",
    "Query",
    "RecordStore",
    "ScanReport",
    "Snapshot",
    "SnapshotCheck",
    "SnapshotSource",
]

#: The entities cartolex reads.
ENTITIES = ("works", "authors", "institutions")
#: A resumable prefetch writes its state down every this many parts, or this many seconds.
SAVE_EVERY_PARTS = 32
SAVE_EVERY_SECONDS = 120.0
#: Parts read ahead of the one given back, per worker process (bounds the memory a reading
#: holds when its results are taken more slowly than they come).
READ_AHEAD = 2
_AUTHOR = re.compile(rb"openalex\.org/(A\d+)")
_INSTITUTION = re.compile(rb"openalex\.org/(I\d+)")
_WORK = re.compile(rb"openalex\.org/(W\d+)")
_DOI = re.compile(rb'"doi"\s*:\s*"https?://(?:dx\.)?doi\.org/([^"]+)"', re.IGNORECASE)
_ROR = re.compile(rb"ror\.org/(0[0-9a-z]{6}\d{2})")
_DATE = re.compile(r"updated_date=(\d{4}-\d{2}-\d{2})")
#: The fields of a work kept from a pass: what the API is asked for (a harvest's works), and
#: what an institution's reading folds.
_WORK_SELECT = tuple(WORK_FIELDS.split(","))
_INSTITUTION_SELECT = tuple(INSTITUTION_WORK_FIELDS.split(","))


@dataclass(frozen=True)
class Partition:
    """One part file of an entity: its update date, where it is, its size in bytes."""

    entity: str
    date: str
    path: Path
    size: int


@dataclass
class ScanReport:
    """What the scans of a job read: bytes, lines, lines parsed, records kept, seconds."""

    passes: int = 0
    bytes: int = 0
    #: Members of indexed parts read (0: every pass read whole parts).
    members: int = 0
    lines: int = 0
    parsed: int = 0
    kept: int = 0
    deleted: int = 0
    seconds: float = 0.0
    by_entity: dict[str, int] = field(default_factory=dict)

    def lines_per_second(self) -> float:
        return self.lines / self.seconds if self.seconds else 0.0


@dataclass(frozen=True)
class EntityCheck:
    """One entity of a snapshot against its manifest: the parts it lists, their bytes, and
    those missing here or of another size; without a manifest, the parts found."""

    entity: str
    parts: int
    bytes: int
    missing: int = 0
    listed: bool = True


@dataclass(frozen=True)
class SnapshotCheck:
    """A snapshot folder against its manifests (:meth:`Snapshot.check`)."""

    root: Path
    release: str
    entities: dict[str, EntityCheck]
    #: The index (:func:`cartolex.collect.snapshot_index.index_state`), or ``None``.
    index: dict[str, Any] | None = None

    @property
    def indexed(self) -> bool:
        """An index of this release is complete: a query reads only what it asks."""
        return bool(
            self.index
            and self.index["state"] == "complete"
            and self.index["release"] == self.release
        )

    @property
    def absent(self) -> list[str]:
        """The entities cartolex reads that the folder has no part of."""
        return [e for e in ENTITIES if e not in self.entities]

    @property
    def missing(self) -> int:
        """Parts the manifests list that are missing here or of another size."""
        return sum(e.missing for e in self.entities.values())

    @property
    def complete(self) -> bool:
        """Every entity cartolex reads is there, with every part its manifest lists."""
        return not self.absent and not self.missing

    def bytes_of(self, *entities: str) -> int:
        """The compressed bytes of *entities*' parts."""
        return sum(self.entities[e].bytes for e in entities if e in self.entities)

    def read_bytes(self, action: str, *, rounds: int = 1, search: bool = False) -> int:
        """About how many bytes a job reads: a harvest (or a retry of one) one pass over the
        authors and one over the works; an institutions' reading the institutions and a pass
        over the works (a search by name, the institutions only); a round of collaborators
        two passes over the works."""
        works = self.bytes_of("works")
        institutions = self.bytes_of("institutions")
        if action in ("harvest", "retry"):
            return self.bytes_of("authors") + works
        if action == "institutions":
            return institutions if search else institutions + works
        if action == "collaborators":
            return 2 * max(1, rounds) * works
        raise ValueError(f"a snapshot does not answer {action!r}")

    def to_json(self) -> dict[str, Any]:
        return {
            "release": self.release,
            "complete": self.complete,
            "absent": self.absent,
            "missing": self.missing,
            "bytes": {e: c.bytes for e, c in self.entities.items()},
            "parts": {e: c.parts for e, c in self.entities.items()},
            "index": self.index,
            "indexed": self.indexed,
        }


def _listed_parts(manifest: Path) -> list[tuple[str, int]] | None:
    """The parts an entity's manifest lists, as ``(updated_date=…/part_….gz, bytes)``, or
    ``None`` without a readable manifest (the files are ``files``, before 2026 ``entries``)."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = (data.get("files") or data.get("entries")) if isinstance(data, dict) else None
    if not isinstance(files, list):
        return None
    out = []
    for f in files:
        try:
            date, name = str(f["url"]).rsplit("/", 2)[1:]
            out.append((f"{date}/{name}", int(f["meta"]["content_length"])))
        except (KeyError, TypeError, ValueError):
            return None
    return out


class Snapshot:
    """A downloaded OpenAlex snapshot, read by streaming its partitions.

    *progress* receives ``(fraction, message)`` as bytes are read; *cancel* is
    asked every few thousand lines whether to stop (:class:`Cancelled`); *jobs*
    worker processes read that many parts at once.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        progress: Callable[[float, str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        jobs: int = 1,
        use_index: bool = True,
    ) -> None:
        self.root = Path(root)
        #: Worker processes reading parts at once (1: in this process).
        self.jobs = max(1, int(jobs))
        self.use_index = use_index
        self._index: Any = None  # opened at the first query: SnapshotIndex, or False
        if not self.root.is_dir():
            raise FileNotFoundError(f"{self.root} is not a folder")
        self._progress = progress
        self._cancel = cancel
        self.report = ScanReport()
        found = [e for e in ENTITIES if self.entity_dir(e) is not None]
        if not found:
            raise FileNotFoundError(
                f"{self.root} holds no OpenAlex snapshot: expected data/jsonl/works/, "
                "data/jsonl/authors/ … with updated_date=… folders of part files"
            )

    # ── the layout ──
    def entity_dir(self, entity: str) -> Path | None:
        """The folder of *entity*'s partitions, or ``None`` when the snapshot has none."""
        for candidate in (
            self.root / "data" / "jsonl" / entity,
            self.root / "data" / entity,
            self.root / entity,
        ):
            if candidate.is_dir() and any(candidate.glob("updated_date=*")):
                return candidate
        return None

    def partitions(self, entity: str) -> list[Partition]:
        """*entity*'s part files, oldest update date first."""
        folder = self.entity_dir(entity)
        if folder is None:
            return []
        out = []
        for path in folder.glob("updated_date=*/part_*.gz"):
            m = _DATE.search(path.parent.name)
            if m:
                out.append(Partition(entity, m.group(1), path, path.stat().st_size))
        return sorted(out, key=lambda p: (p.date, p.path.name))

    def release(self) -> str:
        """The release date: the manifest's, else the newest partition's."""
        for folder in (self.root / "data" / "jsonl", self.root / "data", self.root):
            manifest = folder / "manifest.json"
            if manifest.is_file():
                try:
                    date = json.loads(manifest.read_text(encoding="utf-8")).get("date")
                except (ValueError, OSError):
                    date = None
                if isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    return date
        dates = [p.date for e in ENTITIES for p in self.partitions(e)]
        return max(dates) if dates else "1970-01-01"

    def retrieved_at(self) -> datetime:
        """When its records were read from the index: the release date, at midnight UTC."""
        return datetime.fromisoformat(self.release()).replace(tzinfo=timezone.utc)

    def size(self, entities: Iterable[str] = ENTITIES) -> int:
        """Bytes of the part files of *entities* (compressed)."""
        return sum(p.size for e in entities for p in self.partitions(e))

    def check(self) -> SnapshotCheck:
        """The folder against the manifests: each entity's parts and bytes, and the parts
        listed but missing here or of another size (a download that stopped midway). An
        entity without a manifest counts the parts found. Every part is looked at (its
        size), none is read."""
        from .snapshot_index import index_state, indexed_sizes

        cut = indexed_sizes(self.root)  # the parts an index cut: their sizes changed
        out: dict[str, EntityCheck] = {}
        for entity in ENTITIES:
            folder = self.entity_dir(entity)
            if folder is None:
                continue
            listed = _listed_parts(folder / "manifest.json")
            if listed is None:
                found = self.partitions(entity)
                out[entity] = EntityCheck(entity, len(found), sum(p.size for p in found),
                                          listed=False)  # fmt: skip
                continue
            missing = 0
            for rel, size in listed:
                try:
                    found = (folder / rel).stat().st_size
                except OSError:
                    missing += 1
                    continue
                missing += found != size and found != cut.get((entity, rel))
            out[entity] = EntityCheck(entity, len(listed), sum(n for _, n in listed), missing)
        return SnapshotCheck(self.root, self.release(), out, index_state(self.root))

    @property
    def index(self) -> Any:
        """The snapshot's complete index of this release, or ``None`` (then every query
        reads whole parts); one whose parts changed size since is not used."""
        if self._index is None:
            self._index = False
            if self.use_index:
                from .snapshot_index import SnapshotIndex

                found = SnapshotIndex.open(self.root, self.release())
                if found is not None and not any(
                    found.stale(self.entity_dir(e), e)
                    for e in found.data["entities"]
                    if self.entity_dir(e) is not None
                ):
                    self._index = found
        return self._index or None

    # ── streaming ──
    def scan(self, query: Query, *, what: str = "") -> dict[str, dict[str, Any]]:
        """Every record of the query's entity that it accepts, by id, the newest partition's
        copy; deleted works are left out. With *jobs* above 1, the parts are read in that
        many worker processes."""
        started = time.perf_counter()
        found: dict[str, dict[str, Any]] = {}
        for records in self._part_records(query, what):
            found.update(records)  # parts in date order: the newest copy wins
        if query.entity == "works" and found:
            gone = self.deleted(set(found))
            self.report.deleted += len(gone)
            for wid in gone:
                del found[wid]
        self.report.kept += len(found)
        self.report.seconds += time.perf_counter() - started
        return found

    def scan_into(
        self,
        query: Query,
        store: RecordStore,
        *,
        what: str = "",
        seen: Callable[[str, dict[str, Any]], None] | None = None,
        skip: Collection[str] = (),
        after_part: Callable[[str], None] | None = None,
    ) -> int:
        """The records :meth:`scan` gives, put in *store* as each part is read instead of
        kept in memory; *seen* receives each one as it is put (to index it). Returns how many
        the store holds for this pass, the deleted works dropped.

        A pass resumed leaves out the parts in *skip* (``updated_date=…/part_….gz``, read
        by a run before, their records in *store*); *after_part* is told each part once its
        records are in *store*."""
        started = time.perf_counter()
        kept: set[str] = set(store.ids()) if skip else set()
        for rel, records in self._records_by_part(query, what, skip=skip):
            for rid, record in records.items():
                store.put(rid, record)
                if seen is not None:
                    seen(rid, record)
                kept.add(rid)
            if after_part is not None:
                after_part(rel)
        if query.entity == "works" and kept:
            gone = self.deleted(kept)
            self.report.deleted += len(gone)
            for wid in gone:
                store.drop(wid)
            kept -= gone
        self.report.kept += len(kept)
        self.report.seconds += time.perf_counter() - started
        return len(kept)

    def _part_records(self, query: Query, what: str) -> Iterator[dict[str, dict[str, Any]]]:
        """Each part's records, in date order, as soon as that part is read."""
        for _rel_path, records in self._records_by_part(query, what):
            yield records

    def _records_by_part(
        self, query: Query, what: str, *, skip: Collection[str] = ()
    ) -> Iterator[tuple[str, dict[str, dict[str, Any]]]]:
        """Each part's records, in date order, as soon as that part is read, with the part
        (``updated_date=…/part_….gz``): with an index, only the members that may hold what
        *query* asks, of the parts that have some; the parts in *skip* left out."""
        parts = [p for p in self.partitions(query.entity) if _rel(p) not in skip]
        index = self.index
        spans: dict[str, list[tuple[int, int]]] | None = None
        if index is not None and index.supports(query):
            spans = index.members(query)
            parts = [p for p in parts if _rel(p) in spans]
        sizes = [p.size if spans is None else sum(n for _, n in spans[_rel(p)]) for p in parts]
        total = sum(sizes) or 1
        report = self.report
        report.passes += 1
        message = f"snapshot: {query.entity} {what}".strip()
        if self.jobs > 1 and len(parts) > 1:
            results = self._parallel(parts, query, total, message, spans)
        else:
            results = self._serial(parts, query, total, message, spans)
        for part, size, (records, lines, parsed) in zip(parts, sizes, results, strict=True):
            report.lines += lines
            report.parsed += parsed
            report.bytes += size
            report.members += len(spans[_rel(part)]) if spans is not None else 0
            report.by_entity[query.entity] = report.by_entity.get(query.entity, 0) + size
            yield _rel(part), records

    def _serial(
        self,
        parts: list[Partition],
        query: Query,
        total: int,
        message: str,
        spans: Mapping[str, list[tuple[int, int]]] | None = None,
    ) -> Iterator[tuple[dict[str, dict[str, Any]], int, int]]:
        done = 0
        for part in parts:
            if spans is not None:
                members = spans[_rel(part)]
                self._tick(done / total, message)
                yield _scan_members(str(part.path), members, query)
                done += sum(n for _, n in members)
                continue
            yield _scan_part(
                str(part.path),
                query,
                lambda pos, done=done: self._tick((done + pos) / total, message),
            )
            done += part.size

    def _parallel(
        self,
        parts: list[Partition],
        query: Query,
        total: int,
        message: str,
        spans: Mapping[str, list[tuple[int, int]]] | None = None,
    ) -> Iterator[tuple[dict[str, dict[str, Any]], int, int]]:
        """The parts read in worker processes, each part's result given back in date order
        and let go of once given. At most :data:`READ_AHEAD` parts per worker are read ahead
        of the one given back: however slow the reader of the results, memory holds a few
        parts' records, never all of them. Stopped, the parts not started are dropped."""
        import multiprocessing
        from concurrent.futures import Future, ProcessPoolExecutor
        from concurrent.futures import TimeoutError as NotYet

        sizes = [p.size if spans is None else sum(n for _, n in spans[_rel(p)]) for p in parts]
        ahead = max(1, self.jobs * READ_AHEAD)
        done = [0]
        context = multiprocessing.get_context("spawn")
        pool = ProcessPoolExecutor(
            max_workers=self.jobs, mp_context=context, initializer=ignore_stop_signals
        )
        futures: dict[int, Future[Any]] = {}

        def submit(i: int) -> None:
            part = parts[i]
            if spans is None:
                future = pool.submit(_scan_part, str(part.path), query, None)
            else:
                future = pool.submit(_scan_members, str(part.path), spans[_rel(part)], query)
            future.add_done_callback(lambda _f, n=sizes[i]: done.__setitem__(0, done[0] + n))
            futures[i] = future

        try:
            queued = 0
            for i in range(len(parts)):
                while queued < len(parts) and queued <= i + ahead:
                    submit(queued)
                    queued += 1
                while True:
                    try:
                        result = futures[i].result(timeout=0.5)
                        break
                    except NotYet:
                        self._tick(done[0] / total, message)
                del futures[i]
                self._tick(done[0] / total, message)
                yield result
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def _tick(self, fraction: float, message: str) -> None:
        self._check_cancel()
        self._report(fraction, message)

    def deleted(self, work_ids: set[str]) -> set[str]:
        """Those of *work_ids* the deletion log lists (it is streamed, never loaded whole)."""
        folder = self.entity_dir("works")
        log = folder / "deleted_ids.csv.gz" if folder is not None else None
        if log is None or not log.is_file() or not work_ids:
            return set()
        wanted = {w.encode() for w in work_ids}
        out: set[str] = set()
        with gzip.open(log, "rb") as fh:
            for line in fh:
                m = _WORK.search(line)
                if m and m.group(1) in wanted:
                    out.add(m.group(1).decode())
        return out

    def _check_cancel(self) -> None:
        if self._cancel is not None and self._cancel():
            raise Cancelled("reading the snapshot was cancelled; nothing was written")

    def _report(self, fraction: float, message: str) -> None:
        if self._progress is not None:
            self._progress(min(1.0, fraction), message)

    # ── queries ──
    def authors(self, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """The author records of *ids*."""
        wanted = frozenset(i for i in ids if i)
        if not wanted:
            return {}
        return self.scan(Query("authors", ids=wanted), what="by id")

    def institutions(
        self,
        *,
        ids: Iterable[str] = (),
        rors: Iterable[str] = (),
        lineage: Iterable[str] = (),
        names: Iterable[str] = (),
        everything: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Institution records: by id, by ROR id, every unit whose lineage holds one of
        *lineage*, whose names hold every word of one of *names*, or all of them."""
        query = Query(
            "institutions",
            ids=frozenset(i for i in ids if i),
            rors=frozenset(r.lower() for r in rors if r),
            lineage=frozenset(i for i in lineage if i),
            names=tuple(n for n in names if n),
            everything=everything,
        )
        return self.scan(query, what="institutions")

    def works(
        self,
        *,
        author_ids: Iterable[str] = (),
        lineage: Iterable[str] = (),
        dois: Iterable[str] = (),
        years: Years = None,
    ) -> dict[str, dict[str, Any]]:
        """Works signed by one of *author_ids*, or by an author at one of *lineage* or a
        unit below it, or with one of *dois*; within *years* (by publication date)."""
        query = Query(
            "works",
            author_ids=frozenset(a for a in author_ids if a),
            lineage=frozenset(i for i in lineage if i),
            dois=frozenset(d for d in (bare_doi(x) for x in dois) if d),
            years=tuple(years) if years is not None else None,  # type: ignore[arg-type]
        )
        return self.scan(query, what="works")


@dataclass(frozen=True)
class Query:
    """What one pass looks for: ids, ROR ids, lineages, DOIs, names, years. It is plain
    data, so that a worker process builds the same tests from it."""

    entity: str
    ids: frozenset[str] = frozenset()
    author_ids: frozenset[str] = frozenset()
    lineage: frozenset[str] = frozenset()
    dois: frozenset[str] = frozenset()
    rors: frozenset[str] = frozenset()
    names: tuple[str, ...] = ()
    years: tuple[int | None, int | None] | None = None
    everything: bool = False
    #: The fields kept of each record accepted (all of them when empty).
    select: tuple[str, ...] = ()


def _tests(q: Query) -> tuple[Callable[[bytes], bool], Callable[[dict[str, Any]], bool]]:
    """The quick test on a raw line (true for every line the second could accept), and the
    test on the parsed record."""
    if q.everything:
        return (lambda line: True), (lambda r: True)
    ids = {i.encode() for i in q.ids}
    authors = {a.encode() for a in q.author_ids}
    lineage = {i.encode() for i in q.lineage}
    rors = {r.encode() for r in q.rors}
    dois = set(q.dois)
    doi_bytes = {d.encode() for d in dois}
    word_sets = [set(_words(n)) for n in q.names if _words(n)]
    first, last = q.years if q.years is not None else (None, None)
    pattern = _AUTHOR if q.entity == "authors" else (_WORK if q.entity == "works" else _INSTITUTION)

    def maybe(line: bytes) -> bool:
        if word_sets:
            return True
        if ids and ids & set(pattern.findall(line)):
            return True
        if authors and authors & set(_AUTHOR.findall(line)):
            return True
        if lineage and lineage & set(_INSTITUTION.findall(line)):
            return True
        if rors and rors & set(_ROR.findall(line.lower())):
            return True
        return bool(doi_bytes) and any(d.lower() in doi_bytes for d in _DOI.findall(line))

    def keep(r: dict[str, Any]) -> bool:
        rid = (short_id(r.get("id")) or "").encode()
        if q.entity == "works":
            if not in_window(r, first, last):
                return False
            return (
                rid in ids
                or bool(authors and _authors_of(r) & authors)
                or bool(lineage and _lineages_of(r) & lineage)
                or bool(dois and bare_doi(r.get("doi")) in dois)
            )
        if rid in ids:
            return True
        if q.entity != "institutions":
            return False
        ror = (r.get("ror") or "").rsplit("/", 1)[-1].lower().encode()
        if ror and ror in rors:
            return True
        if lineage & {(short_id(x) or "").encode() for x in r.get("lineage") or []}:
            return True
        if word_sets:
            shown = [r.get("display_name") or ""]
            shown += list(r.get("display_name_acronyms") or [])
            shown += list(r.get("display_name_alternatives") or [])
            return any(ws <= set(_words(s)) for ws in word_sets for s in shown)
        return False

    return maybe, keep


def ignore_stop_signals() -> None:
    """In a worker process: Ctrl-C, which a terminal sends to the whole process group, is
    for the main process, which stops the workers itself. SIGTERM is left as it is: a worker
    whose main process is gone ends with the service stopping it."""
    import signal

    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _scan_part(
    path: str, query: Query, tick: Callable[[int], None] | None
) -> tuple[dict[str, dict[str, Any]], int, int]:
    """One part: the records *query* accepts, by id, and the lines read and parsed. Runs in a
    worker process too (then without *tick*, which receives the compressed bytes read)."""
    maybe, keep = _tests(query)
    found: dict[str, dict[str, Any]] = {}
    lines = parsed = 0
    with open(path, "rb") as raw, gzip.GzipFile(fileobj=raw) as gz:
        reader = io.BufferedReader(gz, buffer_size=1 << 20)
        for line in reader:
            lines += 1
            if tick is not None and lines % 20000 == 1:
                tick(raw.tell())
            parsed += _take(line, maybe, keep, query, found)
    return found, lines, parsed


def _take(
    line: bytes,
    maybe: Callable[[bytes], bool],
    keep: Callable[[dict[str, Any]], bool],
    query: Query,
    found: dict[str, dict[str, Any]],
) -> bool:
    """Test one raw line; when it may match, parse it and keep the record if it does.
    Returns whether it was parsed."""
    if not maybe(line):
        return False
    try:
        record = json.loads(line)
    except ValueError:
        return True
    if isinstance(record, dict) and keep(record):
        rid = short_id(record.get("id"))
        if rid:
            if query.select:
                record = {k: record[k] for k in query.select if k in record}
            found[rid] = record
    return True


def _scan_members(
    path: str, members: Sequence[tuple[int, int]], query: Query
) -> tuple[dict[str, dict[str, Any]], int, int]:
    """The records *query* accepts in the *members* of an indexed part (their ``(offset,
    length)``), tested line by line as :func:`_scan_part` does. Runs in a worker too."""
    from .snapshot_index import read_spans

    maybe, keep = _tests(query)
    found: dict[str, dict[str, Any]] = {}
    lines = parsed = 0
    for block in read_spans(path, members):
        for line in block.split(b"\n")[:-1]:  # a member holds whole lines
            lines += 1
            parsed += _take(line, maybe, keep, query, found)
    return found, lines, parsed


def _rel(part: Partition) -> str:
    """A part's path below its entity's folder (``updated_date=…/part_….gz``)."""
    return f"{part.path.parent.name}/{part.path.name}"


def in_window(work: dict[str, Any], first: int | None, last: int | None) -> bool:
    """Whether a work's publication date falls in the years *first* to *last* (inclusive),
    as the API's ``from_publication_date`` and ``to_publication_date`` filters say."""
    date = work.get("publication_date")
    if not isinstance(date, str) or not date:
        year = work.get("publication_year")
        date = f"{year}-01-01" if isinstance(year, int) else ""
    if first and date < f"{first}-01-01":
        return False
    return not (last and date and date > f"{last}-12-31")


def _words(text: str) -> list[str]:
    from .names import words

    return words(text)


def unpack(data: bytes) -> dict[str, Any]:
    """A record as a :class:`RecordStore` keeps it (compressed JSON), parsed."""
    return json.loads(zlib.decompress(data))


class PackedSource:
    """One person's answers from what :meth:`SnapshotSource.person_inputs` gathered, in a
    worker process: the same answers :class:`SnapshotSource` gives for that person."""

    label = "snapshot"

    def __init__(self, inputs: Mapping[str, Any]) -> None:
        self.at = inputs["at"]
        self._authors = {a: None if d is None else unpack(d) for a, d in inputs["authors"].items()}
        self._works = [unpack(d) for d in inputs["works"]]
        self._dois = {k: [unpack(d) for d in v if d is not None] for k, v in inputs["dois"].items()}

    def author(self, author_id: str) -> Fetched | None:
        record = self._authors.get(author_id)
        return None if record is None else Fetched(record, self.at, False)

    def works_by_authors(self, author_ids: Sequence[str], years: Years) -> Fetched:
        wanted = {a.encode() for a in author_ids if a}
        first, last = years if years is not None else (None, None)
        works = [w for w in self._works if _authors_of(w) & wanted and in_window(w, first, last)]
        return Fetched(_sorted(works), self.at, False)

    def works_by_dois(self, dois: Iterable[str]) -> list[tuple[dict[str, Any], Fetched]]:
        fetched = Fetched(None, self.at, False)
        out = []
        for doi in sorted({d for d in (bare_doi(x) for x in dois) if d}):
            works = [w for w in self._dois.get(doi, ()) if bare_doi(w.get("doi")) == doi]
            out += [(w, fetched) for w in _sorted(works)]
        return out


def _year_of(work: Mapping[str, Any]) -> int | None:
    """A work's publication year: its date's, else its year (what :func:`in_window` reads)."""
    date = work.get("publication_date")
    if isinstance(date, str) and date[:4].isdigit():
        return int(date[:4])
    year = work.get("publication_year")
    return year if isinstance(year, int) else None


def _authors_of(work: dict[str, Any]) -> set[bytes]:
    return {
        (short_id((a.get("author") or {}).get("id")) or "").encode()
        for a in work.get("authorships") or []
    } - {b""}


def _lineages_of(work: dict[str, Any]) -> set[bytes]:
    return {
        (short_id(x) or "").encode()
        for a in work.get("authorships") or []
        for inst in a.get("institutions") or []
        for x in (inst.get("lineage") or [inst.get("id")])
    } - {b""}


def _sorted(works: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(works, key=lambda w: (w.get("publication_date") or "", w.get("id") or ""))


class RecordStore:
    """Records kept on disk while a job reads a snapshot, so that memory holds their index only.

    Each record is compressed on its own and appended to a temporary file (in *folder*, else
    the system's temporary folder; it is gone once the store is closed or the program ends),
    its place indexed by id; a later copy of a record replaces the earlier one.

    Given a *path* instead, the records go to that file and their places to a journal beside
    it (``<path>.index``, a line per record kept or dropped), both kept after the store is
    closed: :meth:`checkpoint` writes them down and says their sizes, and a store opened
    again with those *sizes* holds what it held then (what came after is cut off).
    """

    def __init__(
        self,
        folder: Path | None = None,
        *,
        path: Path | None = None,
        sizes: Sequence[int] | None = None,
    ) -> None:
        self._at: dict[str, tuple[int, int]] = {}
        self._end = 0
        self._lock = threading.Lock()
        self._journal: Any = None
        if path is None:
            if folder is not None:
                Path(folder).mkdir(parents=True, exist_ok=True)
            self._fh = tempfile.TemporaryFile(prefix="cartolex-snapshot-", dir=folder)
            return
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        journal = path.with_name(path.name + ".index")
        if sizes is None or not path.is_file() or not journal.is_file():
            sizes = (0, 0)
        self._fh = open(path, "r+b" if path.is_file() else "w+b")  # noqa: SIM115 (kept open)
        self._fh.truncate(sizes[0])
        self._journal = open(journal, "r+b" if journal.is_file() else "w+b")  # noqa: SIM115
        self._journal.truncate(sizes[1])
        self._journal.seek(0)
        for line in self._journal:
            rid, offset, length = line.decode("utf-8").rstrip("\n").split("\t")
            if int(length):
                self._at[rid] = (int(offset), int(length))
            else:
                self._at.pop(rid, None)
        self._end = sizes[0]

    def put(self, rid: str, record: Mapping[str, Any]) -> None:
        """Keep *record* under *rid* (replacing an earlier copy)."""
        text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        data = zlib.compress(text.encode("utf-8"), 3)
        with self._lock:
            self._fh.seek(self._end)
            self._fh.write(data)
            self._at[rid] = (self._end, len(data))
            if self._journal is not None:
                self._journal.write(f"{rid}\t{self._end}\t{len(data)}\n".encode())
            self._end += len(data)

    def get(self, rid: str | None) -> dict[str, Any] | None:
        """The record kept under *rid*, read back from disk, or ``None``."""
        data = self.get_raw(rid)
        return None if data is None else json.loads(zlib.decompress(data))

    def get_raw(self, rid: str | None) -> bytes | None:
        """The record kept under *rid* as stored (compressed JSON: :func:`unpack`), or ``None``."""
        with self._lock:
            at = self._at.get(rid) if rid else None
            if at is None:
                return None
            self._fh.seek(at[0])
            return self._fh.read(at[1])

    def drop(self, rid: str) -> None:
        """Forget the record kept under *rid*."""
        with self._lock:
            self._at.pop(rid, None)
            if self._journal is not None:
                self._journal.write(f"{rid}\t0\t0\n".encode())

    def checkpoint(self) -> tuple[int, int]:
        """Write down the records and their journal (a store with a *path*); their sizes,
        to open the store again as it is now."""
        with self._lock:
            for fh in (self._fh, self._journal):
                fh.flush()
                os.fsync(fh.fileno())
            return self._end, self._journal.tell()

    def records(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """Every record kept, with its id, in the order of the file (read straight through)."""
        with self._lock:
            places = sorted(self._at.items(), key=lambda item: item[1][0])
        for rid, (offset, length) in places:
            with self._lock:
                self._fh.seek(offset)
                data = self._fh.read(length)
            yield rid, json.loads(zlib.decompress(data))

    def ids(self) -> list[str]:
        """The ids of the records kept."""
        with self._lock:
            return list(self._at)

    def __len__(self) -> int:
        return len(self._at)

    def close(self) -> None:
        """Let go of the file (its space is freed; a store with a *path* keeps its files)."""
        self._fh.close()
        if self._journal is not None:
            self._journal.close()


@dataclass(frozen=True)
class _Unit:
    """What the institutions' questions read of one record, kept in memory: its searched
    names (the display name and acronyms), ROR id, lineage and works count."""

    names: tuple[str, ...]
    ror: str
    lineage: frozenset[str]
    works: int


class _KeptPasses:
    """The passes of one prefetch, their findings kept in files so that a stopped job goes on
    (``state.json`` beside the stores: what was asked, and for each pass the parts read, the
    stores' sizes, whether it ended). A state of another release or another question is set
    aside: its files are dropped."""

    def __init__(self, source: SnapshotSource, authors: Sequence[str], dois: Sequence[str]):
        from cartolex.project.checkpoints import work_key

        self.source = source
        self.folder: Path = source._kept  # type: ignore[assignment]
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "state.json"
        digest = work_key({"release": source.snapshot.release(), "authors": list(authors),
                           "dois": list(dois)})  # fmt: skip
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if state.get("digest") != digest:
            for old in self.folder.iterdir():
                old.unlink()
            state = {"digest": digest, "passes": {}}
        self.state = state

    def _save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".part")
        tmp.write_text(json.dumps(self.state), encoding="utf-8")
        os.replace(tmp, self.path)

    def run(
        self,
        name: str,
        query: Query,
        *,
        what: str,
        seen: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> RecordStore:
        """The store of pass *name*, read (or read further) as *query* asks."""
        mine = self.state["passes"].get(name) or {}
        old = self.source._authors if name == "authors" else self.source._works
        old.close()  # the temporary store made with the source, empty
        store = RecordStore(path=self.folder / f"{name}.records", sizes=mine.get("sizes"))
        if seen is not None:  # what a stopped job had read is indexed again
            for rid, record in store.records():
                seen(rid, record)
        if mine.get("done"):
            return store
        parts: list[str] = list(mine.get("parts") or ())
        last = [time.monotonic()]

        def save(done: bool = False) -> None:
            self.state["passes"][name] = {"parts": parts, "sizes": list(store.checkpoint()),
                                          "done": done}  # fmt: skip
            self._save()
            last[0] = time.monotonic()

        def after_part(rel: str) -> None:
            parts.append(rel)
            if (
                len(parts) % SAVE_EVERY_PARTS == 0
                or time.monotonic() - last[0] >= SAVE_EVERY_SECONDS
            ):
                save()

        self.source.snapshot.scan_into(query, store, what=what, seen=seen, skip=set(parts),
                                       after_part=after_part)  # fmt: skip
        save(done=True)
        return store


class SnapshotSource:
    """The requests the finders make of OpenAlex, answered from a :class:`Snapshot`.

    A finder that knows everything it will ask for (the harvest) calls
    :meth:`prefetch` first: one pass over the authors and one over the works
    for the whole job. Other questions cost one pass each; the institutions
    are read once.

    What the passes find is kept on disk (:class:`RecordStore`, in *spill*, else the
    system's temporary folder) and memory holds indexes only: the records by id, the works
    by the authors and DOIs asked for, the institutions' names and lineages. A work is
    kept with the fields the API is asked for
    (:data:`~cartolex.collect.openalex.WORK_FIELDS`), as the API gives it; the works an
    institution's reading folds, with the fields that reading asks for.
    """

    label = "snapshot"

    def __init__(self, snapshot: Snapshot, *, spill: Path | None = None) -> None:
        self.snapshot = snapshot
        self.at = snapshot.retrieved_at()
        self.spill = spill
        self._authors = RecordStore(spill)
        self._works = RecordStore(spill)
        self._by_author: dict[str, list[str]] = defaultdict(list)
        self._by_doi: dict[str, list[str]] = defaultdict(list)
        #: Each work's publication year, to leave out of a window before reading it back.
        self._years: dict[str, int] = {}
        self._fetched_authors: set[str] = set()
        self._fetched_dois: set[str] = set()
        self._institutions: RecordStore | None = None
        self._units: dict[str, _Unit] = {}
        #: Where the passes' findings are kept for a resumed job (:meth:`keep_findings`).
        self._kept: Path | None = None

    def _fetched(self, data: Any) -> Fetched:
        return Fetched(data, self.at, False)

    def close(self) -> None:
        """Let go of the records kept on disk."""
        for store in (self._authors, self._works, self._institutions):
            if store is not None:
                store.close()

    # ── prefetch ──
    def keep_findings(self, key: str, *, resume: bool) -> None:
        """Keep what the passes of :meth:`prefetch` find in files, ``<spill>/snapshot-<key>/``
        (the spill folder, else the system's temporary folder), with their state written every
        :data:`SAVE_EVERY_PARTS` parts or :data:`SAVE_EVERY_SECONDS` seconds: a job stopped
        later goes on from there (*resume*) instead of reading the snapshot again; without
        *resume*, what an earlier job kept there is dropped. *key* names the job."""
        folder = Path(self.spill or tempfile.gettempdir()) / f"snapshot-{key}"
        if not resume and folder.exists():
            shutil.rmtree(folder)
        self._kept = folder

    def forget_findings(self) -> None:
        """Drop the findings kept for a resumed job (the job is over)."""
        if self._kept is not None:
            self._authors.close()
            self._works.close()
            shutil.rmtree(self._kept, ignore_errors=True)
            self._kept = None

    def prefetch(
        self, *, author_ids: Iterable[str] = (), dois: Iterable[str] = (), years: Years = None
    ) -> None:
        """Read, in one pass per entity, the author records and the works a job will ask for.

        The works are read without a window (each question applies its own). With findings
        kept (:meth:`keep_findings`), a pass a stopped job finished is not read again, and
        one it began goes on from its last saved part."""
        authors = sorted({a for a in author_ids if a} - self._fetched_authors)
        wanted_dois = sorted({d for d in (bare_doi(x) for x in dois) if d} - self._fetched_dois)
        passes = _KeptPasses(self, authors, wanted_dois) if self._kept is not None else None
        if authors:
            query = Query("authors", ids=frozenset(authors))
            if passes is None:
                self.snapshot.scan_into(query, self._authors, what="by id")
            else:
                self._authors = passes.run("authors", query, what="by id")
        if authors or wanted_dois:
            asked, doi_set = {a.encode() for a in authors}, set(wanted_dois)

            def index(rid: str, record: dict[str, Any]) -> None:
                year = _year_of(record)
                if year is not None:
                    self._years[rid] = year
                for aid in _authors_of(record) & asked:
                    self._by_author[aid.decode()].append(rid)
                doi = bare_doi(record.get("doi"))
                if doi in doi_set:
                    self._by_doi[doi].append(rid)

            query = Query(
                "works",
                author_ids=frozenset(authors),
                dois=frozenset(wanted_dois),
                select=_WORK_SELECT,
            )
            if passes is None:
                self.snapshot.scan_into(query, self._works, what="works", seen=index)
            else:
                self._works = passes.run("works", query, what="works", seen=index)
        self._fetched_authors.update(authors)
        self._fetched_dois.update(wanted_dois)

    def _ensure(self, author_ids: Sequence[str] = (), dois: Iterable[str] = ()) -> None:
        missing = [a for a in author_ids if a not in self._fetched_authors]
        missing_dois = [d for d in (bare_doi(x) for x in dois) if d and d not in self._fetched_dois]
        if missing or missing_dois:
            self.prefetch(author_ids=missing, dois=missing_dois)

    # ── the questions ──
    def author(self, author_id: str) -> Fetched | None:
        self._ensure([author_id])
        record = self._authors.get(author_id)
        return self._fetched(record) if record is not None else None

    def works_by_authors(self, author_ids: Sequence[str], years: Years) -> Fetched:
        ids = sorted({a for a in author_ids if a})
        if not ids:
            raise ValueError("no author record to ask for")
        self._ensure(ids)
        wanted = {a.encode() for a in ids}
        first, last = years if years is not None else (None, None)
        works = []
        for wid in sorted({w for a in ids for w in self._by_author.get(a, ())}):
            year = self._years.get(wid)
            if year is not None and ((first and year < first) or (last and year > last)):
                continue  # out of the window: not read back (the window is whole years)
            work = self._works.get(wid)
            if work is not None and _authors_of(work) & wanted and in_window(work, first, last):
                works.append(work)
        return self._fetched(_sorted(works))

    def person_inputs(
        self, author_ids: Sequence[str], dois: Iterable[str], years: Years
    ) -> dict[str, Any]:
        """What :class:`PackedSource` needs to answer one person's questions, as stored
        (compressed, not parsed: a worker process parses them): their author records, their
        works in *years*, the works of *dois*."""
        ids = sorted({a for a in author_ids if a})
        wanted = sorted({d for d in (bare_doi(x) for x in dois) if d})
        self._ensure(ids, wanted)
        first, last = years if years is not None else (None, None)
        works = []
        for wid in sorted({w for a in ids for w in self._by_author.get(a, ())}):
            year = self._years.get(wid)
            if year is not None and ((first and year < first) or (last and year > last)):
                continue
            works.append(self._works.get_raw(wid))
        return {
            "at": self.at,
            "authors": {a: self._authors.get_raw(a) for a in ids},
            "works": [w for w in works if w is not None],
            "dois": {
                d: [self._works.get_raw(w) for w in sorted(set(self._by_doi.get(d, ())))]
                for d in wanted
            },  # fmt: skip
        }

    def works_by_dois(self, dois: Iterable[str]) -> list[tuple[dict[str, Any], Fetched]]:
        wanted = sorted({d for d in (bare_doi(x) for x in dois) if d})
        self._ensure(dois=wanted)
        fetched = self._fetched(None)
        out = []
        for doi in wanted:
            found = [self._works.get(w) for w in sorted(set(self._by_doi.get(doi, ())))]
            works = [w for w in found if w is not None and bare_doi(w.get("doi")) == doi]
            out += [(w, fetched) for w in _sorted(works)]
        return out

    def _all_institutions(self) -> RecordStore:
        if self._institutions is None:
            store, units = RecordStore(self.spill), {}

            def index(rid: str, r: dict[str, Any]) -> None:
                units[rid] = _Unit(
                    names=(r.get("display_name") or "", *(r.get("display_name_acronyms") or [])),
                    ror=(r.get("ror") or "").rsplit("/", 1)[-1].lower(),
                    lineage=frozenset(
                        i for i in (short_id(x) for x in r.get("lineage") or []) if i
                    ),
                    works=int(r.get("works_count") or 0),
                )

            query = Query("institutions", everything=True)
            self.snapshot.scan_into(query, store, what="institutions", seen=index)
            self._institutions, self._units = store, units
        return self._institutions

    def institution(self, ref: str) -> Fetched | None:
        store = self._all_institutions()
        if ref.lower().startswith("ror:"):
            ror = ref[4:].lower()
            rid = next((i for i, u in sorted(self._units.items()) if u.ror == ror), None)
        else:
            rid = ref.upper()
        found = store.get(rid)
        return self._fetched(found) if found is not None else None

    def search_institutions(self, name: str) -> list[dict[str, Any]]:
        wanted = set(_words(name))
        if not wanted:
            return []
        store = self._all_institutions()
        hits = [
            (i, u)
            for i, u in sorted(self._units.items())
            if any(wanted <= set(_words(s)) for s in u.names)
        ]
        hits.sort(key=lambda hit: (-hit[1].works, hit[0]))
        return [r for r in (store.get(i) for i, _u in hits[:10]) if r is not None]

    def institution_units(self, roots: Sequence[str]) -> Fetched:
        wanted = {r for r in roots if r}
        if not wanted:
            raise ValueError("no institution to ask for")
        store = self._all_institutions()
        found = (store.get(i) for i, u in sorted(self._units.items()) if wanted & u.lineage)
        return self._fetched([r for r in found if r is not None])

    def works_by_institutions(self, roots: Sequence[str], years: Years) -> Fetched:
        found = self.snapshot.works(lineage=[r for r in roots if r], years=years)
        return self._fetched(_sorted(found.values()))

    def institution_work_pages(
        self, roots: Sequence[str], years: Years, *, cursor: str | None = None, read: int = 0
    ) -> Iterator[Page]:
        """The works signed at *roots* in pages of 100, read in one pass and kept on disk
        meanwhile, with the fields the reading folds. A page's cursor is the count of works
        before it (``snapshot:<n>``): resuming from one reads the snapshot again and goes on
        from there."""
        store, dates = RecordStore(self.spill), {}

        def date(rid: str, record: dict[str, Any]) -> None:
            dates[rid] = record.get("publication_date") or ""

        query = Query(
            "works",
            lineage=frozenset(r for r in roots if r),
            years=tuple(years) if years is not None else None,  # type: ignore[arg-type]
            select=(*_INSTITUTION_SELECT, "publication_date"),
        )
        try:
            self.snapshot.scan_into(query, store, what="works", seen=date)
            order = sorted(store.ids(), key=lambda w: (dates[w], w))
            total = len(order)
            start = _cursor_offset(cursor)
            if start >= total:
                yield Page([], cursor or "*", None, total, total, self.at)
                return
            for i in range(start, total, PER_PAGE):
                end = min(total, i + PER_PAGE)
                items = [r for r in (store.get(w) for w in order[i:end]) if r is not None]
                after = f"snapshot:{end}" if end < total else None
                yield Page(items, f"snapshot:{i}", after, total, end, self.at)
        finally:
            store.close()

    def works_of_authors(
        self,
        author_ids: Sequence[str],
        years: Years,
        *,
        max_authors: int | None = None,
        step: Step | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """The works of each author record (one pass; a snapshot never cuts author lists, so
        *max_authors* changes nothing, and the pass reports its own progress)."""
        ids = sorted({a for a in author_ids if a})
        found = self.snapshot.works(author_ids=ids, years=years) if ids else {}
        out: dict[str, list[dict[str, Any]]] = {a: [] for a in ids}
        for w in _sorted(found.values()):
            for aid in _authors_of(w):
                key = aid.decode()
                if key in out:
                    out[key].append(w)
        return out

    def fit_works(
        self,
        author_ids: Sequence[str],
        years: Years,
        *,
        left_out: Mapping[str, Collection[str]] | None = None,
        step: Step | None = None,
    ) -> dict[str, tuple[list[dict[str, Any]], int]]:
        """For each author record, the works a topical fit reads within *years* and how many
        it has there, as the API's :func:`~cartolex.collect.openalex.fit_works` reads them
        (:func:`~cartolex.collect.openalex.fit_reading`), from one pass."""
        found = self.works_of_authors(author_ids, years)
        skip = left_out or {}
        return {
            aid: (fit_reading(works, skip.get(aid, ())), len(works)) for aid, works in found.items()
        }


def _cursor_offset(cursor: str | None) -> int:
    """The works before the page a snapshot cursor names (0 for none, or another's)."""
    if cursor and cursor.startswith("snapshot:"):
        try:
            return max(0, int(cursor.split(":", 1)[1]))
        except ValueError:
            return 0
    return 0


def read_deleted_log(path: Path) -> Iterator[tuple[str, str]]:
    """``(work id, deletion date)`` rows of a deletion log, streamed."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            wid = short_id(row.get("work_id"))
            if wid:
                yield wid, row.get("deleted_date") or ""
