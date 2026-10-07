# SPDX-License-Identifier: MIT
"""Duplicates: a name written another way is still proposed, evidence against lowers a
pair without hiding it, merges keep namesakes in view, and people are reviewed in groups
(merged, set apart or left for later in one step)."""

from __future__ import annotations

import pytest
from _app_helpers import TOKEN, Client, etag
from test_people_identity import _decide, _project

from cartolex.app import AppSettings, create_app
from cartolex.collect import duplicates as dups
from cartolex.collect.duplicates import duplicate_pairs
from cartolex.project import Project
from cartolex.project.pairs import remember_pairs

ORCID_1, ORCID_2 = "0000-0001-0000-0001", "0000-0001-0000-0002"


def _pairs(tmp_path, people, texts, decisions=None):
    project = _project(tmp_path / "p", people, texts)
    _decide(project, decisions or [{"person_id": p[0]} for p in people])
    report: dict = {}
    pairs, _ = duplicate_pairs(project, report=report)
    project.close()
    return {frozenset((p.a, p.b)): p for p in pairs}, report


# Two rows of one name, written as sources write it (last name, first name).
WRITTEN = [
    (("Sørvik", "Ada"), ("Sorvik", "Ada"), "dup_same_name"),  # a letter no accent removes
    (("Tavelin", "Łucja"), ("Tavelin", "Lucja"), "dup_same_name"),
    (("Tavelin", "Ada de"), ("de Tavelin", "Ada"), "dup_same_name"),  # a particle moved
    (("O'Tavelin", "Ada"), ("OTavelin", "Ada"), "dup_same_name"),
    (("Петрова", "Ада"), ("Петрова", "Ада"), "dup_same_name"),  # another script
    (("", "Ada Tavelin"), ("Tavelin", "Ada"), "dup_same_name"),  # one cell for the name
    (("Tavelin Marsh", "Ada"), ("Marsh", "Ada Tavelin"), "dup_name_order"),  # split elsewhere
    (("Ada", "Tavelin"), ("Tavelin", "Ada"), "dup_name_order"),  # swapped
    (("Tavelin Marsh", "Ada"), ("Marsh Tavelin", "Ada"), "dup_name_order"),
    (("Tavelin", "Ada M."), ("Tavelin", "Ada"), "dup_other_given"),  # a middle initial
    (("Tavelin", "Ada Maria"), ("Tavelin", "Ada"), "dup_other_given"),
]


@pytest.mark.parametrize(("a", "b", "code"), WRITTEN)
def test_a_name_written_another_way_is_proposed(tmp_path, a, b, code):
    people = [("p1", *a, None), ("p2", *b, None), ("p3", "Morvan", "Lise", None)]
    texts = [("t1", 2012, [("p1", 1), ("p3", 2)]), ("t2", 2021, [("p2", 1)])]
    pairs, _ = _pairs(tmp_path, people, texts)
    assert code in pairs[frozenset(("p1", "p2"))].codes()


def test_evidence_against_lowers_a_pair_but_never_hides_it(tmp_path):
    people = [("p1", "Tavelin", "Ada", ORCID_1), ("p2", "Tavelin", "Ada", ORCID_2),
              ("p3", "Tavelin", "Ada", None), ("p4", "Tavelin", "Ada", None)]  # fmt: skip
    texts = [
        ("t1", 2019, [("p3", 1), ("p4", 3)]),  # two places of one text
        ("t2", 2020, [("p1", 1), ("p3", 0)]),
    ]  # a place unknown: says nothing
    pairs, _ = _pairs(tmp_path, people, texts)
    orcids, together = pairs[frozenset(("p1", "p2"))], pairs[frozenset(("p3", "p4"))]
    assert orcids.conflict and orcids.score < 0.05 and "dup_same_name" in orcids.codes()
    assert "dup_together" in together.codes() and "dup_same_name" in together.codes()
    assert not {"dup_together", "dup_same_place"} & pairs[frozenset(("p1", "p3"))].codes()


def test_one_work_recorded_under_both_counts_for_them(tmp_path):
    project = _project(tmp_path / "p", [("p1", "Tavelin", "Ada", None),
                                        ("p2", "Tavelin", "Ada", None)],
                       [("t1", 2020, [("p1", 1)]), ("t2", 2021, [("p2", 1)])])  # fmt: skip
    _decide(project, [{"person_id": "p1"}, {"person_id": "p2"}])
    (pair,), _ = duplicate_pairs(project)  # the helper titles every text alike but its id
    assert "dup_same_title" not in pair.codes()
    import pyarrow.parquet as pq

    from cartolex.project.tables import write_source_table

    table = pq.read_table(project.layout.table("texts"))
    titles = ["Tides and sediments of one shared bay"] * 2
    write_source_table(project.layout.table("texts"), "texts",
                       table.set_column(table.schema.get_field_index("title"), "title",
                                        [titles]))  # fmt: skip
    (pair,), _ = duplicate_pairs(project)
    project.close()
    assert "dup_same_title" in pair.codes()


