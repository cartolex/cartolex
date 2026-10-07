# SPDX-License-Identifier: MIT
"""Who is who: a merge keeps every text and can be undone; pairs judged are remembered."""

from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import pytest
from _app_helpers import TOKEN, Client, etag

from cartolex.app import AppSettings, create_app
from cartolex.lexicon.corpus_store import index_rows
from cartolex.project import Project
from cartolex.project.corpus import assemble_corpus
from cartolex.project.identity import MergeRefused, merge_changes, merge_roots, unmerge_changes
from cartolex.project.models import Slot
from cartolex.project.tables import SOURCE_SCHEMAS, decision_csv_bytes, write_source_table

AT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _table(project: Project, name: str, rows: list[dict]) -> None:
    schema = SOURCE_SCHEMAS[name]
    write_source_table(
        project.layout.table(name),
        name,
        pa.table({f.name: [r.get(f.name) for r in rows] for f in schema}, schema=schema),
    )


def _project(root, people: list[tuple], texts: list[tuple]) -> Project:
    """A small collected project: *people* (id, last, first, orcid), *texts* (id, year,
    [(person, position)])."""
    project = Project.init(root, name="Who", domain_title="Coasts",
                           slots=(Slot(id="c", kind="collection"),))  # fmt: skip
    _table(project, "people", [{"person_id": p, "last_name": last, "first_name": first,
                                "orcid": orcid, "ids": [], "source": "import", "columns": [],
                                "aliases": [], "retrieved_at": AT}
                               for p, last, first, orcid in people])  # fmt: skip
    _table(project, "texts", [{"text_id": t, "slot": "c", "position": i, "year": y, "ids": [],
                               "n_authors": len(a), "retrieved_at": AT, "doc_type": "article",
                               "title": f"Tides and sediments of bay number {t}",
                               "source": "openalex"}
                              for i, (t, y, a) in enumerate(texts)])  # fmt: skip
    _table(project, "text_parts", [{"text_id": t, "part": "title", "language": "en",
                                    "provider": "openalex", "format": "plain",
                                    "content": f"Tides and sediments of bay number {t}",
                                    "retrieved_at": AT} for t, _, _ in texts])  # fmt: skip
    _table(project, "authorships", sorted(
        ({"text_id": t, "person_id": p, "position": pos, "orgs": []}
         for t, _, a in texts for p, pos in a),
        key=lambda r: (r["text_id"], r["person_id"])))  # fmt: skip
    return project


def _decide(project: Project, rows: list[dict]) -> None:
    full = [{"role": "mapped", "identity": "confirmed", **r} for r in rows]
    project.layout.people_csv.write_bytes(decision_csv_bytes("people", full))


PEOPLE = [
    ("p1", "Tavelin", "Ada", "0000-0001-0000-0001"),
    ("p2", "Tavelin", "A.", None),
    ("p3", "Morvan", "Lise", None),
]
TEXTS = [
    ("t1", 2019, [("p1", 1)]),
    ("t2", 2020, [("p2", 1), ("p3", 2)]),
    ("t3", 2021, [("p1", 1), ("p2", 1)]),  # one author found twice: read once
]


def test_a_merged_row_brings_its_texts_and_an_unmerge_gives_them_back(tmp_path):
    project = _project(tmp_path / "p", PEOPLE, TEXTS)
    _decide(project, [{"person_id": "p1"}, {"person_id": "p2", "merged_into": "p1"},
                      {"person_id": "p3"}])  # fmt: skip
    assemble_corpus(project.layout, project.config, tmp_path / "out")
    read = sorted((r["last_name"], r["first_name"], r["text_id"])
                  for r in index_rows(tmp_path / "out" / "c" / "index.csv"))  # fmt: skip
    ada = [r for r in read if r[1] == "Ada"]
    assert [r[2] for r in ada] == ["t1", "t2", "t3"]  # the merged row's texts, t3 once
    assert not any(r[1] == "A." for r in read)  # a merged row is never read on its own

    rows = {"p1": {"person_id": "p1", "merged_into": ""},
            "p2": {"person_id": "p2", "merged_into": "p1", "records": "openalex:A2"}}  # fmt: skip
    assert unmerge_changes(rows, ["p1"]) == {"p2": {"merged_into": ""}}
    _decide(project, [{"person_id": "p1"}, {"person_id": "p2"}, {"person_id": "p3"}])
    assemble_corpus(project.layout, project.config, tmp_path / "again")
    read = [r["first_name"] for r in index_rows(tmp_path / "again" / "c" / "index.csv")]
    assert read.count("A.") == 2
    project.close()


