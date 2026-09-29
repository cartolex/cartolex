# SPDX-License-Identifier: MIT
"""The dry run: what a build will recompute, what it keeps, and why.

:func:`plan` reads the stages' states and returns a :class:`BuildPlan`: one
:class:`PlanItem` per stage, saying whether it runs, is kept or is skipped,
the reasons, and an estimate of its time and peak memory. A stage whose
estimate exceeds the memory budget, or whose parameters are impossible for this
project, cannot run, and neither can anything that depends on it.
:func:`cartolex.build.build` runs exactly the stages the plan says will run.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from ..project.models import CodeStamp, FileInput, StageInput
from ..project.project import cartolex_version
from .fingerprints import code_fingerprint, input_files
from .machine import available_memory_mb, resident_memory_mb
from .params import ProjectSizes, Resolved
from .stages import STAGES, Estimate, Registry, Stage
from .validity import StageState, StageStatus, _status_of, _View, project_parts

if TYPE_CHECKING:
    from ..project.project import Project

__all__ = ["BuildBusy", "BuildPlan", "PlanItem", "plan"]

Action = Literal["run", "keep", "skip"]


class BuildBusy(RuntimeError):
    """A job is running a stage the build would run."""


@dataclass(frozen=True)
class PlanItem:
    """What the build will do with one stage, and why.

    ``refusal`` is an impossible parameter value known now; ``over_budget``
    says the estimated peak memory exceeds the budget; ``blocked`` says why the
    stage cannot run (its refusal, its memory, or an upstream stage that cannot
    run), before any override. ``resume`` says when a killed run will be resumed.
    """

    stage: str
    name: str
    action: Action
    state: StageState
    reasons: tuple[str, ...] = ()
    estimate: Estimate | None = None
    needs_consent: bool = False
    consent_note: str = ""
    over_budget: bool = False
    refusal: str | None = None
    blocked: str | None = None
    resume: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BuildPlan:
    """The dry run of a build: one item per stage considered, in build order."""

    items: tuple[PlanItem, ...]
    budget_mb: float | None

    def item(self, stage_id: str) -> PlanItem:
        for it in self.items:
            if it.stage == stage_id:
                return it
        raise KeyError(f"the plan does not consider {stage_id!r}")

    @property
    def to_run(self) -> tuple[str, ...]:
        """The stages the build will run (those that can)."""
        return tuple(i.stage for i in self.items if i.action == "run" and i.blocked is None)

    @property
    def to_keep(self) -> tuple[str, ...]:
        return tuple(i.stage for i in self.items if i.action == "keep")

    @property
    def to_skip(self) -> tuple[str, ...]:
        return tuple(i.stage for i in self.items if i.action == "skip")

    @property
    def blocked(self) -> dict[str, str]:
        return {i.stage: i.blocked for i in self.items if i.action == "run" and i.blocked}

    def estimate(self) -> Estimate:
        """The whole run: total time, largest peak memory."""
        run = [i.estimate for i in self.items if i.stage in self.to_run and i.estimate]
        seconds = [e.seconds for e in run]
        memory = [e.peak_memory_mb for e in run if e.peak_memory_mb is not None]
        total = None if any(s is None for s in seconds) else sum(s for s in seconds if s)
        return Estimate(total, max(memory) if memory else None, "sum of the stages")

    def describe(self) -> str:
        """The plan in words, one line per stage."""
        n_run = len(self.to_run)
        head = (
            f"{n_run} stage(s) to run, {len(self.to_keep)} up to date, "
            f"{len(self.to_skip)} skipped"
            + (f", {len(self.blocked)} cannot run" if self.blocked else "")
        )
        lines = [head]
        width = max((len(i.stage) for i in self.items), default=0)
        for i in self.items:
            if i.action == "skip":
                detail = "; ".join(i.reasons)
            elif i.action == "keep":
                detail = "up to date"
            else:
                bits = [str(i.estimate)] if i.estimate else []
                bits += list(i.reasons)
                if i.needs_consent:
                    bits.append(f"asks first: {i.consent_note}")
                if i.resume:
                    bits.append(i.resume)
                if i.blocked:
                    bits.append(f"CANNOT RUN: {i.blocked}")
                detail = "; ".join(bits)
            lines.append(f"  {i.action:<4}  {i.stage:<{width}}  {i.name}: {detail}")
        if n_run:
            lines.append(f"  total: {self.estimate()}")
        return "\n".join(lines)


# ── what a run reads ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunInputs:
    """Everything a run of a stage is computed from, and the key a resume must match."""

    resolved: Resolved
    stages: tuple[StageInput, ...]
    files: tuple[FileInput, ...]
    identity: dict[str, Any]
    code: CodeStamp
    missing_upstream: tuple[str, ...]

    @property
    def key(self) -> str:
        """A digest of the inputs, parameters and code: a killed run resumes only when it matches."""
        data = {
            "code": self.code.fingerprint,
            "stage_version": self.code.stage_version,
            "parameters": {k: v.value for k, v in sorted(self.resolved.values.items())},
            "stages": [[s.stage, s.run_id] for s in self.stages],
            "files": [[f.path, f.fingerprint] for f in self.files],
            "identity": self.identity,
        }
        text = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_inputs(view: _View, stage: Stage) -> RunInputs:
    """What a run of *stage* would read now."""
    stages: list[StageInput] = []
    missing: list[str] = []
    for up in stage.upstream:
        if view.registry[up].skip_reason(view.project.config, view.params) is not None:
            continue
        record = view.records.get(up)
        if record is None:
            missing.append(up)
        else:
            stages.append(StageInput(stage=up, run_id=record.run_id))
    files = []
    for item in input_files(view.project, stage):
        fp = view.memo(item)
        if fp is not None:
            files.append(FileInput(kind=item.kind, path=item.path, fingerprint=fp))
    return RunInputs(
        resolved=view.resolve(stage),
        stages=tuple(stages),
        files=tuple(files),
        identity=project_parts(view.project.config, stage.project),
        code=CodeStamp(
            version=cartolex_version(),
            fingerprint=code_fingerprint(),
            stage_version=stage.version,
        ),
        missing_upstream=tuple(missing),
    )


# ── the plan ─────────────────────────────────────────────────────────────────


def _wanted(registry: Registry, targets: Iterable[str] | None) -> set[str]:
    if targets is None:
        return set(registry.ids)
    wanted: set[str] = set()
    for t in targets:
        registry[t]  # raises on an unknown stage
        wanted |= {t} | registry.upstream_of(t)
    return wanted


def _why_run(st: StageStatus, forced: bool, upstream_runs: list[str]) -> list[str]:
    reasons: list[str] = []
    if forced:
        reasons.append("asked for")
    if st.state is StageState.NEVER_BUILT:
        reasons.append("never built")
    if st.attempt is not None:
        reasons.append(f"the last attempt {st.attempt.outcome}: {st.attempt.error or 'no reason'}")
    elif st.interrupted is not None:
        reasons.append("the last run was interrupted")
    reasons += [str(r) for r in st.reasons]
    reasons += [
        f"{u} will be rebuilt"
        for u in upstream_runs
        if not any(r.kind == "upstream" and r.subject == u for r in st.reasons)
    ]
    return reasons


def _mb(value: float) -> str:
    return f"{value / 1024:.1f} GB" if value >= 1024 else f"{value:.0f} MB"


#: Why an opt-in stage a build did not get consent for is skipped.
NO_CONSENT_SKIP = "skipped: no consent for this build; the stages after it run without it"


def plan(
    project: Project,
    targets: Iterable[str] | None = None,
    *,
    registry: Registry | None = None,
    force: Iterable[str] = (),
    budget_mb: float | None = None,
    year: int | None = None,
    off: Iterable[str] = (),
) -> BuildPlan:
    """What a build of *targets* (every stage by default) would run, keep and skip.

    A target brings in every stage upstream of it. A stage runs when it was
    never built, needs an update, failed, is in *force*, or an upstream stage
    runs. *budget_mb* is the peak memory a stage may reach (default: the memory
    available now, plus what this process already holds, since a stage's peak
    is measured for the whole process). A cross-check that needs a size an
    upstream stage of this build will report again waits for the run. Raises
    :class:`BuildBusy` when a job runs a stage the build would run, and
    :class:`~cartolex.build.params.ParamsError` when ``params.json`` does not
    fit the stages. The opt-in stages in *off* are skipped, as if switched off
    (a build does so with the ones its consent was refused for).
    """
    registry = registry or STAGES
    forced = set(force)
    for s in forced:
        registry[s]
    wanted = _wanted(registry, targets)
    off = frozenset(off)
    view = _View.read(project, registry, year, off)
    statuses: dict[str, StageStatus] = {}
    for stage in registry:
        statuses[stage.id] = _status_of(view, stage, statuses)
    busy = [s for s in wanted if statuses[s].state is StageState.RUNNING]
    if busy:
        raise BuildBusy(f"a job is running {', '.join(sorted(busy))}; wait for it or cancel it")
    budget = budget_mb
    if budget is None:
        available = available_memory_mb()
        budget = available + resident_memory_mb() if available is not None else None

    items: list[PlanItem] = []
    runs: set[str] = set()
    blocked: dict[str, str] = {}
    for stage in registry:
        if stage.id not in wanted:
            continue
        st = statuses[stage.id]
        if st.state is StageState.SKIPPED:
            why = NO_CONSENT_SKIP if stage.id in off and stage.opt_in else st.skip_reason or ""
            items.append(PlanItem(stage.id, stage.name, "skip", st.state, (why,)))
            continue
        upstream_runs = [u for u in stage.upstream if u in runs]
        must = st.state in (StageState.NEVER_BUILT, StageState.NEEDS_UPDATE, StageState.FAILED)
        if not (must or stage.id in forced or upstream_runs):
            items.append(PlanItem(stage.id, stage.name, "keep", st.state, ("up to date",)))
            continue
        # Sizes an upstream stage of this build will report again are not known yet.
        pending = {
            n for u in registry.upstream_of(stage.id) if u in runs for n in registry[u].provides
        }
        runs.add(stage.id)
        estimate = stage.estimate(view.sizes, st.record)
        resolved = view.resolve(stage)
        known_now = ProjectSizes(
            **{n: (None if n in pending else v) for n, v in view.sizes.as_dict().items()}
        )
        problems = resolved.problems(stage, known_now, project.config)
        refusal = "; ".join(problems) or None
        over = (
            budget is not None
            and estimate.peak_memory_mb is not None
            and estimate.peak_memory_mb > budget
        )
        inherited = next(
            (u for u in registry.ids if u in blocked and u in registry.upstream_of(stage.id)), None
        )
        reason = refusal
        if reason is None and over:
            reason = (
                f"needs about {_mb(estimate.peak_memory_mb or 0)} of memory; "
                f"the budget is {_mb(budget or 0)}"
            )
        if reason is None and inherited is not None:
            reason = f"depends on {inherited}, which cannot run"
        if reason is not None:
            blocked[stage.id] = reason
        resume = None
        folder = st.interrupted
        if folder is not None and stage.chunked and not upstream_runs:
            if folder.resumable(run_inputs(view, stage).key):
                done, total = folder.chunks_done()
                if done:
                    resume = f"resumes a killed run: {done} of {total or '?'} chunks are done"
        items.append(
            PlanItem(
                stage.id,
                stage.name,
                "run",
                st.state,
                tuple(_why_run(st, stage.id in forced, upstream_runs)),
                estimate=estimate,
                needs_consent=stage.needs_consent,
                consent_note=stage.consent_note,
                over_budget=over,
                refusal=refusal,
                blocked=reason,
                resume=resume,
                parameters=resolved.plain(),
            )
        )
    return BuildPlan(tuple(items), budget)
