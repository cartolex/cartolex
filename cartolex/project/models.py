# SPDX-License-Identifier: MIT
"""The JSON files of a project, as validated models.

Each model is the single source of its file's rules: the JSON Schemas shipped in
``cartolex/project/schemas/`` are generated from these classes (see
:mod:`cartolex.project.schemas`), and the format pages in ``docs/format/``
describe them. Within one major format version, unknown keys are kept, not
refused, so a file written by a newer cartolex survives a rewrite by an older
one.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

__all__ = [
    "COLLECT_PARAMS",
    "LANGUAGES",
    "STAGE_IDS",
    "AIIdentity",
    "AppStamp",
    "Base",
    "CodeStamp",
    "Created",
    "FileInput",
    "Identity",
    "Languages",
    "Level",
    "MapLayout",
    "MapVersion",
    "MapsFile",
    "Measures",
    "Overlay",
    "ParameterValue",
    "ParamsFile",
    "ProjectFile",
    "RunRecord",
    "SetAside",
    "Slot",
    "StageInput",
    "StopwordsFile",
    "ThemeLevel",
    "ThemeNode",
    "ThemesBasis",
    "ThemesFile",
    "ThemesSaved",
    "YearWindow",
]

#: The languages cartolex ships a language pack for today (a spaCy model, function
#: words, prompts, interface names). A pack is code, not format: the files accept
#: any ISO 639-1 code, and a project takes a new pack up by adding its code.
LANGUAGES = ("en", "fr", "pt")
#: An ISO 639-1 language code.
Language = Annotated[str, Field(pattern=r"^[a-z]{2}$")]

#: The build's stages, in order (see ``docs/format/derived.md``).
STAGE_IDS = (
    "corpus.assemble",
    "keywords.extract",
    "keywords.triage",
    "keywords.build",
    "themes.space",
    "themes.group",
    "themes.apply",
    "map.layout",
    "map.trajectories",
    "overlays.position",
)

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
NonEmpty = Annotated[str, Field(min_length=1)]
#: A spaCy model with its version: ``en_core_web_md@3.8.0``.
ModelRef = Annotated[str, Field(pattern=r"^[A-Za-z0-9_]+@[0-9][0-9A-Za-z.+-]*$")]
#: Names per interface language; at least one.
Names = Annotated[dict[Language, NonEmpty], Field(min_length=1)]
Fingerprint = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class _Model(BaseModel):
    """Common settings: unknown keys are kept, values are checked on assignment."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)


def _unique(values: list[str], what: str) -> list[str]:
    seen: set[str] = set()
    for v in values:
        if v in seen:
            raise ValueError(f"{what} {v!r} appears twice")
        seen.add(v)
    return values


# ── project.json ─────────────────────────────────────────────────────────────


class AIIdentity(_Model):
    """The AI provider and model used for AI filtering by API."""

    provider: NonEmpty
    model: NonEmpty


class Identity(_Model):
    """What AI answers and parsed texts are keyed on; frozen once texts are processed."""

    domain_title: NonEmpty
    domain_description: str = ""
    frozen: bool = False
    ai: AIIdentity | None = None
    language_models: dict[Language, ModelRef] = Field(default_factory=dict)


class Languages(_Model):
    """Corpus languages, the reference language that merges forms, the display languages."""

    corpus: Annotated[list[Language], Field(min_length=1)]
    reference: Language = "en"
    display: Annotated[list[Language], Field(min_length=1)] = Field(default_factory=lambda: ["en"])

    @field_validator("corpus", "display")
    @classmethod
    def _no_repeats(cls, v: list[str]) -> list[str]:
        return _unique(v, "language")


class Level(_Model):
    """One organisation level, from the smallest to the largest."""

    id: Slug
    names: Names


Year = Annotated[int, Field(ge=1000, le=2200)]


class YearWindow(_Model):
    """A window of publication years, both ends inclusive; an end left ``null`` is open."""

    first: Year | None = Field(default=None, alias="from")
    last: Year | None = Field(default=None, alias="to")

    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)

    @model_validator(mode="after")
    def _ordered(self) -> YearWindow:
        if self.first is not None and self.last is not None and self.first > self.last:
            raise ValueError(f"the window starts ({self.first}) after it ends ({self.last})")
        return self

    def as_tuple(self) -> tuple[int | None, int | None]:
        """``(first, last)``, either end ``None`` when open."""
        return (self.first, self.last)


