# SPDX-License-Identifier: MIT
"""The build's stages: what each one reads, its parameters, its cost, and the code that runs it.

A :class:`Stage` declares everything the build needs to know about one stage of
``cartolex.project.STAGE_IDS`` (see ``docs/format/derived.md``): its plain name,
the stages upstream of it, the decision files, source tables and parts of
``project.json`` it reads, whether it is opt-in, whether it reaches the network
or costs money, whether it checkpoints by chunk, its parameters, how its cost
grows with the project, and its runner. A :class:`Registry` holds the stages of
one build, in order, and checks that they fit together.

:data:`STAGES` is cartolex's registry; its runners call the engine
(:mod:`cartolex.build.engine`), imported when a stage runs. A stage declared
without a runner raises :class:`StageNotConnected`. :meth:`Registry.with_runners`
replaces runners (a test's fake stages, the AI clean-up with its key).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..project.layout import SOURCE_TABLES
from ..project.models import STAGE_IDS, ParamsFile, ProjectFile, RunRecord
from .params import (
    GLOBAL_PARAMS,
    RULES,
    SIZE_NAMES,
    CrossCheck,
    ParamSpec,
    ProjectSizes,
    theme_level_sizes,
)

if TYPE_CHECKING:
    from ..project.project import Project
    from .execution import StageContext

__all__ = [
    "STAGES",
    "CostModel",
    "Estimate",
    "Registry",
    "Stage",
    "StageNotConnected",
]

#: Runs a stage; returns counts to record (``{"candidates_en": 5214}``) or ``None``.
Runner = Callable[["StageContext"], "Mapping[str, int] | None"]


class StageNotConnected(RuntimeError):
    """The stage has no runner yet."""


def _not_connected(ctx: StageContext) -> None:
    raise StageNotConnected(
        f"the stage {ctx.stage.id} ({ctx.stage.name}) has no runner connected in this registry"
    )


# ── cost ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Estimate:
    """What a stage is expected to cost: wall time, peak memory, and what the guess rests on."""

    seconds: float | None
    peak_memory_mb: float | None
    basis: str

    def __str__(self) -> str:
        def fmt_s(s: float | None) -> str:
            if s is None:
                return "? s"
            return f"{s:.0f} s" if s < 120 else f"{s / 60:.0f} min"

        mem = "? MB" if self.peak_memory_mb is None else f"{self.peak_memory_mb:.0f} MB"
        return f"about {fmt_s(self.seconds)}, {mem} ({self.basis})"


@dataclass(frozen=True)
class CostModel:
    """How a stage's time and memory grow with one size of the project (its *driver*).

    Without a previous run, the estimate is ``fixed + per_unit × driver^exponent``,
    plus ``per_unit × size`` of an optional *extra* size (``(size, seconds per
    unit, MB per unit)``: the texts, whose number costs besides their
    characters). With a previous run, the part of its measures above the fixed
    part is scaled by what the model gives now over what it gave then, from the
    sizes recorded in its counts (the driver's ratio, to the same exponent, when
    the extra size was not recorded). A *fallback* stands in for the driver
    before it is known (the vocabulary's size before a first vocabulary is
    built, from the people): another size, the driver's units per unit of it,
    and optionally a ceiling (a vocabulary keeps at most 10 000 keywords by
    default).
    """

    driver: str
    seconds_fixed: float
    seconds_per_unit: float
    memory_mb_fixed: float
    memory_mb_per_unit: float
    time_exponent: float = 1.0
    memory_exponent: float = 1.0
    #: When the driver is not known yet: another size, how many driver units per unit,
    #: and optionally the most the driver can be.
    fallback: tuple[str, float] | tuple[str, float, float] | None = None
    #: Other sizes costing linearly: (size, seconds per unit, MB per unit), or several of
    #: them. A size may be a product of sizes, ``"texts*mapped_units"`` (points placed
    #: among the people).
    extra: tuple[str, float, float] | tuple[tuple[str, float, float], ...] | None = None

    def __post_init__(self) -> None:
        names = [n for size, _, _ in self._extras() for n in size.split("*")]
        for name in (self.driver, *(self.fallback[:1] if self.fallback else ()), *names):
            if name not in SIZE_NAMES:
                raise ValueError(f"unknown cost driver {name!r}; known: {list(SIZE_NAMES)}")

    def _extras(self) -> list[tuple[str, float, float]]:
        if self.extra is None:
            return []
        if isinstance(self.extra[0], str):
            return [self.extra]  # type: ignore[list-item]
        return list(self.extra)  # type: ignore[arg-type]

    def sizes(self) -> tuple[str, ...]:
        """The sizes the model reads (recorded in a run's counts, to scale it later)."""
        return (self.driver, *(n for size, _, _ in self._extras() for n in size.split("*")))

    def extra_size(self, get: Callable[[str], int | None]) -> tuple[float, ...] | None:
        """The extra sizes, from *get* (a size by name); ``None`` when a factor is unknown."""
        if self.extra is None:
            return None
        out = []
        for size, _, _ in self._extras():
            value = 1.0
            for name in size.split("*"):
                got = get(name)
                if got is None:
                    return None
                value *= float(got)
            out.append(value)
        return tuple(out)

    def _variable(self, driver: float, extra: tuple[float, ...] | None) -> tuple[float, float]:
        """The parts of the time and the memory that grow with the sizes."""
        s = self.seconds_per_unit * driver**self.time_exponent
        m = self.memory_mb_per_unit * driver**self.memory_exponent
        if extra is not None:
            for (_, per_s, per_m), value in zip(self._extras(), extra, strict=True):
                s += per_s * value
                m += per_m * value
        return s, m

    def estimate(self, sizes: ProjectSizes, last: RunRecord | None) -> Estimate:
        now = sizes.get(self.driver)
        if now is None and self.fallback is not None:
            other = sizes.get(self.fallback[0])
            now = round(other * self.fallback[1]) if other is not None else None
            if now is not None and len(self.fallback) > 2:
                now = min(now, round(self.fallback[2]))
        extra_now = self.extra_size(sizes.get)
        if last is not None and last.measures.seconds is not None:
            then = last.measures.counts.get(self.driver)
            s, m = last.measures.seconds, last.measures.peak_memory_mb
            if now is None or not then:
                return Estimate(s, m, "the last run")
            extra_then = self.extra_size(last.measures.counts.get)
            if self.extra is not None and extra_now is not None and extra_then and all(extra_then):
                vs_now, vm_now = self._variable(now, extra_now)
                vs_then, vm_then = self._variable(then, extra_then)
                rs = vs_now / vs_then if vs_then > 0 else 1.0
                rm = vm_now / vm_then if vm_then > 0 else 1.0
            else:
                ratio = now / then
                rs, rm = ratio**self.time_exponent, ratio**self.memory_exponent
            s = self.seconds_fixed + max(0.0, s - self.seconds_fixed) * rs
            if m is not None:
                m = self.memory_mb_fixed + max(0.0, m - self.memory_mb_fixed) * rm
            return Estimate(s, m, f"the last run, scaled by {self.driver.replace('_', ' ')}")
        if now is None:
            return Estimate(None, None, f"unknown until {self.driver.replace('_', ' ')} is known")
        vs, vm = self._variable(now, extra_now)
        return Estimate(
            self.seconds_fixed + vs, self.memory_mb_fixed + vm, "the default cost model"
        )


# ── stages ───────────────────────────────────────────────────────────────────

#: The parts of ``project.json`` a stage may declare it reads.
PROJECT_PARTS = (
    "languages",
    "slots",
    "levels",
    "overlays",
    "bases",
    "identity.domain_title",
    "identity.domain_description",
    "identity.ai",
    "identity.language_models",
)


@dataclass(frozen=True)
class Stage:
    """One stage of the build.

    ``decisions`` are decision files, relative to the project root
    (``decisions/stopwords.json``); ``sources`` are source tables by name;
    ``project`` names the parts of ``project.json`` whose values the results
    depend on (recorded in the run's ``identity``). ``uses`` lists the global
    parameters the stage reads (``seed``, ``year``); ``provides`` the sizes its
    counts report. ``applies`` returns why the stage does not apply to a project
    (it is then *skipped*), or ``None``; an opt-in stage applies only when its
    ``enabled`` parameter is true. ``extra_inputs`` lists more files it reads
    (an overlay's tables) as ``(kind, path)`` pairs. ``version`` is raised when
    cartolex deliberately changes what the stage produces. A ``bounded`` stage sizes
    its work (its worker processes, its blocks) to the job's memory budget
    (:class:`cartolex.scale.Budget`): its estimated peak is at most that budget,
    whatever its cost model gives for the project's sizes.
    """

    id: str
    name: str
    upstream: tuple[str, ...] = ()
    #: Raised when cartolex deliberately changes what this stage produces.
    version: int = 1
    decisions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    project: tuple[str, ...] = ()
    opt_in: bool = False
    network: bool = False
    paid: bool = False
    chunked: bool = False
    bounded: bool = False
    params: tuple[ParamSpec, ...] = ()
    checks: tuple[CrossCheck, ...] = ()
    uses: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    cost: CostModel | None = None
    run: Runner = _not_connected
    estimator: Callable[[Stage, ProjectSizes, RunRecord | None], Estimate] | None = None
    applies: Callable[[ProjectFile, ParamsFile], str | None] | None = None
    extra_inputs: Callable[[Project], list[Any]] | None = None
    #: Called by a build just before the stage runs (not by a dry run): it may write a
    #: decision the stage needs, such as the first map version; returns what it did.
    prepare: Callable[[Project], list[str]] | None = None
    #: For a network or paid stage: what goes out and what it costs, in words.
    consent_note: str = ""

    def __post_init__(self) -> None:
        if self.id not in STAGE_IDS:
            raise ValueError(f"unknown stage id {self.id!r}; known: {list(STAGE_IDS)}")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"{self.id}: a stage version is a whole number from 1")
        if self.opt_in and not any(p.name == "enabled" for p in self.params):
            enabled = ParamSpec("enabled", "bool", "run this opt-in stage", default=False)
            object.__setattr__(self, "params", (enabled, *self.params))
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.id}: a parameter is declared twice: {names}")
        clash = set(names) & set(GLOBAL_PARAMS)
        if clash:
            raise ValueError(f"{self.id}: {sorted(clash)} are global parameters")
        for key, allowed in (
            ("uses", GLOBAL_PARAMS),
            ("provides", SIZE_NAMES),
            ("sources", SOURCE_TABLES),
            ("project", PROJECT_PARTS),
        ):
            bad = sorted(set(getattr(self, key)) - set(allowed))
            if bad:
                raise ValueError(f"{self.id}: unknown {key} {bad}; known: {list(allowed)}")
        for path in self.decisions:
            if not path.startswith("decisions/") or ".." in path.split("/"):
                raise ValueError(f"{self.id}: {path!r} is not a file of decisions/")
        if (self.network or self.paid) and not self.consent_note:
            raise ValueError(f"{self.id}: a network or paid stage says what it sends and costs")

    @property
    def needs_consent(self) -> bool:
        """Whether the build asks before running this stage."""
        return self.network or self.paid

    def param(self, name: str) -> ParamSpec:
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(f"{self.id} has no parameter {name!r}")

    def skip_reason(self, config: ProjectFile, params: ParamsFile) -> str | None:
        """Why this stage does not apply to the project, or ``None`` when it does."""
        if self.opt_in and params.stages.get(self.id, {}).get("enabled", False) is not True:
            return f"switched off (set {self.id}.enabled in decisions/params.json to run it)"
        return self.applies(config, params) if self.applies is not None else None

    def estimate(
        self, sizes: ProjectSizes, last: RunRecord | None, memory_mb: float | None = None
    ) -> Estimate:
        """The stage's cost for *sizes* (from its last run when there is one); a
        :attr:`bounded` stage's peak is at most the job's budget *memory_mb*, or what its
        own process held in its last run, scaled like the rest, when that is more (its
        workers are sized to the budget, its own process is not)."""
        if self.estimator is not None:
            found = self.estimator(self, sizes, last)
        elif self.cost is not None:
            found = self.cost.estimate(sizes, last)
        elif last is not None and last.measures.seconds is not None:
            found = Estimate(last.measures.seconds, last.measures.peak_memory_mb, "the last run")
        else:
            found = Estimate(None, None, "no cost model")
        peak = found.peak_memory_mb
        if self.bounded and memory_mb is not None and peak is not None and peak > memory_mb:
            then = last.measures if last is not None else None
            own = None
            if then is not None and then.own_memory_mb and then.peak_memory_mb:
                own = then.own_memory_mb * peak / then.peak_memory_mb
            if own is not None and own > memory_mb:
                return Estimate(
                    found.seconds,
                    min(peak, own),
                    f"{found.basis}; its own process beyond the job's budget",
                )
            return Estimate(found.seconds, memory_mb, f"{found.basis}; at most the job's budget")
        return found


