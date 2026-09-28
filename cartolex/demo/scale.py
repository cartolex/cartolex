# SPDX-License-Identifier: MIT
"""Large demo worlds, streamed straight into a project: 10⁴ to 10⁶ people.

:func:`generate` keeps a whole world in memory, which is right for the small
worlds of the tests and the reference. A world of a hundred thousand people
holds some 700 000 texts: :func:`write_scale_project` writes it into a project's
source tables a row group at a time, and never holds more than the people's
compact attributes and one row group of texts.

The world keeps the demo world's structure: groups of about twelve people with
a profile over the twelve themes (one primary theme, one or two neighbours),
people with a mixture of one to three themes and their own preferences over
each theme's terms (mixed with their group's), works led by each person in the
years of their career stage, in English or French (or Portuguese) by the
group's habits, co-authored mostly within the group and sometimes across groups
that share the work's theme, and a projected set of applicants (one person in
ten, beside the mapped cohort). Texts are composed by the demo's composer.

Everything is deterministic: the same number of people and seed give the same
tables, whatever the number of worker processes. Each person draws from a
random stream of their own, so the works can be composed in parallel.
"""

from __future__ import annotations

import math
import os
import random
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import names as nm
from .generator import (
    _DOC_TYPE_WEIGHTS,
    _DOI_PROBABILITY,
    _LED_WORKS,
    _SPREAD_ORDER,
    _STAGE_WEIGHTS,
    COAUTHOR_WEIGHTS,
    FRENCH_GROUPS,
    PORTUGUESE_GROUPS,
    _normalise,
    _preference,
    _raw_scores,
    _split,
    _weighted_choice,
    _zipf_weights,
    parse_languages,
)
from .identifiers import make_idhal, orcid_check_character
from .model import CAREER_STAGES, CAREER_WINDOWS, NOW_YEAR, SOURCES
from .texts import MIN_TOPICS, TextPlan, compose
from .vocabulary import DRIVERS, METHODS, SETTINGS, THEME_BY_ID, THEMES

__all__ = ["PEOPLE_PER_GROUP", "ScaleSummary", "scale_groups", "write_scale_project"]

#: The mapped people of a group, on average (as in the demo worlds).
PEOPLE_PER_GROUP = 12
#: Projected people (applicants) per mapped person.
APPLICANT_SHARE = 0.1
#: Rows per Parquet row group.
ROW_GROUP = 50_000

_THEME_IDS = tuple(t.id for t in THEMES)
_THEME_INDEX = {tid: i for i, tid in enumerate(_THEME_IDS)}
_STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)
_COVERAGES = ("good", "thin", "no_data")


def scale_groups(people: int) -> tuple[int, int, int]:
    """(cohort groups, groups hosting only applicants, institutions) for *people* mapped people."""
    groups = max(3, round(people / PEOPLE_PER_GROUP))
    external = max(1, groups // 10)
    institutions = min(len(nm.SITES) * len(nm.INSTITUTION_PATTERNS), max(2, round(groups / 2.5)))
    return groups, external, institutions


@dataclass(frozen=True)
class _Group:
    acronym: str
    name: str
    institution: int
    site: nm.Site
    lat: float
    lon: float
    themes: dict[str, float]
    french_share: float
    portuguese_share: float
    settings: tuple
    methods: tuple
    external: bool


@dataclass
class ScaleSummary:
    """What :func:`write_scale_project` wrote."""

    people: int = 0
    mapped: int = 0
    applicants: int = 0
    groups: int = 0
    institutions: int = 0
    texts: int = 0
    authorships: int = 0
    characters: int = 0
    by_language: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "people": self.people,
            "mapped": self.mapped,
            "applicants": self.applicants,
            "groups": self.groups,
            "institutions": self.institutions,
            "texts": self.texts,
            "authorships": self.authorships,
            "characters": self.characters,
            "by_language": dict(sorted(self.by_language.items())),
        }