class Slot(_Model):
    """One ordered way texts enter the project.

    ``years`` (optional) is the window of publication years collections of this
    slot use by default; a collection given another window explicitly uses that.
    """

    id: Slug
    kind: Literal["collection", "folder", "corpus"]
    fit: bool = True
    trajectory: bool = True
    doc_types: list[NonEmpty] | None = None
    years: YearWindow | None = None

    @model_serializer(mode="wrap")
    def _omit_no_window(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict) and data.get("years") is None:
            data.pop("years", None)
        return data


class Overlay(_Model):
    """A projected set: placed on the finished map, never shaping it.

    Without a ``root``, its people are in the project's own tables, with the role
    ``projected`` and this set's id in ``decisions/people.csv``; with one, the set
    is a folder of its own, laid out like ``sources/``.
    """

    id: Slug
    root: NonEmpty | None = None
    trajectory: bool = False


class Base(_Model):
    """Another project's map this project can be placed on."""

    id: Slug
    bundle: NonEmpty
    map_version: NonEmpty


class Created(_Model):
    at: datetime
    by: NonEmpty


class AppStamp(_Model):
    id: NonEmpty
    version: NonEmpty


class ProjectFile(_Model):
    """``project.json``: what the project is."""

    format: Literal["cartolex-project/1"] = "cartolex-project/1"
    name: NonEmpty
    identity: Identity
    languages: Languages
    levels: list[Level] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    overlays: list[Overlay] = Field(default_factory=list)
    bases: list[Base] = Field(default_factory=list)
    created: Created
    app: AppStamp

    @model_validator(mode="after")
    def _ids(self) -> ProjectFile:
        _unique([lv.id for lv in self.levels], "level id")
        _unique([s.id for s in self.slots], "slot id")
        _unique([o.id for o in self.overlays], "overlay id")
        _unique([b.id for b in self.bases], "base id")
        clash = {s.id for s in self.slots} & {o.id for o in self.overlays}
        if clash:
            raise ValueError(f"an overlay cannot share a slot's id: {sorted(clash)}")
        return self


# ── params.json ──────────────────────────────────────────────────────────────


#: The collection parameters ``params.json`` may set (its optional ``collect`` key), by
#: step, each a whole number with its smallest value: the collaborators' rounds are taken
#: whole up to ``cap`` people, a work with more than ``max_authors`` authors is left out of
#: the co-author graph, and a person is well covered from ``good`` texts with an abstract.
COLLECT_PARAMS: dict[str, dict[str, int]] = {
    "snowball": {"cap": 1, "max_authors": 2},
    "coverage": {"good": 1},
}


