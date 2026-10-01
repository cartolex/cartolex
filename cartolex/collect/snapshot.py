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
holds the matches, never a partition. Deleted works are dropped, the deletion
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
import re
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .http import Cancelled, Fetched, Page
from .openalex import Years, bare_doi, short_id

__all__ = [
    "ENTITIES",
    "Partition",
    "Query",
    "ScanReport",
    "Snapshot",
    "SnapshotSource",
]

#: The entities cartolex reads.
ENTITIES = ("works", "authors", "institutions")
_AUTHOR = re.compile(rb"openalex\.org/(A\d+)")
_INSTITUTION = re.compile(rb"openalex\.org/(I\d+)")
_WORK = re.compile(rb"openalex\.org/(W\d+)")
_DOI = re.compile(rb'"doi"\s*:\s*"https?://(?:dx\.)?doi\.org/([^"]+)"', re.IGNORECASE)
_ROR = re.compile(rb"ror\.org/(0[0-9a-z]{6}\d{2})")
_DATE = re.compile(r"updated_date=(\d{4}-\d{2}-\d{2})")


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
    lines: int = 0
    parsed: int = 0
    kept: int = 0
    deleted: int = 0
    seconds: float = 0.0
    by_entity: dict[str, int] = field(default_factory=dict)

    def lines_per_second(self) -> float:
        return self.lines / self.seconds if self.seconds else 0.0


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
    ) -> None:
        self.root = Path(root)
        #: Worker processes reading parts at once (1: in this process).
        self.jobs = max(1, int(jobs))
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

    # ── streaming ──
    def scan(self, query: Query, *, what: str = "") -> dict[str, dict[str, Any]]:
        """Every record of the query's entity that it accepts, by id, the newest partition's
        copy; deleted works are left out. With *jobs* above 1, the parts are read in that
        many worker processes."""
        parts = self.partitions(query.entity)
        total = sum(p.size for p in parts) or 1
        started = time.perf_counter()
        found: dict[str, dict[str, Any]] = {}
        report = self.report
        report.passes += 1
        message = f"snapshot: {query.entity} {what}".strip()
        if self.jobs > 1 and len(parts) > 1:
            results = self._scan_parallel(parts, query, total, message)
        else:
            results = []
            done = 0
            for part in parts:
                results.append(
                    _scan_part(
                        str(part.path),
                        query,
                        lambda pos, done=done: self._tick((done + pos) / total, message),
                    )
                )
                done += part.size
        for part, (records, lines, parsed) in zip(parts, results, strict=True):
            found.update(records)  # parts in date order: the newest copy wins
            report.lines += lines
            report.parsed += parsed
            report.bytes += part.size
            report.by_entity[query.entity] = report.by_entity.get(query.entity, 0) + part.size
        if query.entity == "works" and found:
            gone = self.deleted(set(found))
            report.deleted += len(gone)
            for wid in gone:
                del found[wid]
        report.kept += len(found)
        report.seconds += time.perf_counter() - started
        return found

    def _scan_parallel(
        self, parts: list[Partition], query: Query, total: int, message: str
    ) -> list[tuple[dict[str, dict[str, Any]], int, int]]:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor, wait

        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=self.jobs, mp_context=context) as pool:
            futures = [pool.submit(_scan_part, str(p.path), query, None) for p in parts]
            pending = set(futures)
            done_bytes = 0
            sizes = {f: p.size for f, p in zip(futures, parts, strict=True)}
            while pending:
                finished, pending = wait(pending, timeout=0.5)
                done_bytes += sum(sizes[f] for f in finished)
                try:
                    self._tick(done_bytes / total, message)
                except Cancelled:
                    for f in pending:
                        f.cancel()
                    raise
            return [f.result() for f in futures]

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
            if not maybe(line):
                continue
            parsed += 1
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and keep(record):
                rid = short_id(record.get("id"))
                if rid:
                    found[rid] = record
    return found, lines, parsed


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


