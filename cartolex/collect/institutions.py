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
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from cartolex.project import Project
from cartolex.project.models import Level

from .decisions import read_people, slot_window, update_people
from .names import compatible_first_names, split_full_name, surname_parts
from .openalex import OpenAlexSource, Years, parse_institution_ref, short_id
from .people_import import _collection_slot, _ensure_levels
from .tables import RawRun, RawWriter, SourceBuilder, iso, parse_time, read_runs, rebuild_sources

__all__ = [
    "LARGE_TYPES",
    "MIN_WORKS",
    "InstitutionProposal",
    "MergeSuggestion",
    "ProposedPerson",
    "TakeReport",
    "Unit",
    "find_institutions",
    "propose_levels",
    "propose_people",
    "read_institution_runs",
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
    """Author records that may be one person, and why; taken as one only on request."""

    records: list[str]
    reason: str
    works: int


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
                "country": unit.country,
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
    """``openalex:A…`` → the person whose confirmed records hold it."""
    out = {}
    for pid, row in read_people(project.layout).items():
        if row.get("merged_into"):
            continue
        for record in (row.get("records") or "").split(";"):
            if record:
                out.setdefault(record, pid)
    return out


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
) -> InstitutionProposal:
    """Propose the authors of *institutions* and their units with *min_works* works or more.

    *years* defaults to the slot's window. The proposal is kept in the slot's
    raw folder (``institution_proposals``); nothing enters the tables until
    people are taken (:func:`take_people`).
    """
    if min_works < 1:
        raise ValueError("min_works must be at least 1")
    now = now or datetime.now(timezone.utc)
    slot = _collection_slot(project, slot, "collection")
    if years is None:
        years = slot_window(project.config, slot)
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
    units = {iid: Unit.of(r) for iid, r in sorted(unit_records.items())}
    # A unit may belong to other institutions too (a joint unit): their records are kept,
    # so that it enters with every parent.
    for iid in sorted({p for u in units.values() for p in u.parents} - set(units)):
        fetched = source.institution(iid)
        if fetched is not None:
            unit_records[iid] = fetched.data
    fetched_works = source.works_by_institutions(root_ids, years)
    proposal = InstitutionProposal(
        roots=root_ids,
        window=years,
        min_works=min_works,
        units=units,
        slot=slot,
        source=source.label,
    )
    proposal.levels = propose_levels(
        {iid: Unit.of(r) for iid, r in unit_records.items()}, project.config.levels, levels
    )
    authors: dict[str, dict[str, Any]] = {}
    inside = set(units)
    for work in fetched_works.data:
        wid = short_id(work.get("id"))
        year = (
            work.get("publication_year") if isinstance(work.get("publication_year"), int) else None
        )
        if not wid:
            continue
        proposal.works += 1
        for authorship in work.get("authorships") or []:
            stated = [short_id(i.get("id")) for i in authorship.get("institutions") or []]
            here = sorted({s for s in stated if s in inside})
            if not here:
                continue
            shown = authorship.get("author") or {}
            aid = short_id(shown.get("id"))
            if not aid:
                proposal.unnamed += 1
                continue
            entry = authors.setdefault(
                aid,
                {
                    "type": "author",
                    "record": f"openalex:{aid}",
                    "name": shown.get("display_name") or authorship.get("raw_author_name") or aid,
                    "orcid": None,
                    "works": [],
                },
            )
            orcid = (shown.get("orcid") or "").rsplit("/", 1)[-1] or None
            entry["orcid"] = entry["orcid"] or orcid
            if wid not in {w["id"] for w in entry["works"]}:
                entry["works"].append({"id": wid, "year": year, "units": here})
    known = _records_of_people(project)
    for aid in sorted(authors):
        entry = authors[aid]
        person = _person_of(entry, units)
        person.person_id = known.get(entry["record"])
        if person.works >= min_works:
            proposal.people.append(person)
        else:
            proposal.below += 1
    proposal.people.sort(key=lambda p: (-p.works, p.name, p.record))
    proposal.merges = _merges(authors, units, min_works)
    header = {
        "roots": root_ids,
        "years": list(years) if years else None,
        "min_works": min_works,
        "levels": proposal.levels,
        "source": source.label,
        "works": proposal.works,
    }
    at = iso(fetched_works.retrieved_at)
    with RawWriter(project.layout, slot, PROPOSALS, header, now=now) as out:
        for iid in sorted(unit_records):
            out.add({"type": "unit", "retrieved_at": at, "record": unit_records[iid]})
        for aid in sorted(authors):
            out.add({**authors[aid], "retrieved_at": at})
    proposal.run_id = out.run_id
    return proposal


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


def _merges(
    authors: Mapping[str, Mapping[str, Any]], units: Mapping[str, Unit], min_works: int
) -> list[MergeSuggestion]:
    """Author records that may be one person: a shared ORCID; or the same surname, first names
    that agree (one may be an initial), a unit in common and no work in common (and not two
    different ORCIDs). One of them must reach *min_works*, or the two together must."""
    works = {aid: {w["id"] for w in a["works"]} for aid, a in authors.items()}
    stated = {aid: {u for w in a["works"] for u in w["units"]} for aid, a in authors.items()}
    names = {aid: _split_name(a["name"]) for aid, a in authors.items()}
    pairs: dict[tuple[str, str], str] = {}
    by_orcid: dict[str, list[str]] = defaultdict(list)
    by_surname: dict[str, list[str]] = defaultdict(list)
    for aid in sorted(authors):
        if authors[aid]["orcid"]:
            by_orcid[authors[aid]["orcid"]].append(aid)
        key = " ".join(surname_parts(names[aid][0]))
        if key:
            by_surname[key].append(aid)
    for group in by_orcid.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                pairs[(a, b)] = "the same ORCID"
    for group in by_surname.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                oa, ob = authors[a]["orcid"], authors[b]["orcid"]
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
        total = len(works[a] | works[b])
        if max(len(works[a]), len(works[b])) < min_works and total < min_works:
            continue
        out.append(MergeSuggestion([f"openalex:{a}", f"openalex:{b}"], reason, total))
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


def take_people(
    project: Project,
    take: Sequence[str] | str = "all",
    *,
    role: str = "mapped",
    run_id: str | None = None,
    levels: Mapping[str, str] | None = None,
    slot: str | None = None,
    now: datetime | None = None,
) -> TakeReport:
    """Take people from an institution proposal (the latest, or *run_id*), with *role*.

    *take* is ``"all"`` (every proposed author not yet in the project) or a list
    of records: ``A1`` (or ``openalex:A1``) takes one record, ``A1+A2`` takes two
    records as one person. They enter with ``identity = confirmed`` and their
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
        groups = [[aid] for aid in sorted(authors) if len(authors[aid]["works"]) >= min_works]
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
