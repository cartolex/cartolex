# SPDX-License-Identifier: MIT
"""People from institutions: who signed works at an institution and its units, over some years.

Instead of a list of names, a project can start from one or several
institutions: given by their OpenAlex id or their ROR id, or searched by name
and confirmed (:func:`find_institutions` shows the candidates, the person
chooses). :func:`propose_people` reads the institutions and every unit below
them (labs, departments: the records whose ``lineage`` holds one of them),
then every work signed there in the window of years, and proposes the authors
with at least *min_works* works there, with their evidence:

* the works in the window, the first and the last year;
* the units they stated (a lab below the institution, several when they
  moved from one to another), each with its works and years;
* their ORCID when the index shows one, and the person of the project they
  already are, if any.

**Affiliations are dated by work**: each work states where each author was
that year, so a person who left keeps the years they were there, and only
those. **Split records** of one person (the same name, first names that agree,
one may be an initial, at the same institution, with no work in common; or a
shared ORCID) are listed as suggested merges, never merged: taking ``A1+A2``
takes them as one person.

Nothing becomes a table row until people are taken (:func:`take_people`), with
a role (``mapped`` by default): they enter with their records confirmed, their
units as organisations **with levels** (the project's levels; the service's
types of institution are mapped to them by a proposed, editable mapping,
:func:`propose_levels`) and **with every parent** the service gives, and their
affiliations dated by their works. Their works come with the harvest.

The proposal is kept in ``sources/<slot>/raw/institution_proposals/`` (no row
is built from it), the people taken in ``raw/institution/``.

**Large institutions.** The works are read page by page, with only the fields
the proposal needs, and folded into per-author aggregates (a few bytes per
signature): memory does not grow with the work records. Every
:data:`CHECKPOINT_PAGES` pages the cursor and the aggregates are saved in
``raw/checkpoints/`` (:mod:`cartolex.project.checkpoints`); a stop or a page
that keeps failing pauses the reading, and a resumed reading gives the
proposal an uninterrupted one gives.
"""

from __future__ import annotations

import base64
import math
import sys
import time
from array import array
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cartolex.project import Project
from cartolex.project.checkpoints import Checkpoint, JobPaused, work_key
from cartolex.project.identity import merge_roots
from cartolex.project.models import Level

from .decisions import read_people, slot_window, update_people
from .names import compatible_first_names, split_full_name, surname_parts
from .openalex import PER_PAGE, OpenAlexSource, Years, parse_institution_ref, short_id
from .people_import import _collection_slot, _ensure_levels
from .tables import (
    RawRun,
    RawWriter,
    SourceBuilder,
    iso,
    parse_time,
    raw_folder,
    read_runs,
    rebuild_sources,
)

__all__ = [
    "CHECKPOINT_PAGES",
    "CONFIRM_WORKS",
    "CURSOR_VALIDITY_S",
    "LARGE_TYPES",
    "MIN_WORKS",
    "InstitutionProposal",
    "MergeSuggestion",
    "ProposedPerson",
    "TakeReport",
    "Unit",
    "checkpoint_folder",
    "checkpoint_options",
    "find_institutions",
    "propose_levels",
    "propose_people",
    "read_institution_runs",
    "read_proposal",
    "resolve_institutions",
    "take_people",
]

#: The least number of works in the window for an author to be proposed.
MIN_WORKS = 2
#: Types of institution that go to the project's largest level by default; the others
#: (``facility``, ``other``…) go to its smallest.
LARGE_TYPES = frozenset(
    {"education", "government", "healthcare", "company", "nonprofit", "archive", "funder"}
)
#: The levels a project without levels gets when people are taken.
DEFAULT_LEVELS = (
    Level(id="unit", names={"en": "Unit", "fr": "Unité", "pt": "Unidade"}),
    Level(id="institution", names={"en": "Institution", "fr": "Institution", "pt": "Instituição"}),
)
PROPOSALS = "institution_proposals"
TAKEN = "institution"
CHECKPOINTS = "checkpoints"
CHECKPOINT_KIND = "institution_works"
#: Pages read between two checkpoints (10,000 works).
CHECKPOINT_PAGES = 100
#: How long a paused reading's cursor is trusted. OpenAlex documents no expiry: a cursor is a
#: place in a sort order, and the index changes from day to day; an older reading starts again.
CURSOR_VALIDITY_S = 7 * 24 * 3600.0
#: Above this many works the app asks before reading them: a day of OpenAlex's free budget
#: without a key (1,000 list requests of 100 works).
CONFIRM_WORKS = 100_000
#: Pages the running time left is measured over: the rate of the last ones, so that the
#: estimate follows the run (a slower service, a throttled stretch) rather than its start.
ETA_WINDOW = 20
#: The clock the rate is measured with (a test replaces it).
_clock = time.monotonic


@dataclass
class Unit:
    """An institution or a unit below it, as the index describes it."""

    id: str
    name: str
    type: str | None
    ror: str | None
    parents: list[str]
    lineage: list[str]
    acronym: str | None = None
    country: str | None = None

    @classmethod
    def of(cls, record: Mapping[str, Any]) -> Unit:
        iid = short_id(record.get("id")) or ""
        parents = [
            short_id(a.get("id"))
            for a in record.get("associated_institutions") or []
            if a.get("relationship") == "parent" and short_id(a.get("id"))
        ]
        lineage = [short_id(x) for x in record.get("lineage") or [] if short_id(x)]
        if not parents:  # a dehydrated record: its ancestors stand for its parents
            parents = [x for x in lineage if x != iid]
        acronyms = record.get("display_name_acronyms") or []
        return cls(
            id=iid,
            name=record.get("display_name") or iid,
            type=record.get("type") or None,
            ror=(record.get("ror") or "").rsplit("/", 1)[-1] or None,
            parents=[p for p in parents if p],
            lineage=[x for x in lineage if x],
            acronym=acronyms[0] if acronyms else None,
            country=record.get("country_code") or None,
        )


