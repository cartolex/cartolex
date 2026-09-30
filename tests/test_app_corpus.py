# SPDX-License-Identifier: MIT
"""The corpus screen's API on the demo services (world XS, on this computer): import a list,
plan and confirm what leaves the computer, find identities, accept the clear ones in bulk,
harvest, then read coverage, organisations, texts and a person's sheet."""

from __future__ import annotations

import csv
import io

import pytest
from _app_helpers import TOKEN, Client, etag

from cartolex.app import AppSettings, create_app
from cartolex.app.collect_service import ServiceCollection
from cartolex.collect import local_settings
from cartolex.demo import generate
from cartolex.demo.services import DemoServices
from cartolex.project import Project
from cartolex.project.models import Level


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture()
def client(tmp_path, services):
    root = tmp_path / "p"
    project = Project.init(
        root, name="Collected", domain_title="Coastal systems", corpus_languages=("en", "fr")
    )
    levels = [Level(id="lab", names={"en": "Lab"}), Level(id="institution", names={"en": "Inst"})]
    project.save_config(project.config.model_copy(update={"levels": levels}), action="levels")
    project.close()
    collection = ServiceCollection(local_settings(services.endpoints()), local=True)
    app = create_app(
        AppSettings(
            project=root, launch_token=TOKEN, data_dir=tmp_path / "data", collection=collection
        )
    )
    yield Client(app)
    app.state.cartolex.shutdown()


def _list(services) -> str:
    rows = services.bibliography.people_rows()
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _run(client: Client, body: dict) -> dict:
    started = client.post("/api/collection/start", json={**body, "consent": True})
    assert started.status_code == 202, started.text
    done = client.wait_job(started.json()["job"]["id"])
    assert done["state"] == "succeeded", done
    return done["result"]


