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
  it, in every band;
- ``GET /api/atlas/coauthors``: the people who signed a work with a person, with the works
  together, a projected one never named, a merged person counted as the one they are
  merged into; the organisations an organisation writes with.
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
        AppSettings(
            project=root,
            launch_token=TOKEN,
            data_dir=tmp_path / "app",
            build_budget_mb=1e9,
            build_year=2026,
        )  # fmt: skip
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


def test_the_distances_pane_reads_the_vectors_and_links_in_the_bundles_order(client):
    """``GET /api/atlas/vectors`` (int8 rows, the space's cosines) and ``GET /api/atlas/links``
    (the co-authors as sparse lists), both over the bundle's order and kept by ETag."""
    import base64

    atlas = client.get("/api/atlas").json()
    root = Path(client.app.state.cartolex.settings.project)
    rids, z = _space(root)
    with open(root / "derived" / "map.layout" / "umap_individuals.csv", encoding="utf-8") as fh:
        order = [r["id"] for r in csv.DictReader(fh)]
    r = client.get("/api/atlas/vectors", params={"kind": "person"})
    body = r.json()
    assert body["count"] == len(atlas["people"]) and body["dim"] == z.shape[1]
    q = np.frombuffer(base64.b64decode(body["values"]), np.int8).reshape(body["count"], -1)
    row = {rid: k for k, rid in enumerate(rids)}
    a, b = 0, 1  # the bundle's first two people are the layout's first two
    unit = q[[a, b]].astype(float) / np.linalg.norm(q[[a, b]].astype(float), axis=1, keepdims=True)
    assert abs(unit[0] @ unit[1] - z[row[order[0]]] @ z[row[order[1]]]) < 0.02
    again = client.get(
        "/api/atlas/vectors", params={"kind": "person"}, headers={"If-None-Match": etag(r)}
    )
    assert again.status_code == 304
    links = client.get("/api/atlas/links", params={"kind": "person"}).json()
    assert len(links["ptr"]) == len(atlas["people"]) + 1
    me = next(i for i in range(len(atlas["people"])) if links["ptr"][i + 1] > links["ptr"][i])
    pid = atlas["people"][me]["person_id"]
    co = client.get("/api/atlas/coauthors", params={"kind": "person", "id": pid}).json()
    mine = {
        atlas["people"][links["nbr"][k]]["person_id"]: links["cnt"][k]
        for k in range(links["ptr"][me], links["ptr"][me + 1])
    }
    assert mine == {i["id"]: i["texts"] for i in co["items"] if i["id"] in mine}
    orgs = client.get("/api/atlas/links", params={"kind": "organisation"}).json()
    assert len(orgs["ptr"]) == len(atlas["organisations"]) + 1


