# SPDX-License-Identifier: MIT
"""The screens' API on a small fake project: people, themes, settings, snapshots, share, handoff."""

from __future__ import annotations

import pytest
from _app_helpers import TOKEN, Client, etag

from cartolex.app import AppSettings, create_app
from cartolex.project import Project
from cartolex.project.models import Overlay, Slot


@pytest.fixture()
def client(tmp_path):
    root = tmp_path / "p"
    project = Project.init(
        root,
        name="Small map",
        domain_title="Ocean physics",
        corpus_languages=("en", "fr"),
        slots=(Slot(id="collected", kind="collection"), Slot(id="docs", kind="folder")),
    )
    project.save_config(
        project.config.model_copy(update={"overlays": [Overlay(id="applicants")]}),
        action="overlays",
    )
    project.close()
    app = create_app(AppSettings(project=root, launch_token=TOKEN, data_dir=tmp_path / "data"))
    yield Client(app)
    app.state.cartolex.shutdown()


CSV = (
    "Nom;Prénom;ORCID;Laboratoire;Grade\n"
    "Walrusson;Ada;0000-0002-1825-0097;LAB-A;senior\n"
    "Otterby;Bram;;LAB-A;phd\n"
    "Seaholm;Cleo;;LAB-B;postdoc\n"
    ";;;;\n"
)


def _import(client: Client, text: str = CSV, name: str = "list.csv", role: str = "mapped"):
    proposal = client.post("/api/people/import", files={"file": (name, text.encode("utf-8"))})
    assert proposal.status_code == 200, proposal.text
    body = proposal.json()
    confirm = client.post(
        f"/api/people/import/{body['import_id']}/confirm",
        json={
            "mapping": body["mapping"],
            "role": role,
            "set": "applicants" if role == "projected" else "",
        },
        headers={"If-Match": etag(client.get("/api/people"))},
    )
    assert confirm.status_code == 200, confirm.text
    return body, confirm.json()


def test_importing_a_list_proposes_a_mapping_then_adds_people(client):
    body, done = _import(client)
    assert body["kind"] == "table"
    assert body["mapping"] == {
        "Nom": "last_name",
        "Prénom": "first_name",
        "ORCID": "orcid",
        "Laboratoire": "org:laboratoire",
        "Grade": "column",
    }
    assert body["preview"][0] == ["Walrusson", "Ada", "0000-0002-1825-0097", "LAB-A", "senior"]
    assert done["added"] == 3
    people = client.get("/api/people?sort=name").json()
    assert [p["last_name"] for p in people["items"]] == ["Otterby", "Seaholm", "Walrusson"]
    ada = people["items"][2]
    assert ada["orcid"] == "0000-0002-1825-0097" and ada["unit"] == "LAB-A"
    assert ada["columns"] == {"Grade": "senior"} and ada["identity"] == "pending"
    assert ada["coverage"]["class"] == "none"
    # the same list again adds nobody; a pasted list of names proposes its duplicates
    _, again = _import(client)
    assert again["added"] == 0 and again["already_known"] == 3
    _, pasted = _import(
        client, "Name\nSeaholm, Cleo\nDune Marlow\n", name="pasted.txt", role="projected"
    )
    assert pasted["added"] == 2
    assert [d["reason"] for d in pasted["duplicates"]] == ["the same name"]
    marlow = client.get("/api/people?q=marlow").json()["items"][0]
    assert (marlow["last_name"], marlow["first_name"]) == ("Marlow", "Dune")
    assert (marlow["role"], marlow["set"]) == ("projected", "applicants")
    counts = client.get("/api/people").json()["counts"]["role"]
    assert counts == {"mapped": 3, "projected": 2}
    # a mapping without a name is refused, and the import stays waiting
    proposal = client.post("/api/people/import", json={"text": CSV}).json()
    bad = client.post(
        f"/api/people/import/{proposal['import_id']}/confirm",
        json={"mapping": {"Grade": "column"}},
        headers={"If-Match": etag(client.get("/api/people"))},
    )
    assert bad.status_code == 422 and "last_name" in bad.json()["error"]["message"]
    assert client.delete(f"/api/people/import/{proposal['import_id']}").status_code == 200
    assert client.delete(f"/api/people/import/{proposal['import_id']}").status_code == 404


