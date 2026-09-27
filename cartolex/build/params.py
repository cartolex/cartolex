# SPDX-License-Identifier: MIT
"""Parameters as decisions: what a stage can be told, and where each value comes from.

A stage declares its parameters as :class:`ParamSpec` objects. ``decisions/params.json``
holds only what people set. Every other value is a default, or a named
:class:`Rule` computed from the project's sizes (:class:`ProjectSizes`). The
effective value of every parameter, and where it came from (``default``,
``rule`` with the rule's name, or ``params.json``), is written into the stage's
``run.json``.

A parameter a stage does not know, or a value of the wrong type or outside its
range, is refused when the file is read (:func:`check_params`). A value that is
only impossible for this project (as many topics as keywords) is refused by the
stage's :class:`CrossCheck` once the sizes are known, before the stage runs.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any, Literal

from ..project.models import ParameterValue, ParamsFile

if TYPE_CHECKING:
    from ..project.models import ProjectFile
    from .stages import Registry, Stage

__all__ = [
    "GLOBAL_PARAMS",
    "RULES",
    "SIZE_NAMES",
    "CrossCheck",
    "ParamSpec",
    "ParamsError",
    "ProjectSizes",
    "Resolved",
    "Rule",
    "check_params",
    "resolve_params",
    "theme_depth",
    "theme_level_sizes",
]

ParamType = Literal["int", "float", "bool", "str", "list"]

#: The sizes a rule may use, in the order they are shown.
SIZE_NAMES = ("people", "texts", "characters", "kept_keywords", "mapped_units")

#: Parameters set at the top of ``params.json`` and recorded by the stages that use them.
GLOBAL_PARAMS = ("seed", "year")


@dataclass(frozen=True)
class ProjectSizes:
    """How big the project is; ``None`` when not known yet.

    ``people``: the people whose texts build the lexicon; ``texts``: their texts;
    ``characters``: the characters those texts hold; ``kept_keywords``: the size
    of the vocabulary; ``mapped_units``: the units the map places (the mapped
    people). Each is reported by the stage that knows it, in its ``run.json``
    counts; before that stage has run, the sources give an estimate.
    """

    people: int | None = None
    texts: int | None = None
    characters: int | None = None
    kept_keywords: int | None = None
    mapped_units: int | None = None

    def get(self, name: str) -> int | None:
        """The size called *name*."""
        if name not in SIZE_NAMES:
            raise KeyError(f"unknown size {name!r}; known: {list(SIZE_NAMES)}")
        return getattr(self, name)

    def merged(self, other: Mapping[str, int | None]) -> ProjectSizes:
        """These sizes, with the known values of *other* taking precedence."""
        values = {f.name: getattr(self, f.name) for f in fields(self)}
        values.update({k: int(v) for k, v in other.items() if k in values and v is not None})
        return ProjectSizes(**values)

    def as_dict(self) -> dict[str, int | None]:
        return {name: getattr(self, name) for name in SIZE_NAMES}


class ParamsError(ValueError):
    """``params.json`` sets a parameter a stage does not know, or a value it cannot take."""

    def __init__(self, problems: list[str], where: str = "decisions/params.json") -> None:
        self.problems = list(problems)
        super().__init__(f"{where}: " + "; ".join(self.problems))


# ── rules ────────────────────────────────────────────────────────────────────


def _digits_below(n: int) -> int:
    """⌊log₁₀ n⌋ for n ≥ 1, computed on integers (no floating-point edge at powers of ten)."""
    return len(str(int(n))) - 1


def theme_depth(kept_keywords: int, mapped_units: int) -> int:
    """How many theme levels a vocabulary of *kept_keywords* on *mapped_units* units supports.

    depth = min(⌊log₁₀ K⌋ − 1, ⌊log₁₀ U⌋), clamped to 1–4: a level for each
    tenfold of keywords beyond a hundred, never more levels than the map has
    decades of units.
    """
    k = _digits_below(max(1, kept_keywords)) - 1
    u = _digits_below(max(1, mapped_units))
    return max(1, min(4, k, u))


def theme_level_sizes(
    kept_keywords: int, depth: int, top_groups: int, keywords_per_group: int
) -> tuple[int, ...]:
    """The number of groups at each theme level, from the top.

    The top level has *top_groups* groups; the finest level has about
    *keywords_per_group* keywords per group; the levels in between grow
    geometrically. At depth 1 the one level follows *top_groups*.
    """
    if depth <= 1:
        return (int(top_groups),)
    finest = max(1, round(kept_keywords / keywords_per_group))
    ratio = finest / top_groups
    inner = [round(top_groups * ratio ** (i / (depth - 1))) for i in range(1, depth - 1)]
    return (int(top_groups), *inner, finest)


@dataclass(frozen=True)
class Rule:
    """A default computed from the project's sizes."""

    name: str
    description: str
    needs: tuple[str, ...]
    compute: Callable[[ProjectSizes], Any]


