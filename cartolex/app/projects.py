# SPDX-License-Identifier: MIT
"""The projects an app works on, and the project context of each request (4c).

Each request that works on a project gets a :class:`ProjectContext` (a FastAPI
dependency): the project, its lock, its paths. Nothing is process-wide: the
projects an app has open live in its :class:`ProjectHost`, created by
:func:`cartolex.app.create_app`, so two apps in one process never see each
other's.

- **Locally** (:class:`LocalProjects`) one project is open at a time, for
  writing (the app holds its lock); ``open``, ``close`` and the recent list
  change which. The recent list is kept in the app's data folder.
- **Hosted** (:class:`HostedProjects`) many projects live in one folder, each
  opened on first use; a request names its project by the route
  (``/api/projects/<id>/…``) or through its principal.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cartolex.project import Project, ProjectLayout

from .errors import ApiError

if TYPE_CHECKING:
    from fastapi import Request

    from .auth import Principal

__all__ = [
    "HostedProjects",
    "LocalProjects",
    "ProjectContext",
    "ProjectHandle",
    "ProjectHost",
    "local_project_id",
]

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_RECENT = 12


@dataclass
class ProjectHandle:
    """An open project: its id in the app, the project (open for writing) and a mutex.

    ``mutex`` serialises the app's own read-change-write sequences on the
    project (two requests editing ``people.csv`` at once); the guarded writes
    still refuse a change made from outside.
    """

    id: str
    project: Project
    mutex: threading.RLock = field(default_factory=threading.RLock)
    opened_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    @property
    def layout(self) -> ProjectLayout:
        return self.project.layout

    @property
    def name(self) -> str:
        return self.project.config.name


@dataclass(frozen=True)
class ProjectContext:
    """What a request working on a project gets: the handle, the project, its paths."""

    handle: ProjectHandle

    @property
    def id(self) -> str:
        return self.handle.id

    @property
    def project(self) -> Project:
        return self.handle.project

    @property
    def layout(self) -> ProjectLayout:
        return self.handle.project.layout


def local_project_id(root: Path) -> str:
    """A local project's id: its folder's name and a digest of its full path."""
    resolved = Path(root).resolve()
    stem = re.sub(r"[^a-z0-9_-]+", "-", resolved.name.lower()).strip("-")[:40] or "project"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{digest}"


OpenHook = Callable[[Project], None]


class ProjectHost:
    """Which projects an app has open, and which one a request works on."""

    hooks: Sequence[OpenHook] = ()

    def resolve(self, request: Request, principal: Principal) -> ProjectHandle:
        raise NotImplementedError

    def project_id(self, request: Request, principal: Principal) -> str | None:
        """The id of the project the request works on, without opening it (``None``: none)."""
        raise NotImplementedError

    def handles(self) -> list[ProjectHandle]:
        raise NotImplementedError

    def close_all(self) -> None:
        for handle in self.handles():
            handle.project.close()

    def _opened(self, project: Project) -> None:
        for hook in self.hooks:
            hook(project)


def _no_project() -> ApiError:
    return ApiError.of("no_project")


class LocalProjects(ProjectHost):
    """One project open at a time, for writing; a recent list kept in *data_dir*."""

    def __init__(self, data_dir: Path | None, hooks: Sequence[OpenHook] = ()) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.hooks = tuple(hooks)
        self._current: ProjectHandle | None = None
        self._lock = threading.RLock()
        self._recent: list[dict[str, Any]] = self._read_recent()

    # ── the current project ──
    def current(self) -> ProjectHandle | None:
        with self._lock:
            return self._current

    def handles(self) -> list[ProjectHandle]:
        current = self.current()
        return [current] if current else []

    def resolve(self, request: Request, principal: Principal) -> ProjectHandle:
        current = self.current()
        if current is None:
            raise _no_project()
        return current

    def project_id(self, request: Request, principal: Principal) -> str | None:
        current = self.current()
        return current.id if current else None

    def open(self, root: Path, *, force: bool = False) -> ProjectHandle:
        """Open the project in *root* for writing and make it the current one.

        *force* overrides a lock held elsewhere (the person was warned). The
        project open now is kept unless another application overrode its lock:
        it is then opened again.
        """
        from cartolex.project.lock import LockLost, ensure_held

        root = Path(root).expanduser()
        with self._lock:
            current = self._current
            if current is not None and current.layout.root.resolve() == root.resolve():
                try:
                    ensure_held(current.layout.lock)
                    return current
                except LockLost:
                    current.project.close()
                    self._current = current = None
            project = Project.open(root, write=True, force=force)
            try:
                self._opened(project)
            except BaseException:
                project.close()
                raise
            if current is not None:
                current.project.close()
            self._current = ProjectHandle(local_project_id(root), project)
            self._remember(project)
            return self._current

    def reopen_last(self) -> ProjectHandle | None:
        """Open the project opened last, when it is still there and no other app holds it;
        else nothing (the interface then shows the start screen)."""
        import logging

        with self._lock:
            last = self._recent[0] if self._recent else None
        if last is None:
            return None
        root = Path(last["path"])
        if not ProjectLayout(root).project_json.exists():
            return None
        try:
            return self.open(root)
        except Exception as exc:  # held elsewhere, moved, unreadable: the start screen
            logging.getLogger("cartolex.app").info(
                "the last project was not opened again",
                extra={"event": "reopen_skipped", "error": type(exc).__name__},
            )
            return None

    def adopt(self, project: Project) -> ProjectHandle:
        """Make a project just created (open for writing) the current one."""
        with self._lock:
            if self._current is not None:
                self._current.project.close()
            self._opened(project)
            self._current = ProjectHandle(local_project_id(project.layout.root), project)
            self._remember(project)
            return self._current

    def close(self) -> None:
        with self._lock:
            if self._current is not None:
                self._current.project.close()
                self._current = None

    # ── the recent list ──
    def _recent_file(self) -> Path | None:
        return self.data_dir / "recent.json" if self.data_dir is not None else None

    def _read_recent(self) -> list[dict[str, Any]]:
        path = self._recent_file()
        if path is None or not path.exists():
            return []
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            items = doc.get("projects", []) if isinstance(doc, dict) else []
            return [i for i in items if isinstance(i, dict) and isinstance(i.get("path"), str)]
        except (OSError, ValueError):
            return []

    def _remember(self, project: Project) -> None:
        root = str(project.layout.root.resolve())
        entry = {
            "path": root,
            "name": project.config.name,
            "opened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._recent = [entry] + [e for e in self._recent if e.get("path") != root]
        self._recent = self._recent[:MAX_RECENT]
        self._save_recent()

    def _save_recent(self) -> None:
        path = self._recent_file()
        if path is not None:
            from cartolex.project.files import atomic_write_bytes, json_bytes

            atomic_write_bytes(
                path, json_bytes({"format": "cartolex-recent/1", "projects": self._recent})
            )

    def listed(self, path: str) -> Path:
        """The folder of the recent project *path* (as the list gives it); refused with
        ``project_not_listed`` when the list has no such project."""
        with self._lock:
            known = any(e.get("path") == path for e in self._recent)
        if not known:
            raise ApiError.of("project_not_listed", path=path)
        return Path(path)

    def forget(self, path: str) -> None:
        """Take *path* out of the recent list (its folder stays as it is)."""
        with self._lock:
            self._recent = [e for e in self._recent if e.get("path") != path]
            self._save_recent()

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._recent)
        out = []
        for item in items:
            root = Path(item["path"])
            out.append(
                {
                    **item,
                    "id": local_project_id(root),
                    "exists": ProjectLayout(root).project_json.exists(),
                }
            )
        return out


class HostedProjects(ProjectHost):
    """Many projects in one folder (*root*), each opened for writing on first use."""

    def __init__(self, root: Path, hooks: Sequence[OpenHook] = ()) -> None:
        self.root = Path(root)
        self.hooks = tuple(hooks)
        self._open: dict[str, ProjectHandle] = {}
        self._lock = threading.RLock()

    def handles(self) -> list[ProjectHandle]:
        with self._lock:
            return list(self._open.values())

    def project_id(self, request: Request, principal: Principal) -> str | None:
        pid = request.scope.get("state", {}).get("cartolex.project") or principal.project
        return pid if isinstance(pid, str) and _SLUG.match(pid) else None

    def folder(self, project_id: str) -> Path:
        if not _SLUG.match(project_id):
            raise ApiError.of("invalid_project_id", id=project_id)
        return self.root / project_id

    def resolve(self, request: Request, principal: Principal) -> ProjectHandle:
        pid = self.project_id(request, principal)
        if pid is None:
            raise ApiError.of("project_not_named")
        return self.open(pid)

    def open(self, project_id: str) -> ProjectHandle:
        with self._lock:
            if project_id in self._open:
                return self._open[project_id]
            folder = self.folder(project_id)
            if not ProjectLayout(folder).project_json.exists():
                raise ApiError.of("project_not_found", id=project_id)
            project = Project.open(folder, write=True)
            try:
                self._opened(project)
            except BaseException:
                project.close()
                raise
            handle = ProjectHandle(project_id, project)
            self._open[project_id] = handle
            return handle

    def adopt(self, project_id: str, project: Project) -> ProjectHandle:
        with self._lock:
            self._opened(project)
            handle = ProjectHandle(project_id, project)
            self._open[project_id] = handle
            return handle

    def list(self, principal: Principal) -> list[dict[str, Any]]:
        out = []
        if not self.root.is_dir():
            return out
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or not _SLUG.match(folder.name):
                continue
            if not (folder / "project.json").exists():
                continue
            if principal.projects is not None and folder.name not in principal.projects:
                continue
            try:
                name = json.loads((folder / "project.json").read_text(encoding="utf-8"))["name"]
            except (OSError, ValueError, KeyError, TypeError):
                name = folder.name
            out.append({"id": folder.name, "name": name})
        return out
