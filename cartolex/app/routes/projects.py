# SPDX-License-Identifier: MIT
"""Which project the app works on: the current one, open, close, recent, create."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Request
from pydantic import BaseModel, Field

from ..errors import ApiError
from ..extensions import NewProject
from ..messages import empty
from ..projects import HostedProjects, LocalProjects
from ..routing import Routes, principal_of, runtime_of
from .build import busy_error

routes = Routes(tags=["projects"])

Language = Annotated[str, Field(pattern=r"^[a-z]{2}$")]


class OpenBody(BaseModel):
    path: Annotated[str, Field(min_length=1, max_length=4096)]


class CreateBody(BaseModel):
    """A new project: its folder (locally) or id (hosted), its name and field."""

    folder: Annotated[str | None, Field(max_length=4096)] = None
    id: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")] = None
    name: Annotated[str, Field(min_length=1, max_length=200)]
    domain_title: Annotated[str, Field(max_length=300)] = ""
    domain_description: Annotated[str, Field(max_length=4000)] = ""
    languages: Annotated[list[Language], Field(min_length=1, max_length=8)] = ["en"]
    reference: Language = "en"


def _local(request: Request) -> LocalProjects:
    host = runtime_of(request).projects
    if not isinstance(host, LocalProjects):
        raise ApiError.of("hosted_projects")
    return host


def _describe(handle: Any, *, local: bool) -> dict[str, Any]:
    out = {"open": True, "id": handle.id, "name": handle.name, "opened_at": handle.opened_at}
    if local:
        out["path"] = str(handle.layout.root)
    if handle.project.recovered:
        out["recovered"] = list(handle.project.recovered)
    return out


def _busy(request: Request) -> None:
    runtime = runtime_of(request)
    host = runtime.projects
    current = host.current() if isinstance(host, LocalProjects) else None
    if current is not None:
        running = runtime.jobs.running(current.id)
        if running is not None:
            raise busy_error(running)


@routes.get("/api/projects/current", action="projects.read", resource="app")
def current(request: Request) -> dict[str, Any]:
    """The project the app works on now (locally), or none."""
    host = runtime_of(request).projects
    if isinstance(host, LocalProjects):
        handle = host.current()
        return _describe(handle, local=True) if handle else {"open": False}
    pid = host.project_id(request, principal_of(request))
    return {"open": pid is not None, "id": pid}


@routes.post("/api/projects/open", action="projects.open", resource="app")
def open_project(request: Request, body: OpenBody) -> dict[str, Any]:
    """Open a project folder (locally), closing the one open before."""
    host = _local(request)
    _busy(request)
    handle = host.open(Path(body.path))
    return _describe(handle, local=True)


@routes.post("/api/projects/close", action="projects.open", resource="app")
def close_project(request: Request) -> dict[str, Any]:
    """Close the open project (locally): its lock is released."""
    host = _local(request)
    _busy(request)
    host.close()
    return {"open": False}


@routes.get("/api/projects/recent", action="projects.read", resource="app")
def recent(request: Request) -> dict[str, Any]:
    """The projects opened lately (locally), the latest first."""
    items = _local(request).recent()
    return {
        "items": items,
        "total": len(items),
        "empty": None if items else empty("empty_no_recent"),
    }


@routes.get("/api/projects", action="projects.read", resource="app")
def list_projects(request: Request) -> dict[str, Any]:
    """The projects this principal may open (hosted), or the recent ones (locally)."""
    host = runtime_of(request).projects
    if isinstance(host, HostedProjects):
        items = host.list(principal_of(request))
        return {"items": items, "total": len(items), "empty": None}
    return recent(request)


@routes.post("/api/projects", action="projects.create", resource="app", status_code=201)
def create_project(request: Request, body: CreateBody) -> dict[str, Any]:
    """Create a project, fill its identity (the host's provider), and open it."""
    from cartolex.project import Project
    from cartolex.project.models import LANGUAGES, AIIdentity, Slot

    runtime = runtime_of(request)
    principal = principal_of(request)
    combined = runtime.extensions
    missing = sorted({x for x in [*body.languages, body.reference] if x not in LANGUAGES})
    if missing:
        raise ApiError.of("no_language_pack", languages=missing, available=list(LANGUAGES))
    identity: dict[str, Any] = {
        "domain_title": body.domain_title,
        "domain_description": body.domain_description,
    }
    new = NewProject(
        name=body.name,
        domain_title=body.domain_title,
        domain_description=body.domain_description,
        languages=tuple(body.languages),
        principal_id=principal.id,
    )
    for ext in combined.extensions:
        if ext.identity_provider is not None:
            identity.update({k: v for k, v in ext.identity_provider(new).items() if v is not None})
    if not str(identity.get("domain_title") or "").strip():
        raise ApiError.of("field_title_missing")
    slots = [s for e in combined.extensions for s in e.corpus_slots] or [
        Slot(id="collected", kind="collection", fit=True, trajectory=True)
    ]
    host = runtime.projects
    if isinstance(host, HostedProjects):
        if not body.id:
            raise ApiError.of("project_id_missing")
        folder = host.folder(body.id)
    else:
        if not body.folder:
            raise ApiError.of("project_folder_missing")
        folder = Path(body.folder).expanduser()
        if not folder.is_absolute():
            raise ApiError.of("project_folder_relative", path=str(folder))
        _busy(request)
    try:
        project = Project.init(
            folder,
            name=body.name,
            domain_title=str(identity["domain_title"]),
            domain_description=str(identity.get("domain_description") or ""),
            corpus_languages=tuple(body.languages),
            reference_language=body.reference,
            slots=tuple(slots),
        )
    except FileExistsError as exc:
        raise ApiError.of("project_exists", path=str(folder)) from exc
    try:
        overlays = [o for e in combined.extensions for o in e.overlay_sets]
        ai = identity.get("ai")
        changes: dict[str, Any] = {}
        if overlays:
            changes["overlays"] = overlays
        if ai:
            changes["identity"] = project.config.identity.model_copy(
                update={"ai": ai if isinstance(ai, AIIdentity) else AIIdentity(**dict(ai))}
            )
        if changes:
            project.save_config(project.config.model_copy(update=changes), action="created")
        for ext in combined.extensions:
            if ext.project_init is not None:
                ext.project_init(project)
    except BaseException:
        project.close()
        raise
    if isinstance(host, HostedProjects):
        handle = host.adopt(body.id or "", project)
        return _describe(handle, local=False)
    handle = host.adopt(project)  # type: ignore[union-attr]
    return _describe(handle, local=True)
