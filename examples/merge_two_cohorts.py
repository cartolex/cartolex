# SPDX-License-Identifier: MIT
"""Merging two cohorts through ``map_bundle/2`` bundles — no network needed.

Builds two tiny synthetic cohorts (math- and physics-flavoured fake
vocabularies), serializes each to a portable cohort bundle, reads the bundles
back, and runs the offline merge pipeline:

    build_bundle ×2 -> write_bundle -> read_bundle -> build_cohort_senses
    -> build_naive_table (no embedder) -> assemble_joint_matrix -> balanced_svd

`build_naive_table` is the offline ablation (merge every shared surface); a
production merge would call `reconcile` with an embedder and an adjudication
decisions cache instead — see INTEGRATION.md.

Usage::

    python examples/merge_two_cohorts.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from cartolex.atlas.map_bundle import build_bundle, read_bundle, write_bundle
from cartolex.atlas.map_merge import CohortInput, assemble_joint_matrix, balanced_svd
from cartolex.atlas.reconcile import build_cohort_senses, build_naive_table

# ── 1. Two synthetic cohorts ────────────────────────────────────────────────


def synthetic_cohorts() -> list[CohortInput]:
    """Two tiny cohorts sharing 'phase transition' and 'spectrum' surfaces."""
    math = CohortInput(
        cohort_id="math",
        terms=["spectral gap", "markov chain", "phase transition", "spectrum", "martingale"],
        X_tf=np.array(
            [
                [2.0, 1.0, 3.0, 0.0, 1.0],
                [0.0, 2.0, 1.0, 4.0, 0.0],
                [1.0, 0.0, 0.0, 2.0, 3.0],
            ]
        ),
        researcher_ids=["m000000", "m000001", "m000002"],
        units=["LAB-MATH-A", "LAB-MATH-A", "LAB-MATH-B"],
    )
    physics = CohortInput(
        cohort_id="physics",
        terms=["phase transition", "laser", "spectrum", "ising model"],
        X_tf=np.array(
            [
                [1.0, 0.0, 2.0, 2.0],
                [0.0, 3.0, 1.0, 0.0],
                [2.0, 1.0, 0.0, 1.0],
                [0.0, 2.0, 3.0, 0.0],
            ]
        ),
        researcher_ids=["p000000", "p000001", "p000002", "p000003"],
        units=["LAB-PHYS-A", "LAB-PHYS-B", "LAB-PHYS-B", "LAB-PHYS-A"],
    )
    return [math, physics]


# ── 2. Bundle round-trip + merge ────────────────────────────────────────────


def main() -> None:
    cohorts = synthetic_cohorts()

    with tempfile.TemporaryDirectory() as tmp:
        bundles_dir = Path(tmp)

        # Each producer writes its own portable bundle …
        paths = []
        for cohort in cohorts:
            bundle = build_bundle(
                cohort,
                profile={"note": "synthetic example — the engine never reads this"},
                build_date="2026-07-12",
                producer="examples/merge_two_cohorts.py",
            )
            path = write_bundle(bundle, bundles_dir / f"{cohort.cohort_id}.zip")
            print(f"[bundle] wrote {path.name}: {bundle.meta['counters']}")
            paths.append(path)

        # … and the merge host reads them back (validation included).
        inputs = [read_bundle(p).to_cohort_input() for p in paths]

    # Sense vocabulary: fold each cohort's terms, then merge shared surfaces.
    senses = [
        build_cohort_senses(s.cohort_id, s.terms, s.X_tf)
        for s in sorted(inputs, key=lambda s: s.cohort_id)
    ]
    table = build_naive_table(senses)
    print(f"[reconcile] {table.stats['n_senses_pre']} senses -> {table.stats['n_groups']} groups")

    joint = assemble_joint_matrix(inputs, table)
    print(f"[joint] T shape: {joint.T.shape} ({len(joint.researcher_ids)} researchers)")

    embedding = balanced_svd(
        joint.T, joint.cohorts, joint.researcher_ids, n_components=3, per_cohort_cap=3
    )
    print(f"[svd] Z_ind shape: {embedding.Z_ind.shape}")
    print(f"[svd] Z_senses shape: {embedding.Z_senses.shape}")
    print(f"[svd] explained variance: {embedding.explained_variance:.3f}")
    print("\nDone — two bundles merged into one joint space, fully offline.")


if __name__ == "__main__":
    main()
