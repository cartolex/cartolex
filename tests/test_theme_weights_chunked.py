# SPDX-License-Identifier: MIT
"""The usage shares and keyword weights, never dense, equal the dense computation bit for bit."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from cartolex.lexicon.subfields import compute_lexicon_weights
from cartolex.lexicon.theme_tree import held_usage, keyword_weights, row_totals, usage_shares


def _random_usage(rng: np.random.Generator, n: int, m: int) -> sparse.csr_matrix:
    X = sparse.random(n, m, density=float(rng.uniform(0.01, 0.4)), random_state=rng, format="csr")
    X.data = rng.random(X.nnz) * rng.choice([1e-3, 1.0, 7.0, 1e3], X.nnz)
    return X


def _dense_shares(X, cols):
    """The shares of the whole matrix made dense, as the engine always computed them."""
    Xc = np.asarray(X.toarray() if sparse.issparse(X) else X, dtype=float)[:, cols]
    sums = Xc.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        shares = np.where(sums > 0, Xc / sums, 0.0)
    return shares, sums[:, 0] > 0


@pytest.mark.parametrize("seed", range(12))
def test_sparse_totals_and_chunked_keyword_weights_equal_the_dense_ones(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.choice([rng.integers(0, 300), rng.integers(8_000, 12_000)]))
    m = int(rng.integers(1, 400))
    X = _random_usage(rng, n, m) if n else sparse.csr_matrix((0, m))
    cols = np.sort(rng.choice(m, size=int(rng.integers(1, m + 1)), replace=False))
    shares, contributing = _dense_shares(X, cols)
    for matrix in (X, X.toarray()):
        H = held_usage(matrix, cols)
        totals = row_totals(H)
        assert int((totals > 0).sum()) == int(contributing.sum())
        assert np.array_equal(usage_shares(H, totals).toarray(), shares)
        for chunk_bytes in (8, 8 * max(1, n) * 3, 1 << 30):
            got = keyword_weights(H, totals, chunk_bytes=chunk_bytes)
            assert np.array_equal(got, shares.sum(axis=0))


def _dense_lexicon_weights(doc, X, terms):
    """The two-level lexicon weights as computed before they were chunked (the whole matrix dense)."""
    from cartolex.lexicon.subfields import _term_status_sets

    X = X.toarray() if sparse.issparse(X) else np.asarray(X, dtype=float)
    kept = {int(s["id"]) for s in doc["subfields"]}
    concepts = [c for c in doc.get("concepts", []) if int(c.get("subfield_id", -1)) in kept]
    curated = sorted({int(ti) for c in concepts for ti in c["term_indices"]})
    col_of = {ti: j for j, ti in enumerate(curated)}
    shares, contributing = _dense_shares(X, curated)
    term_w = shares.sum(axis=0)
    total = float(contributing.sum()) or 1.0
    rows, sf_direct = [], {}
    for c in concepts:
        sf_only, ride = _term_status_sets(c)
        c_weight = 0.0
        for ti in c["term_indices"]:
            w = 0.0 + float(term_w[col_of[ti]])
            t = terms[ti].strip().lower()
            if t in ride:
                pass
            elif t in sf_only:
                sid = int(c["subfield_id"])
                sf_direct[sid] = sf_direct.get(sid, 0.0) + w
            else:
                c_weight += w
            rows.append((ti, round(w, 6), round(w / total, 6)))
        c["weight"], c["share"] = round(c_weight, 6), round(c_weight / total, 6)
    by_sf = dict(sf_direct)
    for c in concepts:
        by_sf[int(c["subfield_id"])] = by_sf.get(int(c["subfield_id"]), 0.0) + float(c["weight"])
    for s in doc["subfields"]:
        w = by_sf.get(int(s["id"]), 0.0)
        s["weight"], s["share"] = round(w, 6), round(w / total, 6)
    return rows


@pytest.mark.parametrize("seed", range(8))
def test_lexicon_weights_equal_the_dense_computation(seed):
    rng = np.random.default_rng(100 + seed)
    n, m = int(rng.integers(1, 200)), int(rng.integers(4, 120))
    X = _random_usage(rng, n, m)
    terms = [f"term {i}" for i in range(m)]
    rows = rng.permutation(m)[: int(rng.integers(2, m))]
    parts = np.array_split(rows, int(rng.integers(1, min(8, len(rows)) + 1)))
    concepts = []
    for cid, part in enumerate(parts):
        if not len(part):
            continue
        idx = sorted(int(i) for i in part)
        concepts.append(
            {
                "id": cid,
                "subfield_id": cid % 3,
                "term_indices": idx,
                "subfield_only_terms": [terms[i] for i in idx if rng.random() < 0.2],
                "ride_along_terms": [terms[i] for i in idx if rng.random() < 0.1],
            }
        )
    doc = {"subfields": [{"id": k} for k in range(3)], "concepts": concepts}
    want_doc = {
        "subfields": [dict(s) for s in doc["subfields"]],
        "concepts": [dict(c) for c in concepts],
    }
    want_rows = _dense_lexicon_weights(want_doc, X, terms)
    for chunk_bytes in (8, 64, None):
        got_doc = {
            "subfields": [dict(s) for s in doc["subfields"]],
            "concepts": [dict(c) for c in concepts],
        }
        table = compute_lexicon_weights(got_doc, X, terms, chunk_bytes=chunk_bytes)
        assert got_doc == want_doc
        assert list(zip(table["term_index"], table["weight"], table["share"], strict=True)) == [
            (ti, w, s) for ti, w, s in want_rows
        ]
        assert isinstance(table, pd.DataFrame)
