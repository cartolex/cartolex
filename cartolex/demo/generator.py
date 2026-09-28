# SPDX-License-Identifier: MIT
"""Generate the demo world: groups, people, works and their texts.

The world is an invented research community working on coastal and marine
systems. Everything is drawn from ``random.Random`` instances seeded from
``(size, seed)`` and one named stream per concern, so the same size and seed
always give the same world, and a change to the text templates does not move
the people or the bibliography.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace

from . import names as nm
from .bodies import compose_body
from .identifiers import make_doi, make_idhal, make_openalex_id, make_orcid
from .model import (
    APPLICANTS,
    CAREER_STAGES,
    COHORT,
    LANGUAGE_SETS,
    LANGUAGES,
    NOW_YEAR,
    SIZES,
    SOURCES,
    DemoWorld,
    Group,
    Person,
    SizeSpec,
    Work,
)
from .texts import MIN_TOPICS, TextPlan, compose
from .vocabulary import DRIVERS, METHODS, SETTINGS, THEME_BY_ID, THEMES, Theme

# Themes in an order that spreads the first groups across the whole field.
_SPREAD_ORDER = (
    "coastal-geomorphology",
    "marine-ecology",
    "coastal-governance",
    "ocean-circulation",
    "marine-pollution",
    "paleoceanography",
    "fisheries-aquaculture",
    "ocean-observation",
    "estuaries-wetlands",
    "plankton-biogeochemistry",
    "coastal-hazards",
    "blue-economy",
)

_STAGE_WEIGHTS = (0.2, 0.15, 0.4, 0.25)
_LED_WORKS = {"phd": (1, 4), "postdoc": (3, 7), "researcher": (4, 11), "senior": (6, 13)}
_DOC_TYPE_WEIGHTS = (("article", 0.62), ("proceedings", 0.14), ("preprint", 0.13), ("report", 0.11))
_DOI_PROBABILITY = {
    "article": 0.95,
    "proceedings": 0.6,
    "preprint": 0.85,
    "report": 0.2,
    "thesis": 0.3,
}
# A person's preference over the terms of a theme: Zipf weights over a ranking
# that mixes the group's ranking (share GROUP_SHARE) with the person's own.
ZIPF = 0.7
GROUP_SHARE = 0.4
# Share of a compound term's score that comes from its object.
FOCUS_SHARE = 0.75
# Share of cohort groups that write often in French.
FRENCH_GROUPS = 0.22
# In a trilingual world, share of cohort groups that write often in Portuguese
# (chosen among those that do not write often in French).
PORTUGUESE_GROUPS = 0.22
# Probability of 0, 1, 2 or 3 co-authors from the cohort on a work.
COAUTHOR_WEIGHTS = (0.18, 0.34, 0.3, 0.18)


@dataclass(frozen=True)
class Institution:
    """An invented institution at a fictional site."""

    name: str
    site: nm.Site


def _rng(size: str, seed: int, stream: str) -> random.Random:
    return random.Random(f"cartolex-demo/{size}/{seed}/{stream}")


def _weighted_choice(rng: random.Random, items: Sequence, weights: Sequence[float]):
    return rng.choices(list(items), weights=list(weights), k=1)[0]


def _split(total: int, parts: int, rng: random.Random, minimum: int) -> list[int]:
    """Split *total* into *parts* uneven integers, each at least *minimum*."""
    raw = [rng.uniform(0.6, 1.4) for _ in range(parts)]
    spare = total - minimum * parts
    shares = [spare * r / sum(raw) for r in raw]
    counts = [minimum + int(s) for s in shares]
    order = sorted(range(parts), key=lambda i: -(shares[i] - int(shares[i])))
    for i in order[: total - sum(counts)]:
        counts[i] += 1
    return counts


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    """Round to three decimals, heaviest first, summing to exactly 1."""
    total = sum(weights.values())
    items = sorted(weights.items(), key=lambda kv: -kv[1])
    rounded = {k: round(v / total, 3) for k, v in items}
    first = items[0][0]
    rounded[first] = round(rounded[first] + 1.0 - sum(rounded.values()), 3)
    return rounded


def _raw_scores(rng: random.Random, theme: Theme) -> tuple[list[float], dict[str, float]]:
    """Random scores for a theme's terms and for the objects its compound terms are about."""
    terms = [rng.random() for _ in theme.terms]
    foci = sorted({t.focus for t in theme.terms if t.focus})
    return terms, {f: rng.random() for f in foci}


