# SPDX-License-Identifier: MIT
"""Noun-phrase candidates on hand-built parses: no language model is needed.

Each document is written token by token with its part of speech, lemma and,
where a rule reads them, its tag, dependency label and morphology, so these
tests pin the patterns themselves, independently of any model's tagging.
"""

from __future__ import annotations

from collections import Counter

import pytest

spacy = pytest.importorskip("spacy")
from spacy.tokens import Doc  # noqa: E402

from cartolex.lexicon import noun_phrases as npx  # noqa: E402


def make_doc(lang: str, tokens: list[tuple]) -> Doc:
    """A parsed document from ``(text, pos, lemma[, tag, dep, morph, space])`` tuples.

    ``space`` (default True) says whether a space follows the token.
    """
    words, pos, lemmas, tags, deps, morphs, spaces = [], [], [], [], [], [], []
    for tok in tokens:
        text, p, lemma = tok[:3]
        tag = tok[3] if len(tok) > 3 and tok[3] else p
        dep = tok[4] if len(tok) > 4 and tok[4] else "dep"
        morph = tok[5] if len(tok) > 5 else ""
        space = tok[6] if len(tok) > 6 else True
        words.append(text)
        pos.append(p)
        lemmas.append(lemma)
        tags.append(tag)
        deps.append(dep)
        morphs.append(morph)
        spaces.append(space)
    vocab = spacy.blank(lang).vocab
    return Doc(
        vocab,
        words=words,
        spaces=spaces,
        pos=pos,
        lemmas=lemmas,
        tags=tags,
        deps=deps,
        morphs=morphs,
        heads=list(range(len(words))),
    )


def candidates(lang: str, *docs: Doc, lp: npx.LanguagePatterns | None = None) -> Counter:
    """``{(key, surface): occurrences}`` over *docs*, with their own lemma table."""
    analyses = [npx.analyse(d, lang) for d in docs]
    table = npx.lemma_table(analyses)
    return Counter(npx.occurrences(analyses, lang, table, lp=lp))


def keys(counter: Counter) -> set[str]:
    return {k for k, _ in counter}


def surfaces(counter: Counter) -> set[str]:
    return {s for _, s in counter}


# ── French ──────────────────────────────────────────────────────────────────

TRAIT_DE_COTE = [
    ("L'", "DET", "le", "", "", "", False),
    ("évolution", "NOUN", "évolution"),
    ("du", "ADP", "de"),
    ("trait", "NOUN", "trait"),
    ("de", "ADP", "de"),
    ("côte", "NOUN", "côte", "", "", "", False),
    (".", "PUNCT", "."),
]


def test_a_french_term_with_a_short_word_is_kept_whole() -> None:
    """« trait de côte »: its two-letter preposition no longer splits the term."""
    found = candidates("fr", make_doc("fr", TRAIT_DE_COTE))
    assert ("trait de côte", "trait de côte") in found
    # One complement at most: the doubly nested phrase is not a candidate.
    assert "évolution de trait de côte" not in keys(found)
    assert {"évolution de trait", "trait", "côte", "évolution"} <= keys(found)


def test_french_nested_spans_all_count() -> None:
    doc = make_doc(
        "fr",
        [
            ("la", "DET", "le"),
            ("variabilité", "NOUN", "variabilité"),
            ("interannuelle", "ADJ", "interannuel"),
            ("du", "ADP", "de"),
            ("niveau", "NOUN", "niveau"),
            ("marin", "ADJ", "marin"),
        ],
    )
    found = surfaces(candidates("fr", doc))
    assert found == {
        "variabilité",
        "variabilité interannuelle",
        "variabilité interannuelle du niveau",
        "variabilité interannuelle du niveau marin",
        "niveau",
        "niveau marin",
    }


def test_french_contractions_and_articles_group_by_lemma() -> None:
    """« les traits de côte » and « le trait de côte » share one key; the article is not in it."""
    singular = make_doc(
        "fr",
        [
            ("le", "DET", "le"),
            ("trait", "NOUN", "trait"),
            ("de", "ADP", "de"),
            ("côte", "NOUN", "côte"),
        ],
    )
    plural = make_doc(
        "fr",
        [
            ("les", "DET", "le"),
            ("traits", "NOUN", "trait"),
            ("de", "ADP", "de"),
            ("côte", "NOUN", "côte"),
        ],
    )
    found = candidates("fr", singular, plural)
    assert found[("trait de côte", "trait de côte")] == 1
    assert found[("trait de côte", "traits de côte")] == 1
    elided = make_doc(
        "fr",
        [
            ("la", "DET", "le"),
            ("masse", "NOUN", "masse"),
            ("d'", "ADP", "de", "", "", "", False),
            ("eau", "NOUN", "eau"),
            ("des", "DET", "un"),
            ("plages", "NOUN", "plage"),
            ("à", "ADP", "à"),
            ("la", "DET", "le"),
            ("houle", "NOUN", "houle"),
        ],
    )
    found = candidates("fr", elided)
    assert ("masse de eau", "masse d'eau") in found
    # « des » tagged as a determiner is still the preposition « de » + article.
    assert ("eau de plage", "eau des plages") in found
    # The article after « à » is allowed, and left out of the key.
    assert ("plage à houle", "plages à la houle") in found


