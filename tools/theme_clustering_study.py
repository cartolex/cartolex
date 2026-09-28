# SPDX-License-Identifier: MIT
"""Measure the keyword clustering at any size: exact Ward against micro-clusters then Ward.

Usage::

    python tools/theme_clustering_study.py threshold [--sizes 5000,10000,15000,20000]
    python tools/theme_clustering_study.py agreement [--sizes 1000,10000,20000] [--demo]
    python tools/theme_clustering_study.py scale [--sizes 1000,10000,100000]

Every measurement runs in a fresh process (``--one``), which reports its wall
time and its peak resident memory, so one measure never inherits another's
memory. Run the large ones through the machine's memory-capped runner.

* ``threshold``: time and peak memory of exact Ward (scipy) by number of keywords,
  to choose :data:`cartolex.atlas.clustering.EXACT_WARD_LIMIT`;
* ``agreement``: exact Ward against the two-stage cut on the same points, at
  the finest level's size (20 keywords per group): adjusted Rand index and the
  share of keywords whose group changes (after matching the groups one to one).
  Below the threshold the two-stage cut is forced with fewer micro-clusters
  (``n/2``, ``n/5``, ``n/10``); above it the engine's own rule applies.
  ``--demo`` adds the keywords of the stored demo worlds (the drift baseline's
  SVD keyword vectors, sizes S and L);
* ``scale``: the engine's path (exact below the threshold, two stages above)
  at each size: time and peak memory.

The keyword vectors are synthetic: unit vectors in 20 dimensions around topics
of unequal sizes, like L2-normalised SVD keyword vectors; the same seed gives
the same points. Nothing here changes a default; it is evidence for one.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KEYWORDS_PER_GROUP = 20
DIMENSIONS = 20


def synthetic_keywords(n: int, *, seed: int = 0, d: int = DIMENSIONS) -> np.ndarray:
    """*n* unit vectors in *d* dimensions around ``n / 20`` topics of unequal sizes."""
    rng = np.random.default_rng(seed)
    topics = max(2, n // KEYWORDS_PER_GROUP)
    centres = rng.normal(size=(topics, d))
    weights = rng.pareto(1.5, size=topics) + 1.0
    which = rng.choice(topics, size=n, p=weights / weights.sum())
    points = centres[which] + rng.normal(scale=0.8, size=(n, d))
    return points / np.linalg.norm(points, axis=1, keepdims=True)


def demo_keywords(size: str) -> np.ndarray:
    """The SVD keyword vectors of a stored demo world (the drift baseline), L2-normalised."""
    folder = ROOT / "tests" / "baseline" / size / "space"
    path = folder / "term_coords.npy.gz"
    if path.exists():
        with gzip.open(path, "rb") as fh:
            Z = np.load(io.BytesIO(fh.read()))
    else:
        Z = np.load(folder / "term_coords.npy")
    from cartolex.atlas.clustering import prepare_cluster_embeddings

    return prepare_cluster_embeddings(np.asarray(Z, dtype=float), 50)


def _points(spec: dict) -> np.ndarray:
    if spec.get("demo"):
        return demo_keywords(spec["demo"])
    return synthetic_keywords(int(spec["n"]), seed=int(spec.get("seed", 0)))


def run_one(spec: dict) -> dict:
    """One measure, in this process: cluster, save the labels, report time and peak memory."""
    from cartolex.atlas.clustering import EXACT_WARD_LIMIT, two_stage_ward_labels, ward_labels

    points = _points(spec)
    n = len(points)
    k = int(spec.get("k") or max(2, round(n / KEYWORDS_PER_GROUP)))
    t0 = time.perf_counter()
    if spec["method"] == "exact":
        from scipy.cluster.hierarchy import fcluster, linkage

        labels = fcluster(linkage(points, method="ward"), t=k, criterion="maxclust") - 1
    elif spec["method"] == "two-stage":
        labels = two_stage_ward_labels(points, k, limit=int(spec.get("limit", EXACT_WARD_LIMIT)))
    else:  # the engine's path
        labels = ward_labels(points, k)
    seconds = time.perf_counter() - t0
    if spec.get("labels"):
        np.save(spec["labels"], np.asarray(labels, dtype=np.int64))
    peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"n": n, "k": k, "seconds": round(seconds, 2), "peak_mb": round(peak_kb / 1024, 1)}


def measure(spec: dict) -> dict:
    """Run :func:`run_one` in a fresh process and return its report."""
    out = subprocess.run(
        [sys.executable, __file__, "--one", json.dumps(spec)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def changed_share(a: np.ndarray, b: np.ndarray) -> float:
    """The share of points whose group changes once the groups of *a* and *b* are matched one to one."""
    from scipy.optimize import linear_sum_assignment

    ia = np.unique(a, return_inverse=True)[1]
    ib = np.unique(b, return_inverse=True)[1]
    table = np.zeros((ia.max() + 1, ib.max() + 1), dtype=np.int64)
    np.add.at(table, (ia, ib), 1)
    rows, cols = linear_sum_assignment(-table)
    return 1.0 - table[rows, cols].sum() / len(a)


def agreement(spec: dict, limits: list[int]) -> list[dict]:
    """Exact Ward against the two-stage cut with each micro-cluster count of *limits*."""
    from sklearn.metrics import adjusted_rand_score

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        exact_path = str(Path(tmp) / "exact.npy")
        exact = measure({**spec, "method": "exact", "labels": exact_path})
        a = np.load(exact_path)
        for limit in limits:
            path = str(Path(tmp) / f"two-{limit}.npy")
            two = measure({**spec, "method": "two-stage", "limit": limit, "labels": path})
            b = np.load(path)
            rows.append(
                {
                    "points": spec.get("demo") or spec["n"],
                    "groups": exact["k"],
                    "micro_clusters": min(limit, exact["n"]),
                    "ari": round(float(adjusted_rand_score(a, b)), 3),
                    "changed": round(changed_share(a, b), 3),
                    "exact_s": exact["seconds"],
                    "exact_mb": exact["peak_mb"],
                    "two_stage_s": two["seconds"],
                    "two_stage_mb": two["peak_mb"],
                }
            )
    return rows


def _table(rows: list[dict]) -> str:
    if not rows:
        return ""
    keys = list(rows[0])
    lines = ["| " + " | ".join(keys) + " |", "|" + " --- |" * len(keys)]
    lines += ["| " + " | ".join(str(r[k]) for k in keys) + " |" for r in rows]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("what", nargs="?", choices=("threshold", "agreement", "scale"))
    parser.add_argument("--sizes", default="")
    parser.add_argument("--demo", action="store_true", help="also the stored demo worlds S and L")
    parser.add_argument("--one", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.one:
        print(json.dumps(run_one(json.loads(args.one))))
        return 0
    from cartolex.atlas.clustering import EXACT_WARD_LIMIT

    sizes = [int(s) for s in args.sizes.split(",") if s]
    rows: list[dict] = []
    if args.what == "threshold":
        for n in sizes or [5_000, 10_000, 15_000, 20_000]:
            rows.append(measure({"n": n, "method": "exact"}))
    elif args.what == "agreement":
        for n in sizes or [1_000, 10_000, 20_000]:
            limits = [EXACT_WARD_LIMIT] if n > EXACT_WARD_LIMIT else [n // 2, n // 5, n // 10]
            rows += agreement({"n": n}, limits)
        if args.demo:
            for size in ("S", "L"):
                n = len(demo_keywords(size))
                rows += agreement({"demo": size}, [n // 2, n // 5, n // 10])
    elif args.what == "scale":
        for n in sizes or [1_000, 10_000, 100_000]:
            rows.append({**measure({"n": n, "method": "engine"}), "limit": EXACT_WARD_LIMIT})
    else:
        parser.error("say what to measure: threshold, agreement or scale")
    print(_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