def _preference(
    theme: Theme,
    own: tuple[list[float], dict[str, float]],
    group: tuple[list[float], dict[str, float]] | None,
) -> list[float]:
    """Rank scores for a theme's terms: a person's own taste mixed with the group's.

    Specialists are object-centred: a compound term ("hake biomass") mostly
    scores as its object ("hake") does, so a person's favourite terms are
    several aspects of a few favourite objects, not one aspect of many.
    """

    def mix(g: float, o: float) -> float:
        return GROUP_SHARE * g + (1.0 - GROUP_SHARE) * o

    own_terms, own_foci = own
    if group is None:
        terms, foci = own_terms, own_foci
    else:
        terms = [mix(g, o) for g, o in zip(group[0], own_terms, strict=True)]
        foci = {f: mix(group[1][f], own_foci[f]) for f in own_foci}
    return [
        FOCUS_SHARE * foci[t.focus] + (1.0 - FOCUS_SHARE) * s if t.focus else s
        for t, s in zip(theme.terms, terms, strict=True)
    ]


def _zipf_weights(scores: Sequence[float]) -> tuple[float, ...]:
    """Zipf weights by rank of *scores* (highest score, highest weight)."""
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    weights = [0.0] * len(scores)
    for rank, i in enumerate(order):
        weights[i] = round(1.0 / (rank + 1) ** ZIPF, 6)
    return tuple(weights)


