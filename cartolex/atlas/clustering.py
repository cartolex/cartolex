from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize

from .types import Embeddings, LexicalData, col_sums

logger = logging.getLogger(__name__)


def prepare_cluster_embeddings(Z_terms: np.ndarray, n_components_cluster: int) -> np.ndarray:
    """L2-normalise the leading SVD components used for clustering.

    Truncates to the leading ``n_components_cluster`` columns (denoise + keep the dominant
    thematic structure) and L2-normalises each row so Euclidean and cosine agree on the
    unit sphere (Ward's Euclidean criterion then follows semantic similarity).
    """
    k_dims = max(2, min(n_components_cluster, Z_terms.shape[1]))
    return normalize(Z_terms[:, :k_dims], norm="l2", axis=1)


def fit_agglomerative_labels(Zn: np.ndarray, *, n_clusters: int) -> np.ndarray:
    """Ward agglomerative clustering on L2-normalised SVD vectors → labels.

    Bottom-up: merges the most correlated terms first, building a tree whose cut at
    ``n_clusters`` gives the "concept" partition. Assigns **every** term (no ``-1`` noise)
    and ``n_clusters`` is a direct knob. Returns a 0-based integer label array.
    """
    from scipy.cluster.hierarchy import fcluster, linkage

    n = Zn.shape[0]
    k = max(1, min(int(n_clusters), n))
    if k == 1:
        return np.zeros(n, dtype=int)
    link = linkage(Zn, method="ward")
    # fcluster labels are 1..k; shift to 0-based to match the rest of the pipeline.
    return fcluster(link, t=k, criterion="maxclust").astype(int) - 1


def cluster_terms(
    data: LexicalData,
    emb: Embeddings,
    *,
    n_clusters: int,
    top_n_terms_per_cluster: int,
    clusters_terms_csv,
    n_components_cluster: int = 50,
) -> pd.DataFrame:
    """Cluster terms into ``n_clusters`` concepts via **Ward agglomerative** clustering.

    Membership is decided in a moderate-dimensional SVD subspace with cosine geometry — the
    leading ``n_components_cluster`` components carry the dominant thematic structure, *not*
    the 2D UMAP projection (which equalises density and distorts global distance). Ward
    assigns every term (full coverage, no noise) and ``n_clusters`` is a direct knob. These
    clusters ARE the hierarchy's concepts. ``emb.umap_terms`` is only borrowed for the
    on-map colour overlay (a cluster may look spatially scattered in 2D — that is honest).
    """
    Z = emb.Z_terms
    if Z is None:
        raise ValueError("SVD term embeddings not computed.")

    n_terms = Z.shape[0]
    Zn = prepare_cluster_embeddings(Z, n_components_cluster)
    logger.info(
        "Clustering terms with Ward agglomerative into %d concepts (top %d of %d SVD comps).",
        n_clusters,
        Zn.shape[1],
        Z.shape[1],
    )
    labels = fit_agglomerative_labels(Zn, n_clusters=n_clusters)

    term_scores = col_sums(data.X)

    # Borrow 2D UMAP coords for the visual overlay; NaN if the projection has not run yet
    # (clustering itself does not need it).
    if emb.umap_terms is not None:
        umap_x = emb.umap_terms[:, 0]
        umap_y = emb.umap_terms[:, 1]
    else:
        umap_x = np.full(n_terms, np.nan)
        umap_y = np.full(n_terms, np.nan)

    df_terms = pd.DataFrame(
        {
            "term": data.terms,
            "umap_x": umap_x,
            "umap_y": umap_y,
            "cluster": labels,
            "global_score": term_scores,
        }
    )

    rows = []
    for cid, grp in df_terms.groupby("cluster"):
        if cid == -1:
            continue
        top_terms = grp.sort_values("global_score", ascending=False)["term"].tolist()
        rows.append(
            {
                "cluster": cid,
                "size": len(grp),
                "terms": ", ".join(top_terms[:top_n_terms_per_cluster]),
            }
        )
    df_clusters = (
        pd.DataFrame(rows).sort_values("cluster")
        if rows
        else pd.DataFrame(columns=["cluster", "size", "terms"])
    )
    df_clusters.to_csv(clusters_terms_csv, index=False)
    logger.info("Wrote %d term clusters to %s", len(rows), clusters_terms_csv)

    return df_terms
