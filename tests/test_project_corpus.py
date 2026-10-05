# SPDX-License-Identifier: MIT
"""From a project's sources to the engine's corpus: same rows, same texts as the old layout."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from cartolex.demo import generate
from cartolex.demo.project import SLOT, write_project
from cartolex.lexicon.corpus_store import index_rows
from cartolex.project.corpus import assemble_corpus, render_text
from cartolex.project.tables import decision_csv_bytes


def _rows_with_texts(index_csv: Path) -> list[tuple]:
    return [
        (r["last_name"], r["first_name"], r["unit"], r["doc_year"], r["doc_type"],
         r["text"].encode("utf-8"))
        for r in index_rows(index_csv)
    ]  # fmt: skip


@pytest.fixture(scope="module")
def world_and_project(tmp_path_factory):
    world = generate("S", 0)
    base = tmp_path_factory.mktemp("corpus")
    world.write_corpus(base / "workspace")
    project = write_project(world, base / "project")
    project.close()
    return world, base


def test_the_project_gives_the_engine_the_same_corpus(world_and_project):
    world, base = world_and_project
    from cartolex.project import Project

    project = Project.open(base / "project")
    summary = assemble_corpus(project.layout, project.config, base / "assembled")
    old = _rows_with_texts(base / "workspace" / "manual_index.csv")
    new = _rows_with_texts(base / "assembled" / SLOT / "index.csv")
    assert new == old and len(new) > 0
    assert summary.slots[SLOT]["rows"] == len(old)
    for name in world.overlay_sets:
        old_set = _rows_with_texts(base / "workspace" / "overlay" / name / "index.csv")
        new_set = _rows_with_texts(base / "assembled" / "overlays" / name / "index.csv")
        assert new_set == old_set


def test_roles_decide_who_is_read(world_and_project, tmp_path):
    world, base = world_and_project
    from cartolex.project import Project

    project = Project.open(base / "project")
    first = world.cohort[0]
    rows = [
        {"person_id": p.person_id, "role": "excluded" if p is first else "mapped"}
        for p in world.cohort
    ]
    layout = project.layout
    saved = layout.people_csv.read_bytes()
    try:
        layout.people_csv.write_bytes(decision_csv_bytes("people", rows))
        summary = assemble_corpus(layout, project.config, tmp_path / "out")
        names = {r[:2] for r in _rows_with_texts(tmp_path / "out" / SLOT / "index.csv")}
        assert (first.last_name, first.first_name) not in names
        assert summary.skipped_people >= 1
        assert "overlay:" + next(iter(world.overlay_sets)) in summary.slots
    finally:
        layout.people_csv.write_bytes(saved)


def test_render_text_reading_order():
    parts = [
        ("abstract", "fr", "Résumé."),
        ("title", "en", "A title"),
        ("abstract", "en", "Abstract."),
        ("body", "en", "Body."),
    ]
    assert render_text(parts, chosen=("title", "abstract")) == "A title\n\nAbstract.\n\nRésumé.\n"
    assert render_text(parts, chosen=("title", "abstract", "body")).endswith("Body.\n")
    assert render_text(parts, chosen=("body",)) == "Body.\n"
    assert render_text([("full", "en", "\nWhole text.\n")], chosen=("full",)) == "Whole text.\n"
    assert render_text(parts, chosen=()) == ""


def test_an_overlay_with_its_own_root_is_read_from_there(world_and_project, tmp_path):
    import pyarrow as pa
    import pyarrow.compute as pc

    from cartolex.project import Project, ProjectLayout
    from cartolex.project.models import Overlay
    from cartolex.project.tables import read_source_table, write_source_table

    world, base = world_and_project
    name, members = next(iter(world.overlay_sets.items()))
    ids = [p.person_id for p in members]
    source = ProjectLayout(base / "project")
    root = tmp_path / "outside" / name  # beside the project, as a host application would keep it
    for table in ("texts", "text_parts", "people", "authorships", "organisations", "affiliations"):
        t = read_source_table(source.table(table), table)
        if "person_id" in t.column_names:
            t = t.filter(pc.is_in(t["person_id"], value_set=pa.array(ids)))
        write_source_table(root / "tables" / f"{table}.parquet", table, t)
    project = Project.open(base / "project")
    config = project.config.model_copy(update={"overlays": [Overlay(id=name, root=str(root))]})
    assemble_corpus(project.layout, config, tmp_path / "out")
    old = _rows_with_texts(base / "workspace" / "overlay" / name / "index.csv")
    new = _rows_with_texts(tmp_path / "out" / "overlays" / name / "index.csv")
    assert new == old and new


def _tree(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_the_parts_are_read_a_batch_at_a_time(world_and_project, tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    from cartolex.project import Project, corpus
    from cartolex.project.tables import TableError

    _, base = world_and_project
    project = Project.open(base / "project")
    try:
        whole = assemble_corpus(project.layout, project.config, tmp_path / "whole")
        monkeypatch.setattr(corpus, "PARTS_BATCH", 7)  # a text's parts straddle batches
        small = assemble_corpus(project.layout, project.config, tmp_path / "small")
    finally:
        project.close()
    assert _tree(tmp_path / "small") == _tree(tmp_path / "whole")
    assert small.slots == whole.slots and small.texts_without_parts == whole.texts_without_parts

    # Rows out of order across two batches are refused.
    copy = tmp_path / "tables"
    copy.mkdir()
    for name in ("texts", "people", "authorships", "organisations", "affiliations"):
        (copy / f"{name}.parquet").write_bytes(project.layout.table(name).read_bytes())
    parts = pq.read_table(project.layout.table("text_parts"))
    swapped = parts.slice(7, 7).to_batches() + parts.slice(0, 7).to_batches()
    pq.write_table(parts.from_batches(swapped), copy / "text_parts.parquet", row_group_size=7)
    texts = pq.read_table(copy / "texts.parquet")
    wanted = set(texts["text_id"].to_pylist())
    with pytest.raises(TableError, match="not sorted"):
        corpus._write_texts(
            copy, {t: {} for t in wanted}, [(tmp_path / "x", wanted)], ["title"], []
        )


def test_person_attributes_reach_the_engine_index(world_and_project, tmp_path):
    """The filter columns of an imported list follow the index's columns, so the roster
    (and the map) can colour and filter people by them."""
    from cartolex.lexicon.io_helpers import write_roster
    from cartolex.project import Project

    world, base = world_and_project
    project = Project.open(base / "project")
    assemble_corpus(project.layout, project.config, tmp_path / "out")
    index = tmp_path / "out" / SLOT / "index.csv"
    with open(index.parent / "people.csv", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        list(reader)
    assert reader.fieldnames == [
        "person_id", "last_name", "first_name", "unit", "career_stage", "site"
    ]  # fmt: skip
    by_name = {(p.last_name, p.first_name): p for p in world.people}
    for r in index_rows(index):
        person = by_name[(r["last_name"], r["first_name"])]
        assert (r["career_stage"], r["site"]) == (person.career_stage, person.site)
    roster = tmp_path / "roster.csv"
    write_roster(index_csvs=[index], out_csv=roster)
    with open(roster, encoding="utf-8", newline="") as fh:
        assert csv.DictReader(fh).fieldnames == [
            "last_name", "first_name", "unit", "career_stage", "site"
        ]  # fmt: skip


def test_an_attribute_named_like_a_contract_column_is_renamed():
    from cartolex.project.corpus import attribute_column

    assert attribute_column("career_stage") == "career_stage"
    assert attribute_column("unit") == "person_unit"
    assert attribute_column("source") == "person_source"


def test_parts_by_slot_kind_read_documents_whole_and_collected_texts_by_their_abstracts(tmp_path):
    """A folder's documents are read whole; a collected text with a full text collected on
    request is still read by its title and abstract, unless the parts say otherwise."""
    from datetime import datetime, timezone

    import pyarrow as pa

    from cartolex.build.params import PARTS_BY_SLOT_KIND
    from cartolex.project import Project
    from cartolex.project.models import Slot
    from cartolex.project.tables import SOURCE_SCHEMAS, write_source_table

    at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    project = Project.init(
        tmp_path / "p",
        name="Parts",
        domain_title="Coasts",
        slots=(Slot(id="collected", kind="collection"), Slot(id="documents", kind="folder")),
    )

    def table(name, rows):
        schema = SOURCE_SCHEMAS[name]
        write_source_table(
            project.layout.table(name),
            name,
            pa.table({f.name: [r.get(f.name) for r in rows] for f in schema}, schema=schema),
        )

    table("people", [{"person_id": "p1", "last_name": "Tavelin", "first_name": "Ada", "ids": [],
                      "source": "import", "columns": [], "aliases": [], "retrieved_at": at}])  # fmt: skip
    base = {"year": 2024, "ids": [], "n_authors": 1, "retrieved_at": at, "doc_type": "article"}
    table("texts", [
        {**base, "text_id": "t1", "slot": "collected", "position": 0, "title": "A", "source": "openalex"},
        {**base, "text_id": "t2", "slot": "documents", "position": 0, "title": "B", "source": "folder"},
    ])  # fmt: skip
    part = {"language": "en", "format": "plain", "retrieved_at": at}
    table("text_parts", [
        {**part, "text_id": "t1", "part": "abstract", "provider": "openalex", "content": "Tidal flats."},
        {**part, "text_id": "t1", "part": "full", "provider": "hal", "content": "A whole paper."},
        {**part, "text_id": "t1", "part": "title", "provider": "openalex", "content": "Flats"},
        {**part, "text_id": "t2", "part": "full", "provider": "folder", "content": "A whole report."},
    ])  # fmt: skip
    table("authorships", [{"text_id": t, "person_id": "p1", "position": 1, "orgs": []}
                          for t in ("t1", "t2")])  # fmt: skip
    out = tmp_path / "out"
    assemble_corpus(project.layout, project.config, out, parts=PARTS_BY_SLOT_KIND)

    def text(folder, tid):
        return next(r["text"] for r in index_rows(folder / "index.csv") if r["text_id"] == tid)

    assert text(out / "collected", "t1") == "Flats\n\nTidal flats.\n"
    assert text(out / "documents", "t2") == "A whole report.\n"
    everywhere = tmp_path / "all"
    assemble_corpus(project.layout, project.config, everywhere, parts=["title", "abstract", "full"])
    assert text(everywhere / "collected", "t1") == "A whole paper.\n"
    project.close()


def test_a_collection_reads_texts_not_datasets_unless_its_slot_says_so(tmp_path):
    """By default a collection slot reads articles, preprints, books, theses, reports…, not
    the datasets, software or peer reviews an index lists; they stay in the tables, and a
    slot's own doc_types replace the default. A folder reads every document it was given."""
    from datetime import datetime, timezone

    import pyarrow as pa

    from cartolex.build.params import DOC_TYPES_BY_SLOT_KIND
    from cartolex.project import Project
    from cartolex.project.models import Slot
    from cartolex.project.tables import SOURCE_SCHEMAS, write_source_table

    at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    project = Project.init(
        tmp_path / "p",
        name="Types",
        domain_title="Coasts",
        slots=(Slot(id="collected", kind="collection"), Slot(id="documents", kind="folder")),
    )

    def table(name, rows):
        schema = SOURCE_SCHEMAS[name]
        write_source_table(
            project.layout.table(name),
            name,
            pa.table({f.name: [r.get(f.name) for r in rows] for f in schema}, schema=schema),
        )

    table("people", [{"person_id": "p1", "last_name": "Tavelin", "first_name": "Ada", "ids": [],
                      "source": "import", "columns": [], "aliases": [], "retrieved_at": at}])  # fmt: skip
    kinds = ["article", "dataset", "software", "peer-review", "thesis", "communication"]
    texts = [
        {"text_id": f"t{i}", "slot": "collected", "position": i, "year": 2024, "ids": [],
         "n_authors": 1, "retrieved_at": at, "doc_type": k, "title": k, "source": "openalex"}
        for i, k in enumerate(kinds)
    ] + [
        {"text_id": "d1", "slot": "documents", "position": 0, "year": 2024, "ids": [],
         "n_authors": 1, "retrieved_at": at, "doc_type": "other", "title": "d", "source": "folder"}
    ]  # fmt: skip
    table("texts", texts)
    table("text_parts", [
        {"text_id": t["text_id"], "part": "title", "language": "en", "provider": "openalex",
         "format": "plain", "content": f"A {t['doc_type']} about tidal flats", "retrieved_at": at}
        for t in texts
    ])  # fmt: skip
    table("authorships", [{"text_id": t["text_id"], "person_id": "p1", "position": 1, "orgs": []}
                          for t in texts])  # fmt: skip

    def read(out):
        collected = {r["doc_type"] for r in index_rows(out / "collected" / "index.csv")}
        documents = {r["doc_type"] for r in index_rows(out / "documents" / "index.csv")}
        return collected, documents

    summary = assemble_corpus(project.layout, project.config, tmp_path / "a",
                              parts=["title"], doc_types=DOC_TYPES_BY_SLOT_KIND)  # fmt: skip
    assert read(tmp_path / "a") == ({"article", "thesis", "communication"}, {"other"})
    assert summary.texts_left_out_by_type == 3
    own = [s.model_copy(update={"doc_types": ["article", "dataset"]}) if s.id == "collected" else s
           for s in project.config.slots]  # fmt: skip
    project.save_config(project.config.model_copy(update={"slots": own}), action="types")
    assemble_corpus(project.layout, project.config, tmp_path / "b",
                    parts=["title"], doc_types=DOC_TYPES_BY_SLOT_KIND)  # fmt: skip
    assert read(tmp_path / "b")[0] == {"article", "dataset"}
    project.close()


