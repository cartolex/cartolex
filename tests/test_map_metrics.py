# SPDX-License-Identifier: MIT
"""Tests for the merged-space metric battery (cartolex.atlas.map_metrics).

Planted synthetic geometries only (math/physics-flavoured labels) — no real data.
"""

from __future__ import annotations

import numpy as np
import pytest
from sklearn.preprocessing import normalize

from cartolex.atlas.map_metrics import (
    bridge_pairs,
    conductance_matrix,
    cross_cohort_sense_mass,
    gap_statistic,
    integrity_ratio,
    mean_pairwise_cosine,
    mixing_matrix,
    mixing_permutation_zscores,
    neighbor_cohort_entropy,
    restricted_knn_overlap,
    same_unit_auc,
    shared_vocab_mass,
    weak_link_ratio,
)


def _blob(axis: np.ndarray, n: int, rng: np.random.Generator, spread: float = 0.08) -> np.ndarray:
    pts = axis[None, :] + spread * rng.normal(size=(n, axis.size))
    return normalize(pts)


def _two_separated_blobs(n: int = 25, dim: int = 8, seed: int = 0):
    """Cohort c2 (math) on one axis, cohort c1 (physics) on an orthogonal one."""
    rng = np.random.default_rng(seed)
    e = np.eye(dim)
    Z = np.vstack([_blob(e[0], n, rng), _blob(e[4], n, rng)])
    cohorts = ["c2"] * n + ["c1"] * n
    return Z, cohorts


def _one_cloud_two_labels(n: int = 25, dim: int = 8, seed: int = 0):
    rng = np.random.default_rng(seed)
    e = np.eye(dim)
    Z = _blob(e[0], 2 * n, rng, spread=0.15)
    cohorts = (["c2", "c1"] * n)[: 2 * n]
    return Z, cohorts


# ── Fidelity ──────────────────────────────────────────────────────────────────
def test_restricted_knn_overlap_identity_is_one_and_noise_is_low() -> None:
    rng = np.random.default_rng(2)
    Z = rng.normal(size=(40, 6))
    assert restricted_knn_overlap(Z, Z, k=10) == pytest.approx(1.0)
    unrelated = rng.normal(size=(40, 6))
    assert restricted_knn_overlap(Z, unrelated, k=10) < 0.45
    with pytest.raises(ValueError, match="Row mismatch"):
        restricted_knn_overlap(Z, Z[:-1])


def test_integrity_ratio_detects_torn_concepts() -> None:
    tight = np.tile(np.array([1.0, 0.0, 0.0]), (4, 1))
    torn = np.eye(4, 3)  # near-orthogonal member vectors
    assert integrity_ratio(tight, tight) == pytest.approx(1.0)
    assert integrity_ratio(torn, tight) < 0.5
    assert mean_pairwise_cosine(tight[:1]) == 1.0


# ── Overlap ───────────────────────────────────────────────────────────────────
def test_mixing_matrix_separated_vs_mixed() -> None:
    Z, cohorts = _two_separated_blobs()
    order, M = mixing_matrix(Z, cohorts, k=10)
    assert order == ["c1", "c2"]
    assert M[0, 0] > 0.95 and M[1, 1] > 0.95  # insular diagonals
    assert M[0, 1] < 0.05 and M[1, 0] < 0.05

    Zm, labels_m = _one_cloud_two_labels()
    _, Mm = mixing_matrix(Zm, labels_m, k=10)
    assert 0.3 < Mm[0, 1] < 0.7 and 0.3 < Mm[1, 0] < 0.7


def test_mixing_permutation_zscores_signs() -> None:
    Z, cohorts = _two_separated_blobs()
    order, z = mixing_permutation_zscores(Z, cohorts, k=10, n_permutations=100)
    assert order == ["c1", "c2"]
    assert z[0, 1] < -3 and z[1, 0] < -3  # far less cross-mixing than random
    assert z[0, 0] > 3 and z[1, 1] > 3


def test_shared_vocab_mass_bounds() -> None:
    # Identical cohort profiles → 1; disjoint supports → 0.
    T = np.array(
        [
            [2.0, 2.0, 0.0, 0.0],
            [1.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 3.0, 1.0],
        ]
    )
    order, V = shared_vocab_mass(T, ["c2", "c2", "c1"])
    assert order == ["c1", "c2"]
    assert V[0, 0] == pytest.approx(1.0) and V[1, 1] == pytest.approx(1.0)
    assert V[0, 1] == pytest.approx(0.0)

    order2, V2 = shared_vocab_mass(np.array([[1.0, 1.0], [1.0, 1.0]]), ["c2", "c1"])
    assert V2[0, 1] == pytest.approx(1.0)