class ParamsFile(_Model):
    """``decisions/params.json``: the parameters people set, and nothing else.

    ``collect`` (optional) holds the collection's parameters people set, by step
    (:data:`COLLECT_PARAMS`); it is left out of the file when empty.
    """

    format: Literal["cartolex-params/1"] = "cartolex-params/1"
    seed: Annotated[int, Field(ge=0, lt=2**32)] = 0
    pinned_year: Annotated[int, Field(ge=1900, le=2200)] | None = None
    stages: dict[str, dict[str, Any]] = Field(default_factory=dict)
    collect: dict[str, dict[str, int]] = Field(default_factory=dict)

    @field_validator("stages")
    @classmethod
    def _known_stages(cls, v: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        unknown = sorted(set(v) - set(STAGE_IDS))
        if unknown:
            raise ValueError(f"unknown stage id(s): {unknown}; known: {list(STAGE_IDS)}")
        return v

    @field_validator("collect", mode="before")
    @classmethod
    def _known_collect(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        problems = []
        for step, values in v.items():
            known = COLLECT_PARAMS.get(step)
            if known is None:
                problems.append(
                    f"unknown collection step {step!r} (known: {sorted(COLLECT_PARAMS)})"
                )
                continue
            if not isinstance(values, dict):
                problems.append(f"collect.{step}: expected an object of parameters")
                continue
            for name, value in values.items():
                if name not in known:
                    problems.append(
                        f"collect.{step}: unknown parameter {name!r} (known: {sorted(known)})"
                    )
                elif isinstance(value, bool) or not isinstance(value, int) or value < known[name]:
                    problems.append(
                        f"collect.{step}.{name}: {value!r} is not a whole number of at least {known[name]}"
                    )
        if problems:
            raise ValueError("; ".join(problems))
        return v

    @model_serializer(mode="wrap")
    def _omit_empty_collect(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict) and not data.get("collect"):
            data.pop("collect", None)
        return data


# ── run.json ─────────────────────────────────────────────────────────────────


class CodeStamp(_Model):
    """The code that ran: cartolex's version, a fingerprint of its sources, the stage's version.

    ``stage_version`` changes when cartolex deliberately changes what a stage
    produces; a result made by another version of its stage needs an update.
    """

    version: NonEmpty
    fingerprint: Fingerprint
    stage_version: Annotated[int, Field(ge=1)] = 1


class ParameterValue(_Model):
    """One effective parameter and where its value came from."""

    value: Any
    source: Literal["default", "rule", "params.json"] = Field(alias="from")
    rule: str | None = None

    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)


class StageInput(_Model):
    kind: Literal["stage"] = "stage"
    stage: NonEmpty
    run_id: NonEmpty


class FileInput(_Model):
    kind: Literal["source", "decision", "cache", "overlay", "base"]
    path: NonEmpty
    fingerprint: Fingerprint


class Measures(_Model):
    seconds: Annotated[float, Field(ge=0)] | None = None
    peak_memory_mb: Annotated[float, Field(ge=0)] | None = None
    counts: dict[str, int] = Field(default_factory=dict)


RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{4,}$")


class RunRecord(_Model):
    """``derived/<stage>/run.json``: the record of the run that produced a stage's results.

    The same record, with the outcome ``failed`` or ``cancelled`` and an
    ``error``, is kept for a stage's last unsuccessful attempt in
    ``derived/.attempts/<stage>.json``.
    """

    format: Literal["cartolex-run/1"] = "cartolex-run/1"
    stage: NonEmpty
    run_id: Annotated[str, Field(pattern=RUN_ID_PATTERN.pattern)]
    outcome: Literal["succeeded", "failed", "cancelled"]
    started_at: datetime
    finished_at: datetime | None = None
    code: CodeStamp
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)
    inputs: list[Annotated[StageInput | FileInput, Field(discriminator="kind")]] = Field(
        default_factory=list
    )
    identity: dict[str, Any] = Field(default_factory=dict)
    measures: Measures = Field(default_factory=Measures)
    warnings: list[str] = Field(default_factory=list)
    #: Why an attempt failed (``derived/.attempts/<stage>.json`` only).
    error: str | None = None

    @field_validator("stage")
    @classmethod
    def _known_stage(cls, v: str) -> str:
        if v not in STAGE_IDS:
            raise ValueError(f"unknown stage id {v!r}")
        return v


# ── themes.json ──────────────────────────────────────────────────────────────


class ThemeLevel(_Model):
    names: Names


class ThemeNode(_Model):
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    parent: str | None = None
    names: dict[Language, str] = Field(default_factory=dict)
    order: int = 0


#: How many levels, from the top, a keyword's usage counts toward (0: none, shown only).
Attribution = Annotated[int, Field(ge=0, le=3)]


class SetAside(_Model):
    """A set-aside keyword: the node it came from, why, and the attribution it had there."""

    source: str | None = Field(default=None, alias="from")
    reason: str = ""
    attribution: Attribution | None = None

    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)

    @model_serializer(mode="wrap")
    def _omit_empty_attribution(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict) and data.get("attribution") is None:
            data.pop("attribution", None)
        return data


class ThemesBasis(_Model):
    run: str | None = None
    vocabulary: Fingerprint | None = None


class ThemesSaved(_Model):
    """When a version of the tree was saved, and the action that saved it."""

    at: datetime
    action: NonEmpty


