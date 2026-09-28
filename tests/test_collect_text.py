# SPDX-License-Identifier: MIT
"""Text hygiene at the collection boundary."""

from __future__ import annotations

import pytest

from cartolex.collect.text import (
    abstract_from_inverted_index,
    clean,
    detect_language,
    strip_markup,
)


def test_clean_normalises_unicode_and_removes_what_cannot_be_stored() -> None:
    decomposed = "Cécile étudie"  # combining accents
    assert clean(decomposed) == "Cécile étudie"
    assert clean("tidal\x00 flats\x07 and​ dunes") == "tidal flats and dunes"
    assert clean("a lone \ud835 half") == "a lone half"
    assert clean("a pair 𝑥 kept") == "a pair \U0001d465 kept"
    assert clean("one\r\ntwo\rthree") == "one\ntwo\nthree"
    assert clean("  spaced   out \t text  \n\n\n\nnext ") == "spaced out text\n\nnext"
    assert clean(None) == "" and clean("") == ""
    assert clean("x").encode("utf-8")


def test_markup_is_stripped_and_its_format_named() -> None:
    text, fmt = strip_markup(
        "<jats:title>Abstract</jats:title><jats:p>Salt marsh <jats:italic>accretion</jats:italic>"
        " rates &amp; sediment supply.</jats:p><jats:p>Second part.</jats:p>"
    )
    assert fmt == "jats"
    assert text == "Salt marsh accretion rates & sediment supply.\n\nSecond part."
    text, fmt = strip_markup("<p>Plain <i>HTML</i> abstract with H<sub>2</sub>O.</p>")
    assert (text, fmt) == ("Plain HTML abstract with H2O.", "jats")
    assert strip_markup("no markup, p < 0.05 and a<b and c>d") == (
        "no markup, p < 0.05 and a<b and c>d",
        "plain",
    )
    text, fmt = strip_markup(r"We solve \emph{shallow water} equations with $u_t + u u_x = 0$.")
    assert fmt == "latex" and "shallow water" in text and r"\emph" not in text
    assert strip_markup("Abstract\nThe text itself.") == ("The text itself.", "plain")
    assert strip_markup(None) == ("", "plain")


def test_an_inverted_index_is_rebuilt_in_word_order() -> None:
    index = {"Tidal": [0], "flats": [1, 5], "store": [2], "carbon;": [3], "mud": [4]}
    assert abstract_from_inverted_index(index) == "Tidal flats store carbon; mud flats"
    assert abstract_from_inverted_index(None) == ""
    assert abstract_from_inverted_index({}) == ""
    for bad in ({"a": [-1]}, {"a": ["0"]}, {"a": 3}, ["a"]):
        with pytest.raises(ValueError):
            abstract_from_inverted_index(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Coastal erosion rates were measured along the sandy shoreline for ten years.", "en"),
        ("Les taux d'érosion du littoral ont été mesurés le long de la côte sableuse.", "fr"),
        ("As taxas de erosão costeira foram medidas ao longo da praia durante dez anos.", "pt"),
        (
            "Las tasas de erosión costera se midieron a lo largo de la playa durante diez años.",
            "es",
        ),
        ("Too short", "und"),
    ],
)
def test_language_detection(text: str, language: str) -> None:
    assert detect_language(text) == language
