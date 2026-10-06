# SPDX-License-Identifier: MIT
"""What the lab measures: precision and recall of kept keywords, AI load, stability.

Terms are compared through *loose keys*: lower case, accents removed, each
word replaced by its corpus lemma (or by its stem when the gold is stemmed),
articles and prepositions dropped — so ``le trait de côte``, ``traits de
côte`` and ``trait côte`` match, whatever the pipeline or the gold writes.

- **Gold** (``gold_all``): the field terms of a demo world (theme terms and
  methods, not settings, drivers or template phrasing), or a benchmark's
  reference keyphrases.
- **Reachable gold** (``gold_shared``): the gold terms that occur in the texts
  of at least ``min_people`` people — what a lexicon with that floor can find.
- **Final lexicon**: the kept band, plus the terms to check that the judge
  accepts. The lab's judge is the oracle (it accepts exactly the gold), so
  final precision is the kept band's, and recall is the recall of the whole
  procedure.
- **AI load**: the terms to check (a judge sees them); the candidates set
  aside and kept are not sent.
- **Lexicon without AI**: the kept and to-check bands together, as the
  consolidation takes them when no AI clean-up runs.
"""

from __future__ import annotations

import random
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

_WORD = re.compile(r"\w+", re.UNICODE)

#: Articles and prepositions a loose key leaves out (accent-free, lower case).
FUNCTION_WORDS = {
    "en": frozenset("a an the of in on at for to from by with and or".split()),
    "fr": frozenset(
        "le la les l un une des du de d au aux a en par pour sur dans avec et ou".split()
    ),
    "pt": frozenset(
        "o a os as um uma de do da dos das em no na nos nas por pelo pela pelos pelas "
        "para com ao aos e ou".split()
    ),
    "es": frozenset("el la los las un una de del a al en por para con y o e u".split()),
    "it": frozenset(
        "il lo la i gli le l un uno una di d del dello della dei degli delle dell a al allo "
        "alla ai agli alle all da dal dallo dalla dai dagli dalle dall in nel nello nella nei "
        "negli nelle nell su sul sullo sulla sui sugli sulle sull per con e ed o".split()
    ),
    "de": frozenset("der die das des dem den ein eine eines einer einem einen und oder".split()),
}


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


class Matcher:
    """Loose keys of one language (see the module docstring)."""

    def __init__(self, lang: str, lemmas: Mapping[str, str], *, stem: bool = False) -> None:
        self.lang = lang
        self.lemmas = lemmas
        self.function = FUNCTION_WORDS[lang]
        self.stemmer = None
        if stem:
            import snowballstemmer

            self.stemmer = snowballstemmer.stemmer("porter")
        self._cache: dict[str, str | None] = {}

    def word(self, word: str) -> str | None:
        hit = self._cache.get(word, "")
        if hit != "":
            return hit
        low = word.lower()
        base = strip_accents(low)
        if base in self.function:
            out = None
        elif self.stemmer is not None:
            out = self.stemmer.stemWord(base)
        else:
            out = strip_accents(self.lemmas.get(low, low))
        self._cache[word] = out
        return out

    def key(self, text: str) -> tuple[str, ...]:
        return tuple(w for w in (self.word(x) for x in _WORD.findall(text)) if w)

    def stream(self, text: str) -> list[str]:
        return [w for w in (self.word(x) for x in _WORD.findall(text)) if w]


def people_with(
    keys: set[tuple[str, ...]], paragraphs_by_person: Mapping[int, Iterable[str]], matcher: Matcher
) -> dict[tuple[str, ...], int]:
    """For each key, the number of people in whose paragraphs it occurs (contiguously)."""
    first: dict[str, list[tuple[str, ...]]] = {}
    for k in keys:
        if k:
            first.setdefault(k[0], []).append(k)
    count: dict[tuple[str, ...], int] = {}
    for paragraphs in paragraphs_by_person.values():
        found: set[tuple[str, ...]] = set()
        for paragraph in paragraphs:
            toks = matcher.stream(paragraph)
            for i, tok in enumerate(toks):
                for k in first.get(tok, ()):
                    if tuple(toks[i : i + len(k)]) == k:
                        found.add(k)
        for k in found:
            count[k] = count.get(k, 0) + 1
    return count


@dataclass
class Gold:
    """The gold of one language as loose keys."""

    all: set[tuple[str, ...]]
    shared: set[tuple[str, ...]]
    canonical: dict[tuple[str, ...], str]
    themes: dict[str, tuple[str, ...]]  # canonical form -> themes


def evaluate(table, matcher: Matcher, gold: Gold) -> dict:
    """Counts of one scored table: bands, gold among them, final lexicon, reachable gold found.

    :func:`ratios` turns counts (of one language, or summed over languages)
    into precision, recall and F1.
    """
    terms = table["term"].tolist()
    bands = table["band"].tolist()
    keys = [matcher.key(t) for t in terms]
    is_gold = [bool(k) and k in gold.all for k in keys]
    kept = {k for k, b in zip(keys, bands, strict=True) if b == "kept" and k}
    accepted = {k for k, g, b in zip(keys, is_gold, bands, strict=True) if b == "check" and g}
    final = kept | accepted
    lexicon = {k for k, b in zip(keys, bands, strict=True) if b in ("kept", "check") and k}
    return {
        "candidates": len(terms),
        "kept": bands.count("kept"),
        "check": bands.count("check"),
        "aside": bands.count("aside"),
        "gold_candidates": sum(is_gold),
        "gold_kept": sum(1 for k in kept if k in gold.all),
        "gold_set_aside": sum(1 for g, b in zip(is_gold, bands, strict=True) if b == "aside" and g),
        "final": len(final),
        "final_gold": sum(1 for k in final if k in gold.all),
        "found": len(final & gold.shared),
        "reachable_found": len(set(keys) & gold.shared),
        "shared": len(gold.shared),
        "kept_keys": len(kept),
        "lexicon": len(lexicon),
        "lexicon_gold": sum(1 for k in lexicon if k in gold.all),
        "lexicon_found": len(lexicon & gold.shared),
    }


