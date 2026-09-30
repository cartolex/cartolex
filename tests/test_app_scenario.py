# SPDX-License-Identifier: MIT
"""The screens' API end to end on the demo world S (synthetic, no network).

Create a project, import people (the stand-in collection answers with the demo
world's texts), build, read the state, change a parameter, curate keywords,
edit and save the themes, apply them, pin a map version, restore a snapshot,
and go through the AI handoff.
"""

from __future__ import annotations

import json

import pytest
from _app_helpers import TOKEN, Client, etag

from cartolex.app import AppSettings, DemoCollection, create_app
from cartolex.demo import generate
from cartolex.demo.model import COHORT

pytestmark = pytest.mark.models("en", "fr")


@pytest.fixture(scope="module")
def world():
    return generate("S", 0)


def test_the_screens_api_end_to_end(tmp_path, world):
    app = create_app(
        AppSettings(
            launch_token=TOKEN,
            data_dir=tmp_path / "data",
            collection=DemoCollection(world),
            build_budget_mb=1e9,
            build_year=2026,
        )
    )
    try:
        _scenario(Client(app), tmp_path, world)
    finally:
        app.state.cartolex.shutdown()


def _states(client: Client) -> dict[str, str]:
    return {s["id"]: s["state"] for s in client.get("/api/project/state").json()["stages"]}


