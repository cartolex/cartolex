"""Ward clustering of keywords, exact below a size threshold and in two stages above it.

**Exact Ward** (scipy's ``linkage``) stores every pairwise distance twice: the
condensed matrix and its working copy, about ``8·n²`` bytes (about 1.8 GB at
:data:`EXACT_WARD_LIMIT` points, 80 GB at 10⁵). Below the threshold every
cut is scipy's, exactly as before.

**Above it: micro-clusters, then Ward.** Mini-batch k-means (fixed seed) groups
the points into :func:`micro_cluster_count` micro-clusters; Ward then merges
their centroids, weighted by their sizes (:func:`weighted_ward_linkage`, a
nearest-neighbour chain with the Lance–Williams update with sizes: with unit
sizes it gives scipy's linkage exactly), and each point takes the group of its
micro-cluster. See ``docs/dev/themes-engine.md`` for the measures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize

from .types import Embeddings, LexicalData, col_sums

logger = logging.getLogger(__name__)

#: Above this many points, a Ward cut runs in two stages (micro-clusters, then Ward).
EXACT_WARD_LIMIT = 15_000
#: The seed of the micro-clustering (the result depends on nothing else).
MICRO_SEED = 0


@dataclass(frozen=True)
class WardOptions:
    """How a Ward cut runs: exactly up to *limit* points, else in two stages.

    *micro* is the number of micro-clusters of the two-stage cut (``None``: the
    rule of :func:`micro_cluster_count`, as many as the exact path allows);
    *seed* is the micro-clustering's seed.
    """

    limit: int = EXACT_WARD_LIMIT
    micro: int | None = None
    seed: int = MICRO_SEED


def micro_cluster_count(
    n_points: int, n_clusters: int, *, limit: int = EXACT_WARD_LIMIT, micro: int | None = None
) -> int:
    """How many micro-clusters the two-stage cut makes of *n_points*: ``min(n, limit)``.

    As many as the weighted Ward merges within the memory the exact path is
    allowed (``limit`` points); *micro*, when given, replaces ``limit``.
    """
    return int(min(n_points, limit if micro is None else micro))


def weighted_ward_linkage(points: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Ward's linkage of *points*, each standing for a cluster of *weights* points.

    Ward merges the pair of clusters whose merge raises the within-cluster
    sum of squares least, ``Δ(a, b) = w_a·w_b / (w_a + w_b) · |c_a − c_b|²``: the
    cost depends on the clusters' sizes, so the centroids of clusters of
    unequal sizes cannot be merged as if each were one point. This runs the
    nearest-neighbour chain on the heights ``d = sqrt(2Δ)`` with the
    Lance–Williams update with sizes,

    ``d(k, i∪j)² = ((w_i + w_k)·d(k, i)² + (w_j + w_k)·d(k, j)² − w_k·d(i, j)²) / (w_i + w_j + w_k)``,

    in the order and arithmetic of scipy's ``linkage(method="ward")``, so unit
    weights give scipy's linkage matrix exactly. Returns a scipy linkage
    matrix over the ``len(points)`` clusters (its fourth column counts them,
    not their weights). Memory: one condensed distance matrix, ``4·m²`` bytes.
    """
    from scipy.spatial.distance import pdist

    P = np.asarray(points, dtype=float)
    size = np.asarray(weights, dtype=float).copy()
    m = P.shape[0]
    if size.shape != (m,) or np.any(size <= 0):
        raise ValueError("weighted_ward_linkage needs one positive weight per point")
    if m < 2:
        return np.zeros((0, 4))
    D = pdist(P, "euclidean")
    if not np.all(size == 1.0):
        start = 0
        for i in range(m - 1):
            stop = start + m - i - 1
            wj = size[i + 1 :]
            D[start:stop] *= np.sqrt(2.0 * size[i] * wj / (size[i] + wj))
            start = stop
    everyone = np.arange(m, dtype=np.int64)
    alive = np.ones(m, dtype=bool)
    Z = np.zeros((m - 1, 4))
    chain: list[int] = []
    for k in range(m - 1):
        if not chain:
            chain.append(int(np.flatnonzero(alive)[0]))
        while True:
            x = chain[-1]
            ids = everyone[alive]
            ids = ids[ids != x]
            dist = D[_condensed(m, ids, x)]
            if len(chain) > 1:
                y = chain[-2]
                current = D[_condensed(m, np.array([y]), x)][0]
            else:
                y, current = -1, np.inf
            best = int(np.argmin(dist))
            if dist[best] < current:
                current, y = dist[best], int(ids[best])
            if len(chain) > 1 and y == chain[-2]:
                break
            chain.append(y)
        del chain[-2:]
        if x > y:
            x, y = y, x
        nx, ny = size[x], size[y]
        Z[k] = (x, y, current, nx + ny)
        alive[x] = False
        size[x] = 0.0
        size[y] = nx + ny
        rest = everyone[alive]
        rest = rest[rest != y]
        if rest.size:
            ix = _condensed(m, rest, x)
            iy = _condensed(m, rest, y)
            ni = size[rest]
            t = 1.0 / (nx + ny + ni)
            d_xi, d_yi = D[ix], D[iy]
            D[iy] = np.sqrt(
                (ni + nx) * t * d_xi * d_xi
                + (ni + ny) * t * d_yi * d_yi
                - ni * t * current * current
            )
    Z = Z[np.argsort(Z[:, 2], kind="mergesort")]
    _relabel(Z, m)
    return Z


