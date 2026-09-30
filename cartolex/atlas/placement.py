# SPDX-License-Identifier: MIT
"""Place points on a finished map by their nearest neighbours in the SVD space.

A map is fitted once on its *anchors* (the mapped people): each has a vector in
the SVD space and a position on the map. Everything else — a keyword, a
projected person, a person's texts of one period — is placed from its ``k``
nearest anchors (cosine distance in the SVD space), in two steps:

1. **weights.** The anchors weigh as in UMAP's fuzzy neighbourhood
   (``smooth_knn_dist``): the nearest weighs 1, the others
   ``exp(-(d - d₁) / σ)``, with ``σ`` set so the weights sum to ``log₂ k``;
2. **the heaviest group.** Two neighbours are linked when their positions on
   the map are within the *link radius* of each other; the linked neighbours
   form groups (the connected parts of those links), and the point goes to the
   weighted mean of the group with the largest total weight (on a tie, the group
   of the nearest neighbour). The link radius is a share of the map's radius,
   the root mean square distance of the anchors from their centre.

A point whose neighbours all sit together lands at their weighted mean. A point
whose neighbours are split between two distant places lands in the place that
weighs most, never in the empty space between them. No fitted model is needed,
nothing is random, and the arithmetic runs in a fixed order (``np.einsum``
without optimisation, neighbours ranked by distance then by anchor index), so
the positions are the same on any machine, whatever the chunk size.

**Size.** A chunk of points holds at most :data:`CHUNK_CELLS` point × anchor
distances. The candidates are found with the numeric library's matrix product
(fast, but its last bits depend on the machine), then the exact distances are
computed for them alone and ranked: every anchor within a margin of the k-th
candidate distance is a candidate, so the neighbours are those of the exact
distances, ties included.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "K",
    "LINK_RADIUS",
    "MapAnchors",
    "Placement",
    "map_radius",
    "neighbour_weights",
    "place",
]

#: The number of neighbours a point is placed from.
K = 8
#: The link radius, as a share of the map's radius.
LINK_RADIUS = 0.25
#: The point × anchor distances one chunk holds at most (2²²: 32 MB of float64).
CHUNK_CELLS = 1 << 22
#: How far a fast distance may be from the exact one (far above the rounding of a product
#: of unit vectors): every anchor this close to the k-th fast distance is ranked exactly.
_MARGIN = 1e-9


@dataclass(frozen=True)
class Placement:
    """Positions of placed vectors, with the neighbours and weights that produced them.

    ``neighbour_weights`` are the UMAP-style weights of the ``k`` neighbours
    (each row sums to 1); ``in_group`` marks the neighbours of the heaviest
    group, and ``weights`` are the weights the position was taken with (those of
    the group, renormalised to sum to 1; zero elsewhere).
    """

    xy: np.ndarray  # (n, 2)
    neighbours: np.ndarray  # (n, k) anchor indices, nearest first
    distances: np.ndarray  # (n, k) cosine distances
    neighbour_weights: np.ndarray  # (n, k), each row sums to 1
    in_group: np.ndarray  # (n, k) bool
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


def map_radius(xy: np.ndarray) -> float:
    """The root mean square distance of *xy* from its centre (the map's scale)."""
    xy = np.asarray(xy, dtype=np.float64)
    if not len(xy):
        return 0.0
    centred = xy - xy.mean(axis=0)
    return float(np.sqrt(np.einsum("ij,ij->", centred, centred, optimize=False) / len(xy)))


def _unit_rows(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float64)
    norms = np.sqrt(np.einsum("ij,ij->i", m, m, optimize=False))
    norms[norms == 0] = 1.0
    return m / norms[:, None]


def _heaviest_group(positions: np.ndarray, weights: np.ndarray, radius: float) -> np.ndarray:
    """For each row, the neighbours of its heaviest linked group (a boolean mask).

    *positions* are the ``(n, k, 2)`` map positions of the neighbours, *weights*
    ``(n, k)``. Groups are the connected parts of the links (positions within
    *radius* of each other); each group is labelled by its lowest neighbour
    index, so on equal weights the group of the nearest neighbour wins.
    """
    n, k, _ = positions.shape
    diff = positions[:, :, None, :] - positions[:, None, :, :]
    linked = np.einsum("nijc,nijc->nij", diff, diff, optimize=False) <= radius * radius
    labels = np.broadcast_to(np.arange(k), (n, k)).copy()
    for _ in range(k):  # the lowest label spreads through the links
        spread = np.where(linked, labels[:, None, :], k).min(axis=2)
        if np.array_equal(spread, labels):
            break
        labels = spread
    members = (labels[:, :, None] == np.arange(k)[None, None, :]).astype(np.float64)
    group_weight = np.einsum("nm,nmg->ng", weights, members, optimize=False)
    best = np.argmax(group_weight, axis=1)  # the first maximum: the lowest label
    return labels == best[:, None]


def _nearest(
    block: np.ndarray, anchors: np.ndarray, k: int, self_offset: int | None
) -> tuple[np.ndarray, np.ndarray]:
    """The *k* nearest anchors of each row of *block* and their exact cosine distances.

    Ranked by exact distance (``np.einsum`` without optimisation, clipped to
    [0, 2]), then by anchor index. *self_offset*, when given, is the index of the
    block's first row among the anchors: row ``i`` then leaves out anchor
    ``self_offset + i``.
    """
    n_rows = block.shape[0]
    rows = np.arange(n_rows)
    fast = 1.0 - block @ anchors.T
    np.clip(fast, 0.0, 2.0, out=fast)
    if self_offset is not None:
        fast[rows, self_offset + rows] = np.inf
    kth = np.partition(fast, k - 1, axis=1)[:, k - 1]
    r, c = np.nonzero(fast <= (kth + _MARGIN)[:, None])
    exact = 1.0 - np.einsum("ij,ij->i", block[r], anchors[c], optimize=False)
    np.clip(exact, 0.0, 2.0, out=exact)
    if self_offset is not None:
        exact[c == self_offset + r] = np.inf
    order = np.lexsort((c, exact, r))  # by row, then distance, then anchor index
    first = np.searchsorted(r[order], rows)
    take = order[first[:, None] + np.arange(k)[None, :]]
    return c[take], exact[take]


def place(
    vectors: np.ndarray,
    anchor_vectors: np.ndarray,
    anchor_xy: np.ndarray,
    *,
    k: int = K,
    link_radius: float = LINK_RADIUS,
    chunk: int = 2048,
    exclude_self: bool = False,
) -> Placement:
    """Place each row of *vectors* on the map of the anchors (see the module's notes).

    *vectors* and *anchor_vectors* live in the same SVD space; *anchor_xy* are the
    anchors' map positions. *k* is capped at the number of anchors (minus one when
    *exclude_self*, which leaves out anchor ``i`` for vector ``i``, for
    leave-one-out checks). *link_radius* is a share of the map's radius. Work
    proceeds in chunks of at most *chunk* rows and :data:`CHUNK_CELLS` distances,
    so memory stays bounded whatever the number of anchors; the positions do not
    depend on the chunking.
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
    radius = link_radius * map_radius(XY)
    chunk = max(1, min(int(chunk), CHUNK_CELLS // max(1, n_anchors)))
    n = V.shape[0]
    xy = np.empty((n, 2))
    nbrs = np.empty((n, k), dtype=np.int64)
    dist = np.empty((n, k))
    plain = np.empty((n, k))
    group = np.empty((n, k), dtype=bool)
    wts = np.empty((n, k))
    for start in range(0, n, chunk):
        block = V[start : start + chunk]
        idx, dk = _nearest(block, A, k, start if exclude_self else None)
        w = neighbour_weights(dk)
        positions = XY[idx]
        mask = _heaviest_group(positions, w, radius)
        kept = np.where(mask, w, 0.0)
        kept = kept / kept.sum(axis=1, keepdims=True)
        stop = start + block.shape[0]
        nbrs[start:stop], dist[start:stop], plain[start:stop] = idx, dk, w
        group[start:stop], wts[start:stop] = mask, kept
        xy[start:stop] = np.einsum("ik,ikj->ij", kept, positions, optimize=False)
    return Placement(
        xy=xy,
        neighbours=nbrs,
        distances=dist,
        neighbour_weights=plain,
        in_group=group,
        weights=wts,
    )


@dataclass(frozen=True)
class MapAnchors:
    """A finished map's anchors: their vectors in the SVD space and their positions, and the
    neighbours (*k*) and link radius a point is placed with."""

    vectors: np.ndarray  # (n, dims)
    xy: np.ndarray  # (n, 2)
    k: int = K
    link_radius: float = LINK_RADIUS

    def place(self, vectors: np.ndarray) -> np.ndarray:
        """The map positions of *vectors* (``(n, 2)``; no rows give ``(0, 2)``)."""
        vectors = np.asarray(vectors, dtype=np.float64)
        if vectors.size == 0:
            return np.zeros((0, 2))
        return place(vectors, self.vectors, self.xy, k=self.k, link_radius=self.link_radius).xy
