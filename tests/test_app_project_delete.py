# SPDX-License-Identifier: MIT
"""Removing a project from the list, and deleting its folder: only a listed cartolex project,
never what cartolex did not write, never through a link, never while held or busy."""

from __future__ import annotations

import json
import threading

import pytest
from _app_helpers import TOKEN, Client, fake_project

from cartolex.app import AppSettings, create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app = create_app(AppSettings(launch_token=TOKEN, data_dir=tmp_path / "data"))
    yield Client(app)
    app.state.cartolex.shutdown()


def _listed(client, root):
    """Open *root* then another project, so *root* is listed and not open."""
    assert client.post("/api/projects/open", json={"path": str(root)}).status_code == 200
    other = fake_project(root.parent / f"{root.name}-other")
    assert client.post("/api/projects/open", json={"path": str(other)}).status_code == 200
    return str(root.resolve())


def test_removing_from_the_list_keeps_the_folder(client, tmp_path):
    root = fake_project(tmp_path / "p")
    path = _listed(client, root)
    after = client.post("/api/projects/forget", json={"path": path})
    assert after.status_code == 200 and path not in {i["path"] for i in after.json()["items"]}
    assert (root / "project.json").exists()
    again = client.post("/api/projects/forget", json={"path": path})
    assert again.status_code == 404 and again.json()["error"]["code"] == "project_not_listed"


def test_deleting_a_folder_asks_first_and_keeps_what_cartolex_did_not_write(client, tmp_path):
    root = fake_project(tmp_path / "p")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("mine")
    (root / "sources" / "linked").symlink_to(outside, target_is_directory=True)
    (root / "notes.txt").write_text("not cartolex's")
    path = _listed(client, root)

    plan = client.get("/api/projects/removal", params={"path": path}).json()
    assert plan["bytes"] > 0 and plan["kept"] == ["notes.txt"] and plan["held"] is None
    refused = client.post("/api/projects/delete", json={"path": path})
    assert refused.json()["error"]["code"] == "project_delete_confirm"
    assert (root / "project.json").exists()

    done = client.post("/api/projects/delete", json={"path": path, "confirm": True}).json()
    assert done["deleted"] and not done["folder_removed"] and done["kept"] == ["notes.txt"]
    assert sorted(p.name for p in root.iterdir()) == ["notes.txt"]
    assert (outside / "keep.txt").read_text() == "mine"  # the link was not followed
    assert path not in {i["path"] for i in client.get("/api/projects/recent").json()["items"]}


def test_the_open_project_is_closed_then_its_folder_deleted(client, tmp_path):
    root = fake_project(tmp_path / "p")
    assert client.post("/api/projects/open", json={"path": str(root)}).status_code == 200
    path = str(root.resolve())
    assert client.get("/api/projects/removal", params={"path": path}).json()["open"] is True
    done = client.post("/api/projects/delete", json={"path": path, "confirm": True}).json()
    assert done["closed"] and done["folder_removed"] and not root.exists()
    assert client.get("/api/projects/current").json() == {"open": False}


def test_refusals_held_busy_not_a_project_not_listed(client, tmp_path):
    held = fake_project(tmp_path / "held")
    path = _listed(client, held)
    lock = {
        "pid": 1,
        "host": "another-computer",
        "app": "cartolex",
        "since": "2026-01-01T00:00:00Z",
    }
    (held / ".lock").write_text(json.dumps(lock))
    plan = client.get("/api/projects/removal", params={"path": path}).json()
    assert plan["held"]["host"] == "another-computer"
    refused = client.post("/api/projects/delete", json={"path": path, "confirm": True})
    assert refused.json()["error"]["code"] == "project_delete_held"
    assert (held / "project.json").exists() and (held / "decisions").is_dir()

    busy = fake_project(tmp_path / "busy")
    assert client.post("/api/projects/open", json={"path": str(busy)}).status_code == 200
    runtime = client.app.state.cartolex
    current = runtime.projects.current()
    release = threading.Event()
    job = runtime.jobs.submit(project=current.id, jobs_dir=current.layout.jobs, kind="build",
                              work=lambda control: release.wait(10) and {})  # fmt: skip
    try:
        refused = client.post("/api/projects/delete",
                              json={"path": str(busy.resolve()), "confirm": True})  # fmt: skip
        assert refused.json()["error"]["code"] == "busy"
        assert (busy / "project.json").exists()
    finally:
        release.set()
        client.wait_job(job.id)

    unlisted = client.post("/api/projects/delete",
                           json={"path": str(tmp_path / "nowhere"), "confirm": True})  # fmt: skip
    assert unlisted.json()["error"]["code"] == "project_not_listed"


def test_a_listed_folder_that_is_not_a_project_or_is_a_link_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "project.json").write_text(json.dumps({"format": "something-else/1"}))
    real = fake_project(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    data = tmp_path / "data"
    data.mkdir()
    items = [{"path": str(plain), "name": "x"}, {"path": str(link), "name": "y"}]
    (data / "recent.json").write_text(
        json.dumps({"format": "cartolex-recent/1", "projects": items})
    )
    app = create_app(AppSettings(launch_token=TOKEN, data_dir=data))
    try:
        client = Client(app)
        for folder, reason in ((plain, "not_a_project"), (link, "link")):
            error = client.post("/api/projects/delete",
                                json={"path": str(folder), "confirm": True}).json()["error"]  # fmt: skip
            assert error["code"] == "project_delete_refused" and error["params"]["reason"] == reason
        assert (plain / "project.json").exists() and (real / "project.json").exists()
    finally:
        app.state.cartolex.shutdown()


def test_a_hosted_service_never_deletes_a_project(tmp_path):
    root = tmp_path / "projects"
    fake_project(root / "alpha")
    app = create_app(AppSettings(mode="hosted", projects_root=root,
                                 allowed_hosts=("127.0.0.1",), launch_token=TOKEN))  # fmt: skip
    try:
        client = Client(app)
        refused = client.post("/api/projects/delete",
                              json={"path": str(root / "alpha"), "confirm": True})  # fmt: skip
        assert refused.json()["error"]["code"] == "project_delete_hosted"
        assert (root / "alpha" / "project.json").exists()
    finally:
        app.state.cartolex.shutdown()
