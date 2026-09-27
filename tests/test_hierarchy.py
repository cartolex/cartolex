# SPDX-License-Identifier: MIT
"""Tests for the deterministic keyword hierarchy (keywords→concepts→subfields).

Concepts ARE the term clusters of the clustering stage (passed as ``cluster_labels``);
subfields are a Ward cut over the concept centroids (the top level); labels are the dominant
keyword. Synthetic data.
"""

from __future__ import annotations

import numpy as np


def _blobs(n_per, dim, n_blobs, sharp, seed):
    rng = np.random.default_rng(seed)
    centers = np.eye(n_blobs, dim) * sharp
    Z = np.vstack([c + rng.normal(0, 0.25, (n_per, dim)) for c in centers])
    labels = np.repeat(range(n_blobs), n_per).astype(int)  # cluster per term
    return Z, labels


# ── group_subfields: forced maxclust cut of concept centroids ───────────────────

# Three well-separated PAIRS of concept centroids (within-pair close, pairs far apart).
# group_subfields honours ``target`` exactly (capped at the concept count) — no heuristic
# collapse, so the subfield count is a predictable, tunable granularity lever.
_THREE_PAIRS = np.array(
    [[10.0, 0, 0], [10.6, 0, 0], [0, 10.0, 0], [0, 10.6, 0], [0, 0, 10.0], [0, 0, 10.6]],
    dtype=float,
)


def test_group_subfields_returns_exactly_target() -> None:
    from cartolex.atlas.hierarchy import group_subfields

    # 12 generic concept centroids (no symmetry → distinct Ward heights): the count is
    # honoured exactly for every target below the concept count — the tunable lever.
    rng = np.random.default_rng(0)
    C = rng.normal(0, 1, (12, 8)) * 5.0
    for target in (2, 3, 5, 8, 11):
        labels = group_subfields(C, target=target)
        assert len(labels) == len(C)
        assert len(set(labels.tolist())) == target  # exactly the requested count
        assert set(labels.tolist()) == set(range(target))  # 0-based, dense


def test_group_subfields_respects_natural_pairs_at_target_3() -> None:
    from cartolex.atlas.hierarchy import group_subfields

    # the 3 well-separated pairs are the natural 3 groups at target=3
    labels = group_subfields(_THREE_PAIRS, target=3)
    assert len(set(labels.tolist())) == 3
    for a, b in [(0, 1), (2, 3), (4, 5)]:
        assert labels[a] == labels[b]  # pair members land together
    assert len({labels[0], labels[2], labels[4]}) == 3  # pairs in distinct subfields


def test_group_subfields_no_collapse_under_dominant_top_split() -> None:
    from cartolex.atlas.hierarchy import group_subfields

    # Regression: a strong top-level 2-way split (two far-apart super-blocks, each with two
    # sub-blocks) used to collapse the soft-cap heuristic to 2. The forced cut must honour 4.
    C = np.array(
        [
            [100.0, 0, 0],
            [100.0, 1, 0],
            [100.0, 0, 80],
            [100.0, 0, 81],
            [-100.0, 0, 0],
            [-100.0, 1, 0],
            [-100.0, 0, 80],
            [-100.0, 0, 81],
        ],
        dtype=float,
    )
    assert len(set(group_subfields(C, target=4).tolist())) == 4  # not collapsed to 2


def test_group_subfields_target_ge_n_is_one_each() -> None:
    from cartolex.atlas.hierarchy import group_subfields

    assert len(set(group_subfields(_THREE_PAIRS, target=6).tolist())) == 6  # target == n
    assert len(set(group_subfields(_THREE_PAIRS, target=10).tolist())) == 6  # target > n → n


def test_group_subfields_deterministic() -> None:
    from cartolex.atlas.hierarchy import group_subfields

    a = group_subfields(_THREE_PAIRS, target=4)
    b = group_subfields(_THREE_PAIRS, target=4)
    assert a.tolist() == b.tolist()


