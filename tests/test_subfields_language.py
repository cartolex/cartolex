# SPDX-License-Identifier: MIT
"""Reference-language-aware subfield labels.

``label`` holds the reference-language form; every *other* display language
gets a ``label_<lang>`` field. With the FR/EN defaults this reproduces the
historical ``label`` (English) + ``label_fr`` contract byte-for-byte.
"""

from __future__ import annotations

from cartolex.lexicon.subfields import subfield_label


def test_subfield_label_backcompat_english_reference() -> None:
    sf = {"label": "Active Matter", "label_fr": "Matière active"}
    assert subfield_label(sf, "en") == "Active Matter"
    assert subfield_label(sf, "fr") == "Matière active"


def test_subfield_label_with_portuguese_reference() -> None:
    sf = {"label": "Matéria Ativa", "label_en": "Active Matter"}
    assert subfield_label(sf, "pt", reference_language="pt") == "Matéria Ativa"
    assert subfield_label(sf, "en", reference_language="pt") == "Active Matter"


def test_subfield_label_falls_back_to_reference_label() -> None:
    sf = {"label": "Matéria Ativa"}  # no other-language skins yet
    assert subfield_label(sf, "en", reference_language="pt") == "Matéria Ativa"
