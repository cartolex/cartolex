# SPDX-License-Identifier: MIT
"""The bibliographic layer: what the demo services know about a demo world.

A real bibliographic index is not the truth about people. It splits one person
over two author records, merges two people into one, has homonyms, misses some
people altogether, dates affiliations from what works state, and lists
co-authors from outside any cohort. This module derives such an index from a
:class:`~cartolex.demo.DemoWorld`, deterministically from ``(size, seed,
layer seed)``, with random streams of its own: the world itself never changes.

What it holds:

* **institutions** — one record per institution of the world, one lab-level
  record per group (whose parent is its institution; most groups' works never
  cite it), and a few outside institutions; institution-level records carry a
  ROR id whose check number is wrong (no real one can match), and one lab is a
  **joint unit** with two parent institutions;
* **author records** — one per person with works, except the people it misses;
  plus records for homonyms and for co-authors outside the cohort;
* **index works** — the world's works the index covers (those whose sources
  include the index), with their authorships as the index states them, plus
  works written by outside people: homonyms, the outside co-authors' own works
  on other themes, and one **large collaboration** (30 authors, two of them
  cohort people);
* **the registry** — for people with an ORCID, the works and employments they
  declared;
* **truth** — for each world person, the name an imported list shows and the
  records a careful person would confirm (:class:`PersonTruth`).

The special cases, each given to a different cohort person (:attr:`Bibliography.specials`):

``homonym``   another record with exactly the same name, in another field and institution;
``trap``      a homonym whose record cites the person's own lab-level institution record,
              while the person's works cite the parent institution;
``split``     the person's works split over two records, one under an initial,
              with one work indexed twice (the copy without its DOI);
``mixed``     one record holding the person's works and those of an outside
              homonym, with the person's ORCID; only the registry separates them;
``compound``  the list gives a compound surname, the index only its first half;
``diacritics`` the list writes the name with accents, the index without;
``moved``     works before a given year state an earlier, outside institution;
``none``      no record and no registry entry at all.
"""

from __future__ import annotations

import random
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, replace

from .. import names as nm
from ..model import COHORT, DemoWorld, Person, Work
from ..texts import MIN_TOPICS, TextPlan, compose
from ..vocabulary import DRIVERS, METHODS, SETTINGS, THEME_BY_ID, THEMES, Theme

__all__ = [
    "BIBLIO_VERSION",
    "Authorship",
    "AuthorRecord",
    "Bibliography",
    "Employment",
    "IndexWork",
    "Institution",
    "PersonTruth",
    "RegistryRecord",
    "RegistryWork",
    "SPECIALS",
    "build_bibliography",
    "openalex_type",
]

BIBLIO_VERSION = "1"
SPECIALS = ("mixed", "split", "trap", "homonym", "compound", "diacritics", "moved", "none")
#: Share of cohort groups whose works cite their lab-level record rather than the institution.
CITED_LAB_SHARE = 0.3
#: Probability that the index shows a person's ORCID on their record.
P_RECORD_ORCID = 0.75
#: Probability that a registry holder declared a given work with a DOI; share with none.
P_DECLARED = 0.85
P_EMPTY_REGISTRY = 0.2
#: Share of works with co-authors from outside the cohort.
P_OUTSIDE_COAUTHORS = 0.15
#: Authors of the large collaboration (more than a co-author graph keeps by default).
CONSORTIUM_AUTHORS = 30
_OPENALEX_TYPES = {
    "article": ("article", "journal"),
    "proceedings": ("article", "conference"),
    "preprint": ("preprint", "repository"),
    "report": ("report", "repository"),
    "thesis": ("dissertation", "repository"),
}
_ORCID_TYPES = {
    "article": "journal-article",
    "proceedings": "conference-paper",
    "preprint": "preprint",
    "report": "report",
    "thesis": "dissertation-thesis",
}
_OUTSIDE_PATTERNS = (
    "Institute of Ocean Studies of {site}",
    "{site} School of Earth Sciences",
    "{site} Research Centre for Environment",
    "University of {site}",
)
_OUTSIDE_SITES = ("Norhaven", "Castelbrun", "Vesterholm", "Arrowmere", "Solcanto", "Brindleford")


def openalex_type(doc_type: str) -> tuple[str, str]:
    """The index's work type and source type for a world document type."""
    return _OPENALEX_TYPES[doc_type]


