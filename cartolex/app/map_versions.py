# SPDX-License-Identifier: MIT
"""The built map versions, and where each one's placed files are.

The pinned version's files are the stages' own (``derived/map.layout/``,
``derived/map.trajectories/``, ``derived/overlays.position/``); every other built
version's are in ``versions/<id>/`` of each of those folders, under the same names
(:data:`cartolex.build.enginefiles.VERSION_FILES`). What was built is what the last
run of ``map.layout`` recorded (``measures.versions``), whatever ``maps.json`` says
since: a version marked to be built is there after the next build of the map.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["VersionPlace", "built_versions", "version_place"]


@dataclass(frozen=True)
class VersionPlace:
    """Where a built map version's files are: its ``layout`` (people, keywords, groups,
    themes), ``trajectories`` (the time windows) and ``overlays`` (the projected sets'
    ``<set>/positions.json``) folders; ``pinned`` for the pinned one."""

    id: str
    pinned: bool
    dimensions: int
    layout: Path
    trajectories: Path
    overlays: Path


def _dimensions_of(folder: Path) -> int:
    """A map's dimensions, from its people's table (``umap_z``: in space)."""
    path = folder / "umap_individuals.csv"
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            header = fh.readline()
    except OSError:
        return 2
    return 3 if "umap_z" in header.strip().split(",") else 2


def built_versions(ctx: Any) -> list[dict[str, Any]]:
    """The built map versions, the pinned one first: ``id``, ``dimensions`` (2 or 3),
    ``method``, ``note`` and ``pinned``. None before the map is built."""
    from cartolex.build.engine import built_others
    from cartolex.build.records import read_record
    from cartolex.project.maps import read_maps

    record = read_record(ctx.layout, "map.layout")
    if record is None:
        return []
    try:
        maps = read_maps(ctx.layout)[0]
    except Exception:  # noqa: BLE001 - a maps.json that does not read: what was built still shows
        maps = None
    listed = {v.id: v for v in maps.versions} if maps is not None else {}
    listed_measures = [
        m
        for m in (record.measures.model_extra or {}).get("versions") or []
        if isinstance(m, dict) and m.get("id")
    ]
    measured = {str(m["id"]): m for m in listed_measures}
    drawn = record.measures.counts.get("version")
    if listed_measures:  # the run's own account: the pinned version first
        pinned_id: str | None = str(listed_measures[0]["id"])
    else:
        pinned_id = f"v{drawn}" if drawn else (maps.pinned if maps is not None else None)
    folder = ctx.layout.stage("map.layout")
    out = []
    rank = {vid: k for k, vid in enumerate(listed)}  # the file's order, then by id
    others = sorted(
        (v for v in built_others(folder) if v != pinned_id), key=lambda v: (rank.get(v, 1e9), v)
    )
    for vid in [pinned_id, *others]:
        if vid is None:
            continue
        version = listed.get(vid)
        measure = measured.get(vid) or {}
        where = folder if vid == pinned_id else folder / "versions" / vid
        out.append(
            {
                "id": vid,
                "dimensions": int(measure.get("dimensions") or _dimensions_of(where)),
                "method": measure.get("method") or (version.layout.method if version else None),
                "note": version.note if version else "",
                "pinned": vid == pinned_id,
            }
        )
    return out


def version_place(ctx: Any, version: str | None = None) -> VersionPlace | None:
    """Where the files of built map version *version* are (the pinned one by default);
    ``None`` when no such version is built."""
    versions = built_versions(ctx)
    if not versions:
        return None
    found = (
        versions[0] if version is None else next((v for v in versions if v["id"] == version), None)
    )
    if found is None:
        return None
    layout = ctx.layout
    stages = ("map.layout", "map.trajectories", "overlays.position")
    folders = [layout.stage(s) for s in stages]
    if not found["pinned"]:
        folders = [f / "versions" / found["id"] for f in folders]
    return VersionPlace(found["id"], found["pinned"], int(found["dimensions"]), *folders)