class SnapshotSource:
    """The requests the finders make of OpenAlex, answered from a :class:`Snapshot`.

    A finder that knows everything it will ask for (the harvest) calls
    :meth:`prefetch` first: one pass over the authors and one over the works
    for the whole job. Other questions cost one pass each; the institutions,
    a small entity, are read once and kept.
    """

    label = "snapshot"

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.at = snapshot.retrieved_at()
        self._authors: dict[str, dict[str, Any]] = {}
        self._works: dict[str, dict[str, Any]] = {}
        self._fetched_authors: set[str] = set()
        self._fetched_dois: set[str] = set()
        self._institutions: dict[str, dict[str, Any]] | None = None

    def _fetched(self, data: Any) -> Fetched:
        return Fetched(data, self.at, False)

    # ── prefetch ──
    def prefetch(
        self, *, author_ids: Iterable[str] = (), dois: Iterable[str] = (), years: Years = None
    ) -> None:
        """Read, in one pass per entity, the author records and the works a job will ask for.

        The works are read without a window (each question applies its own)."""
        authors = sorted({a for a in author_ids if a} - self._fetched_authors)
        wanted_dois = sorted({d for d in (bare_doi(x) for x in dois) if d} - self._fetched_dois)
        if authors:
            self._authors.update(self.snapshot.authors(authors))
        if authors or wanted_dois:
            self._works.update(self.snapshot.works(author_ids=authors, dois=wanted_dois))
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
        works = [
            w for w in self._works.values() if _authors_of(w) & wanted and in_window(w, first, last)
        ]
        return self._fetched(_sorted(works))

    def works_by_dois(self, dois: Iterable[str]) -> list[tuple[dict[str, Any], Fetched]]:
        wanted = sorted({d for d in (bare_doi(x) for x in dois) if d})
        self._ensure(dois=wanted)
        by_doi: dict[str, list[dict[str, Any]]] = {}
        for w in self._works.values():
            doi = bare_doi(w.get("doi"))
            if doi in wanted:
                by_doi.setdefault(doi, []).append(w)
        fetched = self._fetched(None)
        return [(w, fetched) for d in wanted for w in _sorted(by_doi.get(d, []))]

    def _all_institutions(self) -> dict[str, dict[str, Any]]:
        if self._institutions is None:
            self._institutions = self.snapshot.institutions(everything=True)
        return self._institutions

    def institution(self, ref: str) -> Fetched | None:
        insts = self._all_institutions()
        if ref.lower().startswith("ror:"):
            ror = ref[4:].lower()
            found = next(
                (
                    r
                    for _, r in sorted(insts.items())
                    if (r.get("ror") or "").rsplit("/", 1)[-1].lower() == ror
                ),
                None,
            )
        else:
            found = insts.get(ref.upper())
        return self._fetched(found) if found is not None else None

    def search_institutions(self, name: str) -> list[dict[str, Any]]:
        wanted = set(_words(name))
        if not wanted:
            return []
        hits = []
        for _, r in sorted(self._all_institutions().items()):
            shown = [r.get("display_name") or "", *(r.get("display_name_acronyms") or [])]
            if any(wanted <= set(_words(s)) for s in shown):
                hits.append(r)
        return sorted(hits, key=lambda r: (-int(r.get("works_count") or 0), r.get("id") or ""))[:10]

    def institution_units(self, roots: Sequence[str]) -> Fetched:
        wanted = {r for r in roots if r}
        if not wanted:
            raise ValueError("no institution to ask for")
        units = [
            r
            for _, r in sorted(self._all_institutions().items())
            if wanted & {short_id(x) for x in r.get("lineage") or []}
        ]
        return self._fetched(units)

    def works_by_institutions(self, roots: Sequence[str], years: Years) -> Fetched:
        found = self.snapshot.works(lineage=[r for r in roots if r], years=years)
        return self._fetched(_sorted(found.values()))

    def institution_work_pages(
        self, roots: Sequence[str], years: Years, *, cursor: str | None = None, read: int = 0
    ) -> Iterator[Page]:
        """The works of :meth:`works_by_institutions` as one page (the snapshot is on this
        computer: there is no cursor to keep)."""
        fetched = self.works_by_institutions(roots, years)
        items = list(fetched.data)
        yield Page(items, "*", None, len(items), len(items), fetched.retrieved_at)

    def works_of_authors(
        self, author_ids: Sequence[str], years: Years
    ) -> dict[str, list[dict[str, Any]]]:
        ids = sorted({a for a in author_ids if a})
        found = self.snapshot.works(author_ids=ids, years=years) if ids else {}
        out: dict[str, list[dict[str, Any]]] = {a: [] for a in ids}
        for w in _sorted(found.values()):
            for aid in _authors_of(w):
                key = aid.decode()
                if key in out:
                    out[key].append(w)
        return out


def read_deleted_log(path: Path) -> Iterator[tuple[str, str]]:
    """``(work id, deletion date)`` rows of a deletion log, streamed."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            wid = short_id(row.get("work_id"))
            if wid:
                yield wid, row.get("deleted_date") or ""
