# SPDX-License-Identifier: MIT
"""Saving the theme tree and its versions (cartolex.project.themes_versions)."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from themes_random import random_edit, random_tree

from cartolex.project import Project, StaleWrite, fingerprint, json_bytes
from cartolex.project.models import ThemesFile
from cartolex.project.themes import (
    canonical,
    compare,
    create_node,
    new_tree,
    prune_empty,
    rebase,
    rename_node,
)
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


def _tree() -> ThemesFile:
    """Hazards › Surge (two keywords) and Hazards › Erosion (empty), Ecology (empty)."""
    tree = create_node(new_tree(), None, {"en": "Hazards"}).tree
    tree = create_node(tree, "n1", {"en": "Surge"}).tree
    tree = create_node(tree, "n1", {"en": "Erosion"}).tree
    tree = create_node(tree, None, {"en": "Ecology"}).tree
    vocab = {"storm surge model": "n2", "coastal flooding": "n2"}
    return rebase(tree, vocab, vocab, run="r1").tree


def _unstamped(tree: ThemesFile) -> ThemesFile:
    return tree.model_copy(update={"saved": None})


def test_save_prunes_empty_nodes_and_names_them(tmp_path):
    with _project(tmp_path) as project:
        saved = save_themes(project, _tree(), expected=None, action="create", now=NOW)
        assert saved.removed == ("n3", "n4")
        assert saved.action == "create; remove empty nodes n3, n4"
        tree, fp = read_themes(project)
        assert tree == saved.tree and fp == saved.fingerprint
        assert [n.id for n in tree.nodes] == ["n1", "n2"]
        assert _unstamped(tree) == prune_empty(_tree()).tree
        assert tree.saved is not None
        assert (tree.saved.at, tree.saved.action) == (NOW, saved.action)
        again = save_themes(project, tree, expected=fp, action="rename", now=NOW)
        assert again.removed == () and again.action == "rename" and not again.written


def test_prune_empty_rule():
    edit = prune_empty(_tree())
    assert edit.description == "remove empty nodes n3, n4"
    assert prune_empty(edit.tree).description == "no empty node"
    assert prune_empty(edit.tree).tree == edit.tree
    whole = create_node(new_tree(), None, {"en": "Alone"}).tree
    assert prune_empty(whole).tree.nodes == []
    assert prune_empty(whole).description == "remove empty node n1"


def test_save_read_and_list_versions(tmp_path):
    with _project(tmp_path) as project:
        assert read_themes(project) == (None, None)
        assert list_versions(project) == []
        first = save_themes(project, _tree(), expected=None, action="create", now=NOW)
        tree, fp1 = read_themes(project.layout)
        second = rename_node(tree, "n1", {"fr": "Aléas"})
        t2 = NOW + timedelta(minutes=5)
        saved2 = save_themes(project, second.tree, expected=fp1, action=second.description, now=t2)
        versions = list_versions(project)
        assert [v.id for v in versions] == [CURRENT, "20260928T100500Z-rename-n1"]
        current, earlier = versions
        assert (current.made_by, current.made_at) == ("rename n1", t2)
        assert (current.replaced_by, current.replaced_at) == (None, None)
        assert (earlier.made_by, earlier.made_at) == ("create; remove empty nodes n3, n4", NOW)
        assert (earlier.replaced_by, earlier.replaced_at) == ("rename-n1", t2)
        assert read_version(project, earlier.id) == first.tree
        assert read_version(project, CURRENT) == saved2.tree
        with pytest.raises(StaleWrite):
            save_themes(project, first.tree, expected=fp1, action="blind", now=t2)
        assert fingerprint(project.layout.themes_json) == saved2.fingerprint


def test_versions_without_a_stamp_take_the_history_names(tmp_path):
    with _project(tmp_path) as project:
        first = save_themes(project, _tree(), expected=None, action="create", now=NOW)
        path = project.layout.themes_json
        bare = json.loads(path.read_text(encoding="utf-8"))
        del bare["saved"]  # a version written by a tool that does not stamp
        path.write_text(json.dumps(bare), encoding="utf-8")
        second = rename_node(first.tree, "n1", {"fr": "Aléas"}).tree
        t2 = NOW + timedelta(minutes=5)
        save_themes(project, second, expected=fingerprint(path), action="rename n1", now=t2)
        current, earlier = list_versions(project)
        assert (earlier.made_by, earlier.made_at) == (None, None)
        assert (current.made_by, current.made_at) == ("rename n1", t2)


def test_saves_are_canonical(tmp_path):
    with _project(tmp_path) as project:
        doc = _tree().model_dump(mode="json", by_alias=True)
        doc["nodes"].reverse()
        saved = save_themes(
            project, ThemesFile.model_validate(doc), expected=None, action="create", now=NOW
        )
        assert project.layout.themes_json.read_bytes() == json_bytes(canonical(saved.tree))


def test_a_save_needs_an_action_and_a_writable_project(tmp_path):
    with _project(tmp_path) as project, pytest.raises(ValueError, match="action"):
        save_themes(project, _tree(), expected=None, action=" ")
    project = Project.open(tmp_path / "proj")
    with pytest.raises(PermissionError):
        save_themes(project, new_tree(), expected=None, action="create")
    with pytest.raises(PermissionError):
        restore_version(project, "20260928T100000Z-x", expected=None)


def test_unknown_versions(tmp_path):
    with _project(tmp_path) as project:
        for bad in ("nope", "../themes", "20260928T100000Z-x", CURRENT):
            with pytest.raises(KeyError):
                read_version(project, bad)
        with pytest.raises(ValueError, match="already the current"):
            restore_version(project, CURRENT, expected=None)


def test_a_rebase_is_a_version_and_its_reconciliation_can_be_recomputed(tmp_path):
    with _project(tmp_path) as project:
        saved = save_themes(project, _tree(), expected=None, action="create", now=NOW)
        result = rebase(saved.tree, ["storm surge model", "tidal inlet"], {"tidal inlet": "n2"})
        save_themes(
            project,
            result.tree,
            expected=saved.fingerprint,
            action=result.description,
            now=NOW + timedelta(hours=1),
        )
        current, before = list_versions(project)
        assert current.made_by == "rebase: 1 new, 1 gone"
        assert compare(read_version(project, before.id), read_version(project, current.id)) == (
            result.changes
        )


@pytest.mark.parametrize("seed", range(25))
def test_versions_and_restores_round_trip(seed, tmp_path):
    with _project(tmp_path) as project:
        _round_trip(random.Random(seed), project)


def _round_trip(rng: random.Random, project: Project) -> None:
    tree = random_tree(rng)
    now = NOW
    saved = save_themes(project, tree, expected=None, action="create", now=now)
    kept = [saved.tree]
    for _ in range(rng.randint(1, 6)):
        edit = random_edit(rng, kept[-1])
        if edit is None:
            continue
        now += timedelta(seconds=rng.choice([0, 1, 30]))  # same-second saves too
        saved = save_themes(
            project, edit.tree, expected=saved.fingerprint, action=edit.description, now=now
        )
        assert _unstamped(saved.tree) == _unstamped(prune_empty(edit.tree).tree)
        assert not [n for n in saved.tree.nodes if n.id in saved.removed]
        if saved.written:
            assert saved.tree.saved is not None and saved.tree.saved.action == saved.action
            kept.append(saved.tree)
    versions = list_versions(project)
    assert [read_version(project, v.id) for v in reversed(versions)] == kept
    assert [v.made_by for v in reversed(versions)] == [t.saved.action for t in kept]
    for older, newer in zip(reversed(versions[1:]), reversed(versions[:-1]), strict=True):
        assert newer.made_at == older.replaced_at
    target = rng.choice(versions)
    if target.id == CURRENT:
        return
    wanted = read_version(project, target.id)
    restored = restore_version(
        project, target.id, expected=saved.fingerprint, now=now + timedelta(minutes=1)
    )
    tree, fp = read_themes(project)
    assert _unstamped(tree) == _unstamped(wanted) and fp == restored.fingerprint
    after = list_versions(project)
    if not restored.written:  # the version restored is the current tree
        assert len(after) == len(kept)
        return
    assert len(after) == len(kept) + 1
    assert after[0].made_by == f"restore {target.id}"
    assert read_version(project, after[1].id) == kept[-1]  # the replaced version is kept
