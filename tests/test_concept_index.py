# SPDX-License-Identifier: MIT
"""Tests for the concept→term/colour helpers shared by all UMAP surfaces.

Synthetic hierarchy docs only — no real data.
"""

from __future__ import annotations

from cartolex.lexicon.subfields import (
    concept_centroids_from_coords,
    concept_shade,
    concept_term_index,
    subfield_color,
)


def _doc() -> dict:
    return {
        "subfields": [
            {"id": 0, "label": "Quantum", "color": "#e6194b"},
            {"id": 1, "label": "Optics"},  # no persisted colour → palette fallback
        ],
        "concepts": [
            {
                "id": 0,
                "label": "Spintronics",
                "label_fr": "Spintronique",
                "subfield_id": 0,
                "term_indices": [0, 1],
                "color": "#112233",
            },
            {"id": 1, "label": "Lasers", "subfield_id": 1, "term_indices": [2]},
            # index 99 is out of coordinate range → centroid must skip it
            {"id": 2, "label": "Fibres", "subfield_id": 1, "term_indices": [3, 99]},
        ],
    }


def test_concept_term_index_maps_terms_and_colors() -> None:
    term_to_cid, info = concept_term_index(_doc())
    assert term_to_cid == {0: 0, 1: 0, 2: 1, 3: 2, 99: 2}
    assert info[0]["color"] == "#112233"  # persisted colour wins
    assert info[0]["subfield_id"] == 0 and info[0]["label_fr"] == "Spintronique"
    # Fallback: shade of the subfield colour, position-stable within the subfield.
    base = subfield_color(1)
    assert info[1]["color"] == concept_shade(base, 0, 2)
    assert info[2]["color"] == concept_shade(base, 1, 2)
    assert info[2]["color"] != info[1]["color"]


def test_concept_term_index_degrades_on_stale_doc() -> None:
    assert concept_term_index(None) == ({}, {})
    assert concept_term_index({"subfields": [{"id": 0}]}) == ({}, {})  # no concepts
    # Pre-schema concepts (no term_indices anywhere) → empty maps.
    stale = {"subfields": [{"id": 0}], "concepts": [{"id": 0, "label": "C"}]}
    assert concept_term_index(stale) == ({}, {})


def test_concept_term_index_skips_dropped_subfields() -> None:
    doc = _doc()
    doc["subfields"][1]["keep"] = False
    term_to_cid, info = concept_term_index(doc)
    assert set(info) == {0}
    assert set(term_to_cid.values()) == {0}


def test_concept_centroids_from_coords() -> None:
    xs = [0.0, 2.0, 5.0, 7.0]
    ys = [0.0, 4.0, 5.0, 7.0]
    cents = concept_centroids_from_coords(_doc(), xs, ys)
    by_id = {c["id"]: c for c in cents}
    assert by_id[0]["x"] == 1.0 and by_id[0]["y"] == 2.0 and by_id[0]["n"] == 2
    assert by_id[1]["x"] == 5.0 and by_id[1]["n"] == 1
    assert by_id[2]["n"] == 1  # out-of-range index 99 skipped
    assert all(c["color"].startswith("#") for c in cents)
    assert all("label" in c and "subfield_id" in c for c in cents)


def test_concept_centroids_skip_nan_and_degrade() -> None:
    xs = [float("nan"), 2.0, 5.0, 7.0]
    ys = [0.0, 4.0, 5.0, 7.0]
    by_id = {c["id"]: c for c in concept_centroids_from_coords(_doc(), xs, ys)}
    assert by_id[0]["x"] == 2.0 and by_id[0]["n"] == 1  # NaN row skipped
    assert concept_centroids_from_coords(None, xs, ys) == []
    assert concept_centroids_from_coords({}, xs, ys) == []