class Registry:
    """The stages of one build, in the order of ``STAGE_IDS``, checked to fit together."""

    def __init__(self, stages: Iterable[Stage]) -> None:
        self._stages: dict[str, Stage] = {}
        for stage in stages:
            if stage.id in self._stages:
                raise ValueError(f"stage {stage.id} is declared twice")
            self._stages[stage.id] = stage
        ids = list(self._stages)
        if ids != sorted(ids, key=STAGE_IDS.index):
            raise ValueError(f"stages must follow the order of STAGE_IDS: {ids}")
        for stage in self._stages.values():
            for up in stage.upstream:
                if up not in self._stages or ids.index(up) >= ids.index(stage.id):
                    raise ValueError(f"{stage.id}: upstream {up!r} is not an earlier stage")
        for stage in self._stages.values():
            before = self.upstream_of(stage.id)
            provided = {s for u in before for s in self._stages[u].provides}
            for spec in stage.params:
                if spec.rule is None:
                    continue
                missing = set(RULES[spec.rule].needs) - provided
                if missing:
                    raise ValueError(
                        f"{stage.id}.{spec.name}: the rule {spec.rule} needs {sorted(missing)}, "
                        "which no upstream stage reports"
                    )
                for sid, name in RULES[spec.rule].reads:
                    if sid not in before or name not in {p.name for p in self._stages[sid].params}:
                        raise ValueError(
                            f"{stage.id}.{spec.name}: the rule {spec.rule} reads {sid}.{name}, "
                            "which no upstream stage declares"
                        )

    def __iter__(self) -> Iterator[Stage]:
        return iter(self._stages.values())

    def __len__(self) -> int:
        return len(self._stages)

    def __contains__(self, stage_id: object) -> bool:
        return stage_id in self._stages

    def __getitem__(self, stage_id: str) -> Stage:
        try:
            return self._stages[stage_id]
        except KeyError:
            raise KeyError(f"this build has no stage {stage_id!r}") from None

    def get(self, stage_id: str) -> Stage | None:
        return self._stages.get(stage_id)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._stages)

    def upstream_of(self, stage_id: str) -> set[str]:
        """Every stage *stage_id* depends on, directly or not."""
        seen: set[str] = set()
        todo = list(self[stage_id].upstream)
        while todo:
            up = todo.pop()
            if up not in seen:
                seen.add(up)
                todo.extend(self._stages[up].upstream)
        return seen

    def downstream_of(self, stage_id: str) -> set[str]:
        """Every stage that depends on *stage_id*, directly or not."""
        return {s for s in self._stages if stage_id in self.upstream_of(s)}

    def with_runners(self, runners: Mapping[str, Runner]) -> Registry:
        """A copy of this registry with the given stages' runners replaced."""
        unknown = sorted(set(runners) - set(self._stages))
        if unknown:
            raise KeyError(f"no such stage(s) in this registry: {unknown}")
        return Registry(
            dataclasses.replace(s, run=runners[s.id]) if s.id in runners else s
            for s in self._stages.values()
        )

    def replace(self, stage_id: str, **changes: Any) -> Registry:
        """A copy of this registry with one stage's declaration changed."""
        return Registry(
            dataclasses.replace(s, **changes) if s.id == stage_id else s
            for s in self._stages.values()
        )


