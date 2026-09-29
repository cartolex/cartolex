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
    assert cli(["params", str(root), "--set", "pinned_year=2026", "themes.group.depth=2"]) == 0
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
    assert {f["name"] for f in keywords["fixed"]} >= {"vote", "bands.even_spread"}

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
