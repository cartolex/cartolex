# SPDX-License-Identifier: MIT
"""Deleting what sharing keeps: a build and its zip, older builds and leftovers, an export
and its description; what each takes on disk; never while a build or an export runs."""

from __future__ import annotations

import json
import threading

import pytest
from _app_helpers import TOKEN, Client, fake_project

from cartolex.app import AppSettings, create_app


def _build(sites, build_id, size=1000):
    folder = sites / build_id
    folder.mkdir(parents=True)
    (folder / "index.html").write_bytes(b"x" * size)
    (folder / "site.json").write_text(json.dumps({"id": build_id, "size": size, "options": {}}))


@pytest.fixture()
def shared(tmp_path):
    root = fake_project(tmp_path / "p")
    sites = root / "outputs" / "sites"
    for i, build_id in enumerate(["2026-01-01_100000", "2026-02-01_100000", "2026-03-01_100000"]):
        _build(sites, build_id, size=1000 * (i + 1))
    (sites / "latest").write_text("2026-03-01_100000\n")
    (sites / ".zips").mkdir()
    (sites / ".zips" / "2026-01-01_100000.zip").write_bytes(b"z" * 500)
    (sites / ".zips" / "gone.zip").write_bytes(b"z" * 70)  # its build was removed by hand
    (sites / ".building-2026-04-01_100000").mkdir()
    exports = root / "outputs" / "exports"
    exports.mkdir(parents=True)
    (exports / "neighbours-2026-01-01_100000.csv").write_text("a,b\n")
    (exports / "neighbours-2026-01-01_100000.meta.json").write_text("{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("mine")
    (sites / "2026-02-01_100000" / "linked").symlink_to(outside, target_is_directory=True)
    app = create_app(AppSettings(project=root, launch_token=TOKEN, data_dir=tmp_path / "data"))
    yield Client(app), root, outside
    app.state.cartolex.shutdown()


def test_the_disk_each_takes_and_deleting_a_build(shared):
    client, root, outside = shared
    sites = root / "outputs" / "sites"
    share = client.get("/api/share").json()
    oldest = next(b for b in share["items"] if b["id"] == "2026-01-01_100000")
    assert oldest["zip_size"] == 500 and oldest["disk"] > 1500
    assert share["disk"]["older"]["count"] == 2 and share["disk"]["exports"] > 0
    assert share["disk"]["total"] >= 6000 + 500 + 70

    done = client.delete("/api/share/builds/2026-01-01_100000").json()
    assert done["bytes"] == oldest["disk"] and done["latest"] == "2026-03-01_100000"
    assert not (sites / "2026-01-01_100000").exists()
    assert not (sites / ".zips" / "2026-01-01_100000.zip").exists()
    # the latest goes: the marker names the newest left, and a link inside is not followed
    done = client.delete("/api/share/builds/2026-03-01_100000").json()
    assert done["latest"] == "2026-02-01_100000"
    assert (sites / "latest").read_text().strip() == "2026-02-01_100000"
    missing = client.delete("/api/share/builds/2026-03-01_100000")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "site_not_found"
    assert client.delete("/api/share/builds/2026-02-01_100000").status_code == 200
    assert (outside / "keep.txt").read_text() == "mine" and not (sites / "latest").exists()


def test_deleting_older_builds_and_leftovers(shared):
    client, root, _ = shared
    sites = root / "outputs" / "sites"
    plan = client.post("/api/share/builds/prune", json={"plan": True}).json()
    assert plan["count"] == 2 and plan["leftovers"] == 2 and plan["kept"] == "2026-03-01_100000"
    assert (sites / "2026-01-01_100000").exists()
    done = client.post("/api/share/builds/prune", json={}).json()
    assert done["bytes"] == plan["bytes"]
    assert sorted(p.name for p in sites.iterdir()) == [".zips", "2026-03-01_100000", "latest"]
    assert list((sites / ".zips").iterdir()) == []


def test_deleting_an_export_takes_its_description_too(shared):
    client, root, _ = shared
    exports = root / "outputs" / "exports"
    done = client.delete("/api/share/exports/neighbours-2026-01-01_100000.csv").json()
    assert sorted(done["files"]) == ["neighbours-2026-01-01_100000.csv",
                                     "neighbours-2026-01-01_100000.meta.json"]  # fmt: skip
    assert list(exports.iterdir()) == []
    gone = client.delete("/api/share/exports/neighbours-2026-01-01_100000.csv")
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "export_not_found"
    assert client.delete("/api/share/exports/..%2Fproject.json").status_code >= 400
    assert (root / "project.json").exists()


def test_nothing_is_deleted_while_a_site_build_runs(shared):
    client, root, _ = shared
    runtime = client.app.state.cartolex
    current = runtime.projects.current()
    release = threading.Event()
    job = runtime.jobs.submit(project=current.id, jobs_dir=current.layout.jobs, kind="site",
                              work=lambda control: release.wait(10) and {})  # fmt: skip
    try:
        refused = client.delete("/api/share/builds/2026-01-01_100000")
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "busy"
        assert client.post("/api/share/builds/prune", json={}).json()["error"]["code"] == "busy"
        assert (root / "outputs" / "sites" / "2026-01-01_100000").exists()
    finally:
        release.set()
        client.wait_job(job.id)