# ── cartolex's stages ────────────────────────────────────────────────────────


def _levels_grow(values: Mapping[str, Any], sizes: ProjectSizes, _: ProjectFile) -> str | None:
    k = sizes.kept_keywords or 0
    if values.get("level_sizes"):
        levels = tuple(values["level_sizes"])
        if levels[-1] >= k:
            return f"level_sizes ends with {levels[-1]} groups for {k} keyword(s)"
        if any(b <= a for a, b in zip(levels, levels[1:], strict=False)):
            return f"level_sizes {list(levels)} does not grow from the top"
        return None
    top, depth = values["top_groups"], values["depth"]
    if top >= k:
        return f"top_groups is {top}, but the vocabulary holds only {k} keyword(s)"
    levels = theme_level_sizes(k, depth, top, values["keywords_per_group"])
    if any(b <= a for a, b in zip(levels, levels[1:], strict=False)):
        return (
            f"with {k} keywords, top_groups {top} and keywords_per_group "
            f"{values['keywords_per_group']}, {depth} levels would not grow from the top "
            f"({' › '.join(map(str, levels))} groups); lower the depth or the group size"
        )
    return None


def _engine(name: str) -> Runner:
    """A runner of :mod:`cartolex.build.engine`, imported when the stage runs."""

    def run(ctx: StageContext) -> Mapping[str, int] | None:
        from . import engine

        return getattr(engine, name)(ctx)

    run.__name__ = run.__qualname__ = name
    return run


def _no_ai_key(ctx: StageContext) -> Mapping[str, int] | None:
    from . import engine

    return engine.triage_runner(None)(ctx)


