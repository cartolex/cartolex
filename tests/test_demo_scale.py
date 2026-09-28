# SPDX-License-Identifier: MIT
"""Streamed demo worlds: valid project tables, the demo's structure, determinism."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from cartolex.demo import generate
from cartolex.demo.identifiers import DOI_RE, IDHAL_RE, OPENALEX_RE, is_valid_orcid
from cartolex.demo.scale import person_id, scale_groups, text_id, write_scale_project
from cartolex.project import Project
from cartolex.project.corpus import assemble_corpus
from cartolex.project.layout import SOURCE_TABLES
from cartolex.project.tables import read_decision_csv, read_source_table


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    root = tmp_path_factory.mktemp("scale") / "project"
    summary = write_scale_project(root, 150, seed=3, row_group=400)
    return root, summary.as_dict()


def _tables(root: Path) -> dict:
    return {
        name: read_source_table(root / "sources" / "tables" / f"{name}.parquet", name)
        for name in SOURCE_TABLES
    }


def test_the_tables_are_valid_and_written_in_row_groups(world):
    root, summary = world
    tables = _tables(root)  # read_source_table checks schema, order and keys
    assert tables["people"].num_rows == summary["people"] == 165
    assert summary["mapped"] == 150 and summary["applicants"] == 15
    assert tables["texts"].num_rows == summary["texts"] > 700
    assert tables["text_parts"].num_rows == 2 * summary["texts"]
    assert tables["authorships"].num_rows == summary["authorships"]
    assert pq.ParquetFile(root / "sources" / "tables" / "text_parts.parquet").num_row_groups > 1
    ids = tables["people"]["person_id"].to_pylist()
    assert ids[0] == person_id(0) and ids[-1] == person_id(164)
    assert tables["texts"]["text_id"].to_pylist()[0] == text_id(0)
    groups, external, institutions = scale_groups(150)
    assert (
        tables["organisations"].num_rows
        == groups + external + institutions
        == summary["groups"] + summary["institutions"]
    )


def test_the_world_keeps_the_demo_structure(world):
    root, summary = world
    tables = _tables(root)
    langs = Counter(tables["text_parts"]["language"].to_pylist())
    assert set(langs) == {"en", "fr"} and langs["en"] > 2 * langs["fr"]
    authors = Counter(tables["authorships"]["text_id"].to_pylist())
    mean_authors = sum(authors.values()) / len(authors)
    assert 1.8 < mean_authors < 3.2  # co-authorship, as in the demo worlds
    orcids = [o for o in tables["people"]["orcid"].to_pylist() if o]
    assert orcids and all(is_valid_orcid(o) for o in orcids)
    for scheme, values in (
        pair for ids in tables["people"]["ids"].to_pylist() for pair in (ids or [])
    ):
        pattern = IDHAL_RE if scheme == "idhal" else OPENALEX_RE
        assert all(pattern.match(v) for v in values)
    assert all(DOI_RE.match(d) for d in tables["texts"]["doi"].to_pylist() if d)
    names = list(
        zip(
            tables["people"]["last_name"].to_pylist(),
            tables["people"]["first_name"].to_pylist(),
            strict=True,
        )
    )
    assert len(set(names)) == len(names)
    roles = read_decision_csv(root / "decisions" / "people.csv", "people")
    assert Counter(r["role"] for r in roles) == {"mapped": 150, "projected": 15}


def test_the_corpus_is_assembled_from_the_tables(world, tmp_path):
    root, _ = world
    project = Project.open(root)
    try:
        summary = assemble_corpus(project.layout, project.config, tmp_path / "corpus")
    finally:
        project.close()
    assert summary.slots["manual"]["people"] > 130
    assert summary.slots["overlay:applicants"]["people"] > 10


def test_the_same_world_whatever_the_workers(world, tmp_path):
    root, _ = world
    again = tmp_path / "again"
    write_scale_project(again, 150, seed=3, row_group=10_000, workers=2)
    for name, table in _tables(root).items():
        assert _tables(again)[name].equals(table), name
    other = tmp_path / "other"
    write_scale_project(other, 150, seed=4)
    assert not _tables(other)["texts"].equals(_tables(root)["texts"])


def test_the_small_worlds_are_untouched():
    # The streamed worlds live beside generate(), which they do not change.
    assert len(generate("XS", 0).people) == 14
    with pytest.raises(ValueError, match="at least 12"):
        write_scale_project("unused", 5)
