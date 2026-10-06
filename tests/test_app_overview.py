# SPDX-License-Identifier: MIT
"""The overview's API: the one next step, the health panel, the preview."""

from __future__ import annotations

from _app_helpers import Client, fake_app, fake_project

from cartolex.app.routes.overview import next_step
from cartolex.project.tables import write_decision_csv


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
        # Without collection in this app, nothing waits for a check or a harvest: build.
        assert first["next"]["code"] == "next_first_build"
        assert first["next"]["next"]["action"] == "build"
        assert first["preview"] is None and first["shares"]["items"] == []
        steps = {s["id"]: s["state"] for s in first["steps"]}
        assert steps["people"] == steps["identities"] == "done" and steps["build"] == "todo"
        job = client.post("/api/build", json={"dry_run": False}).json()["job"]["id"]
        assert client.wait_job(job)["state"] == "succeeded"
        after = client.get("/api/overview").json()
        # Once built: the keywords are reviewed first, then the themes.
        assert after["next"]["code"] == "next_review_keywords"
        assert {s["id"] for s in after["steps"] if s["state"] == "done"} == {
            "project", "people", "identities", "texts", "build"
        }  # fmt: skip
        write_decision_csv(
            tmp_path / "p" / "decisions" / "keywords.csv",
            "keywords",
            [{"term": "tide", "language": "en", "decision": "keep", "target": "", "reason": "",
              "source": "person", "decided_at": "2999-01-01T00:00:00Z"}],
        )  # fmt: skip
        # A decision made: the vocabulary is built again before the themes.
        assert client.get("/api/overview").json()["next"]["code"] == "next_update"
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


def _stage(sid, state, has_results=True, attempt=None):
    return {"id": sid, "state": state, "has_results": has_results, "attempt": attempt}


def test_the_next_step_restores_a_stale_map_before_other_updates():
    stages = [
        _stage("corpus.assemble", "up_to_date"),
        _stage("themes.apply", "needs_update"),
        _stage("map.layout", "needs_update"),
    ]
    stale = {"code": "health_map_stale"}
    people = {"people": 3, "mapped": 3, "identities": 0, "to_harvest": 0}
    step = next_step(stages, [stale], None, people=people)
    assert step["code"] == "next_restore_map" and step["scope"] == ["map"]
    failed = [_stage("keywords.build", "failed"), *stages]
    assert next_step(failed, [stale], None, people=people)["code"] == "next_see_failure"
    # A failure a later job came after is no longer the next step.
    later = next_step(failed, [stale], None, people=people, quiet={"keywords.build"})
    assert later["code"] == "next_restore_map"

    class Running:
        kind = "build"

    assert next_step(failed, [stale], Running())["code"] == "next_watch_build"
    Running.kind = "collection"
    other = next_step(failed, [stale], Running())
    assert other["code"] == "next_job_running" and other["next"]["action"] == "wait"


def test_texts_that_were_never_collected_lead_to_the_harvest():
    stages = [_stage("corpus.assemble", "failed", False, {"code": "stage_no_texts"})]
    people = {"people": 3, "mapped": 3, "identities": 0, "to_harvest": 3}
    step = next_step(stages, [], None, people=people)
    assert step["code"] == "next_collect_texts"
    assert step["next"]["action"] == "open:/people?collect=harvest"
    # before the texts are gathered: the identities waiting come first, then the harvest
    stages = [_stage("corpus.assemble", "never_built", False)]
    waiting = {**people, "identities": 2}
    step = next_step(stages, [], None, people=waiting)
    assert step["code"] == "next_check_identities" and step["params"] == {"n": 2}
    assert next_step(stages, [], None, people=people)["code"] == "next_collect_texts"
    nobody = {**people, "mapped": 0, "to_harvest": 0}
    stages = [_stage("corpus.assemble", "failed", False, {"code": "stage_no_mapped"})]
    assert next_step(stages, [], None, people=nobody)["code"] == "next_set_roles"


def test_the_demo_project_leads_to_its_first_build(tmp_path):
    """The demo writes its people whole: one has no work and no record, so there is nothing
    to collect for them, even with collection available (nothing would be asked anywhere)."""
    from _app_helpers import TOKEN

    from cartolex.app import AppSettings, create_app
    from cartolex.app.collection import DemoCollection
    from cartolex.demo import generate

    app = create_app(
        AppSettings(
            launch_token=TOKEN,
            data_dir=tmp_path / "data",
            collection=DemoCollection(generate("S", 0)),
        )  # fmt: skip
    )
    try:
        client = Client(app)
        made = client.post("/api/projects/demo", json={"folder": str(tmp_path / "demo")})
        assert made.status_code == 201, made.text
        overview = client.get("/api/overview").json()
        assert overview["people"]["without_texts"] == 1 and overview["people"]["to_harvest"] == 0
        assert overview["next"]["code"] == "next_first_build"
        assert {s["id"]: s["state"] for s in overview["steps"]}["texts"] == "done"
    finally:
        app.state.cartolex.shutdown()
