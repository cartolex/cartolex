# SPDX-License-Identifier: MIT
"""From a project's sources to the engine's corpus: same rows, same texts as the old layout."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from cartolex.demo import generate
from cartolex.demo.project import SLOT, write_project
from cartolex.project.corpus import assemble_corpus, render_text
from cartolex.project.tables import decision_csv_bytes


def _rows_with_texts(index_csv: Path) -> list[tuple]:
    with open(index_csv, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [
        (
            r["last_name"],
            r["first_name"],
            r["unit"],
            r["doc_year"],
            r["doc_type"],
            (index_csv.parent / r["txt_path"]).read_bytes(),
        )
        for r in rows
    ]


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
