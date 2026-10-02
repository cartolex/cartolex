# SPDX-License-Identifier: MIT
"""OpenAlex read from a snapshot in the app: the folder saved on this computer and checked
against its manifests, the plan's two ways of reading OpenAlex, and a harvest read on this
computer, with nothing sent to OpenAlex."""

from __future__ import annotations

import shutil

import pytest
from _app_helpers import TOKEN, Client
from _collect_world import confirm_truth, demo_project, world_ids

from cartolex.app import AppSettings, create_app
from cartolex.app.collect_service import ServiceCollection
from cartolex.collect import local_settings
from cartolex.demo import generate
from cartolex.demo.services import DemoServices, write_snapshot


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture(scope="module")
def snapshot_dir(services, tmp_path_factory):
    folder = tmp_path_factory.mktemp("snapshot") / "openalex"
    write_snapshot(services.bibliography, folder, per_part=20)
    return folder


@pytest.fixture()
def client(tmp_path, services):
    bib = services.bibliography
    project = demo_project(tmp_path / "p", bib)
    confirm_truth(project, bib, world_ids(project, bib))
    project.close()
    collection = ServiceCollection(local_settings(services.endpoints()), local=True)
    app = create_app(
        AppSettings(
            project=tmp_path / "p",
            launch_token=TOKEN,
            data_dir=tmp_path / "data",
            collection=collection,
        )
    )
    yield Client(app)
    app.state.cartolex.shutdown()


def _hosts(plan: dict) -> set[str]:
    return {h["service"] for h in plan["leaves_the_computer"]}


def test_the_folder_is_saved_on_this_computer_and_checked(client, snapshot_dir, tmp_path):
    assert client.get("/api/machine").json()["snapshot"] is None
    for folder in (str(tmp_path / "p"), "openalex-snapshot"):  # no snapshot there; not a full path
        refused = client.put("/api/machine/snapshot", json={"folder": folder})
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "snapshot_invalid"
    saved = client.put("/api/machine/snapshot", json={"folder": str(snapshot_dir)})
    status = saved.json()["snapshot"]
    assert status["state"] == "ready" and status["release"] == "2026-01-14"
    assert status["bytes"]["works"] > 0 and not status["rate_measured"]
    assert (tmp_path / "data" / "snapshot.json").is_file()
    # A download that stopped midway is incomplete; a disk not plugged in, missing.
    partial = tmp_path / "partial"
    shutil.copytree(snapshot_dir, partial)
    next((partial / "data" / "jsonl" / "works").glob("updated_date=*/part_*.gz")).unlink()
    status = client.put("/api/machine/snapshot", json={"folder": str(partial)}).json()["snapshot"]
    assert status["state"] == "incomplete" and status["missing"] == 1
    plan = client.post("/api/collection/plan", json={"action": "harvest"}).json()
    assert plan["openalex"]["preselected"] == "api" and "openalex" in _hosts(plan)
    asked = client.post("/api/collection/plan", json={"action": "harvest", "openalex": "snapshot"})
    assert asked.status_code == 409 and asked.json()["error"]["code"] == "snapshot_unavailable"
    shutil.rmtree(partial)
    client.app.state.cartolex.snapshot._checked = None  # past the check's cache
    assert client.get("/api/machine").json()["snapshot"]["state"] == "missing"
    removed = client.put("/api/machine/snapshot", json={"folder": None})
    assert removed.json()["snapshot"] is None
    assert (
        client.post("/api/collection/plan", json={"action": "harvest"}).json()["openalex"] is None
    )


def test_a_harvest_reads_openalex_from_the_snapshot(client, snapshot_dir):
    client.put("/api/machine/snapshot", json={"folder": str(snapshot_dir)})
    plan = client.post("/api/collection/plan", json={"action": "harvest"}).json()
    route = plan["openalex"]
    assert route["chosen"] == route["preselected"]
    assert route["api"]["requests"] > 0 and route["snapshot"]["bytes"] > 0
    assert route["snapshot"]["seconds"] is not None
    api = client.post("/api/collection/plan", json={"action": "harvest", "openalex": "api"}).json()
    assert api["openalex"]["chosen"] == "api" and "openalex" in _hosts(api)
    body = {"action": "harvest", "openalex": "snapshot", "hal": False}
    snap = client.post("/api/collection/plan", json=body).json()
    assert snap["openalex"]["chosen"] == "snapshot" and "openalex" not in _hosts(snap)
    assert any(n["code"] == "note_snapshot" for n in snap["notes"])
    # Identities are searched by name: never offered the snapshot.
    assert (
        client.post("/api/collection/plan", json={"action": "identify"}).json()["openalex"] is None
    )

    started = client.post("/api/collection/start", json={**body, "consent": True})
    assert started.status_code == 202, started.text
    done = client.wait_job(started.json()["job"]["id"])
    assert done["state"] == "succeeded", done
    result = done["result"]
    assert result["texts"] > 0
    assert result["snapshot"]["release"] == "2026-01-14" and result["snapshot"]["bytes"] > 0
    assert "openalex" not in {e["service"] for e in result["egress"]}
    coverage = client.get("/api/collection/coverage").json()
    assert coverage["states"]["good"] > 0


def test_an_indexed_snapshot_is_read_through_its_index(client, snapshot_dir, tmp_path):
    from cartolex.collect.snapshot_index import build_index

    folder = tmp_path / "indexed"
    shutil.copytree(snapshot_dir, folder)
    assert build_index(folder, block_bytes=4096).complete
    status = client.put("/api/machine/snapshot", json={"folder": str(folder)}).json()["snapshot"]
    assert status["state"] == "ready" and status["indexed"]
    assert status["index"]["state"] == "complete"
    plan = client.post("/api/collection/plan", json={"action": "harvest"}).json()
    assert plan["openalex"]["snapshot"]["indexed"]
    body = {"action": "harvest", "openalex": "snapshot", "hal": False, "consent": True}
    started = client.post("/api/collection/start", json=body)
    done = client.wait_job(started.json()["job"]["id"])
    assert done["state"] == "succeeded", done
    assert done["result"]["texts"] > 0 and done["result"]["snapshot"]["members"] > 0
