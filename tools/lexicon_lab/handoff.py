# SPDX-License-Identifier: MIT
"""AI triage by handoff: the bundle a reviewer (a person, or an AI assistant in a browser) gets.

Today the triage sends batches of 150 bare term strings to an AI provider's
API. A *handoff* instead gives one bundle, with evidence for each term, to a
judge: the bundle can be read by a person, or pasted into an AI assistant the
owner already uses, and the answers come back in the triage's line format.
This module builds the bundle, writes it as the files a person uploads to a
chat assistant (:func:`write_part`), reads the answers back
(:func:`parse_answer`), defines what a judge is, and provides fake judges for
measurement. No paid service is called here.

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
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from cartolex.lexicon.scoring import Candidate, ScoredCandidates

#: Characters per token, for the estimates (a common rule of thumb for Latin scripts).
CHARS_PER_TOKEN = 4.0
#: A cautious count, for lists full of numbers, punctuation and accented words.
CAUTIOUS_CHARS_PER_TOKEN = 3.0


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


_LINE = re.compile(r"^\s*(?:(\d+)\.\s*)?([CMONGKF])\s+(?:([a-z]{2})\s+)?(.+?)\s*$")


class ReplayJudge:
    """Answers pasted back from a chat or a person (see :func:`parse_answer`)."""

    name = "replay"

    def __init__(self, text: str) -> None:
        self.text = text

    def judge(self, bundle: Bundle) -> dict[str, Verdict]:
        parsed = parse_answer(self.text, bundle.items)
        out = {bundle.items[i].term: v for i, v in parsed.verdicts.items()}
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


# ── the browser handoff: the files a person uploads, and the answer read back ──

#: Format of ``bundle.json``, the machine-readable half of a handoff part.
HANDOFF_FORMAT = "cartolex-handoff/1"
#: Version of :data:`PROMPT`, recorded in each part. 2: a process, property or
#: measure of an object is a keyword; F is only for broken pieces.
PROMPT_VERSION = 2
#: The codes of the answer (the engine's triage codes).
ACCEPT_CODES = ("C", "M", "O")
REJECT_CODES = ("N", "K", "G", "F")
LANGUAGE_NAMES = {"en": "English", "fr": "French", "pt": "Portuguese"}

PROMPT = """\
I am building the keyword list of a map of a research field. The map is made
from the titles and abstracts of the field's publications. The attached file
terms.txt{part_note} lists {n} candidate terms found in them automatically,
numbered 1 to {n}, in {languages}.

The field: {domain}
In its owner's words: {description}

For each term, decide whether it is a good keyword of this field: a term a
researcher of the field would use to name a subject, a method or an object of
their work. Accept it with one of these codes:
  C  a concept, phenomenon, process, property or theory
  M  a method, technique, instrument, model or kind of data
  O  an object of study: a material, an organism, a system, an environment
or reject it with one of these:
  N  a name: a person, a particular place, an institution, a project, a journal
  K  administrative, career or project-management wording
  G  too generic to be a keyword on its own: it could be said of any field or study
  F  a broken piece, not a term: a phrase cut out of a longer one, words split
     across a phrase boundary, or debris of a sentence

A phrase that joins a process, a property or a measure to an object of the
field (the growth of a cell, the stiffness of a material, the rate of a
reaction) is a good keyword, usually C, when both parts belong to the field.
French often writes it « X des Y », English as a compound: judge the whole
phrase, and do not reject it in favour of its object alone. F is only for
broken pieces.

The evidence after each term (how many people and texts use it, its other
spellings, the longer phrases it appears in) is there to help; judge the term
itself as a keyword of this field. Terms from neighbouring disciplines are
welcome when they name a real concept, method or object.

Answer with exactly one line per term, in the order of the list, and nothing
else. For an accepted term (C, M, O):
  <number> | <code> | <term as listed> | <English form>
where the English form is the usual English name of what the term names, in
lower case except proper nouns and acronyms; leave it out when it is the term
itself (most English terms). For a rejected term (N, K, G, F), stop after the
term:
  <number> | <code> | <term as listed>

For example, with terms from another field:
  1 | C | phase transition
  2 | O | levure bourgeonnante | budding yeast
  3 | M | microscopie à force atomique | atomic force microscopy
  4 | G | further work
  5 | N | Lyon
  6 | C | repliement des protéines | protein folding
  7 | C | enzyme turnover rate
  8 | F | matter physics
  9 | F | égard des mesures

