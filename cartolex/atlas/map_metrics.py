# SPDX-License-Identifier: MIT
"""Validation metrics for a merged multi-cohort space.

Every metric answers one pre-registered question about a multi-cohort merge
(see INTEGRATION.md), computed in the joint high-dimensional space Z (2-D
layouts are display only):

- *do no harm*: :func:`restricted_knn_overlap` (joint vs each cohort's own
  space), plus :func:`integrity_ratio` for curated concepts;
- *overlap*: :func:`mixing_matrix` (+ permutation z-scores) and the model-free
  :func:`shared_vocab_mass` reference it must rank-correlate with;
- *continuity/discontinuity*: :func:`conductance_matrix`, :func:`gap_statistic`,
  :func:`weak_link_ratio` on the mutual-kNN graph, and the named
  :func:`bridge_pairs`;
- *interdisciplinarity*: :func:`neighbor_cohort_entropy` (shuffle-null
  calibrated) and :func:`cross_cohort_sense_mass`;
- *external validity*: :func:`same_unit_auc` — same-laboratory researchers
  evaluated in different cohorts are ground-truth interdisciplinarity, and the
  ``unit`` facet survives anonymization.

All functions are pure and deterministic (fixed seeds on any subsampling or
permutation null). Cosine geometry throughout, matching the pipelines.

Size: no function holds a researcher × researcher matrix. Similarities are
computed a block of rows at a time (:mod:`cartolex.atlas.blocks`): on a set of
at most :data:`~cartolex.atlas.blocks.BLOCK_CELLS` pairs (every demo world) the
arithmetic is that of the whole matrix; the pooled matrices may be sparse.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import dijkstra
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import normalize

from .blocks import dense_rows, one_block, row_blocks

logger = logging.getLogger(__name__)

__all__ = [
    "restricted_knn_overlap",
    "mixing_matrix",
    "mixing_permutation_zscores",
    "conductance_matrix",
    "gap_statistic",
    "weak_link_ratio",
    "neighbor_cohort_entropy",
    "cross_cohort_sense_mass",
    "same_unit_auc",
    "shared_vocab_mass",
    "mean_pairwise_cosine",
    "integrity_ratio",
    "bridge_pairs",
]


def _knn_indices(Z: np.ndarray, k: int) -> np.ndarray:
    """Cosine kNN indices (self excluded), k clamped to n−1."""
    Z = np.asarray(Z, dtype=float)
    eff_k = max(1, min(k, Z.shape[0] - 1))
    nn = NearestNeighbors(n_neighbors=eff_k + 1, metric="cosine").fit(Z)
    idx = nn.kneighbors(Z, return_distance=False)
    # Drop self robustly (self is normally column 0 but ties can reorder).
    out = np.empty((Z.shape[0], eff_k), dtype=int)
    for i in range(Z.shape[0]):
        row = [j for j in idx[i] if j != i][:eff_k]
        out[i] = row
    return out


def restricted_knn_overlap(Z_joint_rows: np.ndarray, Z_cohort: np.ndarray, *, k: int = 10) -> float:
    """Within-cohort fidelity of the joint space (the *do no harm* gate).

    Both arguments hold the SAME researchers row-aligned: their joint-space
    vectors and their cohort-space vectors. For each researcher, the fraction
    of its ``k`` nearest same-cohort neighbours in the joint space that are
    also among its ``k`` nearest in the cohort's own space, averaged.
    Restricting the joint kNN to same-cohort rows means cross-cohort mixing
    is not penalized — only *rearranging a cohort internally* is.
    """
    A = np.asarray(Z_joint_rows, dtype=float)
    B = np.asarray(Z_cohort, dtype=float)
    if A.shape[0] != B.shape[0]:
        raise ValueError(f"Row mismatch: {A.shape[0]} joint vs {B.shape[0]} cohort rows")
    if A.shape[0] < 3:
        return 0.0
    nn_joint = _knn_indices(A, k)
    nn_cohort = _knn_indices(B, k)
    eff_k = nn_joint.shape[1]
    overlap = [len(set(nn_joint[i]) & set(nn_cohort[i])) / eff_k for i in range(A.shape[0])]
    return float(np.mean(overlap))


def _neighbor_cohort_counts(
    neigh: np.ndarray, cohorts: np.ndarray, cohort_order: list[str]
) -> np.ndarray:
    """(n, S) counts of each row's neighbours per cohort."""
    cohort_index = {s: j for j, s in enumerate(cohort_order)}
    lab = np.vectorize(cohort_index.__getitem__)(cohorts[neigh])
    counts = np.zeros((neigh.shape[0], len(cohort_order)))
    for j in range(len(cohort_order)):
        counts[:, j] = (lab == j).sum(axis=1)
    return counts


