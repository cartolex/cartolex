# SPDX-License-Identifier: MIT
"""Place points on a finished map by their nearest neighbours in the SVD space.

A map is fitted once on its *anchors* (the mapped people): each has a vector in
the SVD space and a position on the map. A new vector (a keyword, a projected
person, a person's texts of one period) is placed at the weighted mean of the
positions of its ``k`` nearest anchors, by cosine distance in the SVD space.

The weights follow UMAP's fuzzy neighbourhood (``smooth_knn_dist``): the
nearest anchor weighs 1, and the others decay as ``exp(-(d - d₁) / σ)`` with
``σ`` set so the weights sum to ``log₂ k``. This is how UMAP's own ``transform``
starts a new point, before it refines it by stochastic optimisation; placing
without that refinement needs no fitted model, involves no randomness, and
gives the same positions whatever the machine or the number of threads.

The distance computation avoids BLAS (``np.einsum`` without optimisation), so
the sums run in a fixed order; neighbours are ranked by distance, then by
anchor index, so ties break the same way everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["Placement", "neighbour_weights", "place"]

#: Default number of neighbours, UMAP's default ``n_neighbors``.
DEFAULT_K = 15


@dataclass(frozen=True)
class Placement:
    """Positions of placed vectors, with the anchors and weights that produced them."""

    xy: np.ndarray  # (n, 2)
    neighbours: np.ndarray  # (n, k) anchor indices, nearest first
    distances: np.ndarray  # (n, k) cosine distances
    weights: np.ndarray  # (n, k), each row sums to 1


def neighbour_weights(distances: np.ndarray, *, iterations: int = 64) -> np.ndarray:
    """UMAP-style membership weights for rows of sorted distances, normalised to sum to 1.

    For each row, ``ρ`` is the nearest distance and ``σ`` is found by bisection so
    that ``Σ exp(-(dᵢ - ρ) / σ) = log₂ k``. Rows where every distance is equal get
    equal weights.
    """
    d = np.asarray(distances, dtype=np.float64)
    n, k = d.shape
    if k == 1:
        return np.ones((n, 1))
    target = np.log2(k)
    rho = d[:, :1]
    gap = d - rho
    lo = np.zeros(n)
    hi = np.full(n, np.inf)
    sigma = np.ones(n)
    for _ in range(iterations):
        total = np.exp(-gap / sigma[:, None]).sum(axis=1)
        too_big = total > target
        hi = np.where(too_big, sigma, hi)
        lo = np.where(too_big, lo, sigma)
        sigma = np.where(np.isinf(hi), sigma * 2.0, (lo + hi) / 2.0)
    w = np.exp(-gap / sigma[:, None])
    flat = gap.max(axis=1) <= 0
    w[flat] = 1.0
    return w / w.sum(axis=1, keepdims=True)


def _unit_rows(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float64)
    norms = np.sqrt(np.einsum("ij,ij->i", m, m, optimize=False))
    norms[norms == 0] = 1.0
    return m / norms[:, None]


def place(
    vectors: np.ndarray,
    anchor_vectors: np.ndarray,
    anchor_xy: np.ndarray,
    *,
    k: int = DEFAULT_K,
    chunk: int = 2048,
    exclude_self: bool = False,
) -> Placement:
    """Place each row of *vectors* at the weighted mean of its *k* nearest anchors.

    *vectors* and *anchor_vectors* live in the same SVD space; *anchor_xy* are the
    anchors' map positions. *k* is capped at the number of anchors (minus one when
    *exclude_self*, which leaves out anchor ``i`` for vector ``i``, for
    leave-one-out checks). Work proceeds in chunks of *chunk* rows, so memory
    stays proportional to ``chunk × anchors``.
    """
    V = _unit_rows(np.atleast_2d(vectors))
    A = _unit_rows(np.atleast_2d(anchor_vectors))
    XY = np.asarray(anchor_xy, dtype=np.float64)
    n_anchors = A.shape[0]
    if XY.shape != (n_anchors, 2):
        raise ValueError(f"anchor_xy has shape {XY.shape}, expected ({n_anchors}, 2)")
    if V.shape[1] != A.shape[1]:
        raise ValueError(f"vectors have {V.shape[1]} dimensions, anchors {A.shape[1]}")
    k = max(1, min(k, n_anchors - (1 if exclude_self else 0)))
    order_idx = np.arange(n_anchors)
    xy = np.empty((V.shape[0], 2))
    nbrs = np.empty((V.shape[0], k), dtype=np.int64)
    dist = np.empty((V.shape[0], k))
    wts = np.empty((V.shape[0], k))
    for start in range(0, V.shape[0], chunk):
        block = V[start : start + chunk]
        d = 1.0 - np.einsum("ij,kj->ik", block, A, optimize=False)
        np.clip(d, 0.0, 2.0, out=d)
        if exclude_self:
            rows = np.arange(block.shape[0])
            d[rows, start + rows] = np.inf
        # rank by distance, then anchor index: a fixed tie-break
        idx = np.lexsort((np.broadcast_to(order_idx, d.shape), d), axis=1)[:, :k]
        dk = np.take_along_axis(d, idx, axis=1)
        w = neighbour_weights(dk)
        stop = start + block.shape[0]
        nbrs[start:stop], dist[start:stop], wts[start:stop] = idx, dk, w
        xy[start:stop] = np.einsum("ik,ikj->ij", w, XY[idx], optimize=False)
    return Placement(xy=xy, neighbours=nbrs, distances=dist, weights=wts)