@dataclass
class ProposedPerson:
    """An author proposed from an institution, with the evidence to decide."""

    record: str
    name: str
    orcid: str | None
    works: int
    first_year: int | None
    last_year: int | None
    units: list[dict[str, Any]]
    person_id: str | None = None

    def describe(self) -> str:
        years = f"{self.first_year}–{self.last_year}" if self.first_year else "no year"
        units = "; ".join(
            f"{u['name']} ({u['works']}, {u['first_year']}–{u['last_year']})"
            for u in self.units[:3]
        )
        return (
            f"{self.record}  {self.name}  {self.works} work(s), {years}"
            + (f"  ORCID {self.orcid}" if self.orcid else "")
            + (f"  already {self.person_id}" if self.person_id else "")
            + f"\n      {units}"
        )


@dataclass
class MergeSuggestion:
    """Author records that may be one person, and why; taken as one only on request.

    ``clear`` when the records share an ORCID and their names agree: taking every
    proposed person takes them as one (:func:`take_people`'s *join*). ``people`` holds
    what tells the records apart, each as a proposed person (also when it has fewer
    works than the proposal's minimum)."""

    records: list[str]
    reason: str
    works: int
    clear: bool = False
    people: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class InstitutionProposal:
    """What :func:`propose_people` found."""

    roots: list[str]
    window: Years
    min_works: int
    units: dict[str, Unit] = field(default_factory=dict)
    people: list[ProposedPerson] = field(default_factory=list)
    merges: list[MergeSuggestion] = field(default_factory=list)
    levels: dict[str, str] = field(default_factory=dict)
    below: int = 0
    unnamed: int = 0
    works: int = 0
    slot: str = ""
    run_id: str = ""
    source: str = "api"
    #: What happened on the way (a reading started again, a count that changed): codes,
    #: params and words.
    notes: list[dict[str, Any]] = field(default_factory=list)

    def lines(self, show: int | None = None) -> list[str]:
        """The proposal in words."""
        names = ", ".join(self.units[r].name for r in self.roots if r in self.units)
        years = (
            "every year"
            if self.window is None
            else f"{self.window[0] or '…'}–{self.window[1] or '…'}"
        )
        out = [
            f"{names}: {len(self.units)} institution(s) and unit(s), {self.works} work(s) in "
            f"{years}; {len(self.people)} author(s) with {self.min_works} work(s) or more "
            f"({self.below} with fewer, {self.unnamed} signature(s) without an author record)"
        ]
        out.append(
            "levels: "
            + ", ".join(f"{t or 'untyped'} → {lv}" for t, lv in sorted(self.levels.items()))
        )
        for person in self.people[:show]:
            out.append("  " + person.describe())
        for m in self.merges:
            out.append(
                f"  may be one person: {' + '.join(m.records)} ({m.reason}; {m.works} works)"
            )
        return out

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def find_institutions(source: OpenAlexSource, name: str) -> list[dict[str, Any]]:
    """Institutions whose names match *name*, with what tells them apart (for confirmation)."""
    out = []
    for record in source.search_institutions(name):
        unit = Unit.of(record)
        parents = [
            a.get("display_name") or ""
            for a in record.get("associated_institutions") or []
            if a.get("relationship") == "parent"
        ]
        out.append(
            {
                "id": unit.id,
                "name": unit.name,
                "type": unit.type,
                "ror": unit.ror,
                "acronym": unit.acronym,
                "country": unit.country,
                "city": (record.get("geo") or {}).get("city") or None,
                "parents": parents,
                "works_count": int(record.get("works_count") or 0),
            }
        )
    return out


def resolve_institutions(source: OpenAlexSource, refs: Sequence[str]) -> list[Unit]:
    """The institutions *refs* name (OpenAlex ids, ROR ids, or URLs holding one); each must exist."""
    out: list[Unit] = []
    for ref in refs:
        parsed = parse_institution_ref(ref)
        if parsed is None:
            raise ValueError(f"{ref!r} is not an OpenAlex institution id or a ROR id")
        fetched = source.institution(parsed)
        if fetched is None:
            raise ValueError(f"{ref!r}: no such institution in OpenAlex")
        unit = Unit.of(fetched.data)
        if unit.id not in {u.id for u in out}:
            out.append(unit)
    return out


def propose_levels(
    units: Mapping[str, Unit], levels: Sequence[Level], given: Mapping[str, str] | None = None
) -> dict[str, str]:
    """A level for every type of institution among *units*: *given* first, then the largest
    project level for :data:`LARGE_TYPES`, the smallest for the others."""
    ids = [lv.id for lv in levels] or [lv.id for lv in DEFAULT_LEVELS]
    out = {}
    for typ in sorted({u.type or "" for u in units.values()}):
        if given and typ in given:
            out[typ] = given[typ]
        else:
            out[typ] = ids[-1] if typ in LARGE_TYPES else ids[0]
    for typ, level in (given or {}).items():
        out.setdefault(typ, level)
    return out


def _records_of_people(project: Project) -> dict[str, str]:
    """``openalex:A…`` → the person whose confirmed records hold it (a merged row's records
    are those of the person it is merged into)."""
    rows = read_people(project.layout)
    roots = merge_roots(rows)
    out = {}
    for pid, row in sorted(rows.items(), key=lambda kv: (kv[0] in roots, kv[0])):
        for record in (row.get("records") or "").split(";"):
            if record:
                out.setdefault(record, roots.get(pid, pid))
    return out