class _Builder:
    """Holds the random streams and the partial world while it is generated."""

    def __init__(
        self,
        spec: SizeSpec,
        seed: int,
        languages: tuple[str, ...] = LANGUAGES,
        bodies: bool = False,
    ) -> None:
        self.spec = spec
        self.seed = seed
        self.languages = languages
        self.bodies = bodies
        self.rng_struct = _rng(spec.code, seed, "structure")
        self.rng_people = _rng(spec.code, seed, "people")
        self.rng_works = _rng(spec.code, seed, "works")
        self.rng_text = _rng(spec.code, seed, "text")
        self.groups: list[Group] = []
        self.group_scores: dict[str, dict[str, tuple[list[float], dict[str, float]]]] = {}
        self.people: list[Person] = []
        self._name_keys: set[str] = set()
        self._orcids: set[str] = set()
        self._openalex: set[str] = set()
        self._n_drafts = 0

    # -- structure -----------------------------------------------------------

    def build_structure(self) -> None:
        rng = self.rng_struct
        spec = self.spec
        sites = list(nm.SITES)
        rng.shuffle(sites)
        cohort_sites = sites[: spec.sites]
        extra_sites = sites[spec.sites :] or sites
        per_site = _split(spec.institutions, spec.sites, rng, minimum=1)
        institutions: list[Institution] = []
        for site, n in zip(cohort_sites, per_site, strict=True):
            patterns = list(nm.INSTITUTION_PATTERNS)
            rng.shuffle(patterns)
            institutions += [Institution(p.format(site=site.name), site) for p in patterns[:n]]
        per_inst = _split(spec.groups, len(institutions), rng, minimum=1)
        order = list(_SPREAD_ORDER)
        shift = rng.randrange(len(order))
        order = order[shift:] + order[:shift]
        slots = [inst for inst, n in zip(institutions, per_inst, strict=True) for _ in range(n)]
        # About one group in five writes often in French; always at least one.
        n_french = max(1, math.ceil(FRENCH_GROUPS * len(slots)))
        french_heavy = set(rng.sample(range(len(slots)), n_french))
        for i, inst in enumerate(slots):
            primary = THEME_BY_ID[order[i % len(order)]]
            self._add_group(primary, inst, external=False, french_heavy=i in french_heavy)
        for j in range(spec.external_groups):
            site = extra_sites[j % len(extra_sites)]
            pattern = rng.choice(nm.INSTITUTION_PATTERNS)
            inst = Institution(pattern.format(site=site.name), site)
            primary = THEME_BY_ID[rng.choice(_SPREAD_ORDER)]
            self._add_group(primary, inst, external=True, french_heavy=rng.random() < 0.3)

    def _add_group(
        self, primary: Theme, inst: Institution, *, external: bool, french_heavy: bool
    ) -> None:
        rng = self.rng_struct
        n_secondary = rng.choice((1, 1, 2))
        neighbours = list(primary.neighbours)
        secondaries = []
        for _ in range(n_secondary):
            weights = [1.0 / (k + 1) for k in range(len(neighbours))]
            pick = _weighted_choice(rng, neighbours, weights)
            neighbours.remove(pick)
            secondaries.append(pick)
        raw = {primary.id: rng.uniform(0.55, 0.7)}
        rest = 1.0 - raw[primary.id]
        split = [rng.uniform(0.5, 1.5) for _ in secondaries]
        for theme_id, s in zip(secondaries, split, strict=True):
            raw[theme_id] = rest * s / sum(split)
        themes = _normalise(raw)

        used_names = {g.name for g in self.groups}
        used_acronyms = {g.acronym for g in self.groups}
        stems = list(primary.group_stems)
        rng.shuffle(stems)
        kinds = list(nm.GROUP_KINDS)
        rng.shuffle(kinds)
        candidates = [(s, k) for s in stems for k in kinds]
        stem, (kind, letter) = next(
            ((s, k) for s, k in candidates if f"{s} {k[0]}" not in used_names), candidates[0]
        )
        name = f"{stem} {kind}"
        if name in used_names:
            name = f"{name} of {inst.site.name}"
        initials = "".join(w[0].upper() for w in stem.replace("-", " ").split() if w != "and")
        acronym = f"{initials}{letter}-{inst.site.code}"
        n = 2
        while acronym in used_acronyms:
            acronym = f"{initials}{letter}{n}-{inst.site.code}"
            n += 1

        # Most groups write mostly in English; a French-heavy group often in French.
        french = rng.uniform(0.28, 0.42) if french_heavy else rng.uniform(0.05, 0.15)
        if primary.kind == "social":
            french += 0.06
        kinds_ok = ("any", primary.kind)
        methods = [m for m in METHODS if m.kind in kinds_ok]
        settings = [s for s in SETTINGS if primary.kind == "natural" or s.social]
        group_id = f"g{len(self.groups) + 1:03d}"
        self.group_scores[group_id] = {tid: _raw_scores(rng, THEME_BY_ID[tid]) for tid in themes}
        self.groups.append(
            Group(
                group_id=group_id,
                acronym=acronym,
                name=name,
                institution=inst.name,
                site=inst.site.name,
                lat=round(inst.site.lat + rng.uniform(-0.04, 0.04), 4),
                lon=round(inst.site.lon + rng.uniform(-0.04, 0.04), 4),
                themes=themes,
                french_share=round(min(french, 0.7), 3),
                settings=tuple(rng.sample(settings, 3)),
                methods=tuple(rng.sample(methods, 4)),
                external=external,
            )
        )

    def assign_portuguese(self) -> None:
        """Give each group its share of works in Portuguese (trilingual worlds only).

        Drawn from a stream of its own, after the structure: the groups, people
        and bibliography are those of the world in the default languages.
        """
        if "pt" not in self.languages:
            return
        rng = _rng(self.spec.code, self.seed, "languages")
        cohort = [i for i, g in enumerate(self.groups) if not g.external]
        candidates = [i for i in cohort if self.groups[i].french_share < 0.2] or cohort
        n_heavy = min(len(candidates), max(1, math.ceil(PORTUGUESE_GROUPS * len(cohort))))
        heavy = set(rng.sample(candidates, n_heavy))
        for i, group in enumerate(self.groups):
            share = rng.uniform(0.3, 0.45) if i in heavy else rng.uniform(0.03, 0.08)
            if THEME_BY_ID[next(iter(group.themes))].kind == "social":
                share += 0.04
            self.groups[i] = replace(group, portuguese_share=round(share, 3))

    # -- people --------------------------------------------------------------

    def build_people(self) -> None:
        spec = self.spec
        rng = self.rng_people
        cohort_groups = [g for g in self.groups if not g.external]
        external_groups = [g for g in self.groups if g.external]
        sizes = _split(spec.people, len(cohort_groups), rng, minimum=2)
        for group, n in zip(cohort_groups, sizes, strict=True):
            for _ in range(n):
                stage = _weighted_choice(rng, CAREER_STAGES, _STAGE_WEIGHTS)
                r = rng.random()
                coverage = "no_data" if r < 0.03 else ("thin" if r < 0.13 else "good")
                self._add_person(group, stage, coverage, COHORT)
        self._ensure_coverage_variety()
        # The projected set: half from outside groups, half from cohort groups.
        for i in range(spec.applicants):
            pool = external_groups if (i % 2 == 0 and external_groups) else cohort_groups
            group = rng.choice(pool)
            stage = _weighted_choice(rng, ("phd", "postdoc", "researcher"), (0.3, 0.45, 0.25))
            coverage = "thin" if rng.random() < 0.1 else "good"
            self._add_person(group, stage, coverage, APPLICANTS)

    def _ensure_coverage_variety(self) -> None:
        """Every world has at least one person without works and one thin person."""
        rng = self.rng_people
        for wanted in ("no_data", "thin"):
            if any(p.coverage == wanted for p in self.people):
                continue
            candidates = [
                i for i, p in enumerate(self.people) if p.coverage == "good" and p.role == COHORT
            ]
            i = rng.choice(candidates)
            old = self.people[i]
            self.people[i] = replace(old, coverage=wanted)

    def _unique_name(self) -> tuple[str, str]:
        rng = self.rng_people
        while True:
            first = rng.choice(nm.FIRST_NAMES)
            last = nm.invented_surname(rng)
            key = f"{nm.slug(first)}|{nm.slug(last)}"
            if key not in self._name_keys:
                self._name_keys.add(key)
                return first, last

    def _add_person(self, group: Group, stage: str, coverage: str, role: str) -> None:
        rng = self.rng_people
        first, last = self._unique_name()
        p_orcid = {"good": 0.8, "thin": 0.5, "no_data": 0.4}[coverage]
        p_openalex = {"good": 0.92, "thin": 0.8, "no_data": 0.6}[coverage]
        p_idhal = min(0.25 + group.french_share, 0.85)
        orcid = ""
        if rng.random() < p_orcid:
            orcid = make_orcid(rng)
            while orcid in self._orcids:
                orcid = make_orcid(rng)
            self._orcids.add(orcid)
        openalex = ""
        if rng.random() < p_openalex:
            openalex = make_openalex_id(rng)
            while openalex in self._openalex:
                openalex = make_openalex_id(rng)
            self._openalex.add(openalex)
        idhal = make_idhal(first, last) if rng.random() < p_idhal else ""

        themes = self._mixture(group)
        kinds_ok = ("any", THEME_BY_ID[next(iter(themes))].kind)
        pool = [m for m in METHODS if m.kind in kinds_ok and m not in group.methods]
        methods = tuple(rng.sample(list(group.methods), 2)) + (rng.choice(pool),)
        group_scores = self.group_scores[group.group_id]
        term_weights = {}
        for tid in themes:
            theme = THEME_BY_ID[tid]
            own = _raw_scores(rng, theme)
            term_weights[tid] = _zipf_weights(_preference(theme, own, group_scores.get(tid)))

        self.people.append(
            Person(
                person_id=f"p{len(self.people) + 1:04d}",
                last_name=last,
                first_name=first,
                group=group.group_id,
                institution=group.institution,
                site=group.site,
                career_stage=stage,
                orcid=orcid,
                openalex_id=openalex,
                idhal=idhal,
                role=role,
                coverage=coverage,
                themes=themes,
                methods=methods,
                term_weights=term_weights,
            )
        )

    def _mixture(self, group: Group) -> dict[str, float]:
        """1 to 3 dominant themes, mostly from the group's profile."""
        rng = self.rng_people
        n = _weighted_choice(rng, (1, 2, 3), (0.3, 0.5, 0.2))
        candidates: dict[str, float] = dict(group.themes)
        for tid in group.themes:
            for nb in THEME_BY_ID[tid].neighbours:
                candidates.setdefault(nb, 0.06)
        chosen: list[str] = []
        for _ in range(n):
            ids = [t for t in candidates if t not in chosen]
            chosen.append(_weighted_choice(rng, ids, [candidates[t] for t in ids]))
        # Gamma(2, 1) draws as sums of two exponentials, from random() alone
        # (so the stream does not depend on a library routine's internals).
        values = sorted(
            (-math.log(1.0 - rng.random()) - math.log(1.0 - rng.random()) for _ in chosen),
            reverse=True,
        )
        return _normalise(dict(zip(chosen, values, strict=True)))

    # -- works ---------------------------------------------------------------

    def build_works(self) -> list[Work]:
        drafts: list[dict] = []
        by_group: dict[str, list[Person]] = {}
        for p in self.people:
            if p.coverage == "good":
                by_group.setdefault(p.group, []).append(p)
        groups = {g.group_id: g for g in self.groups}
        for person in self.people:
            if person.coverage == "no_data":
                continue
            group = groups[person.group]
            if person.coverage == "thin":
                n_led = self.rng_works.randint(1, 2)
            else:
                lo, hi = _LED_WORKS[person.career_stage]
                n_led = self.rng_works.randint(lo, hi)
            for _ in range(n_led):
                drafts.append(self._draft(person, group, by_group))
            if (
                person.career_stage == "phd"
                and person.coverage == "good"
                and self.rng_works.random() < 0.6
            ):
                drafts.append(self._draft(person, group, by_group, thesis=True))
        # Chronological identifiers.
        drafts.sort(key=lambda d: (d["year"], d["authors"][0], d["seq"]))
        works = []
        writers = []
        for n, d in enumerate(drafts, start=1):
            doi = make_doi(self.spec.code, n) if d["has_doi"] else ""
            writers.append(d.pop("_writer"))
            del d["seq"], d["has_doi"]
            works.append(Work(work_id=f"w{n:05d}", doi=doi, **d))
        if self.bodies:
            # A stream of its own, after every work is made: nothing else moves.
            rng = _rng(self.spec.code, self.seed, "bodies")
            works = [
                replace(w, body=compose_body(rng, plan, filler))
                for w, (plan, filler) in zip(works, writers, strict=True)
            ]
        return works

    def _draft(
        self, lead: Person, group: Group, by_group: dict[str, list[Person]], *, thesis: bool = False
    ) -> dict:
        rng = self.rng_works
        lo, hi = lead.window
        if thesis:
            year = rng.choice((NOW_YEAR - 1, NOW_YEAR))
            doc_type = "thesis"
        else:
            years = list(range(lo, hi + 1))
            year = _weighted_choice(rng, years, [1.0 + 0.1 * (y - lo) for y in years])
            doc_type = _weighted_choice(
                rng, [d for d, _ in _DOC_TYPE_WEIGHTS], [w for _, w in _DOC_TYPE_WEIGHTS]
            )
        p_french = group.french_share + (0.15 if doc_type in ("report", "thesis") else 0.0)
        # One draw whatever the languages: a trilingual world keeps the works of
        # the default one and only writes some English ones in Portuguese.
        draw = rng.random()
        if draw < p_french:
            language = "fr"
        elif draw < p_french + group.portuguese_share:
            language = "pt"
        else:
            language = "en"

        theme_ids = list(lead.themes)
        primary = _weighted_choice(rng, theme_ids, [lead.themes[t] for t in theme_ids])
        secondary = None
        others = [t for t in theme_ids if t != primary]
        # A theme made mostly of techniques studies the topics of another one.
        needs_topics = len(THEME_BY_ID[primary].topics) < MIN_TOPICS
        if others and (needs_topics or rng.random() < 0.35):
            secondary = _weighted_choice(rng, others, [lead.themes[t] for t in others])
        elif needs_topics or rng.random() < 0.1:
            secondary = rng.choice(THEME_BY_ID[primary].neighbours)

        authors = [lead]
        if not thesis:
            n_co = _weighted_choice(rng, (0, 1, 2, 3), COAUTHOR_WEIGHTS)
            for _ in range(n_co):
                co = self._coauthor(lead, primary, year, authors, by_group)
                if co is not None:
                    authors.append(co)
        ids = [a.person_id for a in authors]
        if len(ids) > 1 and rng.random() < 0.2:
            # The lead signs last or second, as senior or shared authorship.
            ids.remove(lead.person_id)
            ids.insert(len(ids) if lead.career_stage == "senior" else 1, lead.person_id)

        has_doi = rng.random() < _DOI_PROBABILITY[doc_type]
        sources = []
        if (has_doi or doc_type == "preprint") and rng.random() < 0.92:
            sources.append("openalex")
        p_hal = 0.9 if (language == "fr" or doc_type in ("report", "thesis")) else 0.6
        if any(a.idhal for a in authors) and rng.random() < p_hal:
            sources.append("hal")
        if any(a.orcid for a in authors) and rng.random() < 0.55:
            sources.append("orcid")
        if not sources:
            sources.append("hal" if doc_type in ("report", "thesis") else "openalex")

        venue = self._venue(doc_type, language, group)

        plan = TextPlan(
            language=language,
            kind=THEME_BY_ID[primary].kind,
            year=year,
            primary=THEME_BY_ID[primary],
            primary_weights=self._weights(primary, authors),
            secondary=THEME_BY_ID[secondary] if secondary else None,
            secondary_weights=self._weights(secondary, authors) if secondary else (),
            methods=lead.methods,
            settings=group.settings,
            drivers=DRIVERS,
            all_methods=METHODS,
            all_settings=SETTINGS,
        )
        kept: list = []
        title, abstract = compose(self.rng_text, plan, keep=kept)
        self._n_drafts += 1
        themes = (primary,) if secondary is None else (primary, secondary)
        return {
            "title": title,
            "abstract": abstract,
            "year": year,
            "doc_type": doc_type,
            "language": language,
            "venue": venue,
            "sources": tuple(s for s in SOURCES if s in sources),
            "themes": themes,
            "authors": tuple(ids),
            "has_doi": has_doi,
            "seq": self._n_drafts,
            "_writer": (plan, kept[0]),
        }

    def _weights(self, theme_id: str, authors: list[Person]) -> tuple[float, ...]:
        """Preference over a theme's terms: the first author who works on it, else flat."""
        for a in authors:
            if theme_id in a.term_weights:
                return a.term_weights[theme_id]
        return tuple(1.0 for _ in THEME_BY_ID[theme_id].terms)

    def _coauthor(
        self,
        lead: Person,
        theme_id: str,
        year: int,
        taken: list[Person],
        by_group: dict[str, list[Person]],
    ) -> Person | None:
        rng = self.rng_works
        same_role = [p for g in by_group.values() for p in g if p.role == lead.role]
        if rng.random() < 0.85:
            pool = [p for p in by_group.get(lead.group, []) if p.role == lead.role]
        else:
            pool = [p for p in same_role if p.group != lead.group and theme_id in p.themes]
        pool = [p for p in pool if p not in taken and p.window[0] <= year <= p.window[1]]
        if not pool:
            return None
        weights = [1.0 + 3.0 * p.themes.get(theme_id, 0.0) for p in pool]
        return _weighted_choice(rng, pool, weights)

    def _venue(self, doc_type: str, language: str, group: Group) -> str:
        # Portuguese venues draw exactly as English ones do (lists of equal length).
        rng = self.rng_works
        if doc_type == "article":
            if language == "fr" and rng.random() < 0.7:
                return rng.choice(nm.VENUES_FR)
            return rng.choice(nm.VENUES_PT if language == "pt" else nm.VENUES_EN)
        if doc_type == "proceedings":
            lists = {"fr": nm.PROCEEDINGS_FR, "pt": nm.PROCEEDINGS_PT}
            return rng.choice(lists.get(language, nm.PROCEEDINGS_EN))
        if doc_type == "preprint":
            return nm.PREPRINT_SERVER
        if doc_type == "report":
            prefix = {"fr": "Rapport technique", "pt": "Relatório técnico"}.get(
                language, "Technical report"
            )
            return f"{prefix}, {group.institution}"
        prefix = {"fr": "Thèse de doctorat", "pt": "Tese de doutorado"}.get(
            language, "Doctoral thesis"
        )
        return f"{prefix}, {group.institution}"


