from __future__ import annotations

from collections.abc import Collection

import numpy as np
import pandas as pd
from scipy import sparse

from .text_utils import length_bonus

#: How many keywords each person's list holds (``keywords_by_researcher_restricted.csv``):
#: what the app and the site show of a person, the best first. The space reads the whole
#: people × keywords matrix (:func:`researcher_matrices`), never this list.
PERSON_TERMS_LISTED = 30


def researcher_matrices(
    X: sparse.spmatrix,
    feature_names: np.ndarray,
    whitelist_set: Collection[str],
    top_n: int | None,
    length_alpha: float,
    *,
    X_tf: sparse.spmatrix | None = None,
) -> tuple[sparse.csr_matrix, sparse.csr_matrix | None, np.ndarray]:
    """Each person's keywords as matrices: ``(score, score_tf, lengths)``.

    *X* is the people × keywords TF-IDF (rows L2-normalised before the aliases were folded
    onto their keywords). ``score`` is it with the length bonus, ``score_tf`` the plain term
    frequencies of the same entries (*X_tf*, same shape; ``None`` without it), ``lengths``
    each keyword's number of words. With *top_n* ``None`` every keyword a person uses is
    kept: the whole lexicon. With a number, each person keeps their *top_n* highest TF-IDF
    keywords and the whitelisted ones they use, the rest zeroed, as the per-person lists
    of earlier versions did (:func:`compute_keywords_by_researcher`).
    """
    X = sparse.csr_matrix(X, dtype=np.float64)
    X.sort_indices()
    terms = np.asarray(feature_names)
    factor, lengths = length_bonus(terms, np.ones(len(terms)), alpha=length_alpha)
    keep = X.data > 0
    if top_n is not None:
        whitelist = np.isin(terms, np.array(sorted(whitelist_set), dtype=object))
        keep = np.zeros(X.nnz, dtype=bool)
        for i in range(X.shape[0]):
            lo, hi = X.indptr[i], X.indptr[i + 1]
            if lo == hi:
                continue
            data = X[i].toarray().ravel()
            if data.sum() == 0:
                continue
            chosen = np.zeros(len(terms), dtype=bool)
            chosen[np.argsort(data)[::-1][:top_n]] = True
            chosen |= (data > 0) & whitelist
            keep[lo:hi] = chosen[X.indices[lo:hi]] & (X.data[lo:hi] > 0)
    score = _masked(X, X.data * factor[X.indices], keep)
    tf = None
    if X_tf is not None:
        T = sparse.csr_matrix(X_tf, dtype=np.float64)
        T.sort_indices()
        if np.array_equal(T.indptr, X.indptr) and np.array_equal(T.indices, X.indices):
            values = T.data
        else:  # another pattern: read each entry of X from it
            rows = np.repeat(np.arange(X.shape[0]), np.diff(X.indptr))
            values = np.asarray(T[rows, X.indices]).ravel()
        tf = _masked(X, values, keep)
    return score, tf, lengths


def _masked(X: sparse.csr_matrix, values: np.ndarray, keep: np.ndarray) -> sparse.csr_matrix:
    """The matrix of *X*'s structure holding *values*, at the entries *keep* marks only."""
    rows = np.repeat(np.arange(X.shape[0]), np.diff(X.indptr))
    counts = np.bincount(rows[keep], minlength=X.shape[0])
    indptr = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    return sparse.csr_matrix(
        (np.asarray(values, dtype=np.float64)[keep], X.indices[keep], indptr), shape=X.shape
    )


def listed_keywords(
    score: sparse.csr_matrix,
    tf: sparse.csr_matrix | None,
    lengths: np.ndarray,
    feature_names: np.ndarray,
    meta_df: pd.DataFrame,
    limit: int = PERSON_TERMS_LISTED,
) -> pd.DataFrame:
    """Each person's list: at most *limit* keywords of their row of *score*, the highest
    first (ties by keyword), with ``last_name``, ``first_name``, ``unit``, ``term``,
    ``score``, ``len`` and, with *tf*, ``score_tf``. A person without a keyword has no row."""
    rows = np.repeat(np.arange(score.shape[0]), np.diff(score.indptr))
    cols = score.indices
    order = np.lexsort((cols, -score.data, rows))
    rank = np.arange(score.nnz) - score.indptr[rows[order]]
    chosen = order[rank < limit]
    r, c = rows[chosen], cols[chosen]
    terms = np.asarray(feature_names, dtype=object)
    out = pd.DataFrame(
        {
            "last_name": meta_df["last_name"].to_numpy()[r],
            "first_name": meta_df["first_name"].to_numpy()[r],
            "unit": meta_df["unit"].to_numpy()[r],
            "term": terms[c],
            "score": score.data[chosen],
            "len": np.asarray(lengths)[c].astype(int),
        }
    )
    if tf is not None:
        out["score_tf"] = tf.data[chosen]
    return out