class _Authors:
    """What the proposal keeps of each author while the works go by: no work record.

    Per author: the record, the name and ORCID shown first, and one entry per
    work signed there (the work's number, and its year with the units stated,
    shared between authors as one small table). Two arrays per author: the
    memory grows with the signatures, a few bytes each, never with the records.
    """

    def __init__(self) -> None:
        self.index: dict[str, int] = {}
        self.ids: list[str] = []
        self.names: list[str] = []
        self.orcids: list[str | None] = []
        self.works: list[array] = []  # work numbers (``W123`` → 123)
        self.keys: list[array] = []  # indices into :attr:`combos`
        self.combos: list[tuple[int | None, tuple[str, ...]]] = []
        self._combo: dict[tuple[int | None, tuple[str, ...]], int] = {}

    def __len__(self) -> int:
        return len(self.ids)

    def add(
        self,
        aid: str,
        name: str,
        orcid: str | None,
        work: int | None,
        year: int | None,
        here: tuple[str, ...],
    ) -> None:
        """One signature: *aid* signed *work* (``None``: the same work again) at *here*."""
        row = self.index.get(aid)
        if row is None:
            row = self.index[aid] = len(self.ids)
            self.ids.append(aid)
            self.names.append(name)
            self.orcids.append(orcid)
            self.works.append(array("q"))
            self.keys.append(array("i"))
        elif orcid and not self.orcids[row]:
            self.orcids[row] = orcid
        if work is None:
            return
        combo = (year, here)
        key = self._combo.get(combo)
        if key is None:
            key = self._combo[combo] = len(self.combos)
            self.combos.append(combo)
        self.works[row].append(work)
        self.keys[row].append(key)

    def entry(self, aid: str) -> dict[str, Any]:
        """The author as the proposal's raw run keeps it (each work once, in reading order)."""
        row = self.index[aid]
        works = []
        seen: set[int] = set()
        for number, key in zip(self.works[row], self.keys[row], strict=True):
            if number in seen:
                continue
            seen.add(number)
            year, here = self.combos[key]
            works.append({"id": f"W{number}", "year": year, "units": list(here)})
        return {
            "type": "author",
            "record": f"openalex:{aid}",
            "name": self.names[row],
            "orcid": self.orcids[row],
            "works": works,
        }

    def add_entry(self, rec: Mapping[str, Any]) -> None:
        """An author read back from a raw run (:meth:`entry`'s shape)."""
        aid = rec["record"].split(":", 1)[1]
        self.add(aid, rec.get("name") or aid, rec.get("orcid"), None, None, ())
        for w in rec.get("works") or []:
            self.add(aid, "", None, _work_number(w["id"]), w.get("year"), tuple(w["units"]))

    # ── what merges need, author by author ──
    def work_set(self, aid: str) -> set[int]:
        return set(self.works[self.index[aid]])

    def unit_set(self, aid: str) -> set[str]:
        return {u for k in set(self.keys[self.index[aid]]) for u in self.combos[k][1]}

    def n_works(self, aid: str) -> int:
        return len(set(self.works[self.index[aid]]))

    # ── checkpoints ──
    def state(self) -> dict[str, Any]:
        return {
            "byteorder": sys.byteorder,
            "ids": self.ids,
            "names": self.names,
            "orcids": self.orcids,
            "works": [_b64(a) for a in self.works],
            "keys": [_b64(a) for a in self.keys],
            "combos": [[y, list(u)] for y, u in self.combos],
        }

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> _Authors:
        if state.get("byteorder") != sys.byteorder:
            raise ValueError("the checkpoint was written on a computer of another byte order")
        out = cls()
        out.ids = list(state["ids"])
        out.names = list(state["names"])
        out.orcids = list(state["orcids"])
        out.index = {aid: i for i, aid in enumerate(out.ids)}
        out.works = [_array("q", t) for t in state["works"]]
        out.keys = [_array("i", t) for t in state["keys"]]
        out.combos = [(y, tuple(u)) for y, u in state["combos"]]
        out._combo = {c: i for i, c in enumerate(out.combos)}
        if not len(out.ids) == len(out.names) == len(out.works) == len(out.keys):
            raise ValueError("the checkpoint's authors do not add up")
        return out


def _b64(a: array) -> str:
    return base64.b64encode(a.tobytes()).decode("ascii")


def _array(typecode: str, text: str) -> array:
    out = array(typecode)
    out.frombytes(base64.b64decode(text))
    return out


def _work_number(wid: str) -> int:
    return int(wid[1:])


@dataclass
class _Reading:
    """Where the reading of an institution's works got to: what a checkpoint keeps."""

    options: dict[str, Any]
    roots: list[str]
    unit_records: dict[str, dict[str, Any]]
    #: The units whose works are read: the roots and every unit below them.
    inside: list[str] = field(default_factory=list)
    authors: _Authors = field(default_factory=_Authors)
    cursor: str | None = None
    read: int = 0
    total: int | None = None
    pages: int = 0
    works: int = 0
    unnamed: int = 0
    first_at: str | None = None
    confirmed: bool = False
    notes: list[dict[str, Any]] = field(default_factory=list)

    def state(self) -> dict[str, Any]:
        return {
            "options": self.options,
            "roots": self.roots,
            "unit_records": self.unit_records,
            "inside": self.inside,
            "authors": self.authors.state(),
            "cursor": self.cursor,
            "read": self.read,
            "total": self.total,
            "pages": self.pages,
            "works": self.works,
            "unnamed": self.unnamed,
            "first_at": self.first_at,
            "confirmed": self.confirmed,
            "notes": self.notes,
        }

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> _Reading:
        return cls(
            options=dict(state["options"]),
            roots=list(state["roots"]),
            unit_records=dict(state["unit_records"]),
            inside=list(state["inside"]),
            authors=_Authors.from_state(state["authors"]),
            cursor=state["cursor"],
            read=int(state["read"]),
            total=state["total"],
            pages=int(state["pages"]),
            works=int(state["works"]),
            unnamed=int(state["unnamed"]),
            first_at=state["first_at"],
            confirmed=bool(state["confirmed"]),
            notes=list(state.get("notes") or []),
        )

    def progress(self) -> dict[str, Any]:
        return {"works": self.read, "total": self.total, "pages": self.pages}


