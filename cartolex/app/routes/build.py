# SPDX-License-Identifier: MIT
"""Building: the dry run with its estimate and consent requests, then a build job; the tracker."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from fastapi import Request, Response
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..jobs import JobConflict, JobControl
from ..messages import empty
from ..routing import Routes, runtime_of
from .state import AREAS

routes = Routes(tags=["build"])

StageOrArea = Annotated[str, Field(pattern=r"^[a-z][a-z._-]{0,63}$")]
AiStepId = Literal["keywords.triage", "themes.curation"]
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
    that ask consent only when their id is in ``consent``. A build pauses at an
    AI step whose route is a copilot (:mod:`cartolex.app.ai_steps`), except at
    the steps in ``continue`` (« continue the build »).
    """

    scope: Annotated[list[StageOrArea], Field(max_length=32)] | None = None
    options: BuildOptions = BuildOptions()
    dry_run: bool = True
    consent: Annotated[list[StageOrArea], Field(max_length=32)] = []
    go_on: Annotated[list[AiStepId], Field(max_length=4, alias="continue")] = []


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


def ai_tokens(runtime: Any, ctx: Any, the_plan: Any, stage: str) -> dict[str, int] | None:
    """About how many tokens a paid stage sends and receives (``in``, ``out``, ``calls``; an
    upper bound, answers already paid for left out); unknown when the extraction runs again
    first or for another stage."""
    from ..ai_usage import TRIAGE_STAGE, triage_estimate

    if runtime is None or ctx is None or stage != TRIAGE_STAGE:
        return None
    if "keywords.extract" in the_plan.to_run:
        return None
    e = triage_estimate(runtime, ctx)
    return (
        None if e is None else {"in": e["tokens_in"], "out": e["tokens_out"], "calls": e["calls"]}
    )


def plan_json(
    the_plan: Any,
    registry: Any,
    ctx: Any = None,
    pause: dict[str, Any] | None = None,
    runtime: Any = None,
) -> dict[str, Any]:
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
                    "ai_tokens": ai_tokens(runtime, ctx, the_plan, i.stage) if stage.paid else None,
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
        "pause": pause,
    }


def ai_view(runtime: Any, ctx: Any) -> dict[str, Any]:
    """The route of each AI step (``none``, ``copilot``, ``api``), the routes each can take,
    whether the API is ready (a key and a provider), and the version of ``params.json``."""
    from cartolex.project.models import AI_ROUTES

    from ..ai_steps import routes_of

    params, fp = ctx.project.read_params()
    ai = runtime.ai_access()
    ready = bool(ai is not None and (ai.api_key or ai.client_factory))
    from .state import triage_status

    return {
        "routes": routes_of(params),
        "choices": {step: list(routes) for step, routes in AI_ROUTES.items()},
        "api_ready": ready and ctx.project.config.identity.ai is not None,
        "version": version_of(fp),
        "triage": triage_status(runtime, ctx.project),
    }


def preflight_notes(
    runtime: Any, ctx: Any, to_run: list[str], ai: dict[str, Any]
) -> list[dict[str, Any]]:
    """What the pre-flight sheet says before a build, each a message with its next action:
    mapped people whose texts were never collected (when the texts are gathered again), a
    copilot's result imported and not accepted, the API's verdicts a route other than the
    API leaves aside (when the vocabulary is built again)."""
    from ..guidance import people_facts
    from .overview import item, review_item

    out = []
    if "corpus.assemble" in to_run:
        try:
            people = people_facts(runtime, ctx.project)
        except Exception:  # a note: the sheet shows without it
            people = {"to_harvest": 0}
        if people["to_harvest"]:
            out.append(item("preflight_no_texts", level="warning", n=people["to_harvest"]))
    triage = ai["triage"]
    if "keywords.build" in to_run and triage["pending"]:
        out.append(review_item("preflight_copilot_pending", triage["pending"][0]))
    if "keywords.build" in to_run and triage["api_verdicts"] and triage["route"] != "api":
        out.append(item("preflight_api_dropped", level="warning"))
    return out


