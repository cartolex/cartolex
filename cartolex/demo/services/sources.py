# SPDX-License-Identifier: MIT
"""The open archive, the journal platform and the full-text layer of the demo services.

Derived from a :class:`~cartolex.demo.services.biblio.Bibliography`
deterministically, with random streams of its own (the world and the index
layer never move), it says what each of these services holds:

* **the open archive (HAL)** — a deposit for every world work whose sources
  include ``hal``, with its authors (idHAL when the person has one, some
  ORCIDs), their structures (a lab-level structure whose parent is its
  institution, sometimes the institution alone), titles and abstracts in the
  work's language and sometimes a translation, the DOI (missing on some
  deposits, whose year is then sometimes the deposit's year, one later), a
  PDF file on most; plus the **preprint** of some published articles (a
  deposit of type ``UNDEFINED`` a year earlier, with an arXiv identifier) and
  a few deposits of an outside homonym;
* **the journal platform (SciELO)** — in a world written in Portuguese, the
  Portuguese articles and some English articles of the groups that write in
  Portuguese, with their abstracts and titles in two or three languages
  (translations written anew from the same plan), their authors (most ORCIDs
  shown) and, with bodies, their full text in each language;
* **preprint servers** — arXiv (world preprints, and the preprints of the
  published articles above, with the DOI of their published version) with a
  LaTeX source, one gzipped file or a tar archive; bioRxiv for the other
  preprints about the natural world;
* **Europe PMC** — half of the published articles about the natural world,
  most of them open access with a JATS full text;
* **open-access links** — for OpenAlex: the HAL file of a work, else its arXiv PDF.

Identifiers stay in blocks nobody uses: HAL ids ``hal-099…`` (``hal-098…`` for
the preprint deposits), document ids from 99,000,000, structure ids from
9,900,000, arXiv ids ``99MM.NNNNN`` (a month that new-style arXiv ids never
had), PMIDs from 99,000,000 and PMCIDs ``PMC99…``, journal ISSNs whose check
character is wrong, and DOIs under ``10.5555``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .. import names as nm
from ..bodies import compose_body
from ..model import FIRST_YEAR, Person, Work
from ..texts import TextPlan, compose
from ..vocabulary import DRIVERS, METHODS, SETTINGS, THEME_BY_ID
from .biblio import Bibliography
from .http import DEMO_BASE

__all__ = [
    "DEMO_BASE",
    "SOURCES_VERSION",
    "ArxivEntry",
    "BiorxivEntry",
    "HalAuthor",
    "HalDeposit",
    "HalStructure",
    "PmcEntry",
    "ScieloArticle",
    "SourcesLayer",
    "sources_layer",
]

SOURCES_VERSION = "1"

DOCID_BLOCK = 99_000_000
STRUCT_BLOCK = 9_900_000
PMID_BLOCK = 99_000_000
SCIELO_COLLECTION = "dmo"

P_DOI_MISSING = 0.2
P_YEAR_SHIFT = 0.5
P_FILE = 0.6
P_HAL_ORCID = 0.5
P_INSTITUTION_ONLY = 0.2
P_OUTSIDE_COAUTHOR = 0.15
P_TRANSLATED = {"fr": 0.5, "pt": 0.5, "en": 0.2}
P_PREPRINT_VERSION = 0.15
P_ARXIV = 0.5
P_SCIELO_ENGLISH = 0.3
P_SCIELO_FRENCH = 0.3
P_SCIELO_ORCID = 0.8
P_PMC = 0.5
P_PMC_OPEN = 0.7
P_LATEX_ARCHIVE = 0.5
P_LATEX_MACROS = 0.5

_HAL_TYPES = {
    "article": "ART",
    "proceedings": "COMM",
    "preprint": "UNDEFINED",
    "report": "REPORT",
    "thesis": "THESE",
}


@dataclass(frozen=True)
class HalStructure:
    """A structure of the archive's referential."""

    docid: int
    name: str
    acronym: str | None
    type: str  # "laboratory" or "institution"
    parents: tuple[int, ...] = ()
    country: str | None = None


@dataclass(frozen=True)
class HalAuthor:
    """One author of a deposit."""

    first: str
    last: str
    idhal: str | None
    orcid: str | None
    structures: tuple[int, ...]
    person_id: str | None  # the world person; None for an outside author

    @property
    def full_name(self) -> str:
        return f"{self.first} {self.last}"


