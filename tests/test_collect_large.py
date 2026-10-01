# SPDX-License-Identifier: MIT
"""A large institution: its works read page by page, paused on a stop or a failing page, resumed
to the proposal an uninterrupted reading gives; a failed job says why."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from _app_helpers import TOKEN, Client

from cartolex.app import AppSettings, create_app
from cartolex.app.collect_service import ServiceCollection
from cartolex.app.jobs import LocalJobRunner, read_job_logs
from cartolex.collect import HttpClient, RetryPolicy, local_settings
from cartolex.collect import institutions as inst
from cartolex.collect.http import ServiceUnavailable
from cartolex.collect.openalex import OpenAlexApi
from cartolex.collect.tables import read_runs
from cartolex.demo.services.http import DemoServer
from cartolex.demo.services.large import ROOT_ID, LargeInstitutionService
from cartolex.project import Project
from cartolex.project.checkpoints import JobPaused

WORKS = 1_250  # 13 pages


@pytest.fixture(scope="module")
def server():
    with DemoServer({"openalex": LargeInstitutionService(WORKS, seed=3)}) as srv:
        yield srv


def _settings(server):
    return local_settings(
        {"openalex": f"{server.base_url}/openalex"},
        retry=RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0),
    )


def _project(root: Path) -> Project:
    return Project.init(root, name="Large", domain_title="Invented field")


def _propose(project, server, *, cancel=None, **kw):
    client = HttpClient(_settings(server), cancel=cancel)
    return inst.propose_people(project, OpenAlexApi(client), [ROOT_ID], checkpoint_every=4, **kw)


def _same(a, b) -> None:
    left, right = a.to_json(), b.to_json()
    for key in ("run_id", "slot"):
        left.pop(key), right.pop(key)
    assert left == right


def _records(project):
    slot = project.config.slots[0].id
    (run,) = read_runs(project.layout, slot, "institution_proposals")
    # Everything but when the first page was read.
    return [{k: v for k, v in r.items() if k != "retrieved_at"} for r in run.records()]


@pytest.fixture(scope="module")
def reference(server, tmp_path_factory):
    project = _project(tmp_path_factory.mktemp("ref") / "p")
    proposal = _propose(project, server)
    yield proposal, _records(project)
    project.close()


def test_a_failing_page_pauses_and_the_resumed_reading_gives_the_same_proposal(
    server, reference, tmp_path
) -> None:
    project = _project(tmp_path / "p")
    server.faults.add(
        "status", service="openalex", path=r"^works\?.*cursor=", skip=6, times=2, status=503
    )
    with pytest.raises(JobPaused) as paused:
        _propose(project, server)
    server.faults.clear()
    pause = paused.value
    assert pause.code == "collect_paused" and isinstance(pause.cause, ServiceUnavailable)
    assert pause.progress == {"works": 600, "total": WORKS, "pages": 6}
    slot = project.config.slots[0].id
    assert inst.checkpoint_options(project, pause.checkpoint)["institutions"] == [ROOT_ID]
    assert not read_runs(project.layout, slot, "institution_proposals")
    before = len(server.requests)
    resumed = _propose(project, server, resume=True)
    # The resumed reading asks only the pages it did not read (7 of 13).
    assert sum(r.path == "works" and "cursor" in r.query for r in server.requests[before:]) == 7
    _same(resumed, reference[0])
    assert _records(project) == reference[1]
    assert not list(inst.checkpoint_folder(project, slot).glob("*.json.gz"))


def test_a_stop_pauses_and_resume_goes_on(server, reference, tmp_path) -> None:
    project = _project(tmp_path / "p")
    stop = threading.Event()
    seen = []

    def progress(p):
        seen.append(p)
        if p["pages"] == 9:
            stop.set()

    with pytest.raises(JobPaused) as paused:
        _propose(project, server, cancel=stop.is_set, progress=progress)
    assert paused.value.code == "collect_stopped" and paused.value.progress["pages"] == 9
    assert seen[-1]["works"] == 900 and seen[-1]["total"] == WORKS
    _same(_propose(project, server, resume=True), reference[0])


def test_a_stale_checkpoint_starts_again_with_a_note(
    server, reference, tmp_path, monkeypatch
) -> None:
    project = _project(tmp_path / "p")
    stop = threading.Event()
    with pytest.raises(JobPaused):
        _propose(project, server, cancel=stop.is_set, progress=lambda p: stop.set())
    monkeypatch.setattr(inst, "CURSOR_VALIDITY_S", -1.0)
    proposal = _propose(project, server, resume=True)
    assert [n["code"] for n in proposal.notes] == ["checkpoint_expired"]
    proposal.notes = []
    _same(proposal, reference[0])


@pytest.fixture()
def app_client(tmp_path, server, monkeypatch):
    monkeypatch.setattr(inst, "CONFIRM_WORKS", 1_000)
    root = tmp_path / "p"
    _project(root).close()
    collection = ServiceCollection(_settings(server), local=True)
    app = create_app(
        AppSettings(
            project=root, launch_token=TOKEN, data_dir=tmp_path / "data", collection=collection
        )
    )
    yield Client(app)
    app.state.cartolex.shutdown()


def _start(client, body):
    started = client.post("/api/collection/start", json={**body, "consent": True})
    assert started.status_code == 202, started.text
    return client.wait_job(started.json()["job"]["id"])


def test_a_large_list_asks_before_reading_and_resumes_in_the_app(app_client, server) -> None:
    job = _start(app_client, {"action": "institutions", "institutions": [ROOT_ID]})
    assert job["state"] == "paused", job
    pause = job["result"]["pause"]
    assert pause["code"] == "collect_size_confirm"
    assert pause["params"]["total"] == WORKS and pause["params"]["requests"] == 13
    assert pause["progress"]["works"] == 100
    listed = app_client.get("/api/jobs").json()["jobs"][0]
    assert listed["state"] == "paused" and listed["result"]["pause"]["checkpoint"]
    done = _start(app_client, {"action": "institutions", "resume": pause["checkpoint"]})
    assert done["state"] == "succeeded", done
    assert done["result"]["works"] == WORKS
    again = app_client.post(
        "/api/collection/start",
        json={"action": "institutions", "resume": pause["checkpoint"], "consent": True},
    )
    assert again.status_code == 404 and again.json()["error"]["code"] == "checkpoint_not_found"


def test_a_failed_job_records_its_cause_in_its_log(server, tmp_path) -> None:
    project = _project(tmp_path / "p")
    collection = ServiceCollection(_settings(server), local=True)
    runner = LocalJobRunner()
    # The institution itself cannot be read: the job fails before any page.
    server.faults.add("status", service="openalex", path=r"^institutions/", times=2, status=500)
    try:
        info = runner.submit(
            project="p",
            jobs_dir=project.layout.jobs,
            kind="collection",
            work=lambda control: collection.collect(
                project, control, "institutions", {"institutions": [ROOT_ID]}
            ),
        )
        done = runner.wait(info.id, timeout=60)
    finally:
        server.faults.clear()
        runner.shutdown()
    assert done.state == "failed"
    assert done.error["code"] == "collect_service_unavailable"
    assert done.error["exception"] == "ServiceUnavailable" and "status 500" in done.error["detail"]
    lines = [
        json.loads(x)
        for x in Path(project.layout.jobs / f"{info.id}.jsonl").read_text().splitlines()
    ]
    assert [x["event"] for x in lines] == ["job", "egress", "job-end"]
    assert lines[-1]["error"]["code"] == "collect_service_unavailable"
    (logged,) = read_job_logs(project.layout.jobs, "p")
    assert logged.state == "failed" and logged.error["params"]["status"] == 500
    project.close()


@pytest.mark.heavy
def test_memory_stays_bounded_on_a_hundred_thousand_works() -> None:
    """Run under ~/cartolex-work/heavy.sh: the probe reads 10⁵ works through the app's job."""
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, str(root / "tools" / "large_collect_probe.py"), "--works", "100000"],
        capture_output=True,
        text=True,
        timeout=1800,
        env={**os.environ, "PYTHONPATH": str(root)},
        check=True,
    ).stdout
    assert "state: succeeded" in out, out
    line = next(x for x in out.splitlines() if x.startswith("memory:"))
    growth = float(line.split("peak growth ")[1].split(" MB")[0])
    # Before: ~530 MB per 10⁴ works read, all kept until the end. Now: the authors only.
    assert growth < 20, line