def compute_keywords_by_researcher(
    X,
    feature_names: np.ndarray,
    meta_df: pd.DataFrame,
    whitelist_set: set[str],
    top_n: int,
    length_alpha: float,
    *,
    X_tf=None,
) -> pd.DataFrame:
    """Score and rank the top-N keywords for each researcher from the TF-IDF matrix.

    Selection and ordering always use the length-boosted TF-IDF ``score`` (the
    ranking/geometry track). When *X_tf* (same shape as *X*, plain term
    frequencies recovered before alias folding) is given, each row also gets a
    ``score_tf`` column — the quantity track that downstream researcher-equal
    L1 shares are computed from (no IDF, no length bonus).
    """
    rows = []
    n_docs = X.shape[0]
    whitelist_mask = np.isin(feature_names, np.array(list(whitelist_set)))

    for i in range(n_docs):
        row_vec = X[i]
        data = row_vec.toarray().ravel()
        if data.sum() == 0:
            continue

        idx_top = np.argsort(data)[::-1][:top_n]
        idx_wl = np.where((data > 0) & whitelist_mask)[0]
        idx_all = np.unique(np.concatenate([idx_top, idx_wl]))
        if idx_all.size == 0:
            continue

        raw_scores = data[idx_all]
        terms = feature_names[idx_all]

        scores_len, lens = length_bonus(terms, raw_scores, alpha=length_alpha)
        order = np.argsort(scores_len)[::-1]

        idx_ordered = idx_all[order]
        terms = terms[order]
        scores = scores_len[order]
        lens = lens[order]
        tf_row = X_tf[i].toarray().ravel() if X_tf is not None else None

        meta = meta_df.iloc[i]
        for j, term, score, L in zip(idx_ordered, terms, scores, lens, strict=False):
            if score <= 0:
                continue
            row = {
                "last_name": meta["last_name"],
                "first_name": meta["first_name"],
                "unit": meta["unit"],
                "term": term,
                "score": score,
                "len": int(L),
            }
            if tf_row is not None:
                row["score_tf"] = float(tf_row[j])
            rows.append(row)

    return pd.DataFrame(rows)


def compute_keywords_by_unit(
    X,
    feature_names: np.ndarray,
    meta_df: pd.DataFrame,
    whitelist_set: set[str],
    top_n: int,
    length_alpha: float,
) -> pd.DataFrame:
    """Score and rank the top-N keywords for each unit from the TF-IDF matrix."""
    rows = []
    units = sorted(meta_df["unit"].unique())
    whitelist_mask = np.isin(feature_names, np.array(list(whitelist_set)))

    for unit in units:
        idx_docs = np.where(meta_df["unit"].values == unit)[0]
        if len(idx_docs) == 0:
            continue

        X_unit = X[idx_docs]
        mean_scores = np.asarray(X_unit.mean(axis=0)).ravel()
        if mean_scores.sum() == 0:
            continue

        idx_top = np.argsort(mean_scores)[::-1][:top_n]
        idx_wl = np.where((mean_scores > 0) & whitelist_mask)[0]
        idx_all = np.unique(np.concatenate([idx_top, idx_wl]))
        if idx_all.size == 0:
            continue

        raw_scores = mean_scores[idx_all]
        terms = feature_names[idx_all]

        scores_len, lens = length_bonus(terms, raw_scores, alpha=length_alpha)
        order = np.argsort(scores_len)[::-1]

        terms = terms[order]
        scores = scores_len[order]
        lens = lens[order]

        for term, score, L in zip(terms, scores, lens, strict=False):
            if score <= 0:
                continue
            rows.append(
                {
                    "unit": unit,
                    "term": term,
                    "score": score,
                    "len": int(L),
                }
            )

    return pd.DataFrame(rows)


def compute_keywords_domain(
    X,
    feature_names: np.ndarray,
    whitelist_set: set[str],
    top_n: int,
    length_alpha: float,
) -> pd.DataFrame:
    """Score and rank the top-N keywords for the whole domain from the TF-IDF matrix."""
    mean_scores = np.asarray(X.mean(axis=0)).ravel()
    if mean_scores.sum() == 0:
        return pd.DataFrame(columns=["term", "score"])

    whitelist_mask = np.isin(feature_names, np.array(list(whitelist_set)))
    idx_top = np.argsort(mean_scores)[::-1][:top_n]
    idx_wl = np.where((mean_scores > 0) & whitelist_mask)[0]
    idx_all = np.unique(np.concatenate([idx_top, idx_wl]))
    if idx_all.size == 0:
        return pd.DataFrame(columns=["term", "score"])

    raw_scores = mean_scores[idx_all]
    terms = feature_names[idx_all]

    scores_len, lens = length_bonus(terms, raw_scores, alpha=length_alpha)
    order = np.argsort(scores_len)[::-1]

    terms = terms[order]
    scores = scores_len[order]
    lens = lens[order]

    rows = []
    for term, score, L in zip(terms, scores, lens, strict=False):
        if score <= 0:
            continue
        rows.append({"term": term, "score": score, "len": int(L)})

    return pd.DataFrame(rows)