@dataclass(frozen=True)
class HalDeposit:
    """One deposit of the open archive."""

    hal_id: str
    docid: int
    doc_type: str  # the archive's code: ART, COMM, UNDEFINED, REPORT, THESE
    language: str
    titles: dict[str, str]  # the deposit's language first
    abstracts: dict[str, str]
    year: int
    date: str
    doi: str | None
    arxiv: str | None
    authors: tuple[HalAuthor, ...]
    file: bool
    world_work: str | None  # the world work it holds (None: an outside homonym's work)
    preprint_of: str | None = None  # the world article this preprint became
    body: str = ""


@dataclass(frozen=True)
class ScieloArticle:
    """One article of the journal platform."""

    pid: str
    collection: str
    issn: str
    journal: str
    world_work: str
    doi: str | None
    year: int
    date: str
    language: str
    titles: dict[str, str]  # the article's language first
    abstracts: dict[str, str]
    bodies: dict[str, str]  # per language, with bodies only
    authors: tuple[tuple[str, str, str | None, int], ...]  # given, surname, ORCID, affiliation
    affiliations: tuple[str, ...]


@dataclass(frozen=True)
class ArxivEntry:
    """One preprint of the arXiv-like server."""

    arxiv_id: str
    world_work: str
    title: str
    abstract: str
    language: str
    body: str
    year: int
    date: str
    doi: str | None  # the published version's DOI, once published
    authors: tuple[tuple[str, str], ...]
    archive: bool  # a tar archive rather than one gzipped file
    macros: bool  # accents written as LaTeX macros
    preprint_of: str | None = None


@dataclass(frozen=True)
class BiorxivEntry:
    """One preprint of the life-science preprint server."""

    doi: str
    server: str
    world_work: str
    title: str
    abstract: str
    body: str
    date: str
    authors: tuple[tuple[str, str], ...]
    published_doi: str | None = None


@dataclass(frozen=True)
class PmcEntry:
    """One article of the Europe PMC-like index."""

    pmid: str
    pmcid: str
    doi: str
    world_work: str
    title: str
    abstract: str
    body: str
    year: int
    open_access: bool
    authors: tuple[tuple[str, str], ...]


@dataclass
class SourcesLayer:
    """What the open archive, the journal platform and the full-text services hold."""

    bib: Bibliography
    structures: dict[int, HalStructure] = field(default_factory=dict)
    lab_structure: dict[str, int] = field(default_factory=dict)  # group id → structure
    institution_structure: dict[str, int] = field(default_factory=dict)  # name → structure
    deposits: dict[str, HalDeposit] = field(default_factory=dict)
    scielo: dict[str, ScieloArticle] = field(default_factory=dict)
    arxiv: dict[str, ArxivEntry] = field(default_factory=dict)
    biorxiv: dict[str, BiorxivEntry] = field(default_factory=dict)
    pmc: dict[str, PmcEntry] = field(default_factory=dict)
    #: World work → the absolute (demo-base) link of an open-access PDF.
    oa_links: dict[str, str] = field(default_factory=dict)
    _translations: dict[tuple[str, str], tuple[str, str, str]] = field(default_factory=dict)

    @property
    def world(self):  # noqa: ANN201 - the DemoWorld
        return self.bib.world

    def work(self, work_id: str) -> Work:
        return self._works[work_id]

    def text(self, work: Work, language: str) -> tuple[str, str, str]:
        """``(title, abstract, body)`` of *work* in *language*: its own text, or a translation
        written anew from the same plan (the body only in a world with bodies)."""
        if language == work.language:
            return work.title, work.abstract, work.body
        key = (work.work_id, language)
        if key not in self._translations:
            self._translations[key] = _translate(self, work, language)
        return self._translations[key]

    def pdf_blocks(self, work: Work, *, title: str | None = None) -> list[str]:
        """What a PDF of *work* shows: title, abstract, then the body's blocks."""
        blocks = [title or work.title, work.abstract]
        if work.body:
            blocks += [b for b in work.body.split("\n\n") if b.strip()]
        return blocks

    def __post_init__(self) -> None:
        self._works = {w.work_id: w for w in self.bib.world.works}


def _rng(bib: Bibliography, stream: str) -> random.Random:
    w = bib.world
    return random.Random(
        f"cartolex-demo-sources/{SOURCES_VERSION}/{w.size}/{w.seed}/{bib.seed}/{stream}"
    )


def _number(work: Work) -> int:
    return int(work.work_id.lstrip("w"))


