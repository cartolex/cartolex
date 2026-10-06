# SPDX-License-Identifier: MIT
"""The text space of many texts: the exact SVD through the keywords' Gram matrix."""

from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.linalg import subspace_angles
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize

from cartolex.atlas import reducers


def test_the_gram_solver_gives_the_exact_decomposition(monkeypatch) -> None:
    T = normalize(sparse.random(4000, 300, density=0.03, random_state=1, format="csr"))
    monkeypatch.setattr(reducers, "GRAM_CHUNK", 900)  # several blocks of texts
    exact = TruncatedSVD(25, algorithm="arpack", random_state=42).fit(T)
    gram = reducers.gram_svd(T, 25, TruncatedSVD(25, random_state=42))
    assert np.allclose(gram.singular_values_, exact.singular_values_, rtol=1e-10)
    assert np.max(subspace_angles(gram.components_.T, exact.components_.T)) < 1e-8
    assert np.allclose(gram.explained_variance_ratio_, exact.explained_variance_ratio_, rtol=1e-8)
    # The components' signs are scikit-learn's: the largest value of each positive.
    big = np.abs(gram.components_).argmax(axis=1)
    assert np.all(gram.components_[np.arange(25), big] > 0)
    Z = gram.transform(T[:5])
    assert Z.shape == (5, 25) and np.allclose(np.abs(Z), np.abs(exact.transform(T[:5])), atol=1e-8)
