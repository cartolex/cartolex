# SPDX-License-Identifier: MIT
"""The overview's API: the one next step, the health panel, the preview."""

from __future__ import annotations

from _app_helpers import Client, fake_app, fake_project

from cartolex.app.routes.overview import next_step


def test_the_overview_leads_from_a_first_build_to_the_themes(tmp_path):
    app, _ = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log")
    try:
        client = Client(app)
        first = client.get("/api/overview").json()
        assert first["project"] == {
            "id": first["project"]["id"],
            "name": "Build test",
            "state": "never_built",
        }
        assert first["next"]["code"] == "next_first_build"
        assert first["next"]["next"]["action"] == "build"
        assert first["preview"] is None and first["shares"]["items"] == []
        job = client.post("/api/build", json={"dry_run": False}).json()["job"]["id"]
        assert client.wait_job(job)["state"] == "succeeded"
        after = client.get("/api/overview").json()
        assert after["next"]["code"] == "next_curate_themes"
        assert all(h["code"] != "health_too_large" for h in after["health"])
    finally:
        app.state.cartolex.shutdown()


def test_a_stage_too_large_for_the_machine_is_a_health_warning(tmp_path):
    app, _ = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log", build_budget_mb=1.0)
    try:
        body = Client(app).get("/api/overview").json()
        large = [h for h in body["health"] if h["code"] == "health_too_large"]
        assert large and large[0]["level"] == "warning"
        assert set(large[0]["params"]) == {"stage", "need_mb", "budget_mb"}
        assert large[0]["next"]["action"] == "settings"
    finally:
        app.state.cartolex.shutdown()


def _stage(sid, state, has_results=True):
    return {"id": sid, "state": state, "has_results": has_results}


def test_the_next_step_restores_a_stale_map_before_other_updates(tmp_path):
    class Ctx:
        class layout:  # noqa: N801
            themes_json = tmp_path / "themes.json"
            people_csv = tmp_path / "people.csv"

    stages = [
        _stage("corpus.assemble", "up_to_date"),
        _stage("themes.apply", "needs_update"),
        _stage("map.layout", "needs_update"),
    ]
    stale = {"code": "health_map_stale"}
    step = next_step(Ctx, stages, [stale], None)
    assert step["code"] == "next_restore_map" and step["scope"] == ["map"]
    failed = [_stage("keywords.build", "failed"), *stages]
    assert next_step(Ctx, failed, [stale], None)["code"] == "next_see_failure"
    assert next_step(Ctx, failed, [], object())["code"] == "next_watch_build"
