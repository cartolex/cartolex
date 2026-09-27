# SPDX-License-Identifier: MIT
"""Person attributes and group labels are generic.

Any column of a corpus index that neither identifies a person nor describes a
document is a person attribute, kept by the roster under its own name; the
group column labels the group aggregates and panels. Synthetic data.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from cartolex.atlas.io import load_index
from cartolex.atlas.types import Embeddings, LexicalData
from cartolex.lexicon.io_helpers import write_roster


def _index(path: Path, rows: list[dict]) -> Path:
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_roster_keeps_every_person_attribute(tmp_path: Path) -> None:
    first = _index(
        tmp_path / "a_index.csv",
        [
            {"last_name": "Ames", "first_name": "Ada", "unit": "G1", "rank": "", "txt_path": "a1.txt", "doc_year": 2020, "source": "a"},
            {"last_name": "Ames", "first_name": "Ada", "unit": "G1", "rank": "r2", "txt_path": "a2.txt", "doc_year": 2021, "source": "a"},
            {"last_name": "Bell", "first_name": "Bo", "unit": "G2", "rank": "r1", "txt_path": "b1.txt", "doc_year": 2019, "source": "a"},
        ],
    )  # fmt: skip
    second = _index(
        tmp_path / "b_index.csv",
        [
            {"last_name": "Ames", "first_name": "Ada", "unit": "G1", "start_year": 2004, "txt_path": "a3.txt", "doc_type": "t"},
            {"last_name": "Cole", "first_name": "Cy", "unit": "G2", "start_year": "", "txt_path": "c1.txt", "doc_type": "t"},
        ],
    )  # fmt: skip
    out = tmp_path / "roster.csv"
    assert write_roster(index_csvs=[first, second], out_csv=out) == 3

    roster = pd.read_csv(out, dtype=str, keep_default_na=False)
    assert list(roster.columns) == ["last_name", "first_name", "unit", "rank", "start_year"]
    by_name = roster.set_index("last_name")
    assert by_name.loc["Ames", "rank"] == "r2"  # the first non-empty value
    assert by_name.loc["Ames", "start_year"] == "2004"
    assert by_name.loc["Bell", "rank"] == "r1" and by_name.loc["Bell", "start_year"] == ""
    assert by_name.loc["Cole", "rank"] == "" and by_name.loc["Cole", "start_year"] == ""


def test_roster_without_attributes_has_the_identity_columns_only(tmp_path: Path) -> None:
    index = _index(
        tmp_path / "manual_index.csv",
        [{"last_name": "Ames", "first_name": "Ada", "unit": "G1", "txt_path": "a.txt", "doc_year": 2020, "doc_type": "t"}],
    )  # fmt: skip
    out = tmp_path / "roster.csv"
    write_roster(index_csvs=[index], out_csv=out)
    assert list(pd.read_csv(out).columns) == ["last_name", "first_name", "unit"]
    meta = load_index(out)
    assert list(meta.columns) == [
        "last_name",
        "first_name",
        "unit",
        "last_name_canon",
        "first_name_canon",
        "id",
    ]


def _persons() -> tuple[LexicalData, Embeddings]:
    meta = pd.DataFrame(
        {
            "last_name": ["A", "B", "C", "D", "E"],
            "first_name": ["V", "W", "X", "Y", "Z"],
            "unit": ["G/2", "G1", "G1", "G/2", "G1"],
            "start_year": [2001, 2008, np.nan, 2015, 2012],
        }
    )
    meta["id"] = [f"p{i}" for i in range(5)]
    xy = np.arange(10, dtype=float).reshape(5, 2)
    data = LexicalData(
        X=np.eye(5), terms=list("abcde"), individuals=list(meta["id"]), meta_ind=meta
    )
    return data, Embeddings(Z_ind=xy, Z_terms=xy, umap_ind=xy, umap_terms=xy)


def test_group_aggregates_are_labelled_by_the_group_column() -> None:
    from cartolex.atlas.plots import aggregate_labs

    data, emb = _persons()
    labs = aggregate_labs(data.meta_ind, emb, min_researchers_per_lab=2)
    assert list(labs["unit"]) == ["G/2", "G1"]
    assert list(labs.columns[:4]) == ["unit", "umap_x", "umap_y", "size"]
    assert list(labs["size"]) == [2, 3]
    assert "sigle" not in labs.columns and "unit_code" not in labs.columns


def test_panels_are_drawn_for_the_requested_groups_only(tmp_path: Path, monkeypatch) -> None:
    from cartolex.atlas import plots

    data, emb = _persons()
    terms = pd.DataFrame(
        {
            "term": list("abcde"),
            "cluster": [0, 0, 1, 1, -1],
            "global_score": [5.0, 4.0, 3.0, 2.0, 1.0],
            "umap_x": emb.umap_terms[:, 0],
            "umap_y": emb.umap_terms[:, 1],
        }
    )
    saved: list[str] = []
    monkeypatch.setattr(plots, "_save_figure", lambda path, **_: saved.append(Path(path).name))
    monkeypatch.setattr(plots, "adjust_text", lambda *a, **k: None)
    panel = lambda key: tmp_path / f"panel_{key}.png"  # noqa: E731

    kw = dict(n_main_clusters=2, n_top_terms_per_main_cluster=2, n_top_terms_per_lab=2)
    plots.plot_lab_panels(data, emb, terms, groups=(), lab_xy_padding=0.02, panel_png=panel, **kw)
    assert saved == []
    plots.plot_lab_panels(
        data, emb, terms, groups=["G/2", "absent"], lab_xy_padding=0.02, panel_png=panel, **kw
    )
    assert saved == ["panel_G_2.png"]


def test_cohorts_come_from_any_numeric_person_column() -> None:
    from cartolex.atlas.plots import compute_cohort_trajectories

    data, _ = _persons()
    traj = pd.DataFrame(
        {
            "researcher_id": ["p0", "p1", "p2", "p3", "p4"],
            "bin_start": [2020] * 5,
            "bin_end": [2022] * 5,
            "umap_x": [0.0, 1.0, 2.0, 3.0, 4.0],
            "umap_y": [1.0, 1.0, 1.0, 1.0, 1.0],
        }
    )
    cohorts = compute_cohort_trajectories(traj, data.meta_ind, cohort_column="start_year")
    assert list(cohorts["cohort_start"]) == [2000, 2010]
    assert list(cohorts["n_researchers"]) == [2, 2]  # p2 has no value
    assert list(cohorts["umap_x"]) == [0.5, 3.5]
    empty = compute_cohort_trajectories(traj, data.meta_ind, cohort_column="absent")
    assert empty.empty