def _read_page(reading: _Reading, works: Sequence[Mapping[str, Any]], inside: set[str]) -> None:
    """Fold one page of works into the per-author aggregates."""
    authors = reading.authors
    for work in works:
        wid = short_id(work.get("id"))
        year = (
            work.get("publication_year") if isinstance(work.get("publication_year"), int) else None
        )
        if not wid or not wid.startswith("W"):
            continue
        reading.works += 1
        number = _work_number(wid)
        signed: set[str] = set()
        for authorship in work.get("authorships") or []:
            stated = [short_id(i.get("id")) for i in authorship.get("institutions") or []]
            here = tuple(sorted({s for s in stated if s in inside}))
            if not here:
                continue
            shown = authorship.get("author") or {}
            aid = short_id(shown.get("id"))
            if not aid:
                reading.unnamed += 1
                continue
            orcid = (shown.get("orcid") or "").rsplit("/", 1)[-1] or None
            name = shown.get("display_name") or authorship.get("raw_author_name") or aid
            authors.add(aid, name, orcid, None if aid in signed else number, year, here)
            signed.add(aid)


def checkpoint_folder(project: Project, slot: str) -> Path:
    """``sources/<slot>/raw/checkpoints/``: where paused collections keep their state."""
    return raw_folder(project.layout, slot) / CHECKPOINTS


def _checkpoint(project: Project, slot: str, options: Mapping[str, Any]) -> Checkpoint:
    return Checkpoint(checkpoint_folder(project, slot), CHECKPOINT_KIND, work_key(options))


def checkpoint_options(
    project: Project, checkpoint_id: str, *, slot: str | None = None
) -> dict[str, Any] | None:
    """The options of the paused proposal *checkpoint_id* (to resume it), or ``None``."""
    slot = _collection_slot(project, slot, "collection")
    try:
        cp = Checkpoint.by_id(checkpoint_folder(project, slot), checkpoint_id)
    except ValueError:
        return None
    if cp.kind != CHECKPOINT_KIND:
        return None
    saved = cp.load()
    if saved is None:
        return None
    return dict(saved.state.get("options") or {})


def _pause(
    cp: Checkpoint,
    reading: _Reading,
    code: str,
    message: str,
    *,
    cause: BaseException | None = None,
    **params: Any,
) -> JobPaused:
    cp.save(reading.state())
    return JobPaused(
        code=code,
        message=message,
        checkpoint=cp.id,
        params={**reading.progress(), **params},
        progress=reading.progress(),
        cause=cause,
    )


