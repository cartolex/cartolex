# SPDX-License-Identifier: MIT
"""Tests for the shared name-matching helpers."""

from cartolex.lexicon.name_match import name_similarity, normalize_for_match, tokenize_name


def test_normalize_strips_accents_and_punctuation():
    assert normalize_for_match("Crécy-Müller") == "crecy muller"


def test_tokenize_drops_empties():
    assert tokenize_name("  Jean--Paul  ") == ["jean", "paul"]


def test_exact_name_scores_high():
    assert name_similarity("Durand", "Marie", "Durand", "Marie") > 0.95


def test_different_people_score_low():
    assert name_similarity("Durand", "Marie", "Lefevre", "Pierre") < 0.5
