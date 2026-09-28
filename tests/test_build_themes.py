# SPDX-License-Identifier: MIT
"""Theme trees of every depth through a project build (the S demo world).

- depths 1, 3 and 4 build end to end with ``cartolex build``, and the map bundle
  of each reads back;
- at depth 2 the tables of any depth equal the engine's two-level outputs
  exactly, for the proposal and for a curated tree with keywords on themes and
  attributions;
- the rebase proposes each new keyword the node of the curated tree that holds
  a majority of its group in the new grouping.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cartolex.atlas.map_bundle import BUNDLE_SCHEMA, BUNDLE_SCHEMA_THEMES, read_bundle, write_bundle
from cartolex.atlas.model_files import load_lexical_data
from cartolex.build.bundle import project_bundle
from cartolex.build.engine import _vocabulary, proposed_places
from cartolex.cli import main as cli
from cartolex.demo import generate
from cartolex.demo.project import write_project
from cartolex.lexicon.subfields import researcher_group_weights, term_to_group_maps
from cartolex.project import Project
from cartolex.project.models import ThemesFile
from cartolex.project.themes import new_tree, node_level
from cartolex.project.themes_curated import from_curated
from cartolex.project.themes_versions import read_themes, save_themes

TWO_LEVEL_FILES = {
    "themes.group": ("subfields_draft.json",),
    "themes.apply": ("subfields.json", "subfield_weights.csv", "lexicon_weights.csv"),
    "map.layout": ("subfields.json", "lexicon_weights.csv"),
}


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """The S demo world as a project, built by the command line at its rule's depth."""
    root = tmp_path_factory.mktemp("s") / "project"
    write_project(generate("S", 0), root).close()
    assert cli(["params", str(root), "--set", "pinned_year=2026"]) == 0
    assert cli(["build", str(root)]) == 0
    return root


def _copy(built: Path, tmp_path: Path, *settings: str) -> Path:
    root = tmp_path / "project"
    shutil.copytree(built, root)
    if settings:
        assert cli(["params", str(root), "--set", *settings]) == 0
    assert cli(["build", str(root)]) == 0
    return root


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_depth(root: Path, depth: int) -> None:
    project = Project.open(root)
    derived = project.layout.derived
    record = _json(project.layout.run_json("themes.group"))
    counts = record["measures"]["counts"]
    assert counts["depth"] == depth
    sizes = [counts[f"groups_level_{i}"] for i in range(1, depth + 1)]
    assert sizes == sorted(sizes)
    proposal = ThemesFile.model_validate(_json(derived / "themes.group" / "themes_draft.json"))
    assert proposal.depth == depth
    assert all(node_level(proposal, n) == depth for n in proposal.keywords.values())
    applied = _json(derived / "map.layout" / "themes_applied.json")
    assert applied["depth"] == depth and applied["source"] == "draft"
    assert [n["id"] for n in applied["nodes"]] == [n.id for n in proposal.nodes]
    assert all(n["x"] is not None for n in applied["nodes"] if n["weight"] > 0)
    people = pd.read_parquet(derived / "themes.apply" / "theme_people.parquet")
    assert set(people["level"]) == set(range(1, depth + 1))
    assert np.allclose(people.groupby(["researcher_id", "level"])["share"].sum(), 1.0)
    assert (people["person_id"] != "").all()
    windows = pd.read_parquet(derived / "map.trajectories" / "trajectory_themes.parquet")
    assert set(windows["level"]) <= set(range(1, depth + 1)) and len(windows)
    positions = _json(derived / "overlays.position" / "applicants" / "positions.json")
    levels = [lv["level"] for lv in positions["items"][0]["levels"]]
    assert levels == sorted(levels) and set(levels) <= set(range(1, depth + 1))
    for stage, names in TWO_LEVEL_FILES.items():
        for name in names:
            assert (derived / stage / name).exists() == (depth == 2), (stage, name)
    project.close()


