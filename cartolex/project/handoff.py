# SPDX-License-Identifier: MIT
"""The answers to a keyword handoff, as earlier versions imported them: read them back.

A *handoff* gave the terms, with their evidence, to a chat assistant: a
prompt to paste, the terms to attach, and ``bundle.json`` (``cartolex-handoff/1``),
the same items numbered, to read the answer back, one line per term::

    1 | C | tide gauge
    2 | O | trait de côte | shoreline
    4 | G | further work | sure

The AI copilot (:mod:`cartolex.project.copilot`) has replaced it; the answers
already imported (``decisions/history/ai/<time>-handoff.txt`` beside the part
they answer) stay readable and can still be accepted. This module reads them.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..lexicon.categories import ACCEPT_CODES as _ACCEPT
from ..lexicon.categories import CODES as _CODES
from ..lexicon.categories import REJECT_CODES as _REJECT
from ..lexicon.categories import category_of

__all__ = [
    "ACCEPT_CODES",
    "CODES",
    "HANDOFF_FORMAT",
    "REJECT_CODES",
    "BundleItem",
    "ParsedAnswer",
    "Verdict",
    "items_of",
    "parse_answer",
    "tokens",
]

#: Characters per token, for the estimates (a common rule of thumb for Latin scripts).
CHARS_PER_TOKEN = 4.0

#: The triage codes and what each says about a term (:mod:`cartolex.lexicon.categories`).
CODES = {code: meaning for code, (_, meaning) in _CODES.items()}
#: The codes of an answer (the engine's triage codes).
ACCEPT_CODES = _ACCEPT
REJECT_CODES = _REJECT
#: Format of ``bundle.json``, the machine-readable half of a handoff part.
HANDOFF_FORMAT = "cartolex-handoff/1"


def tokens(text: str) -> int:
    """An estimate of the tokens of *text* (:data:`CHARS_PER_TOKEN`)."""
    return int(math.ceil(len(text) / CHARS_PER_TOKEN))


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


@dataclass(frozen=True)
class Verdict:
    """A judge's answer for one term: a triage code, the canonical form of an accept, and
    whether the judge is sure (``sure`` or ``unsure``; an answer that does not say is
    ``unsure``). Only a ``never`` answer given as sure enters the rejection cache."""

    code: str
    canonical: str = ""
    confidence: str = "unsure"

    @property
    def accept(self) -> bool:
        return self.code in ACCEPT_CODES

    @property
    def category(self) -> str:
        """The code's category (:mod:`cartolex.lexicon.categories`)."""
        return category_of(self.code)


_LINE = re.compile(r"^\s*(?:(\d+)\.\s*)?([CMOPDNGKFH])\s+(?:([a-z]{2})\s+)?(.+?)\s*$")


def items_of(record: Mapping[str, Any]) -> list[BundleItem]:
    """The items of a part's ``bundle.json``, in order (refused when it is not one)."""
    if record.get("format") != HANDOFF_FORMAT:
        raise ValueError(
            f"not a handoff part (format {record.get('format')!r}, expected {HANDOFF_FORMAT!r})"
        )
    fields = set(BundleItem.__dataclass_fields__)
    return [BundleItem(**{k: v for k, v in x.items() if k in fields}) for x in record["items"]]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.strip().strip("\"'«»`*").casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\s’']+", " ", text).strip()


@dataclass
class ParsedAnswer:
    """An answer read back: a verdict per item (by index), and what could not be read."""

    verdicts: dict[int, Verdict]
    lines: int = 0  # lines that follow the format
    ignored: int = 0  # other non-empty lines
    unmatched: int = 0  # lines in the format that fit no item
    renumbered: int = 0  # lines matched by their term, their number being wrong
    term_mismatch: int = 0  # lines matched by number whose term differs from the item's
    duplicates: int = 0  # second answers for an item (the first one counts)

    def missing(self, n_items: int) -> int:
        return n_items - len(self.verdicts)


#: The confidences an answer line may end with.
CONFIDENCES = ("sure", "unsure")


def _cells(line: str) -> tuple[int | None, str, str, str, str] | None:
    """(number, code, term, English form, confidence) of an answer line, in either format;
    None otherwise. A last cell ``sure`` or ``unsure`` is the confidence (else ``unsure``)."""
    line = line.strip().strip("`").strip()
    if "|" in line or "\t" in line:
        cells = [c.strip() for c in re.split(r"[|\t]", line.strip("|"))]
        confidence = "unsure"
        if len(cells) > 3 and cells[-1].strip("*").casefold() in CONFIDENCES:
            confidence = cells.pop().strip("*").casefold()
        if len(cells) >= 2 and re.fullmatch(r"\d+[.)]?", cells[0]):
            code = cells[1].strip("*").upper()
            if code in ACCEPT_CODES + REJECT_CODES:
                term = cells[2] if len(cells) > 2 else ""
                english = cells[3] if len(cells) > 3 else ""
                return int(cells[0].rstrip(".)")), code, term, english, confidence
        return None
    m = _LINE.match(line)
    if not m:
        return None
    number, code, _lang, rest = m.groups()
    term, _, english = rest.partition("=")
    return (int(number) if number else None), code, term.strip(), english.strip(), "unsure"


def parse_answer(text: str, items: Sequence[BundleItem]) -> ParsedAnswer:
    """Read an answer to a handoff part: one verdict per item, by index into *items*.

    Lines are ``<number> | <code> | <term> | <English form>`` (the handoff
    format; a Markdown table row or tab-separated cells also work) or the
    triage's ``<code> <lang> <term>=<English form>``. A line is matched to its
    item by number, and checked against the term it repeats: when the term is
    another item's, that item is taken (``renumbered``). The English form of
    an accepted term defaults to the term. The first answer for an item counts.
    """
    by_term: dict[str, int] = {}
    for i, it in enumerate(items):
        by_term.setdefault(_norm(it.term), i)
    out = ParsedAnswer({})
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("```"):
            continue
        cells = _cells(raw)
        if cells is None:
            out.ignored += 1
            continue
        number, code, term, english, confidence = cells
        out.lines += 1
        index = None
        said = _norm(term)
        if number is not None and 1 <= number <= len(items):
            index = number - 1
            if said and said != _norm(items[index].term):
                other = by_term.get(said)
                if other is not None:
                    index = other
                    out.renumbered += 1
                else:
                    out.term_mismatch += 1
        elif said in by_term:
            index = by_term[said]
            if number is not None:
                out.renumbered += 1
        if index is None:
            out.unmatched += 1
            continue
        if index in out.verdicts:
            out.duplicates += 1
            continue
        canonical = english or (items[index].term if code in ACCEPT_CODES else "")
        out.verdicts[index] = Verdict(code, canonical, confidence)
    return out
