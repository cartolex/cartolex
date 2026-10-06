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
