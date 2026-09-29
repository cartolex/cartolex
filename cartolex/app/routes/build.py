# SPDX-License-Identifier: MIT
"""Building: the dry run with its estimate and consent requests, then a build job; the tracker."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from fastapi import Request
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..jobs import JobConflict, JobControl
from ..messages import attempt_message, empty
from ..routing import Routes, runtime_of
from .state import AREAS

routes = Routes(tags=["build"])

StageOrArea = Annotated[str, Field(pattern=r"^[a-z][a-z._-]{0,63}$")]
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class BuildOptions(BaseModel):
    """``force``: stages to run even when up to date; ``allow_over_budget``: run past the memory
    estimate."""

    force: Annotated[list[StageOrArea], Field(max_length=32)] = []
    allow_over_budget: bool = False


class BuildBody(BaseModel):
    """What to build: stage ids or areas (``keywords``, ``themes``…), every stage by default.

    ``dry_run`` (the default) answers with the plan, its estimate and the
    consent requests; ``dry_run: false`` starts a job, running the stages
    that ask consent only when their id is in ``consent``.
    """

    scope: Annotated[list[StageOrArea], Field(max_length=32)] | None = None
    options: BuildOptions = BuildOptions()
    dry_run: bool = True
    consent: Annotated[list[StageOrArea], Field(max_length=32)] = []


def busy_error(running: Any) -> ApiError:
    """409: a job runs on the project (it is named)."""
    return ApiError.of("busy", kind=running.kind, job=running.id, extra={"job": running.id})


def targets_of(runtime: Any, scope: list[str] | None) -> list[str] | None:
    """Stage ids of a scope: stage ids as they are, an area as its stages."""
    if not scope or scope == ["all"]:
        return None
    areas = {a: stages for a, _, stages in AREAS}
    out: list[str] = []
    for item in scope:
        if item in runtime.registry:
            out.append(item)
        elif item in areas and areas[item]:
            out.extend(s for s in areas[item] if s in runtime.registry)
        else:
            raise ApiError.of("unknown_scope", item=item, stages=list(runtime.registry.ids))
    return out


def _estimate(e: Any) -> dict[str, Any] | None:
    if e is None:
        return None
    return {"seconds": e.seconds, "peak_memory_mb": e.peak_memory_mb, "basis": e.basis}


#: Terms per AI call of the clean-up (the engine's ``llm_batch_size``).
AI_BATCH = 150
#: What to do after a failed stage, by the code of its attempt.
FAILED_NEXT = {
    "language_model_missing": ("Open the settings", "settings"),
    "stage_refused": ("Open the settings", "settings"),
    "stage_failed": ("Copy a diagnostic", "report"),
}


def ai_calls(ctx: Any, the_plan: Any, stage: str) -> int | None:
    """At most how many AI calls a paid stage makes: the candidates of the last extraction
    in batches (answers already paid for are reused, so it is an upper bound); unknown
    when the extraction runs again first."""
    from cartolex.build.records import read_record

    if ctx is None or "keywords.extract" in the_plan.to_run or not stage.startswith("keywords."):
        return None
    record = read_record(ctx.layout, "keywords.extract")
    n = record.measures.counts.get("candidates") if record else None
    return -(-int(n) // AI_BATCH) if n else None


def plan_json(the_plan: Any, registry: Any, ctx: Any = None) -> dict[str, Any]:
    items = [
        {
            "stage": i.stage,
            "name": i.name,
            "action": i.action,
            "state": str(i.state.value).replace(" ", "_"),
            "reasons": list(i.reasons),
            "estimate": _estimate(i.estimate),
            "needs_consent": i.needs_consent,
            "consent_note": i.consent_note,
            "over_budget": i.over_budget,
            "refusal": i.refusal,
            "blocked": i.blocked,
            "resume": i.resume,
            "parameters": i.parameters,
        }
        for i in the_plan.items
    ]
    consent = []
    for i in the_plan.items:
        if i.action == "run" and i.needs_consent and not i.blocked:
            stage = registry[i.stage]
            consent.append(
                {
                    "stage": i.stage,
                    "name": i.name,
                    "network": stage.network,
                    "paid": stage.paid,
                    # without consent, an opt-in stage is skipped and the later stages run
                    "skipped_without": stage.opt_in,
                    "note": stage.consent_note,
                    "estimate": _estimate(i.estimate),
                    "ai_calls_max": ai_calls(ctx, the_plan, i.stage) if stage.paid else None,
                }
            )
    return {
        "items": items,
        "to_run": list(the_plan.to_run),
        "to_keep": list(the_plan.to_keep),
        "to_skip": list(the_plan.to_skip),
        "blocked": the_plan.blocked,
        "estimate": _estimate(the_plan.estimate()),
        "budget_mb": the_plan.budget_mb,
        "consent": consent,
        "describe": the_plan.describe(),
    }


def progress_json(event: Any) -> dict[str, Any]:
    """A build's progress event for the tracker, with an estimate of the time left."""
    eta = None
    if event.fraction >= 0.02 and event.elapsed_s > 0:
        eta = round(event.elapsed_s * (1 - event.fraction) / event.fraction, 1)
    return {
        "phase": event.phase,
        "phases": event.phases,
        "stage": event.stage,
        "name": event.name,
        "stage_fraction": event.stage_fraction,
        "fraction": event.fraction,
        "message": event.message,
        "elapsed_s": event.elapsed_s,
        "eta_s": eta,
        "heartbeat": event.heartbeat,
    }


