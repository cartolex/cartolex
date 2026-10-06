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
from cartolex.lexicon.text_utils import term_words  # noqa: E402


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


def test_a_french_phrase_after_an_elided_word_starts_as_after_a_space() -> None:
    """« choix de l'apprentissage profond » and « choix des méthodes statistiques » nest alike."""
    elided = make_doc(
        "fr",
        [
            ("le", "DET", "le"),
            ("choix", "NOUN", "choix"),
            ("de", "ADP", "de"),
            ("l’", "DET", "le", "", "", "", False),
            ("apprentissage", "NOUN", "apprentissage"),
            ("profond", "ADJ", "profond"),
        ],
    )
    spaced = make_doc(
        "fr",
        [
            ("le", "DET", "le"),
            ("choix", "NOUN", "choix"),
            ("des", "ADP", "de"),
            ("méthodes", "NOUN", "méthode"),
            ("statistiques", "ADJ", "statistique"),
        ],
    )
    lp = npx.PATTERNS["fr"]
    for doc, inner, outer in (
        (elided, "apprentissage profond", "choix de apprentissage profond"),
        (spaced, "méthode statistique", "choix de méthode statistique"),
    ):
        a = npx.analyse(doc, "fr")
        found = {s.key: s for s in npx.spans(a, lp, npx.lemma_table([a]))}
        # The inner phrase is found where it starts, with the longer one around it.
        assert found[inner].classes == "NA"
        assert found[inner].containers == (outer,)
        assert found[outer].classes in ("NPDNA", "NPNA")
    assert "choix de l’apprentissage profond" in surfaces(candidates("fr", elided))


def test_french_elided_pronouns_and_conjunctions_break_a_phrase() -> None:
    """« qu' », « s' », « n' » … are not part of a term: the noun after them starts one."""
    doc = make_doc(
        "fr",
        [
            ("qu'", "SCONJ", "que", "", "", "", False),
            ("aucune", "DET", "aucun"),
            ("étude", "NOUN", "étude"),
            ("s'", "PRON", "se", "", "", "", False),
            ("appuie", "VERB", "appuyer"),
            ("sur", "ADP", "sur"),
            ("l'", "DET", "le", "", "", "", False),
            ("érosion", "NOUN", "érosion"),
            ("côtière", "ADJ", "côtier"),
        ],
    )
    assert keys(candidates("fr", doc)) == {"étude", "érosion", "érosion côtier"}


def test_a_french_elision_left_attached_by_the_tokenizer_is_split() -> None:
    """A token « l'apprentissage » or « d'eau » is cut as the French models usually cut it."""
    attached = make_doc(
        "fr",
        [
            ("l'apprentissage", "NOUN", "apprentissage"),
            ("automatique", "ADJ", "automatique"),
            ("et", "CCONJ", "et"),
            ("la", "DET", "le"),
            ("masse", "NOUN", "masse"),
            ("D’eau", "NOUN", "d’eau"),
            ("et", "CCONJ", "et"),
            ("qu'estuaire", "NOUN", "qu'estuaire"),
            ("et", "CCONJ", "et"),
            ("aujourd'hui", "ADV", "aujourd'hui"),
            ("presqu'île", "NOUN", "presqu'île"),
        ],
    )
    found = candidates("fr", attached)
    assert ("apprentissage automatique", "apprentissage automatique") in found
    assert ("masse de eau", "masse d’eau") in found
    # « qu' » breaks the phrase; a word with an apostrophe inside stays whole.
    assert "estuaire" in keys(found) and "presqu'île" in keys(found)
    a = npx.analyse(attached, "fr")
    assert a.runs[0] == (("l'", "D"), ("apprentissage", "N"), ("automatique", "A"))


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


def test_a_portuguese_elided_preposition_is_a_word_unit() -> None:
    """« coluna d'água »: the model keeps « d'água » as one token; it is split into « d' » and « água »."""
    doc = make_doc(
        "pt",
        [
            ("a", "DET", "o"),
            ("coluna", "NOUN", "coluna"),
            ("d'água", "NOUN", "d'água"),
            ("e", "CCONJ", "e"),
            ("os", "DET", "o"),
            ("níveis", "NOUN", "nível"),
            ("d’água", "NOUN", "d’águo"),
            ("extremos", "ADJ", "extremo"),
        ],
    )
    found = candidates("pt", doc)
    assert ("coluna de água", "coluna d'água") in found
    assert ("nível de água extremo", "níveis d’água extremos") in found
    # The word after the elision is a candidate of its own, as after a space.
    assert ("água", "água") in found
    assert not any(k.startswith(("d'", "d’")) for k in keys(found))
    a = npx.analyse(doc, "pt")
    assert a.runs[0] == (("a", "D"), ("coluna", "N"), ("d'", "P"), ("água", "N"))
    assert {("água", "água", 1), ("água", "águo", 1)} <= set(a.lemmas)
    # A word with an apostrophe inside it is one word: « olho-d'água ».
    compound = make_doc("pt", [("o", "DET", "o"), ("olho-d'água", "NOUN", "olho-d'água")])
    assert keys(candidates("pt", compound)) == {"olho-d'água"}