def test_roles_sets_notes_and_merges(client):
    _import(client)
    people = client.get("/api/people?sort=name")
    ids = [p["person_id"] for p in people.json()["items"]]
    r = client.patch(
        "/api/people",
        json={"person_ids": ids[:2], "role": "context"},
        headers={"If-Match": etag(people)},
    )
    assert r.status_code == 200 and r.json()["changed"] == 2
    assert client.get("/api/people?role=context").json()["total"] == 2
    bad = client.patch(
        "/api/people",
        json={"person_ids": ids[:1], "role": "projected", "set": "nowhere"},
        headers={"If-Match": etag(r)},
    )
    assert bad.status_code == 422
    unknown = client.patch(
        "/api/people", json={"person_ids": ["p9999"], "note": "x"}, headers={"If-Match": etag(r)}
    )
    assert unknown.status_code == 404
    merged = client.post(
        "/api/people/merge",
        json={"target": ids[0], "sources": [ids[1]]},
        headers={"If-Match": etag(r)},
    )
    assert merged.status_code == 200 and merged.json()["merged"] == [ids[1]]
    chained = client.post(
        "/api/people/merge",
        json={"target": ids[2], "sources": [ids[0]]},
        headers={"If-Match": etag(merged)},
    )
    assert sorted(chained.json()["merged"]) == sorted([ids[0], ids[1]])
    into = {p["person_id"]: p["merged_into"] for p in client.get("/api/people").json()["items"]}
    assert into[ids[1]] == into[ids[0]] == ids[2]
    refused = client.post(
        "/api/people/merge",
        json={"target": ids[0], "sources": [ids[2]]},
        headers={"If-Match": etag(chained)},
    )
    assert refused.status_code == 409


def test_identities_without_a_collection_service(client):
    _import(client)
    queue = client.get("/api/collection/identities")
    assert queue.json()["total"] == 3 and queue.json()["items"][0]["candidates"] == []
    pid = queue.json()["items"][0]["person_id"]
    none = client.post(
        f"/api/collection/identities/{pid}",
        json={"decision": "none"},
        headers={"If-Match": etag(queue)},
    )
    assert none.status_code == 200 and none.json()["identity"] == "none"
    pasted = client.post(
        f"/api/collection/identities/{pid}",
        json={"decision": "id", "record": "0000-0002-1825-0097"},
        headers={"If-Match": etag(none)},
    )
    assert pasted.json()["records"] == "orcid:0000-0002-1825-0097"
    wrong = client.post(
        f"/api/collection/identities/{pid}",
        json={"decision": "id", "record": "not an id"},
        headers={"If-Match": etag(pasted)},
    )
    assert wrong.status_code == 422
    assert client.post("/api/collection/start").status_code == 409
    assert client.get("/api/collection/plan").json()["available"] is False
    coverage = client.get("/api/collection/coverage").json()
    assert coverage["classes"] == {"good": 0, "thin": 0, "none": 3}


def test_theme_operations_saves_versions_and_restore(client):
    from cartolex.project.themes import new_tree

    tree = new_tree(2).model_dump(mode="json", by_alias=True)
    tree["keywords"] = {}
    ops = [
        {"op": "create_node", "parent": None, "names": {"en": "Waves"}},
        {"op": "create_node", "parent": "n1", "names": {"en": "Tides"}},
        {"op": "rename_level", "level": 1, "names": {"en": "Area"}},
    ]
    edited = client.post("/api/themes/ops", json={"tree": tree, "ops": ops}).json()
    assert [s["description"] for s in edited["steps"]][0].startswith("create")
    doc = edited["tree"]
    doc["keywords"] = {"wave height": "n2", "tide gauge": "n2"}
    themes = client.get("/api/themes")
    assert themes.json()["source"] == "none" and themes.json()["empty"]
    saved = client.put(
        "/api/themes",
        json={"tree": doc, "action": "first tree"},
        headers={"If-Match": etag(themes)},
    )
    assert saved.status_code == 200 and saved.json()["written"]
    moved = client.post(
        "/api/themes/ops",
        json={
            "tree": saved.json()["tree"],
            "ops": [{"op": "set_aside", "keywords": ["tide gauge"], "reason": "x"}],
        },
    ).json()
    second = client.put(
        "/api/themes",
        json={"tree": moved["tree"], "action": "set aside"},
        headers={"If-Match": etag(saved)},
    )
    assert second.status_code == 200
    stale = client.put(
        "/api/themes",
        json={"tree": moved["tree"], "action": "again"},
        headers={"If-Match": etag(saved)},
    )
    assert stale.status_code == 412
    versions = client.get("/api/themes/versions").json()["items"]
    assert [v["id"] for v in versions][0] == "current" and len(versions) == 2
    earlier = versions[1]["id"]
    assert (
        client.get(f"/api/themes/versions/{earlier}").json()["tree"]["keywords"]["tide gauge"]
        == "n2"
    )
    restored = client.post(
        f"/api/themes/versions/{earlier}/restore", headers={"If-Match": etag(second)}
    )
    assert restored.status_code == 200 and restored.json()["action"] == f"restore {earlier}"
    assert client.get("/api/themes").json()["tree"]["keywords"]["tide gauge"] == "n2"
    invalid = client.post(
        "/api/themes/ops", json={"tree": {"depth": 9}, "ops": [{"op": "prune_empty"}]}
    )
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "invalid_tree"


