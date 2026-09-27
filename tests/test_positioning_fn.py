# SPDX-License-Identifier: MIT
"""Tests for the shared text-projection function (framework-agnostic)."""

import numpy as np

from cartolex.lexicon.positioning import project_text


class _FakeTfidf:
    def get_feature_names_out(self):
        return np.array(["alpha", "beta"])

    def transform(self, docs):
        import scipy.sparse as sp

        return sp.csr_matrix(np.array([[1.0, 1.0]]))


class _FakeSvd:
    def transform(self, X):
        return np.array([[0.5, -0.5]])


def test_project_text_returns_vector_and_keywords():
    z, top = project_text(
        "alpha beta alpha",
        tfidf=_FakeTfidf(),
        restricted_terms=["alpha", "beta"],
        svd=_FakeSvd(),
        alias_map={},
        length_bonus_alpha=2.0,
        top_k=5,
    )
    assert z.shape == (2,)
    assert top[0]["term"] in {"alpha", "beta"}


class _CaptureSvd:
    """Records the (already L2-normalised) restricted vector handed to SVD."""

    def __init__(self):
        self.last_x = None

    def transform(self, X):
        self.last_x = np.asarray(X).ravel().copy()
        return np.zeros((1, 2))


class _ManyTermsTfidf:
    """Five canonical terms, each with a distinct raw TF-IDF weight."""

    terms = ["t1", "t2", "t3", "t4", "t5"]

    def get_feature_names_out(self):
        return np.array(self.terms)

    def transform(self, docs):
        import scipy.sparse as sp

        return sp.csr_matrix(np.array([[0.1, 0.2, 0.3, 0.4, 0.5]]))


def test_top_n_truncation_zeros_low_scoring_terms():
    """top_n must keep only the highest-scoring canonical terms (matches the
    embedding of the fitted persons); the rest are zeroed before SVD.

    This is what keeps projected documents on the fitted manifold — without it
    the vector is denser and points land off the map.
    """
    svd = _CaptureSvd()
    terms = _ManyTermsTfidf.terms
    project_text(
        "x",
        tfidf=_ManyTermsTfidf(),
        restricted_terms=terms,
        svd=svd,
        alias_map={},
        length_bonus_alpha=0.0,  # isolate truncation from the length bonus
        top_k=5,
        top_n=2,
    )
    nonzero = np.flatnonzero(svd.last_x)
    # Only the two highest raw scores (t4=0.4, t5=0.5) survive.
    assert set(nonzero.tolist()) == {3, 4}


def test_length_bonus_uses_canonical_token_count():
    """The length bonus must use the *canonical* term's token count, not the raw
    n-gram, so multi-word concepts are weighted as in the fitted space.
    """
    svd = _CaptureSvd()
    # Single raw feature "ab" folds (via alias) to a two-word canonical term.
    project_text(
        "x",
        tfidf=_ManyTermsTfidf(),
        restricted_terms=["two words", "t2", "t3", "t4", "t5"],
        svd=svd,
        alias_map={"t1": "two words"},
        length_bonus_alpha=1.0,
        top_k=5,
        top_n=0,
    )
    # Before L2 norm: t1's 0.1 raw → canonical "two words" with multiplier
    # (1 + 1*(2-1)) = 2 → 0.2, while single-word t5 keeps 0.5. After L2 norm the
    # ratio 0.2/0.5 is preserved; verify the two-word term got the 2x bonus.
    x = svd.last_x
    assert x[0] / x[4] == np.float64(0.2) / np.float64(0.5)


def test_a_projected_vector_is_described_by_terms_and_centroids():
    """Nearest terms by cosine; concept/subfield weights from term centroids."""
    from cartolex.lexicon.positioning import (
        concept_svd_centroids,
        scored_top_terms_for_vector,
        subfield_svd_centroids,
        subfield_weights_for_vector,
    )

    terms = ["alpha", "beta", "gamma"]
    Z = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
    z = np.array([1.0, 0.2])
    top = scored_top_terms_for_vector(z, Z, terms, k=2)
    assert [t["term"] for t in top] == ["alpha", "beta"]
    assert top[0]["score"] == round(float(1.0 / np.hypot(1.0, 0.2)), 4)
    assert scored_top_terms_for_vector(z, np.zeros((0, 2)), [], k=2) == []

    groups = [
        {"id": 1, "top_terms": ["alpha"]},
        {"id": 2, "top_terms": ["beta", "unknown"]},
        {"id": 3, "top_terms": ["unknown"]},  # no known term: skipped
        {"top_terms": ["alpha"]},  # no id: skipped
    ]
    centroids = subfield_svd_centroids(groups, Z, terms)
    assert set(centroids) == {1, 2}
    assert concept_svd_centroids(groups, Z, terms).keys() == centroids.keys()
    weights = subfield_weights_for_vector(z, centroids)
    assert [w["id"] for w in weights] == [1, 2]
    assert abs(sum(w["weight"] for w in weights) - 1.0) < 1e-3
    # Only negative similarities: no weight at all.
    assert subfield_weights_for_vector(np.array([-1.0, -1.0]), centroids) == []
    assert subfield_weights_for_vector(z, {}) == []