def test_portuguese_contractions_are_words_not_elisions() -> None:
    """« do », « pelo », « nas » are prepositions of their own; nothing is split off them."""
    doc = make_doc(
        "pt",
        [
            ("nível", "NOUN", "nível"),
            ("do", "ADP", "de o"),
            ("mar", "NOUN", "mar"),
            ("pelo", "ADP", "por o"),
            ("método", "NOUN", "método"),
            ("nas", "ADP", "em o"),
            ("praias", "NOUN", "praia"),
        ],
    )
    a = npx.analyse(doc, "pt")
    assert [c for _, c in a.runs[0]] == list("NPNPNPN")
    found = candidates("pt", doc)
    assert ("nível de mar", "nível do mar") in found
    assert ("método em praia", "método nas praias") in found


def test_portuguese_article_is_not_a_preposition() -> None:
    """« a » tagged as an article only follows a preposition; it never joins two nouns."""
    doc = make_doc(
        "pt",
        [("praia", "NOUN", "praia"), ("a", "DET", "o"), ("costa", "NOUN", "costa")],
    )
    assert keys(candidates("pt", doc)) == {"praia", "costa"}


# ── Spanish, Italian, German ────────────────────────────────────────────────


def test_spanish_terms_with_their_prepositions() -> None:
    """« nivel del mar », « lesión por presión »; an article after a preposition is left out of keys."""
    doc = make_doc(
        "es",
        [
            ("el", "DET", "el"),
            ("nivel", "NOUN", "nivel"),
            ("del", "ADP", "del"),
            ("mar", "NOUN", "mar"),
            ("y", "CCONJ", "y"),
            ("las", "DET", "el"),
            ("lesiones", "NOUN", "lesión"),
            ("por", "ADP", "por"),
            ("presión", "NOUN", "presión", "", "", "", False),
            (",", "PUNCT", ","),
            ("educación", "NOUN", "educación"),
            ("para", "ADP", "para"),
            ("la", "DET", "el"),
            ("salud", "NOUN", "salud"),
            ("en", "ADP", "en"),
            ("el", "DET", "el"),
            ("estado", "NOUN", "estado"),
            ("de", "ADP", "de"),
            ("Santa", "PROPN", "Santa"),
            ("Catarina", "PROPN", "Catarina"),
        ],
    )
    found = candidates("es", doc)
    # A name after the preposition is whole, not cut after its first word (« estado de
    # Santa » nests in it: the part-of rule sets it aside).
    assert ("estado de santa catarina", "estado de Santa Catarina") in found
    assert ("nivel de mar", "nivel del mar") in found
    assert ("lesión por presión", "lesiones por presión") in found
    assert ("educación para salud", "educación para la salud") in found
    # « y » (one letter, a conjunction) breaks the phrase: no span crosses it.
    assert not any("y" in k.split() for k in keys(found))


def test_italian_joined_and_elided_prepositions() -> None:
    """« qualità dell'acqua »: « dell' » is a preposition of its own, written without a space."""
    split = make_doc(
        "it",
        [
            ("la", "DET", "il"),
            ("qualità", "NOUN", "qualità"),
            ("dell'", "ADP", "di il", "", "", "", False),
            ("acqua", "NOUN", "acqua"),
            ("e", "CCONJ", "e"),
            ("il", "DET", "il"),
            ("livello", "NOUN", "livello"),
            ("del", "ADP", "di il"),
            ("mare", "NOUN", "mare"),
            ("a", "ADP", "a"),
            ("distanza", "NOUN", "distanza"),
        ],
    )
    # A tokenizer that leaves « dell’acqua » whole (typographic apostrophe): split here.
    whole = make_doc("it", [("qualità", "NOUN", "qualità"), ("dell’acqua", "NOUN", "dell’acqua")])
    found = candidates("it", split, whole)
    assert found[("qualità di acqua", "qualità dell'acqua")] == 1
    assert found[("qualità di acqua", "qualità dell’acqua")] == 1
    assert ("livello di mare", "livello del mare") in found
    # « a » is one letter, but a preposition of the pattern: it does not break.
    assert ("mare a distanza", "mare a distanza") in found
    assert npx.analyse(whole, "it").runs[0] == (
        ("qualità", "N"),
        ("dell’", "P"),
        ("acqua", "N"),
    )
    assert term_words("qualità dell'acqua") == ["qualità", "dell'", "acqua"]