class _World:
    """The compact world: groups, and each person's attributes as arrays."""

    def __init__(self, people: int, seed: int, languages: tuple[str, ...]) -> None:
        self.n_mapped = int(people)
        self.seed = int(seed)
        self.languages = languages
        self.key = f"cartolex-scale/{self.n_mapped}/{self.seed}"
        self.groups: list[_Group] = []
        self.institutions: list[tuple[str, nm.Site]] = []

    def rng(self, *stream: object) -> random.Random:
        return random.Random("/".join([self.key, *map(str, stream)]))

    # -- structure -------------------------------------------------------------

    def build_groups(self) -> None:
        rng = self.rng("structure")
        n_groups, n_external, n_inst = scale_groups(self.n_mapped)
        pairs = [(p, s) for s in nm.SITES for p in nm.INSTITUTION_PATTERNS]
        rng.shuffle(pairs)
        self.institutions = [(p.format(site=s.name), s) for p, s in pairs[:n_inst]]
        per_inst = _split(n_groups, n_inst, rng, minimum=1)
        slots = [i for i, n in enumerate(per_inst) for _ in range(n)]
        order = list(_SPREAD_ORDER)
        shift = rng.randrange(len(order))
        order = order[shift:] + order[:shift]
        french = set(rng.sample(range(n_groups), max(1, math.ceil(FRENCH_GROUPS * n_groups))))
        names: dict[str, int] = {}
        acronyms: set[str] = set()
        for i, inst in enumerate(slots):
            primary = order[i % len(order)]
            self._add_group(rng, primary, inst, names, acronyms, False, i in french)
        for _ in range(n_external):
            inst = rng.randrange(n_inst)
            primary = rng.choice(_SPREAD_ORDER)
            self._add_group(rng, primary, inst, names, acronyms, True, rng.random() < 0.3)

    def _add_group(
        self,
        rng: random.Random,
        primary_id: str,
        inst: int,
        names: dict[str, int],
        acronyms: set[str],
        external: bool,
        french_heavy: bool,
    ) -> None:
        primary = THEME_BY_ID[primary_id]
        neighbours = list(primary.neighbours)
        secondaries = []
        for _ in range(rng.choice((1, 1, 2))):
            pick = _weighted_choice(
                rng, neighbours, [1.0 / (k + 1) for k in range(len(neighbours))]
            )
            neighbours.remove(pick)
            secondaries.append(pick)
        raw = {primary.id: rng.uniform(0.55, 0.7)}
        rest = 1.0 - raw[primary.id]
        split = [rng.uniform(0.5, 1.5) for _ in secondaries]
        for theme_id, s in zip(secondaries, split, strict=True):
            raw[theme_id] = rest * s / sum(split)
        inst_name, site = self.institutions[inst]
        stem = rng.choice(primary.group_stems)
        kind, letter = rng.choice(nm.GROUP_KINDS)
        base = f"{stem} {kind} of {site.name}"
        names[base] = names.get(base, 0) + 1
        name = base if names[base] == 1 else f"{base} {names[base]}"
        initials = "".join(w[0].upper() for w in stem.replace("-", " ").split() if w != "and")
        acronym, n = f"{initials}{letter}-{site.code}", 2
        while acronym in acronyms:
            acronym, n = f"{initials}{letter}{n}-{site.code}", n + 1
        acronyms.add(acronym)
        french = rng.uniform(0.28, 0.42) if french_heavy else rng.uniform(0.05, 0.15)
        if primary.kind == "social":
            french += 0.06
        portuguese = 0.0
        if "pt" in self.languages:
            heavy = french < 0.2 and rng.random() < PORTUGUESE_GROUPS
            portuguese = rng.uniform(0.3, 0.45) if heavy else rng.uniform(0.03, 0.08)
        kinds_ok = ("any", primary.kind)
        methods = [m for m in METHODS if m.kind in kinds_ok]
        settings = [s for s in SETTINGS if primary.kind == "natural" or s.social]
        self.groups.append(
            _Group(
                acronym=acronym,
                name=name,
                institution=inst,
                site=site,
                lat=round(site.lat + rng.uniform(-0.04, 0.04), 4),
                lon=round(site.lon + rng.uniform(-0.04, 0.04), 4),
                themes=_normalise(raw),
                french_share=round(min(french, 0.7), 3),
                portuguese_share=round(portuguese, 3),
                settings=tuple(rng.sample(settings, 3)),
                methods=tuple(rng.sample(methods, 4)),
                external=external,
            )
        )

    # -- people ----------------------------------------------------------------

    def build_people(self) -> Iterator[dict]:
        """Draw every person; keep their compact attributes; yield their table rows."""
        rng = self.rng("people")
        cohort = [g for g, grp in enumerate(self.groups) if not grp.external]
        external = [g for g, grp in enumerate(self.groups) if grp.external]
        sizes = _split(self.n_mapped, len(cohort), rng, minimum=2)
        plan = [(g, None) for g, n in zip(cohort, sizes, strict=True) for _ in range(n)]
        n_app = round(self.n_mapped * APPLICANT_SHARE)
        for i in range(n_app):
            pool = external if (i % 2 == 0 and external) else cohort
            plan.append((rng.choice(pool), "applicant"))
        n = len(plan)
        self.group_of = np.zeros(n, dtype=np.int32)
        self.stage = np.zeros(n, dtype=np.int8)
        self.coverage = np.zeros(n, dtype=np.int8)
        self.applicant = np.zeros(n, dtype=bool)
        self.theme_ids = np.full((n, 3), -1, dtype=np.int8)
        self.theme_weights = np.zeros((n, 3), dtype=np.float64)
        self.methods = np.zeros((n, 3), dtype=np.int16)
        seen: set[str] = set()
        method_index = {m: i for i, m in enumerate(METHODS)}
        for pi, (g, kind) in enumerate(plan):
            group = self.groups[g]
            if kind is None:
                stage = _weighted_choice(rng, CAREER_STAGES, _STAGE_WEIGHTS)
                r = rng.random()
                coverage = "no_data" if r < 0.03 else ("thin" if r < 0.13 else "good")
            else:
                stage = _weighted_choice(rng, ("phd", "postdoc", "researcher"), (0.3, 0.45, 0.25))
                coverage = "thin" if rng.random() < 0.1 else "good"
            while True:
                first, last = rng.choice(nm.FIRST_NAMES), nm.invented_surname(rng)
                key = f"{nm.slug(first)}|{nm.slug(last)}"
                if key not in seen:
                    seen.add(key)
                    break
            themes = self._mixture(rng, group)
            kinds_ok = ("any", THEME_BY_ID[next(iter(themes))].kind)
            pool = [m for m in METHODS if m.kind in kinds_ok and m not in group.methods]
            methods = tuple(rng.sample(list(group.methods), 2)) + (rng.choice(pool),)
            self.group_of[pi] = g
            self.stage[pi] = CAREER_STAGES.index(stage)
            self.coverage[pi] = _COVERAGES.index(coverage)
            self.applicant[pi] = kind is not None
            for j, (tid, w) in enumerate(themes.items()):
                self.theme_ids[pi, j] = _THEME_INDEX[tid]
                self.theme_weights[pi, j] = w
            self.methods[pi] = [method_index[m] for m in methods]
            p_idhal = min(0.25 + group.french_share, 0.85)
            has_orcid = rng.random() < {"good": 0.8, "thin": 0.5, "no_data": 0.4}[coverage]
            has_openalex = rng.random() < {"good": 0.92, "thin": 0.8, "no_data": 0.6}[coverage]
            idhal = make_idhal(first, last) if rng.random() < p_idhal else ""
            ids = [("idhal", [idhal])] if idhal else []
            if has_openalex:
                ids.append(("openalex", [f"A999{pi:07d}"]))
            yield {
                "person_id": person_id(pi),
                "last_name": last,
                "first_name": first,
                "orcid": _orcid(pi) if has_orcid else None,
                "ids": ids,
                "source": "import",
                "columns": [("career_stage", stage), ("site", group.site.name)],
                "retrieved_at": _STAMP,
            }
        self._index_pools()

    def _mixture(self, rng: random.Random, group: _Group) -> dict[str, float]:
        n = _weighted_choice(rng, (1, 2, 3), (0.3, 0.5, 0.2))
        candidates: dict[str, float] = dict(group.themes)
        for tid in group.themes:
            for nb in THEME_BY_ID[tid].neighbours:
                candidates.setdefault(nb, 0.06)
        chosen: list[str] = []
        for _ in range(n):
            ids = [t for t in candidates if t not in chosen]
            chosen.append(_weighted_choice(rng, ids, [candidates[t] for t in ids]))
        values = sorted(
            (-math.log(1.0 - rng.random()) - math.log(1.0 - rng.random()) for _ in chosen),
            reverse=True,
        )
        return _normalise(dict(zip(chosen, values, strict=True)))

    def _index_pools(self) -> None:
        """Co-author pools: each group's well-covered members, and the groups of each theme."""
        good = self.coverage == 0
        order = np.argsort(self.group_of, kind="stable")
        members: dict[tuple[int, bool], list[int]] = {}
        for pi in order.tolist():
            if good[pi]:
                members.setdefault((int(self.group_of[pi]), bool(self.applicant[pi])), []).append(
                    pi
                )
        self.members = {k: np.array(v, dtype=np.int64) for k, v in members.items()}
        self.groups_of_theme: dict[tuple[str, bool], list[int]] = {}
        for (g, app), _ in sorted(self.members.items()):
            for tid in self.groups[g].themes:
                self.groups_of_theme.setdefault((tid, app), []).append(g)

    # -- one person's works ------------------------------------------------------

    def themes_of(self, pi: int) -> dict[str, float]:
        return {
            _THEME_IDS[t]: float(w)
            for t, w in zip(self.theme_ids[pi], self.theme_weights[pi], strict=True)
            if t >= 0
        }

    def term_weights(self, pi: int) -> dict[str, tuple[float, ...]]:
        rng = self.rng("person", pi)
        scores = _group_scores(self, int(self.group_of[pi]))
        return {
            tid: _zipf_weights(
                _preference(THEME_BY_ID[tid], _raw_scores(rng, THEME_BY_ID[tid]), scores.get(tid))
            )
            for tid in self.themes_of(pi)
        }

    def works_of(self, pi: int) -> list[dict]:
        """The works *pi* leads, in year order: bibliographic record, authors and text."""
        coverage = _COVERAGES[self.coverage[pi]]
        if coverage == "no_data":
            return []
        rng = self.rng("works", pi)
        text_rng = self.rng("text", pi)
        stage = CAREER_STAGES[self.stage[pi]]
        weights = self.term_weights(pi)
        n_led = rng.randint(1, 2) if coverage == "thin" else rng.randint(*_LED_WORKS[stage])
        drafts = [self._draft(pi, stage, weights, rng, text_rng) for _ in range(n_led)]
        if stage == "phd" and coverage == "good" and rng.random() < 0.6:
            drafts.append(self._draft(pi, stage, weights, rng, text_rng, thesis=True))
        return [d for _, d in sorted(enumerate(drafts), key=lambda x: (x[1]["year"], x[0]))]

    def _draft(
        self,
        pi: int,
        stage: str,
        weights: dict[str, tuple[float, ...]],
        rng: random.Random,
        text_rng: random.Random,
        *,
        thesis: bool = False,
    ) -> dict:
        group = self.groups[int(self.group_of[pi])]
        lo, hi = CAREER_WINDOWS[stage]
        if thesis:
            year, doc_type = rng.choice((NOW_YEAR - 1, NOW_YEAR)), "thesis"
        else:
            years = list(range(lo, hi + 1))
            year = _weighted_choice(rng, years, [1.0 + 0.1 * (y - lo) for y in years])
            doc_type = _weighted_choice(
                rng, [d for d, _ in _DOC_TYPE_WEIGHTS], [w for _, w in _DOC_TYPE_WEIGHTS]
            )
        p_french = group.french_share + (0.15 if doc_type in ("report", "thesis") else 0.0)
        draw = rng.random()
        if draw < p_french:
            language = "fr"
        elif draw < p_french + group.portuguese_share:
            language = "pt"
        else:
            language = "en"
        themes = self.themes_of(pi)
        theme_ids = list(themes)
        primary = _weighted_choice(rng, theme_ids, [themes[t] for t in theme_ids])
        secondary = None
        others = [t for t in theme_ids if t != primary]
        needs_topics = len(THEME_BY_ID[primary].topics) < MIN_TOPICS
        if others and (needs_topics or rng.random() < 0.35):
            secondary = _weighted_choice(rng, others, [themes[t] for t in others])
        elif needs_topics or rng.random() < 0.1:
            secondary = rng.choice(THEME_BY_ID[primary].neighbours)
        authors = [pi]
        if not thesis:
            for _ in range(_weighted_choice(rng, (0, 1, 2, 3), COAUTHOR_WEIGHTS)):
                co = self._coauthor(pi, primary, year, authors, rng)
                if co is not None:
                    authors.append(co)
        if len(authors) > 1 and rng.random() < 0.2:
            authors.remove(pi)
            authors.insert(len(authors) if stage == "senior" else 1, pi)
        has_doi = rng.random() < _DOI_PROBABILITY[doc_type]
        idhal_share = min(0.25 + group.french_share, 0.85)
        sources = []
        if (has_doi or doc_type == "preprint") and rng.random() < 0.92:
            sources.append("openalex")
        p_hal = 0.9 if (language == "fr" or doc_type in ("report", "thesis")) else 0.6
        if rng.random() < idhal_share and rng.random() < p_hal:
            sources.append("hal")
        if rng.random() < 0.55:
            sources.append("orcid")
        if not sources:
            sources.append("hal" if doc_type in ("report", "thesis") else "openalex")
        flat = (1.0,)
        plan = TextPlan(
            language=language,
            kind=THEME_BY_ID[primary].kind,
            year=year,
            primary=THEME_BY_ID[primary],
            primary_weights=weights.get(primary) or flat * len(THEME_BY_ID[primary].terms),
            secondary=THEME_BY_ID[secondary] if secondary else None,
            secondary_weights=(
                weights.get(secondary) or flat * len(THEME_BY_ID[secondary].terms)
                if secondary
                else ()
            ),
            methods=tuple(METHODS[m] for m in self.methods[pi]),
            settings=group.settings,
            drivers=DRIVERS,
            all_methods=METHODS,
            all_settings=SETTINGS,
        )
        title, abstract = compose(text_rng, plan)
        return {
            "title": title,
            "abstract": abstract,
            "year": year,
            "doc_type": doc_type,
            "language": language,
            "has_doi": has_doi,
            "sources": tuple(s for s in SOURCES if s in sources),
            "authors": tuple(authors),
        }

    def _coauthor(
        self, lead: int, theme_id: str, year: int, taken: list[int], rng: random.Random
    ) -> int | None:
        app = bool(self.applicant[lead])
        if rng.random() < 0.85:
            pool = self.members.get((int(self.group_of[lead]), app))
        else:
            groups = [
                g for g in self.groups_of_theme.get((theme_id, app), ()) if g != self.group_of[lead]
            ]
            pool = self.members.get((rng.choice(groups), app)) if groups else None
        if pool is None or not len(pool):
            return None
        candidates = [
            int(p)
            for p in pool
            if int(p) not in taken
            and CAREER_WINDOWS[CAREER_STAGES[self.stage[p]]][0]
            <= year
            <= CAREER_WINDOWS[CAREER_STAGES[self.stage[p]]][1]
        ]
        if not candidates:
            return None
        weights = [1.0 + 3.0 * self.themes_of(p).get(theme_id, 0.0) for p in candidates]
        return _weighted_choice(rng, candidates, weights)


