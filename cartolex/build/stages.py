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

    Without a previous run, the estimate is ``fixed + per_unit × driver^exponent``.
    With one, the previous run's measures are scaled by the ratio of the driver
    now to the driver then (recorded in its counts), to the same exponent.
    """

    driver: str
    seconds_fixed: float
    seconds_per_unit: float
    memory_mb_fixed: float
    memory_mb_per_unit: float
    time_exponent: float = 1.0
    memory_exponent: float = 1.0

    def __post_init__(self) -> None:
        if self.driver not in SIZE_NAMES:
            raise ValueError(f"unknown cost driver {self.driver!r}; known: {list(SIZE_NAMES)}")

    def estimate(self, sizes: ProjectSizes, last: RunRecord | None) -> Estimate:
        now = sizes.get(self.driver)
        if last is not None and last.measures.seconds is not None:
            then = last.measures.counts.get(self.driver)
            s, m = last.measures.seconds, last.measures.peak_memory_mb
            if now is None or not then:
                return Estimate(s, m, "the last run")
            ratio = now / then
            s = self.seconds_fixed + max(0.0, s - self.seconds_fixed) * ratio**self.time_exponent
            if m is not None:
                m = self.memory_mb_fixed + max(0.0, m - self.memory_mb_fixed) * (
                    ratio**self.memory_exponent
                )
            return Estimate(s, m, f"the last run, scaled by {self.driver.replace('_', ' ')}")
        if now is None:
            return Estimate(None, None, f"unknown until {self.driver.replace('_', ' ')} is known")
        return Estimate(
            self.seconds_fixed + self.seconds_per_unit * now**self.time_exponent,
            self.memory_mb_fixed + self.memory_mb_per_unit * now**self.memory_exponent,
            "the default cost model",
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
    cartolex deliberately changes what the stage produces.
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
    params: tuple[ParamSpec, ...] = ()
    checks: tuple[CrossCheck, ...] = ()
    uses: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    cost: CostModel | None = None
    run: Runner = _not_connected
    estimator: Callable[[Stage, ProjectSizes, RunRecord | None], Estimate] | None = None
    applies: Callable[[ProjectFile, ParamsFile], str | None] | None = None
    extra_inputs: Callable[[Project], list[tuple[str, Path]]] | None = None
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

    def estimate(self, sizes: ProjectSizes, last: RunRecord | None) -> Estimate:
        if self.estimator is not None:
            return self.estimator(self, sizes, last)
        if self.cost is not None:
            return self.cost.estimate(sizes, last)
        if last is not None and last.measures.seconds is not None:
            return Estimate(last.measures.seconds, last.measures.peak_memory_mb, "the last run")
        return Estimate(None, None, "no cost model")


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


def _person_counting(values: Mapping[str, Any], _: ProjectSizes, __: ProjectFile) -> str | None:
    if values["counting_unit"] != "person":
        return "counting_unit 'text' is not available yet: keywords are counted per person"
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


def _ai_configured(_: Mapping[str, Any], __: ProjectSizes, config: ProjectFile) -> str | None:
    if config.identity.ai is None:
        return "the AI clean-up needs a provider and a model in project.json (identity.ai)"
    return None


def _has_overlays(config: ProjectFile, _: ParamsFile) -> str | None:
    return None if config.overlays else "the project has no overlay"


def _overlay_tables(project: Project) -> list[tuple[str, Path]]:
    """The tables of the projected sets kept in folders of their own.

    A set without a ``root`` lives in the project's own tables, which
    ``corpus.assemble`` reads.
    """
    files: list[tuple[str, Path]] = []
    for overlay in project.config.overlays:
        if overlay.root is None:
            continue
        root = Path(overlay.root)
        root = root if root.is_absolute() else project.layout.root / root
        files.extend(("overlay", root / "tables" / f"{t}.parquet") for t in SOURCE_TABLES)
    return files


#: cartolex's stages, each running the engine (``cartolex.build.engine``). The AI
#: clean-up needs a key or a client: see :func:`cartolex.build.engine.engine_registry`.
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
            project=("languages", "slots", "levels"),
            params=(
                ParamSpec(
                    "parts",
                    "list",
                    "the parts of a text that feed the lexicon",
                    default=["title", "abstract"],
                    choices=("title", "abstract", "body", "full"),
                    minimum=1,
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
                ),
                ParamSpec(
                    "recency_years",
                    "int",
                    "only texts of the last N years build the keywords (0: every year)",
                    default=5,
                    minimum=0,
                    maximum=200,
                ),
            ),
            uses=("year",),
            provides=("people", "texts", "characters", "mapped_units"),
            cost=CostModel("characters", 2.0, 1e-8, 150.0, 3e-6),
            run=_engine("run_corpus"),
        ),
        Stage(
            "keywords.extract",
            "find keyword candidates",
            upstream=("corpus.assemble",),
            decisions=("decisions/stopwords.json",),
            project=("languages", "identity.language_models"),
            params=(
                ParamSpec(
                    "counting_unit",
                    "str",
                    "what one occurrence counts for: a person's texts together, or each text",
                    default="person",
                    choices=("person", "text"),
                ),
                ParamSpec(
                    "min_people",
                    "int",
                    "a candidate is kept only if at least this many people use it",
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
            ),
            checks=(
                CrossCheck(
                    "no more people required than the lexicon has",
                    ("min_people",),
                    ("people",),
                    _min_people_fit,
                ),
                CrossCheck("keywords counted per person", ("counting_unit",), (), _person_counting),
            ),
            cost=CostModel("characters", 5.0, 2e-6, 500.0, 1e-5),
            run=_engine("run_extract"),
        ),
        Stage(
            "keywords.triage",
            "AI clean-up",
            upstream=("keywords.extract",),
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
                "description, to the AI provider set in project.json; it is billed by that "
                "provider, and answers already paid for are reused from cache/ai/"
            ),
            cost=CostModel("people", 10.0, 0.5, 200.0, 0.0),
            run=_no_ai_key,
        ),
        Stage(
            "keywords.build",
            "build the vocabulary",
            upstream=("keywords.extract", "keywords.triage"),
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
            ),
            provides=("kept_keywords",),
            cost=CostModel("characters", 5.0, 5e-7, 300.0, 5e-6),
            run=_engine("run_build"),
        ),
        Stage(
            "themes.space",
            "place keywords in a common space",
            upstream=("keywords.build",),
            params=(
                ParamSpec(
                    "dimensions",
                    "int",
                    "the dimensions of the space keywords and people are placed in",
                    default=20,
                    minimum=2,
                    maximum=1000,
                ),
            ),
            cost=CostModel("people", 2.0, 0.01, 200.0, 0.5),
            run=_engine("run_space"),
        ),
        Stage(
            "themes.group",
            "group keywords into topics and themes",
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
                    default=20,
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
            cost=CostModel("kept_keywords", 1.0, 1e-3, 100.0, 8e-6, memory_exponent=2.0),
            run=_engine("run_group"),
        ),
        Stage(
            "themes.apply",
            "apply your themes",
            upstream=("themes.group",),
            decisions=("decisions/themes.json",),
            cost=CostModel("kept_keywords", 1.0, 1e-4, 200.0, 0.01),
            prepare=_prepare_themes,
            run=_engine("run_apply"),
        ),
        Stage(
            "map.layout",
            "draw the map",
            upstream=("themes.apply",),
            decisions=("decisions/maps.json",),
            project=("levels",),
            cost=CostModel("mapped_units", 5.0, 0.01, 300.0, 0.05),
            prepare=_prepare_maps,
            run=_engine("run_layout"),
        ),
        Stage(
            "map.trajectories",
            "change over time",
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
            ),
            uses=("year",),
            cost=CostModel("mapped_units", 2.0, 0.005, 200.0, 0.02),
            run=_engine("run_trajectories"),
        ),
        Stage(
            "overlays.position",
            "place projected people",
            upstream=("map.layout",),
            project=("overlays",),
            applies=_has_overlays,
            extra_inputs=_overlay_tables,
            cost=CostModel("mapped_units", 2.0, 0.001, 200.0, 0.01),
            run=_engine("run_overlays"),
        ),
    ]
)
