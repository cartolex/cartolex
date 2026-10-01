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
    "DOC_TYPES_BY_SLOT_KIND",
    "GLOBAL_PARAMS",
    "PARTS_BY_SLOT_KIND",
    "RULES",
    "SIZE_NAMES",
    "TIERS",
    "WIDGETS",
    "CrossCheck",
    "ParamSpec",
    "ParamsError",
    "ProjectSizes",
    "Resolved",
    "Rule",
    "check_params",
    "resolve_params",
    "space_dimensions",
    "theme_depth",
    "theme_level_sizes",
]

ParamType = Literal["int", "float", "bool", "str", "list", "ints", "floats"]

#: How prominent a parameter is on the screens (display only): the few a curator turns
#: (``essential``), those shown under « More » (``intermediate``), and the rest.
Tier = Literal["essential", "intermediate", "advanced"]
TIERS: tuple[str, ...] = ("essential", "intermediate", "advanced")

#: The control a parameter is edited with (see :attr:`ParamSpec.shape`).
Widget = Literal[
    "switch", "choice", "slider", "number", "text", "chips", "order", "range", "levels", "grid"
]
WIDGETS: tuple[str, ...] = (
    "switch",
    "choice",
    "slider",
    "number",
    "text",
    "chips",
    "order",
    "range",
    "levels",
    "grid",
)

#: Whole-number ranges at most this wide get a slider; wider ones a number field.
SLIDER_SPAN = 500

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


#: The space's dimensions on a small project, and where the rule starts to grow them.
SPACE_DIMENSIONS = 20
SPACE_FROM_PEOPLE = 2_000
#: The most dimensions the rule gives.
SPACE_MAX_DIMENSIONS = 200


def space_dimensions(people: int) -> int:
    """How many dimensions the space of a project with *people* people keeps.

    20 up to 2 000 people, then ``20 × √(people / 2 000)``: a field of more
    people holds more distinct themes, and a space of 20 dimensions keeps a
    smaller share of them (``docs/sizes.md`` has the measures); at most 200.
    """
    n = max(1, int(people))
    if n <= SPACE_FROM_PEOPLE:
        return SPACE_DIMENSIONS
    grown = round(SPACE_DIMENSIONS * math.sqrt(n / SPACE_FROM_PEOPLE))
    return int(min(SPACE_MAX_DIMENSIONS, max(SPACE_DIMENSIONS, grown)))


@dataclass(frozen=True)
class Rule:
    """A default computed from the project's sizes, and from earlier stages' parameters.

    *needs* names the sizes the rule uses; *reads* the parameters of upstream stages
    it follows, as ``(stage, parameter)`` pairs: *compute* then takes the sizes and
    those parameters' effective values, keyed ``"stage.parameter"``.
    """

    name: str
    description: str
    needs: tuple[str, ...]
    compute: Callable[..., Any]
    reads: tuple[tuple[str, str], ...] = ()

    def value(self, sizes: ProjectSizes, params: ParamsFile) -> Any:
        """The rule's value for *sizes* and the parameters it reads in *params*."""
        if not self.reads:
            return self.compute(sizes)
        read = {f"{sid}.{name}": _effective(sid, name, params, sizes) for sid, name in self.reads}
        return self.compute(sizes, read)


def _effective(stage_id: str, name: str, params: ParamsFile, sizes: ProjectSizes) -> Any:
    """The effective value of *stage_id*'s parameter *name*: set in *params*, else its
    rule's value, else its default (cartolex's stages)."""
    from .stages import STAGES

    spec = next(p for p in STAGES[stage_id].params if p.name == name)
    given = params.stages.get(stage_id, {}) or {}
    if name in given:
        return spec.coerce(given[name])
    if spec.rule is not None:
        return spec.coerce(RULES[spec.rule].value(sizes, params))
    return spec.coerce(spec.default)


def _by_space(key: str) -> Callable[[ProjectSizes, Mapping[str, Any]], Any]:
    """The comb's *key* (``grid``, ``one_level`` or ``sideways``) calibrated for the
    space's unit."""

    def compute(_: ProjectSizes, read: Mapping[str, Any]) -> Any:
        from ..lexicon.theme_comb import CALIBRATION

        value = getattr(CALIBRATION[str(read["themes.space.space_unit"])], key)
        return list(value) if isinstance(value, tuple) else value

    return compute


