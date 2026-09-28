# SPDX-License-Identifier: MIT
"""Ward at any size: the weighted linkage, the two-stage cut and the exact path."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.cluster.hierarchy import fcluster, linkage
from sklearn.metrics import adjusted_rand_score

from cartolex.atlas.clustering import (
    EXACT_WARD_LIMIT,
    fit_agglomerative_labels,
    micro_cluster_count,
    two_stage_ward_labels,
    ward_labels,
    weighted_ward_linkage,
)


@pytest.mark.parametrize("seed", range(10))
def test_unit_weights_give_scipys_ward_linkage_exactly(seed):
    rng = np.random.default_rng(seed)
    n, d = int(rng.integers(2, 250)), int(rng.integers(1, 12))
    points = rng.normal(size=(n, d))
    if seed % 3 == 0:
        points = np.round(points, 1)  # ties
    assert np.array_equal(weighted_ward_linkage(points, np.ones(n)), linkage(points, "ward"))


@pytest.mark.parametrize("seed", range(8))
def test_weighted_ward_is_ward_on_the_points_repeated(seed):
    rng = np.random.default_rng(50 + seed)
    m, d = int(rng.integers(3, 50)), int(rng.integers(2, 6))
    points = rng.normal(size=(m, d))
    weights = rng.integers(1, 6, size=m)
    repeated = np.repeat(points, weights, axis=0)
    owner = np.repeat(np.arange(m), weights)
    full = linkage(repeated, "ward")
    mine = weighted_ward_linkage(points, weights.astype(float))
    heights = np.sort(full[:, 2][full[:, 2] > 1e-12])
    assert np.allclose(heights, np.sort(mine[:, 2]))
    for k in (2, 3, max(2, m // 3)):
        a = fcluster(full, k, "maxclust")
        b = fcluster(mine, k, "maxclust")
        first = np.array([a[owner == i][0] for i in range(m)])
        assert adjusted_rand_score(first, b) == 1.0


def test_weighted_ward_refuses_bad_weights():
    with pytest.raises(ValueError):
        weighted_ward_linkage(np.zeros((3, 2)), np.array([1.0, 0.0, 2.0]))
    assert weighted_ward_linkage(np.zeros((1, 2)), np.ones(1)).shape == (0, 4)


def _blobs(rng, n, k, d=12):
    centres = rng.normal(size=(k, d)) * 4
    which = rng.integers(0, k, size=n)
    points = centres[which] + rng.normal(size=(n, d))
    return points / np.linalg.norm(points, axis=1, keepdims=True), which


def test_the_exact_path_is_scipys_below_the_limit():
    rng = np.random.default_rng(3)
    points, _ = _blobs(rng, 400, 7)
    want = fcluster(linkage(points, "ward"), t=9, criterion="maxclust").astype(int) - 1
    assert np.array_equal(ward_labels(points, 9), want)
    assert np.array_equal(fit_agglomerative_labels(points, n_clusters=9), want)
    assert EXACT_WARD_LIMIT >= 10_000


def test_the_two_stage_cut_is_deterministic_and_close_to_exact_ward():
    rng = np.random.default_rng(4)
    points, truth = _blobs(rng, 3000, 12)
    exact = ward_labels(points, 12)
    first = ward_labels(points, 12, limit=600)
    again = two_stage_ward_labels(points, 12, limit=600)
    assert np.array_equal(first, again)
    assert sorted(set(first.tolist())) == list(range(12))
    assert adjusted_rand_score(exact, first) > 0.95
    assert adjusted_rand_score(truth, first) > 0.95


def test_many_groups_take_the_k_means_partition():
    rng = np.random.default_rng(5)
    points, _ = _blobs(rng, 500, 10)
    labels = two_stage_ward_labels(points, 200, limit=300)
    assert len(set(labels.tolist())) <= 200
    assert labels.min() == 0 and labels.max() == len(set(labels.tolist())) - 1
    assert micro_cluster_count(10**5, 5000) == EXACT_WARD_LIMIT
    assert micro_cluster_count(500, 10, limit=300) == 300
    assert np.array_equal(two_stage_ward_labels(points, 1, limit=300), np.zeros(500, dtype=int))