def _plan(layer: SourcesLayer, work: Work, language: str) -> TextPlan:
    world = layer.world
    authors = [world.person(pid) for pid in work.authors]
    lead = authors[0]
    group = world.group(lead.group)
    primary = THEME_BY_ID[work.themes[0]]
    secondary = THEME_BY_ID[work.themes[1]] if len(work.themes) > 1 else None

    def weights(theme_id: str) -> tuple[float, ...]:
        for a in authors:
            if theme_id in a.term_weights:
                return a.term_weights[theme_id]
        return tuple(1.0 for _ in THEME_BY_ID[theme_id].terms)

    return TextPlan(
        language=language,
        kind=primary.kind,
        year=work.year,
        primary=primary,
        primary_weights=weights(primary.id),
        secondary=secondary,
        secondary_weights=weights(secondary.id) if secondary else (),
        methods=lead.methods,
        settings=group.settings,
        drivers=DRIVERS,
        all_methods=METHODS,
        all_settings=SETTINGS,
    )


def _translate(layer: SourcesLayer, work: Work, language: str) -> tuple[str, str, str]:
    rng = _rng(layer.bib, f"translation/{work.work_id}/{language}")
    kept: list[Any] = []
    title, abstract = compose(rng, _plan(layer, work, language), keep=kept)
    body = ""
    if work.body:
        body = compose_body(
            _rng(layer.bib, f"translation-body/{work.work_id}/{language}"),
            _plan(layer, work, language),
            kept[0],
        )
    return title, abstract, body


def _date(bib: Bibliography, work: Work, rng: random.Random) -> str:
    indexed = bib.index_of.get(work.work_id)
    if indexed:
        return bib.works[indexed[0]].date
    return f"{work.year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def _names(person: Person) -> tuple[str, str]:
    return person.first_name, person.last_name


def _bad_issn(n: int) -> str:
    """An ISSN-shaped string whose check character is wrong (so no journal can have it)."""
    digits = f"9990{n:03d}"
    total = sum(int(d) * w for d, w in zip(digits, range(8, 1, -1), strict=True))
    check = (11 - total % 11) % 11
    wrong = (check + 1) % 11
    return f"{digits[:4]}-{digits[4:]}{'X' if wrong == 10 else wrong}"


def _structures(layer: SourcesLayer) -> None:
    world = layer.world
    n = STRUCT_BLOCK
    institution: dict[str, int] = {}
    for name in sorted({g.institution for g in world.groups}):
        n += 1
        layer.structures[n] = HalStructure(n, name, None, "institution")
        institution[name] = n
    for g in world.groups:
        n += 1
        layer.structures[n] = HalStructure(
            n, g.name, g.acronym, "laboratory", (institution[g.institution],)
        )
        layer.lab_structure[g.group_id] = n
    for inst in layer.bib.institutions.values():
        if inst.parent is None and inst.name not in institution:
            n += 1
            layer.structures[n] = HalStructure(n, inst.name, None, "institution")
            institution[inst.name] = n
    layer.institution_structure = institution


def _hal_authors(
    layer: SourcesLayer, work: Work, rng: random.Random, outside: list[tuple[str, str, int]]
) -> tuple[HalAuthor, ...]:
    world = layer.world
    institution = layer.institution_structure
    authors = []
    for pid in work.authors:
        p = world.person(pid)
        lab = layer.lab_structure[p.group]
        structs = (institution[p.institution],) if rng.random() < P_INSTITUTION_ONLY else (lab,)
        orcid = p.orcid if (p.orcid and rng.random() < P_HAL_ORCID) else None
        authors.append(HalAuthor(p.first_name, p.last_name, p.idhal or None, orcid, structs, pid))
    if outside and rng.random() < P_OUTSIDE_COAUTHOR:
        first, last, struct = rng.choice(outside)
        authors.append(HalAuthor(first, last, None, None, (struct,), None))
    return tuple(authors)


def _translated(
    layer: SourcesLayer, work: Work, rng: random.Random
) -> tuple[dict[str, str], dict[str, str]]:
    titles = {work.language: work.title}
    abstracts = {work.language: work.abstract}
    other = "en" if work.language != "en" else "fr"
    if rng.random() < P_TRANSLATED.get(work.language, 0.0):
        title, abstract, _ = layer.text(work, other)
        titles[other], abstracts[other] = title, abstract
    return titles, abstracts


