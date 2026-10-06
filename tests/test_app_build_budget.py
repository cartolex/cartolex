# SPDX-License-Identifier: MIT
"""What the builds may use of this computer (memory, workers, a scratch folder): saved on
this computer, checked, and given to the builds the app starts."""

from __future__ import annotations

import os

import pytest
from _app_helpers import TOKEN, Client

from cartolex.app import AppSettings, create_app
from cartolex.app.build_run import child_recipe
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
    # Back to the defaults: nothing saved.
    cleared = client.put("/api/machine/budget", json={}).json()["build_budget"]
    assert cleared["saved"] == {} and not (tmp_path / "data" / "budget.json").exists()
