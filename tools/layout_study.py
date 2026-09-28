# SPDX-License-Identifier: MIT
"""Compare the layout methods of a map on built projects: UMAP, t-SNE and the theme tree.

Usage::

    python tools/layout_study.py PROJECT [PROJECT …] [--methods umap tsne tree] --out FILE.jsonl

Each project must be built through ``themes.apply``. For each method the study
fits the people's map in a fresh process (its time and peak memory are that
process's), places the keywords by their nearest people (as every method does),
and measures, on fixed samples:

* **people**: the share of a person's 10 nearest people on the map that are
  also among their 10 nearest in the space (cosine);
* **keywords**: the same for a placed keyword and its 10 nearest people;
* **theme links**: for each pair of top-level themes, how many of the people's
  10 nearest neighbours cross from one to the other, in the space and on the
  map; the rank correlation of the two over the pairs (1: themes that touch in
  the space touch on the map);
* **borders**: the people measure restricted to people with a neighbour of
  another theme in the space;
* **stability**: a second edition of the same world is made from the first
  (2 % of the people gone, 2 % changed, 2 % new ones), its space refitted and
  its map drawn with the same seed; for the people in both editions, the share
  of their 10 nearest people on the first map still among their 10 nearest on
  the second, and the Procrustes disparity of their positions (0: the same map
  up to a rotation and a scale).

Nothing here changes a default; it is evidence for choosing one.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent
K = 10
SAMPLE = 3000
METHODS = ("umap", "tsne", "tree")


# ── the inputs of a map ──────────────────────────────────────────────────────


def _inputs(project: Path) -> dict:
    from cartolex.atlas.model_files import load_embeddings, load_lexical_data
    from cartolex.lexicon.theme_tree import read_tree

    models = project / "derived" / "themes.space" / "models"
    data = load_lexical_data(models / "lexical_data.json")
    emb = load_embeddings(models / "embeddings.json")
    terms = [str(t) for t in data.terms]
    tree = read_tree(project / "derived" / "themes.apply" / "themes_tree.json", terms)
    usage = data.X_tf if data.X_tf is not None else data.X
    return {
        "X": sparse.csr_matrix(data.X, dtype=np.float64),
        "usage": sparse.csr_matrix(usage, dtype=np.float64),
        "Z_ind": np.asarray(emb.Z_ind),
        "Z_terms": np.asarray(emb.Z_terms),
        "tree": tree,
        "dims": int(np.asarray(emb.Z_ind).shape[1]),
    }


def _edition(X: sparse.csr_matrix, usage: sparse.csr_matrix, seed: int = 11) -> dict:
    """A second edition: 2 % of people gone, 2 % changed, 2 % new (mixtures of two others)."""
    rng = np.random.default_rng(seed)
    n = X.shape[0]
    k = max(1, round(0.02 * n))
    gone = rng.choice(n, size=k, replace=False)
    rest = np.setdiff1d(np.arange(n), gone)
    changed = rng.choice(rest, size=k, replace=False)
    Xb, Ub = X.tolil(copy=True), usage.tolil(copy=True)
    for i in changed.tolist():
        for M in (Xb, Ub):
            row = M.rows[i]
            if row:
                scale = rng.uniform(0.5, 1.5, size=len(row))
                M.data[i] = [v * s for v, s in zip(M.data[i], scale, strict=True)]
    a, b = rng.choice(rest, size=k), rng.choice(rest, size=k)
    new_X = 0.7 * X[a] + 0.3 * X[b]
    new_U = 0.7 * usage[a] + 0.3 * usage[b]
    Xb = sparse.vstack([sparse.csr_matrix(Xb)[rest], new_X], format="csr")
    Ub = sparse.vstack([sparse.csr_matrix(Ub)[rest], new_U], format="csr")
    kept_rows = np.setdiff1d(rest, changed)  # people unchanged in both editions
    position_in_b = {int(p): i for i, p in enumerate(rest.tolist())}
    return {
        "X": Xb,
        "usage": Ub,
        "shared_a": kept_rows,
        "shared_b": np.array([position_in_b[int(p)] for p in kept_rows]),
    }


def _space(X: sparse.csr_matrix, dims: int) -> tuple[np.ndarray, np.ndarray]:
    """The engine's space: L2-normalised rows, TruncatedSVD with its seed."""
    from sklearn.decomposition import TruncatedSVD
    from sklearn.preprocessing import normalize
    from threadpoolctl import threadpool_limits

    svd = TruncatedSVD(n_components=min(dims, min(X.shape) - 1), random_state=42)
    with threadpool_limits(limits=1, user_api="blas"):
        Z = svd.fit_transform(normalize(X, norm="l2", axis=1))
    return Z, svd.components_.T * svd.singular_values_


