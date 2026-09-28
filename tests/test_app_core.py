# SPDX-License-Identifier: MIT
"""The app's core: the manifest, extensions, per-request projects, jobs, logs, diagnostic."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import pytest
from _app_extension import make_extension
from _app_helpers import TOKEN, Client, etag, fake_app, fake_project

from cartolex.app import AppSettings, Branding, Extension, ExtensionError, NavEntry, create_app
from cartolex.app.jobs import host_digest
from cartolex.app.logs import JsonFormatter
from cartolex.app.manifest import Manifest

FIXTURE = Path(__file__).with_name("fixtures") / "manifest.example.json"


# ── the manifest ─────────────────────────────────────────────────────────────


def _keys(example, actual, where="") -> list[str]:
    """Keys of *example* missing from *actual* (dicts compared recursively)."""
    missing = []
    if isinstance(example, dict):
        if not isinstance(actual, dict):
            return [where or "."]
        for key, value in example.items():
            if key not in actual:
                missing.append(f"{where}.{key}")
            else:
                missing += _keys(value, actual[key], f"{where}.{key}")
    elif isinstance(example, list) and example and isinstance(example[0], dict):
        if actual:
            missing += _keys(example[0], actual[0], f"{where}[0]")
    return missing


def test_the_fixture_is_a_valid_manifest_and_the_app_serves_its_shape(tmp_path):
    example = json.loads(FIXTURE.read_text(encoding="utf-8"))
    Manifest.model_validate(example)
    app, _ = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log")
    try:
        manifest = Client(app).get("/api/app/manifest").json()
        Manifest.model_validate(manifest)
        assert _keys(example, manifest) == []
        assert manifest["format"] == "cartolex-manifest/1"
        assert manifest["project"]["open"] is True and manifest["project"]["name"] == "Build test"
        assert manifest["security"]["csrf_header"] == "X-Cartolex-CSRF"
        assert manifest["security"]["csrf_cookie"] == app.state.cartolex.csrf_cookie
        assert [n["id"] for n in manifest["nav"]][:2] == ["overview", "people"]
        assert manifest["modules"] == []
        assert manifest["capabilities"] == {
            "collection": False,
            "ai_api": False,
            "ai_handoff": True,
            "hosted": False,
        }
    finally:
        app.state.cartolex.shutdown()


def test_the_stored_schema_is_the_models(tmp_path):
    from cartolex.app.schemas import main

    assert main(["--check"]) == 0
    app, _ = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log")
    try:
        client = Client(app)
        assert client.get("/api/app/manifest/schema").json()["title"] == "Manifest"
        paths = client.get("/api/openapi.json").json()["paths"]
        for path in ("/api/keywords", "/api/themes/ops", "/api/build", "/api/atlas", "/api/params"):
            assert path in paths, path
        assert not any(p.startswith(("/static", "/launch")) for p in paths)
    finally:
        app.state.cartolex.shutdown()


# ── extensions ───────────────────────────────────────────────────────────────


def test_the_app_runs_with_the_test_extension(tmp_path):
    from _build_fakes import _write_from

    opened: list[str] = []
    runner = _write_from("overlays.position", "host.txt", ("themes.group", "groups.txt"))
    app, controls = fake_app(
        fake_project(tmp_path / "p"),
        tmp_path / "c.log",
        extensions=[
            make_extension(tmp_path / "ext", opened=opened, stage_runner=None),
        ],
    )
    try:
        client = Client(app)
        m = client.get("/api/app/manifest").json()
        reports = next(n for n in m["nav"] if n["id"] == "reports")
        assert reports == {
            "id": "reports",
            "label": "nav.reports",
            "route": "/reports",
            "module": "/static/ext/reports/pages/reports.js",
            "order": 70,
            "placement": "main",
        }
        assert [n["id"] for n in m["nav"]].index("reports") == 6  # after map (50) and share (60)
        assert m["modules"] == ["/static/ext/reports/index.js"]
        assert m["locales"]["catalogues"]["fr"] == [
            "/static/i18n/fr.json",
            "/static/ext/reports/i18n/fr.json",
        ]
        assert m["locales"]["catalogues"]["pt-BR"] == ["/static/i18n/pt-BR.json"]
        assert m["branding"] == {
            "name": "Example host",
            "logo": "/static/ext/reports/logo.svg",
            "accent": {"light": "#2b47a8", "dark": None},
        }
        assert m["app"]["name"] == "Example host" and m["capabilities"]["reports"] is True
        assert client.get("/static/ext/reports/i18n/fr.json").json() == {"nav.reports": "Rapports"}
        assert client.get("/api/ext/reports/hello").json() == {"hello": "Build test"}
        assert Client(app, sign_in=False).get("/api/ext/reports/hello").status_code == 401
        assert opened == ["Build test"]  # on_project_open
        state = client.get("/api/project/state").json()
        area = next(a for a in state["areas"] if a["id"] == "reports")
        assert area["state"] == "never_built" and area["items"][0]["id"] == "report"
        params = client.get("/api/params").json()
        group = next(s for s in params["stages"] if s["id"] == "themes.group")
        top = next(p for p in group["params"] if p["name"] == "top_groups")
        assert (top["value"], top["from"], top["default"]) == (7, "default", 7)
    finally:
        app.state.cartolex.shutdown()
    # the extension's own stage declaration replaces cartolex's
    app, controls = fake_app(
        fake_project(tmp_path / "q"),
        tmp_path / "c2.log",
        extensions=[make_extension(tmp_path / "ext2", stage_runner=runner(Controls_for(tmp_path)))],
    )
    try:
        client = Client(app)
        stages = client.get("/api/project/state").json()["stages"]
        placed = next(s for s in stages if s["id"] == "overlays.position")
        assert placed["name"] == "place projected people (the host's way)"
        job = client.post("/api/build", json={"dry_run": False}).json()["job"]["id"]
        assert "overlays.position" in client.wait_job(job)["result"]["ran"]
        assert (tmp_path / "q" / "derived" / "overlays.position" / "host.txt").exists()
    finally:
        app.state.cartolex.shutdown()


def Controls_for(tmp_path):  # noqa: N802 - a factory named like the class it builds
    from _build_fakes import Controls

    return Controls(log=tmp_path / "host.log")


def test_a_created_project_gets_the_extensions_slots_overlays_and_identity(tmp_path):
    app = create_app(
        AppSettings(launch_token=TOKEN, data_dir=tmp_path / "data"),
        [make_extension(tmp_path / "ext")],
    )
    try:
        client = Client(app)
        assert client.get("/api/project/state").json()["error"]["code"] == "no_project"
        r = client.post(
            "/api/projects",
            json={
                "folder": str(tmp_path / "new"),
                "name": "New map",
                "domain_title": "Ocean physics",
                "languages": ["en", "fr"],
            },
        )
        assert r.status_code == 201, r.text
        config = json.loads((tmp_path / "new" / "project.json").read_text())
        assert [s["id"] for s in config["slots"]] == ["reports"]
        assert [o["id"] for o in config["overlays"]] == ["applicants"]
        assert config["identity"]["domain_description"] == "described by the host for New map"
        assert client.get("/api/app/manifest").json()["project"]["name"] == "New map"
        recent = client.get("/api/projects/recent").json()["items"]
        assert recent[0]["name"] == "New map" and recent[0]["exists"]
        assert client.post("/api/projects/close").json() == {"open": False}
        assert not (tmp_path / "new" / ".lock").exists()
        r = client.post("/api/projects/open", json={"path": str(tmp_path / "new")})
        assert r.status_code == 200 and (tmp_path / "new" / ".lock").exists()
        again = client.post(
            "/api/projects",
            json={"folder": str(tmp_path / "new"), "name": "x", "domain_title": "y"},
        )
        assert again.status_code == 409 and again.json()["error"]["code"] == "project_exists"
        missing = client.post("/api/projects/open", json={"path": str(tmp_path / "nowhere")})
        assert missing.status_code == 404
    finally:
        app.state.cartolex.shutdown()
    assert not (tmp_path / "new" / ".lock").exists()  # stopping the app closes the project


@pytest.mark.parametrize(
    ("extensions", "message"),
    [
        ([Extension("a"), Extension("a")], "two extensions"),
        ([Extension("Bad id")], "extension id"),
        ([Extension("a", branding=Branding(accent="#ffff00"))], "contrast"),
        ([Extension("a", branding=Branding(accent_dark="#000080"))], "contrast"),
        (
            [Extension("a", nav=(NavEntry("keywords", "x", "/k", "/static/x.js"),))],
            "already used",
        ),
        ([Extension("a", nav=(NavEntry("x", "x", "/api/x", "/static/x.js"),))], "page path"),
        ([Extension("a", capabilities={"hosted": True})], "cartolex's"),
        ([Extension("a", stage_patches={"themes.group": {"defaults": {"nope": 1}}})], "nope"),
        ([Extension("a", stage_patches={"nowhere": {}})], "no stage"),
        ([Extension("a", modules=("../x.js",))], "static_dir"),
        (
            [Extension("a", branding=Branding("x")), Extension("b", branding=Branding("y"))],
            "only one",
        ),
    ],
)
def test_extensions_that_do_not_fit_are_refused(extensions, message):
    with pytest.raises(ExtensionError, match=message):
        create_app(AppSettings(), extensions)


def test_a_host_stage_of_its_own_waits_for_the_format():
    from cartolex.app.extensions import patched_registry
    from cartolex.build import STAGES, Stage

    class Fake:
        id = "reports"
        stages = (Stage("map.layout", "draw the map, the host's way"),)
        stage_patches: dict = {}

    registry = patched_registry(STAGES, [Fake])  # type: ignore[list-item]
    assert registry["map.layout"].name == "draw the map, the host's way"
    with pytest.raises(ValueError, match="unknown stage id"):
        Stage("reports.build", "a host stage")


# ── per-request projects: two apps, two projects ─────────────────────────────


def test_two_apps_in_one_process_share_nothing(tmp_path):
    a, _ = fake_app(fake_project(tmp_path / "a"), tmp_path / "a.log")
    b, _ = fake_app(fake_project(tmp_path / "b", n_people=2), tmp_path / "b.log")
    try:
        ca, cb = Client(a), Client(b)
        ida = ca.get("/api/app/manifest").json()["project"]["id"]
        idb = cb.get("/api/app/manifest").json()["project"]["id"]
        assert ida != idb
        assert a.state.cartolex.session_cookie != b.state.cartolex.session_cookie
        va = etag(ca.get("/api/params"))
        r = ca.put("/api/params", json={"seed": 11, "stages": {}}, headers={"If-Match": va})
        assert r.status_code == 200
        assert ca.get("/api/params").json()["global"]["seed"]["value"] == 11
        assert cb.get("/api/params").json()["global"]["seed"]["value"] == 7
        # a session of one app is nothing to the other
        stranger = Client(b, sign_in=False)
        stranger.http.cookies.update(ca.http.cookies)
        assert stranger.get("/api/params").status_code == 401
        # a second app cannot open a project the first holds
        with pytest.raises(RuntimeError, match="open in"):
            create_app(AppSettings(project=tmp_path / "a"))
    finally:
        a.state.cartolex.shutdown()
        b.state.cartolex.shutdown()


def test_hosted_projects_are_chosen_by_the_route(tmp_path):
    from cartolex.app.auth import Principal

    root = tmp_path / "projects"
    fake_project(root / "alpha")
    fake_project(root / "beta", n_people=2)
    app = create_app(
        AppSettings(
            mode="hosted",
            projects_root=root,
            allowed_hosts=("127.0.0.1",),
            launch_token=TOKEN,
            authenticate=lambda request: (
                Principal("someone", projects=frozenset({"alpha", "gamma"}))
                if request.headers.get("x-test-user")
                else None
            ),
        )
    )
    try:
        client = Client(app, sign_in=False)
        client.http.headers["x-test-user"] = "1"
        assert client.get("/api/projects").json()["items"] == [
            {"id": "alpha", "name": "Build test"}
        ]
        assert client.get("/api/projects/alpha/project/state").status_code == 200
        denied = client.get("/api/projects/beta/project/state")
        assert (
            denied.status_code == 403
            and "not one you may open" in denied.json()["error"]["message"]
        )
        unnamed = client.get("/api/project/state").json()["error"]
        assert unnamed["code"] == "project_not_named" and unnamed["params"] == {}
        gamma = client.get("/api/projects/gamma/project/state")
        assert gamma.status_code == 404 and gamma.json()["error"]["params"] == {"id": "gamma"}
    finally:
        app.state.cartolex.shutdown()


# ── jobs ─────────────────────────────────────────────────────────────────────


def test_a_build_job_runs_is_tracked_and_logged(tmp_path):
    app, controls = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log")
    try:
        client = Client(app)
        dry = client.post("/api/build", json={"dry_run": True}).json()
        assert dry["to_run"] == [
            "corpus.assemble",
            "keywords.extract",
            "keywords.build",
            "themes.group",
        ]
        assert dry["consent"] == []
        r = client.post("/api/build", json={"dry_run": False})
        assert r.status_code == 202
        job = client.wait_job(r.json()["job"]["id"])
        assert job["state"] == "succeeded" and job["result"]["ran"] == dry["to_run"]
        tracker = client.get("/api/build").json()
        assert [s["state"] for s in tracker["stages"]] == ["done"] * 4
        assert job["progress"]["fraction"] == 1.0
        log = (tmp_path / "p" / "logs" / "jobs" / f"{job['id']}.jsonl").read_text()
        events = [json.loads(x)["event"] for x in log.splitlines()]
        assert events[0] == "job" and events[1] == "start" and events[-1] == "job-end"
        assert "Survey" not in log and "p000" not in log
        states = client.get("/api/project/state").json()
        assert next(a for a in states["areas"] if a["id"] == "keywords")["state"] == "up_to_date"
        triage = next(s for s in states["stages"] if s["id"] == "keywords.triage")
        assert triage["state"] == "skipped" and triage["skip_reason"].startswith("switched off")
        assert (triage["skip"]["code"], triage["skip"]["params"]) == (
            "stage_switched_off",
            {"stage": "keywords.triage"},
        )
        again = client.post("/api/build", json={"dry_run": True}).json()
        assert again["to_run"] == [] and again["empty"]["message"] == "everything is up to date"
    finally:
        app.state.cartolex.shutdown()


def test_a_stage_that_asks_consent_runs_only_with_it(tmp_path):
    app, controls = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log")
    try:
        client = Client(app)
        params = client.get("/api/params")
        body = {"seed": 7, "stages": {"keywords.triage": {"enabled": True}}}
        assert (
            client.put("/api/params", json=body, headers={"If-Match": etag(params)}).status_code
            == 200
        )
        dry = client.post("/api/build", json={"dry_run": True, "scope": ["keywords"]}).json()
        assert [c["stage"] for c in dry["consent"]] == ["keywords.triage"]
        assert dry["consent"][0]["paid"] and dry["consent"][0]["note"]
        job = client.post("/api/build", json={"dry_run": False, "scope": ["keywords"]}).json()[
            "job"
        ]
        refused = client.wait_job(job["id"])
        assert refused["result"]["refused"] == {
            "keywords.triage": "no consent",
            "keywords.build": "depends on keywords.triage, which does not run",
        }
        job = client.post(
            "/api/build",
            json={"dry_run": False, "scope": ["keywords"], "consent": ["keywords.triage"]},
        ).json()["job"]
        ran = client.wait_job(job["id"])
        assert ran["result"]["ran"] == ["keywords.triage", "keywords.build"]
    finally:
        app.state.cartolex.shutdown()


def test_a_second_build_is_refused_with_409_naming_the_running_one(tmp_path):
    app, controls = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log")
    started, release = threading.Event(), threading.Event()
    controls.hold["keywords.extract"] = (started, release)
    try:
        client = Client(app)
        first = client.post("/api/build", json={"dry_run": False}).json()["job"]["id"]
        assert started.wait(10)
        second = client.post("/api/build", json={"dry_run": False})
        assert second.status_code == 409
        error = second.json()["error"]
        assert error["code"] == "busy" and error["job"] == first and first in error["message"]
        assert error["params"] == {"kind": "build", "job": first}
        assert client.post("/api/themes/apply").status_code == 409
        state = client.get("/api/project/state").json()
        assert state["job"]["id"] == first
        assert (
            next(s for s in state["stages"] if s["id"] == "keywords.extract")["state"] == "running"
        )
        release.set()
        assert client.wait_job(first)["state"] == "succeeded"
    finally:
        release.set()
        app.state.cartolex.shutdown()


def test_a_cancelled_build_changes_nothing_or_finishes_before_the_cancel(tmp_path):
    app, controls = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log")
    started, release = threading.Event(), threading.Event()
    controls.hold["keywords.extract"] = (started, release)
    try:
        client = Client(app)
        job_id = client.post("/api/build", json={"dry_run": False}).json()["job"]["id"]
        assert started.wait(10)
        cancelling = client.post(f"/api/jobs/{job_id}/cancel")
        assert cancelling.status_code == 200 and cancelling.json()["state"] == "cancelling"
        release.set()
        job = client.wait_job(job_id)
        assert job["state"] == "cancelled"
        assert job["result"]["summary"].startswith("cancelled: finished before the cancel")
        assert job["result"]["ran"] == ["corpus.assemble"]
        stages = client.get("/api/project/state").json()["stages"]
        extract = next(s for s in stages if s["id"] == "keywords.extract")
        assert extract["state"] == "failed" and extract["attempt"]["code"] == "stage_cancelled"
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409
        # a cancel before anything ran: nothing changed
        controls.hold.clear()
        started2, release2 = threading.Event(), threading.Event()
        controls.hold["keywords.extract"] = (started2, release2)
        job2 = client.post("/api/build", json={"dry_run": False}).json()["job"]["id"]
        assert started2.wait(10)
        client.post(f"/api/jobs/{job2}/cancel")
        release2.set()
        done = client.wait_job(job2)
        assert done["state"] == "cancelled" and done["result"]["summary"].startswith(
            "cancelled: nothing changed"
        )
    finally:
        release.set()
        app.state.cartolex.shutdown()


def test_a_job_whose_process_is_gone_is_interrupted_never_running(tmp_path):
    root = fake_project(tmp_path / "p")
    jobs = root / "logs" / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "20260101T000000Z-aaaaaa.jsonl").write_text(
        json.dumps(
            {
                "at": "2026-01-01T00:00:00Z",
                "event": "job",
                "kind": "build",
                "pid": 2**22 + 7,
                "host": host_digest(),
                "boot": None,
            }
        )
        + "\n"
        + json.dumps({"at": "2026-01-01T00:00:01Z", "event": "start", "run": ["corpus.assemble"]})
        + "\n",
        encoding="utf-8",
    )
    app, _ = fake_app(root, tmp_path / "c.log")
    try:
        client = Client(app)
        job = client.get("/api/jobs").json()["jobs"][0]
        assert job["id"] == "20260101T000000Z-aaaaaa" and job["state"] == "interrupted"
        assert client.get("/api/build").json()["job"]["state"] == "interrupted"
        assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409
        events = client.get(f"/api/jobs/{job['id']}/events?after=1").json()
        assert [e["event"] for e in events["events"]] == ["start"] and events["next"] == 2
    finally:
        app.state.cartolex.shutdown()


# ── logs and the diagnostic ──────────────────────────────────────────────────


def test_request_logs_are_json_lines_with_the_route_and_no_personal_data(tmp_path, caplog):
    app, _ = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log")
    try:
        client = Client(app)
        with caplog.at_level(logging.INFO, logger="cartolex.app.access"):
            r = client.get(
                "/api/jobs/20260101T000000Z-bbbbbb?secret=Survey",
                headers={"X-Request-ID": "req-12345678"},
            )
        assert r.headers["x-request-id"] == "req-12345678"
        records = [rec for rec in caplog.records if rec.name == "cartolex.app.access"]
        line = json.loads(JsonFormatter().format(records[-1]))
        assert line["route"] == "/api/jobs/{job_id}" and line["request_id"] == "req-12345678"
        assert line["status"] == 404 and line["method"] == "GET" and line["event"] == "request"
        assert "20260101T000000Z-bbbbbb" not in json.dumps(line) and "Survey" not in json.dumps(
            line
        )
        assert set(line) <= {
            "at",
            "level",
            "logger",
            "message",
            "event",
            "request_id",
            "method",
            "route",
            "status",
            "ms",
        }
    finally:
        app.state.cartolex.shutdown()


def test_the_diagnostic_holds_versions_and_sizes_never_project_data(tmp_path):
    app, _ = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log")
    try:
        client = Client(app)
        job = client.post("/api/build", json={"dry_run": False}).json()["job"]["id"]
        client.wait_job(job)
        d = client.get("/api/diagnostic").json()
        assert d["python"] and d["cartolex"] and d["dependencies"]["fastapi"]
        assert {m["language"] for m in d["language_models"]} >= {"en", "fr", "pt"}
        assert d["machine"]["cpus"] >= 1
        assert d["recent_jobs"][0]["id"] == job
        text = json.dumps(d)
        for secret in ("Build test", str(tmp_path), "Coastal and marine", "Survey"):
            assert secret not in text, secret
    finally:
        app.state.cartolex.shutdown()
