# SPDX-License-Identifier: MIT
"""Tests for the occurrence/concordance scanner (audit provenance).

The scanner must agree EXACTLY with the sklearn vectorizer that produced every
pipeline count — same token regex, lowercasing, accents kept, overlapping
n-grams all counted — while adding what sklearn cannot give: character spans
in the ORIGINAL text and word-window context snippets. Synthetic strings only.
"""

from __future__ import annotations

import pytest
from sklearn.feature_extraction.text import CountVectorizer

from cartolex.lexicon.occurrences import (
    TOKEN_PATTERN,
    filter_chronological_forms,
    find_ngram_spans,
    is_chronological_term,
    iter_word_tokens,
    scan_concept_occurrences,
)

# ── analyzer agreement ──────────────────────────────────────────────────────

ADVERSARIAL_TEXTS = [
    "Étude de l'électron et des électrons libres",
    "ÉLECTRON électron Electron",
    "le couplage spin-orbit et le SPIN-Orbit fort",
    "l’électron rapide (apostrophe typographique)",
    "le CO2 et co2 puis x_1 x_1 encore",
    "a b ab ba a",
    "alpha\n\nbeta   gamma\tdelta",
    "aa aa aa",
    "theory of open quantum systems and open quantum systems theory",
    "",
]


@pytest.mark.parametrize("text", ADVERSARIAL_TEXTS)
def test_tokens_match_sklearn_analyzer(text: str) -> None:
    """The unigram token stream is bit-identical to sklearn's analyzer."""
    analyzer = CountVectorizer(lowercase=True, token_pattern=TOKEN_PATTERN).build_analyzer()
    ours = [tok for _, _, tok in iter_word_tokens(text)]
    assert ours == analyzer(text)


COUNT_CASES = [
    # (text, term) — counts must equal CountVectorizer's restricted-vocabulary count
    ("Étude de l'électron et des électrons libres", "électron"),
    ("Étude de l'électron et des électrons libres", "électrons"),
    ("ÉLECTRON électron Electron", "électron"),
    ("le couplage spin-orbit et le SPIN-Orbit fort", "spin orbit"),
    ("le couplage spin-orbit et le SPIN-Orbit fort", "spin"),
    ("le CO2 et co2 puis x_1 x_1 encore", "co2"),
    ("le CO2 et co2 puis x_1 x_1 encore", "x_1"),
    ("aa aa aa", "aa aa"),  # overlapping bigram occurrences: sklearn counts 2
    ("machine learning et deep learning", "learning"),
    ("machine learning et deep learning", "machine learning"),
    ("alpha\n\nbeta   gamma\tdelta", "beta gamma"),
    (
        "theory of open quantum systems and open quantum systems theory",
        "open quantum systems theory",
    ),
    ("rien ici", "absent"),
    ("", "vide"),
]


@pytest.mark.parametrize(("text", "term"), COUNT_CASES)
def test_span_counts_match_countvectorizer(text: str, term: str) -> None:
    """len(find_ngram_spans) == the count sklearn reports for that exact n-gram."""
    n = len(term.split())
    vec = CountVectorizer(
        lowercase=True,
        token_pattern=TOKEN_PATTERN,
        ngram_range=(n, n),
        vocabulary=[term],
    )
    expected = int(vec.fit_transform([text]).toarray()[0, 0])
    spans = find_ngram_spans(iter_word_tokens(text), term)
    assert len(spans) == expected


def test_spans_point_into_original_text() -> None:
    """Spans index the ORIGINAL text: verbatim surface, case and accents kept."""
    text = "Le SPIN-Orbit  coupling puis le spin orbit encore"
    spans = find_ngram_spans(iter_word_tokens(text), "spin orbit")
    assert [text[s:e] for s, e in spans] == ["SPIN-Orbit", "spin orbit"]


def test_spans_are_in_document_order() -> None:
    text = "aa bb aa bb aa"
    spans = find_ngram_spans(iter_word_tokens(text), "aa")
    assert spans == sorted(spans)
    assert len(spans) == 3


# ── snippet windows ─────────────────────────────────────────────────────────


def _words(prefix: str, n: int) -> list[str]:
    return [f"{prefix}{i:02d}" for i in range(n)]


def test_snippet_window_is_ten_words_each_side() -> None:
    words = _words("w", 12) + ["kw"] + _words("v", 12)
    text = " ".join(words)
    result = scan_concept_occurrences(text, {"kw": ["kw"]})
    snip = result["kw"].snippets[0]
    assert snip.match == "kw"
    assert snip.before.split() == _words("w", 12)[2:]  # exactly the 10 preceding words
    assert snip.after.split() == _words("v", 12)[:10]  # exactly the 10 following words


def test_snippet_window_clamps_at_document_edges() -> None:
    text = "kw suite fin"
    result = scan_concept_occurrences(text, {"kw": ["kw"]})
    snip = result["kw"].snippets[0]
    assert snip.before.split() == []
    assert snip.after.split() == ["suite", "fin"]