def pause_for(runtime: Any, ctx: Any, the_plan: Any, passed: list[str]) -> dict[str, Any] | None:
    """Where a build of *the_plan* pauses for a copilot (:func:`cartolex.app.ai_steps.pause_of`)."""
    from ..ai_steps import pause_of, routes_of

    params, _ = ctx.project.read_params()
    return pause_of(ctx.project, runtime.registry, the_plan, routes_of(params), set(passed))


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
    passed: list[str] | None = None,
) -> dict[str, Any]:
    """Submit a build of *targets* as a job; 409 when a job runs on the project.

    With *passed* (a list, the build page's builds), the build pauses at an AI step
    whose route is a copilot, except the steps in it: it runs what comes before the
    step and ends ``waiting``, its result naming the pause (``waiting``).
    """
    from cartolex.build import plan

    from ..build_run import child_recipe, run_build

    project, registry = ctx.project, runtime.registry
    settings = runtime.settings
    recipe = child_recipe(runtime)
    memory_mb = runtime.budget.budget().memory_mb

    def work(control: JobControl) -> dict[str, Any]:
        run_targets, pause = targets, None
        if passed is not None:
            the_plan = plan(
                project,
                targets,
                registry=registry,
                force=force,
                budget_mb=settings.build_budget_mb,
                year=settings.build_year,
                memory_mb=memory_mb,
            )
            pause = pause_for(runtime, ctx, the_plan, passed)
            if pause is not None:
                run_targets = [s for s in the_plan.to_run if s not in pause["held"]]
                control.event("waiting", **pause)
        if pause is not None and not run_targets:
            return {
                "outcome": "waiting",
                "summary": "waiting for a copilot",
                "ran": [],
                "refused": {},
                "not_run": [],
                "changed": False,
                "waiting": pause,
            }
        out = run_build(
            project,
            recipe,
            control,
            registry=registry,
            targets=run_targets,
            consent=list(consent),
            force=[s for s in force if run_targets is None or s in run_targets],
            budget_mb=settings.build_budget_mb,
            allow_over_budget=allow_over_budget,
            heartbeat_s=settings.heartbeat_s,
            year=settings.build_year,
            job_id=control.job_id,
            memory_mb=memory_mb,
        )
        if pause is not None and out["outcome"] == "succeeded":
            out["outcome"], out["waiting"] = "waiting", pause
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
            memory_mb=runtime.budget.budget().memory_mb,
        )
        pause = pause_for(runtime, ctx, the_plan, list(body.go_on))
        out = plan_json(the_plan, runtime.registry, ctx, pause, runtime)
        out["running"] = running.as_dict() if running else None
        out["ai"] = ai_view(runtime, ctx)
        out["notes"] = preflight_notes(runtime, ctx, out["to_run"], out["ai"])
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
        passed=list(body.go_on),
    )
    return JSONResponse(started, status_code=202)


class AiRoutesBody(BaseModel):
    """The route of each AI step: ``none``, ``copilot`` or ``api`` (the keyword clean-up
    only). A step left out keeps its route."""

    model_config = {"populate_by_name": True}

    keywords_triage: Literal["none", "copilot", "api"] | None = Field(None, alias="keywords.triage")
    themes_curation: Literal["none", "copilot"] | None = Field(None, alias="themes.curation")


@routes.put("/api/build/ai", action="params.write")
def put_ai_routes(
    request: Request, response: Response, body: AiRoutesBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Choose the route of the AI steps, kept in ``params.json`` (``ai``; the clean-up by API
    switches ``keywords.triage`` on); ``If-Match`` with the version of ``params.json``."""
    from ..ai_steps import with_routes

    runtime = runtime_of(request)
    expected = expected_version(request)
    chosen = {k: v for k, v in body.model_dump(by_alias=True).items() if v is not None}
    with ctx.handle.mutex:
        check_version(ctx.layout.params_json, expected)
        params, _ = ctx.project.read_params()
        updated = with_routes(params, chosen)
        if updated != params:
            said = ", ".join(f"{k}={v}" for k, v in sorted(chosen.items()))
            ctx.project.save_params(updated, expected=expected, action=f"AI route {said}")
    view = ai_view(runtime, ctx)
    response.headers["ETag"] = etag_of(view["version"])
    return view


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


def last_build(runtime: Any, ctx: Any) -> Any:
    """The running build job, or the last one (from this process, else from the logs)."""
    from ..jobs import read_job_logs

    # The logs first, then the runner: a build submitted meanwhile is live, not interrupted.
    logs = read_job_logs(ctx.layout.jobs, ctx.id, limit=1, kind="build")
    jobs = [j for j in runtime.jobs.list(ctx.id) if j.kind == "build"] or logs
    return jobs[0] if jobs else None


@routes.get("/api/build", action="build.read")
def get_build(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """The tracker: the running build, or the last one: stage, progress, ETA, what changed."""
    last = last_build(runtime_of(request), ctx)
    if last is None:
        return {
            "job": None,
            "empty": empty("empty_nothing_built"),
        }
    job = last.as_dict()
    return {"job": job, **tracker(ctx, job)}
