# SPDX-License-Identifier: MIT
"""What the HAL and SciELO finders share: the people they look for, names, reports.

A finder looks for the works of people already in the tables
(:func:`people_refs` reads them), in a publication-year window passed
explicitly. It stores what the service returned in the slot's raw folder, one
run per job; what it only *proposes* (works found by a name, never accepted
on their own) goes to a run of candidates, from which no table row is built.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cartolex.project.layout import ProjectLayout
from cartolex.project.tables import read_source_table

__all__ = [
    "FinderReport",
    "add_parts",
    "PersonRef",
    "Window",
    "check_window",
    "fold",
    "name_matches",
    "normalise_doi",
    "normalise_title",
    "people_refs",
    "ranks",
]

#: A publication-year window: first and last year, inclusive.
Window = tuple[int, int]

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")
_WORD = re.compile(r"[^\W_]+")


def check_window(window: Window) -> Window:
    """*window* as two ordered years; refuses anything else."""
    try:
        first, last = (int(y) for y in window)
    except (TypeError, ValueError):
        raise ValueError(f"a window is two years, not {window!r}") from None
    if first > last:
        raise ValueError(f"the window starts ({first}) after it ends ({last})")
    return first, last


def fold(text: str | None) -> str:
    """Lower case, accents removed, words separated by single spaces (for comparing names)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(_WORD.findall(plain))


def normalise_doi(value: str | None) -> str | None:
    """A DOI in lower case, without ``https://doi.org/`` or ``doi:``; ``None`` when empty."""
    if not value:
        return None
    text = str(value).strip()
    lowered = text.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    else:
        if lowered.startswith("doi:"):
            text = text[4:]
    text = text.strip().lower()
    return text if text.startswith("10.") and "/" in text else None


def normalise_title(title: str | None) -> str:
    """A title for comparing: folded, words only, single spaces."""
    return fold(title)


@dataclass(frozen=True)
class PersonRef:
    """A person a finder looks for: their id, names, and the identifiers finders use."""

    person_id: str
    last_name: str
    first_name: str | None = None
    orcid: str | None = None
    idhal: tuple[str, ...] = ()
    aliases: tuple[tuple[str, str | None], ...] = ()

    def names(self) -> list[tuple[str, str | None]]:
        """``(last, first)`` forms: the person's own, then every alias."""
        out = [(self.last_name, self.first_name)]
        out += [a for a in self.aliases if a not in out]
        return out


def people_refs(
    layout: ProjectLayout, *, person_ids: Iterable[str] | None = None
) -> list[PersonRef]:
    """The people of the project's tables, as finders take them, in ``person_id`` order.

    The idHAL of a person is read from ``ids["idhal"]`` (or ``ids["hal"]``).
    """
    path = layout.table("people")
    if not path.exists():
        return []
    wanted = set(person_ids) if person_ids is not None else None
    out = []
    for row in read_source_table(path, "people").to_pylist():
        if wanted is not None and row["person_id"] not in wanted:
            continue
        ids = dict(row["ids"] or [])
        idhal = tuple(ids.get("idhal") or ids.get("hal") or ())
        aliases = tuple((a["last_name"], a["first_name"]) for a in row["aliases"] or [])
        out.append(
            PersonRef(
                person_id=row["person_id"],
                last_name=row["last_name"],
                first_name=row["first_name"],
                orcid=row["orcid"],
                idhal=idhal,
                aliases=aliases,
            )
        )
    return sorted(out, key=lambda p: p.person_id)


def _first_names_agree(a: str | None, b: str | None) -> bool:
    """Same first names, or one is the initials of the other (``J.`` and ``Jane``)."""
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return True
    if fa == fb:
        return True
    wa, wb = fa.split(), fb.split()
    short, long_ = (wa, wb) if len(" ".join(wa)) <= len(" ".join(wb)) else (wb, wa)
    if all(len(w) == 1 for w in short):
        return [w[0] for w in long_[: len(short)]] == short
    return False


def name_matches(person: PersonRef, first: str | None, last: str | None) -> bool:
    """Whether a printed name (*first*, *last*) can be one of *person*'s names.

    Last names must be equal once folded; first names equal, or given as
    initials. Used to *propose* candidates, never to accept a work.
    """
    last_f = fold(last)
    if not last_f:
        return False
    return any(
        fold(p_last) == last_f and _first_names_agree(p_first, first)
        for p_last, p_first in person.names()
    )


@dataclass
class FinderReport:
    """What one finder job did.

    *found* counts the works kept per person; *candidates* lists what was only
    proposed (a name match); *failures* names the people whose search failed and
    why; *runs* are the raw runs written. The HTTP client's egress record says
    what left the computer.
    """

    finder: str
    window: Window
    people: int = 0
    works: int = 0
    found: dict[str, int] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    runs: list[Path] = field(default_factory=list)
    stopped: str | None = None

    def skip(self, why: str, n: int = 1) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + n


def ranks(names: Sequence[str], wanted: str) -> int | None:
    """The 1-based rank of *wanted* among *names*, folded; ``None`` when absent."""
    key = fold(wanted)
    for i, name in enumerate(names, start=1):
        if fold(name) == key:
            return i
    return None


def add_parts(
    builder: Any,
    text_id: str,
    part: str,
    by_language: Iterable[tuple[str | None, str]],
    *,
    provider: str,
    retrieved_at: Any,
) -> int:
    """Clean and store the *part* of a text in each language given; returns how many were kept.

    A language the service declares is kept when it is one cartolex detects;
    otherwise the language is detected from the cleaned text (``und`` when unsure).
    """
    from .text import DETECTED_LANGUAGES, detect_language, strip_markup

    kept = 0
    for lang, raw in by_language:
        content, fmt = strip_markup(raw)
        if not content:
            continue
        declared = (lang or "").lower()[:2]
        language = declared if declared in DETECTED_LANGUAGES else detect_language(content)
        builder.part(
            text_id,
            part=part,
            language=language,
            provider=provider,
            content=content,
            retrieved_at=retrieved_at,
            format=fmt,
        )
        kept += 1
    return kept
