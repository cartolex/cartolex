# SPDX-License-Identifier: MIT
"""AI triage by handoff: the bundle a reviewer (a person, or an AI assistant in a browser) gets.

Today the triage sends batches of 150 bare term strings to an AI provider's
API. A *handoff* instead gives one bundle, with evidence for each term, to a
judge: the bundle can be read by a person, or pasted into an AI assistant the
owner already uses, and the answers come back in the triage's line format.
This module builds the bundle, defines what a judge is, and provides fake
judges for measurement. No paid service is called here.

Each bundle item carries: the term and its language, its band and reason, how
many people and texts use it, its specificity (the share of people who do
*not* use it, as a percentile of the corpus), its other surface forms, the
longer candidates it sits in, and optionally a few usage lines (the term in
its sentence).
"""

from __future__ import annotations

import json
import math
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Protocol

from cartolex.lexicon.scoring import Candidate, ScoredCandidates

#: Characters per token, for the estimates (a common rule of thumb for Latin scripts).
CHARS_PER_TOKEN = 4.0


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

    def to_json(self) -> str:
        return json.dumps(
            {
                "format": "cartolex-handoff/0",
                "domain": self.domain,
                "description": self.description,
                "items": [asdict(i) for i in self.items],
            },
            ensure_ascii=False,
            indent=1,
        )

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


INSTRUCTIONS = """You are helping to build the keyword list of a map of the research field
"{domain}". Field described by its owner: {description}
For each numbered term, answer on one line, in order, with the triage codes:
  C <lang> <term>=<canonical English form>   a concept, M a method, O an object of study
  N <term>   a person, place or institution;  G too generic;  F a fragment or not a term
The evidence (people and texts using the term, its other forms, the longer phrases it
sits in, its use in context) is there to help; judge the term as a keyword of this field."""


def bundle(
    scored: Mapping[str, ScoredCandidates],
    *,
    bands: Sequence[str] = ("check",),
    domain: str = "",
    description: str = "",
    usage: Mapping[str, Mapping[str, list[str]]] | None = None,
) -> Bundle:
    """The bundle of the candidates in *bands*, every language, best scored first."""
    items: list[BundleItem] = []
    for lang, sc in scored.items():
        n_people = max(sc.n_people, 1)
        for c in sorted(sc.candidates.values(), key=lambda c: (-c.score_len, c.term)):
            if c.band not in bands:
                continue
            items.append(_item(lang, c, sc, n_people, (usage or {}).get(lang, {})))
    return Bundle(domain, description, items)


def _item(lang: str, c: Candidate, sc: ScoredCandidates, n_people: int, usage) -> BundleItem:
    inside = [sc.candidates[k].term for k, _ in c.containers[:3] if k in sc.candidates]
    return BundleItem(
        term=c.term,
        lang=lang,
        band=c.band,
        reason=c.reason,
        people=c.people,
        texts=c.texts,
        specificity=round(1.0 - c.people / n_people, 3),
        forms=[f for f, _ in c.forms[:4]],
        inside=inside,
        usage=list(usage.get(c.term, []))[:2],
    )


def usage_lines(
    paragraphs: Sequence[str], terms: Sequence[str], *, per_term: int = 2, width: int = 60
) -> dict[str, list[str]]:
    """Up to *per_term* short lines showing each term in its sentence (first found, in order)."""
    out: dict[str, list[str]] = {t: [] for t in terms}
    patterns = {t: re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)", re.IGNORECASE) for t in terms}
    for para in paragraphs:
        for t, rx in patterns.items():
            if len(out[t]) >= per_term:
                continue
            m = rx.search(para)
            if m:
                a, b = max(m.start() - width, 0), min(m.end() + width, len(para))
                out[t].append(
                    ("…" if a else "") + para[a:b].strip() + ("…" if b < len(para) else "")
                )
    return out


# ── judges ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Verdict:
    """A judge's answer for one term: a triage code, and the canonical form of an accept."""

    code: str
    canonical: str = ""

    @property
    def accept(self) -> bool:
        return self.code in ("C", "M", "O")