def by_reason(table, matcher: Matcher, gold: Gold) -> dict[tuple[str, str], list[int]]:
    """``{(band, reason code): [candidates, gold among them]}`` of one scored table.

    The reason code drops what follows the colon (``part-of: sea level`` is
    ``part-of``): what each band rule catches, and how much gold it catches.
    """
    out: dict[tuple[str, str], list[int]] = {}
    for term, band, reason in zip(table["term"], table["band"], table["reason"], strict=True):
        k = matcher.key(term)
        cell = out.setdefault((band, str(reason).split(":")[0]), [0, 0])
        cell[0] += 1
        cell[1] += int(bool(k) and k in gold.all)
    return out


COUNTS = (
    "candidates",
    "kept",
    "check",
    "aside",
    "gold_candidates",
    "gold_kept",
    "gold_set_aside",
    "final",
    "final_gold",
    "found",
    "reachable_found",
    "shared",
    "kept_keys",
    "lexicon",
    "lexicon_gold",
    "lexicon_found",
)


def summed(rows: Iterable[Mapping[str, int]]) -> dict[str, int]:
    out = dict.fromkeys(COUNTS, 0)
    for r in rows:
        for k in COUNTS:
            out[k] += int(r[k])
    return out


def ratios(c: Mapping[str, int]) -> dict[str, float]:
    """Precision (final lexicon, kept band, candidate list), recall, its ceiling, F1, AI load.

    ``precision_lexicon`` and ``recall_lexicon`` are those of the lexicon
    without AI (the kept and to-check bands).
    """
    precision = c["final_gold"] / c["final"] if c["final"] else 0.0
    recall = c["found"] / c["shared"] if c["shared"] else 0.0
    return {
        "precision": precision,
        "precision_kept": c["gold_kept"] / c["kept_keys"] if c["kept_keys"] else 0.0,
        "precision_all": c["gold_candidates"] / c["candidates"] if c["candidates"] else 0.0,
        "recall": recall,
        "recall_ceiling": c["reachable_found"] / c["shared"] if c["shared"] else 0.0,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "ai_load": float(c["check"]),
        "precision_lexicon": c["lexicon_gold"] / c["lexicon"] if c["lexicon"] else 0.0,
        "recall_lexicon": c["lexicon_found"] / c["shared"] if c["shared"] else 0.0,
    }


def final_keys(table, matcher: Matcher, gold: Gold) -> dict[tuple[str, ...], float]:
    """The final lexicon (kept, and accepted to-check terms) as loose keys with their score."""
    out: dict[tuple[str, ...], float] = {}
    for term, band, score in zip(table["term"], table["band"], table["score_len"], strict=True):
        k = matcher.key(term)
        if not k:
            continue
        if band == "kept" or (band == "check" and k in gold.all):
            out[k] = max(out.get(k, 0.0), float(score))
    return out


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """Spearman rank correlation (ties share their mean rank)."""
    if len(x) < 3:
        return float("nan")

    def ranks(v):
        v = np.asarray(v, dtype=float)
        order = v.argsort(kind="stable")
        r = np.empty(len(v))
        r[order] = np.arange(len(v))
        _, inv = np.unique(v, return_inverse=True)
        return (np.bincount(inv, weights=r) / np.bincount(inv))[inv]

    rx, ry = ranks(x), ranks(y)
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def subsample(units_by_lang, share: float, seed: int):
    """The same corpus without a random *share* of its texts (by text, every language)."""
    texts = sorted({u.text for units in units_by_lang.values() for u in units})
    rng = random.Random(seed)
    drop = set(rng.sample(texts, int(round(share * len(texts)))))
    return {lang: [u for u in units if u.text not in drop] for lang, units in units_by_lang.items()}


def adjusted_rand(a: Sequence, b: Sequence) -> float:
    from sklearn.metrics import adjusted_rand_score

    return float(adjusted_rand_score(list(a), list(b))) if len(a) > 1 else float("nan")


def ranking(scores: Sequence[float], gold: Sequence[bool], *, top: float = 0.1) -> dict[str, float]:
    """How well a score ranks the gold first: ROC AUC, and precision in the best *top* share."""
    s = np.asarray(scores, dtype=float)
    g = np.asarray(gold, dtype=bool)
    out = {"auc": float("nan"), "p_top": float("nan")}
    if len(s) == 0:
        return out
    order = np.argsort(-s, kind="stable")
    k = max(1, int(round(top * len(s))))
    out["p_top"] = float(g[order[:k]].mean())
    n1, n0 = int(g.sum()), int((~g).sum())
    if n1 and n0:
        ranks = np.empty(len(s))
        ranks[s.argsort(kind="stable")] = np.arange(1, len(s) + 1)
        _, inv = np.unique(s, return_inverse=True)
        ranks = (np.bincount(inv, weights=ranks) / np.bincount(inv))[inv]
        out["auc"] = float((ranks[g].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))
    return out
