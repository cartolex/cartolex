# SPDX-License-Identifier: MIT
"""The start screen's API: the defaults of a new project, its starting point, the demo project."""

from __future__ import annotations

import json

import pytest
from _app_helpers import TOKEN, Client

from cartolex.app import AppSettings, create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app = create_app(AppSettings(launch_token=TOKEN, data_dir=tmp_path / "data"))
    yield Client(app)
    app.state.cartolex.shutdown()


def test_a_new_project_from_a_folder_of_texts(client, tmp_path):
    defaults = client.get("/api/projects/defaults").json()
    assert defaults["folder"] == str(tmp_path / "home" / "cartolex-projects")
    assert "demo" in defaults["start_points"] and "collaborators" in defaults["start_points"]
    root = tmp_path / "home" / "cartolex-projects" / "reefs"
    made = client.post(
        "/api/projects",
        json={
            "folder": str(root),
            "name": "Reefs",
            "domain_title": "Coral reef ecology",
            "domain_description": "Reefs, not fisheries.",
            "languages": ["en", "pt"],
            "reference": "en",
            "start": "folder",
        },
    )
    assert made.status_code == 201 and made.json()["next"] == "/?start=folder"
    config = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert [s["kind"] for s in config["slots"]] == ["collection", "folder"]
    assert config["languages"]["display"] == ["en", "pt"]
    assert client.get("/api/projects/current").json()["name"] == "Reefs"


def test_the_demo_project_is_created_and_opened(client, tmp_path):
    made = client.post("/api/projects/demo", json={})
    assert made.status_code == 201, made.text
    root = tmp_path / "home" / "cartolex-projects" / "demo"
    assert (root / "project.json").exists() and made.json()["open"] is True
    again = client.post("/api/projects/demo", json={})
    assert again.status_code == 409 and again.json()["error"]["code"] == "project_exists"
    recent = client.get("/api/projects/recent").json()["items"]
    assert recent[0]["path"] == str(root.resolve())