def propose_people(
    project: Project,
    source: OpenAlexSource,
    institutions: Sequence[str],
    *,
    years: Years = None,
    min_works: int = MIN_WORKS,
    levels: Mapping[str, str] | None = None,
    slot: str | None = None,
    now: datetime | None = None,
    resume: bool = False,
    confirm_above: int | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    checkpoint_every: int = CHECKPOINT_PAGES,
) -> InstitutionProposal:
    """Propose the authors of *institutions* and their units with *min_works* works or more.

    *years* defaults to the slot's window. The proposal is kept in the slot's
    raw folder (``institution_proposals``); nothing enters the tables until
    people are taken (:func:`take_people`).

    The works are read page by page and folded into per-author aggregates; every
    *checkpoint_every* pages the cursor and the aggregates are saved in
    ``raw/checkpoints/``. When the job is cancelled, or a page still fails after
    its retries, the state is saved and :class:`~cartolex.project.checkpoints.JobPaused`
    is raised; with *resume*, the reading continues from the saved cursor (a
    state older than :data:`CURSOR_VALIDITY_S`, or a cursor the service
    refuses, starts again from the first page, with a note). With
    *confirm_above*, a list announcing more works than that pauses after its
    first page (``collect_size_confirm``), until resumed. *progress* receives,
    after each page, the works read, the total, the pages and the rate.
    """
    from .http import Cancelled, RequestRefused, ServiceError

    if min_works < 1:
        raise ValueError("min_works must be at least 1")
    now = now or datetime.now(timezone.utc)
    slot = _collection_slot(project, slot, "collection")
    if years is None:
        years = slot_window(project.config, slot)
    refs = []
    for ref in institutions:
        parsed = parse_institution_ref(ref)
        if parsed is None:
            raise ValueError(f"{ref!r} is not an OpenAlex institution id or a ROR id")
        refs.append(parsed)
    options = {
        "institutions": sorted(set(refs)),
        "years": list(years) if years else None,
        "source": source.label,
    }
    cp = _checkpoint(project, slot, options)
    reading: _Reading | None = None
    notes: list[dict[str, Any]] = []
    if resume:
        saved = cp.load(now=now)
        if saved is not None and saved.age_s > CURSOR_VALIDITY_S:
            notes.append(_note("checkpoint_expired", days=round(saved.age_s / 86400, 1)))
        elif saved is not None:
            try:
                reading = _Reading.from_state(saved.state)
            except (KeyError, TypeError, ValueError):
                notes.append(_note("checkpoint_unreadable"))
    if reading is None:
        cp.clear()
        reading = _start_reading(source, institutions, {**options, "min_works": min_works})
        reading.notes = notes
    resumed = resume and reading.cursor is not None
    units = {iid: Unit.of(r) for iid, r in sorted(reading.unit_records.items())}
    inside = set(reading.inside)
    started = _clock()
    # (time, works read) at the start and after each recent page: the running rate.
    marks: deque[tuple[float, int]] = deque([(started, reading.read)], maxlen=ETA_WINDOW + 1)
    while True:
        fresh_pages = 0
        try:
            pages = source.institution_work_pages(
                reading.roots, years, cursor=reading.cursor, read=reading.read
            )
            for page in pages:
                fresh_pages += 1
                if reading.first_at is None:
                    reading.first_at = iso(page.retrieved_at)
                _read_page(reading, page.items, inside)
                reading.cursor = page.next_cursor
                reading.read = page.read
                reading.total = page.total
                reading.pages += 1
                marks.append((_clock(), reading.read))
                if progress is not None:
                    rate = _rate(marks)
                    left = max(0, (reading.total or reading.read) - reading.read)
                    progress(
                        {
                            "works": reading.read,
                            "total": reading.total,
                            "pages": reading.pages,
                            "authors": len(reading.authors),
                            "rate": round(rate, 1),
                            "eta_s": round(left / rate, 1) if rate > 0 else None,
                        }
                    )
                if page.next_cursor is None:
                    break
                total = reading.total or 0
                if confirm_above is not None and not reading.confirmed and total > confirm_above:
                    reading.confirmed = True
                    elapsed = max(1e-3, _clock() - started)
                    requests = math.ceil(total / PER_PAGE)
                    raise _pause(
                        cp,
                        reading,
                        "collect_size_confirm",
                        f"{total} works are signed there: reading them takes about {requests} "
                        "requests; confirm to go on, narrow the years or the units, or read "
                        "the OpenAlex snapshot instead (cartolex collect snapshot)",
                        requests=requests,
                        seconds=round(requests * elapsed / fresh_pages),
                    )
                reading.confirmed = True
                if reading.pages % checkpoint_every == 0:
                    cp.save(reading.state())
        except JobPaused:
            raise
        except Cancelled as exc:
            raise _pause(
                cp,
                reading,
                "collect_stopped",
                f"stopped after {reading.read} of {reading.total} works; resume to go on",
                cause=exc,
            ) from exc
        except ServiceError as exc:
            if resumed and fresh_pages == 0 and isinstance(exc, RequestRefused):
                # The service no longer takes the saved cursor: start again, cleanly.
                fresh = _start_reading(source, institutions, reading.options)
                fresh.notes = [*reading.notes, _note("cursor_refused")]
                fresh.confirmed = True
                reading, resumed = fresh, False
                marks.clear()
                marks.append((_clock(), 0))
                cp.clear()
                continue
            if exc.budget_spent:
                raise _pause(
                    cp,
                    reading,
                    "collect_budget_paused",
                    f"the service's daily budget is spent after {reading.read} of "
                    f"{reading.total} works; they are kept: resume once it comes back",
                    cause=exc,
                    resets_at=iso(_budget_back(exc)),
                ) from exc
            raise _pause(
                cp,
                reading,
                "collect_paused",
                f"a page still failed after its retries ({exc}); "
                f"{reading.read} of {reading.total} works are kept: resume to go on",
                cause=exc,
            ) from exc
        break
    if reading.total is not None and reading.read != reading.total:
        reading.notes.append(
            _note("works_count_changed", read=reading.read, announced=reading.total)
        )
    proposal = _finish(project, reading, units, years, min_works, levels, slot, source, now)
    cp.clear()
    return proposal


def _rate(marks: Sequence[tuple[float, int]]) -> float:
    """Works a second over the recent pages in *marks* (time, works read)."""
    (t0, w0), (t1, w1) = marks[0], marks[-1]
    return (w1 - w0) / max(1e-6, t1 - t0)


def _budget_back(exc: BaseException) -> datetime:
    """When a spent daily budget comes back: the time the service gave, else the next
    midnight UTC (when OpenAlex renews its budgets)."""
    resets_at = getattr(exc, "resets_at", None)
    if resets_at is not None:
        return resets_at
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=1)


def _note(code: str, **params: Any) -> dict[str, Any]:
    words = {
        "checkpoint_expired": "the paused reading was too old to go on from: it started again",
        "checkpoint_unreadable": "the paused reading could not be read: it started again",
        "cursor_refused": "the service no longer took the paused reading's place: it started "
        "again from the first page",
        "works_count_changed": "the index changed during the reading: {read} works read of "
        "the {announced} announced",
    }
    return {"code": code, "params": params, "message": words[code].format(**params)}


def _start_reading(
    source: OpenAlexSource, institutions: Sequence[str], options: Mapping[str, Any]
) -> _Reading:
    """The institutions, their units and the other parents of joint units: before any work."""
    roots = resolve_institutions(source, institutions)
    root_ids = [u.id for u in roots]
    unit_records: dict[str, dict[str, Any]] = {}
    for record in source.institution_units(root_ids).data:
        iid = short_id(record.get("id"))
        if iid:
            unit_records[iid] = record
    for root in roots:
        if root.id not in unit_records:
            fetched = source.institution(root.id)
            if fetched is not None:
                unit_records[root.id] = fetched.data
    inside = set(unit_records)
    # A unit may belong to other institutions too (a joint unit): their records are kept,
    # so that it enters with every parent.
    parents = {p for r in unit_records.values() for p in Unit.of(r).parents} - inside
    for iid in sorted(parents):
        fetched = source.institution(iid)
        if fetched is not None:
            unit_records[iid] = fetched.data
    return _Reading(
        options=dict(options), roots=root_ids, unit_records=unit_records, inside=sorted(inside)
    )


