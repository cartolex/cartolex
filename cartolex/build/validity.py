# SPDX-License-Identifier: MIT
"""Is a stage's result up to date? The six states, computed from the records alone.

A stage is in exactly one :class:`StageState`. The state is computed from the
stage's ``run.json``, its last failed attempt, its staging folders and the
current fingerprints of what it reads: never from file dates. A stage that needs
an update says why, with one :class:`Reason` per change: an input file, a
parameter, a part of ``project.json``, or an upstream stage.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from ..project.models import ParamsFile, ProjectFile, RunRecord
from ..project.tables import read_decision_csv
from .fingerprints import FingerprintMemo, code_fingerprint, input_files
from .params import ParamsError, ProjectSizes, Resolved, check_params, resolve_params
from .records import StagingFolder, read_attempt, read_record, scan_staging
from .stages import STAGES, Registry, Stage

if TYPE_CHECKING:
    from ..project.project import Project

__all__ = [
    "Reason",
    "StageState",
    "StageStatus",
    "current_sizes",
    "effective_params",
    "load_params",
    "project_parts",
    "status",
]


class StageState(str, Enum):
    """The six states of a stage."""

    NEVER_BUILT = "never built"
    UP_TO_DATE = "up to date"
    NEEDS_UPDATE = "needs update"
    RUNNING = "running"
    FAILED = "failed"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Reason:
    """One thing that changed since a stage's results were computed."""

    kind: Literal["code", "input", "parameter", "project", "upstream"]
    subject: str  # the stage id, path, parameter name, project.json part or upstream stage id
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class StageStatus:
    """The state of one stage and everything it was computed from.

    ``record`` is the run that produced the current results; ``attempt`` the
    last failed or cancelled attempt; ``interrupted`` the staging folder a
    killed run left (a new run resumes from it when nothing changed);
    ``running`` the staging folder of the job running the stage now.
    ``code_changed`` says the results were computed by other code than this
    one; it is information, not a reason for an update (a deliberate change of
    what the stage produces raises its version, which is one).
    """

    stage: str
    name: str
    state: StageState
    reasons: tuple[Reason, ...] = ()
    record: RunRecord | None = None
    attempt: RunRecord | None = None
    interrupted: StagingFolder | None = None
    running: StagingFolder | None = None
    skip_reason: str | None = None
    code_changed: bool = False

    @property
    def has_results(self) -> bool:
        """Whether results are in place (usable even when failed or out of date)."""
        return self.record is not None

    def describe(self) -> str:
        """The state in words, with its reasons."""
        text = f"{self.stage} ({self.name}): {self.state}"
        if self.state is StageState.SKIPPED and self.skip_reason:
            return f"{text}, {self.skip_reason}"
        extra: list[str] = []
        if self.attempt is not None:
            extra.append(
                f"last attempt {self.attempt.outcome}: {self.attempt.error or 'no reason'}"
            )
        if self.interrupted is not None and self.running is None:
            extra.append("a run was interrupted")
        extra += [str(r) for r in self.reasons]
        return text + ("; " + "; ".join(extra) if extra else "")


def load_params(project: Project, registry: Registry) -> ParamsFile:
    """``decisions/params.json``, refused with every reason when it does not fit *registry*."""
    try:
        params, _ = project.read_params()
    except ValueError as exc:
        raise ParamsError([str(exc)]) from exc
    problems = check_params(params, registry)
    if problems:
        raise ParamsError(problems)
    return params


def project_parts(config: ProjectFile, parts: tuple[str, ...]) -> dict[str, Any]:
    """The values of the named parts of ``project.json``, keyed as in a run's ``identity``.

    ``identity.language_models`` is recorded as ``language_models``.
    """
    data = config.model_dump(mode="json", by_alias=True)
    out: dict[str, Any] = {}
    for part in parts:
        value: Any = data
        for key in part.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        out[part.removeprefix("identity.")] = value
    return out


def _this_year() -> int:
    return _dt.date.today().year