@lru_cache(maxsize=256)
def _group_scores_cached(key: str, g: int, themes: tuple[str, ...]) -> dict:
    rng = random.Random(f"{key}/group/{g}")
    return {tid: _raw_scores(rng, THEME_BY_ID[tid]) for tid in themes}


def _group_scores(world: _World, g: int) -> dict:
    return _group_scores_cached(world.key, g, tuple(world.groups[g].themes))


def person_id(pi: int) -> str:
    """The id of person number *pi* (from 0)."""
    return f"p{pi + 1:07d}"


def text_id(n: int) -> str:
    """The id of text number *n* (from 0)."""
    return f"w{n + 1:08d}"


def _orcid(pi: int) -> str:
    body = f"00000000{pi + 1:07d}"
    full = body + orcid_check_character(body)
    return "-".join(full[i : i + 4] for i in range(0, 16, 4))


# ── writing ──────────────────────────────────────────────────────────────────


class _TableWriter:
    """One source table written a row group at a time, renamed into place when closed."""

    def __init__(self, path: Path, name: str, row_group: int) -> None:
        from cartolex.project.tables import SOURCE_SCHEMAS

        self.path = Path(path)
        self.schema = SOURCE_SCHEMAS[name]
        self.row_group = row_group
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        os.close(fd)
        self.tmp = Path(tmp)
        self.writer = pq.ParquetWriter(self.tmp, self.schema, compression="zstd")
        self.rows: list[dict] = []
        self.count = 0

    def add(self, row: dict) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.row_group:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        columns = {f.name: [r.get(f.name) for r in self.rows] for f in self.schema}
        self.writer.write_table(pa.table(columns, schema=self.schema))
        self.count += len(self.rows)
        self.rows = []

    def close(self) -> None:
        self.flush()
        self.writer.close()
        os.replace(self.tmp, self.path)

    def abort(self) -> None:
        self.writer.close()
        self.tmp.unlink(missing_ok=True)


