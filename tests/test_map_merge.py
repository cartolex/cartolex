# SPDX-License-Identifier: MIT
"""Tests for the pooled multi-cohort matrix + joint embedding (cartolex.atlas.map_merge).

Synthetic math/physics vocabularies only — no real workspace data.
"""

from __future__ import annotations

import numpy as np
import pytest

from cartolex.atlas.map_merge import (
    CohortInput,
    JointData,
    anchor_concepts,
    assemble_joint_matrix,
    balanced_svd,
    joint_idf,
    namespaced_id,
    procrustes_residual,
    researcher_concept_weights,
    weight_matrix,
)
from cartolex.atlas.reconcile import CohortSense, reconcile


def _sense(cohort: str, surface: str, context: list[str]) -> CohortSense:
    return CohortSense(
        cohort_id=cohort,
        surface=surface,
        source_terms=[surface],
        df=5,
        tf_total=5.0,
        context=[(c, 1.0) for c in context],
    )


SHARED_CTX = ["critical exponent", "order parameter", "universality"]


def _table():
    """c2 (math) + c1 (physics): 'phase transition' merges, 'spectrum' splits."""
    cohort_c2 = [
        _sense("c2", "spectral gap", ["eigenvalue", "mixing time"]),
        _sense("c2", "markov chain", ["mixing time", "martingale"]),
        _sense("c2", "phase transition", [*SHARED_CTX, "percolation"]),
        _sense("c2", "spectrum", ["eigenvalue", "operator", "hilbert space", "resolvent"]),
    ]
    cohort_c1 = [
        _sense("c1", "phase transition", [*SHARED_CTX, "ising model"]),
        _sense("c1", "laser", ["photon", "cavity"]),
        _sense("c1", "spectrum", ["emission", "wavelength", "laser", "photoluminescence"]),
    ]
    return reconcile([cohort_c2, cohort_c1])


def _inputs() -> list[CohortInput]:
    # c2: 2 researchers × 4 terms; c1: 3 researchers × 3 terms.
    Xc2 = np.array(
        [
            [2.0, 1.0, 3.0, 0.0],  # spectral gap, markov chain, phase transition, spectrum
            [0.0, 2.0, 1.0, 4.0],
        ]
    )
    Xc1 = np.array(
        [
            [1.0, 0.0, 2.0],  # phase transition, laser, spectrum
            [0.0, 3.0, 1.0],
            [2.0, 1.0, 0.0],
        ]
    )
    return [
        CohortInput(
            cohort_id="c2",
            terms=["spectral gap", "markov chain", "phase transition", "spectrum"],
            X_tf=Xc2,
            researcher_ids=["r000000", "r000001"],
            units=["GRP1", "GRP2"],
        ),
        CohortInput(
            cohort_id="c1",
            terms=["phase transition", "laser", "spectrum"],
            X_tf=Xc1,
            researcher_ids=["r000000", "r000001", "r000002"],
            units=["GRP2", "GRP3", "GRP3"],
        ),
    ]


def test_assemble_pools_merged_senses_and_namespaces_rows() -> None:
    table = _table()
    joint = assemble_joint_matrix(_inputs(), table)

    assert joint.researcher_ids == [
        "c1:r000000",
        "c1:r000001",
        "c1:r000002",
        "c2:r000000",
        "c2:r000001",
    ]
    assert joint.cohorts == ["c1", "c1", "c1", "c2", "c2"]
    assert joint.units == ["GRP2", "GRP3", "GRP3", "GRP1", "GRP2"]
    assert not joint.unmapped_terms

    col = {s: j for j, s in enumerate(joint.sense_ids)}
    # The merged sense pools both cohorts' TF; the split senses stay apart.
    assert "phase transition" in col and "spectrum#c2" in col and "spectrum#c1" in col
    np.testing.assert_allclose(joint.T[:, col["phase transition"]], [1.0, 0.0, 2.0, 3.0, 1.0])
    np.testing.assert_allclose(joint.T[:, col["spectrum#c2"]], [0.0, 0.0, 0.0, 0.0, 4.0])
    np.testing.assert_allclose(joint.T[:, col["spectrum#c1"]], [2.0, 1.0, 0.0, 0.0, 0.0])
    # Pooling preserves total mass (every raw term is mapped).
    assert joint.T.sum() == pytest.approx(sum(np.asarray(s.X_tf).sum() for s in _inputs()))
    assert joint.sense_cohorts["phase transition"] == ["c1", "c2"]


def test_assemble_counts_unmapped_terms() -> None:
    table = _table()
    inputs = _inputs()
    inputs[1].terms[1] = "unmapped term"  # 'laser' column no longer in the table
    joint = assemble_joint_matrix(inputs, table)
    assert joint.unmapped_terms == {"c1": 1}
    col = {s: j for j, s in enumerate(joint.sense_ids)}
    np.testing.assert_allclose(joint.T[:, col["laser"]], 0.0)