def _source_sizes(project: Project, want: set[str]) -> dict[str, int | None]:
    """Sizes estimated from the sources, before any stage has reported them."""
    import pyarrow.parquet as pq

    layout = project.layout
    out: dict[str, int | None] = {}

    def rows(name: str) -> int | None:
        path = layout.table(name)
        return pq.read_metadata(path).num_rows if path.exists() else None

    if "texts" in want:
        out["texts"] = rows("texts")
    if "characters" in want:
        path = layout.table("text_parts")
        if path.exists():
            meta = pq.read_metadata(path)
            total = 0
            for i in range(meta.num_row_groups):
                group = meta.row_group(i)
                for j in range(group.num_columns):
                    column = group.column(j)
                    if column.path_in_schema == "content":
                        total += column.total_uncompressed_size
            out["characters"] = total
    if want & {"people", "mapped_units"}:
        roles = [r["role"] for r in read_decision_csv(layout.people_csv, "people")]
        everyone = rows("people")
        if roles:
            out["people"] = sum(r in ("mapped", "context") for r in roles)
            out["mapped_units"] = sum(r == "mapped" for r in roles)
        else:
            out["people"] = out["mapped_units"] = everyone
    return {k: v for k, v in out.items() if k in want}


def current_sizes(
    project: Project, registry: Registry, records: dict[str, RunRecord | None]
) -> ProjectSizes:
    """The project's sizes: as the stages last reported them, else estimated from the sources."""
    reported: dict[str, int | None] = {}
    for stage in registry:
        record = records.get(stage.id)
        if record is None:
            continue
        for name in stage.provides:
            if name in record.measures.counts:
                reported[name] = record.measures.counts[name]
    sizes = ProjectSizes().merged(reported)
    missing = {n for n in ("people", "texts", "characters", "mapped_units") if sizes.get(n) is None}
    if missing:
        sizes = sizes.merged(_source_sizes(project, missing))
    return sizes


@dataclass
class _View:
    """What one status check reads once and shares between stages."""

    project: Project
    registry: Registry
    params: ParamsFile
    year: int
    records: dict[str, RunRecord | None]
    attempts: dict[str, RunRecord | None]
    staging: dict[str, list[StagingFolder]]
    sizes: ProjectSizes
    memo: FingerprintMemo = field(default_factory=FingerprintMemo)
    code: str = field(default_factory=code_fingerprint)

    @classmethod
    def read(cls, project: Project, registry: Registry, year: int | None) -> _View:
        layout = project.layout
        params = load_params(project, registry)
        records = {s.id: read_record(layout, s.id) for s in registry}
        return cls(
            project=project,
            registry=registry,
            params=params,
            year=year if year is not None else _this_year(),
            records=records,
            attempts={s.id: read_attempt(layout, s.id) for s in registry},
            staging=scan_staging(layout),
            sizes=current_sizes(project, registry, records),
        )

    def resolve(self, stage: Stage) -> Resolved:
        return resolve_params(stage, self.params, self.sizes, year=self.year)


def _origin(pv: Any) -> str:
    if pv.source == "rule":
        return f"rule {pv.rule}"
    return "from params.json" if pv.source == "params.json" else "default"