# ── Continuity / discontinuity ────────────────────────────────────────────────
def test_conductance_zero_across_gap_positive_in_continuum() -> None:
    Z, cohorts = _two_separated_blobs()
    _, C = conductance_matrix(Z, cohorts, k=10)
    assert C[0, 1] == pytest.approx(0.0, abs=1e-12)

    Zm, labels_m = _one_cloud_two_labels()
    _, Cm = conductance_matrix(Zm, labels_m, k=10)
    assert Cm[0, 1] > 0.1


def test_gap_statistic_separates_gap_from_continuum() -> None:
    Z, cohorts = _two_separated_blobs()
    _, gaps = gap_statistic(Z, cohorts)
    assert gaps["c2|c1"]["median_ratio"] > 3.0
    assert gaps["c2|c1"]["frac_gap"] > 0.8

    Zm, labels_m = _one_cloud_two_labels()
    _, gm = gap_statistic(Zm, labels_m)
    assert gm["c2|c1"]["median_ratio"] < 1.5
    assert gm["c2|c1"]["frac_continuum"] > 0.8


def test_weak_link_ratio_inf_across_gap_finite_in_continuum() -> None:
    Z, cohorts = _two_separated_blobs()
    wl = weak_link_ratio(Z, cohorts, k=8)
    assert wl["c1|c2"] == float("inf")

    Zm, labels_m = _one_cloud_two_labels()
    wlm = weak_link_ratio(Zm, labels_m, k=8)
    assert np.isfinite(wlm["c1|c2"]) and wlm["c1|c2"] < 5.0


def test_bridge_pairs_finds_planted_bridge() -> None:
    Z, cohorts = _two_separated_blobs()
    bridge = normalize((Z[0] + Z[-1]).reshape(1, -1))[0]
    Z2 = np.vstack([Z, bridge + 1e-4, bridge - 1e-4])
    cohorts2 = [*cohorts, "c2", "c1"]
    ids = [f"{s}:r{i:03d}" for i, s in enumerate(cohorts2)]
    pairs = bridge_pairs(Z2, cohorts2, ids, top_n=5)
    assert pairs, "the planted cross-cohort pair must qualify as a bridge"
    top = pairs[0]
    assert {top["a"], top["b"]} == {ids[-2], ids[-1]}
    assert top["cosine"] > 0.99


# ── Interdisciplinarity ───────────────────────────────────────────────────────
def test_neighbor_cohort_entropy_insular_vs_mixed() -> None:
    Z, cohorts = _two_separated_blobs()
    res = neighbor_cohort_entropy(Z, cohorts, k=10, n_permutations=100)
    assert res["mean"] < 0.1
    assert res["z_mean"] < -3

    Zm, labels_m = _one_cloud_two_labels()
    resm = neighbor_cohort_entropy(Zm, labels_m, k=10, n_permutations=100)
    assert resm["mean"] > 0.8


def test_cross_cohort_sense_mass_shares() -> None:
    T = np.array([[1.0, 3.0], [2.0, 0.0], [0.0, 0.0]])
    sense_ids = ["phase transition", "spectrum#c2"]
    sense_cohorts = {"phase transition": ["c1", "c2"], "spectrum#c2": ["c2"]}
    mass = cross_cohort_sense_mass(T, sense_cohorts, sense_ids)
    np.testing.assert_allclose(mass, [0.25, 1.0, 0.0])


# ── External validity ─────────────────────────────────────────────────────────
def test_same_unit_auc_recognizes_close_same_lab_pairs() -> None:
    rng = np.random.default_rng(3)
    dim, n = 6, 8
    base = normalize(rng.normal(size=(n, dim)))
    # Cohort c2 researchers; cohort c1 researchers duplicated nearby with the
    # same unit → same-unit cross-cohort pairs are the closest pairs.
    Z = np.vstack([base, normalize(base + 0.02 * rng.normal(size=(n, dim)))])
    cohorts = ["c2"] * n + ["c1"] * n
    units = [f"GRP{i}" for i in range(n)] * 2
    auc = same_unit_auc(Z, cohorts, units)
    assert auc is not None and auc > 0.95

    # Too few positive pairs → None.
    units_none = [f"U{i}" for i in range(2 * n)]
    assert same_unit_auc(Z, cohorts, units_none) is None
    # Unknown units are excluded.
    assert same_unit_auc(Z, cohorts, [""] * (2 * n)) is None