def _finish(
    project: Project,
    reading: _Reading,
    units_all: Mapping[str, Unit],
    years: Years,
    min_works: int,
    levels: Mapping[str, str] | None,
    slot: str,
    source: OpenAlexSource,
    now: datetime,
) -> InstitutionProposal:
    units = {iid: u for iid, u in units_all.items() if iid in set(reading.inside)}
    proposal = InstitutionProposal(
        roots=reading.roots,
        window=years,
        min_works=min_works,
        units=units,
        slot=slot,
        source=source.label,
        works=reading.works,
        unnamed=reading.unnamed,
        notes=list(reading.notes),
    )
    proposal.levels = propose_levels(units_all, project.config.levels, levels)
    header = {
        "roots": reading.roots,
        "years": list(years) if years else None,
        "min_works": min_works,
        "levels": proposal.levels,
        "source": source.label,
        "works": reading.works,
    }
    if reading.notes:
        header["notes"] = reading.notes
    at = reading.first_at or iso(now)
    known = _records_of_people(project)
    authors = reading.authors
    with RawWriter(project.layout, slot, PROPOSALS, header, now=now) as out:
        for iid in sorted(reading.unit_records):
            out.add({"type": "unit", "retrieved_at": at, "record": reading.unit_records[iid]})
        for aid in sorted(authors.index):
            entry = authors.entry(aid)
            out.add({**entry, "retrieved_at": at})
            _propose(proposal, entry, units, known, min_works)
    proposal.run_id = out.run_id
    proposal.people.sort(key=lambda p: (-p.works, p.name, p.record))
    proposal.merges = _merges(_TableView(authors, units), min_works)
    return proposal


def _propose(
    proposal: InstitutionProposal,
    entry: Mapping[str, Any],
    units: Mapping[str, Unit],
    known: Mapping[str, str],
    min_works: int,
) -> None:
    person = _person_of(entry, units)
    person.person_id = known.get(entry["record"])
    if person.works >= min_works:
        proposal.people.append(person)
    else:
        proposal.below += 1


def _person_of(entry: Mapping[str, Any], units: Mapping[str, Unit]) -> ProposedPerson:
    years = [w["year"] for w in entry["works"] if w["year"] is not None]
    per_unit: dict[str, list[int | None]] = defaultdict(list)
    for w in entry["works"]:
        for u in w["units"]:
            per_unit[u].append(w["year"])
    unit_rows = []
    for uid, ys in per_unit.items():
        known = [y for y in ys if y is not None]
        unit_rows.append(
            {
                "id": uid,
                "name": units[uid].name if uid in units else uid,
                "works": len(ys),
                "first_year": min(known) if known else None,
                "last_year": max(known) if known else None,
            }
        )
    unit_rows.sort(key=lambda u: (-u["works"], u["name"]))
    return ProposedPerson(
        record=entry["record"],
        name=entry["name"],
        orcid=entry["orcid"],
        works=len(entry["works"]),
        first_year=min(years) if years else None,
        last_year=max(years) if years else None,
        units=unit_rows,
    )


def _split_name(name: str) -> tuple[str, str]:
    last, first = split_full_name(name)
    return last, first


class _TableView:
    """What :func:`_merges` asks of the authors, from the compact table."""

    def __init__(self, authors: _Authors, units: Mapping[str, Unit] | None = None) -> None:
        self.a = authors
        self.units_of = units or {}

    def ids(self) -> list[str]:
        return sorted(self.a.index)

    def name(self, aid: str) -> str:
        return self.a.names[self.a.index[aid]]

    def orcid(self, aid: str) -> str | None:
        return self.a.orcids[self.a.index[aid]]

    def works(self, aid: str) -> set[int]:
        return self.a.work_set(aid)

    def units(self, aid: str) -> set[str]:
        return self.a.unit_set(aid)

    def person(self, aid: str) -> ProposedPerson:
        """The author as a proposed person (whatever their number of works)."""
        return _person_of(self.a.entry(aid), self.units_of)


def _merges(view: _TableView, min_works: int) -> list[MergeSuggestion]:
    """Author records that may be one person: a shared ORCID; or the same surname, first names
    that agree (one may be an initial), a unit in common and no work in common (and not two
    different ORCIDs). One of them must reach *min_works*, or the two together must.

    The works and units of an author are looked at only within a group that may hold a
    pair, so memory stays that of the largest group."""
    names = {aid: _split_name(view.name(aid)) for aid in view.ids()}
    pairs: dict[tuple[str, str], str] = {}
    by_orcid: dict[str, list[str]] = defaultdict(list)
    by_surname: dict[str, list[str]] = defaultdict(list)
    for aid in sorted(names):
        orcid = view.orcid(aid)
        if orcid:
            by_orcid[orcid].append(aid)
        key = " ".join(surname_parts(names[aid][0]))
        if key:
            by_surname[key].append(aid)
    for group in by_orcid.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                pairs[(a, b)] = "the same ORCID"
    for group in by_surname.values():
        if len(group) < 2:
            continue
        works = {aid: view.works(aid) for aid in group}
        stated = {aid: view.units(aid) for aid in group}
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                oa, ob = view.orcid(a), view.orcid(b)
                if (
                    (a, b) not in pairs
                    and not (oa and ob and oa != ob)
                    and compatible_first_names(names[a][1], names[b][1])
                    and stated[a] & stated[b]
                    and not works[a] & works[b]
                ):
                    pairs[(a, b)] = "the same name at the same unit, no work in common"
    out = []
    for (a, b), reason in sorted(pairs.items()):
        wa, wb = view.works(a), view.works(b)
        total = len(wa | wb)
        if max(len(wa), len(wb)) < min_works and total < min_works:
            continue
        clear = reason == "the same ORCID" and compatible_first_names(names[a][1], names[b][1])
        clear = clear and set(surname_parts(names[a][0])) & set(surname_parts(names[b][0])) != set()
        out.append(
            MergeSuggestion(
                [f"openalex:{a}", f"openalex:{b}"],
                reason,
                total,
                clear=clear,
                people=[asdict(view.person(x)) for x in (a, b)],
            )
        )
    return out