def test_the_time_left_follows_the_recent_rate(server, tmp_path, monkeypatch) -> None:
    # Five slow pages (10 s each), then fast ones (1 s): the estimate follows the fast ones.
    ticks = iter([0.0, *(10.0 * i for i in range(1, 6)), *(50.0 + i for i in range(1, 9))])
    monkeypatch.setattr(inst, "_clock", lambda: next(ticks))
    monkeypatch.setattr(inst, "ETA_WINDOW", 3)
    seen = []
    _propose(_project(tmp_path / "p"), server, progress=seen.append)
    tenth = seen[9]
    assert tenth["works"] == 1_000 and tenth["rate"] == 100.0  # 300 works in the last 3 s
    assert tenth["eta_s"] == 2.5


def test_a_spent_budget_pauses_with_the_reset_time(app_client, server) -> None:
    server.faults.add(
        "status",
        service="openalex",
        path=r"^works\?",
        status=429,
        times=None,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1790000000"},
    )
    try:
        job = _start(app_client, {"action": "institutions", "institutions": [ROOT_ID]})
    finally:
        server.faults.clear()
    pause = job["result"]["pause"]
    assert job["state"] == "paused" and pause["code"] == "collect_budget_paused", job
    assert pause["params"]["resets_at"] == "2026-09-21T14:13:20+00:00"
    assert pause["params"]["keyed"] is False