RULES: dict[str, Rule] = {
    rule.name: rule
    for rule in (
        Rule(
            "theme_depth",
            "min(⌊log₁₀ kept keywords⌋ − 1, ⌊log₁₀ mapped units⌋), clamped to 1–4",
            ("kept_keywords", "mapped_units"),
            lambda s: theme_depth(s.kept_keywords or 1, s.mapped_units or 1),
        ),
    )
}


# ── parameter declarations ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ParamSpec:
    """One parameter of a stage.

    Its value comes from ``params.json`` when set there; otherwise from the named
    :attr:`rule` when there is one, else from :attr:`default`. ``minimum`` and
    ``maximum`` bound numbers; ``choices`` lists the allowed values of a string,
    or of each item of a list.
    """

    name: str
    type: ParamType
    description: str
    default: Any = None
    rule: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] | None = None

    def __post_init__(self) -> None:
        if self.rule is not None and self.rule not in RULES:
            raise ValueError(f"parameter {self.name!r}: unknown rule {self.rule!r}")
        if self.rule is None:
            problem = self.problem(self.default)
            if problem:
                raise ValueError(f"parameter {self.name!r}: its default {problem}")

    def problem(self, value: Any) -> str | None:
        """Why *value* cannot be this parameter's value, or ``None`` when it can."""
        t = self.type
        if t == "bool":
            if not isinstance(value, bool):
                return f"{value!r} is not true or false"
            return None
        if t == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                return f"{value!r} is not a whole number"
        elif t == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"{value!r} is not a number"
            if not math.isfinite(value):
                return f"{value!r} is not a finite number"
        elif t == "str":
            if not isinstance(value, str):
                return f"{value!r} is not text"
        elif t == "list":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                return f"{value!r} is not a list of texts"
            if len(set(value)) != len(value):
                return f"{value!r} repeats an item"
            if self.choices is not None:
                bad = [v for v in value if v not in self.choices]
                if bad:
                    return (
                        f"{bad} {'is' if len(bad) == 1 else 'are'} not among {list(self.choices)}"
                    )
            if self.minimum is not None and len(value) < self.minimum:
                return f"{value!r} has fewer than {self.minimum:g} item(s)"
            return None
        if t in ("int", "float"):
            if self.minimum is not None and value < self.minimum:
                return f"{value!r} is below the minimum {self.minimum:g}"
            if self.maximum is not None and value > self.maximum:
                return f"{value!r} is above the maximum {self.maximum:g}"
        if self.choices is not None and value not in self.choices:
            return f"{value!r} is not one of {list(self.choices)}"
        return None

    def coerce(self, value: Any) -> Any:
        """The value as stored in a record (a float parameter set to 1 is 1.0)."""
        if self.type == "float":
            return float(value)
        if self.type == "list":
            return list(value)
        return value