#: The parts of a text read by default, by the kind of its slot: collected texts by their
#: title and abstract (a full text collected on request counts only when asked for),
#: documents a person gave (a folder, a corpus) whole.
PARTS_BY_SLOT_KIND: dict[str, list[str]] = {
    "collection": ["title", "abstract"],
    "folder": ["title", "abstract", "full"],
    "corpus": ["title", "abstract", "full"],
}

#: The document types read by default, by the kind of the slot: a collection slot reads
#: texts (articles, preprints, books and their chapters, theses, reports, communications),
#: not the datasets, software or peer reviews an index also lists; a folder or a corpus
#: slot reads every document it was given. A slot's own ``doc_types`` replace these.
DOC_TYPES_BY_SLOT_KIND: dict[str, list[str] | None] = {
    "collection": [
        "article",
        "book",
        "chapter",
        "communication",
        "preprint",
        "proceedings",
        "report",
        "review",
        "thesis",
    ],
    "folder": None,
    "corpus": None,
}

RULES: dict[str, Rule] = {
    rule.name: rule
    for rule in (
        Rule(
            "theme_depth",
            "min(⌊log₁₀ kept keywords⌋ − 1, ⌊log₁₀ mapped units⌋), clamped to 1–4",
            ("kept_keywords", "mapped_units"),
            lambda s: theme_depth(s.kept_keywords or 1, s.mapped_units or 1),
        ),
        Rule(
            "space_dimensions",
            "20 up to 2 000 people, then 20 × √(people / 2 000), at most 200",
            ("people",),
            lambda s: space_dimensions(s.people or 1),
        ),
        Rule(
            "space_unit_texts",
            "the texts: keywords are near when the same texts use them, which groups them by "
            "subject better than the people do (a person working on two subjects no longer "
            "brings them together); the people's space may suit a corpus in several "
            "languages better",
            (),
            lambda s: "text",
        ),
        Rule(
            "comb_grid_by_space",
            "the θ values calibrated for the space's unit: 0.1 to 0.25 by 0.025 on the "
            "people's space, 0.125 to 0.275 on the texts'",
            (),
            _by_space("grid"),
            reads=(("themes.space", "space_unit"),),
        ),
        Rule(
            "comb_theta_one_level_by_space",
            "θ for one level, calibrated for the space's unit: 0.2 on the people's space, "
            "0.3 on the texts'",
            (),
            _by_space("one_level"),
            reads=(("themes.space", "space_unit"),),
        ),
        Rule(
            "comb_sideways_by_space",
            "anywhere on the people's space; within the parent on the texts' (there the "
            "keywords many texts use gather in topics of their own, which would draw the "
            "specific keywords of other themes)",
            (),
            _by_space("sideways"),
            reads=(("themes.space", "space_unit"),),
        ),
        Rule(
            "doc_types_by_slot_kind",
            "a collection slot reads articles, preprints, reviews, books, chapters, theses, "
            "reports and communications; a folder or a corpus slot every document; a slot's "
            "own doc_types replace these",
            (),
            lambda s: {
                kind: (list(types) if types is not None else None)
                for kind, types in DOC_TYPES_BY_SLOT_KIND.items()
            },
        ),
        Rule(
            "parts_by_slot_kind",
            "title and abstract for a collection slot; title, abstract and the whole document "
            "for a folder or a corpus slot",
            (),
            lambda s: {kind: list(parts) for kind, parts in PARTS_BY_SLOT_KIND.items()},
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
    or of each item of a list. A ``list`` holds texts, ``ints`` whole numbers and
    ``floats`` numbers (``minimum`` and ``maximum`` then bound each number,
    ``items`` the length). A *nullable* parameter also takes ``null``: « not set »
    (for some, « computed »: its description says what).

    *section* is the parameter's place on the method screen: the heading it is
    shown under, within its stage's step (``""``: the step's first rows).

    The rest is for display and never changes what a value may be, except *keys*.
    *tier* says how prominent the parameter is (the assignment lives in one table,
    ``cartolex.build.stages.PARAM_TIERS``). *widget* names its control when its type
    does not say enough (:attr:`shape` infers the others). *keys* makes a ``list``
    parameter keyed as well: its value is either one list for every key, or an object
    giving each key (a slot kind) its own list (``null`` for a key when *nullable*).
    *suggestions* are the items a control offers for a list without *choices*; any
    other item is still allowed.
    """

    name: str
    type: ParamType
    description: str
    default: Any = None
    rule: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] | None = None
    nullable: bool = False
    items: tuple[int, int] | None = None
    section: str = ""
    tier: Tier = "advanced"
    widget: Widget | None = None
    keys: tuple[str, ...] | None = None
    suggestions: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"parameter {self.name!r}: unknown tier {self.tier!r}")
        if self.widget is not None and self.widget not in WIDGETS:
            raise ValueError(f"parameter {self.name!r}: unknown widget {self.widget!r}")
        if self.keys is not None and self.type != "list":
            raise ValueError(f"parameter {self.name!r}: only a list parameter can be keyed")
        if self.rule is not None and self.rule not in RULES:
            raise ValueError(f"parameter {self.name!r}: unknown rule {self.rule!r}")
        if self.rule is None:
            problem = self.problem(self.default)
            if problem:
                raise ValueError(f"parameter {self.name!r}: its default {problem}")

    @property
    def shape(self) -> str:
        """The control that edits this parameter: :attr:`widget`, else inferred from the type.

        ``switch`` a bool; ``grid`` a keyed list (keys × items); ``range`` two numbers;
        ``chips`` another list; ``choice`` a value among *choices*; ``slider`` a number
        with both bounds (a whole number only when at most :data:`SLIDER_SPAN` apart);
        ``number`` another number; ``text`` a text.
        """
        if self.widget is not None:
            return self.widget
        if self.type == "bool":
            return "switch"
        if self.keys is not None:
            return "grid"
        if self.type in ("ints", "floats") and self.items == (2, 2):
            return "range"
        if self.type in ("list", "ints", "floats"):
            return "chips"
        if self.choices is not None:
            return "choice"
        if self.type in ("int", "float"):
            bounded = self.minimum is not None and self.maximum is not None
            if bounded and (self.type == "float" or self.maximum - self.minimum <= SLIDER_SPAN):
                return "slider"
            return "number"
        return "text"

    def problem(self, value: Any) -> str | None:
        """Why *value* cannot be this parameter's value, or ``None`` when it can."""
        if self.keys is not None and isinstance(value, Mapping):
            if sorted(value) != sorted(self.keys):
                return f"{value!r} does not give each of {list(self.keys)} its own value"
            for key, item in value.items():
                problem = self._problem(item)
                if problem:
                    return f"{key}: {problem}"
            return None
        return self._problem(value)

    def _problem(self, value: Any) -> str | None:
        t = self.type
        if value is None and self.nullable:
            return None
        if t in ("ints", "floats"):
            number = (int,) if t == "ints" else (int, float)
            if not isinstance(value, list) or not all(
                isinstance(v, number)
                and not isinstance(v, bool)
                and (t == "ints" or math.isfinite(v))
                for v in value
            ):
                kind = "whole numbers" if t == "ints" else "numbers"
                return f"{value!r} is not a list of {kind}"
            if self.items is not None and not self.items[0] <= len(value) <= self.items[1]:
                return f"{value!r} does not have {self.items[0]} to {self.items[1]} items"
            low = [v for v in value if self.minimum is not None and v < self.minimum]
            if low:
                return f"{value!r} has a number below the minimum {self.minimum:g}"
            high = [v for v in value if self.maximum is not None and v > self.maximum]
            if high:
                return f"{value!r} has a number above the maximum {self.maximum:g}"
            return None
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
        """The value as stored in a record (a float parameter set to 1 is 1.0); a rule's value
        by slot kind stays a mapping."""
        if self.type == "float":
            return None if value is None else float(value)
        if isinstance(value, Mapping):
            return {k: self.coerce(v) for k, v in value.items()}
        if self.type == "floats" and value is not None:
            return [float(v) for v in value]
        if self.type in ("list", "ints") and value is not None:
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
                    value=spec.coerce(rule.value(sizes, params)), source="rule", rule=rule.name
                )
        else:
            values[spec.name] = ParameterValue(value=spec.coerce(spec.default), source="default")
    return Resolved(values, unknown)