# ── fitting, in a fresh process ──────────────────────────────────────────────


def fit(method: str, arrays: Path, out: Path, seed: int) -> dict:
    """Fit *method* on the arrays saved in *arrays*; write the positions to *out*."""
    from cartolex.atlas import reducers
    from cartolex.atlas.driver import DEFAULTS
    from cartolex.lexicon.theme_tree import EngineTree

    saved = np.load(arrays, allow_pickle=False)
    Z_ind, Z_terms = saved["Z_ind"], saved["Z_terms"]
    t0 = time.perf_counter()
    if method == "umap":
        people, words = reducers.fit_researcher_umap(
            Z_ind,
            Z_terms,
            n_neighbors=DEFAULTS.umap_n_neighbors,
            min_dist=DEFAULTS.umap_min_dist,
            n_components=2,
            metric=DEFAULTS.umap_metric,
            random_state=seed,
        )
    elif method == "tsne":
        people, words = reducers.fit_tsne_layout(Z_ind, Z_terms, random_state=seed)
    else:
        doc = json.loads(Path(str(arrays) + ".tree.json").read_text(encoding="utf-8"))
        tree = EngineTree.from_document(doc["tree"], doc["terms"])
        usage = sparse.csr_matrix(
            (saved["u_data"], saved["u_indices"], saved["u_indptr"]), shape=tuple(saved["u_shape"])
        )
        people, words = reducers.fit_tree_layout(Z_ind, Z_terms, tree=tree, usage=usage)
    seconds = time.perf_counter() - t0
    np.savez(out, people=people, words=words)
    return {
        "seconds": round(seconds, 2),
        "peak_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }


def _run_fit(method: str, Z_ind, Z_terms, usage, tree_doc, terms, seed: int, tmp: Path) -> dict:
    arrays = tmp / f"in-{method}.npz"
    np.savez(
        arrays,
        Z_ind=Z_ind,
        Z_terms=Z_terms,
        u_data=usage.data,
        u_indices=usage.indices,
        u_indptr=usage.indptr,
        u_shape=np.array(usage.shape),
    )
    Path(str(arrays) + ".tree.json").write_text(
        json.dumps({"tree": tree_doc, "terms": terms}), encoding="utf-8"
    )
    out = tmp / f"out-{method}.npz"
    proc = subprocess.run(
        [sys.executable, __file__, "fit", method, str(arrays), str(out), str(seed)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-3000:])
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    got = np.load(out)
    return {**info, "people": got["people"], "words": got["words"]}


# ── measures ─────────────────────────────────────────────────────────────────


def _knn_cos(Z: np.ndarray, rows: np.ndarray, k: int) -> np.ndarray:
    from sklearn.preprocessing import normalize

    Zn = normalize(Z)
    out = []
    for start in range(0, len(rows), 500):
        r = rows[start : start + 500]
        sims = Zn[r] @ Zn.T
        sims[np.arange(len(r)), r] = -np.inf
        out.append(np.argpartition(-sims, k, axis=1)[:, :k])
    return np.vstack(out)


def _knn_xy(xy: np.ndarray, points: np.ndarray, k: int, exclude: np.ndarray | None) -> np.ndarray:
    from sklearn.neighbors import NearestNeighbors

    nn = NearestNeighbors(n_neighbors=k + (1 if exclude is not None else 0)).fit(xy)
    idx = nn.kneighbors(points, return_distance=False)
    if exclude is None:
        return idx
    return np.array([[j for j in row if j != e][:k] for row, e in zip(idx, exclude, strict=True)])


def _overlap(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.array([len(set(x) & set(y)) / a.shape[1] for x, y in zip(a, b, strict=True)])


def _knn_cos_to(Q: np.ndarray, Z: np.ndarray, k: int) -> np.ndarray:
    from sklearn.preprocessing import normalize

    Qn, Zn = normalize(Q), normalize(Z)
    return np.vstack(
        [
            np.argpartition(-(Qn[s : s + 500] @ Zn.T), k, axis=1)[:, :k]
            for s in range(0, len(Qn), 500)
        ]
    )


def _procrustes(a: np.ndarray, b: np.ndarray) -> float:
    from scipy.spatial import procrustes

    return float(procrustes(a, b)[2])


def measure(
    Z_ind: np.ndarray,
    Z_terms: np.ndarray,
    people: np.ndarray,
    words: np.ndarray,
    top: np.ndarray,
    seed: int = 0,
) -> dict:
    rng = np.random.default_rng(seed)
    n = len(Z_ind)
    rows = np.sort(rng.choice(n, size=min(SAMPLE, n), replace=False))
    high = _knn_cos(Z_ind, rows, K)
    low = _knn_xy(people, people[rows], K, rows)
    people_score = _overlap(high, low)
    kw = np.sort(rng.choice(len(Z_terms), size=min(SAMPLE, len(Z_terms)), replace=False))
    kw_high = _knn_cos_to(Z_terms[kw], Z_ind, K)
    kw_low = _knn_xy(people, words[kw], K, None)
    kw_score = _overlap(kw_high, kw_low)
    # theme links: cross-theme neighbour counts, space against map
    themes = np.unique(top)
    index = {t: i for i, t in enumerate(themes.tolist())}
    t_rows = np.array([index[t] for t in top[rows]])

    def links(neigh: np.ndarray) -> np.ndarray:
        L = np.zeros((len(themes), len(themes)))
        t_neigh = np.vectorize(lambda j: index[top[j]])(neigh)
        for i in range(len(rows)):
            for tj in t_neigh[i]:
                if tj != t_rows[i]:
                    L[min(t_rows[i], tj), max(t_rows[i], tj)] += 1
        return L[np.triu_indices(len(themes), 1)]

    lh, ll = links(high), links(low)
    border = np.array([np.any(top[h] != top[r]) for h, r in zip(high, rows, strict=True)])
    return {
        "people_neighbours": round(float(people_score.mean()), 3),
        "keyword_neighbours": round(float(kw_score.mean()), 3),
        "theme_links": round(float(spearmanr(lh, ll).statistic), 3) if len(lh) > 2 else None,
        "border_neighbours": round(float(people_score[border].mean()), 3) if border.any() else None,
        "border_share": round(float(border.mean()), 3),
    }


def study(project: Path, methods: list[str], seed: int = 3) -> list[dict]:
    from cartolex.atlas.tree_layout import people_paths

    inp = _inputs(project)
    tree = inp["tree"]
    tree_doc = json.loads(
        (project / "derived" / "themes.apply" / "themes_tree.json").read_text(encoding="utf-8")
    )
    terms = list(tree.terms)
    top = people_paths(tree, inp["usage"], inp["Z_ind"])[:, 0]
    ed = _edition(inp["X"], inp["usage"])
    Zb, Zb_terms = _space(ed["X"], inp["dims"])
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for method in methods:
            a = _run_fit(
                method, inp["Z_ind"], inp["Z_terms"], inp["usage"], tree_doc, terms, seed, Path(tmp)
            )
            b = _run_fit(method, Zb, Zb_terms, ed["usage"], tree_doc, terms, seed, Path(tmp))
            row = {
                "project": project.name,
                "people": int(len(inp["Z_ind"])),
                "keywords": int(len(inp["Z_terms"])),
                "method": method,
                "seconds": a["seconds"],
                "peak_mb": a["peak_mb"],
                **measure(inp["Z_ind"], inp["Z_terms"], a["people"], a["words"], top),
            }
            sa, sb = ed["shared_a"], ed["shared_b"]
            rng = np.random.default_rng(1)
            pick = np.sort(rng.choice(len(sa), size=min(SAMPLE, len(sa)), replace=False))
            na = _knn_xy(a["people"][sa], a["people"][sa][pick], K, pick)
            nb = _knn_xy(b["people"][sb], b["people"][sb][pick], K, pick)
            row["stability_neighbours"] = round(float(_overlap(na, nb).mean()), 3)
            row["stability_disparity"] = round(_procrustes(a["people"][sa], b["people"][sb]), 4)
            row["keyword_disparity"] = round(_procrustes(a["words"], b["words"]), 4)
            print(json.dumps(row), flush=True)
            out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "fit":
        method, arrays, out, seed = argv[1:5]
        print(json.dumps(fit(method, Path(arrays), Path(out), int(seed))))
        return 0
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("projects", nargs="+", type=Path)
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    rows = [r for p in args.projects for r in study(p, args.methods)]
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
