# SPDX-License-Identifier: MIT
"""The configurable language model on KeywordsConfig.

Defaults must reproduce the historical hardwired FR/EN + English-pivot
behaviour (zero migration); a Portuguese workspace is expressible; obvious
misconfigurations fail loudly.
"""

from __future__ import annotations

import pytest

from cartolex.lexicon.config import KeywordsConfig, SettingsError


def test_defaults_reproduce_fr_en_english_pivot() -> None:
    cfg = KeywordsConfig()
    assert cfg.reference_language == "en"
    assert cfg.corpus_languages == ("fr", "en")
    assert cfg.display_languages == ("fr", "en")


def test_portuguese_workspace_is_expressible() -> None:
    cfg = KeywordsConfig(
        reference_language="pt",
        corpus_languages=("pt", "en"),
        display_languages=("pt", "en"),
    )
    assert cfg.reference_language == "pt"
    assert cfg.corpus_languages == ("pt", "en")
    assert cfg.display_languages == ("pt", "en")


def test_reference_language_need_not_be_a_corpus_language() -> None:
    # An all-Portuguese corpus that keeps an English pivot (the "keep English
    # pivot" model as a special case) is valid.
    cfg = KeywordsConfig(reference_language="en", corpus_languages=("pt",))
    assert cfg.reference_language == "en"
    assert cfg.corpus_languages == ("pt",)


def test_language_codes_are_normalised_to_lowercase() -> None:
    cfg = KeywordsConfig(
        reference_language="PT",
        corpus_languages=("PT", "En"),
        display_languages=("Pt",),
    )
    assert cfg.reference_language == "pt"
    assert cfg.corpus_languages == ("pt", "en")
    assert cfg.display_languages == ("pt",)


def test_empty_corpus_languages_rejected() -> None:
    with pytest.raises(SettingsError, match="language"):
        KeywordsConfig(corpus_languages=())


def test_blank_reference_language_rejected() -> None:
    with pytest.raises(SettingsError, match="language"):
        KeywordsConfig(reference_language="")


def test_empty_display_languages_rejected() -> None:
    with pytest.raises(SettingsError, match="language"):
        KeywordsConfig(display_languages=())


def test_corpus_languages_are_those_with_a_language_model() -> None:
    """Any subset of the six languages with a model; another language fails at once."""
    assert KeywordsConfig(corpus_languages=("pt", "fr", "en")).corpus_languages == (
        "pt",
        "fr",
        "en",
    )
    assert KeywordsConfig(corpus_languages=("es", "de", "it")).corpus_languages == (
        "es",
        "de",
        "it",
    )
    with pytest.raises(SettingsError, match=r"'nl'.*en, fr, pt, es, de, it"):
        KeywordsConfig(corpus_languages=("nl", "en"))
    # Display and reference languages are labels, not parsed text: any code goes.
    assert KeywordsConfig(display_languages=("es",), reference_language="de")
