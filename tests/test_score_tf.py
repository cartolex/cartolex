# SPDX-License-Identifier: MIT
"""Tests for the plain-TF score track (``score_tf``) recovered from TF-IDF.

The consolidation's vectorizer stores l2-normalised tf·idf. Dividing each column by
``idf_`` recovers tf up to a per-row constant, which cancels under the
per-researcher L1 normalisation used by ``compute_lexicon_weights``. The
division must happen on the *expanded* matrix, before alias folding —
folded concept columns mix per-alias IDFs and are not recoverable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer

from cartolex.lexicon.canonicalization import fold_tfidf_to_canonical
from cartolex.lexicon.tfidf_utils import compute_keywords_by_researcher

DOCS = [
    "neural net training and spin dynamics spin dynamics",
    "neural network training with photon optics and photon detectors",
    "spin dynamics in magnets with photon probes and neural net models",
]
VOCAB = ["neural net", "neural network", "spin dynamics", "photon", "training", "magnets"]
# Two aliases with different document frequencies (hence different IDFs)
# folding onto one canonical concept — the regression case for fold order.
ALIASES = {"neural net": "neural networks", "neural network": "neural networks"}
CONCEPTS = sorted({ALIASES.get(t, t) for t in VOCAB})


def _vectorize() -> tuple:
    vec = TfidfVectorizer(lowercase=True, vocabulary=VOCAB, ngram_range=(1, 2))
    X = vec.fit_transform(DOCS)
    feats = vec.get_feature_names_out()
    return vec, X, feats


def _fold(M, feats):
    return fold_tfidf_to_canonical(
        X_expanded=M, expanded_terms=feats, canonical_terms=CONCEPTS, alias_to_canon=ALIASES
    )


def _l1_rows(M) -> np.ndarray:
    A = np.asarray(M.todense(), dtype=float)
    sums = A.sum(axis=1, keepdims=True)
    return np.where(sums > 0, A / sums, 0.0)


def test_tf_recovery_survives_alias_folding() -> None:
    """L1-normalised folded X_tf equals L1-normalised folded raw counts."""
    vec, X, feats = _vectorize()
    X_tf = X.multiply(1.0 / vec.idf_).tocsr()

    counts = CountVectorizer(lowercase=True, vocabulary=VOCAB, ngram_range=(1, 2)).fit_transform(
        DOCS
    )

    got = _l1_rows(_fold(X_tf, feats))
    expected = _l1_rows(_fold(counts.astype(float), feats))
    np.testing.assert_allclose(got, expected, atol=1e-12)

    # Folding the tf·idf matrix and normalising does NOT give count shares
    # (per-alias IDFs differ) — guards against reintroducing the wrong order.
    wrong = _l1_rows(_fold(X, feats))
    assert not np.allclose(wrong, expected)


def _meta(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "last_name": [f"NAME{i}" for i in range(n)],
            "first_name": [f"F{i}" for i in range(n)],
            "unit": ["GRP0000"] * n,
        }
    )


def test_compute_keywords_by_researcher_emits_score_tf() -> None:
    vec, X, feats = _vectorize()
    X_tf = X.multiply(1.0 / vec.idf_).tocsr()
    Xf = _fold(X, feats).tocsr()
    Xf_tf = _fold(X_tf, feats).tocsr()
    counts_f = _fold(
        CountVectorizer(lowercase=True, vocabulary=VOCAB, ngram_range=(1, 2))
        .fit_transform(DOCS)
        .astype(float),
        feats,
    ).tocsr()

    feats_f = np.array(CONCEPTS, dtype=object)
    df = compute_keywords_by_researcher(
        Xf, feats_f, _meta(3), set(), top_n=10, length_alpha=2.0, X_tf=Xf_tf
    )
    assert "score_tf" in df.columns
    assert (df["score_tf"] > 0).all()

    # Per researcher, score_tf shares over the emitted terms == count shares.
    cols = {t: j for j, t in enumerate(CONCEPTS)}
    for i, (_, grp) in enumerate(df.groupby(df["last_name"], sort=True)):
        emitted = [cols[t] for t in grp["term"]]
        count_row = np.asarray(counts_f[i].todense()).ravel()[emitted]
        got = grp["score_tf"].to_numpy() / grp["score_tf"].sum()
        expected = count_row / count_row.sum()
        np.testing.assert_allclose(got, expected, atol=1e-12)


def test_compute_keywords_by_researcher_without_x_tf_has_no_column() -> None:
    _, X, feats = _vectorize()
    Xf = _fold(X, feats).tocsr()
    df = compute_keywords_by_researcher(
        Xf, np.array(CONCEPTS, dtype=object), _meta(3), set(), top_n=10, length_alpha=2.0
    )
    assert "score_tf" not in df.columns
    assert not df.empty