class ThemesFile(_Model):
    """``decisions/themes.json``: the theme tree, 1 to 4 levels of nodes holding keywords.

    A keyword is under one node, at any level (a keyword on a higher node is
    broader than every node below it), or set aside. Its usage counts toward its
    node and every node above it; ``attribution`` lowers that to levels 1 to
    ``n`` (``0``: nowhere), with ``n`` below its node's level.
    """

    format: Literal["cartolex-themes/1"] = "cartolex-themes/1"
    depth: Annotated[int, Field(ge=1, le=4)]
    levels: list[ThemeLevel]
    nodes: list[ThemeNode] = Field(default_factory=list)
    keywords: dict[str, str] = Field(default_factory=dict)
    attribution: dict[str, Attribution] = Field(default_factory=dict)
    set_aside: dict[str, SetAside] = Field(default_factory=dict)
    review: dict[str, Literal["to_check", "reviewed"]] = Field(default_factory=dict)
    based_on: ThemesBasis = Field(default_factory=ThemesBasis)
    saved: ThemesSaved | None = None

    @model_validator(mode="after")
    def _tree(self) -> ThemesFile:
        if len(self.levels) != self.depth:
            raise ValueError(f"depth is {self.depth} but {len(self.levels)} level(s) are named")
        by_id = {n.id: n for n in self.nodes}
        if len(by_id) != len(self.nodes):
            _unique([n.id for n in self.nodes], "node id")
        level: dict[str, int] = {}

        def level_of(node_id: str, trail: tuple[str, ...] = ()) -> int:
            if node_id in level:
                return level[node_id]
            if node_id in trail:
                raise ValueError(f"node {node_id!r} is its own ancestor")
            node = by_id[node_id]
            if node.parent is None:
                lv = 1
            elif node.parent not in by_id:
                raise ValueError(f"node {node_id!r} has an unknown parent {node.parent!r}")
            else:
                lv = level_of(node.parent, (*trail, node_id)) + 1
            if lv > self.depth:
                raise ValueError(f"node {node_id!r} sits below the tree's depth {self.depth}")
            level[node_id] = lv
            return lv

        for n in self.nodes:
            level_of(n.id)
        for term, node_id in self.keywords.items():
            if node_id not in by_id:
                raise ValueError(f"keyword {term!r} points to an unknown node {node_id!r}")
        both = set(self.keywords) & set(self.set_aside)
        if both:
            raise ValueError(f"keyword(s) both placed and set aside: {sorted(both)[:5]}")
        stray = set(self.review) - set(self.keywords) - set(self.set_aside)
        if stray:
            raise ValueError(f"review names keyword(s) the tree does not hold: {sorted(stray)[:5]}")
        unplaced = set(self.attribution) - set(self.keywords)
        if unplaced:
            raise ValueError(
                f"attribution names keyword(s) that are not placed: {sorted(unplaced)[:5]}"
            )
        high = [k for k, n in self.attribution.items() if n >= level[self.keywords[k]]]
        if high:
            raise ValueError(
                "an attribution counts toward fewer levels than the keyword's node is on "
                f"(0 to its level - 1): {sorted(high)[:5]}"
            )
        deep = [k for k, e in self.set_aside.items() if (e.attribution or 0) >= self.depth]
        if deep:
            raise ValueError(
                f"a set-aside attribution counts toward 0 to {self.depth - 1} level(s) in a tree "
                f"of depth {self.depth}: {sorted(deep)[:5]}"
            )
        return self


# ── maps.json ────────────────────────────────────────────────────────────────

Shows = Annotated[str, Field(pattern=r"^(people|texts|organisations:[a-z0-9][a-z0-9_-]{0,63})$")]


class MapLayout(_Model):
    method: NonEmpty
    seed: Annotated[int, Field(ge=0, lt=2**32)] = 0
    params: dict[str, Any] = Field(default_factory=dict)


class MapVersion(_Model):
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")]
    shows: Annotated[list[Shows], Field(min_length=1)]
    layout: MapLayout
    base: Slug | None = None
    created_at: datetime
    note: str = ""


class MapsFile(_Model):
    """``decisions/maps.json``: the map versions and the pinned one."""

    format: Literal["cartolex-maps/1"] = "cartolex-maps/1"
    pinned: str | None = None
    versions: list[MapVersion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _pinned_exists(self) -> MapsFile:
        ids = _unique([v.id for v in self.versions], "map version")
        if self.pinned is not None and self.pinned not in ids:
            raise ValueError(f"the pinned version {self.pinned!r} is not listed")
        return self


# ── stopwords.json ───────────────────────────────────────────────────────────


class StopwordsFile(_Model):
    """``decisions/stopwords.json``: additions to and removals from the function-word lists."""

    format: Literal["cartolex-stopwords/1"] = "cartolex-stopwords/1"
    add: dict[Language, list[NonEmpty]] = Field(default_factory=dict)
    remove: dict[Language, list[NonEmpty]] = Field(default_factory=dict)