def _condensed(m: int, i: np.ndarray, j: int) -> np.ndarray:
    """Condensed indices of the pairs (i, j), i ≠ j, of an m × m distance matrix."""
    a = np.minimum(i, j).astype(np.int64)
    b = np.maximum(i, j).astype(np.int64)
    return m * a - a * (a + 1) // 2 + b - a - 1


def _relabel(Z: np.ndarray, n: int) -> None:
    """Scipy's numbering of the clusters of a linkage sorted by height, in place (union-find)."""
    parent = np.arange(2 * n - 1)
    count = np.zeros(2 * n - 1, dtype=np.int64)
    count[:n] = 1

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = int(parent[root])
        while parent[x] != root:
            parent[x], x = root, int(parent[x])
        return root

    for i in range(n - 1):
        a, b = find(int(Z[i, 0])), find(int(Z[i, 1]))
        Z[i, 0], Z[i, 1] = (a, b) if a < b else (b, a)
        parent[a] = parent[b] = n + i
        count[n + i] = count[a] + count[b]
        Z[i, 3] = count[n + i]


#: How the micro-clustering chooses its first centres (``"k-means++"`` or ``"random"``).
MICRO_INIT = "k-means++"


def micro_clusters(
    points: np.ndarray, m: int, *, seed: int = MICRO_SEED, init: str = MICRO_INIT
) -> np.ndarray:
    """Mini-batch k-means labels of *points* into at most *m* micro-clusters (0-based, dense).

    First centres by *init* (drawn from at most ``3·m`` points) with *seed*,
    batches of ``max(1024, 4·m)`` points, no reassignment of small clusters:
    the result depends on the points and the seed only. Empty clusters are
    dropped and the labels renumbered in order.
    """
    from sklearn.cluster import MiniBatchKMeans

    P = np.asarray(points, dtype=float)
    km = MiniBatchKMeans(
        n_clusters=int(m),
        init=init,
        init_size=min(len(P), 3 * int(m)),
        n_init=1,
        batch_size=max(1024, 4 * int(m)),
        max_iter=50,
        random_state=seed,
        reassignment_ratio=0.0,
    )
    labels = km.fit_predict(P)
    _, dense = np.unique(labels, return_inverse=True)
    return dense.astype(int)


