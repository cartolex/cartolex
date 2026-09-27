# SPDX-License-Identifier: MIT
"""The one lexical gate every candidate term passes: no web address, no encoding garbage.

The n-gram extraction of earlier versions also filtered its candidates with
packaged stop-word lists (short words, administrative words, place and person
names, generic nouns). The noun-phrase extraction does not: its patterns and
short function-word lists (:mod:`cartolex.lexicon.noun_phrases`) replace them,
because those lists also blocked real terms.
"""

from __future__ import annotations

import re

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

    Catches two classes of strings that are never terms: scheme-less URLs and
    bare domains, and terms containing any character that is neither
    alphanumeric (in any script, so Greek/accented letters are fine) nor
    common scientific punctuation — i.e. mojibake (``¨``), the Unicode
    replacement character, and control characters.
    """
    if _URL_RE.search(term):
        return True
    return any(not (ch.isalnum() or ch in _ALLOWED_PUNCT) for ch in term)