def test_concepts_are_the_clusters_full_coverage() -> None:
    from cartolex.atlas.hierarchy import build_hierarchy

    Z, labels = _blobs(20, 30, 4, 6.0, 1)
    terms = [f"t{i}" for i in range(len(Z))]
    h = build_hierarchy(Z, terms, cluster_labels=labels, target_subfields=4)
    assert len(h["concepts"]) == 4  # one concept per term cluster
    covered = sorted(i for c in h["concepts"] for i in c["term_indices"])
    assert covered == list(range(len(Z)))  # full coverage
    assert h["dropped_term_indices"] == []
    assert "fields" not in h  # fields level removed


def test_negative_cluster_is_dropped_as_noise() -> None:
    from cartolex.atlas.hierarchy import build_hierarchy

    Z, labels = _blobs(10, 20, 3, 6.0, 2)
    terms = [f"t{i}" for i in range(len(Z))]
    labels[0] = -1  # a noise term (agglomerative produces none, but be defensive)
    h = build_hierarchy(Z, terms, cluster_labels=labels, target_subfields=3)
    assert 0 in h["dropped_term_indices"]
    assert 0 not in {i for c in h["concepts"] for i in c["term_indices"]}


def test_concept_label_is_dominant_keyword() -> None:
    from cartolex.atlas.hierarchy import build_hierarchy

    Z, labels = _blobs(15, 20, 3, 7.0, 5)  # clusters: 0-14, 15-29, 30-44
    terms = [f"t{i}" for i in range(len(Z))]
    g = np.zeros(len(Z))
    g[20] = 100.0  # t20 is the dominant keyword of cluster 1
    h = build_hierarchy(Z, terms, g, cluster_labels=labels, target_subfields=3)
    owner = next(c for c in h["concepts"] if 20 in c["term_indices"])
    assert owner["label"] == "t20"


def test_subfields_cover_concepts() -> None:
    from cartolex.atlas.hierarchy import build_hierarchy

    Z, labels = _blobs(15, 40, 6, 6.0, 7)
    terms = [f"t{i}" for i in range(len(Z))]
    h = build_hierarchy(Z, terms, cluster_labels=labels, target_subfields=6)
    concept_ids = {c["id"] for c in h["concepts"]}
    seen = [cid for s in h["subfields"] for cid in s["concept_ids"]]
    # every concept lands in exactly one subfield (subfields are the top level now)
    assert sorted(seen) == sorted(concept_ids) and len(seen) == len(set(seen))
    assert "fields" not in h


def test_flags_exactly_one_general_node() -> None:
    from cartolex.atlas.hierarchy import build_hierarchy

    rng = np.random.default_rng(2)
    core = rng.normal(0, 1.0, (120, 10))  # one big diffuse generalist cluster
    spec, _ = _blobs(20, 10, 1, 12.0, 4)  # one tight specific cluster
    Z = np.vstack([core, spec])
    labels = np.array([0] * 120 + [1] * 20)
    # split the core into a few clusters so there are several concepts to group
    labels[:120] = np.repeat(range(2, 8), 20)
    terms = [f"t{i}" for i in range(len(Z))]
    h = build_hierarchy(Z, terms, cluster_labels=labels, target_subfields=4)
    flagged = [s for s in h["subfields"] if s.get("general")]
    assert len(flagged) == 1
    assert flagged[0]["generality"] == max(s["generality"] for s in h["subfields"])


def test_deterministic() -> None:
    from cartolex.atlas.hierarchy import build_hierarchy

    Z, labels = _blobs(12, 25, 4, 6.0, 11)
    terms = [f"t{i}" for i in range(len(Z))]
    a = build_hierarchy(Z, terms, cluster_labels=labels, target_subfields=4)
    b = build_hierarchy(Z, terms, cluster_labels=labels, target_subfields=4)
    assert [c["label"] for c in a["concepts"]] == [c["label"] for c in b["concepts"]]
    assert [s["label"] for s in a["subfields"]] == [s["label"] for s in b["subfields"]]
