# SPDX-License-Identifier: MIT
"""Distances in the space of the themes, who uses a keyword, and links between screens,
on the S demo world.

- ``GET /api/atlas/neighbours``: the nearest people (organisations of the same level) by
  the cosine of the space's vectors, the person left out, the nearest first;
- ``GET /api/atlas/keyword-people``: the people who use a keyword, ranked by the share of
  their keyword use it holds;
- ``GET /api/atlas/compare``: two people or organisations side by side;
- the exports of distances (a job): the nearest of each, the full matrix written by blocks
  (``.npz`` when large, confirmed above its threshold), pseudonyms when asked;
- ``GET /api/keywords?term=``: a keyword of the vocabulary with the candidates merged into
  it, in every band.
"""

from __future__ import annotations

import csv
import io
import shutil
from pathlib import Path

import numpy as np
import pytest
from _app_helpers import TOKEN, Client, etag

from cartolex.app import AppSettings, create_app
from cartolex.cli import main as cli
from cartolex.demo import generate
from cartolex.demo.project import write_project

pytestmark = pytest.mark.models("en", "fr")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("space-s") / "project"
    write_project(generate("S", 0), root).close()
    assert cli(["params", str(root), "--set", "pinned_year=2026"]) == 0
    assert cli(["build", str(root)]) == 0
    return root


@pytest.fixture()
def client(built, tmp_path):
    root = tmp_path / "copy"
    shutil.copytree(built, root)
    app = create_app(
        AppSettings(project=root, launch_token=TOKEN, data_dir=tmp_path / "app",
                    build_budget_mb=1e9, build_year=2026)  # fmt: skip
    )
    yield Client(app)
    app.state.cartolex.shutdown()


