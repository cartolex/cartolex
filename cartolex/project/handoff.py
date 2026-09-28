# SPDX-License-Identifier: MIT
"""The AI clean-up by handoff: the bundle a reviewer gets, and the answers that come back.

Instead of sending batches of bare terms to an AI provider's API, a *handoff*
gives one bundle, with the evidence of each term, to a judge: a person, or an
AI assistant the project owner already uses. The answers come back in the
triage's line format, one line per term::

    C en tide gauge=tide gauge      a concept (M a method, O an object of study)
    N <term>                        a person, place or institution
    K <term>                        an administrative or generic action phrase
    G <term>                        too generic
    F <term>                        a fragment, not a term

:class:`Bundle` is the exchange format (``cartolex-handoff/1``): the field's
title and description, the instructions, and one :class:`BundleItem` per
term. :func:`parse_answers` reads pasted answers against the bundle. A bundle
holds keyword strings and counts only: never a text, a person or a key.
The lexicon lab (``tools/lexicon_lab/handoff.py``) builds bundles from scored
candidates and measures judges with the same classes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "ACCEPT",
    "CODES",
    "FORMAT",
    "INSTRUCTIONS",
    "Bundle",
    "BundleItem",
    "Verdict",
    "parse_answers",
]

FORMAT = "cartolex-handoff/1"

#: The triage codes and what each says about a term.
CODES = {
    "C": "a concept of the field",
    "M": "a method",
    "O": "an object of study",
    "N": "a person, place or institution",
    "K": "an administrative or generic action phrase",
    "G": "too generic",
    "F": "a fragment, not a term",
}
#: The codes that keep a term.
ACCEPT = frozenset({"C", "M", "O"})

INSTRUCTIONS = """You are helping to build the keyword list of a map of the research field
"{domain}". Field described by its owner: {description}
For each numbered term, answer on one line, in order, with the triage codes:
  C <lang> <term>=<canonical English form>   a concept, M a method, O an object of study
  N <term>   a person, place or institution;  G too generic;  F a fragment or not a term
The evidence (people and texts using the term, its other forms, the longer phrases it
sits in, its use in context) is there to help; judge the term as a keyword of this field."""


@dataclass
class BundleItem:
    """One term to judge, with its evidence."""

    term: str
    lang: str
    band: str
    reason: str
    people: int
    texts: int
    specificity: float
    forms: list[str]
    inside: list[str]
    usage: list[str] = field(default_factory=list)


@dataclass
class Bundle:
    """What a judge receives: the domain, the instructions and the items."""

    domain: str
    description: str
    items: list[BundleItem]

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "domain": self.domain,
            "description": self.description,
            "items": [asdict(i) for i in self.items],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=1)

    def to_text(self) -> str:
        """The bundle as plain text to paste: instructions, then one line per term."""
        lines = [INSTRUCTIONS.format(domain=self.domain, description=self.description or "—"), ""]
        for i, it in enumerate(self.items, 1):
            extra = []
            if len(it.forms) > 1:
                extra.append("forms: " + "; ".join(it.forms[1:4]))
            if it.inside:
                extra.append("inside: " + "; ".join(it.inside[:3]))
            lines.append(
                f"{i}. {it.term} [{it.lang}] — {it.people} people, {it.texts} texts, "
                f"specificity {it.specificity:.2f}, {it.reason}"
                + (" — " + " — ".join(extra) if extra else "")
            )
            for u in it.usage:
                lines.append(f"   « {u} »")
        return "\n".join(lines)

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> Bundle:
        """Read a bundle written by :meth:`as_dict` (``cartolex-handoff/0`` or ``/1``)."""
        fmt = doc.get("format")
        if fmt not in (FORMAT, "cartolex-handoff/0"):
            raise ValueError(f"not a handoff bundle (format {fmt!r}, expected {FORMAT!r})")
        fields = {f for f in BundleItem.__dataclass_fields__}
        items = [BundleItem(**{k: v for k, v in i.items() if k in fields}) for i in doc["items"]]
        return cls(str(doc.get("domain", "")), str(doc.get("description", "")), items)


@dataclass(frozen=True)
class Verdict:
    """A judge's answer for one term: a triage code, and the canonical form of an accept."""

    code: str
    canonical: str = ""

    @property
    def accept(self) -> bool:
        return self.code in ACCEPT


_LINE = re.compile(r"^\s*(?:\d+\.\s*)?([CMONGKF])\s+(?:([a-z]{2})\s+)?(.+?)\s*$")


def parse_answers(text: str, terms: Iterable[str]) -> dict[str, Verdict]:
    """The verdicts pasted back, for the *terms* they answer (matched without case).

    A line that names no term of *terms*, or is not in the line format, is
    ignored; a term without a line has no verdict. A later line for a term
    replaces an earlier one.
    """
    lookup = {t.lower(): t for t in terms}
    out: dict[str, Verdict] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        code, _lang, rest = m.groups()
        term, _, canonical = rest.partition("=")
        original = lookup.get(term.strip().lower())
        if original is not None:
            out[original] = Verdict(code, canonical.strip())
    return out
