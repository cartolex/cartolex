# SPDX-License-Identifier: MIT
"""The theme editor's API on the S demo world, at depths 1, 2 and 3.

- ``GET /api/atlas`` (``cartolex-atlas/2``) reads only the theme files of any
  depth: the levels, every node, and usage shares per level for people,
  organisations, time windows and projected people;
- ``GET /api/themes`` gives the grouping's proposal (``themes_draft.json``)
  before anything is saved, and the saved tree after;
- a clustering-only change (new parameters, same vocabulary) waits until it is
  agreed on once: adopted or kept over;
- a tree based on another vocabulary is rebased on demand;
- the theme handoff exports the tree, and reads an answer into operations.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from _app_helpers import TOKEN, Client, etag

from cartolex.app import AppSettings, create_app
from cartolex.cli import main as cli
from cartolex.demo import generate
from cartolex.demo.project import write_project
from cartolex.lexicon.theme_tree import TOO_BROAD

pytestmark = pytest.mark.models("en", "fr")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """The S demo world as a project, built at its rule's depth (one level)."""
    root = tmp_path_factory.mktemp("themes-s") / "project"
    write_project(generate("S", 0), root).close()
    assert cli(["params", str(root), "--set", "pinned_year=2026"]) == 0
    assert cli(["build", str(root)]) == 0
    return root


@pytest.fixture(scope="module")
def depths(built, tmp_path_factory) -> dict[int, Path]:
    """The same project built at depths 1, 2 and 3."""
    out = {1: built}
    for depth in (2, 3):
        root = tmp_path_factory.mktemp(f"themes-d{depth}") / "project"
        shutil.copytree(built, root)
        assert cli(["params", str(root), "--set", f"themes.group.depth={depth}"]) == 0
        assert cli(["build", str(root)]) == 0
        out[depth] = root
    return out


@pytest.fixture()
def client_for(tmp_path):
    apps = []

    def make(root: Path) -> Client:
        app = create_app(
            AppSettings(
                project=root,
                launch_token=TOKEN,
                data_dir=tmp_path / f"app{len(apps)}",
                build_budget_mb=1e9,
                build_year=2026,
            )
        )
        apps.append(app)
        return Client(app)

    yield make
    for app in apps:
        app.state.cartolex.shutdown()