def _space(root: Path) -> tuple[list[str], np.ndarray]:
    """The space's people (engine ids) and their vectors, read the way the engine wrote them."""
    with open(root / "derived" / "themes.space" / "pca_individuals.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    z = np.array([[float(v) for k, v in r.items() if k.startswith("PC")] for r in rows])
    return [r["id"] for r in rows], z / np.linalg.norm(z, axis=1, keepdims=True)


def test_the_nearest_and_the_comparison_follow_the_space(client):
    atlas = client.get("/api/atlas").json()
    root = Path(client.app.state.cartolex.settings.project)
    rids, z = _space(root)
    with open(root / "derived" / "map.layout" / "umap_individuals.csv", encoding="utf-8") as fh:
        order = [r["id"] for r in csv.DictReader(fh)]
    person = {rid: atlas["people"][k]["person_id"] for k, rid in enumerate(order)}
    me = atlas["people"][0]["person_id"]
    near = client.get("/api/atlas/neighbours", params={"kind": "person", "id": me, "k": 5}).json()
    sims = [i["similarity"] for i in near["items"]]
    assert len(sims) == 5 and sims == sorted(sims, reverse=True)
    assert me not in [i["id"] for i in near["items"]]
    # the same as a cosine computed apart from the engine's table
    row = [person[r] for r in rids].index(me)
    brute = z @ z[row]
    brute[row] = -np.inf
    expected = [person[rids[j]] for j in np.argsort(-brute)[:5]]
    assert [i["id"] for i in near["items"]] == expected
    assert sims[0] == pytest.approx(float(np.sort(brute)[-1]), abs=1e-3)
    # an organisation's nearest are organisations of its level
    org = next(o for o in atlas["organisations"] if o["x"] is not None and o["level"] == "lab")
    found = client.get("/api/atlas/neighbours", params={"kind": "organisation", "id": org["id"]})
    levels = {o["id"]: o["level"] for o in atlas["organisations"]}
    assert found.json()["items"] and {levels[i["id"]] for i in found.json()["items"]} == {"lab"}
    missing = client.get("/api/atlas/neighbours", params={"kind": "person", "id": "nobody"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "atlas_item_not_found"
    # two side by side: the same whichever comes first; one with itself shares everything
    other = near["items"][0]["id"]
    ab = client.get("/api/atlas/compare", params={"a": f"person:{me}", "b": f"person:{other}"})
    ba = client.get("/api/atlas/compare", params={"a": f"person:{other}", "b": f"person:{me}"})
    ab, ba = ab.json(), ba.json()
    assert ab["space"] == pytest.approx(sims[0], abs=1e-3) == ba["space"]
    for key in ("cosine", "jaccard", "common"):
        assert ab["keywords"][key] == ba["keywords"][key]
    assert ab["themes"]["overlap"] == pytest.approx(ba["themes"]["overlap"])
    same = client.get("/api/atlas/compare", params={"a": f"person:{me}", "b": f"person:{me}"})
    same = same.json()
    assert same["keywords"]["jaccard"] == 1 and same["themes"]["overlap"] == pytest.approx(1, 1e-3)
    assert same["texts"]["shared"] >= 1 and same["texts"]["items"][0]["title"]
    mixed = client.get(
        "/api/atlas/compare", params={"a": f"organisation:{org['id']}", "b": f"person:{me}"}
    )
    assert mixed.status_code == 200 and mixed.json()["a"]["name"] == org["name"]


def test_the_people_using_a_keyword_are_ranked_by_its_share_of_their_use(client):
    atlas = client.get("/api/atlas").json()
    kw = max(atlas["keywords"], key=lambda k: k["weight"] or 0)["term"]
    got = client.get("/api/atlas/keyword-people", params={"term": kw, "limit": 4}).json()
    assert got["count"] >= len(got["items"]) and len(got["items"]) == min(4, got["count"])
    shares = [i["share"] for i in got["items"]]
    assert shares == sorted(shares, reverse=True) and 0 < shares[0] <= 1
    # the places on the map name the same people
    every = client.get("/api/atlas/keyword-people", params={"term": kw, "limit": 500}).json()
    on_map = {atlas["people"][k]["person_id"] for k in every["at"]}
    assert on_map == {i["id"] for i in every["items"]}
    # each share is the keyword's part of that person's use (the keywords stage's table)
    root = Path(client.app.state.cartolex.settings.project)
    table = root / "derived" / "keywords.build" / "keywords_by_researcher_restricted.csv"
    first = got["items"][0]
    who = next(p for p in atlas["people"] if p["person_id"] == first["id"])
    with open(table, encoding="utf-8") as fh:
        mine = [r for r in csv.DictReader(fh) if f"{r['first_name']} {r['last_name']}" == who["name"]]
    total = sum(float(r["score_tf"]) for r in mine)
    part = sum(float(r["score_tf"]) for r in mine if r["term"].lower() == kw.lower())
    assert first["share"] == pytest.approx(part / total, abs=1e-3)
    unknown = client.get("/api/atlas/keyword-people", params={"term": "no such keyword"})
    assert unknown.status_code == 404


def test_distances_are_exported_by_a_job(client, monkeypatch):
    import cartolex.app.distance_exports as exports

    atlas = client.get("/api/atlas").json()
    people = sum(1 for p in atlas["people"] if p["person_id"] and p["x"] is not None)
    asked = client.post("/api/share/exports", json={"kind": "neighbours", "k": 3})
    assert asked.json()["error"]["code"] == "export_names_question"
    plan = client.post("/api/share/exports", json={"kind": "neighbours", "k": 3, "plan": True})
    assert plan.json()["plan"]["count"] == people and plan.json()["plan"]["format"] == "csv"

    def written(body: dict) -> bytes:
        started = client.post("/api/share/exports", json=body)
        assert started.status_code == 202, started.text
        job = client.wait_job(started.json()["job"]["id"])
        assert job["state"] == "succeeded", job
        return client.get(f"/api/share/exports/{job['result']['name']}").content

    # the nearest of each, under pseudonyms: no name, no project id
    text = written({"kind": "neighbours", "k": 3, "names": "pseudonyms"}).decode()
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == people * 3 and set(rows[0]) == {"source", "target", "rank", "similarity"}
    assert all(r["source"].startswith("s") for r in rows)
    assert not any(p["person_id"] in text or p["name"] in text for p in atlas["people"])
    # the full matrix of the people the map's filters keep, by blocks, as .npz when large
    monkeypatch.setattr(exports, "CSV_CELLS", 0)
    monkeypatch.setattr(exports, "BLOCK_BYTES", 1)  # the smallest blocks
    some = [p["person_id"] for p in atlas["people"] if p["person_id"]][:12]
    data = written({"kind": "similarity", "names": "names", "ids": some})
    with np.load(io.BytesIO(data)) as npz:
        sim, ids = npz["similarity"], list(npz["ids"])
    assert sim.shape == (12, 12) and sorted(ids) == sorted(some)
    assert np.allclose(sim, sim.T, atol=1e-5) and np.allclose(np.diag(sim), 1, atol=1e-4)
    a = client.get("/api/atlas/neighbours", params={"kind": "person", "id": ids[0], "k": 40})
    full = {i["id"]: i["similarity"] for i in a.json()["items"]}
    assert sim[0, 1] == pytest.approx(full[ids[1]], abs=1e-3)
    # above its threshold, a matrix waits for a confirmation that names its size
    monkeypatch.setattr(exports, "CONFIRM_CELLS", 10)
    refused = client.post("/api/share/exports", json={"kind": "similarity", "names": "names"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "export_size_confirm"
    assert refused.json()["error"]["params"]["cells"] == people * people
    # organisations' vectors, always named
    text = written({"kind": "vectors", "of": "organisation"}).decode()
    assert text.startswith("id,name,v")


def test_a_keyword_is_found_with_the_candidates_merged_into_it(client):
    page = client.get("/api/keywords", params={"band": "kept", "limit": 50})
    rows = page.json()["items"]
    target, other = rows[0], next(r for r in rows[1:] if r["language"] != rows[0]["language"])
    merged = client.post(
        "/api/keywords/decisions",
        json={"decisions": [{"term": other["term"], "language": other["language"],
                             "decision": "merge", "target": target["term"]}]},  # fmt: skip
        headers={"If-Match": etag(page)},
    )
    assert merged.status_code == 200, merged.text
    found = client.get("/api/keywords", params={"term": target["term"].upper()}).json()
    terms = {(i["term"], i["band"]) for i in found["items"]}
    assert (target["term"], "kept") in terms and (other["term"], "aside") in terms
    assert found["matched_bands"] == {"kept": 1, "aside": 1}
    # a substring is not the keyword
    assert not client.get("/api/keywords", params={"term": target["term"][:-1]}).json()["total"]
