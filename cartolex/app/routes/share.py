# SPDX-License-Identifier: MIT
"""Sharing: the offline sites built so far, and starting a build (not available yet)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from ..deps import ProjectDep
from ..errors import ApiError
from ..jobs import JobConflict, JobControl
from ..routing import Routes, runtime_of

routes = Routes(tags=["share"])


@routes.get("/api/share", action="share.read")
def builds(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """The site builds in ``outputs/sites/``, the newest first, and whether building works."""
    builder = runtime_of(request).site_builder
    items = builder.builds(ctx.project)
    return {
        "available": builder.available,
        "items": items,
        "total": len(items),
        "empty": None
        if items
        else {
            "message": "no site built yet"
            + ("" if builder.available else "; building a site comes in a later version"),
            "next": {"label": "Close", "action": "none"},
        },
    }


@routes.post("/api/share/builds", action="share.build")
def start(request: Request, ctx: ProjectDep) -> JSONResponse:
    """Start building the offline site (a job)."""
    runtime = runtime_of(request)
    builder = runtime.site_builder
    if not builder.available:
        raise ApiError(
            501,
            "not_available",
            "building the offline site is not available in this version",
            next_action="none",
        )
    project = ctx.project

    def work(control: JobControl) -> dict[str, Any]:
        return dict(builder.build(project, {}, control))

    try:
        info = runtime.jobs.submit(
            project=ctx.id, jobs_dir=ctx.layout.jobs, kind="site", work=work, title="build the site"
        )
    except JobConflict as exc:
        raise ApiError(409, "busy", str(exc), next_action="wait", job=exc.running.id) from exc
    return JSONResponse({"job": info.as_dict()}, status_code=202)
