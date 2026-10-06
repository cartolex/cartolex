# SPDX-License-Identifier: MIT
"""People without a unit reach the map.

The engine names a missing unit ``NA`` in its tables (the roster, the people ×
keywords matrices) while the project's corpus leaves it empty. A table read with
pandas' default missing values turned ``NA`` into NaN, so the roster's ids no
longer matched the stored matrices' and those people silently left the space; a
project where nobody has a unit (people taken from an institution) could not
build its space at all.
"""

from __future__ import annotations

import csv
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from _app_helpers import TOKEN, Client

from cartolex.app import AppSettings, create_app
from cartolex.atlas.io import load_index
from cartolex.atlas.plots import aggregate_labs
from cartolex.atlas.types import Embeddings
from cartolex.cli import main as cli
from cartolex.demo import generate
from cartolex.demo.project import write_project
from cartolex.lexicon.consolidation import researcher_ids
from cartolex.lexicon.utils import make_researcher_id


def test_a_missing_unit_has_one_id_however_a_table_writes_it():
    ids = {make_researcher_id("Ond", "Ama", u) for u in ("NA", "", " ", None, math.nan, "nan")}
    assert ids == {"ond||ama||NA"}
    assert make_researcher_id("Ond", None, "U1") == make_researcher_id("Ond", "", "U1")


def test_the_roster_reads_names_and_units_as_written(tmp_path):
    roster = tmp_path / "researcher_index.csv"
    roster.write_text(
        "last_name,first_name,unit,stage\nNULL,None,NA,NA\nOnd,,NA,senior\nVel,Ama,U1,\n",
        encoding="utf-8",
    )
    idx = load_index(roster)
    assert idx["last_name"].tolist() == ["NULL", "Ond", "Vel"]
    assert idx["first_name"].tolist() == ["None", "", "Ama"]
    assert idx["unit"].tolist() == ["NA", "NA", "U1"]
    # the ids the keyword stage stores for the same people
    meta = pd.DataFrame(
        {
            "last_name": ["NULL", "Ond", "Vel"],
            "first_name": ["None", "", "Ama"],
            "unit": ["NA", "NA", "U1"],
        }  # fmt: skip
    )
    assert idx["id"].tolist() == researcher_ids(meta)
    assert idx["stage"].isna().tolist() == [True, False, True]  # other columns: as pandas reads


def test_people_without_a_unit_form_no_group():
    meta = pd.DataFrame({"unit": ["NA", "NA", "NA"]})
    emb = Embeddings(np.zeros((3, 2)), np.zeros((1, 2)), np.zeros((3, 2)), np.zeros((1, 2)))
    labs = aggregate_labs(meta, emb, min_researchers_per_lab=1)
    assert labs.empty and "unit" in labs.columns


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """A small demo project whose people have no organisation, built to the map."""
    root = tmp_path_factory.mktemp("no-unit") / "project"
    write_project(generate("XS", 0), root).close()
    affiliations = root / "sources" / "tables" / "affiliations.parquet"
    table = pq.read_table(affiliations)
    pq.write_table(table.slice(0, 0), affiliations)
    assert cli(["params", str(root), "--set", "pinned_year=2026"]) == 0
    assert cli(["build", str(root)]) == 0
    return root


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_people_without_a_unit_are_on_the_map(built, tmp_path):
    derived = built / "derived"
    roster = _rows(derived / "keywords.build" / "researcher_index.csv")
    assert roster and {r["unit"] for r in roster} == {"NA"}
    placed = _rows(derived / "map.layout" / "umap_individuals.csv")
    # everyone with keywords (all but at most one person of the small world without any)
    assert len(placed) >= len(roster) - 1
    assert not _rows(derived / "map.layout" / "umap_labs.csv")
    people = pd.read_parquet(derived / "themes.apply" / "theme_people.parquet")
    assert people["person_id"].notna().all() and people["person_id"].nunique() == len(placed)

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
    try:
        atlas = Client(app).get("/api/atlas").json()
        assert len(atlas["people"]) == len(placed)
        assert all(p["person_id"] for p in atlas["people"])
    finally:
        app.state.cartolex.shutdown()