def _archive(layer: SourcesLayer) -> None:
    bib, world = layer.bib, layer.world
    rng = _rng(bib, "archive")
    outside_names = []
    institution = layer.institution_structure
    outside_structs = [
        institution[i.name]
        for i in bib.institutions.values()
        if i.parent is None and i.name not in {g.institution for g in world.groups}
    ]
    for _ in range(4):
        first, last = rng.choice(nm.FIRST_NAMES), nm.invented_surname(rng)
        outside_names.append((first, last, rng.choice(outside_structs)))
    arxiv_n = 0

    def next_arxiv(year: int) -> str:
        nonlocal arxiv_n
        arxiv_n += 1
        return f"99{(year % 12) + 1:02d}.{arxiv_n:05d}"

    for work in world.works:
        if "hal" not in work.sources:
            continue
        n = _number(work)
        doi = work.doi or None
        year, date = work.year, _date(bib, work, rng)
        if doi and rng.random() < P_DOI_MISSING:
            doi = None
            if rng.random() < P_YEAR_SHIFT:
                year += 1
                date = f"{year}{date[4:]}"
        titles, abstracts = _translated(layer, work, rng)
        authors = _hal_authors(layer, work, rng, outside_names)
        arxiv = None
        if work.doc_type == "preprint":
            natural = THEME_BY_ID[work.themes[0]].kind == "natural"
            if rng.random() < P_ARXIV or not natural:
                arxiv = next_arxiv(work.year)
                _arxiv_entry(layer, work, arxiv, rng, doi=None, preprint_of=None)
            elif work.doi:
                _biorxiv_entry(layer, work, rng)
        deposit = HalDeposit(
            hal_id=f"hal-099{n:05d}",
            docid=DOCID_BLOCK + n,
            doc_type=_HAL_TYPES[work.doc_type],
            language=work.language,
            titles=titles,
            abstracts=abstracts,
            year=year,
            date=date,
            doi=doi,
            arxiv=arxiv,
            authors=authors,
            file=rng.random() < P_FILE,
            world_work=work.work_id,
            body=work.body,
        )
        layer.deposits[deposit.hal_id] = deposit
        # The preprint this published article was first (an earlier deposit with an arXiv id).
        if work.doc_type == "article" and work.doi and rng.random() < P_PREPRINT_VERSION:
            pre_year = max(FIRST_YEAR, work.year - 1)
            pre_id = next_arxiv(pre_year)
            _arxiv_entry(
                layer, work, pre_id, rng, doi=work.doi, preprint_of=work.work_id, year=pre_year
            )
            pre = HalDeposit(
                hal_id=f"hal-098{n:05d}",
                docid=DOCID_BLOCK + 50_000 + n,
                doc_type="UNDEFINED",
                language=work.language,
                titles={work.language: work.title},
                abstracts={work.language: work.abstract},
                year=pre_year,
                date=f"{pre_year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                doi=None,
                arxiv=pre_id,
                authors=authors,
                file=False,
                world_work=work.work_id,
                preprint_of=work.work_id,
                body=work.body,
            )
            layer.deposits[pre.hal_id] = pre
    # An outside homonym of one person deposits works too (found by a name search only).
    homonym = bib.specials.get("homonym")
    if homonym:
        p = world.person(homonym)
        records = [
            a
            for a in bib.authors.values()
            if a.person_id is None and a.display_name == f"{p.first_name} {p.last_name}"
        ]
        k = 0
        for record in records[:1]:
            for wid in record.works[:2]:
                w = bib.works[wid]
                k += 1
                deposit = HalDeposit(
                    hal_id=f"hal-0997{k:04d}",
                    docid=DOCID_BLOCK + 90_000 + k,
                    doc_type="ART",
                    language=w.language,
                    titles={w.language: w.title},
                    abstracts={w.language: w.abstract},
                    year=w.year,
                    date=w.date,
                    doi=w.doi,
                    arxiv=None,
                    authors=(
                        HalAuthor(
                            p.first_name, p.last_name, None, None, (outside_names[0][2],), None
                        ),
                    ),
                    file=False,
                    world_work=None,
                )
                layer.deposits[deposit.hal_id] = deposit


def _arxiv_entry(
    layer: SourcesLayer,
    work: Work,
    arxiv_id: str,
    rng: random.Random,
    *,
    doi: str | None,
    preprint_of: str | None,
    year: int | None = None,
) -> None:
    year = year or work.year
    layer.arxiv[arxiv_id] = ArxivEntry(
        arxiv_id=arxiv_id,
        world_work=work.work_id,
        title=work.title,
        abstract=work.abstract,
        language=work.language,
        body=work.body,
        year=year,
        date=f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        doi=doi,
        authors=tuple(_names(layer.world.person(pid)) for pid in work.authors),
        archive=rng.random() < P_LATEX_ARCHIVE,
        macros=rng.random() < P_LATEX_MACROS,
        preprint_of=preprint_of,
    )


