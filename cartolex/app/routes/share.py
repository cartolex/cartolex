# SPDX-License-Identifier: MIT
"""Sharing: the offline sites built so far, and starting a build (not available yet)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from ..deps import ProjectDep
from ..errors import ApiError
from ..jobs import JobConflict, JobControl
from ..messages import empty
from ..routing import Routes, runtime_of
from .build import busy_error

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
        else empty("empty_no_site" if builder.available else "empty_no_site_unavailable"),
    }


@routes.post("/api/share/builds", action="share.build")
def start(request: Request, ctx: ProjectDep) -> JSONResponse:
    """Start building the offline site (a job)."""
    runtime = runtime_of(request)
    builder = runtime.site_builder
    if not builder.available:
        raise ApiError.of("not_available")
    project = ctx.project

    def work(control: JobControl) -> dict[str, Any]:
        return dict(builder.build(project, {}, control))

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="site",
            work=work,
            title="build the site",
            title_code="build_site",
        )
    except JobConflict as exc:
        raise busy_error(exc.running) from exc
    return JSONResponse({"job": info.as_dict()}, status_code=202)
