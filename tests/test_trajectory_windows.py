# SPDX-License-Identifier: MIT
"""Tests for exact per-window trajectory reprojection (Atlas time machine).

All names and text are fabricated.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from cartolex.atlas.driver import _load_per_document_corpus
from cartolex.atlas.trajectories import TrajectoryData, build_trajectory_windows


class _IdentitySVD:
    def transform(self, X):
        return np.asarray(X, dtype=float)


class _FirstTwoDims:
    """Stands for the map's anchors: a point's position is its first two coordinates."""

    def __init__(self) -> None:
        self.calls = 0

    def place(self, Z):
        self.calls += 1
        return np.asarray(Z, dtype=float)[:, :2]


def _traj_two_bins() -> TrajectoryData:
    # One researcher, two bins, 3 ref-terms. Older bin = term0; recent bin = term1.
    meta = pd.DataFrame(
        [
            {
                "researcher_id": "r1",
                "last_name": "A",
                "first_name": "X",
                "unit": "L",
                "bin_start": 2018,
                "bin_end": 2020,
                "n_docs": 1,
                "top_terms": "t0",
            },
            {
                "researcher_id": "r1",
                "last_name": "A",
                "first_name": "X",
                "unit": "L",
                "bin_start": 2021,
                "bin_end": 2023,
                "n_docs": 1,
                "top_terms": "t1",
            },
        ]
    )
    B = np.array([[2.0, 0.0, 0.0], [0.0, 1.0, 0.0]])  # masses 2.0 and 1.0
    return TrajectoryData(B=B, meta=meta, terms=["t0", "t1", "t2"])


def test_build_trajectory_windows_contiguous_keys_and_mass() -> None:
    traj = _traj_two_bins()
    # Evidence-based lexicon: t0 → concept 12 → subfield 3; t1 → concept 40 → subfield 4.
    term_to_concept = {"t0": 12, "t1": 40}
    concept_to_subfield = {12: 3, 40: 4}

    anchors = _FirstTwoDims()
    out = build_trajectory_windows(
        traj,
        svd_model=_IdentitySVD(),
        anchors=anchors,
        term_to_concept=term_to_concept,
        concept_to_subfield=concept_to_subfield,
    )
    assert anchors.calls == 1  # every window placed in one call

    assert set(out) == {"r1"}
    keys = {e["key"] for e in out["r1"]}
    # Three contiguous runs over two bins: [bin0], [bin1], [bin0..bin1].
    assert keys == {"2018_2020", "2021_2023", "2018_2023"}

    by_key = {e["key"]: e for e in out["r1"]}
    assert by_key["2018_2020"]["mass"] == 2.0
    assert by_key["2021_2023"]["mass"] == 1.0
    assert by_key["2018_2023"]["mass"] == 3.0

    # Older-only window used only t0 → pure subfield 3; recent-only → pure subfield 4.
    assert by_key["2018_2020"]["subfields"][0]["id"] == 3
    assert by_key["2021_2023"]["subfields"][0]["id"] == 4
    # Full span: t0 mass 2 vs t1 mass 1 → subfield 3 dominates 2/3 over subfield 4.
    full = {s["id"]: s["weight"] for s in by_key["2018_2023"]["subfields"]}
    assert full == {3: round(2 / 3, 4), 4: round(1 / 3, 4)}
    # Each entry carries a 2-D position (here its vector's first two coordinates, normalised)
    # and normalised subfield weights.
    assert (by_key["2018_2020"]["x"], by_key["2018_2020"]["y"]) == (1.0, 0.0)
    assert (by_key["2021_2023"]["x"], by_key["2021_2023"]["y"]) == (0.0, 1.0)
    for e in out["r1"]:
        assert list(e) == ["key", "mass", "x", "y", "subfields", "concepts"]
        assert abs(sum(s["weight"] for s in e["subfields"]) - 1.0) < 1e-6


def test_build_trajectory_windows_empty() -> None:
    empty = TrajectoryData(B=np.zeros((0, 3)), meta=pd.DataFrame(), terms=["t0", "t1", "t2"])
    assert (
        build_trajectory_windows(
            empty,
            svd_model=_IdentitySVD(),
            anchors=_FirstTwoDims(),
            term_to_concept={},
            concept_to_subfield={},
        )
        == {}
    )


def _index(path: Path, rows: list[tuple[str, str, str]], *, with_type: bool = True) -> Path:
    """Write a per-document index of (name, year, type) rows and their texts."""
    records = []
    for i, (name, year, doc_type) in enumerate(rows):
        text = path.parent / f"{path.stem}_{i}.txt"
        text.write_text(f"text of {name} {year}", encoding="utf-8")
        record = {"last_name": name, "first_name": "F", "unit": "", "doc_year": year}
        if with_type:
            record["doc_type"] = doc_type
        records.append({**record, "txt_path": text.name})
    pd.DataFrame(records).to_csv(path, index=False)
    return path


def test_trajectories_read_every_trajectory_slot_in_order(tmp_path: Path) -> None:
    """Each slot's types filter its own rows; an index without a type column is read whole."""
    first = _index(
        tmp_path / "first_index.csv",
        [("A", "2020", "annual"), ("B", "2021", "admin"), ("C", "2022", "")],
    )
    second = _index(tmp_path / "second_index.csv", [("D", "2023", "")], with_type=False)
    docs = _load_per_document_corpus(
        [
            ("first", first, ("annual",)),
            ("second", second, None),
            ("absent", tmp_path / "absent_index.csv", None),
        ]
    )
    assert docs["last_name"].tolist() == ["A", "C", "D"]
    assert docs["unit"].tolist() == ["", "", ""]
    assert docs["text"].tolist()[0] == "text of A 2020"