def _check_bundle(root: Path, tmp_path: Path, depth: int) -> None:
    project = Project.open(root)
    bundle = project_bundle(project, build_date="2026-09-28")
    project.close()
    assert bundle.meta["schema"] == BUNDLE_SCHEMA_THEMES and bundle.themes["depth"] == depth
    for dest in (tmp_path / "bundle", tmp_path / "bundle.zip"):
        back = read_bundle(write_bundle(bundle, dest))
        assert back.themes == json.loads(json.dumps(bundle.themes))
        pd.testing.assert_frame_equal(
            back.theme_weights.sort_values(["entity_id", "level", "node"]).reset_index(drop=True),
            bundle.theme_weights.sort_values(["entity_id", "level", "node"]).reset_index(drop=True),
            check_dtype=False,
        )
        a, b = back.to_cohort_input(), bundle.to_cohort_input()
        assert a.terms == b.terms and np.array_equal(np.asarray(a.X_tf), np.asarray(b.X_tf))


@pytest.mark.models("en", "fr")
def test_the_depth_rule_gives_one_level_on_the_s_world(built, tmp_path):
    record = _json(built / "derived" / "themes.group" / "run.json")
    assert record["parameters"]["depth"] == {"value": 1, "from": "rule", "rule": "theme_depth"}
    _check_depth(built, 1)
    _check_bundle(built, tmp_path, 1)


@pytest.mark.models("en", "fr")
@pytest.mark.parametrize("depth", [3, 4])
def test_deeper_trees_build_end_to_end(built, tmp_path, depth):
    root = _copy(built, tmp_path, f"themes.group.depth={depth}")
    _check_depth(root, depth)
    _check_bundle(root, tmp_path, depth)


@pytest.fixture(scope="module")
def two_levels(built, tmp_path_factory) -> Path:
    return _copy(built, tmp_path_factory.mktemp("d2"), "themes.group.depth=2")


def _same_numbers(root: Path, stage: str) -> None:
    """The tables of any depth equal the two-level outputs of *stage*, exactly."""
    folder = root / "derived" / stage
    applied = _json(folder / "themes_applied.json")
    legacy = _json(folder / "subfields.json")
    nodes = {n["id"]: n for n in applied["nodes"]}
    tree = ThemesFile.model_validate(_json(root / "derived" / "themes.apply" / "themes_tree.json"))
    theme_of = {}
    for s in legacy["subfields"]:
        node = s.get("theme_node", {}).get("id") or f"s{s['id']}"
        theme_of[s["id"]] = node
        assert (nodes[node]["weight"], nodes[node]["share"]) == (s["weight"], s["share"])
    for c in legacy["concepts"]:
        if c.get("theme_keywords_of"):
            assert c["weight"] == 0.0  # the theme's own keywords count toward the theme only
            continue
        node = c.get("theme_node", {}).get("id") or f"c{c['id']}"
        assert (nodes[node]["weight"], nodes[node]["share"]) == (c["weight"], c["share"])
        assert node_level(tree, node) == 2 and tree.nodes
    keywords = pd.read_csv(root / "derived" / "themes.apply" / "theme_keywords.csv")
    lexicon = pd.read_csv(folder / "lexicon_weights.csv")
    mine = keywords.set_index("term_index").loc[lexicon["term_index"]]
    assert mine["weight"].tolist() == lexicon["weight"].tolist()
    assert mine["share"].tolist() == lexicon["share"].tolist()
    assert set(keywords["term_index"]) == set(lexicon["term_index"])