Read and judge every term yourself; do not write or run a program to decide.
Give the whole answer as plain text in one code block, or as a downloadable
text file named answer.txt. If your answer stops before term {n}, I will write
"continue": then go on from the next number, in the same format, without
repeating the lines already given.
"""

ANSWER_FORMAT = """\
The expected answer: one line per term of terms.txt, in order, numbered as in
the list, fields separated by a vertical bar.

  accepted (C, M, O):  <number> | <code> | <term as listed> | <English form>
  rejected (N, K, G, F):  <number> | <code> | <term as listed>

The English form is left out when it is the term itself. Codes: C concept,
M method, O object of study (accepted); N name, K administrative wording,
G too generic, F broken piece of a phrase (rejected).

Example:
  1 | C | phase transition
  2 | O | levure bourgeonnante | budding yeast
  4 | G | further work

Save the answer as answer.txt next to this file. If it came in several pieces
(after "continue"), paste them one after the other in answer.txt, or save them
as answer-1.txt, answer-2.txt, …: every answer*.txt file is read, in name
order. Lines that do not follow the format (a sentence, a code-block fence)
are ignored.
"""


def cautious_tokens(text: str) -> int:
    """A cautious token count for sizing parts (see :data:`CAUTIOUS_CHARS_PER_TOKEN`)."""
    return int(math.ceil(len(text) / CAUTIOUS_CHARS_PER_TOKEN))


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def item_line(number: int, it: BundleItem) -> str:
    """One line of ``terms.txt``: the number, the term, its language and its evidence.

    The band and its reason stay out of the line (they are in ``bundle.json``):
    the judge sees the term, not what the extraction thought of it.
    """
    people = _count(it.people, "person", "people")
    parts = [f"{number}. {it.term} [{it.lang}] — {people}, {_count(it.texts, 'text', 'texts')}"]
    others = [f for f in it.forms if f != it.term][:3]
    if others:
        parts.append("also: " + "; ".join(others))
    if it.inside:
        parts.append("in: " + "; ".join(it.inside[:3]))
    return " — ".join(parts)


def _languages(items: Sequence[BundleItem]) -> list[str]:
    return sorted({it.lang for it in items}, key=lambda lang: (lang != "en", lang))


def terms_text(
    items: Sequence[BundleItem], *, domain: str, n_people: int, n_texts: int, part_note: str
) -> str:
    """``terms.txt``: a short header, then one line per term."""
    langs = ", ".join(LANGUAGE_NAMES.get(x, x) for x in _languages(items))
    head = [
        f"Candidate keywords of the field «{domain}»{part_note}",
        f"{len(items)} terms ({langs}), found in {n_texts:,} texts by {n_people:,} people.",
        "Each line: number. term [language] — how many people and texts use it — also: its "
        "other spellings — in: longer phrases it appears in.",
        "",
    ]
    return "\n".join(head + [item_line(i, it) for i, it in enumerate(items, 1)]) + "\n"


def prompt_text(items: Sequence[BundleItem], *, domain: str, description: str, part_note: str):
    langs = [f"{LANGUAGE_NAMES.get(x, x)} [{x}]" for x in _languages(items)]
    languages = langs[0] if len(langs) == 1 else ", ".join(langs[:-1]) + " or " + langs[-1]
    return PROMPT.format(
        part_note=part_note,
        n=len(items),
        languages=languages,
        domain=domain,
        description=description or "—",
    )


def split_items(
    items: Sequence[BundleItem],
    *,
    max_tokens: int,
    domain: str,
    description: str,
    n_people: int,
    n_texts: int,
) -> list[list[BundleItem]]:
    """Cut *items* into the fewest parts whose prompt and terms stay under *max_tokens*.

    Tokens are counted cautiously (:func:`cautious_tokens`); the parts are
    balanced (about the same size each) and keep the items' order.
    """
    items = list(items)

    def size(chunk: Sequence[BundleItem]) -> int:
        note = " (part 10 of 10)"
        return cautious_tokens(
            prompt_text(chunk, domain=domain, description=description, part_note=note)
            + terms_text(chunk, domain=domain, n_people=n_people, n_texts=n_texts, part_note=note)
        )

    if not items:
        return []
    fixed = size(items[:1]) - cautious_tokens(item_line(1, items[0]) + "\n")
    line_tokens = [cautious_tokens(item_line(99999, it) + "\n") for it in items]
    n_parts = max(1, math.ceil(sum(line_tokens) / max(max_tokens - fixed, 1)))
    while True:
        target = sum(line_tokens) / n_parts
        parts, current, acc = [], [], 0
        for it, t in zip(items, line_tokens, strict=True):
            if current and acc + t > target and len(parts) < n_parts - 1:
                parts.append(current)
                current, acc = [], 0
            current.append(it)
            acc += t
        if current:
            parts.append(current)
        if all(size(p) <= max_tokens for p in parts):
            return parts
        n_parts += 1


def write_part(
    folder: Path,
    items: Sequence[BundleItem],
    *,
    name: str,
    domain: str,
    description: str,
    n_people: int,
    n_texts: int,
    part: int = 1,
    parts: int = 1,
    meta: Mapping | None = None,
) -> dict:
    """Write one handoff part in *folder*: what a person uploads and pastes, zipped and loose.

    ``prompt.txt`` (to paste), ``terms.txt`` (to attach), ``expected-answer.txt``
    (the answer expected, and where to save it), the zip of the three
    (``<name>.zip``), and ``bundle.json`` (the same items, numbered, to read
    the answer back). Returns the part's sizes.
    """
    folder.mkdir(parents=True, exist_ok=True)
    note = f" (part {part} of {parts})" if parts > 1 else ""
    prompt = prompt_text(items, domain=domain, description=description, part_note=note)
    terms = terms_text(items, domain=domain, n_people=n_people, n_texts=n_texts, part_note=note)
    files = {"prompt.txt": prompt, "terms.txt": terms, "expected-answer.txt": ANSWER_FORMAT}
    for fname, text in files.items():
        (folder / fname).write_text(text, encoding="utf-8")
    with zipfile.ZipFile(folder / f"{name}.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, text in files.items():
            info = zipfile.ZipInfo(f"{name}/{fname}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text)
    record = {
        "format": HANDOFF_FORMAT,
        "prompt_version": PROMPT_VERSION,
        "name": name,
        "part": part,
        "parts": parts,
        "domain": domain,
        "description": description,
        **dict(meta or {}),
        "items": [{"number": i, **asdict(it)} for i, it in enumerate(items, 1)],
    }
    (folder / "bundle.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    upload = prompt + terms
    # Every term accepted, and a French or Portuguese term given an English form as long.
    answer = "".join(
        answer_line(i, it, "C", it.term + " " if it.lang != "en" else "") + "\n"
        for i, it in enumerate(items, 1)
    )
    return {
        "part": part,
        "items": len(items),
        "characters": len(upload),
        "tokens": tokens(upload),
        "cautious_tokens": cautious_tokens(upload),
        "answer_tokens": tokens(answer),
    }


def answer_line(number: int, it: BundleItem, code: str, english: str = "") -> str:
    """One line of an answer in the expected format (the English form only when it differs)."""
    if code in ACCEPT_CODES and english and english != it.term:
        return f"{number} | {code} | {it.term} | {english}"
    return f"{number} | {code} | {it.term}"


def load_part(folder: Path) -> tuple[dict, list[BundleItem]]:
    """``bundle.json`` of a part: its record and its items, in order."""
    record = json.loads((folder / "bundle.json").read_text(encoding="utf-8"))
    fields = set(BundleItem.__dataclass_fields__)
    items = [BundleItem(**{k: v for k, v in x.items() if k in fields}) for x in record["items"]]
    return record, items


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


def _cells(line: str) -> tuple[int | None, str, str, str] | None:
    """(number, code, term, English form) of an answer line, in either format; None otherwise."""
    line = line.strip().strip("`").strip()
    if "|" in line or "\t" in line:
        cells = [c.strip() for c in re.split(r"[|\t]", line.strip("|"))]
        if len(cells) >= 2 and re.fullmatch(r"\d+[.)]?", cells[0]):
            code = cells[1].strip("*").upper()
            if code in ACCEPT_CODES + REJECT_CODES:
                term = cells[2] if len(cells) > 2 else ""
                english = cells[3] if len(cells) > 3 else ""
                return int(cells[0].rstrip(".)")), code, term, english
        return None
    m = _LINE.match(line)
    if not m:
        return None
    number, code, _lang, rest = m.groups()
    term, _, english = rest.partition("=")
    return (int(number) if number else None), code, term.strip(), english.strip()


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
        number, code, term, english = cells
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
        out.verdicts[index] = Verdict(code, canonical)
    return out
