# SPDX-License-Identifier: MIT
"""N-language detection (cartolex/lexicon/lang_utils.py).

The FR/EN defaults must be preserved; Portuguese (and any language in the
``allowed`` set) must be detectable; a language outside ``allowed`` must never
be returned.
"""

from __future__ import annotations

from cartolex.lexicon.lang_utils import (
    detect_language_text,
    detect_language_tokens,
    supported_languages,
)

# Make langdetect deterministic for the test run (library default is seeded
# from a PRNG, which would make assertions flaky).
try:  # pragma: no cover - only when langdetect is installed
    from langdetect import DetectorFactory

    DetectorFactory.seed = 0
except Exception:  # pragma: no cover
    pass

FR = "apprentissage automatique et réseaux de neurones pour la classification supervisée des images"
EN = "machine learning and neural networks for the supervised classification of images in practice"
PT = "aprendizagem automática e redes neuronais para a classificação supervisionada de imagens digitais"


def test_french_and_english_defaults_preserved() -> None:
    assert detect_language_text(FR) == "fr"
    assert detect_language_text(EN) == "en"


def test_portuguese_detected_when_allowed() -> None:
    assert detect_language_text(PT, allowed=("pt", "en")) == "pt"


ES = (
    "aprendizaje automático y redes neuronales para la clasificación supervisada "
    "de imágenes digitales en grandes conjuntos de datos"
)


def test_spanish_detected_when_allowed() -> None:
    assert detect_language_text(ES, allowed=("es", "en")) == "es"


def test_spanish_markers_detected_short_text() -> None:
    assert detect_language_tokens("modelos del sistema y una red", allowed=("es", "en")) == "es"


def test_language_outside_allowed_is_never_returned() -> None:
    # Portuguese text, but only fr/en are accepted → must NOT surface "pt".
    assert detect_language_text(PT, allowed=("fr", "en")) != "pt"


def test_tokens_with_no_marker_hits_returns_default() -> None:
    assert (
        detect_language_tokens("zzz qwerty vbnm", allowed=("fr", "en"), default="unknown")
        == "unknown"
    )


def test_tokens_detect_portuguese_markers() -> None:
    # Short marker-only fallback path: Portuguese function words.
    assert detect_language_tokens("modelos de dados e uma rede", allowed=("pt", "en")) == "pt"


def test_supported_languages_includes_seed_set() -> None:
    langs = supported_languages()
    assert {"fr", "en", "pt"} <= set(langs)


def test_all_caps_text_case_normalised_before_langdetect(monkeypatch) -> None:
    """Pilot feedback: short ALL-CAPS titles confuse the statistical detector.
    All-caps input must be lower-cased before it reaches langdetect."""
    import cartolex.lexicon.lang_utils as lu

    seen: list[str] = []

    def recorder(s: str) -> str:
        seen.append(s)
        return "pt"

    monkeypatch.setattr(lu, "_detect_lang", recorder)
    caps = "MUDANÇAS CLIMÁTICAS E POLÍTICA MONETÁRIA NO BRASIL CONTEMPORÂNEO"
    assert lu.detect_language_text(caps, allowed=("pt", "en")) == "pt"
    assert seen and seen[0] == caps.lower()


def test_language_name_maps_codes_to_names() -> None:
    from cartolex.lexicon.lang_utils import language_name

    assert language_name("en") == "English"
    assert language_name("fr") == "French"
    assert language_name("pt") == "Portuguese"


def test_language_name_falls_back_to_uppercased_code() -> None:
    from cartolex.lexicon.lang_utils import language_name

    # An unknown code is echoed (upper-cased) rather than raising.
    assert language_name("xx") == "XX"
