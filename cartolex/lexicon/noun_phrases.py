# SPDX-License-Identifier: MIT
"""Noun-phrase candidates: part-of-speech patterns over parsed text.

The keyword extraction finds its candidate terms here. A parsed text (a spaCy
``Doc``) is cut into *word units* — a word, or a compound joined by a hyphen or
a slash without spaces (``sand-gravel``, ``îles-barrières``) — and each unit
gets a class:

====  ===========================================================
``N`` a noun
``R`` a proper noun
``A`` an adjective, or a participle used as one
``G`` (English) a gerund used as a noun (``decision making``)
``P`` a preposition the language's pattern allows inside a term
``D`` a definite article after such a preposition (French, Portuguese)
``X`` anything else: it breaks a phrase
====  ===========================================================

Punctuation, line breaks, numbers, addresses, one-letter words and the
language's short list of function words
(``cartolex/_data/stopwords/function_words.json``) break a phrase; the
parser's sentence boundaries are not used (inside a stretch without
punctuation they are mostly errors, such as a boundary inside a hyphenated
word). The classes of a stretch form a string, and every contiguous
span of at most :data:`MAX_UNITS` units that fully matches the language's
pattern is one occurrence of a candidate — nested spans included, so
``sediment transport model`` also counts ``sediment transport``,
``transport model``, ``sediment``, ``transport`` and ``model``:

- English: ``(ADJ|NOUN|PROPN)* (NOUN|PROPN|gerund)``, with at most one ``of``
  complement (``degrees of freedom``);
- French: ``NOUN ADJ* ((de|du|des|d'|à|au|aux) DET? (NOUN|PROPN) ADJ*)?``;
- Portuguese: the French shape with ``de``, ``em``, ``por``, ``para``,
  ``com``, ``a`` and their contractions with the article (``do``, ``da``,
  ``no``, ``pela``, ``ao``, ``à`` …). spaCy's Portuguese tokenizer keeps a
  contraction as one token tagged as a preposition, so ``linha da costa`` is
  ``N P N``.

Occurrences are grouped by a *key*: each content unit is replaced by the
corpus lemma of its words (the lemma the corpus most often gives that word,
so a word the tagger hesitates on stays in one group), a preposition by its
base form (``du``, ``des`` → ``de``; ``pela`` → ``por``) and articles are
left out. ``le trait de côte`` and ``les traits de côte`` share a key; the
term shown is the key's most frequent surface form.

The analysis of one text depends only on the text, the model and
:data:`PATTERN_VERSION` (raise it with any change to what this module
records), which is what makes it cacheable.
"""

from __future__ import annotations

import functools
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from spacy.tokens import Doc, Token

__all__ = [
    "MAX_UNITS",
    "PATTERN_VERSION",
    "PATTERNS",
    "LanguagePatterns",
    "TextAnalysis",
    "analyse",
    "function_words",
    "join_surface",
    "lemma_table",
    "occurrences",
]

#: Version of what :func:`analyse` records (classes, surfaces, patterns):
#: part of every parse-cache key.
PATTERN_VERSION = "np1"
#: Longest candidate, in word units (prepositions and articles included).
MAX_UNITS = 5

#: Characters that join two words into one unit when no space surrounds them.
JOINERS = frozenset({"-", "‐", "‑", "–", "/"})
_BREAK_POS = frozenset({"SPACE", "SYM", "NUM", "X", "PUNCT"})
_CONTENT = frozenset("NRAG")
_HEADS = frozenset("NRG")
# English dependency labels under which a gerund acts as a noun.
_NOMINAL_DEPS_EN = frozenset(
    {"pobj", "dobj", "nsubj", "nsubjpass", "conj", "appos", "attr", "ROOT"}
)


@dataclass(frozen=True)
class LanguagePatterns:
    """The pattern of one language and the function words inside it.

    ``prepositions`` maps each preposition (lower case, contractions
    included) to the base form used in keys; ``preposition_pos`` are the
    part-of-speech tags under which such a word is a preposition (French
    taggers mark ``du`` and ``des`` as determiners too); ``articles`` are the
    definite articles allowed right after a preposition.
    """

    lang: str
    pattern: str
    prepositions: Mapping[str, str]
    preposition_pos: frozenset[str]
    articles: frozenset[str] = frozenset()

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        """The pattern, compiled (over a string of unit classes)."""
        return re.compile(self.pattern)


def _prep_map(groups: Mapping[str, Iterable[str]]) -> Mapping[str, str]:
    return MappingProxyType({form: base for base, forms in groups.items() for form in forms})


_EN_NP = "[ANR]*[NRG]"

