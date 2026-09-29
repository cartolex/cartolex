# SPDX-License-Identifier: MIT
"""Structure suggestions for a theme tree: sibling nodes to merge, nodes to split.

Measures over one set of sibling nodes, each given by the rows of its
keywords (on it or under it):

- **merge** (:func:`merge_measures`), for each pair of siblings: how close
  their centroids are in the keywords' space (*closeness*), how much the texts
  that use one also use the other (*overlap*), how many of their keywords sit
  nearer the other node than their own (*mixing*), and how small the smaller
  one is (*small*);
- **split** (:func:`split_measures`), for each node: the gain of cutting its
  keywords in two by Ward's method, against the gain the same cut gives on
  random keywords of the same spread (*gain_z*), and how far apart the texts of
  the two halves are (*texts_apart*), and how weakly the co-use graph of its
  keywords holds across its best cut in two (*cut*, one minus the conductance).

An experiment (``tools/theme_comb_study.py structure``): the measures, and why
none of them is shipped, are in ``docs/dev/themes-engine.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy import sparse
from sklearn.preprocessing import normalize

__all__ = ["merge_measures", "split_measures"]


def _centroid(Zn: np.ndarray, rows: np.ndarray) -> np.ndarray:
    return normalize(Zn[rows].mean(axis=0, keepdims=True))[0]


def _text_shares(D: sparse.spmatrix, groups: Sequence[np.ndarray], n_terms: int) -> np.ndarray:
    """texts × nodes: the share of each text's keywords (among these nodes') on each node."""
    rows = np.concatenate([np.asarray(g) for g in groups]) if groups else np.zeros(0, int)
    cols = np.concatenate([np.full(len(g), j) for j, g in enumerate(groups)]) if groups else rows
    G = sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n_terms, len(groups)))
    C = np.asarray((sparse.csr_matrix(D) @ G).todense())
    tot = C.sum(axis=1, keepdims=True)
    return np.divide(C, tot, out=np.zeros_like(C), where=tot > 0)


def merge_measures(
    Z: np.ndarray, D: sparse.spmatrix, groups: Sequence[np.ndarray], *, margin: float = 0.05
) -> list[dict]:
    """The merge measures of every pair of sibling *groups* (keyword rows of *Z* and *D*).

    ``score`` is the mean of *closeness* (clipped at 0), *overlap* and *mixing*.
    """
    Zn = normalize(np.asarray(Z, dtype=float))
    groups = [np.asarray(g, dtype=np.int64) for g in groups]
    cent = np.array([_centroid(Zn, g) for g in groups])
    sums = np.array([Zn[g].sum(axis=0) for g in groups])
    A = _text_shares(D, groups, Zn.shape[0])
    mass = A.sum(axis=0)
    sizes = np.array([len(g) for g in groups], dtype=float)
    median = float(np.median(sizes)) if len(sizes) else 1.0
    out = []
    for a in range(len(groups)):
        for b in range(a + 1, len(groups)):
            close = float(cent[a] @ cent[b])
            both = np.minimum(A[:, a], A[:, b]).sum()
            overlap = float(both / max(min(mass[a], mass[b]), 1e-12))
            mixed = 0
            for own, other in ((a, b), (b, a)):
                g = groups[own]
                V = Zn[g]
                rest = normalize(sums[own][None, :] - V) if len(g) > 1 else V
                own_cos = np.einsum("ij,ij->i", V, rest)
                mixed += int(((own_cos - V @ cent[other]) < margin).sum())
            mixing = mixed / (len(groups[a]) + len(groups[b]))
            small = float(np.clip(1.0 - min(sizes[a], sizes[b]) / max(median, 1.0), 0.0, 1.0))
            out.append(
                {
                    "a": a,
                    "b": b,
                    "closeness": close,
                    "overlap": overlap,
                    "mixing": mixing,
                    "small": small,
                    "score": (max(close, 0.0) + overlap + mixing) / 3,
                }
            )
    return out


def _ward_two(V: np.ndarray) -> tuple[np.ndarray, float]:
    """Ward's cut of *V* in two: the labels, and the share of the spread it removes."""
    from scipy.cluster.hierarchy import fcluster, linkage

    total = float(((V - V.mean(axis=0)) ** 2).sum())
    if len(V) < 4 or total <= 0:
        return np.zeros(len(V), dtype=int), 0.0
    labels = fcluster(linkage(V, method="ward"), 2, criterion="maxclust") - 1
    within = sum(float(((V[labels == c] - V[labels == c].mean(axis=0)) ** 2).sum()) for c in (0, 1))
    return labels, 1.0 - within / total


