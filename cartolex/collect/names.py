# SPDX-License-Identifier: MIT
"""People's names: normal forms, the variants a search needs, and how well two names match.

Names are compared in a folded form: accents removed, case ignored, hyphens,
apostrophes and dots read as spaces, and particles (``de``, ``van der``,
``dos``…) set aside. :func:`variants` gives the search strings a resolver
tries for one person: the name as given, without accents, each half of a
compound surname, without particles, and with the first name as an initial.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

__all__ = [
    "PARTICLES",
    "compatible_first_names",
    "fold",
    "name_key",
    "name_similarity",
    "split_full_name",
    "strip_accents",
    "surname_parts",
    "variants",
    "words",
]

#: Words that belong to a surname without identifying it.
PARTICLES = frozenset(
    {
        "d",
        "da",
        "das",
        "de",
        "del",
        "della",
        "den",
        "der",
        "des",
        "di",
        "do",
        "dos",
        "du",
        "e",
        "l",
        "la",
        "le",
        "ten",
        "ter",
        "van",
        "von",
        "y",
    }
)
_SPLIT = re.compile(r"[^0-9a-z]+")
_WORD = re.compile(r"\w+")


def strip_accents(text: str) -> str:
    """*text* without its accents, case kept (``Élodie`` → ``Elodie``)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return unicodedata.normalize(
        "NFC", "".join(c for c in decomposed if not unicodedata.combining(c))
    )


def fold(text: str) -> str:
    """Accent-free, lower-case form of *text*."""
    return strip_accents(text or "").casefold()


def words(text: str) -> list[str]:
    """The folded words of *text* (hyphens, apostrophes and dots separate words)."""
    return [w for w in _SPLIT.split(fold(text)) if w]


def surname_parts(last: str) -> list[str]:
    """The parts of a surname that identify it: its words without particles, in order."""
    return [w for w in words(last) if w not in PARTICLES]


def name_key(last: str, first: str | None) -> str:
    """A key equal for two ways of writing the same name (case, accents, hyphens, particles)."""
    return " ".join(surname_parts(last)) + "|" + " ".join(words(first or ""))


def split_full_name(full: str) -> tuple[str, str]:
    """``(last, first)`` from one full-name cell.

    ``Last, First`` splits at the comma; a name with some words in capitals
    (``TAVELIN Ada``) takes those as the surname; otherwise the last word,
    with the particles just before it, is the surname.
    """
    full = " ".join((full or "").split())
    if not full:
        return "", ""
    if "," in full:
        last, _, first = full.partition(",")
        return last.strip(), first.strip()
    tokens = full.split(" ")
    upper = [t for t in tokens if t.isupper() and sum(c.isalpha() for c in t) > 1]
    if upper and len(upper) < len(tokens):
        last = " ".join(t for t in tokens if t in upper)
        first = " ".join(t for t in tokens if t not in upper)
        return last, first
    if len(tokens) == 1:
        return tokens[0], ""
    cut = len(tokens) - 1
    while cut > 1 and fold(tokens[cut - 1]).strip(".'") in PARTICLES:
        cut -= 1
    return " ".join(tokens[cut:]), " ".join(tokens[:cut])


def _initials(first: str) -> str:
    return " ".join(w[0].upper() for w in re.split(r"[\s\-]+", first.strip()) if w)


def compatible_first_names(a: str | None, b: str | None) -> bool:
    """Whether two first names can be the same person's (equal, or one is the other's initial)."""
    wa, wb = words(a or ""), words(b or "")
    if not wa or not wb:
        return True
    if wa == wb:
        return True
    short, long_ = (wa, wb) if len("".join(wa)) <= len("".join(wb)) else (wb, wa)
    if all(len(w) == 1 for w in short):
        return [w[0] for w in long_][: len(short)] == short or short[0] == long_[0][0]
    return False


def variants(last: str, first: str | None, *, limit: int = 8) -> list[str]:
    """The search strings a resolver tries for one person, most specific first.

    The name as given; without accents; a compound surname whole with a space,
    then each of its parts; without particles; with the first name as an
    initial; then the accent-free forms of these. Two strings that differ only
    in case or punctuation count once.
    """
    first = " ".join((first or "").split())
    last = " ".join((last or "").split())
    out: list[str] = []
    seen: set[tuple[str, ...]] = set()

    def add(text: str) -> None:
        text = " ".join(text.split())
        key = tuple(_WORD.findall(text.lower()))
        if key and key not in seen:
            seen.add(key)
            out.append(text)

    add(f"{first} {last}")
    add(strip_accents(f"{first} {last}"))
    parts = [p for p in re.split(r"[\s\-]+", last) if p and fold(p).strip(".'") not in PARTICLES]
    if len(parts) >= 2:
        add(f"{first} {' '.join(parts)}")
        for part in parts:
            add(f"{first} {part}")
    elif parts and len(parts) < len(last.split()):
        add(f"{first} {' '.join(parts)}")
    if first:
        add(f"{_initials(first)} {last}")
    for text in list(out):
        add(strip_accents(text))
    return out[:limit]


def name_similarity(
    names: Iterable[tuple[str, str | None]], shown: str, alternatives: Sequence[str] = ()
) -> tuple[float, str]:
    """How well a record's name matches one of a person's names; ``(score, reason)``.

    1.0 the same name (accents, case, hyphens and particles aside); 0.85 one
    half of a compound surname; 0.75 the first name as an initial; 0.3 the
    same surname with another first name; 0 otherwise.
    """
    best = (0.0, "another name")
    forms = [shown, *alternatives]
    for last, first in names:
        sur = surname_parts(last)
        given = words(first or "")
        for form in forms:
            got = [w for w in words(form) if w not in PARTICLES]
            if not got or not sur:
                continue
            candidates: list[tuple[float, str]] = []
            if sorted(got) == sorted(sur + given):
                candidates.append((1.0, "same name"))
            if len(sur) >= 2 and given and any(sorted(got) == sorted([p, *given]) for p in sur):
                candidates.append((0.85, "one part of a compound surname"))
            rest = list(got)
            if all(p in rest for p in sur):
                for p in sur:
                    rest.remove(p)
                if rest and given and compatible_first_names(" ".join(rest), " ".join(given)):
                    if all(len(w) == 1 for w in rest):
                        candidates.append((0.75, "the first name as an initial"))
                    else:
                        candidates.append((0.9, "the same name with other first names"))
                elif rest:
                    candidates.append((0.3, "the same surname, another first name"))
            for c in candidates:
                if c[0] > best[0]:
                    best = c
    return best