def test_collecting_from_the_services_end_to_end(client, services):
    # ── the list: one field per column, organisations at the project's levels ──
    proposal = client.post("/api/people/import", json={"text": _list(services)}).json()
    assert proposal["mapping"]["lab"] == "org:lab"
    assert proposal["mapping"]["institution"] == "org:institution"
    assert proposal["mapping"]["career_stage"] == "column"
    confirmed = client.post(
        f"/api/people/import/{proposal['import_id']}/confirm",
        json={"mapping": proposal["mapping"]},
        headers={"If-Match": etag(client.get("/api/people"))},
    )
    assert confirmed.status_code == 200, confirmed.text
    people = client.get("/api/people?limit=500").json()
    assert people["total"] == confirmed.json()["added"] > 10
    facet = next(f for f in people["facets"] if f["column"] == "career_stage")
    stage = facet["values"][0]["value"]
    only = client.get(f"/api/people?col=career_stage:{stage}").json()
    assert only["total"] == facet["values"][0]["count"]

    # ── what leaves the computer, said before anything is sent; consent is required ──
    plan = client.post("/api/collection/plan", json={"action": "identify"}).json()
    assert plan["consent_needed"] and plan["people"] == people["total"]
    assert {h["service"] for h in plan["leaves_the_computer"]} >= {"openalex", "orcid", "hal"}
    assert any(s["code"] == "sends_names" for h in plan["leaves_the_computer"] for s in h["sends"])
    assert plan["never_leaves"] and plan["notes"][0]["code"] == "note_local"
    refused = client.post("/api/collection/start", json={"action": "identify"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "consent_needed"

    # ── identities: candidates of every finder, the single clear matches in bulk ──
    _run(client, {"action": "identify"})
    queue = client.get("/api/collection/identities?limit=500")
    body = queue.json()
    assert body["counts"]["clear"] > 0
    finders = {c["finder"] for item in body["items"] for c in item["candidates"]}
    assert {"openalex", "hal"} <= finders
    clear = client.get("/api/collection/identities?clear=true&limit=500").json()
    assert clear["total"] == body["counts"]["clear"]
    accepted = client.post(
        "/api/collection/identities/accept",
        json={"person_ids": [i["person_id"] for i in body["items"]]},
        headers={"If-Match": etag(queue)},
    )
    assert accepted.status_code == 200, accepted.text
    assert len(accepted.json()["accepted"]) == body["counts"]["clear"]
    assert set(accepted.json()["left"]) == {
        i["person_id"] for i in body["items"] if not any(c["clear"] for c in i["candidates"])
    }

    # ── harvest, then read what came in ──
    result = _run(client, {"action": "harvest"})
    assert result["texts"] > 0 and result["egress"]
    coverage = client.get("/api/collection/coverage").json()
    assert sum(coverage["states"].values()) == coverage["counted"] == people["total"]
    assert coverage["states"]["good"] > 0 and coverage["by_organisation"]
    assert coverage["by_year"] and coverage["by_language"]
    waiting = client.get("/api/people?coverage=no_data&limit=5").json()["items"]
    assert waiting and all(p["cause"] for p in waiting)
    orgs = client.get("/api/organisations?level=lab").json()
    assert orgs["total"] > 0 and all(o["parents"] for o in orgs["items"])
    org = client.get(f"/api/organisations/{orgs['items'][0]['org_id']}").json()
    assert org["affiliations"] and org["parents"]
    texts = client.get("/api/texts?content=abstract&limit=3").json()
    assert texts["total"] > 0
    # The index's duplicate texts stay in the tables, and the tab says they are read once.
    every = client.get("/api/texts?limit=500").json()
    copies = [t for t in every["items"] if t["copy_of"]]
    assert copies and every["counts"]["duplicates"] == len(copies)
    text = client.get(f"/api/texts/{texts['items'][0]['text_id']}").json()
    assert any(p["part"] == "abstract" and p["provider"] for p in text["parts"]) and text["people"]
    good = client.get("/api/people?coverage=good&limit=1").json()["items"][0]
    sheet = client.get(f"/api/people/{good['person_id']}/sheet").json()
    assert sheet["sheet"]["state"] == "good" and sheet["texts"] and sheet["affiliations"]
    assert sheet["sheet"]["sources"]
    blocked = client.get(f"/api/people/{waiting[0]['person_id']}/sheet").json()
    assert blocked["sheet"]["cause_text"]

    # ── bulk role change by filter ──
    listed = client.get("/api/people")
    changed = client.patch(
        "/api/people",
        json={"where": {"coverage": "no_data"}, "role": "context"},
        headers={"If-Match": etag(listed)},
    )
    assert changed.status_code == 200 and changed.json()["changed"] == len(
        client.get("/api/people?coverage=no_data&limit=500").json()["items"]
    )


def test_documents_of_one_person_come_in_as_a_job(client, services):
    proposal = client.post(
        "/api/people/import", json={"text": "Tavelin, Ada\nOrrin, Bram\n"}
    ).json()
    client.post(
        f"/api/people/import/{proposal['import_id']}/confirm",
        json={"mapping": proposal["mapping"]},
        headers={"If-Match": etag(client.get("/api/people"))},
    )
    person = client.get("/api/people?q=tavelin").json()["items"][0]
    archive = _zip_of({"notes/tide-gauges.txt": "Tide gauges along the coast."})
    started = client.post(
        "/api/people/import/documents",
        files={"file": ("docs.zip", archive)},
        data={"kind": "folder", "person_id": person["person_id"]},
    )
    assert started.status_code == 202, started.text
    done = client.wait_job(started.json()["job"]["id"])
    assert done["state"] == "succeeded" and done["result"]["texts"] == 1
    sheet = client.get(f"/api/people/{person['person_id']}/sheet").json()
    assert [t["content"] for t in sheet["texts"]] == ["full"]
    refused = client.post(
        "/api/people/import/documents",
        files={"file": ("empty.zip", _zip_of({"readme.csv": "a,b\n"}))},
        data={"kind": "folder"},
    )
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "documents_missing"


def _zip_of(files: dict[str, str]) -> bytes:
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return out.getvalue()
