# SPDX-License-Identifier: MIT
"""The AI steps chosen on the build page: a copilot pauses the build, which then continues."""

from __future__ import annotations

from _app_helpers import Client, etag, fake_app, fake_project

from cartolex.project.tables import write_decision_csv


def _route(client: Client, **routes: str) -> dict:
    version = client.post("/api/build", json={"dry_run": True}).json()["ai"]["version"]
    body = {k.replace("_", "."): v for k, v in routes.items()}
    r = client.put("/api/build/ai", json=body, headers={"If-Match": f'"{version}"'})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_copilot_pauses_the_build_until_it_continues(tmp_path):
    root = fake_project(tmp_path / "p")
    app, _ = fake_app(root, tmp_path / "c.log")
    try:
        client = Client(app)
        assert client.put("/api/build/ai", json={}).status_code == 428
        view = _route(client, keywords_triage="copilot")
        assert view["routes"] == {"keywords.triage": "copilot", "themes.curation": "none"}
        assert view["choices"]["themes.curation"] == ["none", "copilot"]

        dry = client.post("/api/build", json={"dry_run": True}).json()
        assert dry["pause"]["step"] == "keywords.triage"
        assert dry["pause"]["held"] == ["keywords.build", "themes.group"]
        job = client.post("/api/build", json={"dry_run": False}).json()["job"]
        done = client.wait_job(job["id"])
        assert done["state"] == "waiting"
        assert done["result"]["ran"] == ["corpus.assemble", "keywords.extract"]
        assert done["result"]["waiting"]["page"] == "/keywords?copilot=1"
        assert client.get("/api/overview").json()["next"]["code"] == "next_copilot_waiting"

        # Read again from the job's log, as after a restart.
        app.state.cartolex.shutdown()
        app, _ = fake_app(root, tmp_path / "c.log")
        client = Client(app)
        last = client.get("/api/build").json()["job"]
        assert last["state"] == "waiting" and last["result"]["waiting"]["step"] == "keywords.triage"

        # Nothing accepted yet: the build still pauses, at once, unless it is continued.
        assert client.post("/api/build", json={"dry_run": True}).json()["pause"] is not None
        write_decision_csv(
            root / "decisions" / "keywords.csv",
            "keywords",
            [
                {
                    "term": "survey",
                    "language": "en",
                    "decision": "keep",
                    "target": "",
                    "reason": "AI: a method",
                    "source": "ai-copilot",
                    "decided_at": "2999-01-01T00:00:00Z",
                }
            ],
        )
        # A copilot's decision accepted since the extraction: the build goes on by itself.
        assert client.post("/api/build", json={"dry_run": True}).json()["pause"] is None
        job = client.post("/api/build", json={"dry_run": False, "continue": ["keywords.triage"]})
        ran = client.wait_job(job.json()["job"]["id"])
        assert ran["state"] == "succeeded"
        assert ran["result"]["ran"] == ["keywords.build", "themes.group"]
    finally:
        app.state.cartolex.shutdown()


def test_the_api_route_switches_the_clean_up_on_and_asks_consent(tmp_path):
    app, _ = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log")
    try:
        client = Client(app)
        assert _route(client, keywords_triage="api")["routes"]["keywords.triage"] == "api"
        params = client.get("/api/params")
        triage = next(s for s in params.json()["stages"] if s["id"] == "keywords.triage")
        assert next(p for p in triage["params"] if p["name"] == "enabled")["value"] is True
        dry = client.post("/api/build", json={"dry_run": True}).json()
        assert [c["stage"] for c in dry["consent"]] == ["keywords.triage"]
        assert dry["pause"] is None
        assert _route(client, keywords_triage="none")["routes"]["keywords.triage"] == "none"
        bad = client.put(
            "/api/build/ai", json={"themes.curation": "api"}, headers={"If-Match": etag(params)}
        )
        assert bad.status_code == 422
    finally:
        app.state.cartolex.shutdown()