def test_french_participle_is_an_adjective_and_function_words_break() -> None:
    doc = make_doc(
        "fr",
        [
            ("les", "DET", "le"),
            ("zones", "NOUN", "zone"),
            ("inondées", "VERB", "inonder", "", "", "VerbForm=Part"),
            ("et", "CCONJ", "et"),
            ("d'", "DET", "de", "", "", "", False),
            ("autres", "ADJ", "autre"),
            ("sites", "NOUN", "site"),
        ],
    )
    found = surfaces(candidates("fr", doc))
    assert "zones inondées" in found
    assert "autres sites" not in found and "sites" in found


# ── Portuguese ──────────────────────────────────────────────────────────────


def test_a_portuguese_term_with_a_contraction_is_kept_whole() -> None:
    """« linha de costa », « nível do mar »: spaCy keeps the contraction as one preposition."""
    doc = make_doc(
        "pt",
        [
            ("A", "DET", "o"),
            ("linha", "NOUN", "linha"),
            ("de", "ADP", "de"),
            ("costa", "NOUN", "costa"),
            ("e", "CCONJ", "e"),
            ("o", "DET", "o"),
            ("nível", "NOUN", "nível"),
            ("do", "ADP", "de o"),
            ("mar", "NOUN", "mar", "", "", "", False),
            (".", "PUNCT", "."),
        ],
    )
    found = candidates("pt", doc)
    assert ("linha de costa", "linha de costa") in found
    assert ("nível de mar", "nível do mar") in found


def test_portuguese_contractions_share_their_preposition() -> None:
    one = make_doc(
        "pt",
        [("erosão", "NOUN", "erosão"), ("da", "ADP", "de o"), ("praia", "NOUN", "praia")],
    )
    many = make_doc(
        "pt",
        [("erosão", "NOUN", "erosão"), ("das", "ADP", "de o"), ("praias", "NOUN", "praia")],
    )
    by_prep = make_doc(
        "pt",
        [
            ("transporte", "NOUN", "transporte"),
            ("pela", "ADP", "por o"),
            ("corrente", "NOUN", "corrente"),
            ("para", "ADP", "para"),
            ("a", "DET", "o"),
            ("costa", "NOUN", "costa"),
        ],
    )
    found = candidates("pt", one, many, by_prep)
    assert found[("erosão de praia", "erosão da praia")] == 1
    assert found[("erosão de praia", "erosão das praias")] == 1
    assert ("transporte por corrente", "transporte pela corrente") in found
    assert ("corrente para costa", "corrente para a costa") in found


def test_portuguese_article_is_not_a_preposition() -> None:
    """« a » tagged as an article only follows a preposition; it never joins two nouns."""
    doc = make_doc(
        "pt",
        [("praia", "NOUN", "praia"), ("a", "DET", "o"), ("costa", "NOUN", "costa")],
    )
    assert keys(candidates("pt", doc)) == {"praia", "costa"}


# ── English ─────────────────────────────────────────────────────────────────


def test_english_nested_spans_and_head_rule() -> None:
    doc = make_doc(
        "en",
        [
            ("the", "DET", "the"),
            ("sediment", "NOUN", "sediment"),
            ("transport", "NOUN", "transport"),
            ("models", "NOUN", "model"),
            ("are", "AUX", "be"),
            ("coastal", "ADJ", "coastal"),
        ],
    )
    found = candidates("en", doc)
    assert keys(found) == {
        "sediment",
        "transport",
        "model",
        "sediment transport",
        "transport model",
        "sediment transport model",
    }
    assert ("sediment transport model", "sediment transport models") in found