def test_snippet_window_words_parameter() -> None:
    text = "un deux trois kw quatre cinq six"
    result = scan_concept_occurrences(text, {"kw": ["kw"]}, window_words=2)
    snip = result["kw"].snippets[0]
    assert snip.before.split() == ["deux", "trois"]
    assert snip.after.split() == ["quatre", "cinq"]


def test_snippet_is_verbatim_original_language() -> None:
    """Snippets reproduce the stored text exactly — no lowercasing, accents kept."""
    text = "Voici l'ÉLECTRON rapide, mesuré hier"
    result = scan_concept_occurrences(text, {"électron": ["électron"]})
    snip = result["électron"].snippets[0]
    assert snip.match == "ÉLECTRON"
    assert "Voici" in snip.before
    assert "rapide" in snip.after


# ── concept scanning: counts, merge policy, ordering ────────────────────────


def test_nested_aliases_both_count_like_the_fold() -> None:
    """'machine learning' + 'learning' in one concept: both count (fold semantics)."""
    text = "machine learning method"
    result = scan_concept_occurrences(text, {"ml": ["machine learning", "learning"]})
    occ = result["ml"]
    assert occ.count == 2
    assert occ.counts_by_alias == {"machine learning": 1, "learning": 1}
    # ...but the display merges the overlapping spans into ONE snippet.
    assert len(occ.snippets) == 1
    assert occ.snippets[0].match == "machine learning"
    assert occ.snippets[0].aliases == ("learning", "machine learning")


def test_different_concepts_never_merge() -> None:
    text = "machine learning method"
    result = scan_concept_occurrences(text, {"c1": ["machine learning"], "c2": ["learning"]})
    assert result["c1"].snippets[0].match == "machine learning"
    assert result["c2"].snippets[0].match == "learning"
    assert result["c1"].count == 1
    assert result["c2"].count == 1


def test_non_overlapping_same_concept_occurrences_stay_separate() -> None:
    text = "spin ici puis spin là-bas et enfin spin"
    result = scan_concept_occurrences(text, {"spin": ["spin"]})
    occ = result["spin"]
    assert occ.count == 3
    assert len(occ.snippets) == 3
    # Document order.
    positions = [text.index(s.match, len(s.before)) for s in occ.snippets]
    assert positions == sorted(positions)


def test_adjacent_tokens_do_not_merge() -> None:
    """Adjacency is not overlap: 'aa' then 'bb' stay two snippets."""
    text = "aa bb"
    result = scan_concept_occurrences(text, {"c": ["aa", "bb"]})
    assert result["c"].count == 2
    assert len(result["c"].snippets) == 2


def test_unmatched_concept_is_absent() -> None:
    result = scan_concept_occurrences("rien du tout", {"c": ["absent"], "d": ["rien"]})
    assert "c" not in result
    assert result["d"].count == 1


def test_duplicate_aliases_are_deduplicated() -> None:
    result = scan_concept_occurrences("spin ici", {"c": ["spin", "spin"]})
    assert result["c"].count == 1
    assert result["c"].counts_by_alias == {"spin": 1}


def test_empty_text_returns_empty_mapping() -> None:
    assert scan_concept_occurrences("", {"c": ["terme"]}) == {}


# ── alias normalization (punctuation-insensitive matching) ──────────────────


def test_ngram_argument_is_analyzer_normalized() -> None:
    """A hyphenated/apostrophed n-gram matches like its analyzer form."""
    tokens = iter_word_tokens("le couplage spin-orbit est fort")
    assert find_ngram_spans(tokens, "spin-orbit") == find_ngram_spans(tokens, "spin orbit")
    assert len(find_ngram_spans(tokens, "spin-orbit")) == 1


def test_hyphenated_alias_matches_hyphenated_surface() -> None:
    res = scan_concept_occurrences(
        "Effets de spin-orbit coupling remarquables", {"c": ["spin-orbit coupling"]}
    )
    assert res["c"].count == 1
    assert res["c"].snippets[0].match == "spin-orbit coupling"


def test_aliases_equal_after_normalization_count_once() -> None:
    """'spin orbit' and 'spin-orbit' are ONE analyzer form — never double-counted."""
    res = scan_concept_occurrences("le spin orbit ici", {"c": ["spin orbit", "spin-orbit"]})
    assert res["c"].count == 1
    assert sum(res["c"].counts_by_alias.values()) == 1


# ── chronological concepts: bare numerals are never valid forms ─────────────

# Field-realistic alias family for the concept "12th century": the persisted
# alias table folds bare roman-numeral raw terms into the century canonical.
CENTURY_FAMILY = {"12th century": ["12th century", "xii", "xiie", "xiie siècle"]}


