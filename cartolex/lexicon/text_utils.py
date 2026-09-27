# SPDX-License-Identifier: MIT
from __future__ import annotations

import logging
import re
from collections import Counter

import numpy as np

logger = logging.getLogger(__name__)


def tokenize(term: str) -> list[str]:
    """Split a term into normalised word tokens."""
    return term.strip().lower().split()


# Letter runs of length ≥2 (no digits / underscores), matching how the keyword
# vectorizer tokenises. Used by the split-word healer below.
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def heal_split_words(
    docs: list[str], *, min_real: int = 5, max_passes: int = 2
) -> tuple[list[str], int]:
    """Rejoin words PDF extraction split mid-token (e.g. ``adh esion`` → ``adhesion``).

    ``pypdf`` inserts spurious spaces inside words based on glyph spacing, so a
    word like *adhesion* can come out as ``adh esion``; the keyword vectorizer
    then sees two tokens and the broken form survives as an n-gram into the
    lexicon and every render. This pass heals them **using the corpus as its own
    dictionary** — no external word list:

    - A token is *real* when it occurs at least ``min_real`` times across *docs*.
    - Two adjacent tokens ``a b`` separated by whitespace only are merged into
      ``ab`` when the joined form is a real corpus word **and at least one of**
      ``a`` / ``b`` is **not** real on its own (an orphan fragment — the tell-tale
      of a split). Requiring an orphan protects legitimate adjacent words: when
      both sides are real words (``data set``) nothing is merged.

    Case and surrounding text are preserved; the merge runs up to ``max_passes``
    times so words split into three pieces (``cel l ules``) also heal. Returns
    the healed docs and the number of merges performed.
    """
    freq: Counter[str] = Counter()
    for d in docs:
        freq.update(m.group(0).lower() for m in _WORD_RE.finditer(d))

    def real(w: str) -> bool:
        return freq.get(w.lower(), 0) >= min_real

    def should_merge(a: str, b: str) -> bool:
        return real(a + b) and not (real(a) and real(b))

    total = 0

    def heal_one(text: str) -> str:
        nonlocal total
        for _ in range(max_passes):
            toks = list(_WORD_RE.finditer(text))
            if len(toks) < 2:
                break
            out: list[str] = []
            prev_end = 0
            i = 0
            merged_any = False
            while i < len(toks):
                acc = toks[i].group(0)
                acc_start = toks[i].start()
                acc_end = toks[i].end()
                # Greedily absorb following fragments while the gap is blank.
                j = i + 1
                while j < len(toks):
                    gap = text[acc_end : toks[j].start()]
                    nxt = toks[j].group(0)
                    if gap.strip() == "" and gap != "" and should_merge(acc, nxt):
                        acc = acc + nxt
                        acc_end = toks[j].end()
                        total += 1
                        merged_any = True
                        j += 1
                    else:
                        break
                out.append(text[prev_end:acc_start])
                out.append(acc)
                prev_end = acc_end
                i = j
            out.append(text[prev_end:])
            text = "".join(out)
            if not merged_any:
                break
        return text

    healed = [heal_one(d) for d in docs]
    if total:
        logger.info("Healed %d split-word artifact(s) in the corpus.", total)
    return healed, total


def length_bonus(
    terms: np.ndarray | list[str],
    scores: np.ndarray,
    alpha: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Apply a length bonus to scores.

    The bonus is::

        score_len = score * (1 + alpha * (L - 1))

    where ``L`` is the number of tokens in the term.
    """
    if isinstance(terms, list):
        t_list = terms
    else:
        t_list = terms.tolist()
    lengths = np.array([len(t.split()) for t in t_list], dtype=float)
    scores_len = scores * (1.0 + alpha * (lengths - 1.0))
    return scores_len, lengths