#: The patterns of the supported languages.
PATTERNS: Mapping[str, LanguagePatterns] = MappingProxyType(
    {
        "en": LanguagePatterns(
            lang="en",
            pattern=f"{_EN_NP}(?:P{_EN_NP})?",
            prepositions=_prep_map({"of": ("of",)}),
            preposition_pos=frozenset({"ADP"}),
        ),
        "fr": LanguagePatterns(
            lang="fr",
            pattern="NA*(?:PD?[NR]A*)?",
            prepositions=_prep_map(
                {"de": ("de", "d'", "d’", "du", "des"), "à": ("à", "au", "aux")}
            ),
            preposition_pos=frozenset({"ADP", "DET"}),
            articles=frozenset({"le", "la", "les", "l'", "l’"}),
        ),
        "pt": LanguagePatterns(
            lang="pt",
            pattern="NA*(?:PD?[NR]A*)?",
            prepositions=_prep_map(
                {
                    "de": ("de", "do", "da", "dos", "das"),
                    "em": ("em", "no", "na", "nos", "nas"),
                    "por": ("por", "pelo", "pela", "pelos", "pelas"),
                    "para": ("para",),
                    "com": ("com",),
                    "a": ("a", "à", "às", "ao", "aos"),
                }
            ),
            preposition_pos=frozenset({"ADP"}),
            articles=frozenset({"o", "a", "os", "as"}),
        ),
    }
)


@functools.lru_cache(maxsize=1)
def _function_word_data() -> dict[str, frozenset[str]]:
    resource = files("cartolex._data") / "stopwords" / "function_words.json"
    data = json.loads(resource.read_text(encoding="utf-8"))
    return {
        lang: frozenset(str(w).strip().lower() for w in words if str(w).strip())
        for lang, words in data.items()
        if not lang.startswith("_")
    }


def function_words(lang: str) -> frozenset[str]:
    """The short list of function words that break a phrase in *lang* (packaged)."""
    return _function_word_data().get(lang, frozenset())


# ── One text ────────────────────────────────────────────────────────────────

#: One word unit as recorded: ``(surface, class)``, or ``(surface, class,
#: pieces)`` for a compound, whose pieces are its words in lower case.
Unit = tuple


