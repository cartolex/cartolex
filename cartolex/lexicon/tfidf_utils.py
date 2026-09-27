from __future__ import annotations

import numpy as np
import pandas as pd

from .text_utils import length_bonus


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