def test_german_adjectives_nouns_and_capitals() -> None:
    """« künstliche Intelligenz »: the noun keeps its capital, the adjective is folded."""
    doc = make_doc(
        "de",
        [
            ("Künstliche", "ADJ", "künstlich"),
            ("Intelligenz", "NOUN", "Intelligenz"),
            ("und", "CCONJ", "und"),
            ("künstlicher", "ADJ", "künstlicher"),  # a lemma the lemmatizer missed
            ("Intelligenz", "NOUN", "Intelligenz"),
            (",", "PUNCT", ","),
            ("weil", "SCONJ", "weil"),
            ("die", "DET", "der"),
            ("Ergebnisse", "NOUN", "Ergebnis"),
            ("Hinweise", "NOUN", "Hinweis"),
            ("geben", "VERB", "geben"),
        ],
    )
    found = candidates("de", doc)
    assert found[("künstlich intelligenz", "künstliche Intelligenz")] == 1
    assert found[("künstlich intelligenz", "künstlicher Intelligenz")] == 1
    # German writes compounds as one word: two nouns in a row are two phrases.
    assert "ergebnis hinweis" not in keys(found)
    assert {"ergebnis", "hinweis"} <= keys(found)


def test_german_genitive_is_a_switch() -> None:
    """« Anstieg des Meeresspiegels »: only with the lab's genitive switch, only a genitive article."""
    doc = make_doc(
        "de",
        [
            ("Anstieg", "NOUN", "Anstieg"),
            ("des", "DET", "der", "", "", "Case=Gen"),
            ("Meeresspiegels", "NOUN", "Meeresspiegel"),
            ("in", "ADP", "in"),
            ("der", "DET", "der", "", "", "Case=Dat"),
            ("Küstenzone", "NOUN", "Küstenzone"),
            ("der", "DET", "der", "", "", "Case=Dat"),
            ("Nordsee", "PROPN", "Nordsee"),
        ],
    )
    assert "anstieg meeresspiegel" not in keys(candidates("de", doc))
    genitive = candidates("de", doc, lp=npx.language_patterns("de", genitive=True))
    assert ("anstieg meeresspiegel", "Anstieg des Meeresspiegels") in genitive
    # A dative article is no genitive: it breaks the phrase.
    assert not any("nordsee" in k and " " in k for k in keys(genitive))


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
    for lang in ("en", "fr", "pt", "es", "it", "de"):
        words = npx.function_words(lang)
        assert 10 < len(words) < 80
        # Content words the n-gram extraction's stop lists used to block are absent.
        assert not words & {"recrutement", "analysis", "data", "método", "étude"}


def test_closed_words_of_other_languages() -> None:
    """A stream's foreign words: the other languages' closed words, never its own."""
    en, fr, pt = npx.foreign_words("en"), npx.foreign_words("fr"), npx.foreign_words("pt")
    assert {"des", "la", "le", "un", "que", "do", "los"} <= en
    assert {"the", "and", "with"} <= fr and not fr & {"de", "des", "la", "vers"}
    assert "de" not in pt and "des" in pt
    # Content words and chemical symbols of another language are not closed words.
    assert not (en | fr | pt) & {"car", "son", "os", "an", "au", "ni", "se", "el", "sem", "tem"}
    assert not (en | fr | pt) & {"die", "war", "hat", "mit", "als", "ha", "ed", "ai", "non"}
    # German and Italian closed words reach the other streams, never their own.
    assert {"und", "der", "della", "per"} <= en & fr
    assert not npx.foreign_words("de") & {"und", "der"}
    assert not npx.foreign_words("it") & {"della", "per", "la", "il"}
    # A capitalised word begins a name; an elision left attached is the elided word.
    assert [npx.closed_form(w) for w in ("des", "LE", "La", "qu'une")] == ["des", "le", None, "qu'"]
    # A stop word judges single words: spaCy's list and the packaged words.
    assert {"relação", "the", "des"} <= npx.stop_words("pt") | npx.stop_words(
        "en"
    ) | npx.stop_words("fr")