def _prepare_themes(project: Project) -> list[str]:
    from . import engine

    return engine.prepare_themes(project)


def _prepare_maps(project: Project) -> list[str]:
    from . import engine

    return engine.prepare_maps(project)


def _min_people_fit(values: Mapping[str, Any], sizes: ProjectSizes, _: ProjectFile) -> str | None:
    if values["min_people"] > (sizes.people or 0):
        return (
            f"min_people is {values['min_people']}, but only {sizes.people} people's texts "
            "build the lexicon"
        )
    return None


def _min_texts_fit(values: Mapping[str, Any], sizes: ProjectSizes, _: ProjectFile) -> str | None:
    if values["min_texts"] > (sizes.texts or 0):
        return f"min_texts is {values['min_texts']}, but only {sizes.texts} texts build the lexicon"
    return None


def _range_grows(values: Mapping[str, Any], _: ProjectSizes, __: ProjectFile) -> str | None:
    low, high = values["ngram_range"]
    if low > high:
        return f"ngram_range {[low, high]} goes down: the fewest words come first"
    return None


def _ai_configured(_: Mapping[str, Any], __: ProjectSizes, config: ProjectFile) -> str | None:
    if config.identity.ai is None:
        return "the AI clean-up needs a provider and a model in project.json (identity.ai)"
    return None


def _has_overlays(config: ProjectFile, _: ParamsFile) -> str | None:
    return None if config.overlays else "the project has no overlay"


def _overlay_tables(project: Project) -> list[tuple[str, Path]]:
    """The tables of the projected sets kept in folders of their own.

    ``corpus.assemble`` gathers their texts and ``overlays.position`` places
    them, so both read these files; a set without a ``root`` lives in the
    project's own tables.
    """
    files: list[tuple[str, Path]] = []
    for overlay in project.config.overlays:
        if overlay.root is None:
            continue
        root = Path(overlay.root)
        root = root if root.is_absolute() else project.layout.root / root
        files.extend(("overlay", root / "tables" / f"{t}.parquet") for t in SOURCE_TABLES)
    return files


def _corpus_inputs(project: Project) -> list[Any]:
    """What ``corpus.assemble`` reads besides its declared files: the overlays' own tables,
    and the merges of ``people.csv`` when it holds some.

    The merges are an input of their own (``decisions/people.csv#merges``) so that the
    way merged people are read concerns only the projects that merge people: a project
    without a merge records none and stays up to date."""
    from ..project.identity import merges_digest
    from .fingerprints import InputFile

    found: list[Any] = list(_overlay_tables(project))
    people = project.layout.people_csv
    if merges_digest(people) is not None:
        found.append(
            InputFile("decision", "decisions/people.csv#merges", people, digest=merges_digest)
        )
    return found


#: The slot kinds a keyed parameter (``parts``, ``doc_types``) gives a value of its own.
SLOT_KINDS = ("collection", "folder", "corpus")
#: The document types the controls of ``doc_types`` offer (any other is allowed).
DOC_TYPE_SUGGESTIONS = (
    "article",
    "book",
    "chapter",
    "communication",
    "preprint",
    "proceedings",
    "report",
    "review",
    "thesis",
    "dataset",
    "software",
    "peer-review",
    "other",
)

_E, _M, _A = "essential", "intermediate", "advanced"

#: How prominent each parameter is on the screens: the one table to change (the reasons
#: are in docs/dev/params-tiers.md). Every parameter of every stage is listed once.
PARAM_TIERS: dict[str, dict[str, str]] = {
    "corpus.assemble": {
        "parts": _E,
        "recency_years": _E,
        "doc_types": _M,
        "provider_priority": _A,
        "duplicate_min_title": _A,
        "duplicate_year_gap": _A,
    },
    "keywords.extract": {
        "min_people": _E,
        "min_texts": _E,
        "counting_unit": _M,
        "max_share": _M,
        "rejects": _M,
        "max_words": _M,
        "length_bonus": _M,
        "max_candidates": _A,
        "vote": _A,
        "of_complement": _A,
        "fragment_share": _A,
        "drop_share": _A,
        "keep_share": _A,
        "name_share": _A,
        "stop_words": _A,
        "closed_word_edges": _A,
        "foreign_reading": _A,
        "even_spread": _A,
        "even_people": _A,
        "common_modifier": _A,
    },
    "keywords.triage": {"enabled": _M},
    "keywords.build": {
        "max_keywords": _E,
        "keywords_per_person": _A,
        "keywords_per_organisation": _M,
        "keywords_of_field": _M,
        "weights_basis": _M,
        "nested_threshold": _A,
        "ngram_range": _A,
    },
    "themes.space": {
        "space_unit": _E,
        "dimensions": _M,
        "svd_seed": _A,
        "svd_iterations": _A,
        "svd_algorithm": _A,
    },
    "themes.group": {
        "depth": _E,
        "level_sizes": _E,
        "comb": _E,
        "top_groups": _M,
        "keywords_per_group": _M,
        "comb_theta": _M,
        "cluster_dimensions": _A,
        "exact_ward_limit": _A,
        "micro_clusters": _A,
        "micro_seed": _A,
        "comb_grid": _A,
        "comb_theta_one_level": _A,
        "comb_sideways": _A,
        "comb_min_texts": _A,
        "comb_max_cells": _A,
        "own_name_floor": _A,
    },
    "map.layout": {"neighbours": _M, "link_radius": _A},
    "map.trajectories": {"window_years": _E, "min_texts_per_window": _M, "spans": _A},
}


def _tiered(stages: list[Stage]) -> list[Stage]:
    """*stages* with each parameter's tier from :data:`PARAM_TIERS`, which must list them all."""
    declared = {(s.id, p.name) for s in stages for p in s.params}
    listed = {(sid, name) for sid, names in PARAM_TIERS.items() for name in names}
    if declared != listed:
        missing = sorted(f"{a}.{b}" for a, b in declared - listed)
        extra = sorted(f"{a}.{b}" for a, b in listed - declared)
        raise ValueError(f"PARAM_TIERS: missing {missing}, unknown {extra}")
    return [
        dataclasses.replace(
            s,
            params=tuple(dataclasses.replace(p, tier=PARAM_TIERS[s.id][p.name]) for p in s.params),
        )
        for s in stages
    ]


