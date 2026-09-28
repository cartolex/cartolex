# SPDX-License-Identifier: MIT
"""The steps that keep people × keywords matrices sparse give the dense results.

Each step that needs dense arithmetic takes its matrix a block of rows at a
time (``cartolex.atlas.blocks``). With one block (every demo world) it is the
former dense code; these tests shrink the block so that many blocks are used,
and check that the results are the same: bit for bit where the step works row
by row, within rounding where a matrix product is involved.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import sparse

from cartolex.atlas import blocks, map_merge, map_metrics, placement, trajectories
from cartolex.atlas.map_merge import JointData
from cartolex.atlas.reconcile import build_cohort_senses


def _tf(rng: np.random.Generator, n: int, m: int, density: float = 0.15) -> np.ndarray:
    X = rng.random((n, m)) * (rng.random((n, m)) < density)
    X[X > 0] = np.round(X[X > 0] * 7, 3) + 0.001
    return X


def _joint(rng: np.random.Generator, n: int = 60, m: int = 40) -> JointData:
    T = _tf(rng, n, m)
    cohorts = ["a"] * (n // 2) + ["b"] * (n - n // 2)
    senses = [f"s{j}" + (" x" * (j % 3)) for j in range(m)]
    return JointData(
        T=T,
        sense_ids=senses,
        researcher_ids=[f"{c}:r{i:03d}" for i, c in enumerate(cohorts)],
        cohorts=cohorts,
        units=[f"u{i % 7}" for i in range(n)],
        sense_cohorts={s: (["a", "b"] if j % 4 == 0 else ["a"]) for j, s in enumerate(senses)},
        unmapped_terms={},
    )


@pytest.fixture
def small_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blocks of a few rows: every step runs over many blocks."""
    monkeypatch.setattr(blocks, "BLOCK_CELLS", 97)


def test_row_blocks_cover_every_row_once() -> None:
    got = list(blocks.row_blocks(10, 3, cells=7))
    assert [(s.start, s.stop) for s in got] == [(0, 2), (2, 4), (4, 6), (6, 8), (8, 10)]
    assert list(blocks.row_blocks(0, 5)) == []
    assert [(s.start, s.stop) for s in blocks.row_blocks(3, 1000, cells=10)] == [
        (0, 1),
        (1, 2),
        (2, 3),
    ]
    with pytest.raises(ValueError, match="two-dimensional"):
        blocks.as_csr(np.zeros(3))


def test_the_pooled_weights_are_the_same_in_blocks(small_blocks: None) -> None:
    rng = np.random.default_rng(0)
    joint = _joint(rng)
    many = map_merge.weight_matrix(joint)
    many_macro = map_merge.weight_matrix(joint, idf_mode="macro")
    blocks.BLOCK_CELLS = 1 << 24
    one = map_merge.weight_matrix(joint)
    assert sparse.issparse(many)
    assert np.array_equal(many.toarray(), one.toarray())
    assert np.array_equal(
        many_macro.toarray(), map_merge.weight_matrix(joint, idf_mode="macro").toarray()
    )


def test_pooling_sparse_equals_the_dense_loop() -> None:
    rng = np.random.default_rng(1)
    X = _tf(rng, 30, 12)
    # three raw terms fold onto sense 0, two onto sense 1
    target = np.array([0, 1, 0, 2, 3, 0, 1, 4, 5, 6, 7, 8])
    dense = np.zeros((30, 9))
    for j, t in enumerate(target):
        dense[:, t] += X[:, j]
    fold = sparse.csr_matrix((np.ones(12), (np.arange(12), target)), shape=(12, 9))
    assert np.array_equal((blocks.as_csr(X) @ fold).toarray(), dense)


def test_concept_weights_and_masses_are_the_same_in_blocks(small_blocks: None) -> None:
    rng = np.random.default_rng(2)
    joint = _joint(rng)
    meta = [{"sense_cols": [0, 3, 5]}, {"sense_cols": [1, 2]}, {"sense_cols": []}]
    many = map_merge.researcher_concept_weights(joint, meta)
    mass = map_metrics.cross_cohort_sense_mass(joint.T, joint.sense_cohorts, joint.sense_ids)
    blocks.BLOCK_CELLS = 1 << 24
    assert np.array_equal(many, map_merge.researcher_concept_weights(joint, meta))
    dense = joint.T.toarray()
    assert np.array_equal(
        mass, map_metrics.cross_cohort_sense_mass(dense, joint.sense_cohorts, joint.sense_ids)
    )
    assert np.array_equal(
        map_metrics.shared_vocab_mass(joint.T, joint.cohorts)[1],
        map_metrics.shared_vocab_mass(dense, joint.cohorts)[1],
    )


def test_the_joint_embedding_in_blocks_is_within_rounding(small_blocks: None) -> None:
    rng = np.random.default_rng(3)
    joint = _joint(rng, n=80, m=30)
    W = map_merge.weight_matrix(joint)
    many = map_merge.balanced_svd(
        W, joint.cohorts, joint.researcher_ids, n_components=5, per_cohort_cap=30
    )
    blocks.BLOCK_CELLS = 1 << 24
    one = map_merge.balanced_svd(
        W, joint.cohorts, joint.researcher_ids, n_components=5, per_cohort_cap=30
    )
    dense = map_merge.balanced_svd(
        W.toarray(), joint.cohorts, joint.researcher_ids, n_components=5, per_cohort_cap=30
    )
    assert np.array_equal(one.Z_ind, dense.Z_ind)
    # A sparse fit (the fit rows exceed a block) and a blocked transform: rounding only.
    np.testing.assert_allclose(many.Z_ind, one.Z_ind, atol=1e-10)
    np.testing.assert_allclose(many.Z_senses, one.Z_senses, atol=1e-10)