def parse_languages(languages: str | tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    """A supported language set from ``"en,fr,pt"`` or a sequence (``None``: the default)."""
    if languages is None:
        return LANGUAGES
    items = languages.split(",") if isinstance(languages, str) else list(languages)
    wanted = tuple(dict.fromkeys(str(x).strip().lower() for x in items if str(x).strip()))
    for option in LANGUAGE_SETS:
        if set(wanted) == set(option):
            return option
    listed = "; ".join(",".join(o) for o in LANGUAGE_SETS)
    raise ValueError(f"unsupported languages {','.join(wanted)!r}; expected one of: {listed}")


def generate(
    size: str = "S",
    seed: int = 0,
    languages: str | tuple[str, ...] | None = None,
    *,
    bodies: bool = False,
) -> DemoWorld:
    """Generate the demo world of the given *size* (``XS``, ``S`` or ``L``) and *seed*.

    *languages* is ``en,fr`` (the default) or ``en,fr,pt``: a trilingual world
    has the same groups, people and bibliography as the default one; some
    groups write often in Portuguese, so some English works become Portuguese
    works, and every text is written anew.

    With *bodies*, every work also gets a body (``Work.body``, see
    :mod:`cartolex.demo.bodies`): long, repetitive, with generic filler, written
    from a stream of its own, so the rest of the world is unchanged.
    """
    key = size.upper()
    if key not in SIZES:
        raise ValueError(f"unknown size {size!r}; expected one of {', '.join(SIZES)}")
    langs = parse_languages(languages)
    builder = _Builder(SIZES[key], int(seed), langs, bool(bodies))
    builder.build_structure()
    builder.assign_portuguese()
    builder.build_people()
    works = builder.build_works()
    return DemoWorld(
        size=key,
        seed=int(seed),
        groups=tuple(builder.groups),
        people=tuple(builder.people),
        works=tuple(works),
        themes=THEMES,
        languages=langs,
        bodies=bool(bodies),
    )
