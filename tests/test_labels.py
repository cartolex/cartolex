# SPDX-License-Identifier: MIT
"""Tests for the keyword-language relabel layer (cartolex/lexicon/labels.py)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from cartolex.lexicon.labels import load_label_map, relabel_terms

#: The refined-pairs file name (any name works: the functions take explicit paths).
PAIRS_FILENAME = "keywords_global_refined_pairs.csv"


def _write_pairs(tmp_path: Path) -> Path:
    pairs = tmp_path / PAIRS_FILENAME
    pd.DataFrame(
        [
            {"concept": "active matter", "term_fr": "matière active", "term_en": "active matter"},
            {
                "concept": "machine learning",
                "term_fr": "apprentissage automatique",
                "term_en": "machine learning",
            },
            # A concept whose FR side is blank → must fall back to the concept string.
            {"concept": "graphene", "term_fr": "", "term_en": "graphene"},
        ]
    ).to_csv(pairs, index=False)
    return pairs


def test_load_label_map_fr(tmp_path: Path) -> None:
    m = load_label_map(_write_pairs(tmp_path), "fr")
    assert m["active matter"] == "matière active"
    assert m["machine learning"] == "apprentissage automatique"


def test_load_label_map_en(tmp_path: Path) -> None:
    m = load_label_map(_write_pairs(tmp_path), "en")
    assert m["active matter"] == "active matter"
    assert m["machine learning"] == "machine learning"


def _write_trilingual_pairs(tmp_path: Path) -> Path:
    pairs = tmp_path / PAIRS_FILENAME
    pd.DataFrame(
        [
            {
                "concept": "matéria ativa",
                "term_fr": "matière active",
                "term_en": "active matter",
                "term_pt": "matéria ativa",
            },
            {
                "concept": "aprendizagem automática",
                "term_fr": "apprentissage automatique",
                "term_en": "machine learning",
                "term_pt": "aprendizagem automática",
            },
        ]
    ).to_csv(pairs, index=False)
    return pairs


def test_load_label_map_third_display_language(tmp_path: Path) -> None:
    # A Portuguese display skin must be selectable, not silently resolved to
    # the English column (the old `term_fr if fr else term_en` ternary).
    m = load_label_map(_write_trilingual_pairs(tmp_path), "pt")
    assert m["matéria ativa"] == "matéria ativa"
    m_fr = load_label_map(_write_trilingual_pairs(tmp_path), "fr")
    assert m_fr["matéria ativa"] == "matière active"


def test_load_label_map_blank_falls_back_to_concept(tmp_path: Path) -> None:
    m = load_label_map(_write_pairs(tmp_path), "fr")
    assert m["graphene"] == "graphene"


def test_load_label_map_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_label_map(tmp_path / PAIRS_FILENAME, "fr") == {}


def test_relabel_terms_maps_known_leaves_unknown(tmp_path: Path) -> None:
    df = pd.DataFrame({"term": ["active matter", "unknown thing"], "score": [1.0, 2.0]})
    label_map = {"active matter": "matière active"}
    out = relabel_terms(df, "term", label_map)
    assert list(out["term"]) == ["matière active", "unknown thing"]
    # Original is not mutated.
    assert list(df["term"]) == ["active matter", "unknown thing"]
    # Other columns preserved.
    assert list(out["score"]) == [1.0, 2.0]
