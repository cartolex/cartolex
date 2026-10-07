# SPDX-License-Identifier: MIT
"""Deleting a project's folder from the app (locally), and nothing else.

Deleting files cannot be undone, so this module is conservative:

- only a folder the app lists among its recent projects, holding a ``project.json``
  whose format is ``cartolex-project/…``, never a symbolic link, never the computer's
  root, the home folder, a folder holding it or the app's own folder;
- only what cartolex writes in a project (``project.json``, ``sources/``,
  ``decisions/``, ``derived/``, ``cache/``, ``outputs/``, ``logs/``, the lock, and the
  files the system leaves in any folder): anything else in the folder is kept, and so is
  the folder then;
- a link inside the project is removed as a link: what it points to is never touched;
- under the project's lock, taken as any opener takes it: a project another
  application holds is refused, never overridden; ``project.json`` goes last, so a
  deletion stopped half way (a file the system refuses) leaves a project that can be
  opened or deleted again.

The routes (:mod:`cartolex.app.routes.projects`) add the rest: an explicit confirmation,
no job running, the open project closed first.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cartolex.project.folders import is_link as _is_link
from cartolex.project.folders import remove_entry, tree_size
from cartolex.project.layout import ProjectLayout

from .errors import ApiError

__all__ = ["PROJECT_ENTRIES", "FolderPlan", "active_jobs", "delete_project_folder", "inspect"]

#: What cartolex writes at a project's root, in the order a deletion removes it
#: (``project.json`` last: until then the folder is still a project).
PROJECT_ENTRIES = (
    "derived",
    "cache",
    "logs",
    "outputs",
    "sources",
    "decisions",
    "project.json",
)
#: Files the systems leave in any folder (removed with the project).
SYSTEM_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", ".directory"})
#: The lock and its takeover mark (removed when the lock is released).
LOCK_FILES = frozenset({".lock", ".lock.takeover"})


@dataclass
class FolderPlan:
    """What deleting a project's folder would remove: its name, its size, what stays."""

    root: Path
    name: str
    bytes: int = 0
    files: int = 0
    #: Entries of the folder cartolex did not write: they are kept, and so is the folder.
    kept: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"path": str(self.root), "name": self.name, "bytes": self.bytes,
                "files": self.files, "kept": self.kept}  # fmt: skip


def _protected(data_dir: Path | None) -> list[Path]:
    home = Path.home().resolve()
    out = [home, *home.parents]
    if data_dir is not None:
        out.append(Path(data_dir).resolve())
    return out


def _refuse(root: Path, reason: str) -> ApiError:
    return ApiError.of("project_delete_refused", path=str(root), reason=reason)


def inspect(root: Path, *, data_dir: Path | None = None) -> FolderPlan:
    """What deleting the project folder *root* would remove, or why it is refused
    (``project_delete_refused``, ``reason``: ``missing``, ``link``, ``not_a_project``,
    ``protected``)."""
    root = Path(root)
    if not root.is_absolute():
        raise _refuse(root, "not_a_project")
    if _is_link(root) or root.resolve() != root:
        raise _refuse(root, "link")
    if not root.is_dir():
        raise _refuse(root, "missing")
    resolved = root.resolve()
    if resolved == Path(resolved.anchor) or any(
        resolved == p or p.is_relative_to(resolved) for p in _protected(data_dir)
    ):
        raise _refuse(root, "protected")
    marker = ProjectLayout(root).project_json
    try:
        if _is_link(marker):
            raise ValueError("a link")
        raw = json.loads(marker.read_text(encoding="utf-8"))
        found = raw.get("format") if isinstance(raw, dict) else None
        if not isinstance(found, str) or not found.startswith("cartolex-project/"):
            raise ValueError("not a project")
    except (OSError, ValueError):
        raise _refuse(root, "not_a_project") from None
    name = raw.get("name")
    plan = FolderPlan(root, name.strip() if isinstance(name, str) and name.strip() else root.name)
    ours = {*PROJECT_ENTRIES, *SYSTEM_FILES, *LOCK_FILES}
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.name not in ours:
            plan.kept.append(entry.name)
            continue
        size, files = tree_size(entry)
        plan.bytes += size
        plan.files += files
    return plan


def _held_error(info: Any) -> ApiError:
    return ApiError.of(
        "project_delete_held",
        app=info.app if info else "another application",
        pid=info.pid if info else "?",
        host=info.host if info else "?",
        since=info.since if info else "?",
    )


def delete_project_folder(plan: FolderPlan) -> dict[str, Any]:
    """Delete what cartolex wrote in the folder of *plan* (see the module's text), under the
    project's lock; the folder itself when nothing else is left in it.

    Refused with ``project_delete_held`` when another application holds the project (a lock
    left behind by an application that no longer runs is taken over, as when opening).
    A file the system refuses stops the deletion: ``project_delete_failed``, and
    ``project.json`` is still there."""
    from cartolex.project.lock import LockHeld, ProjectLock

    root = plan.root
    layout = ProjectLayout(root)
    try:
        lock = ProjectLock(layout, "cartolex").acquire()
    except LockHeld as exc:
        raise _held_error(exc.info) from exc
    try:
        names = [*PROJECT_ENTRIES[:-1], *sorted(SYSTEM_FILES), PROJECT_ENTRIES[-1]]
        for name in names:
            entry = root / name
            if entry.exists() or _is_link(entry):
                remove_entry(entry, root)
    except OSError as exc:
        lock.release()
        raise ApiError.of(
            "project_delete_failed", path=str(root), error=f"{type(exc).__name__}: {exc}"
        ) from exc
    lock.release()
    left = sorted(p.name for p in root.iterdir()) if root.is_dir() else []
    removed = False
    if not left:
        try:
            root.rmdir()
            removed = True
        except OSError:
            removed = False
    return {**plan.as_dict(), "deleted": True, "folder_removed": removed, "kept": left}


def active_jobs(jobs: Iterable[Any]) -> Any | None:
    """The first job of *jobs* still queued, running or being stopped (any group), or ``None``."""
    from .jobs import ACTIVE_STATES

    return next((j for j in jobs if j.state in ACTIVE_STATES), None)