def test_a_crowded_name_needs_more_than_the_name(tmp_path, monkeypatch):
    monkeypatch.setattr(dups, "MAX_NAMESAKES", 2)
    people = [(f"p{i}", "Tavelin", "Ada", None) for i in range(1, 4)]
    people.append(("p9", "Morvan", "Lise", None))
    texts = [("t1", 2019, [("p1", 1), ("p9", 2)]), ("t2", 2020, [("p2", 1), ("p9", 2)]),
             ("t3", 2021, [("p3", 1)])]  # fmt: skip
    pairs, report = _pairs(tmp_path, people, texts)
    assert set(pairs) == {frozenset(("p1", "p2"))}  # a co-author in common
    assert report["common_names"] == [{"name": "ada tavelin", "people": 3, "compared": True}]


def test_two_orcids_among_the_people_merged_refuse_the_merge():
    from cartolex.project.identity import MergeRefused, merge_changes

    rows = {p: {"person_id": p, "merged_into": "", "records": ""} for p in ("a", "b", "c")}
    orcids = {"a": None, "b": ORCID_1, "c": ORCID_2}
    with pytest.raises(MergeRefused):  # b and c, both merged into a
        merge_changes(rows, "a", ["b", "c"], orcids)
    assert set(merge_changes(rows, "a", ["b", "c"], orcids, override=True)) == {"b", "c"}


def test_the_same_name_at_another_unit_is_a_suggested_merge_of_records():
    from cartolex.collect.institutions import _Authors, _merges, _TableView

    authors = {
        "A1": {"name": "Ada Tavelin", "works": [{"id": "W1", "year": 2015, "units": ["I1"]}]},
        "A2": {"name": "TAVELIN Ada", "works": [{"id": "W2", "year": 2021, "units": ["I2"]}]},
        "A3": {"name": "Ada M. Tavelin", "works": [{"id": "W3", "year": 2022, "units": []}]},
        "A4": {"name": "Ada Tavelin", "works": [{"id": "W1", "year": 2015, "units": ["I1"]}]},
    }  # fmt: skip
    table = _Authors()
    for aid, rec in authors.items():
        table.add_entry({**rec, "orcid": None, "record": f"openalex:{aid}"})
    found = {tuple(r.split(":")[1] for r in m.records): (m.code, m.clear)
             for m in _merges(_TableView(table), 1)}  # fmt: skip
    assert found[("A1", "A2")] == ("same_name", False)
    assert found[("A1", "A4")] == ("same_name_work", False)  # both on one work: still said
    assert ("A1", "A3") not in found  # a middle initial and no unit in common: not enough


# ── groups, on the app ───────────────────────────────────────────────────────

GROUPED = [
    ("p1", "Tavelin", "Ada", None),
    ("p2", "Tavelin", "Ada", None),
    ("p3", "Tavelin", "Ada M.", None),
    ("p4", "Tavelin", "Ada", None),  # a namesake, said to be another person than p1
    ("p5", "Morvan", "Lise", None),
    ("p6", "Quell", "Rui", None),
    ("p7", "Morvan", "Ada", ORCID_1),  # p8 merged into p7: the person p8 now is
    ("p8", "Lindqvist", "Ines", ORCID_1),
    ("p9", "Lindqvist", "Ines", None),
]
GROUPED_TEXTS = [
    (f"t{i}{p}", 2010 + i, [(p, 1), ("p5", 2), ("p6", 3)])
    for i, p in enumerate(("p1", "p2", "p3", "p4"))
] + [("t9", 2020, [("p8", 1)]), ("t10", 2021, [("p9", 1)])]


@pytest.fixture()
def grouped(tmp_path):
    project = _project(tmp_path / "p", GROUPED, GROUPED_TEXTS)
    _decide(project, [{"person_id": p} for p, *_ in GROUPED if p != "p8"]
            + [{"person_id": "p8", "merged_into": "p7"}])  # fmt: skip
    remember_pairs(project.layout, [("p1", "p4")], "distinct")
    project.close()
    app = create_app(AppSettings(project=tmp_path / "p", launch_token=TOKEN,
                                 data_dir=tmp_path / "data"))  # fmt: skip
    yield Client(app)
    app.state.cartolex.shutdown()


def _groups(client, show="open"):
    return client.get(f"/api/people/duplicates/groups?show={show}&limit=100").json()


