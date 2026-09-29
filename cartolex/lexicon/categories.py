# SPDX-License-Identifier: MIT
"""Keyword categories: what an accepted keyword names, and why a rejected one is not a keyword.

Every AI route (the triage by API, the handoff, the copilot) answers each
candidate with a one-letter **code**; each code belongs to one **category**:

========  ==========  ==================================================
code      category    meaning
========  ==========  ==================================================
``C``     concept     a concept, phenomenon, process, property or theory
``M``     method      a method, technique, instrument, model or data source
``O``     object      an object of study: a material, organism, system
``P``     place       a kind of place or setting studied as such
``D``     field       the name of a discipline or field
``K``     never       administrative, career or project wording
``G``     never       a generic word of academic writing
``F``     never       a broken piece, a fragment, boilerplate
``N``     here        a name: a person, a particular place, an institution
``H``     here        a real term, but not informative in this field
========  ==========  ==================================================

``never`` means never informative, in any field, and the judge is sure of it:
such answers may go to the rejection cache (:mod:`cartolex.lexicon.rejects`).
``here`` is uninformative in this field only (or a name): it stays in the
project. Answers of older versions only use ``C``, ``M``, ``O``, ``N``, ``K``,
``G`` and ``F``: they read the same way.
"""

from __future__ import annotations

__all__ = [
    "ACCEPTED",
    "ACCEPT_CODES",
    "CATEGORIES",
    "CODES",
    "NAMING_PREFERRED",
    "REJECTED",
    "REJECT_CODES",
    "category_of",
]

#: Categories of an accepted keyword.
ACCEPTED = ("concept", "method", "object", "place", "field")
#: Categories of a rejected candidate.
REJECTED = ("never", "here")
CATEGORIES = ACCEPTED + REJECTED

#: Each code: its category and what it says about a term, in words.
CODES: dict[str, tuple[str, str]] = {
    "C": ("concept", "a concept, phenomenon, process, property or theory"),
    "M": ("method", "a method, technique, instrument, model or data source"),
    "O": ("object", "an object of study: a material, an organism, a system"),
    "P": ("place", "a kind of place or setting studied as such"),
    "D": ("field", "the name of a discipline or field"),
    "N": ("here", "a name: a person, a particular place, an institution, a project, a journal"),
    "K": ("never", "administrative, career or project-management wording"),
    "G": ("never", "a generic word of academic writing, never a keyword on its own"),
    "F": ("never", "a broken piece, not a term"),
    "H": ("here", "a real term, but not informative in this field"),
}
ACCEPT_CODES = tuple(c for c, (cat, _) in CODES.items() if cat in ACCEPTED)
REJECT_CODES = tuple(c for c, (cat, _) in CODES.items() if cat in REJECTED)
#: The categories a theme's name prefers, on a tie of use.
NAMING_PREFERRED = ("concept", "object")


def category_of(code: str) -> str:
    """The category of a code (``""`` for an unknown one)."""
    found = CODES.get(str(code or "").strip().upper())
    return found[0] if found else ""