class Judge(Protocol):
    """Anything that answers a bundle: an oracle, a person, an assistant's pasted answers."""

    name: str

    def judge(self, bundle: Bundle) -> dict[str, Verdict]: ...


class OracleJudge:
    """Accepts exactly the gold terms, under their canonical form (a perfect judge)."""

    name = "oracle"

    def __init__(self, is_gold, canonical=lambda term: term.lower()) -> None:
        self.is_gold = is_gold
        self.canonical = canonical

    def judge(self, bundle: Bundle) -> dict[str, Verdict]:
        return {
            it.term: Verdict("C", self.canonical(it.term)) if self.is_gold(it) else Verdict("G")
            for it in bundle.items
        }


class NoisyJudge(OracleJudge):
    """The oracle with errors: each answer is flipped with probability *error* (seeded)."""

    def __init__(self, is_gold, error: float, seed: int = 0, canonical=lambda t: t.lower()):
        super().__init__(is_gold, canonical)
        self.error = error
        self.seed = seed
        self.name = f"noisy {error:.0%}"

    def judge(self, bundle: Bundle) -> dict[str, Verdict]:
        rng = random.Random(self.seed)
        out = {}
        for it in bundle.items:
            gold = self.is_gold(it)
            if rng.random() < self.error:
                gold = not gold
            out[it.term] = Verdict("C", self.canonical(it.term)) if gold else Verdict("G")
        return out


_LINE = re.compile(r"^\s*(?:\d+\.\s*)?([CMONGKF])\s+(?:([a-z]{2})\s+)?(.+?)\s*$")


class ReplayJudge:
    """Answers pasted back from a chat or a person: the triage's line format, one per term."""

    name = "replay"

    def __init__(self, text: str) -> None:
        self.text = text

    def judge(self, bundle: Bundle) -> dict[str, Verdict]:
        lookup = {it.term.lower(): it.term for it in bundle.items}
        out: dict[str, Verdict] = {}
        for line in self.text.splitlines():
            m = _LINE.match(line)
            if not m:
                continue
            code, _lang, rest = m.groups()
            term, _, canonical = rest.partition("=")
            original = lookup.get(term.strip().lower())
            if original is not None:
                out[original] = Verdict(code, canonical.strip())
        for it in bundle.items:
            out.setdefault(it.term, Verdict("F"))
        return out


# ── what running it would cost ──────────────────────────────────────────────


def tokens(text: str) -> int:
    return int(math.ceil(len(text) / CHARS_PER_TOKEN))


@dataclass(frozen=True)
class Price:
    """Price of a model, in currency units per million tokens."""

    name: str
    input_per_million: float
    output_per_million: float


def api_cost(terms: Sequence[str], system_prompt: str, *, batch_size: int = 150) -> dict:
    """Tokens of today's API triage for *terms*: batches of bare strings, one answer line each."""
    batches = [terms[i : i + batch_size] for i in range(0, len(terms), batch_size)]
    t_in = sum(tokens(system_prompt) + tokens(json.dumps(b, ensure_ascii=False)) for b in batches)
    t_out = sum(tokens(f"C en {t}={t}") + 1 for t in terms)
    return {
        "terms": len(terms),
        "calls": len(batches),
        "input_tokens": t_in,
        "output_tokens": t_out,
    }


def handoff_cost(b: Bundle, *, extra_input_tokens: int = 0) -> dict:
    """Tokens of one handoff: the bundle as text (plus *extra_input_tokens*), one answer per term."""
    text = b.to_text()
    t_out = sum(tokens(f"C {it.lang} {it.term}={it.term}") + 1 for it in b.items)
    return {
        "terms": len(b.items),
        "calls": 1,
        "input_tokens": tokens(text) + int(extra_input_tokens),
        "output_tokens": t_out,
    }


def priced(cost: Mapping[str, int], price: Price) -> float:
    return (
        cost["input_tokens"] * price.input_per_million
        + cost["output_tokens"] * price.output_per_million
    ) / 1e6