def test_only_projects_with_merges_record_them_as_an_input(tmp_path):
    from cartolex.build.fingerprints import input_files
    from cartolex.build.stages import STAGES

    project = _project(tmp_path / "p", PEOPLE, TEXTS)
    stage = STAGES["corpus.assemble"]
    _decide(project, [{"person_id": p} for p, *_ in PEOPLE])
    assert not any(i.path.endswith("#merges") for i in input_files(project, stage))
    _decide(project, [{"person_id": "p1"}, {"person_id": "p2", "merged_into": "p1"},
                      {"person_id": "p3"}])  # fmt: skip
    merges = [i for i in input_files(project, stage) if i.path.endswith("#merges")]
    assert len(merges) == 1 and merges[0].fingerprint().startswith("sha256:")
    project.close()


def test_two_orcids_block_a_merge_unless_overridden():
    rows = {p: {"person_id": p, "merged_into": "", "records": ""} for p in ("a", "b", "c")}
    rows["c"]["merged_into"] = "b"
    orcids = {"a": "0000-0001-0000-0001", "b": None, "c": "0000-0001-0000-0002"}
    with pytest.raises(MergeRefused) as refused:  # b stands for c, who has another iD
        merge_changes(rows, "a", ["b"], orcids)
    assert refused.value.code == "merge_orcid_conflict"
    changes = merge_changes(rows, "a", ["b"], orcids, override=True)
    assert changes == {"b": {"merged_into": "a"}, "c": {"merged_into": "a"}}
    assert merge_roots({**rows, "a": rows["a"], "b": {**rows["b"], "merged_into": "a"}}) == {
        "b": "a", "c": "a"}  # fmt: skip


def test_merging_above_a_score_never_joins_two_people_said_apart_nor_two_orcids():
    from cartolex.collect.duplicates import DuplicatePair, PersonFacts, clear_groups

    facts = {x: PersonFacts(x, "Doe", "J.", [("Doe", "J.")]) for x in ("p1", "p2", "p3", "p4")}
    facts["p4"].orcids = {"0000-0000-0000-0004"}
    facts["p3"].orcids = {"0000-0000-0000-0003"}

    def pair(a, b, score, conflict=False):
        return DuplicatePair(a, b, 0.0, score, [], clear=False, conflict=conflict)

    pairs = [pair("p1", "p2", 0.9), pair("p2", "p3", 0.6), pair("p3", "p4", 0.95, True)]
    groups = clear_groups(pairs, facts, min_score=0.5)
    assert [sorted([g["keep"], *g["merge"]]) for g in groups] == [["p1", "p2", "p3"]]
    assert clear_groups(pairs, facts, min_score=0.7)[0]["merge"] in (["p2"], ["p1"])
    assert clear_groups(pairs, facts) == []  # none is clear
    # p1 and p3 said to be two people: the chain through p2 does not join them; the
    # likelier pair still joins its two people
    apart = clear_groups(pairs, facts, {("p1", "p3")}, min_score=0.5)
    assert [sorted([g["keep"], *g["merge"]]) for g in apart] == [["p1", "p2"]]


