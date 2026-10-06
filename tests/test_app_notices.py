# SPDX-License-Identifier: MIT
"""The collection notice's three levels (none, brief, full), enforced by the server, the
acknowledgements kept per person in the app's folder and their reset; OpenAlex's cost
against its free daily budget; the next action of a spent budget."""

from __future__ import annotations

import csv
import io

import pytest
from _app_helpers import TOKEN, Client, etag

from cartolex.app import AppSettings, create_app
from cartolex.app.collect_service import ServiceCollection
from cartolex.app.messages import job_error
from cartolex.app.notices import notice_level
from cartolex.collect import local_settings
from cartolex.collect.http import ServiceUnavailable
from cartolex.collect.privacy import SNAPSHOT_ADVICE_USD, openalex_budget
from cartolex.demo import generate
from cartolex.demo.services import DemoServices
from cartolex.project import Project


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


def _app(tmp_path, services):
    collection = ServiceCollection(local_settings(services.endpoints()), local=True)
    settings = AppSettings(
        project=tmp_path / "p", launch_token=TOKEN, data_dir=tmp_path / "data",
        collection=collection,
    )  # fmt: skip
    return create_app(settings)


@pytest.fixture()
def client(tmp_path, services):
    Project.init(tmp_path / "p", name="Collected", domain_title="Coastal systems").close()
    app = _app(tmp_path, services)
    client = Client(app)
    rows = services.bibliography.people_rows()
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    proposal = client.post("/api/people/import", json={"text": out.getvalue()}).json()
    confirmed = client.post(
        f"/api/people/import/{proposal['import_id']}/confirm",
        json={"mapping": proposal["mapping"]},
        headers={"If-Match": etag(client.get("/api/people"))},
    )
    assert confirmed.status_code == 200, confirmed.text
    yield client
    app.state.cartolex.shutdown()


def _plan(client, body):
    return client.post("/api/collection/plan", json=body).json()


def test_the_three_levels_and_their_memory(client, tmp_path, services):
    # an institution searched by its name: nothing personal, within the budget, no consent
    search = {"action": "institutions", "search": "Marine"}
    plan = _plan(client, search)
    assert plan["notice"]["level"] == "none" and not plan["consent_needed"]
    started = client.post("/api/collection/start", json=search)
    assert started.status_code == 202, started.text
    client.wait_job(started.json()["job"]["id"])

    # names leave the computer: the full notice the first time, consent required
    identify = {"action": "identify"}
    plan = _plan(client, identify)
    assert plan["notice"]["level"] == "full" and plan["notice"]["reasons"] == ["first_time"]
    refused = client.post("/api/collection/start", json=identify)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "consent_needed"
    started = client.post("/api/collection/start", json={**identify, "consent": True,
                                                          "remember": True})  # fmt: skip
    assert started.status_code == 202, started.text
    client.wait_job(started.json()["job"]["id"])

    # acknowledged: brief from now on (still a confirmation), kept in the app's folder
    assert _plan(client, identify)["notice"]["level"] == "brief"
    assert client.post("/api/collection/start", json=identify).status_code == 409
    kinds = client.get("/api/me/notices").json()["acknowledged"]
    assert [k["kind"] for k in kinds] == ["identify"]
    assert list((tmp_path / "data" / "users").glob("*.notices.json"))

    # another content (a service less): full again, as changed
    changed = _plan(client, {"action": "identify", "hal": False})["notice"]
    assert changed["level"] == "full" and changed["reasons"] == ["changed"]

    # the reset in Settings › Privacy
    assert client.delete("/api/me/notices").json()["acknowledged"] == []
    assert _plan(client, identify)["notice"]["level"] == "full"


def test_beyond_the_free_budget_the_notice_is_full():
    hosts = [{"service": "openalex", "host": "api.openalex.org",
              "sends": [{"code": "sends_institution_names"}]}]  # fmt: skip
    within = {"action": "institutions", "leaves_the_computer": hosts,
              "budget": openalex_budget(0.05, False)}  # fmt: skip
    assert notice_level(within, {})["level"] == "none"
    over = {**within, "budget": openalex_budget(0.5, False)}
    assert notice_level(over, {})["level"] == "full"
    assert notice_level(over, {})["reasons"] == ["over_budget"]


def test_the_cost_against_the_daily_budgets():
    fits = openalex_budget(0.08, keyed=False)
    assert fits["fits"] and fits["state"] == "fits" and fits["days"] == 1
    needs_key = openalex_budget(0.35, keyed=False)
    assert needs_key["state"] == "needs_key" and needs_key["days"] == 4
    assert needs_key["days_with_key"] == 1
    keyed = openalex_budget(3.2, keyed=True)
    assert keyed["state"] == "days" and keyed["days"] == 4 and not keyed["snapshot_advised"]
    assert openalex_budget(SNAPSHOT_ADVICE_USD + 1, keyed=True)["snapshot_advised"]


def test_a_spent_budget_says_what_to_do():
    unkeyed = ServiceUnavailable("api.openalex.org", 429, "spent", "wait", budget_spent=True)
    out = job_error(unkeyed)
    assert out["code"] == "collect_budget_spent"
    assert out["next"]["action"] == "open:/settings?section=sources"
    keyed = ServiceUnavailable(
        "api.openalex.org", 429, "spent", "wait", budget_spent=True, keyed=True
    )
    assert job_error(keyed)["next"]["action"] == "none" and job_error(keyed)["params"]["keyed"]