def test_joint_idf_pooled_and_macro() -> None:
    T = np.array(
        [
            [1.0, 1.0],
            [1.0, 0.0],
            [1.0, 0.0],
            [1.0, 0.0],
        ]
    )
    cohorts = ["a", "a", "a", "b"]
    pooled = joint_idf(T, cohorts, mode="pooled")
    # Sense 0: df=4/N=4 → log(5/5)+1 = 1; sense 1: df=1 → log(5/2)+1.
    np.testing.assert_allclose(pooled, [1.0, np.log(5 / 2) + 1.0])
    macro = joint_idf(T, cohorts, mode="macro")
    # Sense 0 used by everyone in both cohorts → mean_frac 1 → ~0.
    assert macro[0] == pytest.approx(np.log(1 / (1e-6 + 1.0)), abs=1e-4)
    # Sense 1: fracs 1/3 and 0 → mean 1/6.
    assert macro[1] == pytest.approx(np.log(1 / (1e-6 + 1 / 6)), rel=1e-4)
    with pytest.raises(ValueError, match="idf mode"):
        joint_idf(T, cohorts, mode="nope")


def test_weight_matrix_length_bonus_sublinear_and_l2() -> None:
    joint = JointData(
        T=np.array([[np.e, 1.0]]),  # sublinear: 1+log(e)=2 vs 1+log(1)=1
        sense_ids=["spectral gap", "laser"],  # 2 tokens vs 1 token
        researcher_ids=["c2:r000000"],
        cohorts=["c2"],
        units=[""],
        sense_cohorts={"spectral gap": ["c2"], "laser": ["c2"]},
        unmapped_terms={},
    )
    W = weight_matrix(joint, idf_mode="pooled", length_alpha=2.0)
    # Both senses have df=1/N=1 → same IDF; ratio driven by sublinear TF (2 vs 1)
    # and the 2-token length bonus (1+2·1=3 vs 1): (2·3)/(1·1) = 6.
    assert np.linalg.norm(W[0]) == pytest.approx(1.0)
    assert W[0, 0] / W[0, 1] == pytest.approx(6.0)


def test_balanced_svd_is_deterministic_and_balanced() -> None:
    rng = np.random.default_rng(0)
    n_a, n_b, n_senses = 12, 3, 8
    X = np.vstack(
        [
            rng.normal(size=(n_a, n_senses)) + 4.0,  # big cohort 'a'
            rng.normal(size=(n_b, n_senses)) - 4.0,  # small cohort 'b'
        ]
    )
    cohorts = ["a"] * n_a + ["b"] * n_b
    ids = [f"a:r{i:03d}" for i in range(n_a)] + [f"b:r{i:03d}" for i in range(n_b)]

    e1 = balanced_svd(X, cohorts, ids, n_components=4, per_cohort_cap=4)
    e2 = balanced_svd(X, cohorts, ids, n_components=4, per_cohort_cap=4)
    np.testing.assert_allclose(e1.Z_ind, e2.Z_ind)
    np.testing.assert_allclose(e1.Z_senses, e2.Z_senses)

    assert e1.Z_ind.shape == (n_a + n_b, 4)
    assert e1.Z_senses.shape == (n_senses, 4)
    # Balanced fit: 4 rows from 'a' (capped), all 3 from 'b'.
    assert int(e1.fit_rows[:n_a].sum()) == 4
    assert int(e1.fit_rows[n_a:].sum()) == 3
    assert 0.0 < e1.explained_variance <= 1.0 + 1e-9


def test_anchor_concepts_recenters_curated_termsets() -> None:
    table = _table()
    joint = assemble_joint_matrix(_inputs(), table)
    Z_senses = np.eye(len(joint.sense_ids))  # orthonormal sense axes → easy centroids
    taxonomies = {
        "c2": {
            "concepts": [
                {"id": 0, "label": "Stochastics", "terms": ["markov chain", "spectral gap"]},
                {"id": 1, "label": "Ghost", "terms": ["not a term"]},  # fully unmapped → skipped
            ]
        }
    }
    meta, anchors = anchor_concepts(taxonomies, table, Z_senses, joint.sense_ids)
    assert [m["label"] for m in meta] == ["Stochastics"]
    col = {s: j for j, s in enumerate(joint.sense_ids)}
    expected = np.zeros(len(joint.sense_ids))
    expected[[col["markov chain"], col["spectral gap"]]] = 0.5
    np.testing.assert_allclose(anchors[0], expected / np.linalg.norm(expected))


def test_researcher_concept_weights_are_l1_evidence_shares() -> None:
    table = _table()
    joint = assemble_joint_matrix(_inputs(), table)
    col = {s: j for j, s in enumerate(joint.sense_ids)}
    meta = [
        {"label": "A", "sense_cols": [col["phase transition"]]},
        {"label": "B", "sense_cols": [col["spectrum#c2"], col["markov chain"]]},
    ]
    W = researcher_concept_weights(joint, meta)
    # Row c2:r000001 → phase transition 1.0, spectrum#c2 4.0 + markov chain 2.0.
    np.testing.assert_allclose(W[4], [1.0 / 7.0, 6.0 / 7.0])
    sums = W.sum(axis=1)
    assert set(np.round(sums, 12).tolist()) <= {0.0, 1.0}


def test_procrustes_residual_zero_under_rotation_and_pads_dims() -> None:
    rng = np.random.default_rng(1)
    A = rng.normal(size=(20, 6))
    Q, _ = np.linalg.qr(rng.normal(size=(6, 6)))
    assert procrustes_residual(A, A @ Q) == pytest.approx(0.0, abs=1e-10)
    # Dimension padding: comparing 6-dim vs its first 4 dims is legal and > 0.
    assert procrustes_residual(A, A[:, :4]) > 0.0
    with pytest.raises(ValueError, match="Row mismatch"):
        procrustes_residual(A, A[:-1])


def test_namespaced_id() -> None:
    assert namespaced_id("c3", "p-abc") == "c3:p-abc"