@dataclass(frozen=True)
class CrossCheck:
    """A check that needs the project's sizes: it refuses values impossible for this project.

    *check* receives the effective values of the stage's parameters (with ``seed``
    and ``year`` when the stage uses them), the sizes and the project's
    ``project.json``; it returns the reason for a refusal, or ``None``. It runs
    only once every parameter in *params* and every size in *sizes* is known.
    """

    description: str
    params: tuple[str, ...]
    sizes: tuple[str, ...]
    check: Callable[[Mapping[str, Any], ProjectSizes, ProjectFile], str | None]


# ── reading and resolving ────────────────────────────────────────────────────


def check_params(params: ParamsFile, registry: Registry) -> list[str]:
    """Every problem of *params* for the stages of *registry*, each with its reason."""
    problems: list[str] = []
    for stage_id, values in params.stages.items():
        stage = registry.get(stage_id)
        if stage is None:
            problems.append(f"{stage_id}: this build has no such stage")
            continue
        if not isinstance(values, dict):
            problems.append(f"{stage_id}: expected an object of parameters, got {values!r}")
            continue
        known = {p.name: p for p in stage.params}
        for name, value in values.items():
            spec = known.get(name)
            if spec is None:
                listed = ", ".join(sorted(known)) or "none"
                problems.append(f"{stage_id}: unknown parameter {name!r} (known: {listed})")
                continue
            problem = spec.problem(value)
            if problem:
                problems.append(f"{stage_id}.{name}: {problem}")
    return problems


@dataclass(frozen=True)
class Resolved:
    """The effective parameters of one stage.

    ``values`` maps each parameter to its value and origin. ``unknown`` names the
    rule-computed parameters whose sizes are not known yet, with the missing
    sizes; their value in ``values`` is ``None`` until they are.
    """

    values: dict[str, ParameterValue]
    unknown: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def plain(self) -> dict[str, Any]:
        """Name → value."""
        return {name: pv.value for name, pv in self.values.items()}

    def problems(self, stage: Stage, sizes: ProjectSizes, config: ProjectFile) -> list[str]:
        """The stage's cross-checks that refuse these values (those that can run now)."""
        plain = self.plain()
        found = []
        for check in stage.checks:
            if any(p in self.unknown or p not in plain for p in check.params):
                continue
            if any(sizes.get(s) is None for s in check.sizes):
                continue
            reason = check.check(plain, sizes, config)
            if reason:
                found.append(f"{stage.id}: {reason}")
        return found


def resolve_params(stage: Stage, params: ParamsFile, sizes: ProjectSizes, *, year: int) -> Resolved:
    """The effective value of each of *stage*'s parameters, and where it came from.

    *year* is the current year, used when ``params.json`` pins none. Values set in
    *params* are assumed checked (:func:`check_params`).
    """
    given = params.stages.get(stage.id, {}) or {}
    values: dict[str, ParameterValue] = {}
    unknown: dict[str, tuple[str, ...]] = {}
    if "seed" in stage.uses:
        source = "params.json" if "seed" in params.model_fields_set else "default"
        values["seed"] = ParameterValue(value=params.seed, source=source)
    if "year" in stage.uses:
        if params.pinned_year is not None:
            values["year"] = ParameterValue(value=params.pinned_year, source="params.json")
        else:
            values["year"] = ParameterValue(value=int(year), source="default")
    for spec in stage.params:
        if spec.name in given:
            values[spec.name] = ParameterValue(
                value=spec.coerce(given[spec.name]), source="params.json"
            )
        elif spec.rule is not None:
            rule = RULES[spec.rule]
            missing = tuple(n for n in rule.needs if sizes.get(n) is None)
            if missing:
                unknown[spec.name] = missing
                values[spec.name] = ParameterValue(value=None, source="rule", rule=rule.name)
            else:
                values[spec.name] = ParameterValue(
                    value=spec.coerce(rule.compute(sizes)), source="rule", rule=rule.name
                )
        else:
            values[spec.name] = ParameterValue(value=spec.coerce(spec.default), source="default")
    return Resolved(values, unknown)