def test_three_rows_of_one_person_are_one_group_that_merges_in_one_step(grouped):
    client = grouped
    listed = _groups(client)
    by_ids = {frozenset(g["ids"]): g for g in listed["items"]}
    trio = by_ids[frozenset(("p1", "p2", "p3"))]  # never p4, said to be another person
    assert trio["size"] == 3 and len(trio["pairs"]) == 3 and trio["keep"] in trio["ids"]
    # a namesake of a row merged into another person is proposed with that person
    assert any({"p7", "p9"} == set(g["ids"]) for g in listed["items"])
    compared = client.get("/api/people/duplicates/group?ids=p1,p2,p3").json()
    assert [s["person_id"] for s in compared["people"]] == ["p1", "p2", "p3"]
    assert {c["person_id"] for c in compared["shared"]["coauthors"]} == {"p5", "p6"}

    merged = client.post("/api/people/duplicates/group",
                         json={"ids": ["p1", "p2", "p3"], "decision": "merge", "keep": "p2"},
                         headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
    assert merged.status_code == 200, merged.text
    assert merged.json()["merged"] == ["p1", "p3"]
    sheet = client.get("/api/people/p2/sheet").json()
    assert sorted(m["person_id"] for m in sheet["merged_from"]) == ["p1", "p3"]
    # one step back
    undone = client.post("/api/people/unmerge", json={"person_ids": ["p1", "p3"]},
                         headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
    assert undone.status_code == 200, undone.text
    assert any(set(g["ids"]) == {"p1", "p2", "p3"} for g in _groups(client)["items"])


def test_a_group_is_split_set_apart_or_left_for_later(grouped):
    client = grouped

    def decide(body):
        r = client.post("/api/people/duplicates/group", json=body,
                        headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
        assert r.status_code == 200, r.text

    decide({"ids": ["p1", "p2", "p3"], "decision": "distinct", "apart": ["p3"]})
    groups = [set(g["ids"]) for g in _groups(client)["items"]]
    assert {"p1", "p2"} in groups and not any({"p3", "p1"} <= g or {"p3", "p2"} <= g
                                              for g in groups)  # fmt: skip
    decide({"ids": ["p1", "p2"], "decision": "later"})
    later = [set(g["ids"]) for g in _groups(client, "later")["items"]]
    assert later == [{"p1", "p2"}]


def test_people_said_to_be_one_by_hand_with_two_orcids(tmp_path):
    people = [("p1", "Tavelin", "Ada", ORCID_1), ("p2", "Morvan", "Lise", ORCID_2),
              ("p3", "Quell", "Rui", None)]  # fmt: skip
    project = _project(tmp_path / "p", people, [("t1", 2020, [("p1", 1)])])
    _decide(project, [{"person_id": p} for p, *_ in people])
    project.close()
    app = create_app(AppSettings(project=tmp_path / "p", launch_token=TOKEN,
                                 data_dir=tmp_path / "data"))  # fmt: skip
    client = Client(app)
    try:
        compared = client.get("/api/people/duplicates/group?ids=p3,p1,p2").json()
        assert compared["conflict"] and compared["pairs"] == []  # nobody proposed them
        body = {"ids": ["p3", "p1", "p2"], "decision": "merge", "keep": "p3"}
        refused = client.post("/api/people/duplicates/group", json=body,
                              headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "merge_orcid_conflict"
        merged = client.post("/api/people/duplicates/group", json={**body, "override": True},
                             headers={"If-Match": etag(client.get("/api/people"))})  # fmt: skip
        assert merged.status_code == 200 and merged.json()["merged"] == ["p1", "p2"]
    finally:
        app.state.cartolex.shutdown()


def test_a_project_without_people_has_no_group(tmp_path):
    project = Project.init(tmp_path / "p", name="Empty", domain_title="Coasts")
    project.close()
    app = create_app(AppSettings(project=tmp_path / "p", launch_token=TOKEN,
                                 data_dir=tmp_path / "data"))  # fmt: skip
    try:
        listed = Client(app).get("/api/people/duplicates/groups").json()
        assert listed["total"] == 0 and listed["counts"]["open"] == 0
    finally:
        app.state.cartolex.shutdown()


def test_two_people_said_apart_stay_apart_after_one_is_merged(grouped, tmp_path):
    client = grouped
    assert any(set(g["ids"]) == {"p7", "p9"} for g in _groups(client, "all")["items"])
    project = Project.open(tmp_path / "p", write=False)
    remember_pairs(project.layout, [("p8", "p9")], "distinct")  # p8, now merged into p7
    project.close()
    assert not any("p9" in g["ids"] for g in _groups(client, "all")["items"])
