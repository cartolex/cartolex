# SPDX-License-Identifier: MIT
"""Bilingual subfield label selector."""

from __future__ import annotations

from cartolex.lexicon.subfields import subfield_label


def test_subfield_label_picks_language() -> None:
    sf = {"label": "Active Matter", "label_fr": "Matière active"}
    assert subfield_label(sf, "fr") == "Matière active"
    assert subfield_label(sf, "en") == "Active Matter"


def test_subfield_label_falls_back_when_fr_missing() -> None:
    sf = {"label": "Active Matter"}  # entry with no label_fr
    assert subfield_label(sf, "fr") == "Active Matter"
    assert subfield_label(sf, "en") == "Active Matter"