def _copy(root: Path, tmp_path: Path) -> Path:
    target = tmp_path / "copy"
    shutil.copytree(root, target)
    return target


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_the_atlas_reads_the_theme_files_of_any_depth(depths, client_for, depth):
    root = depths[depth]
    client = client_for(root)
    atlas = client.get("/api/atlas")
    bundle = atlas.json()
    assert atlas.status_code == 200 and bundle["available"]
    assert bundle["format"] == "cartolex-atlas/2" and bundle["depth"] == depth
    assert [lv["level"] for lv in bundle["levels"]] == list(range(1, depth + 1))
    applied = _json(root / "derived" / "map.layout" / "themes_applied.json")
    assert [n["id"] for n in bundle["nodes"]] == [n["id"] for n in applied["nodes"]]
    ids = {n["id"]: n for n in bundle["nodes"]}
    assert {n["level"] for n in bundle["nodes"]} == set(range(1, depth + 1))
    assert all(
        n["parent"] is None or ids[n["parent"]]["level"] == n["level"] - 1 for n in ids.values()
    )
    assert all(n["names"] and n["color"] and n["x"] is not None for n in bundle["nodes"])
    # people: usage shares on every level, summing to one where they have usage
    people = [p for p in bundle["people"] if p["person_id"]]
    assert len(people) >= 30
    for p in people:
        assert len(p["shares"]) == depth
        for level, shares in enumerate(p["shares"], start=1):
            assert all(ids[n]["level"] == level for n in shares)
            if shares:
                assert sum(shares.values()) == pytest.approx(1.0, abs=1e-4)
    # keywords sit on nodes of the tree; organisations, windows and projected people have shares
    placed = [k for k in bundle["keywords"] if k["node"]]
    assert len(placed) > 150 and all(k["node"] in ids for k in placed)
    assert all(ids[k["node"]]["level"] == k["level"] for k in placed)
    assert bundle["units"] and all(len(u["shares"]) == depth for u in bundle["units"])
    assert any(u["shares"][depth - 1] for u in bundle["units"])
    windows = bundle["trajectories"]
    assert windows and all(len(w["shares"]) == depth for w in windows)
    assert sum(1 for w in windows if w["shares"][0]) > len(windows) // 2
    assert bundle["overlays"] and all(len(o["shares"]) == depth for o in bundle["overlays"])
    assert all(o["shares"][0] for o in bundle["overlays"])
    assert client.get("/api/atlas", headers={"If-None-Match": etag(atlas)}).status_code == 304
    # the theme editor's draft is the generic proposal of the same depth
    themes = client.get("/api/themes").json()
    draft = _json(root / "derived" / "themes.group" / "themes_draft.json")
    assert themes["source"] == "draft" and themes["based_on_current"]
    assert themes["tree"]["depth"] == depth and themes["tree"]["keywords"] == draft["keywords"]
    assert themes["tree"]["based_on"] == draft["based_on"]
    usage = client.get("/api/themes/usage").json()
    # the keywords the comb set aside as too broad are used too
    assert set(usage["terms"]) == set(draft["keywords"]) | set(draft["set_aside"])
    assert usage["people"] >= 30
    assert all(people >= 1 and weight > 0 for people, weight in usage["terms"].values())


def test_a_saved_tree_is_applied_and_the_atlas_follows(depths, client_for, tmp_path):
    root = _copy(depths[2], tmp_path)
    client = client_for(root)
    themes = client.get("/api/themes")
    tree = themes.json()["tree"]
    top = next(n["id"] for n in tree["nodes"] if n["parent"] is None)
    topic = next(n["id"] for n in tree["nodes"] if n["parent"] == top)
    keyword = next(k for k, n in tree["keywords"].items() if n == topic)
    edited = client.post(
        "/api/themes/ops",
        json={
            "tree": tree,
            "ops": [
                {"op": "rename_node", "node_id": top, "names": {"en": "Coastal hazards"}},
                {"op": "move_keywords", "keywords": [keyword], "node_id": top},
                {"op": "delete_node", "node_id": "nope"},
            ],
            "lenient": True,
        },
    ).json()
    assert edited["steps"][0] == {"op": "rename_node", "description": f"rename {top}"}
    assert edited["steps"][2]["refused"] and "description" not in edited["steps"][2]
    saved = client.put(
        "/api/themes",
        json={"tree": edited["tree"], "action": "rename and move"},
        headers={"If-Match": etag(themes)},
    )
    assert saved.status_code == 200 and saved.json()["written"]
    compared = client.post(
        "/api/themes/compare", json={"before": tree, "after": saved.json()["tree"]}
    ).json()
    assert compared["counts"] == {"node_renamed": 1, "moved": 1}
    job = client.post("/api/themes/apply").json()["job"]
    assert client.wait_job(job["id"], timeout=600)["state"] == "succeeded"
    bundle = client.get("/api/atlas").json()
    assert bundle["source"] == "decisions"
    node = next(n for n in bundle["nodes"] if n["id"] == top)
    assert node["names"]["en"] == "Coastal hazards"
    placed = next(k for k in bundle["keywords"] if k["term"] == keyword)
    assert placed["node"] == top and placed["level"] == 1
    # a node merged away keeps its name in the versions list: from the version that had it
    other = next(n for n in tree["nodes"] if n["parent"] is None and n["id"] != top)
    merged = client.post(
        "/api/themes/ops",
        json={
            "tree": saved.json()["tree"],
            "ops": [{"op": "merge_nodes", "source": other["id"], "target": top}],
        },
    ).json()
    action = merged["steps"][0]["description"]
    again = client.put(
        "/api/themes",
        json={"tree": merged["tree"], "action": action},
        headers={"If-Match": etag(saved)},
    )
    assert again.status_code == 200, again.text
    latest = client.get("/api/themes/versions").json()["items"][0]
    assert latest["made_by"] == action == f"merge {other['id']} into {top}"
    assert latest["names"][other["id"]] == other["names"]
    assert other["id"] not in latest["names_after"]
    assert latest["names_after"][top]["en"] == "Coastal hazards"