def test_the_measures_the_browser_cannot_compute_are_measured_within_bounds(client, monkeypatch):
    """``POST /api/atlas/similarity`` and ``POST /api/atlas/similar-pairs``: the app's measures
    (here the shared vocabulary) for the Distances pane, the same as the nearest, never past
    their bounds; the pairs that never wrote together exclude every co-author pair."""
    import base64

    from cartolex.app.routes import distances

    atlas = client.get("/api/atlas").json()
    me = atlas["people"][0]["person_id"]
    body = {"measure": "keywords", "a": {"kind": "person", "ids": [me]}, "b": {"kind": "person"}}
    got = client.post("/api/atlas/similarity", json=body).json()
    values = np.frombuffer(base64.b64decode(got["values"]), "<f4")
    assert got["rows"] == 1 and got["cols"] == len(atlas["people"]) == len(values)
    near = client.get(
        "/api/atlas/neighbours", params={"kind": "person", "id": me, "k": 3, "measure": "keywords"}
    ).json()["items"]
    at = {p["person_id"]: k for k, p in enumerate(atlas["people"])}
    for item in near:
        assert abs(values[at[item["id"]]] - item["similarity"]) < 1e-3
    # themes against themes, by their people's keyword use
    tops = [n["id"] for n in atlas["nodes"] if n["level"] == 1]
    themes = client.post(
        "/api/atlas/similarity",
        json={
            "measure": "jaccard",
            "a": {"kind": "theme", "ids": tops},
            "b": {"kind": "theme", "ids": tops},
        },
    ).json()
    grid = np.frombuffer(base64.b64decode(themes["values"]), "<f4").reshape(len(tops), -1)
    assert np.allclose(np.diag(grid), 1, atol=1e-4)
    # the pairs: none of them co-authors, the most alike first
    ids = [p["person_id"] for p in atlas["people"] if p["x"] is not None]
    pairs = client.post(
        "/api/atlas/similar-pairs",
        json={"measure": "keywords", "kind": "person", "ids": ids, "limit": 20},
    ).json()["items"]
    sims = [p["similarity"] for p in pairs]
    assert pairs and sims == sorted(sims, reverse=True)
    for p in pairs[:5]:
        co = client.get(
            "/api/atlas/coauthors", params={"kind": "person", "id": p["a"], "limit": 500}
        ).json()
        assert p["b"] not in {i["id"] for i in co["items"]}
    together = client.post(
        "/api/atlas/similar-pairs",
        json={"measure": "jaccard", "kind": "person", "ids": ids, "mode": "together", "limit": 20},
    ).json()["items"]
    assert together and all(p["texts"] > 0 for p in together)
    assert [p["similarity"] for p in together] == sorted(p["similarity"] for p in together)
    # the bounds
    monkeypatch.setattr(distances, "MAX_PAIRS_SCOPE", 5)
    refused = client.post(
        "/api/atlas/similar-pairs", json={"measure": "keywords", "kind": "person", "ids": ids}
    )
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_parameters"
    monkeypatch.setattr(distances, "MAX_CELLS", 10)
    assert client.post("/api/atlas/similarity", json=body).status_code == 422


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
        mine = [
            r for r in csv.DictReader(fh) if f"{r['first_name']} {r['last_name']}" == who["name"]
        ]
    total = sum(float(r["score_tf"]) for r in mine)
    part = sum(float(r["score_tf"]) for r in mine if r["term"].lower() == kw.lower())
    assert first["share"] == pytest.approx(part / total, abs=1e-3)
    unknown = client.get("/api/atlas/keyword-people", params={"term": "no such keyword"})
    assert unknown.status_code == 200 and unknown.json()["known"] is False
    assert unknown.json()["count"] == 0 and got["known"] is True


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
    with np.load(io.BytesIO(data), allow_pickle=False) as npz:
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


def test_the_chosen_similarity_drives_the_nearest_compare_and_exports(client):
    atlas = client.get("/api/atlas").json()
    me = atlas["people"][0]["person_id"]
    near = client.get("/api/atlas/neighbours", params={"kind": "person", "id": me, "k": 3})
    assert near.json()["measure"] == "space"  # the default
    params = client.get("/api/params")
    assert params.json()["global"]["similarity"]["value"] == "space"
    version = etag(params)
    for measure, part in (("keywords", "cosine"), ("jaccard", "jaccard"), ("themes", "overlap")):
        chosen = client.put("/api/params/similarity", json={"measure": measure},
                            headers={"If-Match": version})  # fmt: skip
        assert chosen.status_code == 200, chosen.text
        version = etag(chosen)
        assert chosen.json()["global"]["similarity"]["value"] == measure
        # the nearest by that measure agree with the comparison of the two
        near = client.get("/api/atlas/neighbours", params={"kind": "person", "id": me, "k": 3})
        near = near.json()
        assert near["measure"] == measure
        sims = [i["similarity"] for i in near["items"]]
        assert sims == sorted(sims, reverse=True)
        best = near["items"][0]
        both = client.get(
            "/api/atlas/compare", params={"a": f"person:{me}", "b": f"person:{best['id']}"}
        )
        both = both.json()
        area = "themes" if measure == "themes" else "keywords"
        assert both["measure"] == measure and both["similarity"] == both[area][part]
        assert best["similarity"] == pytest.approx(both[area][part], abs=2e-3)
    # an organisation's nearest by the same measure
    org = next(o for o in atlas["organisations"] if o["x"] is not None and o["level"] == "lab")
    found = client.get("/api/atlas/neighbours", params={"kind": "organisation", "id": org["id"]})
    assert found.json()["measure"] == "themes" and found.json()["items"]
    # chosen, it is kept in params.json and needs no rebuild
    root = Path(client.app.state.cartolex.settings.project)
    assert '"similarity": "themes"' in (root / "decisions" / "params.json").read_text()
    states = client.get("/api/project/state").json()
    assert not [s for s in states["stages"] if s["state"] == "needs_update"]
    # the exports follow it, and say so beside the file
    started = client.post(
        "/api/share/exports", json={"kind": "neighbours", "k": 2, "names": "names"}
    )
    assert started.json()["plan"]["measure"] == "themes"
    job = client.wait_job(started.json()["job"]["id"])
    name = job["result"]["name"]
    meta = client.get(f"/api/share/exports/{name.rsplit('.', 1)[0]}.meta.json").json()
    assert meta["measure"] == "themes" and meta["kind"] == "neighbours" and meta["file"] == name
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/share/exports/{name}").text)))
    mine = [r for r in rows if r["source"] == me]
    pair = client.get(
        "/api/atlas/compare", params={"a": f"person:{me}", "b": f"person:{mine[0]['target']}"}
    )
    assert float(mine[0]["similarity"]) == pytest.approx(pair.json()["themes"]["overlap"], abs=2e-3)
    stale = client.put(
        "/api/params/similarity", json={"measure": "space"}, headers={"If-Match": '"old"'}
    )
    assert stale.status_code == 412


