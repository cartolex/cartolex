# SPDX-License-Identifier: MIT
"""0.5.0 scale unlocks: sparse lexical path + parallel language split.

Probe-motivated (OpenAlex scale probe, 2026-07): dense build_lexical_matrix
costs 2x float64 full matrices; a CSR twin gave identical SVD ~3x faster in
~1/7 the memory. Per-paragraph language detection was the throughput wall and
langdetect was unseeded.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest
from scipy import sparse

from cartolex.atlas.io import build_lexical_matrix

IDX_COLS = ["last_name", "first_name", "unit"]
KW_COLS = ["last_name", "first_name", "unit", "term", "score", "score_tf"]


def _write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _row(last, term, score, tf):
    return {
        "last_name": last,
        "first_name": last[0],
        "unit": "u1" if last in ("Alpha", "Beta") else "u2",
        "term": term,
        "score": score,
        "score_tf": tf,
    }


@pytest.fixture()
def small_workspace(tmp_path: Path):
    """4 entities: one all-zero (dropped), one non-finite score (skipped)."""
    idx = [
        {"last_name": "Alpha", "first_name": "A", "unit": "u1"},
        {"last_name": "Beta", "first_name": "B", "unit": "u1"},
        {"last_name": "Gamma", "first_name": "G", "unit": "u2"},
        {"last_name": "Empty", "first_name": "E", "unit": "u2"},
    ]
    kw = [
        _row("Alpha", "Neuron", "2.0", "1.0"),
        _row("Alpha", "synapse", "1.0", "0.5"),
        _row("Beta", "neuron", "3.0", "1.5"),
        _row("Beta", "cortex", "inf", "1.0"),
        _row("Gamma", "cortex", "4.0", "2.0"),
    ]
    kw_csv = tmp_path / "kw.csv"
    idx_csv = tmp_path / "idx.csv"
    _write_csv(kw_csv, kw, KW_COLS)
    _write_csv(idx_csv, idx, IDX_COLS)
    return kw_csv, idx_csv


def test_build_lexical_matrix_returns_csr(small_workspace):
    kw_csv, idx_csv = small_workspace
    data = build_lexical_matrix(kw_researcher_csv=kw_csv, researcher_index_csv=idx_csv)
    assert sparse.issparse(data.X) and data.X.format == "csr"
    assert data.X_tf is not None
    assert sparse.issparse(data.X_tf) and data.X_tf.format == "csr"


def test_sparse_values_match_dense_semantics(small_workspace):
    kw_csv, idx_csv = small_workspace
    data = build_lexical_matrix(kw_researcher_csv=kw_csv, researcher_index_csv=idx_csv)
    # terms: sorted lowercased surfaces
    assert data.terms == ["cortex", "neuron", "synapse"]
    # Empty dropped (all-zero); 3 entities remain in roster order
    assert len(data.individuals) == 3
    X = np.asarray(data.X.todense())
    expect = np.array([[0.0, 2.0, 1.0], [0.0, 3.0, 0.0], [4.0, 0.0, 0.0]])
    np.testing.assert_allclose(X, expect)
    # non-finite score row skipped entirely (Beta/cortex), incl. its TF cell
    X_tf = np.asarray(data.X_tf.todense())
    expect_tf = np.array([[0.0, 1.0, 0.5], [0.0, 1.5, 0.0], [2.0, 0.0, 0.0]])
    np.testing.assert_allclose(X_tf, expect_tf)


def test_svd_and_clustering_run_on_sparse(small_workspace, tmp_path):
    from cartolex.atlas.clustering import cluster_terms
    from cartolex.atlas.reducers import compute_svd_embeddings

    kw_csv, idx_csv = small_workspace
    data = build_lexical_matrix(kw_researcher_csv=kw_csv, researcher_index_csv=idx_csv)
    emb = compute_svd_embeddings(data, n_components=2, model_path=tmp_path / "svd.json")
    # dense reference: same pipeline on densified X must give identical output
    dense = np.asarray(data.X.todense())
    from sklearn.decomposition import TruncatedSVD
    from sklearn.preprocessing import normalize as _norm

    ref = TruncatedSVD(n_components=2, random_state=42).fit_transform(_norm(dense, "l2", axis=1))
    np.testing.assert_allclose(np.abs(emb.Z_ind), np.abs(ref), rtol=1e-8, atol=1e-10)

    df = cluster_terms(
        data,
        emb,
        n_clusters=2,
        top_n_terms_per_cluster=3,
        clusters_terms_csv=tmp_path / "clusters.csv",
        n_components_cluster=2,
    )
    # global_score must be the 1-D per-term column sums even with CSR input
    got = df.sort_values("term")["global_score"].to_numpy(dtype=float)
    np.testing.assert_allclose(got, [4.0, 5.0, 1.0])


def test_lexicon_weights_accept_sparse_matrix():
    from cartolex.lexicon.subfields import compute_lexicon_weights

    doc = {
        "subfields": [{"id": 0, "label": "s", "concept_ids": [0]}],
        "concepts": [{"id": 0, "label": "c", "subfield_id": 0, "term_indices": [0]}],
    }
    W_dense = np.array([[1.0, 0.0], [0.0, 2.0]])
    import copy

    df_dense = compute_lexicon_weights(copy.deepcopy(doc), W_dense, ["cortex", "neuron"])
    df_sparse = compute_lexicon_weights(
        copy.deepcopy(doc), sparse.csr_matrix(W_dense), ["cortex", "neuron"]
    )
    assert df_dense.equals(df_sparse)


# ── parallel language split + determinism ──────────────────────────


def _make_corpus(tmp_path: Path, n: int = 8):
    corpus = tmp_path / "automatic_data" / "corpus_manual"
    corpus.mkdir(parents=True)
    rows = []
    for i in range(n):
        en = "We study active matter and stochastic thermodynamics in living systems. " * 3
        fr = (
            "Nous etudions la matiere active et la thermodynamique stochastique des systemes vivants. "
            * 3
        )
        text = en + "\n\n" + fr if i % 2 == 0 else fr + "\n\n" + en
        f = corpus / f"e{i}.txt"
        f.write_text(text, encoding="utf-8")
        rows.append({"last_name": f"E{i:02d}", "first_name": "X", "unit": "u", "txt_path": str(f)})
    idx_csv = tmp_path / "manual_index.csv"
    _write_csv(idx_csv, rows, ["last_name", "first_name", "unit", "txt_path"])
    return idx_csv


def test_langdetect_seeded_when_detecting():
    # clean interpreter: the test suite's conftest seeds langdetect itself,
    # so assert the PRODUCTION detection path does it, in a subprocess — and
    # that merely importing the module leaves the library's state alone.
    import subprocess
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from langdetect import DetectorFactory\n"
            "import cartolex.lexicon.lang_utils as lu\n"
            "print(DetectorFactory.seed)\n"
            "lu.detect_language_text('a sentence long enough to reach the statistical detector')\n"
            "print(DetectorFactory.seed)\n",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.stdout.split() == ["None", "0"], out.stdout + out.stderr


def test_parallel_split_matches_serial(tmp_path):
    from cartolex.lexicon.io_helpers import load_documents_split_by_language

    idx_csv = _make_corpus(tmp_path)
    kwargs = dict(indexes=[("manual", idx_csv, None)], corpus_languages=("fr", "en"))
    serial_docs, serial_meta = load_documents_split_by_language(n_jobs=1, **kwargs)
    par_docs, par_meta = load_documents_split_by_language(n_jobs=2, **kwargs)
    assert serial_docs == par_docs
    assert serial_meta.equals(par_meta)


def test_config_has_extraction_n_jobs():
    from cartolex.lexicon.config import KeywordsConfig

    cfg = KeywordsConfig()
    assert cfg.extraction_n_jobs == 1