# ── taking people ────────────────────────────────────────────────────────────


@dataclass
class TakeReport:
    """What :func:`take_people` did."""

    taken: dict[str, list[str]] = field(default_factory=dict)  # person id → records
    known: dict[str, str] = field(default_factory=dict)  # record → person already there
    refused: list[tuple[str, str]] = field(default_factory=list)
    run_id: str = ""
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"{len(self.taken)} person(s) taken"]
        out += [f"  {pid}: {', '.join(recs)}" for pid, recs in sorted(self.taken.items())]
        out += [f"  {rec} is already {pid}" for rec, pid in sorted(self.known.items())]
        out += [f"  refused {what}: {why}" for what, why in self.refused]
        return out + self.notes


def _latest_proposal(project: Project, slot: str, run_id: str | None) -> RawRun:
    runs = read_runs(project.layout, slot, PROPOSALS)
    if run_id:
        runs = [r for r in runs if r.run_id == run_id]
    if not runs:
        raise FileNotFoundError(
            "no institution proposal to take people from: run propose_people first"
            + (f" (no run {run_id})" if run_id else "")
        )
    return runs[-1]


def read_proposal(
    project: Project, *, run_id: str | None = None, slot: str | None = None
) -> InstitutionProposal:
    """The latest institution proposal (or *run_id*) read again from its raw run: the people
    with ``min_works`` works or more (and the person each already is), the suggested merges,
    the units and the levels. Raises :class:`FileNotFoundError` when there is none. Reading
    never changes the project: without a collection slot there is no proposal."""
    if slot is None:
        slot = next((s.id for s in project.config.slots if s.kind == "collection"), None)
        if slot is None:
            raise FileNotFoundError("no collection slot: no institution proposal")
    run = _latest_proposal(project, slot, run_id)
    units: dict[str, Unit] = {}
    authors = _Authors()
    for rec in run.records():
        if rec.get("type") == "unit":
            unit = Unit.of(rec["record"])
            units[unit.id] = unit
        elif rec.get("type") == "author":
            authors.add_entry(rec)
    header = run.header
    min_works = int(header.get("min_works") or MIN_WORKS)
    years = header.get("years")
    proposal = InstitutionProposal(
        roots=list(header.get("roots") or []),
        window=(years[0], years[1]) if years else None,
        min_works=min_works,
        units=units,
        levels=dict(header.get("levels") or {}),
        works=int(header.get("works") or 0),
        slot=slot,
        run_id=run.run_id,
        source=str(header.get("source") or "api"),
        notes=list(header.get("notes") or []),
    )
    known = _records_of_people(project)
    for aid in sorted(authors.index):
        _propose(proposal, authors.entry(aid), units, known, min_works)
    proposal.people.sort(key=lambda p: (-p.works, p.name, p.record))
    proposal.merges = _merges(_TableView(authors, units), min_works)
    return proposal


def take_people(
    project: Project,
    take: Sequence[str] | str = "all",
    *,
    role: str = "mapped",
    run_id: str | None = None,
    levels: Mapping[str, str] | None = None,
    join: Sequence[Sequence[str]] | None = None,
    slot: str | None = None,
    now: datetime | None = None,
) -> TakeReport:
    """Take people from an institution proposal (the latest, or *run_id*), with *role*.

    *take* is ``"all"`` (every proposed author not yet in the project) or a list
    of records: ``A1`` (or ``openalex:A1``) takes one record, ``A1+A2`` takes two
    records as one person. With ``"all"``, each group of *join* (records that are one
    person: the clear suggested merges, and those someone confirmed) is taken as one
    person, even when each of its records has fewer works than the minimum (``None``:
    the clear suggested merges); the other authors are taken one by one. They enter with ``identity = confirmed`` and their
    records; *levels* changes the proposal's mapping of types to levels.
    """
    if role not in ("mapped", "context", "projected", "excluded"):
        raise ValueError(f"{role!r} is not a role (mapped, context, projected, excluded)")
    now = now or datetime.now(timezone.utc)
    slot = _collection_slot(project, slot, "collection")
    run = _latest_proposal(project, slot, run_id)
    unit_records: dict[str, dict[str, Any]] = {}
    authors: dict[str, dict[str, Any]] = {}
    for rec in run.records():
        if rec.get("type") == "unit":
            iid = short_id(rec["record"].get("id"))
            if iid:
                unit_records[iid] = rec
        elif rec.get("type") == "author":
            authors[rec["record"].split(":", 1)[1]] = rec
    mapping = {**(run.header.get("levels") or {}), **(levels or {})}
    min_works = int(run.header.get("min_works") or MIN_WORKS)
    report = TakeReport(run_id=run.run_id)
    known = _records_of_people(project)
    groups: list[list[str]] = []
    if take == "all" or take == ["all"]:
        if join is None:
            found = read_proposal(project, run_id=run.run_id, slot=slot)
            join = [[r.split(":", 1)[1] for r in m.records] for m in found.merges if m.clear]
        joined: set[str] = set()
        for group in join:
            ids = [(short_id(str(x).strip()) or "").upper() for x in group]
            ids = [a for a in dict.fromkeys(ids) if a in authors and a not in joined]
            if len(ids) > 1:
                groups.append(ids)
                joined.update(ids)
        groups += [
            [aid]
            for aid in sorted(authors)
            if aid not in joined and len(authors[aid]["works"]) >= min_works
        ]
    else:
        for item in [take] if isinstance(take, str) else take:
            group = []
            for part in str(item).split("+"):
                aid = (short_id(part.strip()) or "").upper()
                if aid not in authors:
                    report.refused.append((part.strip(), "not among the proposal's authors"))
                    group = []
                    break
                group.append(aid)
            if group:
                groups.append(group)
    people_records: list[dict[str, Any]] = []
    for group in groups:
        records = [f"openalex:{a}" for a in group]
        already = [r for r in records if r in known]
        if already:
            for r in already:
                report.known[r] = known[r]
            continue
        people_records.append(_taken_person(group, authors))
    if not people_records:
        report.notes.append("nobody new to take")
        return report
    new_levels = sorted(set(mapping.values()) - {lv.id for lv in project.config.levels})
    if new_levels:
        defaults = [lv for lv in DEFAULT_LEVELS if lv.id in new_levels]
        if not project.config.levels and len(defaults) == len(new_levels):
            config = project.config
            project.save_config(config.model_copy(update={"levels": defaults}), action="add levels")
        else:
            _ensure_levels(project, new_levels)
        report.notes.append("levels added to the project: " + ", ".join(new_levels))
    needed = _units_needed(people_records, unit_records)
    header = {"proposal": run.run_id, "levels": mapping, "roots": run.header.get("roots")}
    with RawWriter(project.layout, slot, TAKEN, header, now=now) as out:
        for iid in sorted(needed):
            out.add(unit_records[iid])
        for rec in people_records:
            out.add({**rec, "retrieved_at": iso(now)})
    report.run_id = out.run_id
    rebuild_sources(project.layout, project.config)
    from .people_import import _registry_ids

    pid_of = _registry_ids(project, slot, [r["key"] for r in people_records])
    changes = {}
    for rec in people_records:
        pid = pid_of[rec["key"]]
        report.taken[pid] = rec["records"]
        changes[pid] = {
            "role": role,
            "set": "institution" if role == "projected" else "",
            "identity": "confirmed",
            "records": ";".join(rec["records"]),
            "note": "taken from an institution",
        }
    update_people(project.layout, changes, action="take people from institutions", now=now)
    return report


