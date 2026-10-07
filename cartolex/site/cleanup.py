# SPDX-License-Identifier: MIT
"""What sharing keeps on disk, and deleting it: site builds, their zips, exported files.

- :func:`disk` says what each build takes (its files, and its zip once downloaded) and
  the totals: builds, zips, exported files, and what interrupted work left behind
  (:func:`leftovers`: an unfinished build's hidden folder, a zip whose build is gone, a
  zip half written).
- :func:`delete_build` removes a build and its zip; the ``latest`` marker then names
  the newest build left. :func:`delete_older` removes every build but the latest, and
  the leftovers. :func:`delete_zip` removes a build's zip only (it is written again
  when downloaded). :func:`delete_export` removes an exported file and the
  ``<stem>.meta.json`` written beside it.

Only entries directly inside ``outputs/sites/``, its ``.zips/`` or ``outputs/exports/``
are removed; a link is removed as a link, never followed. The app refuses these while a
site build or an export runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cartolex.project.files import atomic_write_bytes
from cartolex.project.folders import is_link, remove_entry, tree_size

from .builder import _latest, _sites
from .exports import exports_folder

if TYPE_CHECKING:
    from cartolex.project import Project

__all__ = ["delete_build", "delete_export", "delete_older", "delete_zip", "disk", "leftovers"]

META = ".meta.json"


def _zip(project: Project, build_id: str) -> Path:
    return _sites(project) / ".zips" / f"{build_id}.zip"


def _builds(folder: Path) -> list[str]:
    """The builds' ids, the newest first (hidden folders and links left out)."""
    if not folder.is_dir():
        return []
    return sorted((p.name for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")
                   and not is_link(p)), reverse=True)  # fmt: skip


def _size(path: Path) -> int:
    return tree_size(path)[0] if path.exists() or is_link(path) else 0


def leftovers(project: Project) -> list[Path]:
    """What interrupted work left in ``outputs/sites/``: unfinished builds, zips of builds
    that are gone, zips half written."""
    folder = _sites(project)
    if not folder.is_dir():
        return []
    out = [p for p in folder.iterdir() if p.name.startswith(".building-")]
    zips = folder / ".zips"
    if zips.is_dir() and not is_link(zips):
        builds = set(_builds(folder))
        for p in zips.iterdir():
            if not (p.name.endswith(".zip") and p.name[:-4] in builds):
                out.append(p)
    return sorted(out)


def disk(project: Project, builds: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Fill each of *builds* (``list_builds``' entries) with ``zip_size`` and ``disk`` (its
    files and its zip, in bytes), and answer the totals: ``sites``, ``zips``, ``exports``,
    ``leftovers``, ``total``, and ``older`` (what :func:`delete_older` would free:
    ``count``, ``bytes``)."""
    sites = zips = 0
    older_count = older_bytes = 0
    for build in builds:
        folder = _sites(project) / build["id"]
        # the record's sum of the files it wrote, and the record itself; else walk the folder
        size = build["size"] + _size(folder / "site.json") if build.get("size") else _size(folder)
        zipped = _size(_zip(project, build["id"]))
        build["zip_size"] = zipped
        build["disk"] = size + zipped
        sites += size
        zips += zipped
        if not build.get("latest"):
            older_count += 1
            older_bytes += size + zipped
    if builds and not any(b.get("latest") for b in builds):  # no marker: the newest stays
        older_count -= 1
        older_bytes -= builds[0]["disk"]
    left = sum(_size(p) for p in leftovers(project))
    folder = exports_folder(project)
    exports = (
        sum(_size(p) for p in folder.iterdir() if not p.name.startswith("."))
        if folder.is_dir() else 0
    )  # fmt: skip
    return {
        "sites": sites,
        "zips": zips,
        "exports": exports,
        "leftovers": left,
        "total": sites + zips + exports + left,
        "older": {"count": older_count, "bytes": older_bytes + left},
    }


def _set_latest(folder: Path) -> str | None:
    """After a deletion: the ``latest`` marker names the newest build left (or goes)."""
    left = _builds(folder)
    marker = folder / "latest"
    if left:
        if _latest(folder) not in left:
            atomic_write_bytes(marker, f"{left[0]}\n".encode())
        return _latest(folder)
    marker.unlink(missing_ok=True)
    return None


def delete_build(project: Project, build_id: str) -> dict[str, Any] | None:
    """Delete the build *build_id* and its zip: ``{id, bytes, latest}`` (the build named
    ``latest`` now), or ``None`` when there is no such build."""
    folder = _sites(project)
    path = folder / build_id
    if build_id.startswith(".") or build_id not in _builds(folder):
        return None
    freed = _size(path) + _size(_zip(project, build_id))
    remove_entry(path, folder)
    zipped = _zip(project, build_id)
    if zipped.exists() or is_link(zipped):
        remove_entry(zipped, zipped.parent)
    return {"id": build_id, "bytes": freed, "latest": _set_latest(folder)}


def delete_zip(project: Project, build_id: str) -> int | None:
    """Delete a build's zip (written again when it is downloaded): the bytes freed, or
    ``None`` when the build has no zip."""
    zipped = _zip(project, build_id)
    if build_id.startswith(".") or not (zipped.is_file() or is_link(zipped)):
        return None
    freed = _size(zipped)
    remove_entry(zipped, zipped.parent)
    return freed


def delete_older(project: Project, *, plan: bool = False) -> dict[str, Any]:
    """Delete every build but the latest (the newest when no build is marked) with their zips,
    and the leftovers of interrupted work: ``{ids, count, bytes}``. With *plan*, only say so."""
    folder = _sites(project)
    builds = _builds(folder)
    keep = _latest(folder) if _latest(folder) in builds else (builds[0] if builds else None)
    ids = [b for b in builds if b != keep]
    left = leftovers(project)
    freed = sum(_size(folder / b) + _size(_zip(project, b)) for b in ids)
    freed += sum(_size(p) for p in left)
    if not plan:
        for b in ids:
            delete_build(project, b)
        for p in leftovers(project):
            remove_entry(p, p.parent)
    return {"ids": ids, "count": len(ids), "leftovers": len(left), "bytes": freed, "kept": keep}


def delete_export(project: Project, name: str) -> dict[str, Any] | None:
    """Delete the exported file *name* and the ``<stem>.meta.json`` beside it: ``{name, bytes,
    files}``, or ``None`` when there is no such file."""
    folder = exports_folder(project)
    path = folder / name
    if name.startswith(".") or "/" in name or "\\" in name or not (path.is_file() or is_link(path)):
        return None
    paths = [path]
    meta = folder / f"{path.stem}{META}"  # how the distances' exports name it
    if not name.endswith(META) and (meta.is_file() or is_link(meta)):
        paths.append(meta)
    freed = sum(_size(p) for p in paths)
    for p in paths:
        remove_entry(p, folder)
    return {"name": name, "bytes": freed, "files": [p.name for p in paths]}
