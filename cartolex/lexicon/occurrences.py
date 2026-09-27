# SPDX-License-Identifier: MIT
"""Occurrence/concordance scanner mirroring the pipeline vectorizer.

Locates every occurrence of canonical-term surface forms (aliases) in one raw
document, with character spans in the ORIGINAL text and word-window context
snippets. Token regex, lowercasing and sliding n-gram semantics are exactly
those of the sklearn vectorizers used at extraction and consolidation
(``lowercase=True``, ``token_pattern=r"(?u)\\b\\w\\w+\\b"``,
``strip_accents=None``, overlapping n-grams all counted), so occurrence counts
agree with the counts behind the pipeline scores.

One deliberate divergence: sklearn lowercases the whole document before
tokenizing, while this module tokenizes the original text and lowercases each
token — equivalent whenever ``str.lower()`` is length-preserving, which holds
for the corpus languages (exotic mappings such as ``'İ'`` are the only
exceptions). Tokenizing the original is what makes verbatim spans possible.

A second deliberate divergence guards chronological concepts ("12th century",
"XIIe siècle"): the pipeline vectorizer counted their BARE numeral aliases
(``xii``, ``xiie``, ``12e``), so a "XIIe arrondissement" address or "Louis XII"
minted false century occurrences. Here every searched form of a chronological
concept must carry a period marker (see :func:`filter_chronological_forms`);
occurrence counts for those concepts are therefore intentionally LOWER than the
raw pipeline scores whenever bare-numeral aliases inflated them.

No I/O and no configuration here: callers resolve each concept's surface forms
(e.g. from the persisted alias table) and pass them in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# sklearn's default token pattern — the exact regex behind every pipeline count.
TOKEN_PATTERN = r"(?u)\b\w\w+\b"
_TOKEN_RE = re.compile(TOKEN_PATTERN)

# ── chronological concepts (century/millennium references) ──────────────────
# Strict roman numeral (non-empty), e.g. "xii", "xxi" — lowercased input only.
_ROMAN_NUMERAL = r"(?=[ivxlcdm])m{0,4}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
# Optional ordinal suffix: French (12e/12ème/1er/IInde…), English (12th/1st…),
# superscript ᵉ. Longest alternatives first.
_ORDINAL_SUFFIX = r"(?:ème|eme|ère|ere|nde|er|re|nd|st|th|rd|ᵉ|e|è)?"
_NUM_ORD = rf"(?:{_ROMAN_NUMERAL}|\d{{1,2}}){_ORDINAL_SUFFIX}"
_RANGE_SEP = r"(?:\s*[-–—/]\s*|\s+(?:et|and|au|to)\s+)"
_PERIOD_MARKER = (
    r"(?:siècles?|siecles?|century|centuries|millénaires?|millenaires?|millennium|millennia)"
)
_CHRONO_TERM_RE = re.compile(rf"{_NUM_ORD}(?:{_RANGE_SEP}{_NUM_ORD})?[\s -]+{_PERIOD_MARKER}")
# Period-marker tokens as the analyzer emits them (lowercased, accents kept).
_PERIOD_MARKER_TOKENS = frozenset(
    {
        "siècle",
        "siècles",
        "siecle",
        "siecles",
        "century",
        "centuries",
        "millénaire",
        "millénaires",
        "millenaire",
        "millenaires",
        "millennium",
        "millennia",
    }
)
# Alias written with the French abbreviation "s." ("XIIe s."). The analyzer
# drops single-letter tokens, so these forms need a raw-text check at match
# time instead of a marker token.
_S_ABBREV_ALIAS_RE = re.compile(r"(?:^|\s)s\.$")
_S_ABBREV_TEXT_RE = re.compile(r"\s*s\.", re.IGNORECASE)


def is_chronological_term(term: str) -> bool:
    """True when *term* is an explicit numbered century/millennium reference.

    Covers EN and FR forms with roman, arabic or superscript ordinals —
    ``12th century``, ``xiie siècle``, ``12e siècle``, ``XIIᵉ siècle``,
    ``iie millénaire`` — including two-term ranges (``12th-13th centuries``,
    ``xiie et xiiie siècles``). Named period phrases without a numbered
    century/millennium marker (``bronze age``, ``moyen âge``, ``siècle des
    lumières``) are NOT chronological here.
    """
    return bool(_CHRONO_TERM_RE.fullmatch(term.strip().lower()))


def _chronological_form_mode(alias: str) -> str:
    """How a chronological concept's alias may be searched.

    ``"marker"``: a period-marker token survives analyzer normalization
    (``xiie siècle``, ``12th century``). ``"s_abbrev"``: the alias ends with
    the abbreviation ``s.`` (``xiie s.``) — searchable only with a raw-text
    check, because its analyzer form degrades to the bare numeral.
    ``"invalid"``: bare roman/arabic numerals (``xii``, ``xiie``, ``12e``,
    ``12ème``) and any other marker-less form — never searched.
    """
    parts = _ngram_parts(alias)
    if any(p in _PERIOD_MARKER_TOKENS for p in parts):
        return "marker"
    if parts and _S_ABBREV_ALIAS_RE.search(alias.strip().lower()):
        return "s_abbrev"
    return "invalid"


def filter_chronological_forms(term: str, forms: Sequence[str]) -> list[str]:
    """Drop the surface forms of a chronological *term* that may not be searched.

    For a chronological *term* (see :func:`is_chronological_term`), a valid
    form must carry a period marker — ``siècle(s)``/``century``/``centuries``/
    ``millénaire(s)``, the abbreviation ``s.``, ordinal + siècle
    (``12e siècle``), superscript + siècle (``XIIᵉ siècle``). Bare roman or
    arabic numerals (``xii``, ``xiie``, ``12e``, ``12ème`` alone) are never
    valid. Non-chronological terms keep every form unchanged.
    """
    if not is_chronological_term(term):
        return list(forms)
    return [f for f in forms if _chronological_form_mode(f) != "invalid"]


@dataclass(frozen=True)
class Snippet:
    """One context window around a matched span, verbatim from the original text."""

    before: str
    match: str
    after: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ConceptOccurrences:
    """All occurrences of one concept's aliases in one document.

    ``count`` sums per-alias sliding-window matches (nested aliases each count,
    reproducing the alias→canonical fold); ``snippets`` merge overlapping
    same-concept spans so near-duplicate excerpts are shown once.
    """

    count: int
    counts_by_alias: dict[str, int]
    snippets: list[Snippet]


def iter_word_tokens(text: str) -> list[tuple[int, int, str]]:
    """Analyzer tokens of *text* as ``(char_start, char_end, lowered_form)``."""
    return [(m.start(), m.end(), m.group().lower()) for m in _TOKEN_RE.finditer(text)]


def _ngram_parts(ngram: str) -> tuple[str, ...]:
    """An n-gram/alias normalized through the analyzer tokenization.

    Alias tables and LLM canonicals may carry punctuation the analyzer never
    emits (``spin-orbit coupling``, ``l'électron``); tokenizing the alias with
    the SAME regex makes matching punctuation-insensitive and equivalent to
    the vectorizer's view of that surface form.
    """
    return tuple(m.group().lower() for m in _TOKEN_RE.finditer(ngram))


def _matches_at(tokens: Sequence[tuple[int, int, str]], i: int, parts: Sequence[str]) -> bool:
    if i + len(parts) > len(tokens):
        return False
    return all(tokens[i + k][2] == parts[k] for k in range(len(parts)))


def find_ngram_spans(tokens: Sequence[tuple[int, int, str]], ngram: str) -> list[tuple[int, int]]:
    """Char spans of every sliding-window occurrence of *ngram* in *tokens*.

    *ngram* is normalized through the analyzer tokenization first (see
    :func:`_ngram_parts`), so ``"spin-orbit"`` and ``"spin orbit"`` are the
    same query; overlapping occurrences all count, exactly like sklearn's
    n-gram term frequencies. Spans run from the first token's start to the
    last token's end in the original text (hyphens/whitespace between tokens
    are included verbatim).
    """
    parts = _ngram_parts(ngram)
    if not parts:
        return []
    return [
        (tokens[i][0], tokens[i + len(parts) - 1][1])
        for i in range(len(tokens) - len(parts) + 1)
        if _matches_at(tokens, i, parts)
    ]


def scan_concept_occurrences(
    text: str,
    aliases_by_concept: Mapping[str, Sequence[str]],
    *,
    window_words: int = 10,
) -> dict[str, ConceptOccurrences]:
    """Scan one document for every concept's surface forms.

    Counting: per-alias independent sliding-window matches, summed per concept.
    Aliases are analyzer-normalized first (punctuation-insensitive) and two
    aliases with the same normalized form count ONCE — the vectorizer had a
    single column for that form. Chronological concepts (see
    :func:`is_chronological_term`) only search their period-marker-carrying
    forms (:func:`filter_chronological_forms`); an ``s.``-abbreviated form
    (``xiie s.``) additionally requires the abbreviation right after the
    numeral in the original text. Display: overlapping or nested spans of the
    SAME concept merge into one snippet (union span, all aliases listed);
    different concepts never merge. Snippets carry ``window_words`` analyzer
    tokens of verbatim context on each side, clamped at document edges.
    Concepts with zero occurrences are absent from the result.
    """
    tokens = iter_word_tokens(text)
    if not tokens:
        return {}
    first_token_index: dict[str, list[int]] = {}
    for i, (_, _, low) in enumerate(tokens):
        first_token_index.setdefault(low, []).append(i)

    out: dict[str, ConceptOccurrences] = {}
    for concept, aliases in aliases_by_concept.items():
        chronological = is_chronological_term(concept)
        counts: dict[str, int] = {}
        # (first_token, last_token, alias) per hit, in document order.
        hits: list[tuple[int, int, str]] = []
        seen_parts: set[tuple[str, ...]] = set()
        for alias in dict.fromkeys(aliases):
            needs_s_abbrev = False
            if chronological:
                mode = _chronological_form_mode(alias)
                if mode == "invalid":
                    continue
                needs_s_abbrev = mode == "s_abbrev"
            parts = _ngram_parts(alias)
            if not parts or parts in seen_parts:
                continue
            seen_parts.add(parts)
            for i in first_token_index.get(parts[0], []):
                if not _matches_at(tokens, i, parts):
                    continue
                end = tokens[i + len(parts) - 1][1]
                if needs_s_abbrev and not _S_ABBREV_TEXT_RE.match(text, end):
                    continue
                hits.append((i, i + len(parts) - 1, alias))
                counts[alias] = counts.get(alias, 0) + 1
        if not hits:
            continue
        hits.sort()
        # Merge token-overlapping same-concept hits for display (union span).
        merged: list[tuple[int, int, set[str]]] = []
        for ti, tj, alias in hits:
            if merged and ti <= merged[-1][1]:
                last_ti, last_tj, alias_set = merged[-1]
                merged[-1] = (last_ti, max(last_tj, tj), alias_set | {alias})
            else:
                merged.append((ti, tj, {alias}))
        snippets = []
        for ti, tj, alias_set in merged:
            before_start = tokens[max(0, ti - window_words)][0]
            after_end = tokens[min(len(tokens) - 1, tj + window_words)][1]
            snippets.append(
                Snippet(
                    before=text[before_start : tokens[ti][0]],
                    match=text[tokens[ti][0] : tokens[tj][1]],
                    after=text[tokens[tj][1] : after_end],
                    aliases=tuple(sorted(alias_set)),
                )
            )
        out[concept] = ConceptOccurrences(
            count=len(hits), counts_by_alias=counts, snippets=snippets
        )
    return out