def _conductance(Dg: sparse.spmatrix) -> float:
    """The conductance of the best cut in two of the keywords' co-use graph (spectral).

    The graph links two keywords by the texts using both; the cut follows the
    sign of the second eigenvector of the normalised graph; its conductance is
    the weight across over the smaller side's weight. Low: two groups of
    keywords used by different texts.
    """
    Dg = sparse.csr_matrix(Dg)
    A = np.asarray((Dg.T @ Dg).todense(), dtype=float)
    np.fill_diagonal(A, 0.0)
    deg = A.sum(axis=1)
    keep = deg > 0
    if keep.sum() < 4:
        return 1.0
    A, deg = A[np.ix_(keep, keep)], deg[keep]
    d = 1.0 / np.sqrt(deg)
    L = np.eye(len(deg)) - d[:, None] * A * d[None, :]
    _, vecs = np.linalg.eigh(L)
    f = vecs[:, 1] * d
    side = f > np.median(f)
    cut = A[np.ix_(side, ~side)].sum()
    vol = min(deg[side].sum(), deg[~side].sum())
    return float(cut / vol) if vol > 0 else 1.0


def split_measures(
    Z: np.ndarray,
    D: sparse.spmatrix,
    groups: Sequence[np.ndarray],
    *,
    seeds: int = 20,
    min_keywords: int = 6,
) -> list[dict]:
    """The split measures of every node of *groups*.

    *gain_z*: how many standard deviations the gain of Ward's cut in two lies
    above the gains of the same cut on *seeds* random sets of as many
    keywords with the node's mean and covariance (Gaussian). *texts_apart*:
    one minus the share of the texts using either half that use both.
    ``score`` is *gain_z*. Nodes under *min_keywords* get zeros.
    """
    Zn = normalize(np.asarray(Z, dtype=float))
    Dc = sparse.csc_matrix(D)
    out = []
    for g in groups:
        g = np.asarray(g, dtype=np.int64)
        if len(g) < min_keywords:
            out.append(
                {
                    "gain": 0.0,
                    "gain_z": 0.0,
                    "texts_apart": 0.0,
                    "cut": 0.0,
                    "score": 0.0,
                    "halves": None,
                }
            )
            continue
        V = Zn[g]
        labels, gain = _ward_two(V)
        rng = np.random.default_rng(len(g))
        Vc = V - V.mean(axis=0)
        null = []
        for _ in range(seeds):
            R = rng.standard_normal((len(g), len(g))) @ Vc / np.sqrt(max(len(g) - 1, 1))
            null.append(_ward_two(R + V.mean(axis=0))[1])
        sd = float(np.std(null)) or 1e-9
        z = (gain - float(np.mean(null))) / sd
        conductance = _conductance(Dc[:, g])
        used = [np.asarray(Dc[:, g[labels == c]].sum(axis=1) > 0).ravel() for c in (0, 1)]
        either = int((used[0] | used[1]).sum())
        apart = 1.0 - int((used[0] & used[1]).sum()) / max(either, 1)
        out.append(
            {
                "gain": gain,
                "gain_z": z,
                "texts_apart": apart,
                "cut": 1.0 - conductance,
                "score": z,
                "halves": labels,
            }
        )
    return out