#: cartolex's stages, each running the engine (``cartolex.build.engine``). The AI
#: clean-up needs a key or a client: see :func:`cartolex.build.engine.engine_registry`.
#: Cost models: fitted with tools/cost_fit.py on each stage run in a fresh process on
#: the demo worlds and on streamed worlds of 10^3 to 10^5 people (docs/sizes.md), time
#: in seconds and the stage's peak memory in MB; the layout's fixed time is mostly the
#: layout library's compilation in a new process. The AI clean-up's is a guess: it
#: depends on the provider.
STAGES = Registry(
    [
        Stage(
            "corpus.assemble",
            "gather the texts",
            decisions=(
                "decisions/people.csv",
                "decisions/organisations.csv",
                "decisions/affiliations.csv",
            ),
            sources=SOURCE_TABLES,
            project=("languages", "slots", "levels", "overlays"),
            # version 2: projected sets kept in folders of their own are gathered too;
            # version 3: people's attributes in the index, the parts and the document types
            # by slot kind; version 4: one text per work (duplicate texts read once);
            # version 5: the packed corpus (pairs.parquet, people.csv, texts.parquet)
            version=5,
            extra_inputs=_corpus_inputs,
            params=(
                ParamSpec(
                    "parts",
                    "list",
                    "the parts of a text that feed the lexicon (by default, by the kind of its "
                    "slot: a folder's or a corpus's documents are read whole)",
                    rule="parts_by_slot_kind",
                    choices=("title", "abstract", "body", "full"),
                    minimum=1,
                    keys=SLOT_KINDS,
                ),
                ParamSpec(
                    "doc_types",
                    "list",
                    "the document types read, for every slot without doc_types of its own (by "
                    "default, by the kind of the slot: a collection reads texts, not datasets, "
                    "software or peer reviews; empty: every type)",
                    rule="doc_types_by_slot_kind",
                    minimum=1,
                    nullable=True,
                    keys=SLOT_KINDS,
                    suggestions=DOC_TYPE_SUGGESTIONS,
                ),
                ParamSpec(
                    "provider_priority",
                    "list",
                    "which provider's words win when several give the same part, in order; "
                    "providers not listed come after, alphabetically",
                    default=[
                        "folder",
                        "openalex",
                        "hal",
                        "scielo",
                        "europepmc",
                        "arxiv",
                        "biorxiv",
                    ],
                    widget="order",
                ),
                ParamSpec(
                    "recency_years",
                    "int",
                    "only texts of the last N years build the keywords (0: every year)",
                    default=5,
                    minimum=0,
                    maximum=200,
                ),
                ParamSpec(
                    "duplicate_min_title",
                    "int",
                    "two texts can be one work (read once) only if their titles, compared "
                    "without case, accents or punctuation, have at least this many characters",
                    default=25,
                    minimum=1,
                    maximum=1000,
                    section="same_work",
                ),
                ParamSpec(
                    "duplicate_year_gap",
                    "int",
                    "two texts with the same title and a common author are one work when their "
                    "years are at most this far apart",
                    default=1,
                    minimum=0,
                    maximum=50,
                    section="same_work",
                ),
            ),
            uses=("year",),
            provides=("people", "texts", "characters", "mapped_units"),
            # Fitted, as every model here, on synthetic worlds of 39 to 97,000 people
            # (tests/test_build_costs.py); on a national sample of 611,000 texts it gives 0.95
            # of the time and 1.38 of the memory measured.
            cost=CostModel(
                "texts",
                0.272,
                1.023e-4,
                148.3,
                1.363,
                time_exponent=0.96,
                memory_exponent=0.53,
            ),
            run=_engine("run_corpus"),
        ),
        Stage(
            "keywords.extract",
            "find keyword candidates",
            upstream=("corpus.assemble",),
            # version 2: the lexicon lab's defaults (no English "of" complement,
            # simpler bands, no common-modifier rule); version 3: an elided word
            # the tokenizer leaves attached (Portuguese d'água) is a word of its own;
            # version 4: stop words, closed words of another language and evenly
            # spread single words are set aside; version 5: the rejection lists' candidates
            # go to the rejected band (cartolex's list, the machine's cache); version 6:
            # a candidate must also occur in min_texts distinct texts (3 by default)
            version=6,
            decisions=("decisions/stopwords.json",),
            project=("languages", "identity.language_models"),
            params=(
                ParamSpec(
                    "counting_unit",
                    "str",
                    "what weighs the same when keywords are scored: each person, each text, "
                    "or each organisation of the chosen level",
                    default="person",
                    choices=("person", "text", "organisation"),
                ),
                ParamSpec(
                    "min_people",
                    "int",
                    "a candidate is kept only if at least this many people use it",
                    default=3,
                    minimum=1,
                ),
                ParamSpec(
                    "min_texts",
                    "int",
                    "a candidate is kept only if it occurs in at least this many distinct "
                    "texts (a phrase of one co-authored text is one text's evidence)",
                    default=3,
                    minimum=1,
                ),
                ParamSpec(
                    "max_share",
                    "float",
                    "a candidate used by more than this share of people is too general",
                    default=0.6,
                    minimum=0.01,
                    maximum=1.0,
                ),
                ParamSpec(
                    "rejects",
                    "bool",
                    "candidates on cartolex's list of rejections, or that an AI rejected in an "
                    "earlier project on this computer, are rejected automatically",
                    default=True,
                ),
                ParamSpec(
                    "max_candidates",
                    "int",
                    "at most this many candidates per language, the most used, enter the scoring",
                    default=1_000_000,
                    minimum=1,
                ),
                ParamSpec(
                    "vote",
                    "str",
                    "how a text votes for a candidate it holds: its occurrences (frequency), "
                    "once (presence) or 1 + ln of its occurrences (sublinear)",
                    default="frequency",
                    choices=("frequency", "presence", "sublinear"),
                    section="scoring",
                ),
                ParamSpec(
                    "length_bonus",
                    "float",
                    "a candidate of L words scores (1 + α (L − 1)) times more: α, the bonus of "
                    "each word beyond the first",
                    default=2.0,
                    minimum=0.0,
                    maximum=20.0,
                    section="scoring",
                ),
                ParamSpec(
                    "max_words",
                    "int",
                    "the longest candidate, in words (prepositions and articles included)",
                    default=5,
                    minimum=1,
                    maximum=12,
                    section="scoring",
                ),
                ParamSpec(
                    "of_complement",
                    "bool",
                    "English candidates may take one « of » complement (« rate of change »)",
                    default=False,
                    section="scoring",
                ),
                ParamSpec(
                    "fragment_share",
                    "float",
                    "a candidate found this share of its occurrences inside one and the same "
                    "longer candidate is part of it, set aside (empty: never)",
                    default=1.0,
                    minimum=0.0,
                    maximum=1.0,
                    nullable=True,
                    section="bands",
                ),
                ParamSpec(
                    "drop_share",
                    "float",
                    "the least specific share of the candidates, by score, is set aside (0: none)",
                    default=0.0,
                    minimum=0.0,
                    maximum=1.0,
                    section="bands",
                ),
                ParamSpec(
                    "keep_share",
                    "float",
                    "a phrase is kept only within the best share of the candidates, by score; "
                    "the others are to check (1: every phrase is kept)",
                    default=1.0,
                    minimum=0.0,
                    maximum=1.0,
                    section="bands",
                ),
                ParamSpec(
                    "name_share",
                    "float",
                    "a candidate found this share of its occurrences inside a name of a person "
                    "or a place is set aside (when names are known)",
                    default=0.5,
                    minimum=0.0,
                    maximum=1.0,
                    section="bands",
                ),
                ParamSpec(
                    "stop_words",
                    "bool",
                    "a single word among the language's stop words, or a closed word of another "
                    "language, is set aside",
                    default=True,
                    section="closed_words",
                ),
                ParamSpec(
                    "closed_word_edges",
                    "bool",
                    "a phrase that starts or ends with a closed word of another language is set "
                    "aside (with stop_words on)",
                    default=True,
                    section="closed_words",
                ),
                ParamSpec(
                    "foreign_reading",
                    "int",
                    "a paragraph whose phrases hold this many different closed words of another "
                    "language is read as that language: those words break its phrases",
                    default=2,
                    minimum=1,
                    maximum=50,
                    section="closed_words",
                ),
                ParamSpec(
                    "even_spread",
                    "float",
                    "a single word used by at least this many times the people its occurrences "
                    "would reach if scattered at random is spread evenly: set aside (empty: never)",
                    default=0.9,
                    minimum=0.0,
                    maximum=10.0,
                    nullable=True,
                    section="generic_words",
                ),
                ParamSpec(
                    "even_people",
                    "float",
                    "the share of the people with texts in the language a single word must reach "
                    "before its spread is judged",
                    default=0.2,
                    minimum=0.0,
                    maximum=1.0,
                    section="generic_words",
                ),
                ParamSpec(
                    "common_modifier",
                    "float",
                    "a phrase whose edge adjective is used by at least this share of people is "
                    "common: to check (empty: never)",
                    default=None,
                    minimum=0.0,
                    maximum=1.0,
                    nullable=True,
                    section="generic_words",
                ),
            ),
            checks=(
                CrossCheck(
                    "no more people required than the lexicon has",
                    ("min_people",),
                    ("people",),
                    _min_people_fit,
                ),
                CrossCheck(
                    "no more texts required than the lexicon has",
                    ("min_texts",),
                    ("texts",),
                    _min_texts_fit,
                ),
            ),
            bounded=True,
            cost=CostModel(
                "texts",
                5.028,
                1.038,
                544.6,
                81.05,
                time_exponent=0.41,
                memory_exponent=0.41,
                extra=("characters", 2.523e-6, 3.203e-6),
            ),
            run=_engine("run_extract"),
        ),
        Stage(
            "keywords.triage",
            "AI clean-up",
            upstream=("keywords.extract",),
            # version 2: only the kept and to-check bands are judged; the prompt's
            # third version (process of an object, everyday words, English forms);
            # version 3: every band but the rejected one is judged, with categories
            version=3,
            decisions=("decisions/prompts/triage_typed_system.txt",),
            project=(
                "identity.domain_title",
                "identity.domain_description",
                "identity.ai",
                "languages",
            ),
            opt_in=True,
            network=True,
            paid=True,
            checks=(CrossCheck("an AI provider is set", (), (), _ai_configured),),
            consent_note=(
                "sends keyword strings, never texts or people, with the domain title and "
                "description, to the AI provider set in project.json: every candidate but "
                "those rejected automatically; it is billed by that provider, and answers "
                "already paid for are reused from cache/ai/"
            ),
            cost=CostModel("people", 10.0, 0.5, 550.0, 0.0),
            run=_no_ai_key,
        ),
        Stage(
            "keywords.build",
            "build the vocabulary",
            upstream=("keywords.extract", "keywords.triage"),
            # version 2: without the AI clean-up, the set-aside band does not
            # reach the vocabulary either (an explicit keep still wins); version 3:
            # the keywords' categories (categories.json); version 4: each person's whole row
            # of the lexicon (models/person_terms.json), and the copilot's acceptance gate
            version=4,
            decisions=("decisions/keywords.csv",),
            project=("languages",),
            params=(
                ParamSpec(
                    "max_keywords",
                    "int",
                    "the vocabulary keeps at most this many keywords, the best scored",
                    default=10_000,
                    minimum=10,
                ),
                ParamSpec(
                    "nested_threshold",
                    "float",
                    "a phrase replaces a shorter keyword it contains when it scores at least this "
                    "many times the shorter one's score",
                    default=1.3,
                    minimum=1.0,
                    maximum=100.0,
                ),
                ParamSpec(
                    "ngram_range",
                    "ints",
                    "the words of a keyword form counted in the texts, fewest and most (the most "
                    "grows to the longest form)",
                    default=[1, 4],
                    minimum=1,
                    maximum=12,
                    items=(2, 2),
                    section="attribution",
                ),
                ParamSpec(
                    "weights_basis",
                    "str",
                    "what a keyword weighs in a person's themes: its share of their words (tf) or "
                    "its length-boosted TF-IDF (tfidf); ranking and the space use TF-IDF",
                    default="tf",
                    choices=("tf", "tfidf"),
                    section="attribution",
                ),
                ParamSpec(
                    "keywords_per_person",
                    "int",
                    "each person keeps at most this many keywords in the space, the best scored; "
                    "empty: every keyword of the lexicon they use (the space and the map are "
                    "made of them)",
                    default=None,
                    nullable=True,
                    minimum=1,
                    maximum=100_000,
                    section="attribution",
                ),
                ParamSpec(
                    "keywords_per_organisation",
                    "int",
                    "each organisation keeps at most this many keywords, the best scored",
                    default=50,
                    minimum=1,
                    maximum=10_000,
                    section="attribution",
                ),
                ParamSpec(
                    "keywords_of_field",
                    "int",
                    "the whole field keeps at most this many keywords, the best scored",
                    default=200,
                    minimum=1,
                    maximum=100_000,
                    section="attribution",
                ),
            ),
            checks=(
                CrossCheck(
                    "the n-gram range does not go down",
                    ("ngram_range",),
                    (),
                    _range_grows,
                ),
            ),
            provides=("kept_keywords",),
            bounded=True,
            cost=CostModel(
                "texts",
                1.616,
                0.2947,
                114.2,
                5.534,
                time_exponent=0.45,
                memory_exponent=0.48,
                extra=("characters", 4.551e-7, 1.178e-6),
            ),
            run=_engine("run_build"),
        ),
        Stage(
            "themes.space",
            "place keywords in a common space",
            upstream=("keywords.build",),
            params=(
                ParamSpec(
                    "space_unit",
                    "str",
                    "what the space is fitted on: the people (keywords are near when the same "
                    "people use them) or the texts (near when the same texts use them)",
                    rule="space_unit_texts",
                    choices=("person", "text"),
                ),
                ParamSpec(
                    "dimensions",
                    "int",
                    "the dimensions of the space keywords and people are placed in",
                    rule="space_dimensions",
                    minimum=2,
                    maximum=1000,
                ),
                ParamSpec(
                    "svd_seed",
                    "int",
                    "the seed of the truncated SVD's random start",
                    default=42,
                    minimum=0,
                    maximum=2**32 - 1,
                    section="solver",
                ),
                ParamSpec(
                    "svd_iterations",
                    "int",
                    "the power iterations of the randomized SVD: more, closer to the exact one",
                    default=5,
                    minimum=1,
                    maximum=100,
                    section="solver",
                ),
                ParamSpec(
                    "svd_algorithm",
                    "str",
                    "the SVD's solver: randomized, or arpack (exact, slower)",
                    default="randomized",
                    choices=("randomized", "arpack"),
                    section="solver",
                ),
            ),
            bounded=True,
            cost=CostModel(
                "people", 3.983, 4.47e-4, 52.27, 48.39, time_exponent=1.04, memory_exponent=0.35
            ),
            run=_engine("run_space"),
        ),
        Stage(
            "themes.group",
            "group keywords into topics and themes",
            # version 2: the levels of the depth asked for, and the proposal tree;
            # version 3: each name in a language from a keyword with a form in it;
            # version 4: the comb (each keyword on the level its texts support), and the
            # texts × keywords it read (text_keywords.npz)
            version=4,
            upstream=("themes.space",),
            params=(
                ParamSpec(
                    "depth",
                    "int",
                    "the number of theme levels above the keywords",
                    rule="theme_depth",
                    minimum=1,
                    maximum=4,
                ),
                ParamSpec(
                    "top_groups",
                    "int",
                    "about this many groups at the top level",
                    default=15,
                    minimum=2,
                    maximum=500,
                ),
                ParamSpec(
                    "keywords_per_group",
                    "int",
                    "about this many keywords in each group of the finest level",
                    default=40,
                    minimum=2,
                    maximum=10_000,
                ),
                ParamSpec(
                    "level_sizes",
                    "ints",
                    "the number of groups at each level, from the top; when set, it replaces "
                    "depth, top_groups and keywords_per_group",
                    default=None,
                    nullable=True,
                    minimum=1,
                    items=(1, 4),
                    widget="levels",
                ),
                ParamSpec(
                    "cluster_dimensions",
                    "int",
                    "the finest groups are cut in this many leading dimensions of the space",
                    default=50,
                    minimum=2,
                    maximum=1000,
                    section="clustering",
                ),
                ParamSpec(
                    "exact_ward_limit",
                    "int",
                    "up to this many keywords, Ward's grouping is exact; above, it runs on "
                    "micro-clusters (its memory grows with the square of this number)",
                    default=15_000,
                    minimum=2,
                    maximum=1_000_000,
                    section="clustering",
                ),
                ParamSpec(
                    "micro_clusters",
                    "int",
                    "the micro-clusters Ward merges above the exact limit (empty: as many as "
                    "the limit)",
                    default=None,
                    minimum=2,
                    maximum=1_000_000,
                    nullable=True,
                    section="clustering",
                ),
                ParamSpec(
                    "micro_seed",
                    "int",
                    "the seed of the micro-clustering",
                    default=0,
                    minimum=0,
                    maximum=2**32 - 1,
                    section="clustering",
                ),
                ParamSpec(
                    "comb",
                    "bool",
                    "put each keyword on the level its texts support, and set aside the keywords "
                    "too broad for any theme",
                    default=True,
                    section="comb",
                ),
                ParamSpec(
                    "comb_theta",
                    "float",
                    "the share of a keyword's use a theme must hold to take it (above what any "
                    "keyword gives it); empty: calibrated so every level holds about as many "
                    "keywords per theme",
                    default=None,
                    minimum=0.0,
                    maximum=1.0,
                    nullable=True,
                    section="comb",
                ),
                ParamSpec(
                    "comb_grid",
                    "floats",
                    "the θ values the calibration tries",
                    rule="comb_grid_by_space",
                    minimum=0.0,
                    maximum=1.0,
                    items=(1, 100),
                    section="comb",
                ),
                ParamSpec(
                    "comb_theta_one_level",
                    "float",
                    "θ for a tree of one level, where no balance between levels can choose it",
                    rule="comb_theta_one_level_by_space",
                    minimum=0.0,
                    maximum=1.0,
                    section="comb",
                ),
                ParamSpec(
                    "comb_sideways",
                    "str",
                    "where a keyword may move on each level, to the group holding most of its "
                    "use: anywhere; within its group's parent (anywhere at the top level); or "
                    "only up its own group's ancestors",
                    rule="comb_sideways_by_space",
                    choices=("anywhere", "within_parent", "up_only"),
                    section="comb",
                ),
                ParamSpec(
                    "comb_min_texts",
                    "int",
                    "a keyword used in fewer texts keeps its group: too little evidence to move it",
                    default=5,
                    minimum=1,
                    maximum=100_000,
                    section="comb",
                ),
                ParamSpec(
                    "comb_max_cells",
                    "int",
                    "above this many keywords × finest groups the proposal is not combed (8 bytes "
                    "each in memory)",
                    default=50_000_000,
                    minimum=1,
                    section="comb",
                ),
                ParamSpec(
                    "own_name_floor",
                    "float",
                    "a combed theme is named after a keyword of its own only when at least this "
                    "share of that keyword's use falls in it rather than in its siblings",
                    default=0.5,
                    minimum=0.0,
                    maximum=1.0,
                    section="names",
                ),
            ),
            checks=(
                CrossCheck(
                    "the levels grow from the top, and there are fewer groups than keywords",
                    ("depth", "top_groups", "keywords_per_group", "level_sizes"),
                    ("kept_keywords",),
                    _levels_grow,
                ),
            ),
            cost=CostModel(
                "kept_keywords",
                1.647,
                1.789e-3,
                213.8,
                1.766,
                time_exponent=0.97,
                memory_exponent=0.72,
                fallback=("people", 12.0, 10_000),
            ),
            run=_engine("run_group"),
        ),
        Stage(
            "themes.apply",
            "apply your themes",
            version=2,  # 2: trees of any depth, and the weights of every level
            upstream=("themes.group",),
            decisions=("decisions/themes.json",),
            cost=CostModel(
                "people",
                1.925,
                2.621e-7,
                214.3,
                2.698,
                time_exponent=1.51,
                memory_exponent=0.5,
                extra=("kept_keywords", 4.032e-6, 1.052e-6),
            ),
            prepare=_prepare_themes,
            run=_engine("run_apply"),
        ),
        Stage(
            "map.layout",
            "draw the map",
            version=3,  # 2: placed by nearest people; 3: theme nodes placed on the map
            upstream=("themes.apply",),
            decisions=("decisions/maps.json",),
            project=("levels",),
            params=(
                ParamSpec(
                    "neighbours",
                    "int",
                    "a keyword, a projected person or a time window is placed from this many "
                    "nearest mapped people in the space",
                    default=8,
                    minimum=1,
                    maximum=500,
                    section="placement",
                ),
                ParamSpec(
                    "link_radius",
                    "float",
                    "those neighbours closer than this share of the map's radius form a group; "
                    "the point goes to the heaviest group",
                    default=0.25,
                    minimum=0.0,
                    maximum=10.0,
                    section="placement",
                ),
            ),
            cost=CostModel(
                "mapped_units",
                32.53,
                1.428e-3,
                554.9,
                3.632,
                time_exponent=1.02,
                memory_exponent=0.43,
            ),
            prepare=_prepare_maps,
            run=_engine("run_layout"),
        ),
        Stage(
            "map.trajectories",
            "change over time",
            # 2: placed by nearest people; 3: weights on every theme level; 4: one entry per
            # window unless spans is "all"
            version=4,
            upstream=("map.layout",),
            project=("slots",),
            params=(
                ParamSpec(
                    "window_years",
                    "int",
                    "the width of each time window, in years",
                    default=3,
                    minimum=1,
                    maximum=50,
                ),
                ParamSpec(
                    "min_texts_per_window",
                    "int",
                    "a person's time window is placed only if it holds at least this many texts",
                    default=1,
                    minimum=1,
                    maximum=1000,
                ),
                ParamSpec(
                    "spans",
                    "str",
                    "what is described for each person: each time window, or also every run "
                    "of consecutive windows (all: grows with the square of a person's windows)",
                    default="windows",
                    choices=("windows", "all"),
                ),
            ),
            uses=("year",),
            bounded=True,
            cost=CostModel(
                "texts",
                2.981,
                7.105e-3,
                34.29,
                14.03,
                time_exponent=0.79,
                memory_exponent=0.47,
                extra=("texts*mapped_units", 1.187e-8, 8.336e-8),
            ),
            run=_engine("run_trajectories"),
        ),
        Stage(
            "overlays.position",
            "place projected people",
            version=3,  # 2: placed by nearest people; 3: weights on every theme level
            upstream=("map.layout",),
            project=("overlays",),
            applies=_has_overlays,
            extra_inputs=_overlay_tables,
            cost=CostModel(
                "mapped_units",
                1.749,
                2.487e-6,
                208.6,
                0.02228,
                time_exponent=1.6,
                memory_exponent=1.01,
            ),
            run=_engine("run_overlays"),
        ),
    ]
)
STAGES = Registry(_tiered(list(STAGES)))
