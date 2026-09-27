# SPDX-License-Identifier: MIT
"""The deterministic subfield draft (replaces the LLM curation since 0.7.0).

Synthetic data: nine terms in three well-separated clusters, four researchers.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from cartolex.atlas.model_files import save_embeddings, save_lexical_data
from cartolex.atlas.types import Embeddings, LexicalData
from cartolex.context import RunContext
from cartolex.lexicon.config import KeywordsConfig
from cartolex.lexicon.subfields import draft_subfields
from cartolex.lexicon.subfields_edit import validate_doc

TERMS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota"]
CLUSTER = [0, 0, 0, 1, 1, 1, 2, 2, 2]


@pytest.fixture
def workspace(tmp_path):
    rng = np.random.default_rng(0)
    centers = np.eye(3) * 10.0
    Z_terms = np.vstack([centers[c] + rng.normal(0, 0.2, 3) for c in CLUSTER])
    Z_ind = np.vstack([centers[[0, 0, 1, 2]] + rng.normal(0, 0.2, (4, 3))])
    X = np.zeros((4, 9))
    for r, c in enumerate([0, 0, 1, 2]):
        X[r, [i for i, k in enumerate(CLUSTER) if k == c]] = [3, 2, 1]
    X[:, 0] += 5  # "alpha" dominates its cluster
    meta = pd.DataFrame({"researcher_id": [f"r{i}" for i in range(4)]})
    data = LexicalData(
        X=sparse.csr_matrix(X), terms=TERMS, individuals=list(meta.researcher_id), meta_ind=meta
    )
    emb = Embeddings(Z_ind=Z_ind, Z_terms=Z_terms, umap_ind=None, umap_terms=None)
    lex = tmp_path / "lexical_analysis"
    (lex / "models").mkdir(parents=True)
    save_lexical_data(data, lex / "models" / "lexical_data.json")
    save_embeddings(emb, lex / "models" / "embeddings.json")
    pd.DataFrame({"term": TERMS, "cluster": CLUSTER, "global_score": X.sum(axis=0)}).to_csv(
        lex / "umap_terms_clustered.csv", index=False
    )
    (lex / "clusters_terms.csv").write_text("cluster,n\n0,3\n1,3\n2,3\n", encoding="utf-8")
    auto = tmp_path / "automatic_data"
    auto.mkdir()
    pd.DataFrame({"concept": ["alpha", "delta"], "term_fr": ["alpha-fr", "delta-fr"]}).to_csv(
        auto / "keywords_global_refined_pairs.csv", index=False
    )
    return tmp_path


def _draft(ws, n_subfields: int = 2):
    ctx = RunContext.for_workspace(ws, KeywordsConfig(domain_title="Domain test"))
    draft_subfields(ctx, clustering_signature="sig-1", n_subfields=n_subfields)
    return json.loads(ctx.paths.subfields_draft_json.read_text(encoding="utf-8"))


def test_draft_is_a_valid_deterministic_hierarchy(workspace, monkeypatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    doc = _draft(workspace)
    assert doc["status"] == "draft" and doc["curation"] == "deterministic"
    assert doc["clustering_signature"] == "sig-1" and doc["n_dropped_terms"] == 0
    assert len(doc["concepts"]) == 3 and len(doc["subfields"]) == 2
    assert validate_doc(doc, n_terms=len(TERMS), terms_by_idx=TERMS) == []
    # Concepts are the term clusters, labelled by their dominant keyword.
    by_label = {c["label"]: c for c in doc["concepts"]}
    assert by_label["alpha"]["term_indices"] == [0, 1, 2]
    assert by_label["alpha"]["label_fr"] == "alpha-fr"  # pairs CSV side
    assert by_label["eta"]["label_fr"] == "eta"  # no French side → the label itself
    # Every node carries the persistent colour scheme.
    assert all(c["color"] for c in doc["concepts"]) and all(s["color"] for s in doc["subfields"])
    # Subfield labels are distinct, and the concepts' subfield_id references resolve.
    labels = [s["label"] for s in doc["subfields"]]
    assert len(set(labels)) == len(labels)
    ids = {s["id"] for s in doc["subfields"]}
    assert all(c["subfield_id"] in ids for c in doc["concepts"])
    assert sum(s["size"] for s in doc["subfields"]) == 4  # every researcher counted once


def test_draft_is_byte_identical_across_runs(workspace) -> None:
    out = workspace / "automatic_data" / "subfields_draft.json"
    _draft(workspace)
    first = out.read_bytes()
    _draft(workspace)
    assert out.read_bytes() == first


def test_draft_honours_the_subfield_count(workspace) -> None:
    doc = _draft(workspace, n_subfields=3)
    assert len(doc["subfields"]) == 3
    doc = _draft(workspace, n_subfields=1)
    assert len(doc["subfields"]) == 1


def test_draft_needs_no_network_and_no_key(workspace, monkeypatch) -> None:
    import socket

    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    def _no_network(*a, **k):  # pragma: no cover - only trips on a regression
        raise AssertionError("the draft must not open a socket")

    monkeypatch.setattr(socket, "create_connection", _no_network)
    assert os.environ.get("MISTRAL_API_KEY") is None
    assert _draft(workspace)["curation"] == "deterministic"