def test_settings_and_the_frozen_identity(client):
    settings = client.get("/api/settings")
    assert settings.json()["identity"]["frozen"] is False and settings.json()["change_costs"] == {}
    first = client.put(
        "/api/settings",
        json={"ai": {"provider": "mistral", "model": "small"}},
        headers={"If-Match": etag(settings)},
    )
    assert first.status_code == 200 and first.json()["identity"]["ai"]["model"] == "small"
    decided = client.post(
        "/api/keywords/decisions",
        json={"decisions": [{"term": "wave", "language": "en", "decision": "exclude"}]},
        headers={"If-Match": '"none"'},
    )
    assert decided.status_code == 200
    frozen = client.get("/api/settings")
    assert frozen.json()["identity"]["frozen"] and frozen.json()["change_costs"]["domain_title"]
    refused = client.put(
        "/api/settings", json={"domain_title": "Another field"}, headers={"If-Match": etag(frozen)}
    )
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "identity_frozen" and error["next"]["action"] == "confirm"
    assert "paid for again" in error["message"]
    ok = client.put(
        "/api/settings",
        json={"domain_title": "Another field", "confirm_identity_change": True},
        headers={"If-Match": etag(frozen)},
    )
    assert ok.status_code == 200 and ok.json()["identity"]["domain_title"] == "Another field"
    merge = client.post(
        "/api/keywords/decisions",
        json={"decisions": [{"term": "wave", "language": "en", "decision": "merge"}]},
        headers={"If-Match": etag(decided)},
    )
    assert merge.status_code == 422


def test_snapshots_list_read_and_restore(client):
    params = client.get("/api/params")
    one = client.put("/api/params", json={"seed": 5}, headers={"If-Match": etag(params)})
    client.put("/api/params", json={"seed": 6}, headers={"If-Match": etag(one)})
    items = client.get("/api/snapshots?file=params.json").json()["items"]
    assert [i["id"] for i in items][0] == "current" and len(items) == 3
    assert items[1]["replaced_by"].startswith("set seed")
    first = items[-1]["id"]
    content = client.get(f"/api/snapshots/params.json/{first}").json()["content"]
    assert content["seed"] == 0
    r = client.post(
        f"/api/snapshots/params.json/{first}/restore",
        headers={"If-Match": etag(client.get("/api/params"))},
    )
    assert r.status_code == 200
    assert client.get("/api/params").json()["global"]["seed"]["value"] == 0
    assert client.get("/api/snapshots/nothing.json/current").status_code == 404
    assert (
        client.post(
            "/api/snapshots/params.json/current/restore", headers={"If-Match": '"x"'}
        ).status_code
        == 409
    )


def test_share_sources_and_empty_results(client):
    share = client.get("/api/share").json()
    assert share["available"] is True and share["empty"]["message"].startswith("no site")
    refused = client.post("/api/share/builds", json={"names": "pseudonyms"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "no_map_to_share"
    slots = client.get("/api/sources").json()["slots"]
    assert [(s["id"], s["kind"], s["files"]) for s in slots] == [
        ("collected", "collection", 0),
        ("docs", "folder", 0),
    ]
    assert (
        client.post("/api/sources/collected/files", files={"file": ("a.txt", b"x")}).status_code
        == 409
    )
    for url in ("/api/keywords", "/api/atlas", "/api/build", "/api/jobs", "/api/map/versions"):
        body = client.get(url).json()
        assert body["empty"]["next"]["action"] in ("build", "none"), url
    assert client.get("/api/themes").json()["empty"]["next"]["action"] == "build"
    assert client.get("/api/keywords/copilot/summary").status_code == 409


def test_the_about_page_and_the_manifest_name_the_build(client):
    about = client.get("/api/app/about").json()
    assert about["version"] and about["authors"] and about["licence"] == "MIT"
    assert about["version"] in about["citation"] and about["authors"][0] in about["citation"]
    app = client.get("/api/app/manifest").json()["app"]
    assert app["build"] == about["build"]  # run from a checkout: its last commit
    assert app["build"] is None or len(app["build"]["commit"]) >= 7
    assert app["platform"]  # locally, for a diagnostic


def test_preferences_are_kept_per_person_outside_the_project(client, tmp_path):
    empty = client.get("/api/me/preferences").json()
    assert empty["stored"] is False and empty["preferences"]["locale"] is None
    saved = client.put(
        "/api/me/preferences",
        json={
            "locale": "pt-BR",
            "theme": "dark",
            "dismissed_jobs": ["20260928T101200Z-7c1e2a"],
            "saved_at": 1791000000000,
            "other": {"table.density": "compact"},
        },
    )
    assert saved.status_code == 200 and saved.json()["stored"] is True
    again = client.get("/api/me/preferences").json()["preferences"]
    assert again == {
        "locale": "pt-BR",
        "theme": "dark",
        "dismissed_jobs": ["20260928T101200Z-7c1e2a"],
        "saved_at": 1791000000000,
        "other": {"table.density": "compact"},
    }
    files = list((tmp_path / "data" / "users").glob("*.json"))
    assert len(files) == 1 and "local" not in files[0].name
    assert not any((tmp_path / "p").rglob("*preferences*"))
    bad = client.put("/api/me/preferences", json={"locale": "es"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "unknown_locale"
    assert client.put("/api/me/preferences", json={"theme": "pink"}).status_code == 422
    stranger = Client(client.app, sign_in=False)
    assert stranger.get("/api/me/preferences").status_code == 401
