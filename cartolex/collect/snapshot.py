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

from .http import Cancelled, Fetched
from .openalex import Years, bare_doi, short_id

__all__ = [
    "ENTITIES",
    "Partition",
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
    asked every few thousand lines whether to stop (:class:`Cancelled`).
    """

    def __init__(
        self,
        root: Path | str,
        *,
        progress: Callable[[float, str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.root = Path(root)
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
    def scan(
        self,
        entity: str,
        maybe: Callable[[bytes], bool],
        keep: Callable[[dict[str, Any]], bool],
        *,
        what: str = "",
    ) -> dict[str, dict[str, Any]]:
        """Every record of *entity* that *keep* accepts, by id, the newest partition's copy.

        *maybe* sees each raw line first and must be true for every line *keep*
        could accept (a quick test on bytes, so most lines are never parsed).
        Deleted works are left out.
        """
        parts = self.partitions(entity)
        total = sum(p.size for p in parts) or 1
        done = 0
        started = time.perf_counter()
        found: dict[str, dict[str, Any]] = {}
        report = self.report
        report.passes += 1
        for part in parts:
            with open(part.path, "rb") as raw, gzip.GzipFile(fileobj=raw) as gz:
                reader = io.BufferedReader(gz, buffer_size=1 << 20)
                for n, line in enumerate(reader):
                    report.lines += 1
                    if n % 20000 == 0:
                        self._check_cancel()
                        self._report(
                            (done + raw.tell()) / total, f"snapshot: {entity} {what}".strip()
                        )
                    if not maybe(line):
                        continue
                    report.parsed += 1
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict) and keep(record):
                        rid = short_id(record.get("id"))
                        if rid:
                            found[rid] = record
            done += part.size
            report.bytes += part.size
            report.by_entity[entity] = report.by_entity.get(entity, 0) + part.size
        if entity == "works" and found:
            gone = self.deleted(set(found))
            report.deleted += len(gone)
            for wid in gone:
                del found[wid]
        report.kept += len(found)
        report.seconds += time.perf_counter() - started
        return found

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
        wanted = {i.encode() for i in ids if i}
        if not wanted:
            return {}
        return self.scan(
            "authors",
            lambda line: bool(wanted & set(_AUTHOR.findall(line))),
            lambda r: (short_id(r.get("id")) or "").encode() in wanted,
            what="by id",
        )

    def institutions(
        self,
        *,
        ids: Iterable[str] = (),
        rors: Iterable[str] = (),
        lineage: Iterable[str] = (),
        names: Iterable[str] = (),
    ) -> dict[str, dict[str, Any]]:
        """Institution records: by id, by ROR id, every unit whose lineage holds one of
        *lineage*, or whose names hold every word of one of *names*."""
        want_ids = {i.encode() for i in ids if i}
        want_rors = {r.lower().encode() for r in rors if r}
        want_lineage = {i.encode() for i in lineage if i}
        word_sets = [set(_words(n)) for n in names if _words(n)]

        def maybe(line: bytes) -> bool:
            if word_sets:
                return True
            if want_rors and want_rors & set(_ROR.findall(line.lower())):
                return True
            return bool((want_ids | want_lineage) & set(_INSTITUTION.findall(line)))

        def keep(r: dict[str, Any]) -> bool:
            rid = (short_id(r.get("id")) or "").encode()
            if rid in want_ids:
                return True
            ror = (r.get("ror") or "").rsplit("/", 1)[-1].lower().encode()
            if ror and ror in want_rors:
                return True
            if want_lineage & {(short_id(x) or "").encode() for x in r.get("lineage") or []}:
                return True
            if word_sets:
                shown = [r.get("display_name") or ""]
                shown += list(r.get("display_name_acronyms") or [])
                shown += list(r.get("display_name_alternatives") or [])
                return any(ws <= set(_words(s)) for ws in word_sets for s in shown)
            return False

        return self.scan("institutions", maybe, keep, what="institutions")

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
        want_authors = {a.encode() for a in author_ids if a}
        want_lineage = {i.encode() for i in lineage if i}
        want_dois = {d for d in (bare_doi(x) for x in dois) if d}
        want_doi_bytes = {d.encode() for d in want_dois}
        first, last = years if years is not None else (None, None)

        def maybe(line: bytes) -> bool:
            if want_authors and want_authors & set(_AUTHOR.findall(line)):
                return True
            if want_lineage and want_lineage & set(_INSTITUTION.findall(line)):
                return True
            if want_doi_bytes:
                return any(d.lower() in want_doi_bytes for d in _DOI.findall(line))
            return False

        def keep(r: dict[str, Any]) -> bool:
            return in_window(r, first, last) and (
                bool(want_authors and _authors_of(r) & want_authors)
                or bool(want_lineage and _lineages_of(r) & want_lineage)
                or bool(want_dois and bare_doi(r.get("doi")) in want_dois)
            )

        return self.scan("works", maybe, keep, what="works")


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
            self._institutions = self.snapshot.scan(
                "institutions", lambda line: True, lambda r: True, what="institutions"
            )
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