def _scenario(client: Client, tmp_path, world) -> None:
    # ── create a project ──
    created = client.post(
        "/api/projects",
        json={
            "folder": str(tmp_path / "coast"),
            "name": "Coastal map",
            "domain_title": "Coastal and marine systems",
            "languages": ["fr", "en"],
        },
    )
    assert created.status_code == 201, created.text
    assert client.get("/api/people").json()["empty"]["next"]["action"] == "import-people"

    # ── import people: a pasted list, a mapping proposal, then the confirmation ──
    cohort = [p for p in world.people if p.role == COHORT]
    pasted = "\n".join(f"{p.last_name}, {p.first_name}" for p in cohort)
    proposal = client.post("/api/people/import", json={"text": pasted}).json()
    assert proposal["kind"] == "lines" and proposal["rows"] == len(cohort)
    assert proposal["mapping"] == {"name": "name"}
    people_version = etag(client.get("/api/people"))
    confirmed = client.post(
        f"/api/people/import/{proposal['import_id']}/confirm",
        json={"mapping": proposal["mapping"], "role": "mapped"},
        headers={"If-Match": people_version},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["added"] == len(cohort)
    listed = client.get("/api/people?limit=5&sort=name").json()
    assert listed["total"] == len(cohort) and listed["counts"]["role"] == {"mapped": len(cohort)}

    # ── collect (the stand-in answers from the demo world) ──
    plan = client.get("/api/collection/plan").json()
    assert plan["people"] == len(cohort) and plan["leaves_the_computer"]
    assert "never_texts" in {n["code"] for n in plan["never_leaves"]}
    job = client.post("/api/collection/start").json()["job"]
    done = client.wait_job(job["id"])
    assert done["state"] == "succeeded" and done["result"]["texts"] > 100
    coverage = client.get("/api/collection/coverage").json()
    assert coverage["classes"]["none"] < len(cohort)
    queue = client.get("/api/collection/identities?limit=500")
    assert queue.json()["total"] == len(cohort)
    assert all(item["candidates"] for item in queue.json()["items"])
    accepted = client.post(
        "/api/collection/identities/accept",
        json={"person_ids": [i["person_id"] for i in queue.json()["items"]]},
        headers={"If-Match": etag(queue)},
    )
    assert accepted.status_code == 200 and len(accepted.json()["accepted"]) == len(cohort)
    assert client.get("/api/collection/identities").json()["total"] == 0

    # ── build ──
    dry = client.post("/api/build", json={"dry_run": True}).json()
    assert "corpus.assemble" in dry["to_run"] and dry["estimate"]["seconds"] is not None
    job = client.post("/api/build", json={"dry_run": False}).json()["job"]
    built = client.wait_job(job["id"], timeout=600)
    assert built["state"] == "succeeded", built
    states = _states(client)
    assert (
        states.pop("keywords.triage") == "skipped" and states.pop("overlays.position") == "skipped"
    )
    assert set(states.values()) == {"up_to_date"}
    areas = {a["id"]: a["state"] for a in client.get("/api/project/state").json()["areas"]}
    assert areas["map"] == "up_to_date" and areas["share"] == "never_built"

    # ── change a parameter ──
    params = client.get("/api/params")
    body = {"seed": 0, "stages": {"map.trajectories": {"window_years": 4}}}
    changed = client.put("/api/params", json=body, headers={"If-Match": etag(params)})
    assert changed.status_code == 200, changed.text
    window = next(
        p
        for s in changed.json()["stages"]
        if s["id"] == "map.trajectories"
        for p in s["params"]
        if p["name"] == "window_years"
    )
    assert (window["value"], window["from"], window["last_run"]) == (4, "params.json", 3)
    assert window["changed_since_last_run"] is True
    state = client.get("/api/project/state").json()
    trajectories = next(s for s in state["stages"] if s["id"] == "map.trajectories")
    assert trajectories["state"] == "needs_update"
    assert "window_years" in trajectories["reasons"][0]["detail"]
    refused = client.put(
        "/api/params",
        json={"stages": {"themes.group": {"top_groups": 1}}},
        headers={"If-Match": etag(changed)},
    )
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_parameters"

    # ── curate keywords ──
    check = client.get("/api/keywords?band=check&limit=3").json()
    assert check["counts"]["check"] > 0 and len(check["items"]) == 3
    keywords = client.get("/api/keywords?limit=1")
    terms = [(i["term"], i["language"]) for i in check["items"]]
    decided = client.post(
        "/api/keywords/decisions",
        json={
            "decisions": [
                {"term": t, "language": lang, "decision": "exclude", "reason": "noise"}
                for t, lang in terms
            ]
        },
        headers={"If-Match": etag(keywords)},
    )
    assert decided.status_code == 200, decided.text
    aside = client.get("/api/keywords?band=aside&decision=exclude").json()
    assert {i["term"] for i in aside["items"]} == {t for t, _ in terms}
    assert aside["items"][0]["reason"] == "excluded: noise"
    restored = client.post(
        "/api/keywords/restore",
        json={"keywords": [{"term": terms[0][0], "language": terms[0][1]}]},
        headers={"If-Match": etag(decided)},
    )
    assert restored.status_code == 200 and restored.json()["restored"] == 1
    assert _states(client)["keywords.build"] == "needs_update"
    assert client.get("/api/settings").json()["identity"]["frozen"] is True

    # ── edit and save the themes ──
    themes = client.get("/api/themes")
    assert themes.json()["source"] == "draft" and themes.json()["based_on_current"]
    tree = themes.json()["tree"]
    first = tree["nodes"][0]["id"]
    edited = client.post(
        "/api/themes/ops",
        json={
            "tree": tree,
            "ops": [{"op": "rename_node", "node_id": first, "names": {"en": "Renamed theme"}}],
        },
    ).json()
    assert edited["steps"] == [{"op": "rename_node", "description": f"rename {first}"}]
    refused = client.post(
        "/api/themes/ops", json={"tree": tree, "ops": [{"op": "delete_node", "node_id": first}]}
    )
    assert refused.status_code == 422 and refused.json()["error"]["step"] == 0
    saved = client.put(
        "/api/themes",
        json={"tree": edited["tree"], "action": "rename the first theme"},
        headers={"If-Match": etag(themes)},
    )
    assert saved.status_code == 200 and saved.json()["written"]
    assert client.get("/api/themes").json()["source"] == "saved"

    # ── apply ──
    job = client.post("/api/themes/apply").json()["job"]
    applied = client.wait_job(job["id"], timeout=600)
    assert applied["state"] == "succeeded", applied
    assert "themes.apply" in applied["result"]["ran"] and "map.layout" in applied["result"]["ran"]
    atlas = client.get("/api/atlas")
    bundle = atlas.json()
    assert bundle["available"] and bundle["format"] == "cartolex-atlas/2"
    assert "Renamed theme" in [n["names"].get("en") for n in bundle["nodes"]]
    assert len(bundle["people"]) >= len(cohort) - 2 and all(
        p["person_id"] for p in bundle["people"]
    )
    assert bundle["keywords"] and bundle["units"] and bundle["map_version"] == "v1"
    assert client.get("/api/atlas", headers={"If-None-Match": etag(atlas)}).status_code == 304
    tracker = client.get("/api/build").json()
    assert tracker["job"]["id"] == job["id"] and all(
        s["state"] == "done" for s in tracker["stages"]
    )

    # ── pin a map version ──
    versions = client.get("/api/map/versions")
    tried = client.post(
        "/api/map/versions",
        json={"action": "try", "seed": 11},
        headers={"If-Match": etag(versions)},
    )
    assert tried.status_code == 200 and tried.json()["pinned"] == "v1"
    pinned = client.post(
        "/api/map/versions",
        json={"action": "pin", "version": "v2"},
        headers={"If-Match": etag(tried)},
    )
    assert pinned.json()["pinned"] == "v2"
    assert _states(client)["map.layout"] == "needs_update"
    stale = client.post(
        "/api/map/versions",
        json={"action": "discard", "version": "v2"},
        headers={"If-Match": etag(tried)},
    )
    assert stale.status_code == 412

    # ── restore a snapshot ──
    history = client.get("/api/snapshots?file=params.json").json()["items"]
    assert history[0]["id"] == "current" and len(history) >= 2
    before = history[-1]["id"]
    restored = client.post(
        f"/api/snapshots/params.json/{before}/restore",
        headers={"If-Match": etag(client.get("/api/params"))},
    )
    assert restored.status_code == 200, restored.text
    window = client.get("/api/params").json()
    assert all(
        p["value"] == 3
        for s in window["stages"]
        if s["id"] == "map.trajectories"
        for p in s["params"]
        if p["name"] == "window_years"
    )
    summary = client.get("/api/snapshots").json()
    assert {f["file"] for f in summary["files"] if f["versions"]} >= {
        "params.json",
        "keywords.csv",
        "themes.json",
    }
    assert next(g for g in summary["generations"] if g["stage"] == "map.layout")["previous"]

    # ── an answer to a handoff, imported by an earlier version: still read and accepted ──
    assert client.post("/api/handoff/export", json={}).status_code in (404, 405)
    rows = client.get("/api/keywords", params={"band": "check", "limit": 2}).json()["items"]
    items = [
        {"term": r["term"], "lang": r["language"], "band": "check", "reason": "", "people": 1,
         "texts": 1, "specificity": 0.5, "forms": [], "inside": []}
        for r in rows
    ]  # fmt: skip
    ai = tmp_path / "coast" / "decisions" / "history" / "ai"
    ai.mkdir(parents=True, exist_ok=True)
    stamp = "20260101T000000Z-handoff"
    (ai / f"{stamp}.bundle.json").write_text(
        json.dumps({"format": "cartolex-handoff/1", "items": items}), encoding="utf-8"
    )
    (ai / f"{stamp}.txt").write_text(
        f"Here is the list:\n1 | C | {items[0]['term']}\n2 | G | {items[1]['term']}\n",
        encoding="utf-8",
    )
    assert [p["id"] for p in client.get("/api/handoff/proposals").json()["items"]] == [stamp]
    proposal = client.get(f"/api/handoff/proposals/{stamp}").json()
    assert proposal["answered"] == 2 and proposal["read"]["ignored"] == 1
    assert [i["proposed"] for i in proposal["items"]] == ["keep", "exclude"]
    accepted = client.post(
        f"/api/handoff/proposals/{stamp}/accept",
        json={"all": True},
        headers={"If-Match": f'"{proposal["keywords_version"]}"'},
    )
    assert accepted.status_code == 200 and accepted.json()["accepted"] == 2
    kept = client.get("/api/keywords", params={"q": items[0]["term"]}).json()["items"]
    assert any(k["decision"] and k["decision"]["source"] == "ai-handoff" for k in kept)
