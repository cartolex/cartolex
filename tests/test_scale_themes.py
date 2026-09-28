# SPDX-License-Identifier: MIT
"""Clustering 10⁵ keyword vectors stays under a stated memory cap (``--heavy`` only).

Exact Ward on 10⁵ keywords would hold about 80 GB of pairwise distances; the
two-stage cut (micro-clusters, then a size-weighted Ward) must stay under
:data:`CAP_MB`. The clustering runs in a fresh process, whose peak resident
memory is measured. Run it under the machine's memory-capped runner.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The peak memory the clustering of 10⁵ keywords into 5 000 groups may reach, in MB.
CAP_MB = 3_000

_CODE = """
import json, resource, time
import numpy as np
from cartolex.atlas.clustering import fit_agglomerative_labels

n, d, topics = 100_000, 20, 5_000
rng = np.random.default_rng(0)
centres = rng.normal(size=(topics, d))
which = rng.integers(0, topics, size=n)
points = centres[which] + rng.normal(scale=0.8, size=(n, d))
points /= np.linalg.norm(points, axis=1, keepdims=True)
t0 = time.perf_counter()
labels = fit_agglomerative_labels(points, n_clusters=5_000)
print(json.dumps({
    "seconds": time.perf_counter() - t0,
    "groups": int(len(np.unique(labels))),
    "peak_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
}))
"""


@pytest.mark.heavy
def test_clustering_a_hundred_thousand_keywords_stays_under_the_cap():
    out = subprocess.run(
        [sys.executable, "-c", _CODE], cwd=ROOT, check=True, capture_output=True, text=True
    )
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report["groups"] == 5_000
    assert report["peak_mb"] < CAP_MB, report