def mixing_matrix(
    Z: np.ndarray, cohorts: list[str] | np.ndarray, *, k: int = 15
) -> tuple[list[str], np.ndarray]:
    """Row-stochastic cohort mixing matrix M[s, s′] over joint kNN.

    ``M[s, s′]`` = mean over researchers of cohort *s* of the fraction of their
    ``k`` nearest joint neighbours belonging to *s′*; the diagonal is insularity.
    """
    cohort_of = np.asarray(cohorts, dtype=object)
    order = sorted(set(cohort_of.tolist()))
    neigh = _knn_indices(Z, k)
    counts = _neighbor_cohort_counts(neigh, cohort_of, order)
    fracs = counts / neigh.shape[1]
    M = np.zeros((len(order), len(order)))
    for i, s in enumerate(order):
        rows = cohort_of == s
        if rows.any():
            M[i] = fracs[rows].mean(axis=0)
    return order, M


def mixing_permutation_zscores(
    Z: np.ndarray,
    cohorts: list[str] | np.ndarray,
    *,
    k: int = 15,
    n_permutations: int = 200,
    random_state: int = 0,
) -> tuple[list[str], np.ndarray]:
    """Z-scores of the mixing matrix against a label-permutation null.

    The kNN graph is fixed; only the cohort labels are permuted, so the null
    asks "would this mixing arise if cohorts were arbitrary labelings of the
    same cloud?". Positive z = more mixing than random placement.
    """
    cohort_of = np.asarray(cohorts, dtype=object)
    order = sorted(set(cohort_of.tolist()))
    neigh = _knn_indices(Z, k)

    def _matrix(labels: np.ndarray) -> np.ndarray:
        counts = _neighbor_cohort_counts(neigh, labels, order)
        fracs = counts / neigh.shape[1]
        M = np.zeros((len(order), len(order)))
        for i, s in enumerate(order):
            rows = labels == s
            if rows.any():
                M[i] = fracs[rows].mean(axis=0)
        return M

    observed = _matrix(cohort_of)
    rng = np.random.default_rng(random_state)
    null = np.empty((n_permutations, len(order), len(order)))
    for p in range(n_permutations):
        null[p] = _matrix(rng.permutation(cohort_of))
    mu = null.mean(axis=0)
    sd = null.std(axis=0)
    z = np.divide(observed - mu, sd, out=np.zeros_like(observed), where=sd > 0)
    return order, z


def _mutual_knn_adjacency(Z: np.ndarray, k: int) -> sparse.csr_matrix:
    """Binary mutual-kNN adjacency (edge iff each is in the other's kNN)."""
    neigh = _knn_indices(Z, k)
    n = Z.shape[0]
    rows = np.repeat(np.arange(n), neigh.shape[1])
    cols = neigh.ravel()
    A = sparse.csr_matrix((np.ones(rows.size), (rows, cols)), shape=(n, n))
    return A.multiply(A.T).tocsr()  # mutual only


def _cosine_medoid(points: np.ndarray, mask: np.ndarray) -> int:
    """Index (into *points*) of the mask's cosine medoid (max total similarity)."""
    block = points[mask]
    totals = np.concatenate(
        [(block[rows] @ block.T).sum(axis=1) for rows in row_blocks(len(block), len(block))]
    )
    return int(np.nonzero(mask)[0][np.argmax(totals)])


