# SPDX-License-Identifier: MIT
"""Saving the theme tree and its versions (cartolex.project.themes_versions)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from themes_random import random_edit, random_tree

from cartolex.project import Project, StaleWrite, fingerprint, json_bytes
from cartolex.project.themes import canonical, create_node, new_tree, rebase, rename_node
from cartolex.project.themes_versions import (
    CURRENT,
    list_versions,
    read_themes,
    read_version,
    restore_version,
    save_themes,
)

NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)


def _project(tmp_path) -> Project:
    return Project.init(
        tmp_path / "proj", name="Coastal systems map", domain_title="Coastal systems", now=NOW
    )


def test_save_read_and_list_versions(tmp_path):
    project = _project(tmp_path)
    assert read_themes(project) == (None, None)
    assert list_versions(project) == []
    first = create_node(new_tree(), None, {"en": "Hazards"})
    fp1 = save_themes(project, first.tree, expected=None, action="create", now=NOW)
    tree, fp = read_themes(project.layout)
    assert tree == first.tree and fp == fp1 == fingerprint(project.layout.themes_json)
    second = rename_node(tree, "n1", {"fr": "Aléas"})
    t2 = NOW + timedelta(minutes=5)
    fp2 = save_themes(project, second.tree, expected=fp1, action=second.description, now=t2)
    versions = list_versions(project)
    assert [v.id for v in versions] == [CURRENT, "20260928T100500Z-rename-n1"]
    current, earlier = versions
    assert (current.made_by, current.made_at) == ("rename-n1", t2)
    assert (current.replaced_by, current.replaced_at) == (None, None)
    assert (earlier.made_by, earlier.made_at) == (None, None)  # the first version
    assert (earlier.replaced_by, earlier.replaced_at) == ("rename-n1", t2)
    assert read_version(project, earlier.id) == first.tree
    assert read_version(project, CURRENT) == second.tree
    with pytest.raises(StaleWrite):
        save_themes(project, first.tree, expected=fp1, action="blind", now=t2)
    assert fingerprint(project.layout.themes_json) == fp2
    project.close()


def test_saving_the_current_tree_again_writes_nothing(tmp_path):
    project = _project(tmp_path)
    tree = create_node(new_tree(), None, {"en": "Hazards"}).tree
    fp = save_themes(project, tree, expected=None, action="create", now=NOW)
    assert save_themes(project, tree, expected=fp, action="again", now=NOW) == fp
    assert [v.id for v in list_versions(project)] == [CURRENT]
    project.close()


def test_saves_are_canonical(tmp_path):
    project = _project(tmp_path)
    tree = create_node(create_node(new_tree(), None, {"en": "b"}).tree, None, {"en": "a"}).tree
    doc = tree.model_dump(mode="json", by_alias=True)
    doc["nodes"].reverse()
    shuffled = type(tree).model_validate(doc)
    save_themes(project, shuffled, expected=None, action="create", now=NOW)
    assert project.layout.themes_json.read_bytes() == json_bytes(canonical(tree))
    project.close()


def test_a_read_only_project_refuses_to_save(tmp_path):
    _project(tmp_path).close()
    project = Project.open(tmp_path / "proj")
    with pytest.raises(PermissionError):
        save_themes(project, new_tree(), expected=None, action="create")
    with pytest.raises(PermissionError):
        restore_version(project, "20260928T100000Z-x", expected=None)


def test_unknown_versions(tmp_path):
    project = _project(tmp_path)
    for bad in ("nope", "../themes", "20260928T100000Z-x", CURRENT):
        with pytest.raises(KeyError):
            read_version(project, bad)
    with pytest.raises(ValueError, match="already the current"):
        restore_version(project, CURRENT, expected=None)
    project.close()


def test_a_rebase_is_a_version_and_its_reconciliation_can_be_recomputed(tmp_path):
    from cartolex.project.themes import compare

    project = _project(tmp_path)
    tree = create_node(new_tree(), None, {"en": "Hazards"}).tree
    tree = create_node(tree, "n1", {"en": "Surge"}).tree
    tree = rebase(tree, ["storm surge model"], {"storm surge model": "n2"}, run="r1").tree
    fp = save_themes(project, tree, expected=None, action="rebase", now=NOW)
    result = rebase(tree, ["tidal inlet"], {"tidal inlet": None}, run="r2")
    save_themes(project, result.tree, expected=fp, action="rebase", now=NOW + timedelta(hours=1))
    current, before = list_versions(project)
    assert current.made_by == "rebase"
    assert compare(read_version(project, before.id), read_version(project, current.id)) == (
        result.changes
    )
    project.close()


@pytest.mark.parametrize("seed", range(25))
def test_versions_and_restores_round_trip(seed, tmp_path):
    with _project(tmp_path) as project:
        _round_trip(random.Random(seed), project)


def _round_trip(rng: random.Random, project: Project) -> None:
    tree = random_tree(rng)
    saved = [canonical(tree)]
    now = NOW
    fp = save_themes(project, tree, expected=None, action="create", now=now)
    for _ in range(rng.randint(1, 6)):
        edit = random_edit(rng, tree)
        if edit is None or edit.tree == tree:
            continue
        now += timedelta(seconds=rng.choice([0, 1, 30]))  # same-second saves too
        fp = save_themes(project, edit.tree, expected=fp, action=edit.description, now=now)
        tree = edit.tree
        saved.append(tree)
    versions = list_versions(project)
    assert len(versions) == len(saved)
    assert [read_version(project, v.id) for v in reversed(versions)] == saved
    for older, newer in zip(reversed(versions[1:]), reversed(versions[:-1]), strict=True):
        assert (newer.made_at, newer.made_by) == (older.replaced_at, older.replaced_by)
    target = rng.choice(versions)
    if target.id == CURRENT:
        return
    wanted = read_version(project, target.id)
    fp = restore_version(project, target.id, expected=fp, now=now + timedelta(minutes=1))
    assert read_themes(project) == (wanted, fp)
    after = list_versions(project)
    if wanted == saved[-1]:  # restoring the current tree writes nothing
        assert len(after) == len(saved)
        return
    assert len(after) == len(saved) + 1
    assert after[0].made_by.startswith("restore-")
    assert read_version(project, after[1].id) == saved[-1]  # the replaced version is kept