def test_cohort_senses_are_the_same_from_a_sparse_matrix() -> None:
    rng = np.random.default_rng(4)
    terms = [f"term {j}" for j in range(25)] + ["term 3s", "terms 4"]
    X = _tf(rng, 40, len(terms), density=0.25)
    dense = build_cohort_senses("c", terms, X)
    from_sparse = build_cohort_senses("c", terms, sparse.csr_matrix(X))
    assert dense == from_sparse
    assert any(s.context for s in dense)


def test_trajectory_projection_in_blocks(small_blocks: None) -> None:
    class Svd:
        components_ = np.random.default_rng(5).normal(size=(4, 20))

        def transform(self, X: np.ndarray) -> np.ndarray:
            return X @ self.components_.T

    rng = np.random.default_rng(6)
    B = _tf(rng, 50, 20)
    B[7] = 0.0
    anchors = placement.MapAnchors(rng.normal(size=(30, 4)), rng.normal(size=(30, 2)))
    many = trajectories.project_trajectories(sparse.csr_matrix(B), Svd(), anchors)
    blocks.BLOCK_CELLS = 1 << 24
    np.testing.assert_allclose(
        many, trajectories.project_trajectories(B, Svd(), anchors), atol=1e-12
    )


def _brute_nearest(V: np.ndarray, A: np.ndarray, k: int, exclude_self: bool) -> tuple:
    """The former ranking: every exact distance, sorted by distance then index."""
    d = 1.0 - np.einsum("ij,kj->ik", V, A, optimize=False)
    np.clip(d, 0.0, 2.0, out=d)
    if exclude_self:
        d[np.arange(len(V)), np.arange(len(V))] = np.inf
    idx = np.lexsort((np.broadcast_to(np.arange(A.shape[0]), d.shape), d), axis=1)[:, :k]
    return idx, np.take_along_axis(d, idx, axis=1)


@pytest.mark.parametrize("exclude_self", [False, True])
def test_the_nearest_anchors_are_those_of_the_exact_distances(exclude_self: bool) -> None:
    rng = np.random.default_rng(7)
    A = rng.normal(size=(300, 6))
    A[50:80] = A[10]  # thirty identical anchors: ties broken by index
    A[200:203] = -A[5]
    A = placement._unit_rows(A)
    V = (
        A.copy()
        if exclude_self
        else placement._unit_rows(np.vstack([A[:40], rng.normal(size=(60, 6))]))
    )
    for k in (1, 8, 40):
        idx, dk = placement._nearest(V, A, k, 0 if exclude_self else None)
        want_idx, want_d = _brute_nearest(V, A, k, exclude_self)
        assert np.array_equal(idx, want_idx)
        assert np.array_equal(dk, want_d)


def test_placement_does_not_depend_on_the_chunk_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(8)
    z, xy = rng.normal(size=(120, 5)), rng.normal(size=(120, 2))
    vectors = rng.normal(size=(77, 5))
    whole = placement.place(vectors, z, xy)
    monkeypatch.setattr(placement, "CHUNK_CELLS", 500)  # four rows a chunk
    small = placement.place(vectors, z, xy)
    assert np.array_equal(whole.xy, small.xy)
    assert np.array_equal(whole.neighbours, small.neighbours)


def test_pair_metrics_in_blocks(small_blocks: None) -> None:
    rng = np.random.default_rng(9)
    Z = np.vstack([rng.normal(loc=1.0, size=(40, 5)), rng.normal(loc=-1.0, size=(35, 5))])
    cohorts = ["a"] * 40 + ["b"] * 35
    units = [f"u{i % 9}" if i % 5 else "" for i in range(75)]
    ids = [f"r{i}" for i in range(75)]
    many = {
        "auc": map_metrics.same_unit_auc(Z, cohorts, units),
        "gap": map_metrics.gap_statistic(Z, cohorts)[1],
        "weak": map_metrics.weak_link_ratio(Z, cohorts),
        "bridges": map_metrics.bridge_pairs(Z, cohorts, ids),
        "cos": map_metrics.mean_pairwise_cosine(Z),
    }
    blocks.BLOCK_CELLS = 1 << 24
    one = {
        "auc": map_metrics.same_unit_auc(Z, cohorts, units),
        "gap": map_metrics.gap_statistic(Z, cohorts)[1],
        "weak": map_metrics.weak_link_ratio(Z, cohorts),
        "bridges": map_metrics.bridge_pairs(Z, cohorts, ids),
        "cos": map_metrics.mean_pairwise_cosine(Z),
    }
    assert one["auc"] is not None
    assert many["auc"] == pytest.approx(one["auc"], abs=1e-12)
    assert many["cos"] == pytest.approx(one["cos"], abs=1e-12)
    for key, value in one["gap"].items():
        for name, number in value.items():
            assert many["gap"][key][name] == pytest.approx(number, abs=1e-12)
    assert many["weak"] == pytest.approx(one["weak"])
    assert [(b["a"], b["b"]) for b in many["bridges"]] == [(b["a"], b["b"]) for b in one["bridges"]]
