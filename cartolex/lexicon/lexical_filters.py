# SPDX-License-Identifier: MIT
from __future__ import annotations

import re

import pandas as pd

from .text_utils import tokenize

# Short tokens that are valid in scientific contexts
SCIENTIFIC_SHORT_TOKENS = frozenset(
    {
        "ph",
        "uv",
        "ir",
        "1d",
        "2d",
        "3d",
        "4d",
        "5d",
        "nm",
        "ev",
        "qm",
        "ml",
        "ai",
        "ab",
        "pi",
        "gw",
        "rh",
        "co",
        "ii",
        "iv",
        "vi",
        "md",
        "mc",
        "qc",
        "h2",
        "o2",
        "n2",
        "co2",
        "h2o",
        "li",
        "na",
        "si",
        "he",
        "fe",
        "ni",
        "zn",
        "cd",
        "sn",
        "pb",
        "cu",
        "au",
        "ag",
        "pt",
        "mn",
        "cr",
        "ti",
        "al",
        "mg",
        "ga",
        "ge",
        "as",
        "se",
        "br",
        "nb",
        "mo",
        "ru",
    }
)


def matches_admin_pattern(term: str, admin_patterns: list[str]) -> bool:
    """Return True if a term matches an administrative-noise pattern."""
    return any(re.search(p, term) for p in admin_patterns)


def matches_junk(term: str, junk_patterns: list[str]) -> bool:
    """Return True if a term matches a junk (non-scientific) pattern."""
    return any(re.search(p, term) for p in junk_patterns)


def any_blacklist_token(
    term: str,
    blacklist: set[str],
    admin_patterns: list[str],
    junk_patterns: list[str],
) -> bool:
    """Return True if any token of the term is blacklisted or matches a noise pattern."""
    tokens = tokenize(term)
    return (
        any(t in blacklist for t in tokens)
        or matches_admin_pattern(term, admin_patterns)
        or matches_junk(term, junk_patterns)
    )


def is_garbage(term: str) -> bool:
    """Reject terms where non-midword tokens are too short to be meaningful,
    unless they are known scientific abbreviations."""
    tokens = tokenize(term)
    return any(len(t) <= 2 and t not in SCIENTIFIC_SHORT_TOKENS for t in tokens)


# Web/URL junk: an explicit scheme, a ``www.`` host, or a bare ``name.tld`` domain
# (catches scheme-less links like ``en.wikipedia.org/wiki/x`` that ``https?`` misses).
_URL_RE = re.compile(
    r"://|\bwww\.|\b[\w-]+\.(?:com|org|net|edu|gov|io|fr|de|uk|eu|info|biz|wiki)\b",
    re.IGNORECASE,
)
# Punctuation legitimately seen inside scientific terms (everything else non-alphanumeric
# is treated as encoding garbage: mojibake symbols, the U+FFFD replacement char, controls).
_ALLOWED_PUNCT = frozenset(" -–/+().,'’&:°%·")


def is_malformed_term(term: str) -> bool:
    """Reject web/URL junk and character-encoding garbage (config-independent gate).

    Catches two classes the configurable filters miss: scheme-less URLs / bare domains,
    and terms containing any character that is neither alphanumeric (in any script, so
    Greek/accented letters are fine) nor common scientific punctuation — i.e. mojibake
    (``¨``), the Unicode replacement character, and control characters.
    """
    if _URL_RE.search(term):
        return True
    return any(not (ch.isalnum() or ch in _ALLOWED_PUNCT) for ch in term)


def valid_midwords(term: str, midwords: set[str]) -> bool:
    """
    Midwords (de, des, of, the, ...) are allowed only inside a term, not
    at the beginning or end.
    """
    tokens = tokenize(term)
    if not tokens:
        return False
    idx_mid = [i for i, t in enumerate(tokens) if t in midwords]
    if not idx_mid:
        return True
    return all(0 < i < len(tokens) - 1 for i in idx_mid)


def single_blacklist_unigram(term: str, single_blacklist: set[str]) -> bool:
    """Return True if a single-token term is in the unigram blacklist."""
    tokens = tokenize(term)
    return len(tokens) == 1 and tokens[0] in single_blacklist


def is_name_term(term: str, names_set: set[str]) -> bool:
    """
    True if all tokens are names from the index.
    """
    tokens = tokenize(term)
    return bool(tokens) and all(t in names_set for t in tokens)


def filter_global_terms(
    global_df: pd.DataFrame,
    names: set[str],
    blacklist: set[str],
    midwords: set[str],
    single_blacklist: set[str],
    admin_patterns: list[str],
    junk_patterns: list[str],
) -> pd.DataFrame:
    """
    Apply hard lexical filters to a global candidates list.
    """
    # A missing/NaN ``term`` is not a term. Drop it before the per-term gates: pandas >= 3
    # ``read_csv`` yields the string dtype whose NA sentinel is a float ``nan`` that
    # ``astype(str)`` does not stringify, so a blank cell in an older ``keywords_global.csv``
    # would otherwise reach ``is_malformed_term`` as a float and raise ``TypeError``.
    global_df = global_df[global_df["term"].notna()].copy()
    global_df["term"] = global_df["term"].astype(str)

    # Reject terms that are purely numeric or look like admin codes (e.g. "2021", "75001")
    # but allow terms that merely contain digits (e.g. "h2o", "covid19", "2d materials")
    mask_no_pure_digits = ~global_df["term"].str.fullmatch(r"[\d\s]+")

    mask_keep = (
        mask_no_pure_digits
        & (~global_df["term"].apply(is_malformed_term))
        & (~global_df["term"].apply(lambda t: is_name_term(t, names)))
        & (
            ~global_df["term"].apply(
                lambda t: any_blacklist_token(t, blacklist, admin_patterns, junk_patterns)
            )
        )
        & (~global_df["term"].apply(is_garbage))
        & (global_df["term"].apply(lambda t: valid_midwords(t, midwords)))
        & (~global_df["term"].apply(lambda t: single_blacklist_unigram(t, single_blacklist)))
    )

    mask_keep = pd.Series(mask_keep, index=global_df.index)
    return global_df.loc[mask_keep].copy()