def two_stage_ward_labels(
    points: np.ndarray,
    n_clusters: int,
    *,
    limit: int = EXACT_WARD_LIMIT,
    seed: int = MICRO_SEED,
    init: str = MICRO_INIT,
    micro: int | None = None,
) -> np.ndarray:
    """Ward cut of *points* into *n_clusters* groups through micro-clusters (0-based labels).

    Mini-batch k-means makes :func:`micro_cluster_count` micro-clusters; when
    the groups asked for are more than half of them, the k-means partition at
    *n_clusters* is the answer. Otherwise :func:`weighted_ward_linkage` merges
    the micro-clusters' centroids, weighted by their sizes, the tree is cut at
    *n_clusters* like the exact path, and each point takes its micro-cluster's
    group. Deterministic for a given *seed*.
    """
    from scipy.cluster.hierarchy import fcluster

    P = np.asarray(points, dtype=float)
    n = P.shape[0]
    k = max(1, min(int(n_clusters), n))
    if k == 1:
        return np.zeros(n, dtype=int)
    m = micro_cluster_count(n, k, limit=limit, micro=micro)
    if 2 * k > m:
        return micro_clusters(P, k, seed=seed, init=init)
    micro = micro_clusters(P, m, seed=seed, init=init)
    n_micro = int(micro.max()) + 1
    if n_micro <= k:
        return micro
    sizes = np.bincount(micro, minlength=n_micro).astype(float)
    centroids = np.zeros((n_micro, P.shape[1]))
    np.add.at(centroids, micro, P)
    centroids /= sizes[:, None]
    link = weighted_ward_linkage(centroids, sizes)
    groups = fcluster(link, t=k, criterion="maxclust").astype(int) - 1
    return groups[micro]


def ward_labels(
    points: np.ndarray,
    n_clusters: int,
    *,
    limit: int | None = None,
    options: WardOptions | None = None,
) -> np.ndarray:
    """Ward cut of *points* into *n_clusters* groups (0-based labels), exact up to *limit* points.

    Up to *limit* points (*options*' limit, :data:`EXACT_WARD_LIMIT` by default):
    scipy's ``linkage(method="ward")`` cut with ``fcluster(maxclust)``, as
    always. Above: :func:`two_stage_ward_labels`, with *options*' micro-clusters
    and seed.
    """
    from scipy.cluster.hierarchy import fcluster, linkage

    opts = options if options is not None else WardOptions()
    limit = opts.limit if limit is None else limit
    n = points.shape[0]
    if n > limit:
        logger.info("Ward on %d points in two stages (micro-clusters above %d).", n, limit)
        return two_stage_ward_labels(
            points, n_clusters, limit=limit, seed=opts.seed, micro=opts.micro
        )
    link = linkage(points, method="ward")
    # fcluster labels are 1..k; shift to 0-based to match the rest of the pipeline.
    return fcluster(link, t=n_clusters, criterion="maxclust").astype(int) - 1


def prepare_cluster_embeddings(Z_terms: np.ndarray, n_components_cluster: int) -> np.ndarray:
    """L2-normalise the leading SVD components used for clustering.

    Truncates to the leading ``n_components_cluster`` columns (denoise + keep the dominant
    thematic structure) and L2-normalises each row so Euclidean and cosine agree on the
    unit sphere (Ward's Euclidean criterion then follows semantic similarity).
    """
    k_dims = max(2, min(n_components_cluster, Z_terms.shape[1]))
    return normalize(Z_terms[:, :k_dims], norm="l2", axis=1)


def fit_agglomerative_labels(
    Zn: np.ndarray, *, n_clusters: int, ward: WardOptions | None = None
) -> np.ndarray:
    """Ward agglomerative clustering on L2-normalised SVD vectors → labels.

    Bottom-up: merges the most correlated terms first, building a tree whose cut at
    ``n_clusters`` gives the "concept" partition. Assigns **every** term (no ``-1`` noise)
    and ``n_clusters`` is a direct knob. Returns a 0-based integer label array. Above
    *ward*'s limit (:data:`EXACT_WARD_LIMIT`) the cut runs in two stages (:func:`ward_labels`).
    """
    n = Zn.shape[0]
    k = max(1, min(int(n_clusters), n))
    if k == 1:
        return np.zeros(n, dtype=int)
    return ward_labels(Zn, k, options=ward)


def cluster_terms(
    data: LexicalData,
    emb: Embeddings,
    *,
    n_clusters: int,
    top_n_terms_per_cluster: int,
    clusters_terms_csv,
    n_components_cluster: int = 50,
    ward: WardOptions | None = None,
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
    labels = fit_agglomerative_labels(Zn, n_clusters=n_clusters, ward=ward)

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