@dataclass(frozen=True)
class Institution:
    """An institution record of the index."""

    id: str
    name: str
    acronym: str | None
    type: str
    parent: str | None = None
    site: str | None = None
    lat: float | None = None
    lon: float | None = None
    #: Other parents, for a unit that belongs to several institutions.
    also: tuple[str, ...] = ()
    ror: str | None = None

    @property
    def parents(self) -> tuple[str, ...]:
        """Every parent: the main one first."""
        return ((self.parent,) if self.parent else ()) + self.also

    @property
    def lineage(self) -> tuple[str, ...]:
        return (self.id, *self.parents)


@dataclass(frozen=True)
class Authorship:
    """One author of an index work, as the index states it."""

    author_id: str | None  # None: no author record for this name
    name: str  # the name as printed
    orcid: str | None
    institutions: tuple[str, ...]
    corresponding: bool
    person_id: str | None  # the world person behind it; None for outside people


@dataclass(frozen=True)
class IndexWork:
    """One work of the index."""

    id: str
    doi: str | None
    title: str
    abstract: str
    year: int
    date: str
    type: str
    source_type: str
    venue: str
    language: str
    themes: tuple[str, ...]
    authorships: tuple[Authorship, ...]
    world_work: str | None = None


@dataclass(frozen=True)
class AuthorRecord:
    """An author record of the index."""

    id: str
    display_name: str
    alternatives: tuple[str, ...]
    orcid: str | None
    person_id: str | None  # the world person it is (mostly) about
    works: tuple[str, ...]  # index work ids


@dataclass(frozen=True)
class RegistryWork:
    """A work a person declared in the registry."""

    put_code: int
    title: str
    year: int
    doi: str
    type: str
    venue: str


@dataclass(frozen=True)
class Employment:
    """An employment a person declared in the registry."""

    organisation: str
    department: str | None
    start: int | None
    end: int | None
    site: str | None


@dataclass(frozen=True)
class RegistryRecord:
    """What a person declared in the registry."""

    orcid: str
    given: str
    family: str
    other_names: tuple[str, ...]
    employments: tuple[Employment, ...]
    works: tuple[RegistryWork, ...]


@dataclass(frozen=True)
class PersonTruth:
    """What a careful person would confirm about one world person."""

    person_id: str
    last_name: str  # as an imported list shows it
    first_name: str
    records: tuple[str, ...]  # ``openalex:A…``, ``orcid:…``
    special: str | None = None

    @property
    def identity(self) -> str:
        return "confirmed" if self.records else "none"


@dataclass
class Bibliography:
    """The demo services' view of a world (see the module docstring)."""

    world: DemoWorld
    seed: int
    institutions: dict[str, Institution] = field(default_factory=dict)
    authors: dict[str, AuthorRecord] = field(default_factory=dict)
    works: dict[str, IndexWork] = field(default_factory=dict)
    registry: dict[str, RegistryRecord] = field(default_factory=dict)
    truth: dict[str, PersonTruth] = field(default_factory=dict)
    specials: dict[str, str] = field(default_factory=dict)
    #: Institution record id of each world institution name, and lab record of each group.
    institution_of: dict[str, str] = field(default_factory=dict)
    lab_of: dict[str, str] = field(default_factory=dict)
    #: Groups whose works cite the lab-level record.
    cited_labs: frozenset[str] = frozenset()
    #: For the ``moved`` person: the first year at the current institution.
    moved_year: int | None = None
    #: Works indexed per world work id (a world work may be indexed twice: the ``split`` copy).
    index_of: dict[str, list[str]] = field(default_factory=dict)
    #: The lab-level record with two parents, and the large collaboration's work id.
    joint_lab: str | None = None
    consortium: str | None = None

    def record_ids(self, person_id: str) -> tuple[str, ...]:
        """The author records the index has for a world person (the mixed one included)."""
        return tuple(a.id for a in self.authors.values() if a.person_id == person_id)

    def people_rows(self, people: tuple[Person, ...] | None = None) -> list[dict[str, str]]:
        """An import list, as someone would paste it: names, lab, institution, some ORCIDs.

        Columns: ``last_name``, ``first_name``, ``lab``, ``institution``, ``orcid``
        (for about half of the people who have one), ``career_stage``, ``role``
        and ``set``.
        """
        rng = _rng(self.world, self.seed, "list")
        rows = []
        for p in people if people is not None else self.world.people:
            truth = self.truth[p.person_id]
            group = self.world.group(p.group)
            show_orcid = bool(p.orcid) and truth.special != "none" and rng.random() < 0.5
            overlay = p.role.startswith("overlay:")
            rows.append(
                {
                    "last_name": truth.last_name,
                    "first_name": truth.first_name,
                    "lab": group.name,
                    "institution": group.institution,
                    "orcid": p.orcid if show_orcid else "",
                    "career_stage": p.career_stage,
                    "role": "projected" if overlay else "mapped",
                    "set": p.role.split(":", 1)[1] if overlay else "",
                }
            )
        return rows


