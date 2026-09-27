# SPDX-License-Identifier: MIT
"""The core stopword config is language-agnostic: any ``midwords_<lang>`` /
``blacklist_<lang>_base`` block is picked up, and Portuguese ships by default."""

from __future__ import annotations

from cartolex.lexicon.stopwords_config import packaged_lists

sw = packaged_lists()


def test_portuguese_midwords_shipped_and_aggregated() -> None:
    assert sw.block("midwords_pt"), "expected a Portuguese midword set in stopwords/core.json"
    # Aggregated MIDWORDS includes every language's set (fr, en, pt, …).
    assert sw.block("midwords_pt") <= sw.midwords
    assert sw.block("midwords_fr") <= sw.midwords
    assert sw.block("midwords_en") <= sw.midwords


def test_portuguese_blacklist_shipped_and_aggregated() -> None:
    assert sw.block("blacklist_pt_base"), (
        "expected a Portuguese base blacklist in stopwords/core.json"
    )
    assert sw.block("blacklist_pt_base") <= sw.basic_blacklist


def test_portuguese_articles_are_midwords() -> None:
    """Pilot feedback: the article "uma" ranked among the top PT terms —
    PT articles/contractions must be midwords so bare/leading forms are dropped."""
    for w in ("um", "uma", "o", "os", "não", "mais", "pela", "pelo"):
        assert w in sw.midwords, f"{w!r} missing from Portuguese midwords"


def test_bare_portuguese_article_filtered_from_extraction() -> None:
    import pandas as pd

    from cartolex.lexicon.lexical_filters import filter_global_terms

    df = pd.DataFrame(
        {
            "term": ["uma", "uma abordagem", "crescimento económico", "política monetária"],
            "score": [5.0, 4.0, 3.0, 2.0],
        }
    )
    out = filter_global_terms(
        df,
        names=set(),
        blacklist=set(),
        midwords=sw.midwords,
        single_blacklist=set(),
        admin_patterns=[],
        junk_patterns=[],
    )
    terms = set(out["term"])
    assert "uma" not in terms  # bare article
    assert "uma abordagem" not in terms  # leading article
    assert "crescimento económico" in terms
    assert "política monetária" in terms


def test_spanish_shipped_and_aggregated() -> None:
    assert sw.block("midwords_es"), "expected a Spanish midword set in stopwords/core.json"
    assert sw.block("blacklist_es_base"), "expected a Spanish base blacklist in stopwords/core.json"
    assert sw.block("midwords_es") <= sw.midwords
    assert sw.block("blacklist_es_base") <= sw.basic_blacklist
    # Distinctively-Spanish tokens are picked up by the language-agnostic aggregation.
    assert "del" in sw.midwords
    assert "estudio" in sw.basic_blacklist
