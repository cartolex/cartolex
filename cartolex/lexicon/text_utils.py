# SPDX-License-Identifier: MIT
from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable

import numpy as np

logger = logging.getLogger(__name__)


def tokenize(term: str) -> list[str]:
    """Split a term into normalised word tokens."""
    return term.strip().lower().split()


#: Words French writes elided before a vowel, with an apostrophe: the article
#: ``l'``, the preposition ``d'`` (Portuguese elides it too: ``d'água``), and
#: the pronouns and conjunctions ``qu'``, ``j'``, ``n'``, ``s'``, ``c'``,
#: ``m'``, ``t'``.
ELIDED_WORDS = frozenset({"l", "d", "qu", "j", "n", "s", "c", "m", "t"})
#: The straight and the typographic apostrophe.
APOSTROPHES = ("'", "’")
# An elided word at the start of a word, before a letter (« d'eau », « L’Atlantique »);
# never inside a word (« aujourd'hui », « presqu'île », « olho-d'água »).
_ELIDED_ALTERNATIVES = "|".join(sorted(ELIDED_WORDS, key=lambda w: (-len(w), w)))
_ELISION = re.compile(
    rf"(?<!\S)({_ELIDED_ALTERNATIVES})([{''.join(APOSTROPHES)}])(?=[^\W\d_])", re.IGNORECASE
)


def split_elision(word: str) -> tuple[str, str] | None:
    """``(elided word with its apostrophe, rest)`` for a word that starts with one, else ``None``.

    ``split_elision("d'água") == ("d'", "água")``; a word with an apostrophe
    inside it (``aujourd'hui``, ``presqu'île``) is one word.
    """
    m = _ELISION.match(word)
    if m is None:
        return None
    return word[: m.end()], word[m.end() :]


def term_words(term: str) -> list[str]:
    """The words of a term as the keyword extraction cuts them, in lower case.

    Words are separated by spaces, and an elided word (:data:`ELIDED_WORDS`,
    straight or typographic apostrophe) is a word of its own: the word after
    it starts a word, as after a space. ``term_words("systèmes d'information
    géographique") == ["systèmes", "d'", "information", "géographique"]``; a
    hyphen or slash compound (``île-barrière``) is one word. It is the inverse
    of :func:`cartolex.lexicon.noun_phrases.join_surface`, which writes no
    space after an elided word.
    """
    return _ELISION.sub(r"\1\2 ", term.strip().lower()).split()


# Letter runs of length ≥2 (no digits / underscores), matching how the keyword
# vectorizer tokenises. Used by the split-word healer below.
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def word_counts(docs: Iterable[str]) -> Counter[str]:
    """How often each word (lower case, letters only) occurs in *docs*: the dictionary
    :func:`heal_split_words` heals with."""
    freq: Counter[str] = Counter()
    for d in docs:
        freq.update(m.group(0).lower() for m in _WORD_RE.finditer(d))
    return freq


def heal_text(text: str, real: Callable[[str], bool], *, max_passes: int = 2) -> tuple[str, int]:
    """*text* with its split words rejoined (see :func:`heal_split_words`), *real* telling
    whether a word (lower case) is a real word of the corpus; returns the text and the
    number of merges."""

    def should_merge(a: str, b: str) -> bool:
        return real((a + b).lower()) and not (real(a.lower()) and real(b.lower()))

    total = 0
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
    return text, total


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
    the healed docs and the number of merges performed. A large corpus counts its
    words (:func:`word_counts`) and heals each text (:func:`heal_text`) apart.
    """
    freq = word_counts(docs)

    def real(w: str) -> bool:
        return freq.get(w, 0) >= min_real

    healed, total = [], 0
    for d in docs:
        text, n = heal_text(d, real, max_passes=max_passes)
        healed.append(text)
        total += n
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
