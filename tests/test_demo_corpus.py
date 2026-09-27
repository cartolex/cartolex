# SPDX-License-Identifier: MIT
"""The demo world written as the engine's corpus contract, and the command line."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from cartolex.demo import generate
from cartolex.demo.cli import main
from cartolex.demo.writers import INDEX_COLUMNS


@pytest.fixture(scope="module")
def world():
    return generate("XS", 0)


@pytest.fixture(scope="module")
def workspace(world, tmp_path_factory):
    ws = tmp_path_factory.mktemp("demo") / "workspace"
    world.write_corpus(ws)
    return ws


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_manual_index_holds_one_row_per_cohort_authorship(world, workspace):
    rows = _rows(workspace / "manual_index.csv")
    assert rows and list(rows[0]) == list(INDEX_COLUMNS)
    cohort = {p.person_id for p in world.cohort}
    expected = sum(1 for w in world.works for a in w.authors if a in cohort)
    assert len(rows) == expected
    for row in rows:
        path = workspace / row["txt_path"]
        assert path.is_file()
        assert row["txt_path"].startswith("automatic_data/corpus_manual/")
        assert 2012 <= int(row["doc_year"]) <= 2026
        assert row["doc_type"] in ("article", "preprint", "proceedings", "report", "thesis")
    units = {g.acronym for g in world.groups}
    assert {r["unit"] for r in rows} <= units


def test_projected_sets_never_reach_the_manual_slot(world, workspace):
    manual = {(r["last_name"], r["first_name"]) for r in _rows(workspace / "manual_index.csv")}
    applicants = world.overlay_sets["applicants"]
    assert applicants
    assert not manual & {(p.last_name, p.first_name) for p in applicants}
    overlay = workspace / "overlay" / "applicants"
    rows = _rows(overlay / "index.csv")
    assert rows and list(rows[0]) == list(INDEX_COLUMNS)
    names = {(p.last_name, p.first_name) for p in applicants}
    assert {(r["last_name"], r["first_name"]) for r in rows} <= names
    for row in rows:
        assert (overlay / row["txt_path"]).is_file()
    manual_texts = {p.name for p in (workspace / "automatic_data" / "corpus_manual").iterdir()}
    overlay_texts = {p.name for p in (overlay / "texts").iterdir()}
    assert not manual_texts & overlay_texts


def test_people_without_works_have_no_rows(world, workspace):
    manual = {(r["last_name"], r["first_name"]) for r in _rows(workspace / "manual_index.csv")}
    for p in world.cohort:
        assert ((p.last_name, p.first_name) in manual) == (p.coverage != "no_data")


def test_the_engine_reads_the_corpus(world, workspace):
    from cartolex.context import EnginePaths
    from cartolex.lexicon.io_helpers import (
        load_documents_split_by_language,
        write_roster,
    )

    # The demo writes the default corpus slot of the workspace layout.
    paths = EnginePaths.for_workspace(workspace)
    assert paths.corpus_index_csv("manual") == workspace / "manual_index.csv"
    assert paths.corpus_text_dir("manual").is_dir()
    docs, meta = load_documents_split_by_language(
        [("manual", paths.corpus_index_csv("manual"), None)]
    )
    with_works = [p for p in world.cohort if p.coverage != "no_data"]
    assert len(meta) == len(with_works)
    assert sum(1 for d in docs["en"] if d) >= len(with_works) * 0.8
    assert any(docs["fr"])
    out = workspace.parent / "roster.csv"
    assert write_roster(index_csvs=[workspace / "manual_index.csv"], out_csv=out) == (
        len(with_works)
    )


def test_write_corpus_refuses_an_existing_corpus(world, tmp_path):
    world.write_corpus(tmp_path)
    with pytest.raises(FileExistsError):
        world.write_corpus(tmp_path)
    summary = world.write_corpus(tmp_path, overwrite=True)
    assert summary["manual"]["rows"] > 0 and summary["overlay:applicants"]["rows"] > 0


def test_command_line_creates_a_world_and_its_corpus(tmp_path, capsys):
    out = tmp_path / "demo"
    assert main(["create", "--size", "xs", "--seed", "0", "--out", str(out), "--corpus"]) == 0
    assert (out / "manifest.json").is_file()
    assert (out / "workspace" / "manual_index.csv").is_file()
    assert (out / "workspace" / "overlay" / "applicants" / "index.csv").is_file()
    assert "demo world XS/0" in capsys.readouterr().out
    assert main(["create", "--size", "XS", "--out", str(out)]) == 1
    assert main(["create", "--size", "XS", "--out", str(out), "--corpus", "--force"]) == 0
