# SPDX-License-Identifier: MIT
"""Map versions (``decisions/maps.json``): add, pin, try another layout.

A map version records what a map shows and how it was drawn (layout method,
seed, parameters, an optional base). One version is pinned: rebuilds reuse its
layout, so the map people know does not move. « Try another layout » adds a
version beside it; the pinned one changes only when someone pins another.
Versions are never edited in place; :func:`discard` removes one that is not
pinned (the file's history keeps it, like every earlier version).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from .files import fingerprint, json_bytes, read_model, write_decision
from .layout import ProjectLayout
from .models import MapLayout, MapsFile, MapVersion

__all__ = ["add_version", "discard", "pin", "pinned", "read_maps", "save_maps", "try_another"]


def read_maps(layout: ProjectLayout) -> tuple[MapsFile, str | None]:
    """The map versions and the file's fingerprint (no versions when the file is missing)."""
    if not layout.maps_json.exists():
        return MapsFile(), None
    return read_model(layout.maps_json, MapsFile), fingerprint(layout.maps_json)  # type: ignore[return-value]


def save_maps(layout: ProjectLayout, maps: MapsFile, *, expected: str | None, action: str) -> str:
    """Write the map versions, guarded by the fingerprint read (see :func:`write_decision`)."""
    return write_decision(
        layout, layout.maps_json, json_bytes(maps), expected=expected, action=action
    )


def _next_id(maps: MapsFile) -> str:
    numbers = [int(m.group(1)) for v in maps.versions if (m := re.fullmatch(r"v(\d+)", v.id))]
    return f"v{max(numbers, default=0) + 1}"


def add_version(
    maps: MapsFile,
    *,
    shows: Sequence[str] = ("people",),
    method: str = "umap",
    seed: int = 0,
    params: dict[str, Any] | None = None,
    base: str | None = None,
    note: str = "",
    pin_it: bool | None = None,
    now: datetime | None = None,
) -> tuple[MapsFile, str]:
    """A new version (``v1``, ``v2``…); the first one is pinned unless *pin_it* says otherwise."""
    version = MapVersion(
        id=_next_id(maps),
        shows=list(shows),
        layout=MapLayout(method=method, seed=seed, params=dict(params or {})),
        base=base,
        created_at=now or datetime.now(timezone.utc),
        note=note,
    )
    pinned_id = maps.pinned
    if pin_it or (pin_it is None and maps.pinned is None):
        pinned_id = version.id
    return maps.model_copy(update={"versions": [*maps.versions, version]}).model_copy(
        update={"pinned": pinned_id}
    ), version.id


def try_another(
    maps: MapsFile,
    *,
    seed: int,
    method: str | None = None,
    note: str = "",
    now: datetime | None = None,
) -> tuple[MapsFile, str]:
    """A new version drawn like the pinned one, with another seed (or method); the pin stays."""
    current = pinned(maps)
    if current is None:
        raise ValueError("no pinned map version to start from; add a version first")
    return add_version(
        maps,
        shows=current.shows,
        method=method or current.layout.method,
        seed=seed,
        params=current.layout.params,
        base=current.base,
        note=note,
        pin_it=False,
        now=now,
    )


def pin(maps: MapsFile, version_id: str) -> MapsFile:
    """Pin *version_id*; later builds reuse its layout."""
    if version_id not in {v.id for v in maps.versions}:
        raise KeyError(f"no map version {version_id!r}")
    return maps.model_copy(update={"pinned": version_id})


def discard(maps: MapsFile, version_id: str) -> MapsFile:
    """Remove *version_id*, a version that is not pinned (a tried layout nobody kept)."""
    if version_id not in {v.id for v in maps.versions}:
        raise KeyError(f"no map version {version_id!r}")
    if version_id == maps.pinned:
        raise ValueError(f"{version_id} is pinned: pin another version before discarding it")
    return maps.model_copy(update={"versions": [v for v in maps.versions if v.id != version_id]})


def pinned(maps: MapsFile) -> MapVersion | None:
    """The pinned version, if any."""
    return next((v for v in maps.versions if v.id == maps.pinned), None)