def _changes(
    view: _View, stage: Stage, record: RunRecord, earlier: dict[str, StageStatus]
) -> list[Reason]:
    reasons: list[Reason] = []
    before = record.code.stage_version
    if before != stage.version:
        reasons.append(
            Reason(
                "code",
                stage.id,
                f"cartolex changed how this stage works (version {before} → {stage.version})",
            )
        )
    used = {i.stage: i.run_id for i in record.inputs if i.kind == "stage"}
    for up in stage.upstream:
        up_status = earlier[up]
        if up_status.state is StageState.SKIPPED:
            if up in used:
                reasons.append(Reason("upstream", up, f"{up} is now skipped"))
            continue
        current = view.records[up].run_id if view.records[up] is not None else None
        if current is None:
            reasons.append(Reason("upstream", up, f"{up} has no results"))
            continue
        if up not in used:
            reasons.append(Reason("upstream", up, f"{up} now has results (run {current})"))
        elif used[up] != current:
            reasons.append(Reason("upstream", up, f"{up} was rebuilt (run {used[up]} → {current})"))
        if up_status.state is StageState.RUNNING:
            reasons.append(Reason("upstream", up, f"{up} is running"))
        elif up_status.state is StageState.FAILED:
            reasons.append(Reason("upstream", up, f"{up} failed and will run again"))
        elif up_status.state is StageState.NEEDS_UPDATE:
            reasons.append(Reason("upstream", up, f"{up} needs an update"))

    recorded = {i.path: i.fingerprint for i in record.inputs if i.kind != "stage"}
    for item in input_files(view.project, stage):
        now, then = view.memo(item), recorded.get(item.path)
        if now == then:
            continue
        if then is None:
            reasons.append(Reason("input", item.path, f"{item.path} was added"))
        elif now is None:
            reasons.append(Reason("input", item.path, f"{item.path} was removed"))
        else:
            reasons.append(Reason("input", item.path, f"{item.path} changed"))

    for part, value in project_parts(view.project.config, stage.project).items():
        if record.identity.get(part) != value:
            reasons.append(Reason("project", part, f"project.json: {part} changed"))

    resolved = view.resolve(stage)
    for name, pv in resolved.values.items():
        before = record.parameters.get(name)
        if name in resolved.unknown:
            waits = ", ".join(s.replace("_", " ") for s in resolved.unknown[name])
            reasons.append(Reason("parameter", name, f"parameter {name} waits for {waits}"))
        elif before is None:
            reasons.append(
                Reason("parameter", name, f"parameter {name} is new: {pv.value!r} ({_origin(pv)})")
            )
        elif before.value != pv.value:
            reasons.append(
                Reason(
                    "parameter",
                    name,
                    f"parameter {name}: {before.value!r} → {pv.value!r} ({_origin(pv)})",
                )
            )
    for name in sorted(set(record.parameters) - set(resolved.values)):
        reasons.append(Reason("parameter", name, f"parameter {name} is no longer used"))
    return reasons


def _status_of(view: _View, stage: Stage, earlier: dict[str, StageStatus]) -> StageStatus:
    record = view.records[stage.id]
    attempt = view.attempts[stage.id]
    folders = view.staging.get(stage.id, [])
    running = next((f for f in folders if f.running), None)
    interrupted = next((f for f in folders if not f.running), None)
    code_changed = record is not None and record.code.fingerprint != view.code
    common = dict(
        stage=stage.id,
        name=stage.name,
        record=record,
        attempt=attempt,
        interrupted=interrupted,
        running=running,
        code_changed=code_changed,
    )
    skip = stage.skip_reason(view.project.config, view.params)
    if running is None and skip is not None:
        return StageStatus(state=StageState.SKIPPED, skip_reason=skip, **common)  # type: ignore[arg-type]
    reasons = tuple(_changes(view, stage, record, earlier)) if record is not None else ()
    if running is not None:
        state = StageState.RUNNING
    elif attempt is not None or interrupted is not None:
        state = StageState.FAILED
    elif record is None:
        state = StageState.NEVER_BUILT
    elif reasons:
        state = StageState.NEEDS_UPDATE
    else:
        state = StageState.UP_TO_DATE
    return StageStatus(state=state, reasons=reasons, skip_reason=skip, **common)  # type: ignore[arg-type]


def effective_params(
    project: Project, registry: Registry | None = None, *, year: int | None = None
) -> dict[str, Resolved]:
    """Every stage's effective parameters now, and where each comes from.

    Rule values use the sizes the stages last reported (else the sources' estimates);
    a rule whose sizes nobody knows yet is listed in :attr:`Resolved.unknown`.
    """
    view = _View.read(project, registry or STAGES, year)
    return {stage.id: view.resolve(stage) for stage in view.registry}


def status(
    project: Project, registry: Registry | None = None, *, year: int | None = None
) -> dict[str, StageStatus]:
    """The state of every stage of *registry* (cartolex's stages by default), in order.

    *year* is the current year used by stages whose ``params.json`` pins none
    (default: today's). Raises :class:`~cartolex.build.params.ParamsError` when
    ``params.json`` does not fit the stages.
    """
    view = _View.read(project, registry or STAGES, year)
    out: dict[str, StageStatus] = {}
    for stage in view.registry:
        out[stage.id] = _status_of(view, stage, out)
    return out
