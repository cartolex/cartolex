# SPDX-License-Identifier: MIT
"""The app itself: health, manifest, session, diagnostic, the API's description."""

from __future__ import annotations

import os
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..manifest import build_manifest, manifest_schema
from ..routing import Routes, principal_of, runtime_of

routes = Routes(tags=["app"])

#: Distributions whose versions the diagnostic lists.
KEY_DEPENDENCIES = (
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "numpy",
    "scipy",
    "pandas",
    "pyarrow",
    "scikit-learn",
    "spacy",
    "umap-learn",
    "mistralai",
)


@routes.get("/api/health", action="app.health", resource="app")
def health() -> dict[str, str]:
    """Whether the app answers (for a container's health check)."""
    return {"status": "ok"}


def _project_info(request: Request) -> dict[str, Any] | None:
    runtime = runtime_of(request)
    principal = principal_of(request)
    pid = runtime.projects.project_id(request, principal)
    if pid is None:
        return {"open": False}
    for handle in runtime.projects.handles():
        if handle.id == pid:
            return {"open": True, "id": handle.id, "name": handle.name}
    return {"open": True, "id": pid, "name": None}


@routes.get("/api/app/manifest", action="app.read", resource="app")
def manifest(request: Request) -> dict[str, Any]:
    """What the interface starts from (``cartolex-manifest/1``, ``docs/dev/app-manifest.md``)."""
    runtime = runtime_of(request)
    m = build_manifest(runtime, principal_of(request), _project_info(request))
    return m.model_dump(mode="json")


@routes.get("/api/app/about", action="app.read", resource="app")
def about_app() -> dict[str, Any]:
    """The About page: version and build, authors, licence, source, how to cite."""
    from ..about import about

    return about()


@routes.get("/api/app/manifest/schema", action="app.read", resource="app")
def manifest_json_schema() -> dict[str, Any]:
    """The JSON Schema of the manifest."""
    return manifest_schema()


@routes.get("/api/openapi.json", action="app.read", resource="app", include_in_schema=False)
def openapi(request: Request) -> JSONResponse:
    """The OpenAPI description of every route."""
    return JSONResponse(request.app.openapi())


@routes.get("/api/session", action="app.read", resource="app")
def session(request: Request) -> dict[str, Any]:
    """Who the session acts for, and the CSRF header to send with changes."""
    from ..security import CSRF_HEADER

    principal = principal_of(request)
    return {
        "principal": {"id": principal.id, "name": principal.name, "roles": sorted(principal.roles)},
        "csrf_header": CSRF_HEADER,
        "csrf_cookie": runtime_of(request).csrf_cookie,
    }


@routes.delete("/api/session", action="app.session", resource="app")
def sign_out(request: Request) -> dict[str, bool]:
    """End this browser's session."""
    runtime = runtime_of(request)
    sid = request.cookies.get(runtime.session_cookie)
    if sid:
        runtime.sessions.drop(sid)
    return {"signed_out": True}


def _version(dist: str) -> str | None:
    try:
        return version(dist)
    except PackageNotFoundError:
        return None


class PresenceBody(BaseModel):
    """A page of the interface: its own id, and whether it is closing."""

    page: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    bye: bool = False


@routes.post("/api/presence", action="app.session", resource="app")
def presence(request: Request, body: PresenceBody) -> dict[str, Any]:
    """A page says it is open, or closing (the local app stops when none is open)."""
    runtime = runtime_of(request)
    runtime.presence.ping(body.page, bye=body.bye)
    return {"pages": runtime.presence.pages()}


@routes.get("/api/diagnostic", action="app.diagnostic", resource="app")
def diagnostic(request: Request) -> dict[str, Any]:
    """Versions, the machine's size and recent job events: never project data.

    It holds no project name, path, person or text: it can be copied into a
    report as it is.
    """
    from cartolex.build.machine import available_memory_mb
    from cartolex.lexicon import language_models as lm
    from cartolex.project.project import cartolex_version

    runtime = runtime_of(request)
    models = []
    for lang in lm.supported_languages():
        spec = lm.spec(lang)
        models.append(
            {
                "language": lang,
                "model": spec.identity,
                "installed": lm.installed_version(lang),
            }
        )
    jobs = []
    for job in runtime.jobs.list()[:10]:
        events = runtime.jobs.events(job.id)
        jobs.append(
            {
                "id": job.id,
                "kind": job.kind,
                "state": job.state,
                "submitted_at": job.submitted_at,
                "finished_at": job.finished_at,
                "events": [
                    {k: v for k, v in e.items() if k in _EVENT_FIELDS} for e in events[-20:]
                ],
            }
        )
    from ..about import build_stamp

    return {
        "cartolex": cartolex_version(),
        "build": build_stamp(),
        "python": sys.version.split()[0],
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "dependencies": {d: _version(d) for d in KEY_DEPENDENCIES},
        "language_models": models,
        "machine": {
            "cpus": os.cpu_count(),
            "memory_available_mb": available_memory_mb(),
            "memory_total_mb": _total_memory_mb(),
        },
        "mode": runtime.settings.mode,
        "extensions": [e.id for e in runtime.extensions.extensions],
        "open_projects": len(runtime.projects.handles()),
        "recent_jobs": jobs,
    }


#: The fields of a job event the diagnostic keeps: stage names, counts, times, states.
_EVENT_FIELDS = frozenset(
    {
        "at",
        "event",
        "stage",
        "phase",
        "phases",
        "seconds",
        "peak_memory_mb",
        "counts",
        "outcome",
        "state",
        "kind",
        "run",
        "ran",
        "skip",
        "keep",
        "refused",
        "warnings",
        "seq",
    }
)


def _total_memory_mb() -> float | None:
    try:
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        return None
    return round(pages * size / (1024 * 1024), 1)
