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


def test_the_keywords_thresholds_preview_on_the_stored_candidates(client, built):
    params = built / "decisions" / "params.json"
    before = params.read_bytes()
    same = client.get("/api/method/keywords/preview").json()
    assert same["built"] == {
        "min_people": 3,
        "min_texts": 3,
        "max_share": 0.6,
        "max_keywords": 10_000,
    }
    assert same["candidates"]["after"] == same["candidates"]["before"] and not same["needs"]
    # the vocabulary is counted as the step's header counts it: the kept keywords
    header = client.get("/api/method/keywords").json()["vocabulary"]["kept_keywords"]
    assert same["vocabulary"]["before"] == same["vocabulary"]["after"] == header

    stricter = client.get("/api/method/keywords/preview", params={"min_people": 5}).json()
    gone = stricter["candidates"]["leaving"]
    assert gone > 0 and stricter["candidates"]["after"] == stricter["candidates"]["before"] - gone
    assert sum(b["after"] for b in stricter["bands"].values()) == stricter["candidates"]["after"]
    named = stricter["leaving"]
    assert named and all(r["people"] < 5 and r["cause"] == "min_people" for r in named)
    assert [r["score_len"] for r in named] == sorted((r["score_len"] for r in named), reverse=True)
    v = stricter["vocabulary"]
    assert v["leaving"] > 0 and v["after_low"] == header - v["leaving"] <= v["after_high"]

    # a looser window needs a new extraction: named, and the last build's value kept
    looser = client.get("/api/method/keywords/preview", params={"min_people": 2}).json()
    assert [n["code"] for n in looser["needs"]] == ["preview_needs_extraction"]
    assert looser["needs"][0]["params"] == {"param": "min_people", "value": 2, "built": 3}
    assert looser["used"]["min_people"] == 3 and looser["candidates"]["leaving"] == 0

    # the vocabulary's cap, both ways, on the full scored list
    cap = client.get("/api/method/keywords/preview", params={"max_keywords": 50}).json()
    assert cap["vocabulary"]["after_high"] <= 50 and len(cap["vocabulary_leaving"]) == 8
    assert params.read_bytes() == before  # nothing saved


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


def _file_stages(view: dict) -> dict:
    """What params.json sets now, from a read of ``/api/params``."""
    out: dict = {}
    for s in view["stages"]:
        for p in s["params"]:
            if p["set_in_file"] and p["name"] not in ("seed", "year"):
                out.setdefault(s["id"], {})[p["name"]] = p["value"]
    return out


def test_the_recipe_marks_a_changed_value_the_state_counts_it_and_it_exports(client):
    read = client.get("/api/params")
    before = read.json()
    body = {"seed": 0, "pinned_year": before["global"]["pinned_year"]["value"],
            "stages": _file_stages(before)}  # fmt: skip
    changed = {**body, "stages": {**body["stages"], "keywords.extract": {"min_people": 4}}}
    saved = client.put("/api/params", json=changed, headers={"If-Match": etag(read)})
    assert saved.status_code == 200, saved.text
    try:
        recipe = client.get("/api/recipe").json()
        row = next(
            r
            for r in recipe["rows"]
            if (r["group"], r["name"]) == ("keywords.extract", "min_people")
        )
        assert row["value"] == 4 and row["default_value"] == 3 and row["differs"]
        assert row["from"] == "params.json" and row["panel"] == "keywords"
        dims = next(r for r in recipe["rows"] if r["name"] == "dimensions")
        assert dims["from"] == "rule" and dims["rule_description"] and not dims["differs"]
        assert any(r["group"] == "layout" and r["name"] == "method" for r in recipe["rows"])

        state = client.get("/api/project/state").json()
        assert state["changed_params"]["keywords.extract"] == 1
        states = {s["id"]: s["state"] for s in state["stages"]}
        assert states["keywords.extract"] == states["themes.space"] == "needs_update"

        md = client.get("/api/recipe/export", params={"format": "md"})
        assert md.headers["content-type"].startswith("text/markdown")
        assert "| Fewest people * | `min_people` | 4 | 3 |" in md.text
        csv = client.get("/api/recipe/export", params={"format": "csv", "language": "fr"})
        assert "attachment" in csv.headers["content-disposition"]
        line = next(
            x for x in csv.text.splitlines() if x.startswith("keywords.extract,min_people,")
        )
        assert line.split(",")[2:5] == ["Nombre minimal de personnes", "4", "3"]
    finally:
        again = client.get("/api/params")
        client.put("/api/params", json=body, headers={"If-Match": etag(again)})


def test_every_parameter_has_a_short_label_in_every_interface_language():
    import json

    from cartolex.app.method import LAYOUT_DEFAULTS
    from cartolex.app.static_files import PACKAGE_STATIC
    from cartolex.build.stages import STAGES

    keys = {f"param.label.{s.id}.{p.name}" for s in STAGES for p in s.params}
    keys |= {f"param.label.layout.{p['name']}" for specs in LAYOUT_DEFAULTS.values() for p in specs}
    keys |= {"param.label.layout.method", "param.label.layout.seed", "param.label.build.seed",
             "param.label.build.pinned_year"}  # fmt: skip
    for lang in ("en", "fr", "pt-BR"):
        words = json.loads((PACKAGE_STATIC / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        assert sorted(keys - set(words)) == [], lang