def test_arrondissement_is_not_a_century_occurrence() -> None:
    """Bare 'XIIe' in an address must NOT count as '12th century' (field bug)."""
    text = "Adresse professionnelle : bureau du XIIe arrondissement de Paris"
    result = scan_concept_occurrences(text, CENTURY_FAMILY)
    assert "12th century" not in result


def test_century_with_siecle_marker_still_counts() -> None:
    """'au XIIe siècle' counts exactly once, via the marker-carrying form only."""
    text = "L'essor des abbayes au XIIe siècle en Provence"
    result = scan_concept_occurrences(text, CENTURY_FAMILY)
    occ = result["12th century"]
    assert occ.count == 1
    assert occ.counts_by_alias == {"xiie siècle": 1}
    assert occ.snippets[0].match == "XIIe siècle"


def test_regnal_and_volume_roman_numerals_do_not_count() -> None:
    """'Louis XII', 'Pie XII', 'vol. XII' are not '12th century' occurrences."""
    text = "Sous Louis XII puis Pie XII, voir les annales, vol. XII"
    family = {"12th century": ["12th century", "xii", "xiie"]}
    assert scan_concept_occurrences(text, family) == {}


def test_bare_arabic_ordinals_never_match() -> None:
    """'12e' / '12ème' alone are never valid forms of a century concept."""
    text = "Réunion dans le 12e arrondissement pour le 12ème colloque"
    family = {"12th century": ["12e", "12ème", "12e siècle"]}
    assert scan_concept_occurrences(text, family) == {}


def test_arabic_ordinal_with_siecle_counts() -> None:
    text = "les cathédrales du 12e siècle"
    family = {"12th century": ["12e", "12e siècle"]}
    occ = scan_concept_occurrences(text, family)["12th century"]
    assert occ.count == 1
    assert occ.counts_by_alias == {"12e siècle": 1}


def test_english_century_form_counts() -> None:
    text = "A survey of 12th century manuscripts"
    result = scan_concept_occurrences(text, CENTURY_FAMILY)
    assert result["12th century"].counts_by_alias == {"12th century": 1}


def test_s_abbreviation_requires_the_abbreviation_in_text() -> None:
    """'xiie s.' matches 'XIIe s.' but never a bare 'XIIe' in the text."""
    family = {"12th century": ["xiie s."]}
    hit = scan_concept_occurrences("l'art roman du XIIe s. en Provence", family)
    assert hit["12th century"].count == 1
    assert hit["12th century"].counts_by_alias == {"xiie s.": 1}
    miss = scan_concept_occurrences("bureau du XIIe arrondissement de Paris", family)
    assert miss == {}


def test_superscript_siecle_form_matches() -> None:
    text = "au XIIᵉ siècle déjà"
    family = {"12th century": ["xiiᵉ siècle", "xiiᵉ"]}
    occ = scan_concept_occurrences(text, family)["12th century"]
    assert occ.count == 1
    assert occ.counts_by_alias == {"xiiᵉ siècle": 1}


def test_other_centuries_use_the_same_rule() -> None:
    """The rule generalizes beyond XII: XIV family, 'Louis XIV' stays silent."""
    family = {"14th century": ["14th century", "xiv", "xive", "xive siècle"]}
    assert scan_concept_occurrences("la cour de Louis XIV", family) == {}
    occ = scan_concept_occurrences("peinture au XIVe siècle", family)["14th century"]
    assert occ.counts_by_alias == {"xive siècle": 1}


def test_non_chronological_concepts_keep_bare_numeral_like_aliases() -> None:
    """Only chronological concepts are filtered — 'b12' still matches."""
    result = scan_concept_occurrences("carence en vitamine B12", {"vitamin b12": ["b12"]})
    assert result["vitamin b12"].count == 1


@pytest.mark.parametrize(
    "term",
    [
        "12th century",
        "xiie siècle",
        "XIIe siècle",
        "12e siècle",
        "12ème siècle",
        "XIIᵉ siècle",
        "12th-13th centuries",
        "xiie et xiiie siècles",
        "iie millénaire",
        "2nd millennium",
    ],
)
def test_is_chronological_term_true(term: str) -> None:
    assert is_chronological_term(term)


@pytest.mark.parametrize(
    "term",
    [
        "bronze age",
        "moyen âge",
        "siècle des lumières",
        "quantum mechanics",
        "xii",
        "xiie",
        "12e",
        "vitamin b12",
        "millennium development goals",
        "",
    ],
)
def test_is_chronological_term_false(term: str) -> None:
    assert not is_chronological_term(term)


def test_filter_chronological_forms_drops_bare_numerals() -> None:
    forms = ["12th century", "xii", "xiie", "xiie siècle", "xiie s.", "12e", "12ème"]
    assert filter_chronological_forms("12th century", forms) == [
        "12th century",
        "xiie siècle",
        "xiie s.",
    ]


def test_filter_chronological_forms_identity_for_other_terms() -> None:
    forms = ["ml", "machine learning", "b12"]
    assert filter_chronological_forms("machine learning", forms) == forms