def _biorxiv_entry(layer: SourcesLayer, work: Work, rng: random.Random) -> None:
    layer.biorxiv[work.doi] = BiorxivEntry(
        doi=work.doi,
        server="biorxiv" if rng.random() < 0.5 else "medrxiv",
        world_work=work.work_id,
        title=work.title,
        abstract=work.abstract,
        body=work.body,
        date=_date(layer.bib, work, rng),
        authors=tuple(_names(layer.world.person(pid)) for pid in work.authors),
    )


def _journals(layer: SourcesLayer) -> None:
    world, bib = layer.world, layer.bib
    if "pt" not in world.languages:
        return
    rng = _rng(bib, "journals")
    issns: dict[str, str] = {}
    for work in world.works:
        if work.doc_type != "article":
            continue
        lead = world.person(work.authors[0])
        group = world.group(lead.group)
        if work.language != "pt" and not (
            work.language == "en" and group.portuguese_share > 0 and rng.random() < P_SCIELO_ENGLISH
        ):
            continue
        issn = issns.setdefault(work.venue, _bad_issn(len(issns) + 1))
        languages = [work.language] + [x for x in ("en", "pt") if x != work.language]
        if rng.random() < P_SCIELO_FRENCH:
            languages.append("fr")
        titles, abstracts, bodies = {}, {}, {}
        for lang in languages:
            title, abstract, body = layer.text(work, lang)
            titles[lang], abstracts[lang] = title, abstract
            if body:
                bodies[lang] = body
        affiliations: list[str] = []
        authors = []
        for pid in work.authors:
            p = world.person(pid)
            g = world.group(p.group)
            label = f"{g.name}, {g.institution}"
            if label not in affiliations:
                affiliations.append(label)
            orcid = p.orcid if (p.orcid and rng.random() < P_SCIELO_ORCID) else None
            authors.append((p.first_name, p.last_name, orcid, affiliations.index(label) + 1))
        date = _date(bib, work, rng)
        pid = f"S{issn}{work.year}0001{_number(work):05d}"
        layer.scielo[pid] = ScieloArticle(
            pid=pid,
            collection=SCIELO_COLLECTION,
            issn=issn,
            journal=work.venue,
            world_work=work.work_id,
            doi=work.doi or None,
            year=work.year,
            date=date,
            language=work.language,
            titles=titles,
            abstracts=abstracts,
            bodies=bodies,
            authors=tuple(authors),
            affiliations=tuple(affiliations),
        )


def _pmc(layer: SourcesLayer) -> None:
    rng = _rng(layer.bib, "pmc")
    for work in layer.world.works:
        if work.doc_type != "article" or not work.doi:
            continue
        if THEME_BY_ID[work.themes[0]].kind != "natural" or rng.random() >= P_PMC:
            continue
        n = _number(work)
        layer.pmc[f"PMC99{n:06d}"] = PmcEntry(
            pmid=str(PMID_BLOCK + n),
            pmcid=f"PMC99{n:06d}",
            doi=work.doi,
            world_work=work.work_id,
            title=work.title,
            abstract=work.abstract,
            body=work.body,
            year=work.year,
            open_access=rng.random() < P_PMC_OPEN,
            authors=tuple(_names(layer.world.person(pid)) for pid in work.authors),
        )


def _oa_links(layer: SourcesLayer) -> None:
    """Open-access copies, served by the demo's file host: the works deposited with a file,
    the preprints on the arXiv-like server and the open articles of Europe PMC."""
    works: set[str] = set()
    for deposit in layer.deposits.values():
        if deposit.file and deposit.world_work and deposit.preprint_of is None:
            works.add(deposit.world_work)
    works |= {e.world_work for e in layer.arxiv.values() if e.preprint_of is None}
    works |= {e.world_work for e in layer.pmc.values() if e.open_access}
    for work_id in sorted(works):
        layer.oa_links[work_id] = f"{DEMO_BASE}files/oa/{work_id}.pdf"


def _build(bib: Bibliography) -> SourcesLayer:
    layer = SourcesLayer(bib)
    _structures(layer)
    _archive(layer)
    _journals(layer)
    _pmc(layer)
    _oa_links(layer)
    return layer


def sources_layer(bib: Bibliography) -> SourcesLayer:
    """The layer of *bib*, built once and kept with it (every demo service shares it)."""
    cached = vars(bib).get("_sources_layer")
    if cached is None:
        cached = _build(bib)
        vars(bib)["_sources_layer"] = cached
    return cached
