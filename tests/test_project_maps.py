# SPDX-License-Identifier: MIT
"""Map versions: the first is pinned, another layout never moves the pin, writes are guarded."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cartolex.project import Project, StaleWrite
from cartolex.project.maps import (
    add_version,
    discard,
    pin,
    pinned,
    read_maps,
    save_maps,
    try_another,
)
from cartolex.project.models import MapsFile

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def test_versions_and_the_pin():
    maps, first = add_version(MapsFile(), seed=7, now=NOW)
    assert (first, maps.pinned) == ("v1", "v1")
    maps, second = try_another(maps, seed=8, now=NOW)
    assert second == "v2" and maps.pinned == "v1"
    assert pinned(maps).layout.seed == 7
    maps = pin(maps, "v2")
    assert pinned(maps).layout.seed == 8
    maps, third = add_version(maps, shows=["people", "organisations:lab"], method="tsne", now=NOW)
    assert third == "v3" and maps.pinned == "v2"
    with pytest.raises(KeyError):
        pin(maps, "v9")
    with pytest.raises(ValueError, match="no pinned"):
        try_another(MapsFile(), seed=1)


def test_a_version_nobody_pinned_can_be_discarded():
    maps, _ = add_version(MapsFile(), seed=7, now=NOW)
    maps, tried = try_another(maps, seed=8, now=NOW)
    with pytest.raises(ValueError, match="pinned"):
        discard(maps, "v1")
    with pytest.raises(KeyError):
        discard(maps, "v9")
    kept = discard(maps, tried)
    assert [v.id for v in kept.versions] == ["v1"] and kept.pinned == "v1"
    maps, again = try_another(kept, seed=9, now=NOW)
    assert again == "v2"  # ids restart after the highest kept


def test_saving_is_guarded(tmp_path):
    project = Project.init(tmp_path / "p", name="n", domain_title="t", now=NOW)
    layout = project.layout
    maps, fp = read_maps(layout)
    assert maps.versions == [] and fp is None
    maps, _ = add_version(maps, now=NOW)
    fp = save_maps(layout, maps, expected=None, action="first map")
    again, fp2 = read_maps(layout)
    assert again == maps and fp2 == fp
    with pytest.raises(StaleWrite):
        save_maps(layout, maps, expected=None, action="blind")
    project.close()
