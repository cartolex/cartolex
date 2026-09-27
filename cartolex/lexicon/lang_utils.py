# SPDX-License-Identifier: MIT
"""Language detection for corpus ingestion.

The engine buckets each paragraph into one of the configured *corpus
languages* (``KeywordsConfig.corpus_languages``); paragraphs whose detected
language is not in that set are dropped at extraction.  Detection uses
``langdetect`` for text of reasonable length and a small hand-maintained
function-word marker set as the short-text / no-langdetect fallback.

The ``allowed`` argument is the accepted-language set: a language outside it is
never returned.  With the historical default ``allowed=("fr", "en")`` the
behaviour is byte-for-byte the old FR/EN detector.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

try:
    from langdetect import DetectorFactory as _DetectorFactory
    from langdetect import detect as _detect_lang
except Exception:  # pragma: no cover - optional dependency
    _detect_lang = None


def _seeded_detect(text: str) -> str:
    """langdetect's verdict with its seed fixed.

    Deterministic language detection (0.5.0): langdetect's default is an
    unseeded RNG, so borderline paragraphs could route differently between
    otherwise identical runs. The seed is set when a detection runs (not when
    this module is imported), so importing the engine changes no global state.
    """
    _DetectorFactory.seed = 0
    return _detect_lang(text)


_WORD_RE = re.compile(r"[a-zA-Z]+")

# Function-word markers per language — the short-text / fallback signal only
# (langdetect is the primary signal for real paragraphs).  Romance languages
# share many function words, so markers alone cannot reliably separate e.g.
# FR from PT; that is fine because they only decide short or langdetect-less
# fragments.  Seeded for fr/en/pt; extend here to add a language.
FR_MARKERS = {
    "de",
    "des",
    "du",
    "la",
    "le",
    "les",
    "en",
    "sur",
    "et",
    "dans",
}

EN_MARKERS = {
    "of",
    "the",
    "with",
    "in",
    "and",
    "for",
    "on",
}

PT_MARKERS = {
    "da",
    "do",
    "dos",
    "das",
    "uma",
    "com",
    "para",
    "que",
    "não",
    "aos",
}

# Distinctively-Spanish function words (kept apart from FR/PT where they overlap:
# "el", "los", "las", "del", "y", "una" are the Spanish-specific signal).
ES_MARKERS = {
    "el",
    "los",
    "las",
    "del",
    "y",
    "una",
    "un",
    "con",
    "por",
    "para",
    "que",
    "como",
    "su",
}

_MARKERS: dict[str, set[str]] = {
    "fr": FR_MARKERS,
    "en": EN_MARKERS,
    "pt": PT_MARKERS,
    "es": ES_MARKERS,
}


def supported_languages() -> tuple[str, ...]:
    """Languages with built-in marker support (the short-text fallback)."""
    return tuple(_MARKERS)


# Human-readable names for prompt wording ("prompt in the corpus language").
# Extend as needed; an unknown code echoes upper-cased rather than raising.
LANGUAGE_NAMES = {
    "en": "English",
    "fr": "French",
    "pt": "Portuguese",
    "es": "Spanish",
    "de": "German",
    "it": "Italian",
}


def language_name(code: str) -> str:
    """Human-readable language name for an ISO code; unknown codes echo upper-cased."""
    return LANGUAGE_NAMES.get(code, code.upper())


def _marker_score(tokens: Iterable[str], markers: set[str]) -> int:
    return sum(1 for t in tokens if t in markers)


def detect_language_tokens(
    text: str,
    allowed: tuple[str, ...] = ("fr", "en"),
    default: str = "unknown",
) -> str:
    """Detect the dominant language of a token sequence, restricted to ``allowed``.

    Returns the unique highest-scoring allowed language, or ``default`` when no
    marker matches or the top score is tied (ambiguous).
    """
    tokens = [t.lower() for t in _WORD_RE.findall(text or "")]
    if not tokens:
        return default

    scores = {lang: _marker_score(tokens, _MARKERS.get(lang, set())) for lang in allowed}
    if not scores:
        return default
    top_score = max(scores.values())
    if top_score == 0:
        return default
    winners = [lang for lang, s in scores.items() if s == top_score]
    if len(winners) != 1:
        return default
    return winners[0]


def detect_language_text(
    text: str,
    allowed: tuple[str, ...] = ("fr", "en"),
    default: str = "unknown",
) -> str:
    """Detect the dominant language of a free-text string, restricted to ``allowed``."""
    s = (text or "").strip()
    if not s:
        return default

    # 1. Very short text is unreliable for statistical detection ("Table 1"):
    #    use the marker fallback directly.
    if len(s) < 20:
        return detect_language_tokens(s, allowed=allowed, default=default)

    # 2. langdetect — accept only if it names an allowed language.
    # ALL-CAPS text (short thesis titles, headers) skews the statistical
    # profiles badly; case-normalize it before probing.
    if _detect_lang is not None:
        probe = s.lower() if s.isupper() else s
        try:
            lang = _seeded_detect(probe)
            if lang in allowed:
                return lang
        except Exception:
            pass

    # 3. Fallback: marker scoring over the allowed set.
    return detect_language_tokens(s, allowed=allowed, default=default)


def detect_language_term(
    term: str,
    allowed: tuple[str, ...] = ("fr", "en"),
    default: str = "unknown",
) -> str:
    """Detect the language of a single term, restricted to ``allowed``."""
    return detect_language_tokens(term, allowed=allowed, default=default)