def test_the_texts_of_a_focus_are_its_own_from_every_text(client):
    import pyarrow.parquet as pq

    root = Path(client.app.state.cartolex.settings.project)
    authors = pq.read_table(root / "sources" / "tables" / "authorships.parquet").to_pylist()
    texts_of: dict[str, set[str]] = {}
    for a in authors:
        texts_of.setdefault(a["person_id"], set()).add(a["text_id"])
    atlas = client.get("/api/atlas").json()
    me = max((p["person_id"] for p in atlas["people"] if p["person_id"]),
             key=lambda p: len(texts_of.get(p, ())))  # fmt: skip
    got = client.get("/api/atlas/texts", params={"focus": f"person:{me}"}).json()
    assert got["focus"] == f"person:{me}" and got["total"] == len(texts_of[me])
    assert set(got["id"]) <= texts_of[me] and len(got["id"]) + got["unplaced"] == got["total"]
    assert all(me in people for people in got["people"])
    # with the network: their co-authors' texts too
    wide = client.get("/api/atlas/texts", params={"focus": f"person:{me}", "net": 1}).json()
    rings = client.get("/api/atlas/coauthors", params={"kind": "person", "id": me}).json()
    every = set(texts_of[me]).union(*(texts_of.get(i["id"], set()) for i in rings["items"]))
    assert wide["total"] == len(every) > got["total"]
    # a sample, the same each time, when there are more than asked
    some = client.get("/api/atlas/texts", params={"focus": f"person:{me}", "net": 1, "limit": 3})
    again = client.get("/api/atlas/texts", params={"focus": f"person:{me}", "net": 1, "limit": 3})
    assert some.json()["sampled"] and some.json()["id"] == again.json()["id"]
    # an organisation's: its members' on the map
    extras = atlas["people_extra"]
    org = next(o for o in atlas["organisations"] if o["x"] is not None and o["level"] == "lab")
    members = {p for p, e in extras.items() if org["id"] in (e.get("orgs") or [])}
    theirs = client.get("/api/atlas/texts", params={"focus": f"organisation:{org['id']}"}).json()
    assert members and theirs["total"] == len(
        set().union(*(texts_of.get(p, set()) for p in members))
    )
    nobody = client.get("/api/atlas/texts", params={"focus": "person:nobody"})
    assert nobody.status_code in (404, 422) and nobody.json()["error"]["code"] == "unknown_people"


def test_a_keyword_is_found_with_the_candidates_merged_into_it(client):
    page = client.get("/api/keywords", params={"band": "kept", "limit": 50})
    rows = page.json()["items"]
    target, other = rows[0], next(r for r in rows[1:] if r["language"] != rows[0]["language"])
    merged = client.post(
        "/api/keywords/decisions",
        json={
            "decisions": [
                {
                    "term": other["term"],
                    "language": other["language"],
                    "decision": "merge",
                    "target": target["term"],
                }
            ]
        },  # fmt: skip
        headers={"If-Match": etag(page)},
    )
    assert merged.status_code == 200, merged.text
    found = client.get("/api/keywords", params={"term": target["term"].upper()}).json()
    terms = {(i["term"], i["band"]) for i in found["items"]}
    assert (target["term"], "kept") in terms and (other["term"], "aside") in terms
    assert found["matched_bands"] == {"kept": 1, "aside": 1}
    # a substring is not the keyword
    assert not client.get("/api/keywords", params={"term": target["term"][:-1]}).json()["total"]


