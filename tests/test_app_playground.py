# SPDX-License-Identifier: MIT
"""The themes playground: a preview is what a build with its settings proposes, nothing is
saved, and a curated tree is carried onto a new grouping where its nodes continue."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from _app_helpers import TOKEN, Client

from cartolex.app import AppSettings, create_app
from cartolex.cli import main as cli
from cartolex.demo import generate
from cartolex.demo.project import write_project


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """The S demo world as a project at depth 2, built."""
    root = tmp_path_factory.mktemp("playground-s") / "project"
    write_project(generate("S", 0), root).close()
    sets = ["pinned_year=2026", "themes.group.depth=2", "themes.group.keywords_per_group=20"]
    assert cli(["params", str(root), "--set", *sets]) == 0
    assert cli(["build", str(root)]) == 0
    return root


def _client(root: Path, data: Path) -> Client:
    app = create_app(
        AppSettings(
            project=root,
            launch_token=TOKEN,
            data_dir=data,
            build_budget_mb=1e9,
            build_year=2026,
        )
    )
    return Client(app)


def _preview(client: Client, body: dict) -> dict:
    first = client.post("/api/themes/playground", json=body)
    assert first.status_code == 202, first.text
    assert client.wait_job(first.json()["job"]["id"])["state"] == "succeeded"
    again = client.post("/api/themes/playground", json=body)
    assert again.status_code == 200
    return again.json()


def _without_run(doc: dict) -> dict:
    return {**doc, "based_on": {**doc["based_on"], "run": None}}


@pytest.mark.parametrize("depth", [2, 3])
def test_a_preview_is_what_a_build_with_its_settings_proposes(built, tmp_path, depth):
    root = tmp_path / "project"
    shutil.copytree(built, root)
    params = root / "decisions" / "params.json"
    group = root / "derived" / "themes.group"
    before = (params.read_bytes(), (group / "run.json").read_bytes())
    client = _client(root, tmp_path / "app")
    try:
        tree = json.loads((group / "themes_draft.json").read_text(encoding="utf-8"))
        answer = _preview(client, {"settings": {"themes.group": {"depth": depth}}, "tree": tree})
    finally:
        client.app.state.cartolex.shutdown()
    preview = answer["preview"]
    assert preview["settings"]["themes.group"]["depth"] == depth and not preview["space_refit"]
    assert preview["theta"] is not None and preview["tree"]["based_on"]["run"] == "preview"
    against = answer["against"]
    if depth == 2:
        assert against["moved"] == against["split"] == against["merged"] == 0
    # nothing was saved
    assert (params.read_bytes(), (group / "run.json").read_bytes()) == before

    assert cli(["params", str(root), "--set", f"themes.group.depth={depth}"]) == 0
    assert cli(["build", str(root), "--only", "themes.group"]) == 0
    proposal = json.loads((group / "themes_draft.json").read_text(encoding="utf-8"))
    assert _without_run(preview["tree"]) == _without_run(proposal)


def test_the_curation_is_carried_where_a_node_continues(built, tmp_path):
    from cartolex.lexicon.theme_tree import TOO_BROAD
    from cartolex.project.models import ThemesFile
    from cartolex.project.themes import rename_node, set_aside, set_attribution
    from cartolex.project.themes_carry import GROUPING_REASONS, against, carry_curation

    assert TOO_BROAD in GROUPING_REASONS

    draft = (built / "derived" / "themes.group" / "themes_draft.json").read_text(encoding="utf-8")
    tree = ThemesFile.model_validate(json.loads(draft))
    top = next(n for n in tree.nodes if n.parent is None)
    leaf = next(n for n in tree.nodes if n.parent is not None)
    on_leaf = sorted(k for k, n in tree.keywords.items() if n == leaf.id)
    curated = rename_node(tree, top.id, {"en": "Curated name"}).tree
    curated = set_aside(curated, on_leaf[0], "not a theme").tree
    curated = set_attribution(curated, on_leaf[1:2], 0).tree

    # onto the same grouping, everything comes back
    same = carry_curation(curated, tree)
    assert same.tree.model_dump() == curated.model_dump() | {"saved": None}
    assert against(curated, tree)["moved"] == 0

    # onto a deeper grouping: the names follow the node that continues, set-asides stay
    client = _client(built, tmp_path / "app")
    try:
        deeper = _preview(client, {"settings": {"themes.group": {"depth": 3}}})["preview"]["tree"]
        answer = client.post(
            "/api/themes/carry",
            json={"tree": curated.model_dump(mode="json", by_alias=True), "proposal": deeper},
        ).json()
    finally:
        client.app.state.cartolex.shutdown()
    carried = ThemesFile.model_validate(answer["tree"])
    assert carried.depth == 3 and on_leaf[0] in carried.set_aside
    assert carried.set_aside[on_leaf[0]].reason == "not a theme"
    assert any(n.names.get("en") == "Curated name" for n in carried.nodes)
    assert answer["carried"]["names"] >= 1 and answer["carried"]["set_aside"] == 1
    assert carried.attribution.get(on_leaf[1]) == 0
