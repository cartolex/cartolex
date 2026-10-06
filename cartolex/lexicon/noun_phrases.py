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
``D`` a definite article after such a preposition (French, Portuguese,
      Spanish, Italian), or a genitive article after a noun (German)
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

- English: ``(ADJ|NOUN|PROPN)* (NOUN|PROPN|gerund)``; one ``of`` complement
  (``degrees of freedom``) is a switch of the lexicon lab, off by default;
- French: ``NOUN ADJ* ((de|du|des|d'|à|au|aux) DET? (NOUN|PROPN) ADJ*)?``;
- Portuguese: the French shape with ``de``, ``em``, ``por``, ``para``,
  ``com``, ``a`` and their contractions with the article (``do``, ``da``,
  ``no``, ``pela``, ``ao``, ``à`` …). spaCy's Portuguese tokenizer keeps a
  contraction as one token tagged as a preposition, so ``linha da costa`` is
  ``N P N``;
- Spanish: the Portuguese shape with ``de``, ``a``, ``en``, ``por``,
  ``para``, ``con`` and the contractions ``del``, ``al`` (``nivel del mar``,
  ``lesión por presión``), the complement's noun being a noun or a run of
  proper nouns (``estado de Santa Catarina``);
- Italian: the same shape with ``di``, ``a``, ``da``, ``in``, ``su``,
  ``per``, ``con`` and the forms joined to the article (``del``, ``della``,
  ``dell'``, ``nel``, ``sulla`` …), which spaCy's Italian tokenizer keeps as
  one token (``qualità dell'acqua`` is ``N P N``);
- German: ``ADJ* (NOUN|PROPN+)``. German writes compounds as one word
  (``Meeresspiegelanstieg``), so two nouns in a row are mostly two phrases
  (``… die Ergebnisse Hinweise …``) and never one candidate. A genitive
  complement (``Anstieg des Meeresspiegels``: an article in the genitive, or
  adjectives, then a noun) is a switch of the lexicon lab, off by default,
  like the English ``of``: most such spans are phrasing (``Ziel der Arbeit``,
  ``Ergebnisse der Studie``). German nouns keep their capital in the term
  shown.

An elided word (``l'``, ``d'``, ``qu'`` … in French, ``d'`` in Portuguese,
``dell'``, ``all'``, ``un'`` … in Italian; straight or typographic apostrophe)
is a word unit of its own, and the word
after it starts a unit, as after a space: French models split it off
(``l'``, ``apprentissage``), and a token the tokenizer leaves whole
(Portuguese ``d'água``) is split here, so ``coluna d'água`` is ``N P N``.
:func:`join_surface` writes no space after an elided word, and
:func:`cartolex.lexicon.text_utils.term_words` cuts a shown term back into
the same words.

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

import dataclasses
import functools
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from .text_utils import APOSTROPHES, FRENCH_ELIDED, ITALIAN_ELIDED, split_elision

if TYPE_CHECKING:
    from spacy.tokens import Doc, Token

__all__ = [
    "MAX_UNITS",
    "PATTERN_VERSION",
    "PATTERNS",
    "LanguagePatterns",
    "Span",
    "TextAnalysis",
    "analyse",
    "closed_form",
    "closed_words",
    "foreign_words",
    "function_words",
    "join_surface",
    "language_patterns",
    "lemma_table",
    "occurrences",
    "spans",
    "stop_words",
]

#: Version of what :func:`analyse` records (classes, surfaces, patterns):
#: part of every parse-cache key.
PATTERN_VERSION = "np2"
#: Longest candidate, in word units (prepositions and articles included).
MAX_UNITS = 5
#: A paragraph whose phrases hold this many different closed words of another
#: language is read as that language (see :func:`spans`).
FOREIGN_READING = 2
#: The class of a closed word of another language in a paragraph read as that
#: language: a candidate of its own, which the scoring sets aside.
FOREIGN_CLASS = "F"

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
    definite articles allowed right after a preposition; ``elided`` the words
    the language writes elided with an apostrophe (``d'``, ``l'`` …, without
    it), each a word unit of its own even when the tokenizer leaves it
    attached to the next word. ``article_case``, when set, is the grammatical
    case an article must have to be one (German: ``Gen``, the genitive
    ``des`` or ``der`` after a noun); ``noun_capitals`` keeps the capital of
    nouns in the term shown (German writes every noun with one).
    ``noun_endings`` and ``adjective_endings`` fold the inflected forms a
    lemmatizer leaves as they are (German ``Befunden``, ``künstliche``): in a
    key, a noun's or an adjective's lemma loses the first of these endings
    whose removal gives another lemma of the corpus (``Befund``,
    ``künstlich``); a lemma no shorter lemma explains is kept.
    """

    lang: str
    pattern: str
    prepositions: Mapping[str, str]
    preposition_pos: frozenset[str]
    articles: frozenset[str] = frozenset()
    elided: frozenset[str] = frozenset()
    article_case: str = ""
    noun_capitals: bool = False
    noun_endings: tuple[str, ...] = ()
    adjective_endings: tuple[str, ...] = ()

    @functools.cached_property
    def regex(self) -> re.Pattern[str]:
        """The pattern, compiled (over a string of unit classes)."""
        return re.compile(self.pattern)


def _prep_map(groups: Mapping[str, Iterable[str]]) -> Mapping[str, str]:
    return MappingProxyType({form: base for base, forms in groups.items() for form in forms})


_EN_NP = "[ANR]*[NRG]"
# Spanish and Italian: the French shape, a complement's noun being a noun or a run of
# proper nouns (``estado de Santa Catarina``, not ``estado de Santa``).
_ROMANCE_NP = "NA*(?:PD?(?:N|R+)A*)?"
# A German head: a noun, or a run of proper nouns (``Max Planck``).
_DE_HEAD = "(?:N|R+)"
#: Italian prepositions, each with its forms joined to the article (elided ones
#: with a straight or a typographic apostrophe).
_IT_PREPOSITIONS = {
    base: (
        base,
        *(f"{stem}{end}" for end in ("l", "llo", "lla", "i", "gli", "lle")),
        *(f"{stem}ll{a}" for a in ("'", "’")),
    )
    for base, stem in (("di", "de"), ("a", "a"), ("da", "da"), ("in", "ne"), ("su", "su"))
}

#: The patterns of the supported languages.
PATTERNS: Mapping[str, LanguagePatterns] = MappingProxyType(
    {
        "en": LanguagePatterns(
            lang="en",
            pattern=_EN_NP,
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
            elided=FRENCH_ELIDED,
        ),
        "pt": LanguagePatterns(
            lang="pt",
            pattern="NA*(?:PD?[NR]A*)?",
            prepositions=_prep_map(
                {
                    "de": ("de", "d'", "d’", "do", "da", "dos", "das"),
                    "em": ("em", "no", "na", "nos", "nas"),
                    "por": ("por", "pelo", "pela", "pelos", "pelas"),
                    "para": ("para",),
                    "com": ("com",),
                    "a": ("a", "à", "às", "ao", "aos"),
                }
            ),
            preposition_pos=frozenset({"ADP"}),
            articles=frozenset({"o", "a", "os", "as"}),
            elided=frozenset({"d"}),
        ),
        "es": LanguagePatterns(
            lang="es",
            pattern=_ROMANCE_NP,
            prepositions=_prep_map(
                {
                    "de": ("de", "del"),
                    "a": ("a", "al"),
                    "en": ("en",),
                    "por": ("por",),
                    "para": ("para",),
                    "con": ("con",),
                }
            ),
            preposition_pos=frozenset({"ADP"}),
            articles=frozenset({"el", "la", "los", "las"}),
        ),
        "it": LanguagePatterns(
            lang="it",
            pattern=_ROMANCE_NP,
            prepositions=_prep_map(
                {
                    **_IT_PREPOSITIONS,
                    "di": (*_IT_PREPOSITIONS["di"], "d'", "d’"),
                    "per": ("per",),
                    "con": ("con",),
                }
            ),
            preposition_pos=frozenset({"ADP"}),
            articles=frozenset({"il", "lo", "la", "i", "gli", "le", "l'", "l’"}),
            elided=ITALIAN_ELIDED,
        ),
        "de": LanguagePatterns(
            lang="de",
            pattern=f"A*{_DE_HEAD}",
            prepositions=MappingProxyType({}),
            preposition_pos=frozenset(),
            articles=frozenset({"des", "der"}),
            article_case="Gen",
            noun_capitals=True,
            noun_endings=("en", "n", "es", "s", "e"),
            adjective_endings=("en", "em", "er", "es", "e"),
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


@functools.lru_cache(maxsize=1)
def _closed_word_data() -> dict[str, frozenset[str]]:
    resource = files("cartolex._data") / "stopwords" / "closed_words.json"
    data = json.loads(resource.read_text(encoding="utf-8"))
    return {
        lang: frozenset(str(w).strip().lower() for w in words if str(w).strip())
        for lang, words in data.items()
        if not lang.startswith("_")
    }


def closed_words(lang: str) -> frozenset[str]:
    """The closed-class words of *lang* (packaged): articles, prepositions, pronouns …"""
    return _closed_word_data().get(lang, frozenset())


def closed_form(surface: str) -> str | None:
    """*surface* in lower case when it may be a closed word, else ``None``.

    Only a word written in lower case or in capitals may be one: a
    capitalised word begins a name (``La Niña``, ``El Niño``). A word that
    starts with an elided word (``qu'une``, left whole by another language's
    tokenizer) is that elided word (``qu'``).
    """
    if not (surface.islower() or surface.isupper()):
        return None
    low = surface.lower()
    parts = split_elision(low)
    return parts[0] if parts is not None else low


@functools.cache
def foreign_words(lang: str) -> frozenset[str]:
    """The closed words of every other language the lists know, but none of *lang*'s own.

    Words that *lang* itself uses as closed words, prepositions or articles of
    its pattern, or function words are left out (``de`` is French, Spanish and
    Portuguese): in its own language, a word the tagger takes for a noun is
    one (``croissance des vers``).
    """
    own = closed_words(lang) | function_words(lang)
    lp = PATTERNS.get(lang)
    if lp is not None:
        own = own | frozenset(lp.prepositions) | lp.articles
    others = frozenset().union(*(w for code, w in _closed_word_data().items() if code != lang))
    return others - own


@functools.cache
def stop_words(lang: str) -> frozenset[str]:
    """The stop words of *lang*: spaCy's list for the language, the function and closed words.

    A candidate of one word among them is set aside. spaCy's lists hold
    words that are content words inside a phrase (``nível do mar``,
    ``bottom water``), so they never cut a phrase; the language's words
    only, since another language's list holds this language's content words
    (the French list holds ``car`` and ``bat``).
    """
    from spacy.util import get_lang_class

    try:
        spacy_words = get_lang_class(lang).Defaults.stop_words
    except Exception:  # pragma: no cover - a language spaCy does not know
        spacy_words = set()
    return (
        frozenset(str(w).lower() for w in spacy_words) | function_words(lang) | closed_words(lang)
    )


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

    def shared(self, table: dict) -> TextAnalysis:
        """The same analysis, its strings and word units shared through *table*.

        A corpus repeats the same words and units in thousands of texts: sharing
        one object per distinct value (*table* maps a value to its first copy)
        holds them once. The analysis is equal to this one.
        """

        def one(value: Any) -> Any:
            return table.setdefault(value, value)

        runs = tuple(
            tuple(
                one((one(u[0]), one(u[1])))
                if len(u) == 2
                else one((one(u[0]), one(u[1]), one(tuple(one(w) for w in u[2]))))
                for u in run
            )
            for run in self.runs
        )
        lemmas = tuple(one((one(w), one(lem), n)) for w, lem, n in self.lemmas)
        return TextAnalysis(runs, lemmas)

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


def _is_breaker(tok: Token, fwords: frozenset[str], text: str | None = None) -> bool:
    if tok.is_punct or tok.like_url or tok.like_email or tok.like_num or tok.pos_ in _BREAK_POS:
        return True
    text = tok.text if text is None else text
    if len(text) == 1:
        return True
    return text.lower() in fwords


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
    return _token_class(unit[0], lp, fwords)


def _token_class(
    tok: Token, lp: LanguagePatterns, fwords: frozenset[str], text: str | None = None
) -> str:
    """The class of a one-token unit; *text* replaces the token's text (its part after an elision)."""
    low = (tok.text if text is None else text).lower()
    if low in lp.prepositions and tok.pos_ in lp.preposition_pos:
        return "P"
    if (
        low in lp.articles
        and tok.pos_ == "DET"
        and (not lp.article_case or lp.article_case in tok.morph.get("Case"))
    ):
        return "D"
    if _is_breaker(tok, fwords, text):
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


def _shown(tok: Token, text: str, noun_capitals: bool = False) -> str:
    keep = (
        tok.pos_ == "PROPN"
        or any(c.isupper() for c in text[1:])
        or (noun_capitals and tok.pos_ == "NOUN")
    )
    return text if keep else text.lower()


def _surface(unit: Sequence[Token], noun_capitals: bool = False) -> str:
    """A unit as displayed: lower case, except proper nouns and words with inner capitals
    (and nouns, with *noun_capitals*)."""
    return "".join(_shown(tok, tok.text, noun_capitals) for tok in unit)


def _elided_parts(unit: Sequence[Token], lp: LanguagePatterns) -> tuple[str, str] | None:
    """The two words of a token the tokenizer left whole after an elided word (``d'água``).

    Portuguese models keep ``d'água`` as one token; the elided preposition is
    a word unit of its own, as French models make it, so that ``coluna
    d'água`` is a noun, a preposition and a noun.
    """
    if len(unit) != 1 or not lp.elided:
        return None
    parts = split_elision(unit[0].text)
    if parts is None or parts[0][:-1].lower() not in lp.elided:
        return None
    return parts


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
        parts = _elided_parts(unit, lp)
        if parts is not None:
            # The elided word, then the rest of the token as a word of its own.
            elided, rest = parts
            low = elided.lower()
            elided_cls = "P" if low in lp.prepositions else "D" if low in lp.articles else "X"
            if elided_cls == "X":
                close()
            else:
                current.append((low, elided_cls))
            tok = unit[0]
            cls = _token_class(tok, lp, fwords, rest)
            if cls == "X":
                close()
                continue
            if cls in _CONTENT:
                lemma = tok.lemma_ or tok.text
                lemma_parts = split_elision(lemma)
                lemma = lemma_parts[1] if lemma_parts is not None else lemma
                lemmas[(rest.lower(), lemma.lower())] += 1
            current.append((_shown(tok, rest, lp.noun_capitals), cls))
            continue
        cls = unit_class(unit, lp, fwords)
        if cls == "X":
            close()
            continue
        surface = _surface(unit, lp.noun_capitals)
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
    """Unit surfaces joined by spaces, with no space after an elided word (``d'eau``).

    :func:`cartolex.lexicon.text_utils.term_words` is its inverse.
    """
    out = ""
    for p in parts:
        if out and not out.endswith(APOSTROPHES):
            out += " "
        out += p
    return out


@dataclass
class _Keyer:
    lp: LanguagePatterns
    lemmas: Mapping[str, str]
    cache: dict[Unit, str | None] = field(default_factory=dict)
    _known: frozenset[str] | None = None

    def _folded(self, lemma: str, endings: tuple[str, ...]) -> str:
        """*lemma* without the first of *endings* whose removal gives another corpus lemma."""
        if self._known is None:
            self._known = frozenset(self.lemmas.values())
        for end in endings:
            if lemma.endswith(end) and len(lemma) - len(end) >= 3:
                base = lemma[: -len(end)]
                if base in self._known:
                    return base
        return lemma

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
            parts = [self.lemmas.get(w, w) for w in words]
            endings = (
                self.lp.noun_endings
                if cls == "N"
                else self.lp.adjective_endings
                if cls == "A"
                else ()
            )
            if endings:
                # the last word carries the inflection (``Max-Planck-Instituts``)
                parts[-1] = self._folded(parts[-1], endings)
            part = "-".join(parts)
        self.cache[unit] = part
        return part


def language_patterns(
    lang: str, *, of_complement: bool = False, genitive: bool = False
) -> LanguagePatterns:
    """The patterns of *lang*; with *of_complement*, English phrases may take one ``of``
    complement; with *genitive*, German phrases one genitive complement.

    Both complements (``degrees of freedom``, ``Anstieg des Meeresspiegels``)
    are switches of the lexicon lab, off by default: most such spans are
    phrasing (``role of silicic acid uptake``, ``Ziel der Arbeit``), and they
    made the terms inside them look like fragments of a longer phrase. The
    genitive needs no new parse: the analysis records the genitive article.
    """
    lp = PATTERNS[lang]
    if lang == "en" and of_complement:
        return dataclasses.replace(lp, pattern=f"{_EN_NP}(?:P{_EN_NP})?")
    if lang == "de" and genitive:
        return dataclasses.replace(lp, pattern=f"A*{_DE_HEAD}(?:(?:DA*|A+){_DE_HEAD})?")
    return lp


@dataclass(frozen=True)
class Span:
    """One occurrence of a candidate in a text.

    ``classes`` are its units' classes (``NAPN`` …); ``containers`` the keys of
    the longer candidates found around it in the same stretch (the spans that
    strictly contain it).
    """

    key: str
    surface: str
    classes: str
    containers: tuple[str, ...]


def _reads_as_foreign(
    analysis: TextAnalysis, foreign: frozenset[str], reading: int = FOREIGN_READING
) -> bool:
    """Whether the phrases of *analysis* hold *reading* different *foreign* words."""
    seen: set[str] = set()
    for run in analysis.runs:
        for u in run:
            if u[1] in _CONTENT:
                low = closed_form(u[0])
                if low in foreign:
                    seen.add(low)
                    if len(seen) >= reading:
                        return True
    return False


def spans(
    analysis: TextAnalysis,
    lp: LanguagePatterns,
    lemmas: Mapping[str, str],
    *,
    keyer: _Keyer | None = None,
    foreign: frozenset[str] = frozenset(),
    max_units: int = MAX_UNITS,
    foreign_reading: int = FOREIGN_READING,
) -> list[Span]:
    """Every candidate occurrence of one analysed text, with its containers (see :class:`Span`).

    Every span of at most *max_units* units (:data:`MAX_UNITS` by default) of a
    run that fully matches the pattern *lp* is one occurrence, nested spans
    included; *lemmas* is the corpus lemma table (:func:`lemma_table`).

    *foreign* are the closed words of other languages (:func:`foreign_words`),
    written in lower case or in capitals (:func:`closed_form`). A text whose
    phrases hold *foreign_reading* (:data:`FOREIGN_READING`) different ones is text
    in another language that reached this language's stream (a paragraph
    detected wrongly, a title in capitals): there each of them breaks the
    phrase it is in, and is an occurrence of its own, of class
    :data:`FOREIGN_CLASS`, when it alone would match the pattern.
    """
    rx = lp.regex
    keyer = keyer if keyer is not None else _Keyer(lp, lemmas)
    out: list[Span] = []
    runs: Iterable[Sequence[Unit]] = analysis.runs
    if foreign and _reads_as_foreign(analysis, foreign, foreign_reading):
        runs = []
        for run in analysis.runs:
            piece: list[Unit] = []
            for u in run:
                if u[1] in _CONTENT and closed_form(u[0]) in foreign:
                    runs.append(piece)
                    piece = []
                    if rx.fullmatch(u[1]):
                        out.append(Span(keyer.key(u) or u[0].lower(), u[0], FOREIGN_CLASS, ()))
                else:
                    piece.append(u)
            runs.append(piece)
    for run in runs:
        classes = "".join(u[1] for u in run)
        n = len(classes)
        keys = [keyer.key(u) for u in run]
        found: list[tuple[int, int, str]] = []
        for i in range(n):
            if classes[i] not in _CONTENT:
                continue
            for j in range(i + 1, min(i + max_units, n) + 1):
                if rx.fullmatch(classes, i, j):
                    found.append((i, j, " ".join(k for k in keys[i:j] if k is not None)))
        for i, j, key in found:
            containers = tuple(
                dict.fromkeys(k2 for a, b, k2 in found if a <= i and j <= b and (a, b) != (i, j))
            )
            out.append(Span(key, join_surface(u[0] for u in run[i:j]), classes[i:j], containers))
    return out


def occurrences(
    analyses: Iterable[TextAnalysis],
    lang: str,
    lemmas: Mapping[str, str],
    *,
    lp: LanguagePatterns | None = None,
) -> Iterator[tuple[str, str]]:
    """Every candidate occurrence in *analyses*: ``(key, surface)`` pairs.

    See :func:`spans`; *lp* defaults to the patterns of *lang*.
    """
    lp = lp if lp is not None else PATTERNS[lang]
    keyer = _Keyer(lp, lemmas)
    for a in analyses:
        for span in spans(a, lp, lemmas, keyer=keyer):
            yield span.key, span.surface