def test_a_work_found_twice_is_read_once_as_its_version_of_record(tmp_path):
    """Same normalised title, years at most one apart and a shared author: one work. The
    tables keep every record; the corpus reads the version of record (between two articles,
    the one with the richer parts), and each copy's authors read it."""
    from datetime import datetime, timezone

    import pyarrow as pa

    from cartolex.project import Project
    from cartolex.project.models import Slot
    from cartolex.project.tables import SOURCE_SCHEMAS, read_source_table, write_source_table

    at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    project = Project.init(tmp_path / "p", name="Twins", domain_title="Coasts",
                           slots=(Slot(id="c", kind="collection"),))  # fmt: skip

    def table(name, rows):
        schema = SOURCE_SCHEMAS[name]
        write_source_table(
            project.layout.table(name),
            name,
            pa.table({f.name: [r.get(f.name) for r in rows] for f in schema}, schema=schema),
        )

    table("people", [{"person_id": p, "last_name": f"Name{p}", "first_name": "A", "ids": [],
                      "source": "import", "columns": [], "aliases": [], "retrieved_at": at}
                     for p in ("p1", "p2", "p3")])  # fmt: skip
    title = "Sediment Transport on the tidal flats of a bay"
    texts = [  # (id, type, year, title, authors)
        ("a1", "article", 2021, title, ["p1"]),
        ("a2", "article", 2021, title.upper() + ".", ["p2", "p1"]),  # richer: it has an abstract
        ("c1", "communication", 2020, title, ["p1"]),
        ("r1", "preprint", 2020, title, ["p2"]),
        ("x1", "article", 2021, title, ["p3"]),  # no author in common
        ("y1", "article", 2018, title, ["p1"]),  # too far apart
        ("s1", "article", 2021, "Tidal flats", ["p1"]),  # a short title never joins
        ("s2", "article", 2021, "Tidal flats", ["p1"]),
    ]
    table("texts", [{"text_id": t, "slot": "c", "position": i, "year": y, "ids": [],
                     "n_authors": len(a), "retrieved_at": at, "doc_type": k, "title": ti,
                     "source": "openalex"} for i, (t, k, y, ti, a) in enumerate(texts)])  # fmt: skip
    part = {"language": "en", "provider": "openalex", "format": "plain", "retrieved_at": at}
    table("text_parts", sorted([{**part, "text_id": t, "part": "title", "content": ti}
                                for t, _, _, ti, _ in texts]
                               + [{**part, "text_id": "a2", "part": "abstract",
                                   "content": "Mud and sand move with the tide."}],
                               key=lambda r: (r["text_id"], r["part"])))  # fmt: skip
    table("authorships", [{"text_id": t, "person_id": p, "position": j + 1, "orgs": []}
                          for t, _, _, _, a in texts for j, p in enumerate(a)])  # fmt: skip
    out = tmp_path / "out"
    summary = assemble_corpus(project.layout, project.config, out, parts=["title", "abstract"])
    read = sorted((r["last_name"], r["text_id"]) for r in index_rows(out / "c" / "index.csv"))
    assert read == [
        ("Namep1", "a2"), ("Namep1", "s1"), ("Namep1", "s2"),
        ("Namep1", "y1"), ("Namep2", "a2"), ("Namep3", "x1"),
    ]  # fmt: skip
    assert summary.duplicate_texts == 3
    assert read_source_table(project.layout.table("texts"), "texts").num_rows == len(texts)
    project.close()
