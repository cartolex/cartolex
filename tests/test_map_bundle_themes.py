# SPDX-License-Identifier: MIT
"""The bundle built sparse, and the theme tree it may carry (``map_bundle/3``)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from cartolex.atlas.map_bundle import (
    BUNDLE_SCHEMA,
    BUNDLE_SCHEMA_THEMES,
    THEMES_FORMAT,
    build_bundle,
    read_bundle,
    validate_bundle,
    validate_themes,
    write_bundle,
)
from cartolex.atlas.map_merge import CohortInput


def _dense_tables(cohort: CohortInput, S: np.ndarray | None):
    """The entity terms and vocabulary as the bundle computed them with the matrix made dense."""
    X = np.asarray(cohort.X_tf, dtype=float)
    rows = []
    for i, eid in enumerate(cohort.researcher_ids):
        for j, term in enumerate(cohort.terms):
            if X[i, j] == 0:
                continue
            score = float(S[i, j]) if S is not None else float("nan")
            rows.append({"entity_id": eid, "term": term, "tf": float(X[i, j]), "score": score})
    vocab = []
    for j, term in enumerate(cohort.terms):
        col = X[:, j]
        present = col > 0
        if S is not None and present.any():
            vals = S[present, j]
            total = float(np.nansum(vals)) if np.any(~np.isnan(vals)) else float("nan")
        else:
            total = float("nan")
        vocab.append(
            {
                "term": term,
                "n_entities": int(present.sum()),
                "tf_total": float(col.sum()),
                "score_total": total,
            }
        )
    return pd.DataFrame(rows, columns=["entity_id", "term", "tf", "score"]), pd.DataFrame(vocab)


def _cohort(rng: np.random.Generator, n: int, m: int) -> tuple[CohortInput, sparse.csr_matrix]:
    X = sparse.random(n, m, density=float(rng.uniform(0.01, 0.3)), random_state=rng, format="csr")
    X.data = np.ceil(rng.random(X.nnz) * 9)
    return (
        CohortInput(
            cohort_id="c",
            terms=[f"term {j}" for j in range(m)],
            X_tf=X,
            researcher_ids=[f"e{i}" for i in range(n)],
            units=[f"u{i % 4}" for i in range(n)],
        ),
        X,
    )


@pytest.mark.parametrize("seed", range(6))
def test_the_sparse_bundle_equals_the_dense_computation(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.choice([rng.integers(1, 60), rng.integers(9_000, 10_000)]))
    cohort, X = _cohort(rng, n, int(rng.integers(1, 40)))
    S = X.toarray() * rng.random(X.shape)
    S[rng.random(S.shape) < 0.05] = np.nan
    for scores in (None, S, sparse.csr_matrix(np.nan_to_num(S))):
        dense_scores = (
            None
            if scores is None
            else np.asarray(scores.toarray() if sparse.issparse(scores) else scores)
        )
        want_terms, want_vocab = _dense_tables(cohort, dense_scores)
        bundle = build_bundle(cohort, scores=scores)
        pd.testing.assert_frame_equal(
            bundle.terms_long.reset_index(drop=True), want_terms, check_dtype=False
        )
        pd.testing.assert_frame_equal(bundle.vocabulary, want_vocab, check_dtype=False)
        assert bundle.meta["schema"] == BUNDLE_SCHEMA


def _themes() -> tuple[dict, pd.DataFrame]:
    themes = {
        "format": THEMES_FORMAT,
        "depth": 2,
        "levels": [{"level": 1, "names": {"en": "Theme"}}, {"level": 2, "names": {"en": "Topic"}}],
        "nodes": [
            {
                "id": "s0",
                "parent": None,
                "level": 1,
                "order": 1,
                "names": {"en": "A"},
                "color": "#d22d3c",
            },
            {
                "id": "c0",
                "parent": "s0",
                "level": 2,
                "order": 1,
                "names": {"en": "a"},
                "color": "#d22d3c",
            },
            {
                "id": "c1",
                "parent": "s0",
                "level": 2,
                "order": 2,
                "names": {"en": "b"},
                "color": "#e38a93",
            },
        ],
    }
    weights = pd.DataFrame(
        {
            "entity_id": ["e0", "e0", "e0", "e1", "e1"],
            "level": [1, 2, 2, 1, 2],
            "node": ["s0", "c0", "c1", "s0", "c1"],
            "weight": [1.0, 0.25, 0.75, 0.5, 0.5],
            "share": [1.0, 0.25, 0.75, 1.0, 1.0],
        }
    )
    return themes, weights


def test_a_bundle_with_a_theme_tree_is_version_3_and_reads_back(tmp_path: Path):
    rng = np.random.default_rng(0)
    cohort, _ = _cohort(rng, 5, 7)
    themes, weights = _themes()
    bundle = build_bundle(cohort, themes=themes, theme_weights=weights)
    assert bundle.meta["schema"] == BUNDLE_SCHEMA_THEMES
    for dest in (tmp_path / "b", tmp_path / "b.zip"):
        back = read_bundle(write_bundle(bundle, dest))
        assert back.themes == themes
        pd.testing.assert_frame_equal(back.theme_weights, weights, check_dtype=False)
    once = (tmp_path / "b" / "theme_weights.csv").read_bytes()
    write_bundle(bundle, tmp_path / "again")
    assert (tmp_path / "again" / "theme_weights.csv").read_bytes() == once
    with pytest.raises(ValueError, match="together"):
        build_bundle(cohort, themes=themes)


def test_a_version_3_bundle_needs_its_tree_files(tmp_path: Path):
    rng = np.random.default_rng(1)
    cohort, _ = _cohort(rng, 5, 7)
    themes, weights = _themes()
    dest = write_bundle(build_bundle(cohort, themes=themes, theme_weights=weights), tmp_path / "b")
    (dest / "theme_weights.csv").unlink()
    with pytest.raises(ValueError, match="theme_weights.csv"):
        read_bundle(dest)
    plain = write_bundle(build_bundle(cohort), tmp_path / "plain")
    meta = json.loads((plain / "bundle_meta.json").read_text())
    meta["schema"] = BUNDLE_SCHEMA_THEMES
    (plain / "bundle_meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="missing required file"):
        read_bundle(plain)


def test_the_tree_and_its_weights_are_checked():
    themes, weights = _themes()
    validate_themes(themes, weights)
    bad = {**themes, "nodes": [*themes["nodes"], {**themes["nodes"][1], "id": "c9", "level": 1}]}
    with pytest.raises(ValueError, match="level"):
        validate_themes(bad)
    with pytest.raises(ValueError, match="unknown node"):
        validate_themes(themes, weights.assign(node=["s0", "c0", "c7", "s0", "c1"]))
    with pytest.raises(ValueError, match="another level"):
        validate_themes(themes, weights.assign(level=[1, 1, 2, 1, 2]))
    with pytest.raises(ValueError, match="share"):
        validate_themes(themes, weights.assign(share=[1.0, 0.25, 0.75, 1.5, 1.0]))
    with pytest.raises(ValueError, match="1 to 4"):
        validate_themes({**themes, "depth": 6})
    rng = np.random.default_rng(2)
    cohort, _ = _cohort(rng, 2, 3)
    bundle = build_bundle(cohort, themes=themes, theme_weights=weights)
    bundle.theme_weights = weights.assign(entity_id=["e0", "e0", "e0", "ghost", "ghost"])
    with pytest.raises(ValueError, match="absent from entities"):
        validate_bundle(bundle)