def _rng(world: DemoWorld, seed: int, stream: str) -> random.Random:
    return random.Random(
        f"cartolex-demo-services/{BIBLIO_VERSION}/{world.size}/{world.seed}/{seed}/{stream}"
    )


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return unicodedata.normalize(
        "NFC", "".join(c for c in decomposed if not unicodedata.combining(c))
    )


def _initial(first: str) -> str:
    return f"{first[0]}."


_ROR_DIGITS = "0123456789abcdefghjkmnpqrstvwxyz"


def _demo_ror(rng: random.Random) -> str:
    """A ROR id in the demo block: ``0zz`` and four characters, with a wrong check number."""
    body = "zz" + "".join(rng.choice(_ROR_DIGITS) for _ in range(4))
    value = 0
    for c in body:
        value = value * 32 + _ROR_DIGITS.index(c)
    check = 98 - (value * 100) % 97
    return f"0{body}{(check + 1) % 100:02d}"


def _accented(name: str) -> str:
    """The name with its first plain vowel accented (for a list that writes accents)."""
    swaps = {"e": "é", "a": "á", "o": "ó", "i": "í", "u": "ú"}
    for i, c in enumerate(name):
        if i > 0 and c in swaps:
            return name[:i] + swaps[c] + name[i + 1 :]
    return name + "é"


def _invented_work(
    rng: random.Random,
    world: DemoWorld,
    work_id: str,
    theme: Theme,
    doi: str,
    authorships: tuple[Authorship, ...],
    *,
    year: int | None = None,
) -> IndexWork:
    """An English article outside the world, on *theme*, written from the same vocabulary."""
    year = year if year is not None else rng.randint(2012, 2026)
    settings = [s for s in SETTINGS if theme.kind == "natural" or s.social]
    methods = [m for m in METHODS if m.kind in ("any", theme.kind)]
    plan = TextPlan(
        language="en",
        kind=theme.kind,
        year=year,
        primary=theme,
        primary_weights=tuple(1.0 for _ in theme.terms),
        secondary=None,
        secondary_weights=(),
        methods=tuple(rng.sample(methods, 3)),
        settings=tuple(rng.sample(settings, 3)),
        drivers=DRIVERS,
        all_methods=METHODS,
        all_settings=SETTINGS,
    )
    title, abstract = compose(rng, plan)
    return IndexWork(
        id=work_id,
        doi=doi,
        title=title,
        abstract=abstract,
        year=year,
        date=f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        type="article",
        source_type="journal",
        venue=rng.choice(nm.VENUES_EN),
        language="en",
        themes=(theme.id,),
        authorships=authorships,
    )


class _Ids:
    """Identifiers in the demo blocks, never twice."""

    def __init__(self, rng: random.Random, taken: set[str]) -> None:
        self.rng = rng
        self.taken = set(taken)

    def make(self, prefix: str) -> str:
        while True:
            value = prefix + "999" + "".join(str(self.rng.randrange(10)) for _ in range(7))
            if value not in self.taken:
                self.taken.add(value)
                return value