@pytest.mark.models("en", "fr")
def test_at_depth_2_the_generic_tables_equal_the_two_level_outputs(two_levels):
    _check_depth(two_levels, 2)
    _same_numbers(two_levels, "themes.apply")
    _same_numbers(two_levels, "map.layout")
    derived = two_levels / "derived"
    # the proposal is the two-level draft read as a tree (themes named in French too)
    terms, _ = _vocabulary(derived / "themes.space")
    draft = from_curated(_json(derived / "themes.group" / "subfields_draft.json"), terms).tree
    proposal = ThemesFile.model_validate(_json(derived / "themes.group" / "themes_draft.json"))
    assert draft.keywords == proposal.keywords and draft.levels == proposal.levels
    for a, b in zip(draft.nodes, proposal.nodes, strict=True):
        assert (a.id, a.parent, a.order, a.names["en"]) == (b.id, b.parent, b.order, b.names["en"])
        if a.parent is not None:
            assert a.names == b.names
    # each person's shares are the two-level attribution of their keywords
    data = load_lexical_data(derived / "themes.space" / "models" / "lexical_data.json")
    legacy = _json(derived / "themes.apply" / "subfields.json")
    to_concept, to_subfield, _, _ = term_to_group_maps(legacy, list(data.terms))
    people = pd.read_parquet(derived / "themes.apply" / "theme_people.parquet")
    X = data.X_tf.tocsr()
    ids = data.meta_ind["id"].astype(str).tolist()
    for i, rid in enumerate(ids):
        row = X.getrow(i)
        scored = [(data.terms[j], v) for j, v in zip(row.indices, row.data, strict=True)]
        themes, topics = researcher_group_weights(
            scored, term_to_concept=to_concept, concept_to_subfield=to_subfield
        )
        mine = people[people["researcher_id"] == rid]
        for level, want, prefix in ((1, themes, "s"), (2, topics, "c")):
            got = mine[mine["level"] == level].set_index("node")["share"]
            assert {f"{prefix}{w['id']}" for w in want} == set(got.index)
            for w in want:
                assert round(float(got[f"{prefix}{w['id']}"]), 4) == pytest.approx(
                    w["weight"], abs=1e-4
                )


@pytest.mark.models("en", "fr")
def test_a_curated_two_level_tree_gives_the_same_numbers_both_ways(two_levels, tmp_path):
    from cartolex.project.themes import move_keywords, set_attribution

    root = tmp_path / "project"
    shutil.copytree(two_levels, root)
    project = Project.open(root, write=True)
    terms, _ = _vocabulary(project.layout.stage("themes.space"))
    tree = from_curated(
        _json(project.layout.stage("themes.group") / "subfields_draft.json"), terms
    ).tree
    themes = [n.id for n in tree.nodes if n.parent is None]
    topics = sorted(tree.keywords)
    tree = move_keywords(tree, topics[:6], themes[0]).tree  # keywords on a theme itself
    tree = set_attribution(tree, topics[10:14], 1).tree  # counted toward the theme only
    tree = set_attribution(tree, topics[20:23] + topics[:2], 0).tree  # shown, counted nowhere
    save_themes(project, tree, expected=None, action="curate")
    project.close()
    assert cli(["build", str(root)]) == 0
    applied = _json(root / "derived" / "themes.apply" / "themes_applied.json")
    assert applied["source"] == "decisions"
    assert (root / "derived" / "themes.apply" / "curated.json").exists()
    _same_numbers(root, "themes.apply")
    _same_numbers(root, "map.layout")


@pytest.mark.models("en", "fr")
def test_a_curated_deeper_tree_is_applied_and_rebased(built, tmp_path):
    from cartolex.project.themes import insert_level, rename_node

    root = _copy(built, tmp_path, "themes.group.depth=3")
    project = Project.open(root, write=True)
    proposal = ThemesFile.model_validate(
        _json(project.layout.stage("themes.group") / "themes_draft.json")
    )
    tree = insert_level(proposal, 1).tree  # a domain over everything: depth 4
    tree = rename_node(tree, tree.nodes[0].id, {"en": "Everything"}).tree
    save_themes(project, tree, expected=None, action="curate")
    project.close()
    # a lower threshold brings new keywords: the rebase places them before the apply stage
    assert cli(["params", str(root), "--set", "keywords.extract.min_people=2"]) == 0
    assert cli(["build", str(root)]) == 0
    project = Project.open(root)
    rebased, _ = read_themes(project)
    assert rebased.depth == 4
    new = [k for k, state in rebased.review.items() if state == "to_check"]
    assert new, "the lower threshold should add keywords"
    placed = [k for k in new if k in rebased.keywords]
    assert placed, "some new keywords should find a node"
    applied = _json(project.layout.stage("map.layout") / "themes_applied.json")
    assert applied["depth"] == 4 and applied["nodes"][0]["names"]["en"] == "Everything"
    people = pd.read_parquet(project.layout.stage("themes.apply") / "theme_people.parquet")
    assert set(people["level"]) == {1, 2, 3, 4}
    project.close()