def _taken_person(group: Sequence[str], authors: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The raw record of one person taken: records, names, ORCID, units with their years."""
    main = max(group, key=lambda a: (len(authors[a]["works"]), a == group[0]))
    last, first = _split_name(authors[main]["name"])
    spans: dict[str, list[int | None]] = {}
    seen: set[str] = set()
    for aid in group:
        for w in authors[aid]["works"]:
            if w["id"] in seen:
                continue
            seen.add(w["id"])
            for u in w["units"]:
                span = spans.setdefault(u, [None, None])
                if w["year"] is not None:
                    span[0] = w["year"] if span[0] is None else min(span[0], w["year"])
                    span[1] = w["year"] if span[1] is None else max(span[1], w["year"])
    orcid = next((authors[a]["orcid"] for a in group if authors[a]["orcid"]), None)
    aliases = []
    for aid in group:
        if aid == main:
            continue
        la, fa = _split_name(authors[aid]["name"])
        if (la, fa) != (last, first):
            aliases.append({"last_name": la, "first_name": fa or None})
    return {
        "type": "person",
        "key": f"openalex:{group[0]}",
        "records": [f"openalex:{a}" for a in group],
        "last_name": last,
        "first_name": first,
        "orcid": orcid,
        "aliases": aliases,
        "works": len(seen),
        "affiliations": [
            {"unit": u, "first": span[0], "last": span[1]} for u, span in sorted(spans.items())
        ],
    }


def _units_needed(
    people: Sequence[Mapping[str, Any]], unit_records: Mapping[str, Mapping[str, Any]]
) -> set[str]:
    """The units people stated, and every institution above them the proposal holds."""
    todo = [a["unit"] for p in people for a in p["affiliations"]]
    out: set[str] = set()
    while todo:
        iid = todo.pop()
        if iid in out or iid not in unit_records:
            continue
        out.add(iid)
        todo += Unit.of(unit_records[iid]["record"]).parents
    return out


def read_institution_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """The reader of people taken from institutions: their units as organisations with levels
    and parents, the people with their records, affiliations dated by their works."""
    for run in runs:
        levels = run.header.get("levels") or {}
        records = list(run.records())
        for rec in records:
            if rec.get("type") != "unit":
                continue
            unit = Unit.of(rec["record"])
            ids = {"openalex": unit.id}
            if unit.ror:
                ids["ror"] = unit.ror
            builder.organisation(
                slot=run.slot,
                keys=[f"openalex:{unit.id}"],
                name=unit.name,
                acronym=unit.acronym,
                level=levels.get(unit.type or ""),
                parent_keys=[f"openalex:{p}" for p in unit.parents],
                ids=ids,
                country=unit.country,
                source="openalex",
                retrieved_at=parse_time(rec["retrieved_at"]),
            )
        for rec in records:
            if rec.get("type") != "person":
                continue
            at = parse_time(rec["retrieved_at"])
            oa = [r.split(":", 1)[1] for r in rec["records"]]
            pid = builder.person(
                slot=run.slot,
                keys=list(dict.fromkeys([rec["key"], *(f"openalex:{a}" for a in oa)])),
                last_name=rec["last_name"],
                first_name=rec.get("first_name") or None,
                orcid=rec.get("orcid"),
                ids={"openalex": oa},
                source="institution",
                aliases=[{**a, "source": "institution"} for a in rec.get("aliases") or []],
                retrieved_at=at,
            )
            for aff in rec.get("affiliations") or []:
                oid = builder.registry.lookup(
                    "organisations", [f"openalex:{aff['unit']}"], run.slot
                )
                if oid is not None:
                    builder.affiliation(pid, oid, aff.get("first"), aff.get("last"), "stated")
