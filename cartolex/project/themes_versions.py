# SPDX-License-Identifier: MIT
"""Saving the theme tree and its versions (``decisions/themes.json`` and its history).

Every save goes through :func:`~cartolex.project.files.write_decision`: it is
refused if the file changed since it was read (:class:`~cartolex.project.files.StaleWrite`),
and the version it replaces is kept in ``decisions/history/themes.json/`` as
``<UTC time>-<action>.json`` — the time it was replaced and the action that
replaced it. A version is therefore named by the file that holds it: ``current``
for ``themes.json``, the file name without ``.json`` for an earlier one.
Restoring a version writes it again as a new version, so a restore is undone
like any other change.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .files import json_bytes, write_decision
from .layout import ProjectLayout
from .models import ThemesFile
from .project import Project
from .themes import canonical

__all__ = [
    "CURRENT",
    "ThemesVersion",
    "list_versions",
    "read_themes",
    "read_version",
    "restore_version",
    "save_themes",
]

#: The id of the version in ``decisions/themes.json``.
CURRENT = "current"

_HISTORY_NAME = re.compile(r"^(\d{8}T\d{6}Z)-(.+)\.json$")


@dataclass(frozen=True)
class ThemesVersion:
    """One version of the theme tree.

    ``made_at`` and ``made_by`` are when and by which action it was written:
    they come from the history entry of the version it replaced, so they are
    ``None`` for the first version the history knows. ``replaced_at`` and
    ``replaced_by`` are ``None`` for the current version. Actions are given as
    the history file names spell them (``move-3-keywords-to-n7``).
    """

    id: str
    path: Path
    made_at: datetime | None
    made_by: str | None
    replaced_at: datetime | None
    replaced_by: str | None


def _layout(project: Project | ProjectLayout) -> ProjectLayout:
    return project.layout if isinstance(project, Project) else project


def _parse(bytes_: bytes, where: Path) -> ThemesFile:
    try:
        return ThemesFile.model_validate_json(bytes_)
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from exc


def read_themes(project: Project | ProjectLayout) -> tuple[ThemesFile | None, str | None]:
    """The current tree and the fingerprint of the file it was read from.

    ``(None, None)`` when the project has no ``themes.json`` yet. Pass the
    fingerprint to :func:`save_themes` as *expected*.
    """
    path = _layout(project).themes_json
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, None
    return _parse(data, path), "sha256:" + hashlib.sha256(data).hexdigest()


def save_themes(
    project: Project,
    tree: ThemesFile,
    *,
    expected: str | None,
    action: str,
    now: datetime | None = None,
) -> str:
    """Save *tree* as the new current version, named by *action*; return its fingerprint.

    *expected* is the fingerprint :func:`read_themes` gave (``None``: there was
    no file). The tree is written in its canonical form
    (:func:`~cartolex.project.themes.canonical`). Saving the very tree that is
    already current writes nothing. Raises
    :class:`~cartolex.project.files.StaleWrite` when the file changed since it was
    read, and :class:`PermissionError` when the project is open read-only.
    """
    if not project.writable:
        raise PermissionError("the project is open read-only; open it with write=True")
    layout = project.layout
    data = json_bytes(canonical(tree))
    path = layout.themes_json
    if expected is not None:
        try:
            current = path.read_bytes()
        except FileNotFoundError:
            current = None
        if current == data:
            found = "sha256:" + hashlib.sha256(current).hexdigest()
            if found == expected:
                return found
    return write_decision(layout, path, data, expected=expected, action=action, now=now)


def _history(layout: ProjectLayout) -> list[tuple[datetime, str, Path]]:
    folder = layout.history_of(layout.themes_json)
    if not folder.is_dir():
        return []
    entries = []
    for path in folder.iterdir():
        m = _HISTORY_NAME.match(path.name)
        if not m or not path.is_file():
            continue
        at = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        entries.append((at, path.stat().st_mtime_ns, path.name, m.group(2), path))
    # Same-second entries are ordered by the time their file was written.
    entries.sort(key=lambda e: (e[0], e[1], e[2]))
    return [(at, action, path) for at, _ns, _name, action, path in entries]


def list_versions(project: Project | ProjectLayout) -> list[ThemesVersion]:
    """Every version of the tree the project keeps, the current one first."""
    layout = _layout(project)
    versions: list[ThemesVersion] = []
    made_at: datetime | None = None
    made_by: str | None = None
    for at, action, path in _history(layout):
        versions.append(
            ThemesVersion(
                id=path.name[: -len(".json")],
                path=path,
                made_at=made_at,
                made_by=made_by,
                replaced_at=at,
                replaced_by=action,
            )
        )
        made_at, made_by = at, action
    if layout.themes_json.exists():
        versions.append(
            ThemesVersion(
                id=CURRENT,
                path=layout.themes_json,
                made_at=made_at,
                made_by=made_by,
                replaced_at=None,
                replaced_by=None,
            )
        )
    return versions[::-1]


def _version_path(layout: ProjectLayout, version_id: str) -> Path:
    if version_id == CURRENT:
        path = layout.themes_json
    else:
        name = f"{version_id}.json"
        if "/" in version_id or "\\" in version_id or not _HISTORY_NAME.match(name):
            raise KeyError(f"no version {version_id!r} of the theme tree")
        path = layout.history_of(layout.themes_json) / name
    if not path.is_file():
        raise KeyError(f"no version {version_id!r} of the theme tree")
    return path


def read_version(project: Project | ProjectLayout, version_id: str) -> ThemesFile:
    """The tree of one version (an id from :func:`list_versions`)."""
    path = _version_path(_layout(project), version_id)
    return _parse(path.read_bytes(), path)


def restore_version(
    project: Project,
    version_id: str,
    *,
    expected: str | None,
    now: datetime | None = None,
) -> str:
    """Make an earlier version current again, as a new version; return its fingerprint.

    The action name is ``restore <version id>``; the version that was current
    goes to the history like after any save.
    """
    if not project.writable:
        raise PermissionError("the project is open read-only; open it with write=True")
    if version_id == CURRENT:
        raise ValueError("this version is already the current one")
    tree = read_version(project, version_id)
    return save_themes(project, tree, expected=expected, action=f"restore {version_id}", now=now)