_WORLD: list[_World] = []  # the world of a worker process (set once, by _share)


def _share(world: _World) -> None:
    _WORLD[:] = [world]


def _works_between(bounds: tuple[int, int]) -> list[list[dict]]:
    world = _WORLD[0]
    return [world.works_of(pi) for pi in range(*bounds)]


def _chunks_of_works(world: _World, workers: int, chunk: int = 500) -> Iterator[list[dict]]:
    """Each person's works, in person order (in worker processes when *workers* > 1)."""
    n = len(world.group_of)
    bounds = [(s, min(n, s + chunk)) for s in range(0, n, chunk)]
    if workers <= 1:
        for s, e in bounds:
            yield from (world.works_of(pi) for pi in range(s, e))
        return
    import multiprocessing as mp

    # Spawned workers each receive the compact world once; results come back in order.
    with mp.get_context("spawn").Pool(workers, initializer=_share, initargs=(world,)) as pool:
        for part in pool.imap(_works_between, bounds):
            yield from part


def write_scale_project(
    root: Path | str,
    people: int,
    *,
    seed: int = 0,
    languages: str | tuple[str, ...] | None = None,
    workers: int = 1,
    row_group: int = ROW_GROUP,
    progress: Callable[[str, int, int], None] | None = None,
) -> ScaleSummary:
    """Create a project in *root* (empty or missing) with a streamed world of *people* mapped people.

    The world also holds a projected set of applicants (a tenth as many). The
    source tables are written a row group of *row_group* rows at a time;
    *workers* processes compose the texts (the tables do not depend on it).
    *progress*, when given, is called with a phase name, the people done and
    the people in all. Returns what was written.
    """
    from cartolex.project import Project
    from cartolex.project.files import write_decision
    from cartolex.project.models import Overlay, Slot
    from cartolex.project.tables import SOURCE_SCHEMAS, decision_csv_bytes, write_source_table

    from .project import DOMAIN_DESCRIPTION, DOMAIN_TITLE, LEVELS, SLOT

    if people < 12:
        raise ValueError("a streamed world has at least 12 people; use generate() for less")
    langs = parse_languages(languages)
    world = _World(people, seed, langs)
    world.build_groups()
    summary = ScaleSummary(groups=len(world.groups), institutions=len(world.institutions))

    project = Project.init(
        Path(root),
        name=f"Scale world, {people} people, seed {seed}",
        domain_title=DOMAIN_TITLE,
        domain_description=DOMAIN_DESCRIPTION,
        corpus_languages=tuple(sorted(langs, key=lambda lang: (lang != "fr", lang))),
        reference_language="en",
        slots=(Slot(id=SLOT, kind="corpus", fit=True, trajectory=True),),
    )
    try:
        config = project.config.model_copy(
            update={
                "levels": list(LEVELS),
                "overlays": [Overlay(id="applicants", trajectory=False)],
            }
        )
        project.save_config(config, action="scale world levels and overlays")
        layout = project.layout

        inst_id = [f"i{i + 1:03d}" for i in range(len(world.institutions))]
        group_id = [f"g{g + 1:06d}" for g in range(len(world.groups))]
        orgs = [
            {
                "org_id": group_id[g],
                "name": grp.name,
                "acronym": grp.acronym,
                "level": "lab",
                "parents": [inst_id[grp.institution]],
                "ids": [],
                "location": {"lat": grp.lat, "lon": grp.lon},
                "source": "import",
                "retrieved_at": _STAMP,
            }
            for g, grp in enumerate(world.groups)
        ] + [
            {
                "org_id": inst_id[i],
                "name": name,
                "level": "institution",
                "parents": [],
                "ids": [],
                "source": "import",
                "retrieved_at": _STAMP,
            }
            for i, (name, _) in enumerate(world.institutions)
        ]
        schema = SOURCE_SCHEMAS
        write_source_table(
            layout.table("organisations"),
            "organisations",
            pa.table(
                {f.name: [r.get(f.name) for r in orgs] for f in schema["organisations"]},
                schema=schema["organisations"],
            ),
        )

        people_out = _TableWriter(layout.table("people"), "people", row_group)
        affiliations = _TableWriter(layout.table("affiliations"), "affiliations", row_group)
        try:
            for i, row in enumerate(world.build_people()):
                people_out.add(row)
                affiliations.add(
                    {
                        "person_id": row["person_id"],
                        "org_id": group_id[int(world.group_of[i])],
                        "source": "import",
                    }
                )
                if progress is not None and i % 10_000 == 0:
                    progress("people", i, people)
            people_out.close()
            affiliations.close()
        except BaseException:
            people_out.abort()
            affiliations.abort()
            raise
        n = len(world.group_of)
        summary.people = n
        summary.applicants = int(world.applicant.sum())
        summary.mapped = n - summary.applicants

        texts = _TableWriter(layout.table("texts"), "texts", row_group)
        parts = _TableWriter(layout.table("text_parts"), "text_parts", row_group)
        authorships = _TableWriter(layout.table("authorships"), "authorships", row_group)
        try:
            counter = 0
            for pi, works in enumerate(_chunks_of_works(world, workers)):
                for w in works:
                    tid = text_id(counter)
                    texts.add(
                        {
                            "text_id": tid,
                            "slot": SLOT,
                            "position": counter,
                            "year": w["year"],
                            "doc_type": w["doc_type"],
                            "title": w["title"],
                            "doi": f"10.5555/cartolex-demo.scale.{counter + 1}"
                            if w["has_doi"]
                            else None,
                            "ids": [],
                            "n_authors": len(w["authors"]),
                            "source": "import",
                            "retrieved_at": _STAMP,
                        }
                    )
                    for part in ("abstract", "title"):  # the key's order
                        parts.add(
                            {
                                "text_id": tid,
                                "part": part,
                                "language": w["language"],
                                "provider": "import",
                                "format": "plain",
                                "content": w[part],
                                "retrieved_at": _STAMP,
                            }
                        )
                    ranks = {a: r for r, a in enumerate(w["authors"], start=1)}
                    for a in sorted(w["authors"]):
                        authorships.add(
                            {
                                "text_id": tid,
                                "person_id": person_id(a),
                                "position": ranks[a],
                                "orgs": [group_id[int(world.group_of[a])]],
                                "last": ranks[a] == len(w["authors"]),
                            }
                        )
                    counter += 1
                    summary.authorships += len(w["authors"])
                    summary.characters += len(w["title"]) + len(w["abstract"])
                    summary.by_language[w["language"]] = (
                        summary.by_language.get(w["language"], 0) + 1
                    )
                if progress is not None and pi % 10_000 == 0:
                    progress("works", pi, n)
            texts.close()
            parts.close()
            authorships.close()
            summary.texts = counter
        except BaseException:
            texts.abort()
            parts.abort()
            authorships.abort()
            raise

        decided = _STAMP.strftime("%Y-%m-%dT%H:%M:%SZ")
        roles = [
            {
                "person_id": person_id(pi),
                "role": "projected" if world.applicant[pi] else "mapped",
                "set": "applicants" if world.applicant[pi] else "",
                "identity": "confirmed",
                "decided_at": decided,
            }
            for pi in range(n)
        ]
        write_decision(
            layout,
            layout.people_csv,
            decision_csv_bytes("people", roles),
            expected=None,
            action="scale world roles",
        )
    finally:
        project.close()
    return summary
