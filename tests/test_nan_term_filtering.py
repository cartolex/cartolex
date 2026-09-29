# SPDX-License-Identifier: MIT
"""Regression tests: a blank/NaN ``term`` cell must not crash the triage's safety net.

Under pandas >= 3 ``read_csv`` infers the new string dtype whose NA sentinel is a
float ``nan``; ``astype(str)`` no longer stringifies it, so a blank ``term`` cell in
an older ``keywords_global.csv`` reached ``is_malformed_term`` as a float and raised
``TypeError: expected string or bytes-like object, got 'float'``.
"""

from __future__ import annotations

import pandas as pd

from cartolex.context import RunContext
from cartolex.lexicon import consolidation
from cartolex.lexicon.canonicalization import canonical_concept
from cartolex.lexicon.config import KeywordsConfig
from cartolex.lexicon.llm_triage import _load_global_terms


def test_load_global_terms_drops_blank_string_term(tmp_path):
    csv = tmp_path / "keywords_global.csv"
    pd.DataFrame({"term": ["graphene", "  ", ""], "score_len": [1.0, 0.5, 0.5]}).to_csv(
        csv, index=False
    )
    terms, _ = _load_global_terms(csv)
    assert terms == ["graphene"]


def test_the_triage_never_reads_the_rejected_band(tmp_path):
    csv = tmp_path / "keywords_global.csv"
    pd.DataFrame(
        {
            "term": ["support vector machine", "data", "vector machine", "further work"],
            "score_len": [3.0, 1.0, 2.0, 1.5],
            "band": ["kept", "check", "aside", "rejected"],
        }
    ).to_csv(csv, index=False)
    terms, _ = _load_global_terms(csv)
    assert terms == ["support vector machine", "data", "vector machine"]


def test_load_global_terms_tolerates_blank_term_cell(tmp_path):
    # A pre-split keywords_global.csv with one empty term cell (parsed as NaN by pandas 3).
    csv = tmp_path / "keywords_global.csv"
    csv.write_text("term,score_len,lang\ngraphene,1.2,en\n,0.5,fr\nquantum dots,0.9,en\n")
    terms, out = _load_global_terms(csv)
    assert "graphene" in terms
    assert "quantum dots" in terms
    assert out["term"].isna().sum() == 0
    assert "" not in terms


def test_load_and_merge_raw_keywords_tolerates_blank_and_literal_nan(tmp_path):
    # A pre-split raw_keywords_*.csv may carry a blank cell (parsed as NaN by pandas 3)
    # or the literal string "nan" that an older pandas-2 run baked in via astype(str).
    ctx = RunContext.for_workspace(tmp_path, KeywordsConfig(corpus_languages=("fr", "en")))
    ctx.paths.automatic_dir.mkdir(parents=True)
    ctx.paths.raw_terms_csv("fr").write_text("term,score,score_len\ngraphene,2.0,2.2\n,0.5,0.5\n")
    ctx.paths.raw_terms_csv("en").write_text(
        "term,score,score_len\nquantum dots,1.0,1.1\nnan,0.3,0.3\n"
    )

    df = consolidation.load_and_merge_raw_keywords(ctx)

    assert df["term"].isna().sum() == 0
    assert "graphene" in df["term"].tolist()
    assert "quantum dots" in df["term"].tolist()
    assert "nan" not in df["term"].tolist()  # legacy literal "nan" stripped
    # The concept step (canonical_concept does term.strip().lower()) must not crash.
    for t in df["term"]:
        canonical_concept(t, {})
