# SPDX-License-Identifier: MIT
"""Saving the theme tree and its versions (``decisions/themes.json`` and its history).

Every save goes through :func:`~cartolex.project.files.write_decision`: it is
refused if the file changed since it was read (:class:`~cartolex.project.files.StaleWrite`),
and the version it replaces is kept in ``decisions/history/themes.json/`` as
``<UTC time>-<action>.json`` — the time it was replaced and the action that
replaced it. A version is therefore named by the file that holds it: ``current``
for ``themes.json``, the file name without ``.json`` for an earlier one.

A save first removes every node with no keyword in its subtree (the
carry-forward rule: sub-groups left empty are removed at save), then stamps the
tree with ``saved``: the time and the action, which names the removed nodes.
Restoring a version writes it again as a new version, so a restore is undone
like any other change.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .files import json_bytes, write_decision
from .layout import ProjectLayout
from .models import ThemesFile, ThemesSaved
from .project import Project
from .themes import canonical, prune_empty

__all__ = [
    "CURRENT",
    "Saved",
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
class Saved:
    """The result of a save: the tree as written, its fingerprint, and what the save did.

    ``tree`` is the tree now current, pruned and stamped; ``action`` is the
    action it was saved with (the given action, plus the nodes the save
    removed); ``removed`` lists those nodes. ``written`` is false when the tree
    was already current and nothing was written.
    """

    tree: ThemesFile
    fingerprint: str
    action: str
    removed: tuple[str, ...]
    written: bool


@dataclass(frozen=True)
class ThemesVersion:
    """One version of the theme tree.

    ``made_at`` and ``made_by`` are when and by which action it was written, as
    the version's own ``saved`` stamp records them. A version written without a
    stamp takes them from the history entry of the version it replaced (the
    action as the file name spells it), or ``None`` for the first one.
    ``replaced_at`` and ``replaced_by`` come from the version's history file
    name; they are ``None`` for the current version.
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


def _fingerprint(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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
    return _parse(data, path), _fingerprint(data)


def save_themes(
    project: Project,
    tree: ThemesFile,
    *,
    expected: str | None,
    action: str,
    now: datetime | None = None,
) -> Saved:
    """Save *tree* as the new current version, named by *action*.

    The save removes every node with no keyword in its subtree
    (:func:`~cartolex.project.themes.prune_empty`) and appends the removed
    nodes to the action (``move 2 keywords to n7; remove empty node n3``). The
    tree is stamped with ``saved`` (*now*, the action) and written in its
    canonical form. *expected* is the fingerprint :func:`read_themes` gave
    (``None``: there was no file). When the pruned tree is the one already
    current (its stamp aside), nothing is written. Raises
    :class:`~cartolex.project.files.StaleWrite` when the file changed since it was
    read, and :class:`PermissionError` when the project is open read-only.
    """
    if not project.writable:
        raise PermissionError("the project is open read-only; open it with write=True")
    if not str(action or "").strip():
        raise ValueError("a save needs an action name")
    layout = project.layout
    path = layout.themes_json
    pruned = prune_empty(tree)
    kept = {n.id for n in pruned.tree.nodes}
    removed = tuple(n.id for n in canonical(tree).nodes if n.id not in kept)
    action = action.strip() + (f"; {pruned.description}" if removed else "")
    if expected is not None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            data = None
        if data is not None and _fingerprint(data) == expected:
            current = _parse(data, path)
            if current.model_copy(update={"saved": None}) == pruned.tree.model_copy(
                update={"saved": None}
            ):
                return Saved(current, expected, action, removed, written=False)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamped = pruned.tree.model_copy(update={"saved": ThemesSaved(at=now, action=action)})
    fp = write_decision(
        layout, path, json_bytes(stamped), expected=expected, action=action, now=now
    )
    return Saved(stamped, fp, action, removed, written=True)


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


def _stamp(path: Path) -> tuple[datetime | None, str | None]:
    """The ``saved`` stamp of a version file, read leniently: ``(None, None)`` when absent."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8")).get("saved")
        stamp = ThemesSaved.model_validate(saved)
    except (OSError, ValueError, AttributeError):
        return None, None
    return stamp.at, stamp.action


def list_versions(project: Project | ProjectLayout) -> list[ThemesVersion]:
    """Every version of the tree the project keeps, the current one first."""
    layout = _layout(project)
    versions: list[ThemesVersion] = []
    made_at: datetime | None = None
    made_by: str | None = None
    for at, action, path in _history(layout):
        own_at, own_by = _stamp(path)
        versions.append(
            ThemesVersion(
                id=path.name[: -len(".json")],
                path=path,
                made_at=own_at or made_at,
                made_by=own_by or made_by,
                replaced_at=at,
                replaced_by=action,
            )
        )
        made_at, made_by = at, action
    if layout.themes_json.exists():
        own_at, own_by = _stamp(layout.themes_json)
        versions.append(
            ThemesVersion(
                id=CURRENT,
                path=layout.themes_json,
                made_at=own_at or made_at,
                made_by=own_by or made_by,
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
) -> Saved:
    """Make an earlier version current again, as a new version.

    The action is ``restore <version id>``; the version that was current goes
    to the history like after any save.
    """
    if not project.writable:
        raise PermissionError("the project is open read-only; open it with write=True")
    if version_id == CURRENT:
        raise ValueError("this version is already the current one")
    tree = read_version(project, version_id)
    return save_themes(project, tree, expected=expected, action=f"restore {version_id}", now=now)
