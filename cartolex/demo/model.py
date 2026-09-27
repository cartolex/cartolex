# SPDX-License-Identifier: MIT
"""Data model of the demo world: sizes, groups, people, works and the world itself."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .vocabulary import Method, Setting, Theme

GENERATOR_VERSION = "1"
FORMAT = "cartolex-demo/1"
NOW_YEAR = 2026
FIRST_YEAR = 2012
CAREER_STAGES = ("phd", "postdoc", "researcher", "senior")
COHORT = "cohort"
APPLICANTS = "overlay:applicants"
COVERAGES = ("good", "thin", "no_data")
SOURCES = ("openalex", "hal", "orcid")
DOC_TYPES = ("article", "preprint", "proceedings", "report", "thesis")
LANGUAGES = ("en", "fr")

# Years a person can publish in, by career stage.
CAREER_WINDOWS: dict[str, tuple[int, int]] = {
    "phd": (NOW_YEAR - 4, NOW_YEAR),
    "postdoc": (NOW_YEAR - 8, NOW_YEAR),
    "researcher": (FIRST_YEAR, NOW_YEAR),
    "senior": (FIRST_YEAR, NOW_YEAR),
}


@dataclass(frozen=True)
class SizeSpec:
    """How big a world is."""

    code: str
    people: int  # cohort members
    groups: int  # cohort groups
    sites: int
    institutions: int
    applicants: int  # people of the projected set (never in the cohort)
    external_groups: int  # extra groups that only host applicants


SIZES: dict[str, SizeSpec] = {
    "XS": SizeSpec("XS", people=12, groups=3, sites=2, institutions=2, applicants=2, external_groups=1),
    "S": SizeSpec("S", people=40, groups=6, sites=3, institutions=4, applicants=4, external_groups=1),
    "L": SizeSpec(
        "L", people=350, groups=30, sites=8, institutions=12, applicants=35, external_groups=3
    ),
}  # fmt: skip


@dataclass(frozen=True)
class Group:
    """A research group (lab) with a theme profile."""

    group_id: str
    acronym: str
    name: str
    institution: str
    site: str
    lat: float
    lon: float
    themes: dict[str, float]  # theme id -> weight, heaviest first
    french_share: float  # probability that a work led here is written in French
    settings: tuple[Setting, ...] = field(repr=False, default=())
    methods: tuple[Method, ...] = field(repr=False, default=())
    external: bool = False  # hosts only people of the projected set


@dataclass(frozen=True)
class Person:
    """An invented person. There is deliberately no gender attribute."""

    person_id: str
    last_name: str
    first_name: str
    group: str  # group id
    institution: str
    site: str
    career_stage: str
    orcid: str
    openalex_id: str
    idhal: str
    role: str  # "cohort" or "overlay:<set>"
    coverage: str  # "good", "thin" or "no_data"
    themes: dict[str, float]  # theme id -> weight, heaviest first
    methods: tuple[Method, ...] = field(repr=False, default=())
    term_weights: dict[str, tuple[float, ...]] = field(repr=False, default_factory=dict)

    @property
    def window(self) -> tuple[int, int]:
        """First and last year this person can publish in."""
        return CAREER_WINDOWS[self.career_stage]


@dataclass(frozen=True)
class Work:
    """One document: its bibliographic record, its authors and its text."""

    work_id: str
    title: str
    abstract: str
    year: int
    doc_type: str
    language: str
    doi: str
    venue: str
    sources: tuple[str, ...]
    themes: tuple[str, ...]  # theme ids, primary first
    authors: tuple[str, ...]  # person ids in author order

    @property
    def text(self) -> str:
        """The text file content: title, blank line, abstract."""
        return f"{self.title}\n\n{self.abstract}\n"

    @property
    def words(self) -> int:
        """Number of words in title and abstract."""
        return len(self.title.split()) + len(self.abstract.split())


@dataclass(frozen=True)
class DemoWorld:
    """A generated world: groups, people and works, plus the themes they come from."""

    size: str
    seed: int
    groups: tuple[Group, ...]
    people: tuple[Person, ...]
    works: tuple[Work, ...]
    themes: tuple[Theme, ...]

    @property
    def cohort(self) -> tuple[Person, ...]:
        """People of the fitted cohort."""
        return tuple(p for p in self.people if p.role == COHORT)

    @property
    def overlay_sets(self) -> dict[str, tuple[Person, ...]]:
        """Projected sets by name (``applicants`` …): people never in the cohort."""
        sets: dict[str, list[Person]] = {}
        for p in self.people:
            if p.role.startswith("overlay:"):
                sets.setdefault(p.role.split(":", 1)[1], []).append(p)
        return {name: tuple(members) for name, members in sets.items()}

    def person(self, person_id: str) -> Person:
        """The person with this id."""
        return self._people_by_id()[person_id]

    def group(self, group_id: str) -> Group:
        """The group with this id."""
        return {g.group_id: g for g in self.groups}[group_id]

    def _people_by_id(self) -> dict[str, Person]:
        return {p.person_id: p for p in self.people}

    def works_of(self, person_id: str) -> tuple[Work, ...]:
        """Works that list *person_id* among their authors, in id order."""
        return tuple(w for w in self.works if person_id in w.authors)

    def counts(self) -> dict[str, int]:
        """Headline counts, as written in the manifest."""
        return {
            "people": len(self.people),
            "cohort": len(self.cohort),
            "applicants": sum(len(v) for v in self.overlay_sets.values()),
            "groups": len(self.groups),
            "institutions": len({g.institution for g in self.groups}),
            "sites": len({g.site for g in self.groups}),
            "works": len(self.works),
            "works_en": sum(1 for w in self.works if w.language == "en"),
            "works_fr": sum(1 for w in self.works if w.language == "fr"),
            "authorships": sum(len(w.authors) for w in self.works),
            "words": sum(w.words for w in self.works),
        }

    def write(self, out_dir: Path | str, *, overwrite: bool = False) -> dict:
        """Write the neutral ``cartolex-demo/1`` files into *out_dir*; return the manifest.

        Refuses a directory that already holds a demo world unless *overwrite*
        is true, in which case the previous world's files are replaced.
        """
        from .writers import write_world

        return write_world(self, Path(out_dir), overwrite=overwrite)

    def write_corpus(self, workspace: Path | str, *, overwrite: bool = False) -> dict:
        """Write the engine's corpus contract into *workspace*; return a summary.

        The fitted cohort goes to ``manual_index.csv`` and
        ``automatic_data/corpus_manual/``; each projected set to
        ``overlay/<set>/index.csv`` and ``overlay/<set>/texts/``.
        """
        from .writers import write_corpus

        return write_corpus(self, Path(workspace), overwrite=overwrite)