def build_bibliography(world: DemoWorld, seed: int = 0) -> Bibliography:
    """Derive the bibliographic layer of *world* (see the module docstring)."""
    b = Bibliography(world=world, seed=seed)
    ids = _Ids(_rng(world, seed, "ids"), {p.openalex_id for p in world.people if p.openalex_id})
    people = {p.person_id: p for p in world.people}
    works_of: dict[str, list[Work]] = defaultdict(list)
    for w in world.works:
        for pid in w.authors:
            works_of[pid].append(w)
    indexed = [w for w in world.works if "openalex" in w.sources]
    indexed_of: dict[str, list[Work]] = defaultdict(list)
    for w in indexed:
        for pid in w.authors:
            indexed_of[pid].append(w)

    # ── institutions ──
    rng = _rng(world, seed, "institutions")
    for name in sorted({g.institution for g in world.groups}):
        group = next(g for g in world.groups if g.institution == name)
        inst = Institution(
            ids.make("I"), name, None, "education", site=group.site, lat=group.lat, lon=group.lon
        )
        b.institutions[inst.id] = inst
        b.institution_of[name] = inst.id
    for g in world.groups:
        lab = Institution(
            ids.make("I"),
            g.name,
            g.acronym,
            "facility",
            parent=b.institution_of[g.institution],
            site=g.site,
            lat=g.lat,
            lon=g.lon,
        )
        b.institutions[lab.id] = lab
        b.lab_of[g.group_id] = lab.id
    outside: list[str] = []
    for i, site in enumerate(_OUTSIDE_SITES[:4]):
        inst = Institution(ids.make("I"), _OUTSIDE_PATTERNS[i].format(site=site), None, "education")
        b.institutions[inst.id] = inst
        outside.append(inst.id)
    cohort_groups = [g.group_id for g in world.groups if not g.external]
    n_cited = max(1, round(CITED_LAB_SHARE * len(cohort_groups)))
    b.cited_labs = frozenset(rng.sample(cohort_groups, min(n_cited, len(cohort_groups) - 1)))
    # Streams of their own: RORs on institution-level records, and one joint unit.
    rng = _rng(world, seed, "ror")
    for iid in sorted(i.id for i in b.institutions.values() if i.parent is None):
        b.institutions[iid] = replace(b.institutions[iid], ror=_demo_ror(rng))
    rng = _rng(world, seed, "joint")
    tops = sorted(b.institution_of.values())
    joint_group = rng.choice(sorted(b.cited_labs or cohort_groups))
    lab = b.institutions[b.lab_of[joint_group]]
    others = [i for i in tops if i != lab.parent] or [outside[-1]]
    b.institutions[lab.id] = replace(lab, also=(rng.choice(others),))
    b.joint_lab = lab.id

    # ── the special people ──
    rng = _rng(world, seed, "specials")
    cohort = [p for p in world.people if p.role == COHORT]
    chosen: dict[str, str] = {}

    def pick(name: str, ok) -> None:
        pool = [p for p in cohort if p.person_id not in chosen.values() and ok(p)]
        if pool:
            chosen[name] = rng.choice(pool).person_id

    def n_indexed(p: Person) -> int:
        return len(indexed_of[p.person_id])

    pick("mixed", lambda p: p.openalex_id and p.orcid and n_indexed(p) >= 2)
    pick("split", lambda p: p.openalex_id and n_indexed(p) >= 3)
    pick(
        "trap",
        lambda p: p.openalex_id and n_indexed(p) >= 1 and p.group not in b.cited_labs,
    )
    pick("homonym", lambda p: p.openalex_id and n_indexed(p) >= 1)
    pick("compound", lambda p: p.openalex_id and n_indexed(p) >= 1 and "-" not in p.last_name)
    pick(
        "diacritics",
        lambda p: p.openalex_id and n_indexed(p) >= 1 and _fold(p.first_name) != p.first_name,
    )
    if "diacritics" not in chosen:
        pick("diacritics", lambda p: p.openalex_id and n_indexed(p) >= 1)
    pick(
        "moved",
        lambda p: p.openalex_id and len({w.year for w in indexed_of[p.person_id]}) >= 2,
    )
    pick("none", lambda p: not p.openalex_id and not p.orcid)
    if "none" not in chosen:
        pick("none", lambda p: n_indexed(p) >= 1)
    b.specials = dict(chosen)
    special_of = {pid: name for name, pid in chosen.items()}
    if "moved" in chosen:
        years = sorted({w.year for w in indexed_of[chosen["moved"]]})
        b.moved_year = years[len(years) // 2]

    # ── list names and index names ──
    rng = _rng(world, seed, "names")
    list_name: dict[str, tuple[str, str]] = {}
    index_name: dict[str, str] = {}
    for p in world.people:
        last, first = p.last_name, p.first_name
        shown = f"{first} {last}"
        special = special_of.get(p.person_id)
        if special == "compound":
            last = f"{last}-{nm.invented_surname(rng)}"
        elif special == "diacritics":
            if _fold(first) != first:
                shown = f"{_fold(first)} {last}"
            else:
                last = _accented(last)
        list_name[p.person_id] = (last, first)
        index_name[p.person_id] = shown

    # ── outside people ──
    rng = _rng(world, seed, "outside")
    used = {nm.slug(f"{p.first_name} {p.last_name}") for p in world.people}

    def outside_name() -> str:
        while True:
            name = f"{rng.choice(nm.FIRST_NAMES)} {nm.invented_surname(rng)}"
            if nm.slug(name) not in used:
                used.add(nm.slug(name))
                return name

    pool = [
        (ids.make("A"), outside_name(), rng.choice(outside))
        for _ in range(max(4, len(world.people) // 4))
    ]

    # ── records of world people ──
    rng = _rng(world, seed, "records")
    record_of: dict[str, str] = {}
    split_record: str | None = None
    record_orcid: dict[str, str | None] = {}
    for p in world.people:
        if (
            not p.openalex_id
            or not indexed_of[p.person_id]
            or special_of.get(p.person_id) == "none"
        ):
            continue
        record_of[p.person_id] = p.openalex_id
        keep = special_of.get(p.person_id) == "mixed" or rng.random() < P_RECORD_ORCID
        record_orcid[p.person_id] = p.orcid if (p.orcid and keep) else None
    split_works: set[str] = set()
    duplicate_of: str | None = None
    if "split" in chosen:
        pid = chosen["split"]
        split_record = ids.make("A")
        own = sorted(indexed_of[pid], key=lambda w: w.work_id)
        moved_out = rng.sample(own, max(1, len(own) // 3))
        split_works = {w.work_id for w in moved_out}
        with_doi = [w for w in own if w.doi and w.work_id not in split_works]
        duplicate_of = (with_doi or own)[0].work_id

    def institutions_for(p: Person, year: int) -> tuple[str, ...]:
        if p.person_id == chosen.get("moved") and b.moved_year and year < b.moved_year:
            return (outside[0],)
        if p.group in b.cited_labs:
            return (b.lab_of[p.group],)
        return (b.institution_of[p.institution],)

    # ── index works ──
    rng = _rng(world, seed, "works")
    wrng = _rng(world, seed, "coauthors")
    record_works: dict[str, list[str]] = defaultdict(list)
    for w in indexed:
        typ, source_type = openalex_type(w.doc_type)
        date = f"{w.year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        corresponding = rng.random() < 0.5
        authorships = []
        for rank, pid in enumerate(w.authors):
            p = people[pid]
            aid = record_of.get(pid)
            name = index_name[pid]
            if pid == chosen.get("split") and w.work_id in split_works:
                aid, name = split_record, f"{_initial(p.first_name)} {p.last_name}"
            authorships.append(
                Authorship(
                    author_id=aid,
                    name=name,
                    orcid=record_orcid.get(pid) if aid == record_of.get(pid) else None,
                    institutions=institutions_for(p, w.year),
                    corresponding=corresponding and rank == 0,
                    person_id=pid,
                )
            )
        if wrng.random() < P_OUTSIDE_COAUTHORS:
            for aid, name, inst in wrng.sample(pool, wrng.choice((1, 2))):
                authorships.append(Authorship(aid, name, None, (inst,), False, None))
        work = IndexWork(
            id=ids.make("W"),
            doi=w.doi or None,
            title=w.title,
            abstract=w.abstract,
            year=w.year,
            date=date,
            type=typ,
            source_type=source_type,
            venue=w.venue,
            language=w.language,
            themes=w.themes,
            authorships=tuple(authorships),
            world_work=w.work_id,
        )
        b.works[work.id] = work
        b.index_of.setdefault(w.work_id, []).append(work.id)
        for a in authorships:
            if a.author_id:
                record_works[a.author_id].append(work.id)
    if duplicate_of is not None and split_record is not None:
        original = b.works[b.index_of[duplicate_of][0]]
        pid = chosen["split"]
        p = people[pid]
        copy = IndexWork(
            id=ids.make("W"),
            doi=None,
            title=original.title,
            abstract=original.abstract,
            year=original.year,
            date=original.date,
            type=original.type,
            source_type=original.source_type,
            venue=original.venue,
            language=original.language,
            themes=original.themes,
            authorships=(
                Authorship(
                    split_record,
                    f"{_initial(p.first_name)} {p.last_name}",
                    None,
                    institutions_for(p, original.year),
                    False,
                    pid,
                ),
            ),
            world_work=duplicate_of,
        )
        b.works[copy.id] = copy
        b.index_of[duplicate_of].append(copy.id)
        record_works[split_record].append(copy.id)

    # ── outside works: homonyms and the half of the mixed record ──
    rng = _rng(world, seed, "outside-works")
    doi_n = 0

    def outside_works(pid: str, author_id: str, orcid: str | None, inst: str, n: int) -> None:
        nonlocal doi_n
        p = people[pid]
        themes = [t for t in THEMES if t.id not in p.themes and len(t.topics) >= MIN_TOPICS]
        theme = rng.choice(themes)
        settings = [s for s in SETTINGS if theme.kind == "natural" or s.social]
        methods = [m for m in METHODS if m.kind in ("any", theme.kind)]
        for _ in range(n):
            year = rng.randint(2012, 2026)
            plan = TextPlan(
                language="en",
                kind=theme.kind,
                year=year,
                primary=theme,
                primary_weights=tuple(1.0 for _ in theme.terms),
                secondary=None,
                secondary_weights=(),
                methods=tuple(rng.sample(methods, 3)),
                settings=tuple(rng.sample(settings, 3)),
                drivers=DRIVERS,
                all_methods=METHODS,
                all_settings=SETTINGS,
            )
            title, abstract = compose(rng, plan)
            doi_n += 1
            work = IndexWork(
                id=ids.make("W"),
                doi=f"10.5555/cartolex-biblio.{world.size.lower()}.{doi_n}",
                title=title,
                abstract=abstract,
                year=year,
                date=f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                type="article",
                source_type="journal",
                venue=rng.choice(nm.VENUES_EN),
                language="en",
                themes=(theme.id,),
                authorships=(Authorship(author_id, index_name[pid], orcid, (inst,), False, None),),
            )
            b.works[work.id] = work
            record_works[author_id].append(work.id)

    homonym_records: dict[str, str] = {}
    for special in ("homonym", "trap"):
        if special not in chosen:
            continue
        pid = chosen[special]
        hid = ids.make("A")
        homonym_records[hid] = pid
        inst = b.lab_of[people[pid].group] if special == "trap" else outside[1]
        outside_works(pid, hid, None, inst, rng.randint(2, 4))
    if "mixed" in chosen:
        pid = chosen["mixed"]
        # The other person's half is a good share of the record, as in a real merge.
        n_other = max(4, len(indexed_of[pid]) // 2 + 2)
        outside_works(pid, record_of[pid], record_orcid[pid], outside[2], n_other)

    # ── the outside co-authors' own works, and a large collaboration ──
    rng = _rng(world, seed, "pool-works")
    for aid, name, inst in pool:
        if not record_works.get(aid):
            continue
        # Their own works are on a theme none of their joint works is about.
        joint = {t for w in record_works[aid] for t in b.works[w].themes}
        themes = [t for t in THEMES if len(t.topics) >= MIN_TOPICS and t.id not in joint]
        theme = rng.choice(themes or [t for t in THEMES if len(t.topics) >= MIN_TOPICS])
        for _ in range(rng.randint(1, 3)):
            doi_n += 1
            work = _invented_work(
                rng,
                world,
                ids.make("W"),
                theme,
                f"10.5555/cartolex-biblio.{world.size.lower()}.{doi_n}",
                (Authorship(aid, name, None, (inst,), False, None),),
            )
            b.works[work.id] = work
            record_works[aid].append(work.id)
    rng = _rng(world, seed, "consortium")
    members: list[Person] = []
    for g in sorted(cohort_groups):
        indexed_here = [
            p
            for p in cohort
            if p.group == g
            and p.person_id in record_of
            and special_of.get(p.person_id) in (None, "compound", "diacritics", "moved")
        ]
        if len(indexed_here) >= 2:
            members = rng.sample(sorted(indexed_here, key=lambda p: p.person_id), 2)
            break
    if members:
        start = max(max(p.window[0] for p in members), 2016)
        year = rng.randint(start, max(start, min(p.window[1] for p in members)))
        authorships = [
            Authorship(
                record_of[p.person_id],
                index_name[p.person_id],
                record_orcid.get(p.person_id),
                institutions_for(p, year),
                False,
                p.person_id,
            )
            for p in members
        ]
        while len(authorships) < CONSORTIUM_AUTHORS:
            while True:
                name = f"{rng.choice(nm.FIRST_NAMES)} {nm.invented_surname(rng)}"
                if nm.slug(name) not in used:
                    used.add(nm.slug(name))
                    break
            aid = ids.make("A")
            authorships.append(Authorship(aid, name, None, (rng.choice(outside),), False, None))
        order = authorships[2:]
        rng.shuffle(order)
        authorships = [
            authorships[0],
            *order[: len(order) // 2],
            authorships[1],
            *order[len(order) // 2 :],
        ]
        theme = THEME_BY_ID[next(iter(members[0].themes))]
        doi_n += 1
        work = _invented_work(
            rng,
            world,
            ids.make("W"),
            theme,
            f"10.5555/cartolex-biblio.{world.size.lower()}.{doi_n}",
            tuple(authorships),
            year=year,
        )
        b.works[work.id] = work
        b.consortium = work.id
        for a in authorships:
            record_works[a.author_id].append(work.id)
            if a.person_id is None:
                b.authors[a.author_id] = AuthorRecord(a.author_id, a.name, (), None, None, ())

    # ── author records ──
    for p in world.people:
        pid = p.person_id
        if pid not in record_of:
            continue
        last, first = p.last_name, index_name[pid].rsplit(" ", 1)[0]
        b.authors[record_of[pid]] = AuthorRecord(
            id=record_of[pid],
            display_name=index_name[pid],
            alternatives=(f"{_initial(first)} {last}",),
            orcid=record_orcid.get(pid),
            person_id=pid,
            works=tuple(record_works[record_of[pid]]),
        )
    if split_record is not None:
        p = people[chosen["split"]]
        name = f"{_initial(p.first_name)} {p.last_name}"
        b.authors[split_record] = AuthorRecord(
            split_record, name, (), None, p.person_id, tuple(record_works[split_record])
        )
    for hid, pid in homonym_records.items():
        b.authors[hid] = AuthorRecord(
            hid, index_name[pid], (), None, None, tuple(record_works[hid])
        )
    for aid, name, _inst in pool:
        if record_works.get(aid):
            b.authors[aid] = AuthorRecord(aid, name, (), None, None, tuple(record_works[aid]))
    for aid, rec in list(b.authors.items()):
        if rec.person_id is None and not rec.works:  # the large collaboration's outside authors
            b.authors[aid] = replace(rec, works=tuple(record_works[aid]))

    # ── the registry ──
    rng = _rng(world, seed, "registry")
    put_code = 1000
    for p in world.people:
        if not p.orcid or special_of.get(p.person_id) == "none":
            continue
        last, first = list_name[p.person_id]
        empty = special_of.get(p.person_id) != "mixed" and rng.random() < P_EMPTY_REGISTRY
        declared = []
        for w in sorted(works_of[p.person_id], key=lambda w: w.work_id):
            take = rng.random() < P_DECLARED or special_of.get(p.person_id) == "mixed"
            if w.doi and take and not empty:
                put_code += 1
                declared.append(
                    RegistryWork(
                        put_code, w.title, w.year, w.doi, _ORCID_TYPES[w.doc_type], w.venue
                    )
                )
        group = world.group(p.group)
        first_year = min((w.year for w in works_of[p.person_id]), default=p.window[0])
        employments = []
        if p.person_id == chosen.get("moved") and b.moved_year:
            employments.append(
                Employment(
                    b.institutions[outside[0]].name, None, first_year, b.moved_year - 1, None
                )
            )
            first_year = b.moved_year
        employments.append(Employment(group.institution, group.name, first_year, None, group.site))
        b.registry[p.orcid] = RegistryRecord(
            orcid=p.orcid,
            given=first,
            family=last,
            other_names=(f"{_initial(p.first_name)} {p.last_name}",),
            employments=tuple(employments),
            works=tuple(declared),
        )

    # ── truth ──
    for p in world.people:
        pid = p.person_id
        records: list[str] = []
        if special_of.get(pid) != "mixed":
            records += [f"openalex:{rid}" for rid in (record_of.get(pid), None) if rid]
            if pid == chosen.get("split") and split_record:
                records.append(f"openalex:{split_record}")
        reg = b.registry.get(p.orcid) if p.orcid else None
        if reg is not None and reg.works:
            records.append(f"orcid:{p.orcid}")
        last, first = list_name[pid]
        b.truth[pid] = PersonTruth(pid, last, first, tuple(records), special_of.get(pid))
    return b
