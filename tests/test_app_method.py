# SPDX-License-Identifier: MIT
"""The method screen's API: each step's diagnostic from the outputs, the layout preview as a
cached job, a map version tried with layout parameters, and the parameters' defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
from _app_helpers import TOKEN, Client, etag

from cartolex.app import AppSettings, create_app
from cartolex.cli import main as cli
from cartolex.demo import generate
from cartolex.demo.project import write_project


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """The S demo world as a project at depth 2, built."""
    root = tmp_path_factory.mktemp("method-s") / "project"
    write_project(generate("S", 0), root).close()
    assert (
        cli(
            [
                "params",
                str(root),
                "--set",
                "pinned_year=2026",
                "themes.group.depth=2",
                "themes.group.keywords_per_group=20",
            ]
        )
        == 0
    )
    assert cli(["build", str(root)]) == 0
    return root


@pytest.fixture()
def client(built, tmp_path):
    app = create_app(
        AppSettings(
            project=built,
            launch_token=TOKEN,
            data_dir=tmp_path / "app",
            build_budget_mb=1e9,
            build_year=2026,
        )
    )
    yield Client(app)
    app.state.cartolex.shutdown()


def test_each_step_reads_what_its_stages_produced(client, built):
    import json

    keywords = client.get("/api/method/keywords").json()
    assert keywords["candidates"] == sum(keywords["bands"].values())
    assert sum(sum(c) for c in keywords["histogram"]["counts"].values()) <= keywords["candidates"]
    assert keywords["vocabulary"]["max_keywords"] == 10_000
    assert "fixed" not in keywords  # the scoring's settings are parameters now

    space = client.get("/api/method/space").json()
    assert len(space["explained"]) == space["dimensions"] == 20
    curve = space["neighbours"]["curve"]
    assert curve[-1]["dimensions"] == 20 and all(0 <= c["overlap"] <= 1 for c in curve)

    grouping = client.get("/api/method/grouping").json()
    run = json.loads((built / "derived" / "themes.group" / "run.json").read_text())
    counts = run["measures"]["counts"]
    assert [lv["groups"] for lv in grouping["levels"]] == [
        counts["groups_level_1"],
        counts["groups_level_2"],
    ]
    # the calibration read again gives the comb's own result at the θ it kept
    cal = grouping["calibration"]
    kept = next(p for p in cal["points"] if p["theta"] == cal["theta"])
    assert kept["too_broad"] == grouping["too_broad"] == counts["too_broad"]
    assert len(grouping["dendrogram"]["leaves"]) == counts["groups_level_1"]

    layout = client.get("/api/method/layout").json()
    assert layout["pinned"]["method"] == "umap" and 0 <= layout["overlap"] <= 1


def test_a_layout_preview_is_a_job_then_a_cached_answer(client):
    body = {"method": "umap", "seed": 1, "params": {"n_neighbors": 10}}
    started = client.post("/api/method/layout/preview", json=body)
    assert started.status_code == 202
    assert client.wait_job(started.json()["job"]["id"])["state"] == "succeeded"
    again = client.post("/api/method/layout/preview", json=body)
    assert again.status_code == 200
    preview = again.json()["preview"]
    assert len(preview["points"]) == preview["sample"] and preview["current"] is not None
    listed = client.get("/api/method/layout").json()["previews"]
    assert [(p["method"], p["params"]) for p in listed] == [("umap", {"n_neighbors": 10})]
    wrong = client.post("/api/method/layout/preview", json={"method": "umap", "params": {"x": 1}})
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "layout_param_unknown"


def test_a_map_version_takes_layout_parameters_and_params_say_their_default(client):
    versions = client.get("/api/map/versions")
    tried = client.post(
        "/api/map/versions",
        json={"action": "try", "method": "umap", "params": {"n_neighbors": 10}},
        headers={"If-Match": etag(versions)},
    )
    assert tried.status_code == 200
    assert tried.json()["versions"][0]["layout"]["params"] == {"n_neighbors": 10}
    refused = client.post(
        "/api/map/versions",
        json={"action": "try", "method": "umap", "params": {"perplexity": 5}},
        headers={"If-Match": etag(tried)},
    )
    assert refused.status_code == 422
    params = client.get("/api/params").json()
    group = next(s for s in params["stages"] if s["id"] == "themes.group")
    depth = next(p for p in group["params"] if p["name"] == "depth")
    assert depth["value"] == 2 and depth["default_value"] == 1 and depth["differs"] is True


def test_params_carry_their_tier_and_control_and_a_grid_round_trips(client):
    view = client.get("/api/params")
    params = {(s["id"], p["name"]): p for s in view.json()["stages"] for p in s["params"]}
    assert all(p["tier"] in ("essential", "intermediate", "advanced") for p in params.values())
    widgets = {k[1]: p["widget"] for k, p in params.items()}
    assert widgets["parts"] == "grid" and widgets["provider_priority"] == "order"
    assert widgets["level_sizes"] == "levels" and widgets["comb"] == "switch"
    assert params[("corpus.assemble", "parts")]["keys"] == ["collection", "folder", "corpus"]
    parts = {"collection": ["title"], "folder": ["title", "full"], "corpus": ["full"]}
    body = {
        "seed": 0,
        "pinned_year": 2026,
        "stages": {
            "corpus.assemble": {"parts": parts, "provider_priority": ["hal", "openalex"]},
            "themes.group": {"depth": 2, "level_sizes": [4, 12]},
        },
    }
    saved = client.put("/api/params", json=body, headers={"If-Match": etag(view)})
    assert saved.status_code == 200, saved.text
    again = {(s["id"], p["name"]): p for s in saved.json()["stages"] for p in s["params"]}
    assert again[("corpus.assemble", "parts")]["value"] == parts
    assert again[("corpus.assemble", "provider_priority")]["value"] == ["hal", "openalex"]
    assert again[("themes.group", "level_sizes")]["value"] == [4, 12]


def test_tsne_is_listed_switched_off_with_its_reason_when_missing(client, monkeypatch):
    import cartolex.atlas.reducers as reducers

    monkeypatch.setattr(reducers, "opentsne_available", lambda: False)
    for path in ("/api/method/layout", "/api/map/versions"):
        view = client.get(path).json()
        assert view["methods"] == ["umap", "tsne", "tree"]
        reason = view["unavailable"]["tsne"]
        assert reason["code"] == "layout_method_unavailable"
        assert reason["params"]["package"] == "openTSNE" and "cartolex[tsne]" in reason["message"]
    refused = client.post("/api/method/layout/preview", json={"method": "tsne"})
    assert refused.status_code == 422
    assert refused.json()["error"]["params"]["command"] == 'pip install "cartolex[tsne]"'