def test_a_clustering_only_change_is_agreed_once(depths, client_for, tmp_path):
    root = _copy(depths[1], tmp_path)
    client = client_for(root)
    themes = client.get("/api/themes")
    saved = client.put(
        "/api/themes",
        json={"tree": themes.json()["tree"], "action": "keep the proposal"},
        headers={"If-Match": etag(themes)},
    )
    assert saved.status_code == 200
    assert client.get("/api/themes").json()["proposal"]["pending"] is False

    def regroup(top_groups: int) -> None:
        params = client.get("/api/params")
        changed = client.put(
            "/api/params",
            json={"stages": {"themes.group": {"top_groups": top_groups}}},
            headers={"If-Match": etag(params)},
        )
        assert changed.status_code == 200, changed.text
        job = client.post("/api/build", json={"scope": ["themes.group"], "dry_run": False}).json()[
            "job"
        ]
        assert client.wait_job(job["id"], timeout=600)["state"] == "succeeded"

    regroup(12)
    now = client.get("/api/themes")
    proposal = now.json()["proposal"]
    assert proposal["pending"] and proposal["same_vocabulary"] and now.json()["source"] == "saved"
    draft = client.get("/api/themes/draft").json()
    assert draft["run"] == proposal["run"]
    assert len([n for n in draft["tree"]["nodes"] if n["parent"] is None]) == 12
    diff = client.post(
        "/api/themes/compare", json={"before": now.json()["tree"], "after": draft["tree"]}
    ).json()
    assert diff["total"] > 0
    stale = client.post(
        "/api/themes/proposal",
        json={"decision": "keep", "run": "themes.group/20200101T000000Z-0000"},
        headers={"If-Match": etag(now)},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "proposal_changed"
    kept = client.post(
        "/api/themes/proposal",
        json={"decision": "keep", "run": proposal["run"]},
        headers={"If-Match": etag(now)},
    )
    assert kept.status_code == 200 and kept.json()["action"].startswith("keep the curated tree")
    after = client.get("/api/themes").json()
    assert (
        after["proposal"]["pending"] is False
        and after["tree"]["keywords"] == now.json()["tree"]["keywords"]
    )

    regroup(10)
    now = client.get("/api/themes")
    proposal = now.json()["proposal"]
    assert proposal["pending"]
    adopted = client.post(
        "/api/themes/proposal",
        json={"decision": "adopt", "run": proposal["run"]},
        headers={"If-Match": etag(now)},
    )
    assert adopted.status_code == 200
    after = client.get("/api/themes").json()
    draft = client.get("/api/themes/draft").json()["tree"]
    assert after["proposal"]["pending"] is False and after["tree"]["keywords"] == draft["keywords"]
    versions = client.get("/api/themes/versions").json()["items"]
    assert versions[0]["made_by"].startswith("adopt the grouping proposal")

    # an apply made without an answer keeps the tree over the proposal, and says so
    regroup(11)
    now = client.get("/api/themes").json()
    assert now["proposal"]["pending"]
    applied = client.post("/api/themes/apply").json()
    assert applied["kept"]["run"] == now["proposal"]["run"]
    assert applied["kept"]["action"].startswith("keep the curated tree over the proposal")
    assert client.wait_job(applied["job"]["id"], timeout=600)["state"] == "succeeded"
    after = client.get("/api/themes").json()
    assert after["proposal"]["pending"] is False
    assert after["tree"]["keywords"] == now["tree"]["keywords"]
    versions = client.get("/api/themes/versions").json()["items"]
    assert versions[0]["made_by"] == applied["kept"]["action"]
    assert versions[0]["made_by"].endswith("at an apply")


def test_a_tree_of_another_vocabulary_is_rebased_on_demand(depths, client_for, tmp_path):
    root = _copy(depths[1], tmp_path)
    client = client_for(root)
    themes = client.get("/api/themes")
    saved = client.put(
        "/api/themes",
        json={"tree": themes.json()["tree"], "action": "keep the proposal"},
        headers={"If-Match": etag(themes)},
    )
    assert saved.status_code == 200
    keywords = client.get("/api/keywords?band=kept&limit=2")
    gone = [(i["term"], i["language"]) for i in keywords.json()["items"]]
    decided = client.post(
        "/api/keywords/decisions",
        json={
            "decisions": [
                {"term": t, "language": lang, "decision": "exclude", "reason": "noise"}
                for t, lang in gone
            ]
        },
        headers={"If-Match": etag(keywords)},
    )
    assert decided.status_code == 200, decided.text
    job = client.post("/api/build", json={"scope": ["themes.group"], "dry_run": False}).json()[
        "job"
    ]
    assert client.wait_job(job["id"], timeout=600)["state"] == "succeeded"
    before = client.get("/api/themes")
    assert before.json()["based_on_current"] is False
    assert before.json()["extra_count"] >= 1
    rebased = client.post("/api/themes/rebase", headers={"If-Match": etag(before)})
    assert rebased.status_code == 200, rebased.text
    assert rebased.json()["written"] and rebased.json()["notes"][0].startswith("theme tree rebased")
    after = client.get("/api/themes").json()
    assert after["based_on_current"] and after["extra_count"] == 0
    # the grouping ran before the rebase, which placed the new keywords after it: nothing to agree on
    assert after["proposal"]["same_vocabulary"] and after["proposal"]["pending"] is False
    assert all(t not in after["tree"]["keywords"] for t, _ in gone)
    again = client.post("/api/themes/rebase", headers={"If-Match": etag(client.get("/api/themes"))})
    assert again.status_code == 200 and again.json()["written"] is False


def test_the_theme_handoff_exports_the_tree_and_reads_an_answer(depths, client_for, tmp_path):
    root = _copy(depths[2], tmp_path)
    client = client_for(root)
    tree = client.get("/api/themes").json()["tree"]
    exported = client.post("/api/themes/handoff/export", json={"top": 8}).json()
    [part] = exported["parts"]
    assert "people's names or identifiers" in exported["never"]
    text = part["files"]["tree.txt"]
    tops = [n for n in tree["nodes"] if n["parent"] is None]
    assert all(f"[{n['id']}]" in text for n in tree["nodes"])
    assert "Coastal and marine systems" in text and "Context for the assistant" in text
    names = [p["name"] for p in client.get("/api/atlas").json()["people"]]
    ids = [p["person_id"] for p in client.get("/api/atlas").json()["people"]]
    for content in part["files"].values():
        assert not any(n in content for n in names) and not any(i in content for i in ids)
    zipped = client.post("/api/themes/handoff/export.zip", json={})
    assert zipped.status_code == 200 and zipped.content[:2] == b"PK"
    first, second = tops[0]["id"], tops[1]["id"]
    topic = next(n["id"] for n in tree["nodes"] if n["parent"] == first)
    keyword = next(k for k, n in tree["keywords"].items() if n == topic)
    answer = "\n".join(
        [
            f"1 | RENAME | {first} | Coastal hazards | its keywords are floods",
            f"2 | MOVE | {keyword} | {second} | belongs there",
            f"3 | MERGE | {topic} | {first} | not the same level",
            "4 | SET ASIDE | not a keyword at all | noise",
            "Thanks!",
        ]
    )
    imported = client.post(
        "/api/themes/handoff/import", json={"bundle": part["bundle"], "answer": answer}
    )
    assert imported.status_code == 200, imported.text
    proposal = imported.json()
    assert [i["verb"] for i in proposal["items"]] == ["RENAME", "MOVE", "MERGE"]
    assert proposal["items"][2]["refused"] and proposal["applicable"] == 2
    assert [u["problem"] for u in proposal["unreadable"]] == ["unknown_keyword"]
    assert proposal["ignored"] == 1
    listed = client.get("/api/themes/handoff/proposals").json()["items"]
    assert [p["id"] for p in listed] == [proposal["id"]]
    again = client.get(f"/api/themes/handoff/proposals/{proposal['id']}").json()
    assert again["items"] == proposal["items"]
    ai = root / "decisions" / "history" / "ai"
    assert (ai / f"{proposal['id']}.txt").read_text(encoding="utf-8") == answer
    assert client.get("/api/settings").json()["identity"]["frozen"] is True
    ops = [i["op"] for i in proposal["items"] if not i["refused"]]
    applied = client.post("/api/themes/ops", json={"tree": tree, "ops": ops}).json()
    assert applied["tree"]["keywords"][keyword] == second
    bad = client.post(
        "/api/themes/handoff/import", json={"bundle": {"format": "x"}, "answer": "1 | MOVE"}
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_theme_bundle"


def test_borderline_keywords_and_suggested_places_follow_the_tree_sent(depths, client_for):
    client = client_for(depths[2])
    tree = client.get("/api/themes").json()["tree"]
    listed = client.post("/api/themes/borderline", json={"tree": tree, "limit": 5}).json()
    items = listed["items"]
    assert listed["total"] > 100 and len(items) == 5 and listed["sort"] == "margin"
    margins = [i["margin"] for i in items]
    assert margins == sorted(margins) and items[0]["other"] != items[0]["node"]
    first = items[0]["keyword"]

    # a review from a rebase's queue does not hide it; « keep here » (kept) does
    def review(state: str) -> dict:
        return client.post(
            "/api/themes/ops",
            json={"tree": tree, "ops": [{"op": "set_review", "keywords": [first], "state": state}]},
        ).json()["tree"]

    elsewhere = client.post("/api/themes/borderline", json={"tree": review("reviewed")}).json()
    assert elsewhere["total"] == listed["total"]
    kept = review("kept")
    again = client.post("/api/themes/borderline", json={"tree": kept, "limit": 500}).json()
    assert again["total"] == listed["total"] - 1
    assert first not in {i["keyword"] for i in again["items"]}
    # set aside, it gets suggested places; the best is a node holding keywords
    aside = client.post(
        "/api/themes/ops", json={"tree": kept, "ops": [{"op": "set_aside", "keywords": [first]}]}
    ).json()["tree"]
    found = client.post("/api/themes/suggestions", json={"tree": aside}).json()["suggestions"]
    assert first in found and len(found[first]) == 3  # beside the ones set aside as too broad
    assert found[first][0]["node"] in set(aside["keywords"].values())
    assert found[first][0]["score"] >= found[first][2]["score"]


def test_the_atlas_page_reads_organisations_texts_regions_and_bases(built, client_for, tmp_path):
    client = client_for(_copy(built, tmp_path))
    atlas = client.get("/api/atlas").json()
    # organisations at every level, each count saying what it counts; the people's filters
    levels = [lv["id"] for lv in atlas["organisation_levels"]]
    assert levels[:2] == ["lab", "institution"]
    orgs = {o["id"]: o for o in atlas["organisations"]}
    placed = [o for o in orgs.values() if o["x"] is not None]
    assert placed and all(o["members"] >= 1 and o["members_ever"] >= o["members"] for o in placed)
    lab = next(o for o in placed if o["level"] == "lab")
    parent = orgs[lab["parents"][0]]
    assert parent["members"] >= lab["members"]  # an institution counts the people of its labs
    assert atlas["columns"] and all(len(c["values"]) > 1 for c in atlas["columns"])
    extra = atlas["people_extra"]
    assert all(
        pid in extra for pid in (p["person_id"] for p in atlas["people"] if p["x"] is not None)
    )
    assert atlas["years"]["min"] < atlas["years"]["max"]
    # texts placed by the keywords of their title and abstract (columnar)
    texts = client.get("/api/atlas/texts").json()
    assert texts["available"] and len(texts["id"]) > 100 and texts["unplaced"] == 0
    assert sum(1 for by in texts["by"] if by == 0) > len(texts["id"]) // 2
    kws = atlas["keywords"]
    i = texts["by"].index(0)
    xs = [kws[k]["x"] for k in texts["terms"][i]]
    assert texts["x"][i] == pytest.approx(sum(xs) / len(xs), abs=1e-3)
    # the keywords a region spans: a person's, an organisation's members'
    pid = next(p["person_id"] for p in atlas["people"] if p["person_id"])
    regions = client.get("/api/atlas/regions", params={"kind": "person", "ids": pid}).json()
    assert 3 <= len(regions["keywords"][pid]) <= 40
    org = client.get("/api/atlas/regions", params={"kind": "organisation", "ids": lab["id"]}).json()
    assert len(org["keywords"][lab["id"]]) >= 3
    # another project's map as a base: its copy, then this project placed on it
    other = tmp_path / "other"
    shutil.copytree(built, other)
    bases = client.get("/api/map/bases")
    added = client.post(
        "/api/map/bases", json={"folder": str(other)}, headers={"If-Match": etag(bases)}
    )
    assert added.status_code == 200, added.text
    base_id = added.json()["added"]
    assert (tmp_path / "copy" / "sources" / "bases" / base_id / "base_map.json").is_file()
    on_base = client.get("/api/atlas", params={"base": base_id}).json()
    assert on_base["base"]["shared_keywords"] == sum(1 for k in kws if k["x"] is not None)
    assert on_base["base"]["people"] and not on_base["trajectories"]
    assert sum(1 for p in on_base["people"] if p["x"] is not None) >= 30
    refused = client.post(
        "/api/map/bases", json={"folder": str(tmp_path / "copy")}, headers={"If-Match": etag(added)}
    )
    assert refused.json()["error"]["code"] == "base_same_project"
    assert (
        client.get("/api/atlas", params={"base": "nope"}).json()["error"]["code"]
        == "base_not_found"
    )


def test_the_comb_on_the_tree_sent_suggests_too_broad_keywords_put_back(depths, client_for):
    client = client_for(depths[2])
    tree = client.get("/api/themes").json()["tree"]
    broad = sorted(k for k, v in tree["set_aside"].items() if v["reason"] == TOO_BROAD)
    assert broad, "the comb sets some keywords aside on the S world"
    # put back on their topics, the comb reads them as too broad again
    back = client.post(
        "/api/themes/ops", json={"tree": tree, "ops": [{"op": "put_back", "keywords": broad}]}
    ).json()["tree"]
    listed = client.post("/api/themes/levels", json={"tree": back, "limit": 500}).json()
    assert listed["theta"] is not None and listed["sort"] == "suggested"
    found = {i["keyword"]: i for i in listed["items"]}
    assert any(found.get(k, {}).get("to", "") is None for k in broad)
    assert all(i["to"] is None or i["share"] > 0 for i in listed["items"])
    # the copilot bundle carries the same reading
    r = client.post("/api/themes/copilot/export", json={"tree": back, "language": "en"})
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        levels = json.loads(zf.read("baseline/levels.json"))
    assert levels["theta"] == listed["theta"] and levels["items"]