def start_build_job(
    runtime: Any,
    ctx: Any,
    targets: list[str] | None,
    *,
    force: list[str] = (),  # type: ignore[assignment]
    allow_over_budget: bool = False,
    consent: list[str] = (),  # type: ignore[assignment]
    title: str = "build",
    title_code: str = "build",
) -> dict[str, Any]:
    """Submit a build of *targets* as a job; 409 when a job runs on the project."""
    from cartolex.build import build

    project, registry = ctx.project, runtime.registry
    settings = runtime.settings
    accepted = set(consent)

    def work(control: JobControl) -> dict[str, Any]:
        result = build(
            project,
            targets,
            registry=registry,
            force=force,
            budget_mb=settings.build_budget_mb,
            allow_over_budget=allow_over_budget,
            consent=lambda request: request.stage in accepted,
            progress=lambda event: control.progress(progress_json(event)),
            cancel=control.cancel,
            heartbeat_s=settings.heartbeat_s,
            year=settings.build_year,
            job_id=control.job_id,
        )
        out: dict[str, Any] = {
            "outcome": result.outcome,
            "summary": result.summary(),
            "ran": list(result.ran_ids),
            "refused": dict(result.refused),
            "not_run": list(result.not_run),
            "changed": result.changed,
        }
        if result.failed:
            said = attempt_message("failed", result.failed[1])
            label, action = FAILED_NEXT.get(said["code"], FAILED_NEXT["stage_failed"])
            out["failed"] = {
                "stage": result.failed[0],
                "error": result.failed[1],
                **said,
                "next": {"label": label, "action": action},
            }
            out["error"] = f"{result.failed[0]}: {result.failed[1]}"
        return out

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="build",
            work=work,
            title=title,
            title_code=title_code,
        )
    except JobConflict as exc:
        raise busy_error(exc.running) from exc
    return {"job": info.as_dict()}


@routes.post("/api/build", action="build.start")
def post_build(request: Request, body: BuildBody, ctx: ProjectDep) -> Any:
    """The dry run (estimate, consent requests), or a build job (``dry_run: false``)."""
    from fastapi.responses import JSONResponse

    from cartolex.build import plan

    runtime = runtime_of(request)
    targets = targets_of(runtime, body.scope)
    force = targets_of(runtime, body.options.force) or []
    if body.dry_run:
        running = runtime.jobs.running(ctx.id)
        the_plan = plan(
            ctx.project,
            targets,
            registry=runtime.registry,
            force=force,
            budget_mb=runtime.settings.build_budget_mb,
            year=runtime.settings.build_year,
        )
        out = plan_json(the_plan, runtime.registry, ctx)
        out["running"] = running.as_dict() if running else None
        if not out["to_run"]:
            out["empty"] = empty("empty_up_to_date")
        return out
    started = start_build_job(
        runtime,
        ctx,
        targets,
        force=force,
        allow_over_budget=body.options.allow_over_budget,
        consent=list(body.consent),
    )
    return JSONResponse(started, status_code=202)


def job_events(ctx: Any, job_id: str, after: int = 0) -> list[dict[str, Any]]:
    """The events of a job from its log, ``logs/jobs/<job id>.jsonl``, numbered from 0."""
    if not JOB_ID.match(job_id):
        return []
    path = ctx.layout.jobs / f"{job_id}.jsonl"
    if not path.is_file():
        return []
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if i < after:
            continue
        try:
            out.append({**json.loads(line), "seq": i})
        except ValueError:
            continue
    return out


def tracker(ctx: Any, job: dict[str, Any]) -> dict[str, Any]:
    """Each stage of a build job: done, running or waiting, from its events."""
    events = job_events(ctx, job["id"])
    start = next((e for e in events if e.get("event") == "start"), None)
    order = list(start.get("run", [])) if start else []
    done = {e["stage"]: e for e in events if e.get("event") == "stage-end"}
    current = next((e.get("stage") for e in reversed(events) if e.get("event") == "phase"), None)
    stages = []
    for stage in order:
        if stage in done:
            state = "done"
        elif stage == current and job["state"] in ("running", "cancelling"):
            state = "running"
        elif stage == current:
            state = "stopped"
        else:
            state = "waiting"
        end = done.get(stage) or {}
        stages.append(
            {
                "stage": stage,
                "state": state,
                "seconds": end.get("seconds"),
                "counts": end.get("counts"),
            }
        )
    return {
        "stages": stages,
        "refused": start.get("refused", []) if start else [],
        "kept": start.get("keep", []) if start else [],
        "skipped": start.get("skip", []) if start else [],
    }


@routes.get("/api/build", action="build.read")
def get_build(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """The tracker: the running build, or the last one: stage, progress, ETA, what changed."""
    from ..jobs import read_job_logs

    runtime = runtime_of(request)
    jobs = [j for j in runtime.jobs.list(ctx.id) if j.kind == "build"]
    if not jobs:
        jobs = [j for j in read_job_logs(ctx.layout.jobs, ctx.id, limit=5) if j.kind == "build"]
    if not jobs:
        return {
            "job": None,
            "empty": empty("empty_nothing_built"),
        }
    job = jobs[0].as_dict()
    return {"job": job, **tracker(ctx, job)}