def conductance_matrix(
    Z: np.ndarray, cohorts: list[str] | np.ndarray, *, k: int = 15
) -> tuple[list[str], np.ndarray]:
    """Pairwise continuity: conductance of the s|s′ cut in the mutual-kNN graph.

    For each cohort pair, the mutual-kNN graph of the two cohorts together;
    conductance = cross-edges / min(volume_s, volume_s′). ≈0 = discontinuity
    (no mutual neighbours across), higher = a populated continuum.
    """
    cohort_of = np.asarray(cohorts, dtype=object)
    order = sorted(set(cohort_of.tolist()))
    C = np.zeros((len(order), len(order)))
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            rows = np.nonzero((cohort_of == order[i]) | (cohort_of == order[j]))[0]
            A = _mutual_knn_adjacency(np.asarray(Z, dtype=float)[rows], k)
            is_i = (cohort_of[rows] == order[i]).astype(float)
            cut = float(is_i @ A @ (1.0 - is_i))
            vol_i = float(A[is_i.astype(bool)].sum())
            vol_j = float(A[(~is_i.astype(bool))].sum())
            denom = min(vol_i, vol_j)
            C[i, j] = C[j, i] = (cut / denom) if denom > 0 else 0.0
    return order, C


def gap_statistic(
    Z: np.ndarray, cohorts: list[str] | np.ndarray, *, k_within: int = 10
) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Nearest-other-cohort distance vs within-cohort neighbour scale, per pair.

    For each researcher of *s*: ratio of its cosine distance to the nearest *s′*
    researcher over its own ``k_within``-th same-cohort neighbour distance.
    Median ratio ≲ 1.5 reads as a continuum, ≳ 3 with a bimodal distribution as
    a gap. Returns summaries keyed ``"s|s′"`` (directed).
    """
    Zn = normalize(np.asarray(Z, dtype=float))
    cohort_of = np.asarray(cohorts, dtype=object)
    order = sorted(set(cohort_of.tolist()))
    out: dict[str, dict[str, float]] = {}
    for s in order:
        rows_s = np.nonzero(cohort_of == s)[0]
        if len(rows_s) < k_within + 1:
            continue
        within = _knn_indices(Zn[rows_s], k_within)
        # Cosine distance to the k-th within neighbour.
        d_within = np.array(
            [1.0 - float(Zn[rows_s[i]] @ Zn[rows_s[within[i, -1]]]) for i in range(len(rows_s))]
        )
        for s2 in order:
            if s2 == s:
                continue
            rows_o = np.nonzero(cohort_of == s2)[0]
            if not len(rows_o):
                continue
            other = Zn[rows_o].T
            d_cross = 1.0 - np.concatenate(
                [
                    (Zn[rows_s[rows]] @ other).max(axis=1)
                    for rows in row_blocks(len(rows_s), len(rows_o))
                ]
            )
            ratio = d_cross / np.maximum(d_within, 1e-12)
            out[f"{s}|{s2}"] = {
                "median_ratio": float(np.median(ratio)),
                "frac_continuum": float((ratio <= 1.5).mean()),
                "frac_gap": float((ratio >= 3.0).mean()),
            }
    return order, out


def weak_link_ratio(
    Z: np.ndarray, cohorts: list[str] | np.ndarray, *, k: int = 15
) -> dict[str, float]:
    """Weakest link between cohort cores on the mutual-kNN graph, per pair.

    Dijkstra from medoid to medoid over the pair's mutual-kNN graph (edge weight
    = cosine distance); the reported value is the largest edge on the shortest
    path divided by the median within-cohort mutual-edge distance. ~1 = the
    bridge is as dense as cohort interiors; ``inf`` = disconnected (a true gap).
    """
    Zn = normalize(np.asarray(Z, dtype=float))
    cohort_of = np.asarray(cohorts, dtype=object)
    order = sorted(set(cohort_of.tolist()))
    out: dict[str, float] = {}
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            rows = np.nonzero((cohort_of == order[i]) | (cohort_of == order[j]))[0]
            sub = Zn[rows]
            A = _mutual_knn_adjacency(sub, k)
            # Weight mutual edges by cosine distance.
            coo = A.tocoo()
            dists = 1.0 - np.einsum("ij,ij->i", sub[coo.row], sub[coo.col])
            W = sparse.csr_matrix((np.maximum(dists, 0.0), (coo.row, coo.col)), shape=A.shape)
            labels = cohort_of[rows]
            same = labels[coo.row] == labels[coo.col]
            median_within = float(np.median(dists[same])) if same.any() else 0.0

            src = _cosine_medoid(sub, labels == order[i])
            dst = _cosine_medoid(sub, labels == order[j])
            dist_matrix, predecessors = dijkstra(
                W, directed=False, indices=[src], return_predecessors=True
            )
            key = f"{order[i]}|{order[j]}"
            if not np.isfinite(dist_matrix[0, dst]):
                out[key] = float("inf")
                continue
            # Walk the path back and take the largest single edge.
            max_edge, node = 0.0, dst
            while node != src:
                prev = int(predecessors[0, node])
                w = float(W[prev, node]) or float(W[node, prev])
                max_edge = max(max_edge, w)
                node = prev
            out[key] = max_edge / median_within if median_within > 0 else float("inf")
    return out


def neighbor_cohort_entropy(
    Z: np.ndarray,
    cohorts: list[str] | np.ndarray,
    *,
    k: int = 20,
    n_permutations: int = 200,
    random_state: int = 0,
) -> dict[str, Any]:
    """Per-researcher interdisciplinarity: entropy of the neighbour-cohort mix.

    Normalized Shannon entropy of the cohort distribution among each
    researcher's ``k`` joint neighbours (0 = insular, 1 = maximally mixed),
    with a label-permutation null (kNN fixed, labels shuffled) for calibration.
    """
    cohort_of = np.asarray(cohorts, dtype=object)
    order = sorted(set(cohort_of.tolist()))
    if len(order) < 2:
        return {"entropy": np.zeros(len(cohort_of)), "mean": 0.0, "z_mean": 0.0, "cohorts": order}
    neigh = _knn_indices(Z, k)
    log_s = np.log(len(order))

    def _entropies(labels: np.ndarray) -> np.ndarray:
        counts = _neighbor_cohort_counts(neigh, labels, order)
        p = counts / counts.sum(axis=1, keepdims=True)
        with np.errstate(divide="ignore", invalid="ignore"):
            h = -np.nansum(np.where(p > 0, p * np.log(p), 0.0), axis=1)
        return h / log_s

    H = _entropies(cohort_of)
    rng = np.random.default_rng(random_state)
    null_means = np.array(
        [float(_entropies(rng.permutation(cohort_of)).mean()) for _ in range(n_permutations)]
    )
    sd = float(null_means.std())
    z = (float(H.mean()) - float(null_means.mean())) / sd if sd > 0 else 0.0
    return {"entropy": H, "mean": float(H.mean()), "z_mean": z, "cohorts": order}


def cross_cohort_sense_mass(
    T: np.ndarray | sparse.spmatrix, sense_cohorts: dict[str, list[str]], sense_ids: list[str]
) -> np.ndarray:
    """Per-researcher fraction of TF mass on senses shared by ≥ 2 cohorts (*T* dense or sparse)."""
    if not sparse.issparse(T):
        T = np.asarray(T, dtype=float)
    shared = np.array([len(sense_cohorts.get(s, [])) >= 2 for s in sense_ids], dtype=bool)
    out = np.zeros(T.shape[0])
    for rows in row_blocks(T.shape[0], T.shape[1]):
        block = dense_rows(T, rows)
        total = block.sum(axis=1)
        out[rows] = np.divide(
            block[:, shared].sum(axis=1), total, out=np.zeros(block.shape[0]), where=total > 0
        )
    return out


def same_unit_auc(
    Z: np.ndarray,
    cohorts: list[str] | np.ndarray,
    units: list[str],
    *,
    min_positive_pairs: int = 5,
) -> float | None:
    """External validity: can joint distance recognize same-lab pairs across cohorts?

    Considers all cross-cohort researcher pairs with known units; scores each by
    cosine similarity and asks whether same-unit pairs rank higher (ROC AUC).
    Returns ``None`` when fewer than *min_positive_pairs* same-unit cross-cohort
    pairs exist (signal undefined).
    """
    from sklearn.metrics import roc_auc_score

    Zn = normalize(np.asarray(Z, dtype=float))
    cohort_of = np.asarray(cohorts, dtype=object)
    units_arr = np.asarray([str(u).strip() for u in units], dtype=object)
    known = np.nonzero(units_arr != "")[0]
    if not one_block(len(known), len(known)):
        return _same_unit_auc_by_blocks(Zn, cohort_of, units_arr, known, min_positive_pairs)
    ii, jj = np.meshgrid(known, known, indexing="ij")
    mask = (ii < jj) & (cohort_of[ii] != cohort_of[jj])
    ii, jj = ii[mask], jj[mask]
    if not len(ii):
        return None
    y = (units_arr[ii] == units_arr[jj]).astype(int)
    if int(y.sum()) < min_positive_pairs or int(y.sum()) == len(y):
        return None
    scores = np.einsum("ij,ij->i", Zn[ii], Zn[jj])
    return float(roc_auc_score(y, scores))


def _same_unit_auc_by_blocks(
    Zn: np.ndarray,
    cohort_of: np.ndarray,
    units_arr: np.ndarray,
    known: np.ndarray,
    min_positive_pairs: int,
) -> float | None:
    """:func:`same_unit_auc` on many researchers: the exact AUC, counted a block of rows at a time.

    AUC = P(a same-unit pair scores above a different-unit pair), ties counting
    one half: the same-unit pairs' scores are gathered and sorted in a first
    pass, then each different-unit pair is counted against them in a second.
    """
    Zk, coh, uni = Zn[known], cohort_of[known], units_arr[known]
    n = len(known)

    def pairs(rows: slice) -> tuple[np.ndarray, np.ndarray]:
        sims = Zk[rows] @ Zk.T
        i = np.arange(rows.start, rows.stop)[:, None]
        cross = (i < np.arange(n)[None, :]) & (coh[rows][:, None] != coh[None, :])
        same = cross & (uni[rows][:, None] == uni[None, :])
        return sims[same], sims[cross & ~same]

    positives = np.sort(np.concatenate([pairs(r)[0] for r in row_blocks(n, n)] or [np.zeros(0)]))
    if len(positives) < min_positive_pairs:
        return None
    above = 0.0
    n_neg = 0
    for rows in row_blocks(n, n):
        negatives = pairs(rows)[1]
        if not len(negatives):
            continue
        lower = np.searchsorted(positives, negatives, side="left")
        upper = np.searchsorted(positives, negatives, side="right")
        above += float((len(positives) - upper).sum()) + 0.5 * float((upper - lower).sum())
        n_neg += len(negatives)
    if n_neg == 0:
        return None
    return above / (float(len(positives)) * float(n_neg))


def shared_vocab_mass(
    T: np.ndarray | sparse.spmatrix, cohorts: list[str] | np.ndarray
) -> tuple[list[str], np.ndarray]:
    """Model-free overlap reference: V[s, s′] = Σ_senses min(p_s, p_s′).

    ``p_s`` is cohort *s*'s L1-normalized sense-mass profile (summed over its
    researchers). The geometry-based overlap (mixing matrix) must rank-correlate
    with this, or the joint space is inventing structure.
    """
    if not sparse.issparse(T):
        T = np.asarray(T, dtype=float)
    cohort_of = np.asarray(cohorts, dtype=object)
    order = sorted(set(cohort_of.tolist()))
    profiles = []
    for s in order:
        # Summed row after row in order, whether T is dense or sparse (the same bits).
        p = np.asarray(T[np.flatnonzero(cohort_of == s)].sum(axis=0), dtype=float).ravel()
        total = p.sum()
        profiles.append(p / total if total > 0 else p)
    P = np.vstack(profiles)
    V = np.zeros((len(order), len(order)))
    for i in range(len(order)):
        for j in range(len(order)):
            V[i, j] = float(np.minimum(P[i], P[j]).sum())
    return order, V


def mean_pairwise_cosine(V: np.ndarray) -> float:
    """Mean pairwise cosine similarity of a vector set (≥ 2 rows; else 1.0)."""
    V = normalize(np.asarray(V, dtype=float))
    n = V.shape[0]
    if n < 2:
        return 1.0
    if one_block(n, n):
        total = (V @ V.T).sum()
    else:  # Σᵢⱼ vᵢ·vⱼ = ‖Σᵢ vᵢ‖², without the n × n matrix
        s = V.sum(axis=0)
        total = float(s @ s)
    return float((total - n) / (n * (n - 1)))


def integrity_ratio(V_joint: np.ndarray, V_cohort: np.ndarray) -> float:
    """Concept integrity: joint-space tightness / cohort-space tightness.

    Both arguments are the SAME concept's member vectors, in the joint space and
    in the cohort's own space. < 0.5 flags a concept torn apart by the merge
    (usually a polysemy victim — feed back to reconciliation QA).
    """
    denom = mean_pairwise_cosine(V_cohort)
    if denom <= 0:
        return float("nan")
    return mean_pairwise_cosine(V_joint) / denom


def bridge_pairs(
    Z: np.ndarray,
    cohorts: list[str] | np.ndarray,
    researcher_ids: list[str],
    *,
    k_reference: int = 10,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """The named, inspectable bridge set: closest cross-cohort researcher pairs.

    A cross-cohort pair qualifies when its cosine similarity reaches the median
    within-cohort ``k_reference``-th-neighbour similarity (i.e. the two
    researchers are as close as ordinary same-cohort neighbours). Top *top_n*
    by similarity, with the qualification threshold attached.
    """
    Zn = normalize(np.asarray(Z, dtype=float))
    cohort_of = np.asarray(cohorts, dtype=object)
    neigh = _knn_indices(Zn, k_reference)
    kth_sim = []
    for i in range(Zn.shape[0]):
        same = [j for j in neigh[i] if cohort_of[j] == cohort_of[i]]
        if same:
            kth_sim.append(float(Zn[i] @ Zn[same[-1]]))
    threshold = float(np.median(kth_sim)) if kth_sim else 1.0

    n = Zn.shape[0]
    found_i, found_j, found_s = [], [], []
    for rows in row_blocks(n, n):
        sims = Zn[rows] @ Zn.T
        ii, jj = np.meshgrid(np.arange(rows.start, rows.stop), np.arange(n), indexing="ij")
        mask = (ii < jj) & (cohort_of[ii] != cohort_of[jj]) & (sims >= threshold)
        found_i.append(ii[mask])
        found_j.append(jj[mask])
        found_s.append(sims[mask])
    ii = np.concatenate(found_i) if found_i else np.zeros(0, dtype=int)
    jj = np.concatenate(found_j) if found_j else np.zeros(0, dtype=int)
    sims_found = np.concatenate(found_s) if found_s else np.zeros(0)
    order = np.argsort(-sims_found)[:top_n]
    return [
        {
            "a": researcher_ids[int(ii[o])],
            "b": researcher_ids[int(jj[o])],
            "cohort_a": str(cohort_of[int(ii[o])]),
            "cohort_b": str(cohort_of[int(jj[o])]),
            "cosine": round(float(sims_found[o]), 4),
            "threshold": round(threshold, 4),
        }
        for o in order
    ]
