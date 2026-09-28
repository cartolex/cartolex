# SPDX-License-Identifier: MIT
"""Jobs of the open project: list, one job, its events, cancel."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Query, Request

from ..deps import ProjectDep
from ..errors import ApiError
from ..jobs import read_job_logs
from ..routing import Routes, runtime_of
from .build import JOB_ID, job_events

routes = Routes(tags=["jobs"])


def _jobs(request: Request, ctx: Any) -> list[dict[str, Any]]:
    runtime = runtime_of(request)
    live = {j.id: j for j in runtime.jobs.list(ctx.id)}
    past = [j for j in read_job_logs(ctx.layout.jobs, ctx.id, limit=20) if j.id not in live]
    jobs = sorted([*live.values(), *past], key=lambda j: j.id, reverse=True)
    return [j.as_dict() for j in jobs[:30]]


@routes.get("/api/jobs", action="jobs.read")
def list_jobs(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """The project's jobs, newest first: this app's, and earlier ones from their logs.

    A job whose log has no end and whose process is gone is ``interrupted``.
    """
    jobs = _jobs(request, ctx)
    return {
        "jobs": jobs,
        "total": len(jobs),
        "empty": None
        if jobs
        else {"message": "no job has run yet", "next": {"label": "Build", "action": "build"}},
    }


def _find(request: Request, ctx: Any, job_id: str) -> dict[str, Any]:
    if not JOB_ID.match(job_id):
        raise ApiError(404, "not_found", "no such job")
    for job in _jobs(request, ctx):
        if job["id"] == job_id:
            return job
    past = [j for j in read_job_logs(ctx.layout.jobs, ctx.id, limit=500) if j.id == job_id]
    if past:
        return past[0].as_dict()
    raise ApiError(404, "not_found", "no such job", next_action="reload")


@routes.get("/api/jobs/{job_id}", action="jobs.read", id_param="job_id")
def get_job(request: Request, job_id: str, ctx: ProjectDep) -> dict[str, Any]:
    """One job: its state, progress and result."""
    return _find(request, ctx, job_id)


@routes.get("/api/jobs/{job_id}/events", action="jobs.read", id_param="job_id")
def get_events(
    request: Request,
    job_id: str,
    ctx: ProjectDep,
    after: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """The events of a job after *after* (stage names, counts and times only)."""
    _find(request, ctx, job_id)
    events = job_events(ctx, job_id, after)
    return {"events": events, "next": (events[-1]["seq"] + 1) if events else after}


@routes.post("/api/jobs/{job_id}/cancel", action="jobs.cancel", id_param="job_id")
def cancel_job(request: Request, job_id: str, ctx: ProjectDep) -> dict[str, Any]:
    """Ask a job to stop at its next safe point; it ends in « nothing changed » or « finished
    before the cancel »."""
    runtime = runtime_of(request)
    job = _find(request, ctx, job_id)
    if job["state"] not in ("queued", "running", "cancelling"):
        raise ApiError(409, "not_running", f"the job has already ended ({job['state']})")
    info = runtime.jobs.cancel(job_id)
    if info is None:
        raise ApiError(
            409,
            "not_here",
            "this job runs in another process; stop it there",
            next_action="none",
        )
    return info.as_dict()
