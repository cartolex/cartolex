# SPDX-License-Identifier: MIT
"""Diacritic-insensitive person-name matching helpers (shared).

One matching logic for every caller: a consuming application and the pipeline
(document attribution, positioning).
"""

from __future__ import annotations

import re
import unicodedata as _ud
from difflib import SequenceMatcher


def normalize_for_match(s: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace/punctuation for matching."""
    nfkd = _ud.normalize("NFKD", s or "")
    no_acc = "".join(ch for ch in nfkd if not _ud.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", no_acc.lower()).strip()


def tokenize_name(s: str) -> list[str]:
    """Tokenize a normalized name string, dropping empties."""
    return [tok for tok in normalize_for_match(s).split(" ") if tok]


def name_similarity(
    ann_last: str,
    ann_first: str,
    marm_last: str,
    marm_first: str,
) -> float:
    """Return a robust similarity score in [0, 1] for person-name matching.

    We mix last-name and first-name string similarity with full-name similarity,
    add token overlap, and add a small bonus for same first initial.
    """
    ann_ln = normalize_for_match(ann_last)
    ann_fn = normalize_for_match(ann_first)
    mar_ln = normalize_for_match(marm_last)
    mar_fn = normalize_for_match(marm_first)

    if not ann_ln or not mar_ln:
        return 0.0

    last_score = SequenceMatcher(None, ann_ln, mar_ln).ratio()
    first_score = SequenceMatcher(None, ann_fn, mar_fn).ratio() if ann_fn and mar_fn else 0.0
    full_ann = f"{ann_fn} {ann_ln}".strip()
    full_mar = f"{mar_fn} {mar_ln}".strip()
    full_score = SequenceMatcher(None, full_ann, full_mar).ratio()

    ann_tokens = set(tokenize_name(full_ann))
    mar_tokens = set(tokenize_name(full_mar))
    inter = len(ann_tokens & mar_tokens)
    union = len(ann_tokens | mar_tokens)
    token_score = (inter / union) if union else 0.0

    # First initial bonus helps with cases like "J.-P." vs "Jean Paul".
    bonus = 0.0
    if ann_fn and mar_fn and ann_fn[0] == mar_fn[0]:
        bonus += 0.06
    if ann_ln == mar_ln:
        bonus += 0.06

    score = (0.45 * last_score) + (0.20 * first_score) + (0.25 * full_score) + (0.10 * token_score)
    return min(1.0, score + bonus)
