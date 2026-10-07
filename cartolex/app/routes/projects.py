# SPDX-License-Identifier: MIT
"""Which project the app works on: the current one, open, close, recent, create."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Query, Request
from pydantic import BaseModel, Field

from ..errors import ApiError
from ..extensions import NewProject
from ..messages import empty
from ..projects import HostedProjects, LocalProjects
from ..routing import Routes, principal_of, runtime_of
from .build import busy_error

routes = Routes(tags=["projects"])

Language = Annotated[str, Field(pattern=r"^[a-z]{2}$")]


#: Where a new project starts from, and the page the interface goes to next.
START_POINTS = {
    "people": "/people?start=people",
    "institutions": "/people?start=institutions",
    "collaborators": "/people?start=collaborators",
    "folder": "/?start=folder",
    "corpus": "/?start=corpus",
}
StartPoint = Literal["people", "institutions", "collaborators", "folder", "corpus"]


class OpenBody(BaseModel):
    path: Annotated[str, Field(min_length=1, max_length=4096)]
    #: Override a lock held elsewhere (the person confirmed the warning).
    force: bool = False


class CreateBody(BaseModel):
    """A new project: its folder (locally) or id (hosted), its name and field."""

    folder: Annotated[str | None, Field(max_length=4096)] = None
    id: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")] = None
    name: Annotated[str, Field(min_length=1, max_length=200)]
    domain_title: Annotated[str, Field(max_length=300)] = ""
    domain_description: Annotated[str, Field(max_length=4000)] = ""
    languages: Annotated[list[Language], Field(min_length=1, max_length=8)] = ["en"]
    reference: Language = "en"
    display: Annotated[list[Language], Field(min_length=1, max_length=8)] | None = None
    #: Where the project starts from; a folder or a corpus adds a slot of that kind.
    start: StartPoint | None = None


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
    """Open a project folder (locally), closing the one open before; ``force`` overrides its lock."""
    host = _local(request)
    _busy(request)
    handle = host.open(Path(body.path), force=body.force)
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


class ListedBody(BaseModel):
    """A project of the recent list, by its folder as the list gives it."""

    path: Annotated[str, Field(min_length=1, max_length=4096)]


class DeleteBody(ListedBody):
    """Delete a recent project's folder: ``confirm`` says the person was asked."""

    confirm: bool = False


def _deletable(request: Request, path: str) -> tuple[LocalProjects, Any, bool]:
    """The local host, what deleting *path* would remove, and whether it is the open project;
    refused on a hosted service, for a folder not listed or not a project, while a job runs."""
    from ..projects import local_project_id
    from ..removal import active_jobs, inspect

    runtime = runtime_of(request)
    if runtime.settings.hosted:
        raise ApiError.of("project_delete_hosted")
    host = _local(request)
    folder = host.listed(path)
    plan = inspect(folder, data_dir=runtime.settings.data_dir)
    current = host.current()
    is_open = current is not None and current.layout.root.resolve() == folder.resolve()
    pid = current.id if is_open and current else local_project_id(folder)
    running = active_jobs(runtime.jobs.list(pid))
    if running is not None:
        raise busy_error(running)
    return host, plan, is_open


@routes.post("/api/projects/forget", action="projects.open", resource="app")
def forget(request: Request, body: ListedBody) -> dict[str, Any]:
    """Take a project out of the recent list (locally); its folder stays as it is."""
    host = _local(request)
    host.listed(body.path)
    host.forget(body.path)
    return recent(request)


@routes.get("/api/projects/removal", action="projects.read", resource="app")
def removal(request: Request, path: Annotated[str, Query(min_length=1, max_length=4096)]
            ) -> dict[str, Any]:  # fmt: skip
    """What deleting a recent project's folder would remove (its size, the entries cartolex
    did not write, kept), whether it is open here, and why it would be refused."""
    from cartolex.project.layout import ProjectLayout
    from cartolex.project.lock import lock_holder

    host, plan, is_open = _deletable(request, path)
    held = None if is_open else lock_holder(ProjectLayout(plan.root))
    return {**plan.as_dict(), "open": is_open,
            "held": None if held is None else {"app": held.app, "pid": held.pid,
                                               "host": held.host, "since": held.since}}  # fmt: skip


@routes.post("/api/projects/delete", action="projects.delete", resource="app")
def delete_project(request: Request, body: DeleteBody) -> dict[str, Any]:
    """Delete a recent project's folder (locally, after ``confirm``): the open project is
    closed first; refused while another application holds it or a job runs."""
    from ..removal import delete_project_folder

    host, plan, is_open = _deletable(request, body.path)
    if not body.confirm:
        raise ApiError.of("project_delete_confirm", path=str(plan.root))
    if is_open:
        host.close()
    done = delete_project_folder(plan)
    host.forget(body.path)
    return {**done, "closed": is_open}


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
    display = body.display or list(dict.fromkeys([body.reference, *body.languages]))
    missing = sorted({x for x in [*body.languages, body.reference, *display] if x not in LANGUAGES})
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
    if body.start in ("folder", "corpus") and not any(s.kind == body.start for s in slots):
        slots.append(Slot(id="texts" if body.start == "folder" else "corpus", kind=body.start))
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
        if display != list(project.config.languages.display):
            changes["languages"] = project.config.languages.model_copy(update={"display": display})
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
    nxt = START_POINTS.get(body.start or "", "/")
    if isinstance(host, HostedProjects):
        handle = host.adopt(body.id or "", project)
        return {**_describe(handle, local=False), "next": nxt}
    handle = host.adopt(project)  # type: ignore[union-attr]
    return {**_describe(handle, local=True), "next": nxt}


def _suggested_root() -> Path:
    """Where new projects go by default: ``~/cartolex-projects``."""
    return Path.home() / "cartolex-projects"


@routes.get("/api/projects/defaults", action="projects.read", resource="app")
def defaults(request: Request) -> dict[str, Any]:
    """What the new project form starts from: the suggested folder, the starting points, the
    languages a project may use."""
    from cartolex.project.models import LANGUAGES

    hosted = runtime_of(request).settings.hosted
    return {
        "hosted": hosted,
        "folder": None if hosted else str(_suggested_root()),
        "separator": os.sep,
        "start_points": [*START_POINTS, "demo"],
        "languages": list(LANGUAGES),
    }


class DemoBody(BaseModel):
    """The folder of the demo project (locally; default: ``<suggested folder>/demo``)."""

    folder: Annotated[str | None, Field(max_length=4096)] = None


@routes.post("/api/projects/demo", action="projects.create", resource="app", status_code=201)
def create_demo(request: Request, body: DemoBody) -> dict[str, Any]:
    """Create the demo project (the small invented world, in English and French) and open it;
    it still needs a build."""
    from cartolex.demo import generate
    from cartolex.demo.project import write_project

    host = _local(request)
    _busy(request)
    folder = Path(body.folder).expanduser() if body.folder else _suggested_root() / "demo"
    if not folder.is_absolute():
        raise ApiError.of("project_folder_relative", path=str(folder))
    if folder.exists() and any(folder.iterdir()):
        raise ApiError.of("project_exists", path=str(folder))
    project = write_project(generate("S", 0), folder, name="Demo: coastal and marine systems")
    handle = host.adopt(project)
    return {**_describe(handle, local=True), "next": "/"}