def test_merge_and_unmerge_through_the_api(tmp_path):
    project = _project(tmp_path / "p", PEOPLE, TEXTS)
    _decide(project, [{"person_id": p} for p, *_ in PEOPLE])
    project.close()
    app = create_app(AppSettings(project=tmp_path / "p", launch_token=TOKEN,
                                 data_dir=tmp_path / "data"))  # fmt: skip
    client = Client(app)
    try:
        merged = client.post("/api/people/merge", json={"target": "p1", "sources": ["p2"]},
                             headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
        assert merged.status_code == 200, merged.text
        sheet = client.get("/api/people/p1/sheet").json()
        assert sorted(t["text_id"] for t in sheet["texts"]) == ["t1", "t2", "t3"]
        assert sheet["merged_from"] == [{"person_id": "p2", "name": "A. Tavelin"}]
        people = {p["person_id"]: p for p in client.get("/api/people").json()["items"]}
        assert people["p1"]["coverage"]["texts"] == 3 and people["p1"]["merged_from"] == ["p2"]
        texts = client.get("/api/texts?person=p1").json()
        assert texts["total"] == 3

        undone = client.post("/api/people/unmerge",
                             json={"person_ids": ["p2"], "remember": "distinct"},
                             headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
        assert undone.status_code == 200, undone.text
        assert undone.json()["from"] == {"p2": "p1"}
        assert client.get("/api/people/p1/sheet").json()["merged_from"] == []
        pairs = (tmp_path / "p" / "decisions" / "people_pairs.csv").read_text()
        assert "p1,p2,distinct" in pairs
    finally:
        app.state.cartolex.shutdown()


@pytest.fixture(scope="module")
def doubled(tmp_path_factory):
    """A demo project (world S) with duplicate people and namesakes added, and the truth."""
    from cartolex.demo import generate
    from cartolex.demo.duplicates import add_duplicates
    from cartolex.demo.project import write_project

    world = generate("S", 0)
    root = tmp_path_factory.mktemp("doubled") / "p"
    project = write_project(world, root)
    truth = add_duplicates(project, world, seed=0, share=0.25, homonyms=0.1)
    project.close()
    return root, truth


def test_the_clear_pairs_are_one_person_and_the_namesakes_are_not(doubled):
    from cartolex.collect.duplicates import duplicate_pairs

    root, truth = doubled
    project = Project.open(root, write=False)
    pairs, _ = duplicate_pairs(project)
    found = {frozenset((p.a, p.b)) for p in pairs}
    assert all(frozenset(s[:2]) in found for s in truth.same)  # every duplicate proposed
    clear = [p for p in pairs if p.clear]
    assert clear and all(truth.is_same(p.a, p.b) for p in clear)
    for a, b, _ in truth.homonyms:
        pair = next((p for p in pairs if {p.a, p.b} == {a, b}), None)
        assert pair is None or not pair.clear


def test_review_a_pair_then_merge_the_clear_ones_in_one_undoable_step(doubled, tmp_path):
    import shutil

    root, truth = doubled
    shutil.copytree(root, tmp_path / "p")
    app = create_app(AppSettings(project=tmp_path / "p", launch_token=TOKEN,
                                 data_dir=tmp_path / "data"))  # fmt: skip
    client = Client(app)
    try:
        listed = client.get("/api/people/duplicates").json()
        assert listed["counts"]["clear"] >= 1 and listed["items"][0]["evidence"]
        first = listed["items"][0]
        compared = client.get(f"/api/people/duplicates/compare?a={first['a']}&b={first['b']}")
        assert compared.status_code == 200, compared.text
        assert {"texts", "affiliations", "coauthors"} <= set(compared.json()["a"])
        # a namesake judged another person never comes back
        a, b, _ = truth.homonyms[0]
        etag_people = etag(client.get("/api/people"))
        decided = client.post("/api/people/duplicates/decide",
                              json={"a": a, "b": b, "decision": "distinct"},
                              headers={"If-Match": etag_people})  # fmt: skip
        assert decided.status_code == 200, decided.text
        every = client.get("/api/people/duplicates?show=all&limit=500").json()["items"]
        assert not any({i["a"], i["b"]} == {a, b} for i in every)

        preview = client.post("/api/people/duplicates/auto", json={}).json()
        assert preview["merged"] >= 1 and not preview["applied"]
        applied = client.post("/api/people/duplicates/auto", json={"apply": True},
                              headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
        assert applied.status_code == 200, applied.text
        merged = applied.json()["person_ids"]
        assert all(truth.is_same(pid, client.get(f"/api/people/{pid}/sheet").json()
                                 ["merged_into"]["person_id"]) for pid in merged)  # fmt: skip
        last = client.get("/api/people/duplicates").json()["last_auto"]
        assert last["person_ids"] == merged
        undone = client.post("/api/people/unmerge",
                             json={"person_ids": merged, "remember": "later"},
                             headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
        assert undone.status_code == 200 and sorted(undone.json()["unmerged"]) == merged
        again = client.post("/api/people/duplicates/auto", json={}).json()
        assert again["merged"] == 0  # pairs undone are left for a person to decide
    finally:
        app.state.cartolex.shutdown()


def test_merge_every_pair_above_a_score_in_one_undoable_step(doubled, tmp_path):
    import shutil

    root, _ = doubled
    shutil.copytree(root, tmp_path / "p")
    app = create_app(AppSettings(project=tmp_path / "p", launch_token=TOKEN,
                                 data_dir=tmp_path / "data"))  # fmt: skip
    client = Client(app)
    try:
        pairs = client.get("/api/people/duplicates?show=all&limit=500").json()["items"]
        above = [p for p in pairs if p["score"] >= 0.5 and not p["conflict"]]
        assert any(not p["clear"] for p in above)  # more than the clear pairs
        # a pair said to be two people is never merged
        kept_apart = above[0]
        decided = client.post("/api/people/duplicates/decide",
                              json={"a": kept_apart["a"], "b": kept_apart["b"],
                                    "decision": "distinct"},
                              headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
        assert decided.status_code == 200, decided.text
        clear = client.post("/api/people/duplicates/auto", json={}).json()
        preview = client.post("/api/people/duplicates/auto", json={"min_score": 0.5}).json()
        assert preview["merged"] > clear["merged"] and not preview["applied"]
        scores = [g["score"] for g in preview["examples"]]
        assert scores == sorted(scores) and min(scores) >= 0.5  # the nearest to it first
        applied = client.post("/api/people/duplicates/auto", json={"min_score": 0.5, "apply": True},
                              headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
        assert applied.status_code == 200, applied.text
        merged = applied.json()["person_ids"]
        assert len(merged) == preview["merged"]
        into = {pid: client.get(f"/api/people/{pid}/sheet").json()["merged_into"]["person_id"]
                for pid in merged}  # fmt: skip
        assert into.get(kept_apart["a"]) != kept_apart["b"]
        assert into.get(kept_apart["b"]) != kept_apart["a"]
        conflicts = {frozenset((p["a"], p["b"])) for p in pairs if p["conflict"]}
        assert not any(frozenset(x) in conflicts for x in into.items())
        # one step: the last automatic merge is all of them, undone at once
        assert client.get("/api/people/duplicates").json()["last_auto"]["person_ids"] == merged
        undone = client.post("/api/people/unmerge",
                             json={"person_ids": merged, "remember": "later"},
                             headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
        assert undone.status_code == 200 and sorted(undone.json()["unmerged"]) == merged
    finally:
        app.state.cartolex.shutdown()


def test_organisations_renamed_merged_by_identifier_and_unmerged(doubled, tmp_path):
    import shutil

    from cartolex.project.tables import read_source_table

    root, _ = doubled
    shutil.copytree(root, tmp_path / "p")
    layout = Project.open(tmp_path / "p", write=False).layout
    orgs = read_source_table(layout.table("organisations"), "organisations").to_pylist()
    labs = sorted(o["org_id"] for o in orgs if o["level"] == "lab")[:2]
    for o in orgs:  # two labs that are one, by their ROR id
        o["ids"] = [("ror", "05abc1234")] if o["org_id"] in labs else []
    _table(Project.open(tmp_path / "p", write=False), "organisations", orgs)
    app = create_app(AppSettings(project=tmp_path / "p", launch_token=TOKEN,
                                 data_dir=tmp_path / "data"))  # fmt: skip
    client = Client(app)
    try:
        pairs = client.get("/api/organisations/pairs").json()
        assert pairs["counts"]["clear"] == 1 and pairs["items"][0]["clear"]
        version = etag(client.get("/api/organisations"))
        merged = client.post("/api/organisations/auto", json={"apply": True},
                             headers={"If-Match": version})  # fmt: skip
        assert merged.status_code == 200, merged.text
        (gone,) = merged.json()["org_ids"]
        (kept,) = set(labs) - {gone}
        listed = {o["org_id"] for o in client.get("/api/organisations?limit=500").json()["items"]}
        assert gone not in listed and kept in listed
        detail = client.get(f"/api/organisations/{kept}").json()
        assert detail["merged_from"][0]["org_id"] == gone
        people = {a["person_id"] for a in detail["affiliations"]}
        assert len(people) > 1
        renamed = client.patch(f"/api/organisations/{kept}", json={"name": "Shore lab"},
                               headers={"If-Match": etag(client.get("/api/organisations"))})  # fmt: skip
        assert renamed.status_code == 200, renamed.text
        anyone = sorted(people)[0]
        sheet = client.get(f"/api/people/{anyone}/sheet").json()
        assert any(a["name"] == "Shore lab" for a in sheet["affiliations"])
        removed = client.post("/api/affiliations", json={"changes": [
            {"person_id": anyone, "org_id": kept, "action": "remove"}]},
            headers={"If-Match": etag(client.get("/api/affiliations/version"))})  # fmt: skip
        assert removed.status_code == 200, removed.text
        sheet = client.get(f"/api/people/{anyone}/sheet").json()
        assert not any(a["org_id"] == kept for a in sheet["affiliations"])
        undone = client.post("/api/organisations/unmerge",
                             json={"org_ids": [gone], "remember": "distinct"},
                             headers={"If-Match": etag(client.get("/api/organisations"))})  # fmt: skip
        assert undone.status_code == 200, undone.text
        assert client.get("/api/organisations/pairs").json()["counts"]["open"] == 0
    finally:
        app.state.cartolex.shutdown()


def test_the_command_line_merges_the_clear_pairs_and_unmerges_them(doubled, tmp_path, capsys):
    import shutil

    from cartolex.cli import main as cli
    from cartolex.collect.decisions import read_people

    root, truth = doubled
    shutil.copytree(root, tmp_path / "p")
    assert cli(["collect", "duplicates", str(tmp_path / "p"), "--merge-clear"]) == 0
    rows = read_people(Project.open(tmp_path / "p", write=False).layout)
    merged = sorted(pid for pid, r in rows.items() if r["merged_into"])
    assert merged and all(truth.is_same(pid, rows[pid]["merged_into"]) for pid in merged)
    assert cli(["collect", "unmerge", str(tmp_path / "p"), *merged]) == 0
    rows = read_people(Project.open(tmp_path / "p", write=False).layout)
    assert not any(r["merged_into"] for r in rows.values())
    assert "clear" in capsys.readouterr().out
