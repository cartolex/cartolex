# SPDX-License-Identifier: MIT
"""What the builds may use of this computer (memory, workers, a scratch folder): saved on
this computer, checked, and given to the builds the app starts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _app_helpers import TOKEN, Client

from cartolex.app import AppSettings, create_app
from cartolex.app.build_run import child_recipe
from cartolex.collect import workstore
from cartolex.collect.tables import rebuild_sources
from cartolex.project import Project
from cartolex.scale import Budget


@pytest.fixture()
def app(tmp_path):
    app = create_app(AppSettings(launch_token=TOKEN, data_dir=tmp_path / "data"))
    yield app
    app.state.cartolex.shutdown()


def test_the_budget_is_saved_on_this_computer_checked_and_given_to_builds(app, tmp_path):
    client = Client(app)
    default = Budget.for_machine()
    status = client.get("/api/machine").json()["build_budget"]
    assert status["saved"] == {} and status["memory_mb"] == default.memory_mb
    assert status["default"] == {"memory_mb": default.memory_mb, "workers": default.workers}
    for body, field in (
        ({"memory_mb": 100}, "memory_mb"),
        ({"workers": (os.cpu_count() or 1) + 1}, "workers"),
        ({"scratch": "relative/folder"}, "scratch"),
        ({"scratch": str(tmp_path / "missing")}, "scratch"),
    ):
        refused = client.put("/api/machine/budget", json=body)
        assert refused.status_code == 422, body
        error = refused.json()["error"]
        assert error["code"] == "budget_invalid" and error["params"]["field"] == field
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    saved = client.put(
        "/api/machine/budget", json={"memory_mb": 2048, "workers": 1, "scratch": str(scratch)}
    ).json()["build_budget"]
    assert (saved["memory_mb"], saved["workers"], saved["scratch"]) == (2048, 1, str(scratch))
    assert (tmp_path / "data" / "budget.json").is_file()
    budget = child_recipe(app.state.cartolex)["options"].budget
    assert (budget.memory_mb, budget.workers, budget.scratch) == (2048, 1, scratch)
    # Given to the app's process: a collection's rebuild of the tables takes its folder.
    assert Budget.given() == budget
    seen = []

    class Store(workstore.WorkStore):
        def __init__(self, folder, *args, **kwargs):
            seen.append(Path(folder))
            super().__init__(folder, *args, **kwargs)

    project = Project.init(tmp_path / "p", name="Budget", domain_title="Budget")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(workstore, "WorkStore", Store)
        rebuild_sources(project.layout, project.config)
    project.close()
    assert seen == [scratch]
    # Back to the defaults: nothing saved, nothing given.
    cleared = client.put("/api/machine/budget", json={}).json()["build_budget"]
    assert cleared["saved"] == {} and not (tmp_path / "data" / "budget.json").exists()
    assert Budget.given() is None
