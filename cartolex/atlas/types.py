from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import sparse


def to_dense(X) -> np.ndarray:
    """Return *X* as a dense ndarray whether it is dense or scipy-sparse.

    np.asarray() on a scipy sparse matrix silently yields a 0-d object array,
    so every consumer that genuinely needs dense data must go through here.
    """
    if sparse.issparse(X):
        return np.asarray(X.todense())
    return np.asarray(X, dtype=float)


def col_sums(X) -> np.ndarray:
    """1-D per-column sums for dense or sparse matrices (X.sum(axis=0) is 2-D
    on scipy matrices)."""
    return np.asarray(X.sum(axis=0)).ravel()


@dataclass
class LexicalData:
    # CSR (a dense array is accepted too); consumers needing dense must use
    # to_dense()/col_sums().
    X: np.ndarray | sparse.csr_matrix
    terms: list[str]
    individuals: list[str]
    meta_ind: pd.DataFrame
    # Plain term-frequency track (same shape as X, from the score_tf column).
    # None when the keyword table has no score_tf column.
    X_tf: np.ndarray | sparse.csr_matrix | None = None


@dataclass
class Embeddings:
    Z_ind: np.ndarray
    Z_terms: np.ndarray
    umap_ind: np.ndarray | None
    umap_terms: np.ndarray | None