# ── the rebase proposal: a majority of the group's known keywords ────────────


def _tree(depth: int, nodes: list[tuple[str, str | None]], keywords: dict[str, str]) -> ThemesFile:
    base = new_tree(depth).model_dump(mode="json", by_alias=True)
    base["nodes"] = [
        {"id": i, "parent": p, "names": {"en": i}, "order": k} for k, (i, p) in enumerate(nodes, 1)
    ]
    base["keywords"] = keywords
    return ThemesFile.model_validate(base)


NODES = [("t1", None), ("a", "t1"), ("b", "t1"), ("t2", None), ("c", "t2")]


def _proposal(groups: dict[str, list[str]]) -> dict:
    return {"keywords": {k: g for g, ks in groups.items() for k in ks}}


def test_a_new_keyword_goes_where_most_of_its_group_is():
    tree = _tree(2, NODES, {"k1": "a", "k2": "a", "k3": "b", "k4": "c"})
    proposal = _proposal({"g": ["k1", "k2", "k3", "new"]})
    assert proposed_places(tree, proposal, ["new"]) == {"new": "a"}


def test_a_tie_below_goes_up_to_the_level_with_a_majority():
    tree = _tree(2, NODES, {"k1": "a", "k2": "b", "k3": "c"})
    assert proposed_places(tree, _proposal({"g": ["k1", "k2", "new"]}), ["new"]) == {"new": "t1"}
    # a keyword on a theme itself votes only on the theme's level
    tree = _tree(2, NODES, {"k1": "a", "k2": "t1", "k3": "a"})
    assert proposed_places(tree, _proposal({"g": ["k1", "k2", "k3", "n"]}), ["n"]) == {"n": "a"}
    tree = _tree(2, NODES, {"k1": "a", "k2": "t1"})
    assert proposed_places(tree, _proposal({"g": ["k1", "k2", "n"]}), ["n"]) == {"n": "t1"}


def test_no_majority_or_no_known_keyword_sets_the_new_keyword_aside():
    tree = _tree(2, NODES, {"k1": "a", "k2": "c"})
    assert proposed_places(tree, _proposal({"g": ["k1", "k2", "new"]}), ["new"]) == {"new": None}
    tree = _tree(2, NODES, {"k1": "a"})
    proposal = _proposal({"g": ["k1"], "h": ["new", "other new"]})
    assert proposed_places(tree, proposal, ["new", "other new", "absent"]) == {
        "new": None,
        "other new": None,
        "absent": None,
    }
    # set-aside keywords do not vote
    base = tree.model_dump(mode="json", by_alias=True)
    base["set_aside"] = {"k2": {"from": "c", "reason": ""}, "k3": {"from": "c", "reason": ""}}
    tree = ThemesFile.model_validate(base)
    assert proposed_places(tree, _proposal({"g": ["k1", "k2", "k3", "n"]}), ["n"]) == {"n": "a"}


def test_the_legacy_bundle_version_is_kept_without_a_tree(two_levels):
    project = Project.open(two_levels)
    bundle = project_bundle(project)
    project.close()
    assert bundle.meta["schema"] == BUNDLE_SCHEMA_THEMES
    from cartolex.atlas.map_bundle import build_bundle

    plain = build_bundle(bundle.to_cohort_input())
    assert plain.meta["schema"] == BUNDLE_SCHEMA and plain.themes is None