def _together(root: Path) -> dict[str, dict[str, int]]:
    """Each person → their co-authors and the works together, from the authorships table."""
    import pyarrow.parquet as pq

    rows = pq.read_table(root / "sources" / "tables" / "authorships.parquet").to_pylist()
    by_text: dict[str, set[str]] = {}
    for r in rows:
        by_text.setdefault(r["text_id"], set()).add(r["person_id"])
    out: dict[str, dict[str, int]] = {}
    for people in by_text.values():
        for a in people:
            for b in people - {a}:
                out.setdefault(a, {})[b] = out.get(a, {}).get(b, 0) + 1
    return out


def test_coauthors_are_the_people_who_signed_a_work_together(client):
    root = Path(client.app.state.cartolex.settings.project)
    together = _together(root)
    me = max(together, key=lambda p: len(together[p]))
    got = client.get("/api/atlas/coauthors", params={"kind": "person", "id": me, "limit": 500})
    got = got.json()
    assert {i["id"]: i["texts"] for i in got["items"]} == together[me]
    assert got["count"] == len(together[me]) and got["max_authors"] == 25
    counts = [i["texts"] for i in got["items"]]
    assert counts == sorted(counts, reverse=True)
    assert {i for i, _ in got["lines"]} == {i["id"] for i in got["items"] if i["place"]}
    # paged on the server
    page = client.get(
        "/api/atlas/coauthors", params={"kind": "person", "id": me, "offset": 1, "limit": 2}
    ).json()
    assert [i["id"] for i in page["items"]] == [i["id"] for i in got["items"][1:3]]
    # a projected co-author is drawn apart and never named
    atlas = client.get("/api/atlas").json()
    projected = {o["person_id"] for o in atlas["overlays"]}
    them = next(p for p in together if p in projected)
    friend = next(iter(together[them]))
    seen = client.get("/api/atlas/coauthors", params={"kind": "person", "id": friend}).json()
    entry = next(i for i in seen["items"] if i["id"] == them)
    assert entry["name"] is None and entry["place"] == "projected" and entry["mapped"] is False
    # the second and third rings: partners of partners, through the ring before
    three = client.get("/api/atlas/coauthors", params={"kind": "person", "id": me, "circle": 3})
    second, third = three.json()["second"], three.json()["third"]
    first = set(together[me])
    for item in second["items"]:
        assert item["id"] not in first and item["id"] != me
        assert item["paths"] == len([q for q in first if item["id"] in together[q]])
        assert set(item["via"]) <= first
    ring2 = {i["id"] for i in second["items"]}
    assert second["count"] == len(ring2)  # the S world's rings fit in a page
    for item in third["items"]:
        assert item["id"] not in first | ring2 | {me} and set(item["via"]) <= ring2
    # a person merged into another counts as that person
    other = got["items"][-1]["id"]
    people = client.get("/api/people")
    merged = client.post(
        "/api/people/merge",
        json={"target": me, "sources": [other]},
        headers={"If-Match": etag(people)},
    )
    assert merged.status_code == 200, merged.text
    after = client.get("/api/atlas/coauthors", params={"kind": "person", "id": me, "limit": 500})
    ids = {i["id"] for i in after.json()["items"]}
    assert other not in ids and (set(together[other]) - {me}) <= ids
    # organisations: those of the same level, never itself
    org = next(o for o in atlas["organisations"] if o["x"] is not None and o["level"] == "lab")
    orgs = client.get("/api/atlas/coauthors", params={"kind": "organisation", "id": org["id"]})
    levels = {o["id"]: o["level"] for o in atlas["organisations"]}
    assert orgs.json()["level"] == "lab" and org["id"] not in {
        i["id"] for i in orgs.json()["items"]
    }
    assert {levels[i["id"]] for i in orgs.json()["items"]} <= {"lab"}
    ring = client.get(
        "/api/atlas/coauthors", params={"kind": "organisation", "id": org["id"], "circle": 3}
    ).json()
    assert {"second", "third"} <= set(ring) and org["id"] not in {
        i["id"] for i in ring["second"]["items"] + ring["third"]["items"]
    }
    missing = client.get("/api/atlas/coauthors", params={"kind": "person", "id": "nobody"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "unknown_people"
