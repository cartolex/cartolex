# SPDX-License-Identifier: MIT
"""Project new documents into a fitted map (a projected set).

Keeps the TF-IDF → restricted-vocabulary → SVD projection of a text in one
place (framework-agnostic: raises ``FileNotFoundError`` for missing models),
with the pure helpers that describe a projected vector: its nearest terms and
its concept or subfield weights from the centroids of the fitted terms. No
corpus text or person data is involved in the helpers.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
from scipy import sparse
from sklearn.preprocessing import normalize

if TYPE_CHECKING:
    from cartolex.context import RunContext


def load_positioning_models(
    ctx: RunContext, *, neighbours: int | None = None, link_radius: float | None = None
) -> tuple:
    """Positioning stage: load ``(tfidf, restricted_terms, svd, anchors)`` of a run.

    Reads the restricted vectorizer (``ctx.paths.vectorizer_json``), the
    atlas vocabulary (``ctx.paths.atlas_terms_csv``), the fitted SVD
    (``ctx.paths.svd_model_json``) and the map (``ctx.paths.embeddings_json``):
    ``anchors`` is a :class:`~cartolex.atlas.placement.MapAnchors` of the mapped
    people, whose ``place`` gives the map position of projected vectors, or
    ``None`` before the layout has run. Raises ``FileNotFoundError`` if the
    TF-IDF, restricted-terms, or SVD artifacts are missing, and
    :class:`~cartolex.atlas.model_files.ModelFileError` for a model file that
    cannot be used (for example one of an earlier release). Pass the first three
    to :func:`project_text`. *neighbours* and *link_radius* set how the anchors
    place a point (:mod:`cartolex.atlas.placement`'s defaults when ``None``).
    """
    import pandas as pd

    from cartolex.atlas.model_files import (
        load_embeddings,
        load_svd,
        load_vectorizer,
        reject_legacy,
    )
    from cartolex.atlas.placement import MapAnchors

    paths = ctx.paths
    reject_legacy(paths.vectorizer_json, stage="consolidation")
    reject_legacy(paths.svd_model_json, stage="SVD")
    for p in (paths.vectorizer_json, paths.atlas_terms_csv, paths.svd_model_json):
        if not p.exists():
            raise FileNotFoundError(str(p))

    tfidf = load_vectorizer(paths.vectorizer_json)
    terms_df = pd.read_csv(paths.atlas_terms_csv)
    restricted_terms: list[str] = terms_df["term"].tolist()
    svd = load_svd(paths.svd_model_json)
    placement: dict[str, Any] = {}
    if neighbours is not None:
        placement["k"] = int(neighbours)
    if link_radius is not None:
        placement["link_radius"] = float(link_radius)
    anchors = None
    if paths.embeddings_json.exists():
        emb = load_embeddings(paths.embeddings_json)
        if emb.umap_ind is not None:
            anchors = MapAnchors(emb.Z_ind, emb.umap_ind, **placement)
    return tfidf, restricted_terms, svd, anchors


def project_text(
    text: str,
    *,
    tfidf: Any,
    restricted_terms: list[str],
    svd: Any,
    alias_map: dict[str, str],
    length_bonus_alpha: float,
    top_k: int,
    top_n: int | None = None,
    whitelist_terms: set[str] | None = None,
) -> tuple[np.ndarray, list[dict]]:
    """Transform raw text → SVD embedding + top keywords (see :func:`project_text_vector`)."""
    z_vec, top_keywords, _ = project_text_vector(
        text,
        tfidf=tfidf,
        restricted_terms=restricted_terms,
        svd=svd,
        alias_map=alias_map,
        length_bonus_alpha=length_bonus_alpha,
        top_k=top_k,
        top_n=top_n,
        whitelist_terms=whitelist_terms,
    )
    return z_vec, top_keywords


def project_text_vector(
    text: str,
    *,
    tfidf: Any,
    restricted_terms: list[str],
    svd: Any,
    alias_map: dict[str, str],
    length_bonus_alpha: float,
    top_k: int,
    top_n: int | None = None,
    whitelist_terms: set[str] | None = None,
    feature_names: Sequence[str] | None = None,
) -> tuple[np.ndarray, list[dict], np.ndarray]:
    """Transform raw text → SVD embedding + top keywords + the keyword vector that places it.

    *feature_names* are ``tfidf.get_feature_names_out()``, which a caller placing
    many texts passes once (they are computed on each call otherwise).

    Returns ``(z_vec, top_keywords_list, x_vec)`` where ``z_vec`` is the
    SVD-space coordinate vector, ``top_keywords_list`` are the highest-weighted
    restricted-vocabulary terms found in *text*, and ``x_vec`` is the text's
    vector over *restricted_terms* before normalisation (the kept terms, with
    the length bonus): the usage a projected person's theme weights are read
    from.

    The embedding must match exactly how the fitted space was built
    (``compute_keywords_by_researcher``); otherwise projected points land off
    the fitted manifold.  That means, per document:

    1. fold raw TF-IDF features onto canonical restricted terms (sum scores);
    2. with ``top_n`` set, keep only the ``top_n`` highest-scoring canonical terms (by
       raw score), plus any whitelist terms — the rest are zeroed; ``None`` (the
       default, as the space's people) keeps every one;
    3. apply the length bonus using each **canonical** term's token count;
    4. L2-normalise, then SVD-transform.

    A ``top_n`` other than the space's (or basing the length bonus on the raw
    n-gram instead of the canonical term) yields a differently-weighted vector
    and projects points outside the fitted cloud.
    """
    # Step 1: TF-IDF transform
    X_raw = sparse.csr_matrix(tfidf.transform([text]))  # (1, vocab_size)
    vocab = tfidf.get_feature_names_out() if feature_names is None else feature_names

    # Step 2: Fold raw features onto canonical restricted terms (raw scores,
    # no length bonus yet — top_n selection is on the raw aggregated score),
    # the text's features taken in vocabulary order.
    n_terms = len(restricted_terms)
    raw_agg = np.zeros(n_terms)
    term_to_col = {t: i for i, t in enumerate(restricted_terms)}

    order = np.argsort(X_raw.indices, kind="stable")
    for v_idx, raw_score in zip(X_raw.indices[order], X_raw.data[order], strict=True):
        raw_score = float(raw_score)
        if raw_score == 0.0:
            continue
        vocab_term = str(vocab[v_idx])
        canonical = alias_map.get(vocab_term, vocab_term)
        col_idx = term_to_col.get(canonical)
        if col_idx is None:
            continue  # term not in restricted vocabulary
        raw_agg[col_idx] += raw_score

    # Step 3: Keep top_n canonical terms (by raw score) plus whitelist; zero rest.
    nz = np.where(raw_agg > 0)[0]
    if top_n is not None and top_n > 0 and nz.size > top_n:
        top = nz[np.argpartition(raw_agg[nz], -top_n)[-top_n:]]
    else:
        top = nz
    keep_mask = np.zeros(n_terms, dtype=bool)
    keep_mask[top] = True
    if whitelist_terms:
        for t in whitelist_terms:
            j = term_to_col.get(t)
            if j is not None and raw_agg[j] > 0:
                keep_mask[j] = True

    # Step 4: Apply length bonus on the *canonical* term's token count.
    lengths = np.array([max(1, len(t.split())) for t in restricted_terms], dtype=np.float64)
    multipliers = 1.0 + length_bonus_alpha * (lengths - 1.0)
    x_restricted = np.where(keep_mask, raw_agg * multipliers, 0.0)

    # Step 5: Normalise and apply SVD
    x_norm = normalize(x_restricted.reshape(1, -1), norm="l2")
    z_vec = svd.transform(x_norm)[0]

    # Step 6: Collect top-K keywords
    top_indices = np.argsort(x_restricted)[::-1][:top_k]
    top_keywords = [
        {"term": restricted_terms[i], "score": float(x_restricted[i])}
        for i in top_indices
        if x_restricted[i] > 0
    ]

    return z_vec, top_keywords, x_restricted


# ── Describing a projected vector ─────────────────────────────────────────────


def _cosine_to_rows(z: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity between vector ``z`` and each row of ``matrix``."""
    zn = z / (np.linalg.norm(z) + 1e-12)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12
    return (matrix / norms) @ zn


def scored_top_terms_for_vector(
    z: np.ndarray, term_matrix: np.ndarray, term_names: list[str], k: int = 15
) -> list[dict]:
    """Top-``k`` fitted terms by cosine similarity to the SVD vector ``z``.

    Returns ``[{"term", "score"}]`` (score = cosine similarity, 4 decimals)
    sorted by descending score.
    """
    term_matrix = np.asarray(term_matrix, dtype=float)
    if term_matrix.shape[0] == 0:
        return []
    sims = _cosine_to_rows(np.asarray(z, dtype=float), term_matrix)
    order = np.argsort(sims)[::-1][:k]
    return [{"term": term_names[i], "score": round(float(sims[i]), 4)} for i in order]


def _group_svd_centroids(
    groups: list[dict], term_matrix: np.ndarray, term_names: list[str]
) -> dict[int, np.ndarray]:
    """Map each group id to the mean SVD vector of its ``top_terms``.

    Shared by the subfield and concept centroid builders (both group shapes
    carry ``id`` + ``top_terms``). Groups whose ``top_terms`` match no known
    term are skipped.
    """
    term_matrix = np.asarray(term_matrix, dtype=float)
    idx = {t: i for i, t in enumerate(term_names)}
    out: dict[int, np.ndarray] = {}
    for g in groups:
        gid = g.get("id")
        if gid is None:
            continue
        rows = [term_matrix[idx[t]] for t in g.get("top_terms", []) if t in idx]
        if not rows:
            continue
        out[int(gid)] = np.mean(np.vstack(rows), axis=0)
    return out


def subfield_svd_centroids(
    subfields: list[dict], term_matrix: np.ndarray, term_names: list[str]
) -> dict[int, np.ndarray]:
    """Map each subfield id to the mean SVD vector of its ``top_terms``.

    Subfields whose ``top_terms`` match no known term are skipped.
    """
    return _group_svd_centroids(subfields, term_matrix, term_names)


def concept_svd_centroids(
    concepts: list[dict], term_matrix: np.ndarray, term_names: list[str]
) -> dict[int, np.ndarray]:
    """Map each concept id to the mean SVD vector of its ``top_terms``.

    Concepts whose ``top_terms`` match no known term are skipped. With
    :func:`subfield_weights_for_vector` (id-generic) this gives concept
    membership weights of a projected vector, mirroring the subfield ones.
    """
    return _group_svd_centroids(concepts, term_matrix, term_names)


def subfield_weights_for_vector(z: np.ndarray, centroids: dict[int, np.ndarray]) -> list[dict]:
    """Membership weights of ``z`` over *centroids* (subfields or concepts), by cosine.

    Negative similarities are clipped to 0, the rest L1-normalised to sum 1, and
    returned as ``[{"id", "weight"}]`` sorted by descending weight. Empty when
    there are no centroids or no positive similarity.
    """
    if not centroids:
        return []
    z = np.asarray(z, dtype=float)
    pos = {
        sid: max(0.0, float(_cosine_to_rows(z, c.reshape(1, -1))[0]))
        for sid, c in centroids.items()
    }
    total = sum(pos.values())
    if total <= 0:
        return []
    weights = [{"id": int(sid), "weight": round(v / total, 4)} for sid, v in pos.items() if v > 0]
    weights.sort(key=lambda w: w["weight"], reverse=True)
    return weights