@dataclass(frozen=True)
class TextAnalysis:
    """What the candidate search keeps of one parsed text.

    ``runs`` are the maximal stretches of text without a breaking unit
    (only those that hold a possible phrase head); ``lemmas`` counts, for each
    word of a content unit (lower case), the lemmas the model gave it in this
    text: ``(word, lemma, count)`` triples, sorted.
    """

    runs: tuple[tuple[Unit, ...], ...]
    lemmas: tuple[tuple[str, str, int], ...]

    def to_json(self) -> dict[str, Any]:
        """Plain JSON values (lists), for the parse cache."""
        return {
            "runs": [
                [list(u) if len(u) == 2 else [u[0], u[1], list(u[2])] for u in run]
                for run in self.runs
            ],
            "lemmas": [list(x) for x in self.lemmas],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> TextAnalysis:
        """The analysis recorded by :meth:`to_json`."""
        runs = tuple(
            tuple(
                (str(u[0]), str(u[1])) if len(u) == 2 else (str(u[0]), str(u[1]), tuple(u[2]))
                for u in run
            )
            for run in data["runs"]
        )
        lemmas = tuple((str(w), str(lem), int(n)) for w, lem, n in data["lemmas"])
        return cls(runs, lemmas)


def _units(doc: Doc) -> list[list[Token]]:
    """Tokens grouped into word units (hyphen and slash compounds joined)."""
    units: list[list[Token]] = []
    for i, tok in enumerate(doc):
        join = False
        if i > 0 and units and not doc[i - 1].whitespace_:
            prev = doc[i - 1].text
            if prev in JOINERS or tok.text in JOINERS:
                join = not (prev in JOINERS and tok.text in JOINERS)
        if join:
            units[-1].append(tok)
        else:
            units.append([tok])
    return units


def _is_breaker(tok: Token, fwords: frozenset[str]) -> bool:
    if tok.is_punct or tok.like_url or tok.like_email or tok.like_num or tok.pos_ in _BREAK_POS:
        return True
    if len(tok.text) == 1:
        return True
    return tok.text.lower() in fwords


def unit_class(
    unit: Sequence[Token], lp: LanguagePatterns, fwords: frozenset[str] = frozenset()
) -> str:
    """The pattern class of one word unit (``X`` breaks a phrase)."""
    if len(unit) > 1:  # a hyphen or slash compound
        if unit[0].text in JOINERS or unit[-1].text in JOINERS:
            return "X"
        words = [t for t in unit if not t.is_punct and t.text not in JOINERS]
        if (
            not words
            or any(t.like_url or t.like_email for t in words)
            or all(t.like_num for t in words)
        ):
            return "X"
        return "N" if words[-1].pos_ in ("NOUN", "PROPN") else "A"
    tok = unit[0]
    low = tok.text.lower()
    if low in lp.prepositions and tok.pos_ in lp.preposition_pos:
        return "P"
    if low in lp.articles and tok.pos_ == "DET":
        return "D"
    if _is_breaker(tok, fwords):
        return "X"
    pos = tok.pos_
    if pos == "NOUN":
        return "N"
    if pos == "PROPN":
        return "R"
    if pos == "ADJ":
        return "A"
    if pos == "VERB":
        if lp.lang == "en":
            if tok.tag_ in ("VBN", "VBG"):
                if tok.dep_ in ("amod", "compound"):
                    return "A"
                if tok.tag_ == "VBG" and tok.dep_ in _NOMINAL_DEPS_EN:
                    return "G"
        elif "Part" in tok.morph.get("VerbForm"):
            return "A"
    return "X"


def _surface(unit: Sequence[Token]) -> str:
    """A unit as displayed: lower case, except proper nouns and words with inner capitals."""
    out = []
    for tok in unit:
        text = tok.text
        keep = tok.pos_ == "PROPN" or any(c.isupper() for c in text[1:])
        out.append(text if keep else text.lower())
    return "".join(out)


def analyse(doc: Doc, lang: str) -> TextAnalysis:
    """The runs and lemma counts of one parsed text (see :class:`TextAnalysis`)."""
    lp = PATTERNS[lang]
    fwords = function_words(lang)
    runs: list[tuple[Unit, ...]] = []
    lemmas: Counter[tuple[str, str]] = Counter()
    current: list[Unit] = []

    def close() -> None:
        # A run is worth keeping only if it can hold a phrase head.
        if any(u[1] in _HEADS for u in current):
            runs.append(tuple(current))
        current.clear()

    for unit in _units(doc):
        cls = unit_class(unit, lp, fwords)
        if cls == "X":
            close()
            continue
        surface = _surface(unit)
        if cls in _CONTENT:
            words = [t for t in unit if not t.is_punct and t.text not in JOINERS]
            for t in words:
                lemmas[(t.text.lower(), (t.lemma_ or t.text).lower())] += 1
            if len(unit) > 1:
                current.append((surface, cls, tuple(t.text.lower() for t in words)))
                continue
        current.append((surface, cls))
    close()
    return TextAnalysis(
        runs=tuple(runs),
        lemmas=tuple((w, lem, n) for (w, lem), n in sorted(lemmas.items())),
    )


# ── A corpus ────────────────────────────────────────────────────────────────


def lemma_table(analyses: Iterable[TextAnalysis]) -> dict[str, str]:
    """Each word's most frequent lemma over *analyses* (ties: the first in alphabetical order)."""
    counts: dict[str, Counter[str]] = {}
    for a in analyses:
        for word, lemma, n in a.lemmas:
            counts.setdefault(word, Counter())[lemma] += n
    return {w: min(c.items(), key=lambda kv: (-kv[1], kv[0]))[0] for w, c in counts.items()}


def join_surface(parts: Iterable[str]) -> str:
    """Unit surfaces joined by spaces, with no space after an elided article (``d'eau``)."""
    out = ""
    for p in parts:
        if out and not out.endswith(("'", "’")):
            out += " "
        out += p
    return out


@dataclass
class _Keyer:
    lp: LanguagePatterns
    lemmas: Mapping[str, str]
    cache: dict[Unit, str | None] = field(default_factory=dict)

    def key(self, unit: Unit) -> str | None:
        """The key part of a unit (``None`` for an article, which keys leave out)."""
        hit = self.cache.get(unit, "")
        if hit != "":
            return hit
        cls = unit[1]
        if cls == "D":
            part = None
        elif cls == "P":
            part = self.lp.prepositions.get(unit[0].lower(), unit[0].lower())
        else:
            words = unit[2] if len(unit) > 2 else (unit[0].lower(),)
            part = "-".join(self.lemmas.get(w, w) for w in words)
        self.cache[unit] = part
        return part


def occurrences(
    analyses: Iterable[TextAnalysis], lang: str, lemmas: Mapping[str, str]
) -> Iterator[tuple[str, str]]:
    """Every candidate occurrence in *analyses*: ``(key, surface)`` pairs.

    Every span of at most :data:`MAX_UNITS` units of a run that fully matches
    the pattern of *lang* is one occurrence, nested spans included; *lemmas*
    is the corpus lemma table (:func:`lemma_table`).
    """
    lp = PATTERNS[lang]
    rx = lp.regex
    keyer = _Keyer(lp, lemmas)
    for a in analyses:
        for run in a.runs:
            classes = "".join(u[1] for u in run)
            n = len(classes)
            keys = [keyer.key(u) for u in run]
            for i in range(n):
                if classes[i] not in _CONTENT:
                    continue
                for j in range(i + 1, min(i + MAX_UNITS, n) + 1):
                    if rx.fullmatch(classes, i, j):
                        key = " ".join(k for k in keys[i:j] if k is not None)
                        yield key, join_surface(u[0] for u in run[i:j])
