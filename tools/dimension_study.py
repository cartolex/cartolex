# SPDX-License-Identifier: MIT
"""How many dimensions the space needs: explained variance and neighbours against the dimension.

Usage::

    python tools/dimension_study.py PROJECT [PROJECT …] --dims 10 20 50 100 200 400 --out FILE.jsonl

For each project (built through ``themes.space``), reads the people × keywords
matrix the space is fitted on, fits the space at each dimension ``d`` (the
engine's own call: L2-normalised rows, ``TruncatedSVD`` with its seed) and
records:

* the share of the variance the ``d`` dimensions keep;
* **people's neighbours**: for a fixed sample of people, the share of their 10
  nearest people by cosine in the full keyword space that are also among their
  10 nearest in the ``d``-dimensional space;
* **keywords' neighbours**: the same for a sample of keywords, each described by
  the people who use it (its column of the normalised matrix) in the full
  space and by its keyword vector (``Vᵀ·S``) in the space;
* the time of the fit.

Nothing here changes a default; it is evidence for the dimension rule.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from scipy import sparse
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize
from threadpoolctl import threadpool_limits

K = 10
SAMPLE = 2000


def _top(scores: np.ndarray, own: np.ndarray, k: int) -> np.ndarray:
    """The k best columns of each row, leaving out each row's own column."""
    scores = scores.copy()
    scores[np.arange(len(own)), own] = -np.inf
    part = np.argpartition(-scores, k, axis=1)[:, :k]
    return part


def _neighbours_full(M: sparse.csr_matrix, sample: np.ndarray, k: int) -> list[set[int]]:
    """Cosine neighbours of the sampled rows of *M* (rows already L2-normalised), by blocks."""
    out: list[set[int]] = []
    MT = M.T.tocsr()
    for start in range(0, len(sample), 200):
        rows = sample[start : start + 200]
        sims = (M[rows] @ MT).toarray()
        out.extend(set(r.tolist()) for r in _top(sims, rows, k))
    return out


def _neighbours_dense(Z: np.ndarray, sample: np.ndarray, k: int) -> list[set[int]]:
    Zn = normalize(Z)
    out: list[set[int]] = []
    for start in range(0, len(sample), 200):
        rows = sample[start : start + 200]
        sims = Zn[rows] @ Zn.T
        out.extend(set(r.tolist()) for r in _top(sims, rows, k))
    return out


def _overlap(a: list[set[int]], b: list[set[int]]) -> float:
    return float(np.mean([len(x & y) / K for x, y in zip(a, b, strict=True)]))


def study(project: Path, dims: list[int], seed: int = 0) -> list[dict]:
    from cartolex.atlas.model_files import load_lexical_data

    data = load_lexical_data(project / "derived" / "themes.space" / "models" / "lexical_data.json")
    X = normalize(sparse.csr_matrix(data.X, dtype=np.float64), norm="l2", axis=1)
    n, m = X.shape
    rng = np.random.default_rng(seed)
    people = np.sort(rng.choice(n, size=min(SAMPLE, n), replace=False))
    keywords = np.sort(rng.choice(m, size=min(SAMPLE, m), replace=False))
    t0 = time.perf_counter()
    full_people = _neighbours_full(X, people, K)
    columns = normalize(X.T.tocsr(), norm="l2", axis=1)
    full_keywords = _neighbours_full(columns, keywords, K)
    reference_s = time.perf_counter() - t0
    rows = []
    for d in dims:
        eff = min(d, n - 1, m - 1)
        svd = TruncatedSVD(n_components=eff, random_state=42)
        t0 = time.perf_counter()
        with threadpool_limits(limits=1, user_api="blas"):
            Z = svd.fit_transform(X)
        fit_s = time.perf_counter() - t0
        Z_terms = svd.components_.T * svd.singular_values_
        rows.append(
            {
                "project": project.name,
                "people": n,
                "keywords": m,
                "nnz_per_person": round(X.nnz / n, 1),
                "dims": eff,
                "explained_variance": round(float(svd.explained_variance_ratio_.sum()), 4),
                "people_neighbours": round(
                    _overlap(full_people, _neighbours_dense(Z, people, K)), 4
                ),
                "keyword_neighbours": round(
                    _overlap(full_keywords, _neighbours_dense(Z_terms, keywords, K)), 4
                ),
                "fit_seconds": round(fit_s, 2),
                "reference_seconds": round(reference_s, 1),
            }
        )
        print(json.dumps(rows[-1]), flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("projects", nargs="+", type=Path)
    parser.add_argument("--dims", nargs="+", type=int, default=[10, 20, 50, 100, 200, 400])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    rows = [row for p in args.projects for row in study(p, args.dims)]
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