def test_english_additions() -> None:
    """Participle modifiers, gerund heads, compounds, proper nouns; the ``of`` complement switch."""
    doc = make_doc(
        "en",
        [
            ("distributed", "VERB", "distribute", "VBN", "amod"),
            ("systems", "NOUN", "system", "NNS", "pobj", "", False),
            (",", "PUNCT", ","),
            ("decision", "NOUN", "decision", "NN", "compound"),
            ("making", "VERB", "make", "VBG", "pobj", "", False),
            (",", "PUNCT", ","),
            ("degrees", "NOUN", "degree"),
            ("of", "ADP", "of"),
            ("freedom", "NOUN", "freedom", "", "", "", False),
            (",", "PUNCT", ","),
            ("sand", "NOUN", "sand", "", "", "", False),
            ("-", "PUNCT", "-", "HYPH", "", "", False),
            ("gravel", "NOUN", "gravel"),
            ("beaches", "NOUN", "beach"),
            ("in", "ADP", "in"),
            ("Atlantic", "PROPN", "Atlantic"),
            ("estuaries", "NOUN", "estuary"),
        ],
    )
    found = candidates("en", doc)
    s = surfaces(found)
    assert {"distributed systems", "decision making", "degrees", "freedom"} <= s
    # The ``of`` complement is a switch of the lexicon lab, off by default.
    assert "degrees of freedom" not in s
    with_of = surfaces(candidates("en", doc, lp=npx.language_patterns("en", of_complement=True)))
    assert with_of - s == {"degrees of freedom"}
    assert ("sand-gravel beach", "sand-gravel beaches") in found
    assert ("atlantic estuary", "Atlantic estuaries") in found
    # The preposition « in » is not part of the English pattern.
    assert not any(" in " in x for x in s)


def test_breakers() -> None:
    """Numbers, one-letter words, punctuation and function words end a phrase."""
    doc = make_doc(
        "en",
        [
            ("other", "ADJ", "other"),
            ("storm", "NOUN", "storm"),
            ("events", "NOUN", "event"),
            ("in", "ADP", "in"),
            ("2012", "NUM", "2012"),
            ("x", "NOUN", "x"),
            ("ray", "NOUN", "ray"),
            ("such", "ADJ", "such"),
            ("waves", "NOUN", "wave"),
        ],
    )
    s = surfaces(candidates("en", doc))
    assert "other storm events" not in s and "storm events" in s
    assert "x ray" not in s and "ray" in s
    assert "such waves" not in s and "waves" in s


def test_a_compound_cut_at_its_edge_is_not_a_unit() -> None:
    doc = make_doc(
        "en",
        [
            ("sand", "NOUN", "sand", "", "", "", False),
            ("-", "PUNCT", "-"),
            ("and", "CCONJ", "and"),
            ("gravel", "NOUN", "gravel"),
            ("beaches", "NOUN", "beach"),
        ],
    )
    s = surfaces(candidates("en", doc))
    assert "sand-" not in s and "gravel beaches" in s


def test_longest_candidate() -> None:
    words = [("wave", "NOUN", "wave")] * 7
    s = surfaces(candidates("en", make_doc("en", words)))
    assert max(len(x.split()) for x in s) == npx.MAX_UNITS


# ── Keys, surfaces and the recorded analysis ────────────────────────────────


def test_display_keeps_proper_nouns_and_inner_capitals() -> None:
    doc = make_doc(
        "en",
        [("The", "DET", "the"), ("ADCP", "NOUN", "adcp"), ("Mooring", "NOUN", "mooring")],
    )
    assert surfaces(candidates("en", doc)) == {"ADCP", "mooring", "ADCP mooring"}


def test_corpus_lemma_keeps_a_word_in_one_group() -> None:
    """A word the tagger lemmatises two ways is keyed by its most frequent lemma."""
    a = make_doc("en", [("data", "NOUN", "datum"), ("sets", "NOUN", "set")])
    b = make_doc("en", [("data", "NOUN", "datum"), ("sets", "NOUN", "set")])
    c = make_doc("en", [("data", "NOUN", "data"), ("sets", "NOUN", "set")])
    found = candidates("en", a, b, c)
    assert found[("datum set", "data sets")] == 3


def test_analysis_round_trips_through_json() -> None:
    doc = make_doc(
        "en",
        [
            ("sand", "NOUN", "sand", "", "", "", False),
            ("-", "PUNCT", "-", "", "", "", False),
            ("gravel", "NOUN", "gravel"),
            ("beaches", "NOUN", "beach"),
        ],
    )
    a = npx.analyse(doc, "en")
    assert npx.TextAnalysis.from_json(a.to_json()) == a
    assert a.lemmas == (("beaches", "beach", 1), ("gravel", "gravel", 1), ("sand", "sand", 1))


def test_function_word_lists_are_short_and_packaged() -> None:
    for lang in ("en", "fr", "pt"):
        words = npx.function_words(lang)
        assert 10 < len(words) < 80
        # Content words the n-gram extraction's stop lists used to block are absent.
        assert not words & {"recrutement", "analysis", "data", "método", "étude"}
