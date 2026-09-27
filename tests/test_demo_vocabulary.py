# SPDX-License-Identifier: MIT
"""The demo vocabulary: themes, bilingual terms, compounds and article forms."""

from __future__ import annotations

from collections import Counter

import pytest

from cartolex.demo import vocabulary as v


def test_twelve_themes_with_distinct_ids():
    ids = [t.id for t in v.THEMES]
    assert len(ids) == 12
    assert len(set(ids)) == 12
    assert set(v.THEME_BY_ID) == set(ids)


@pytest.mark.parametrize("theme", v.THEMES, ids=lambda t: t.id)
def test_each_theme_has_at_least_sixty_multiword_terms(theme):
    multiword = [t for t in theme.terms if len(t.en.split()) >= 2]
    assert len(multiword) >= 60
    assert theme.name_en and theme.name_fr
    assert theme.kind in ("natural", "social")
    assert all(n in v.THEME_BY_ID for n in theme.neighbours)


@pytest.mark.parametrize("theme", v.THEMES, ids=lambda t: t.id)
def test_every_term_has_both_forms_and_a_valid_article(theme):
    for term in theme.terms:
        assert term.en.strip() == term.en and term.en
        assert term.fr.strip() == term.fr and term.fr
        assert term.article in v.ARTICLES
    counts = Counter(t.en for t in theme.terms)
    assert not [en for en, n in counts.items() if n > 1], "duplicate term in a theme"


def test_a_term_shared_by_two_themes_is_the_same_term():
    seen: dict[str, v.Term] = {}
    for theme in v.THEMES:
        for term in theme.terms:
            assert seen.setdefault(term.en, term) == term


def test_themes_overlap_but_only_a_little():
    owners = Counter(term.en for theme in v.THEMES for term in theme.terms)
    for theme in v.THEMES:
        shared = [t for t in theme.terms if owners[t.en] > 1]
        assert shared, f"{theme.id} shares no term with another theme"
        assert len(shared) <= 0.1 * len(theme.terms)


def test_each_english_form_has_one_french_form():
    terms = v.all_terms()
    assert len({t.fr_def for t in terms}) == len(terms)


def test_french_article_forms():
    le, la, elided, les = v.parse_terms(
        """
        sediment budget | le bilan sédimentaire
        longshore drift | la dérive littorale
        dune erosion | l'érosion dunaire
        rip currents | les courants d'arrachement
        """
    )
    assert (le.fr_def, le.fr_de, le.fr_a) == (
        "le bilan sédimentaire",
        "du bilan sédimentaire",
        "au bilan sédimentaire",
    )
    assert (la.fr_de, la.fr_a) == ("de la dérive littorale", "à la dérive littorale")
    assert (elided.fr_def, elided.fr_de, elided.fr_a) == (
        "l'érosion dunaire",
        "de l'érosion dunaire",
        "à l'érosion dunaire",
    )
    assert (les.fr_de, les.fr_a) == ("des courants d'arrachement", "aux courants d'arrachement")


def test_parse_terms_rejects_malformed_lines():
    with pytest.raises(ValueError):
        v.parse_terms("no separator here")
    with pytest.raises(ValueError):
        v.parse_terms("sediment budget | bilan sédimentaire")  # no article


def test_technique_marker_and_compound_terms():
    (term,) = v.parse_terms("~ flow cytometry | la cytométrie en flux")
    assert term.technique and term.en == "flow cytometry"
    compounds = v.compose_terms(
        """
        biomass | la biomasse
        ~ surveys | les levés
        --
        hake | le merlu
        """
    )
    by_en = {t.en: t for t in compounds}
    assert by_en["hake biomass"].fr_def == "la biomasse du merlu"
    assert by_en["hake biomass"].focus == "hake"
    assert by_en["hake surveys"].technique
    assert by_en["hake surveys"].fr_def == "les levés du merlu"


def test_compounds_never_repeat_a_base_term():
    base = set()
    for theme in v.THEMES:
        base |= {t.en for t in theme.terms if not t.focus}
    compounds = {t.en for theme in v.THEMES for t in theme.terms if t.focus}
    assert compounds and not (compounds & base)


def test_methods_settings_and_drivers_are_bilingual():
    assert {m.kind for m in v.METHODS} == {"natural", "social", "any"}
    for method in v.METHODS:
        assert method.term.en and method.term.fr
    for setting in v.SETTINGS:
        assert setting.en and setting.fr
    assert any(s.social for s in v.SETTINGS)
    assert all(d.en and d.fr for d in v.DRIVERS)
