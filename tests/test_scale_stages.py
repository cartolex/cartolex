# SPDX-License-Identifier: MIT
"""Every stage but the theme stages stays under a memory cap on 10⁵ people (``--heavy`` only).

A streamed world of 10⁵ mapped people (and 10⁴ projected ones) is written as a
project, each person leading one work, and the texts are read by their titles
only, so the keyword extraction takes minutes; the people × keywords matrix is
as large as a full world's. Each stage runs in a fresh process
(``cartolex build --only``), and the peak memory of its own process, measured by
the build itself (``own_memory_mb``: its worker processes are sized to the job's
budget, apart), must stay under :data:`CAP_MB`, which is below what one dense
people × keywords matrix would take: a stage that makes that matrix dense fails. The theme
stages run too (the map needs them) but are measured by their own test
(``test_scale_themes.py``). Run it under the machine's memory-capped runner.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The mapped people of the world.
PEOPLE = 100_000
#: The peak memory a stage's own process may reach, in MB (the build's measure).
CAP_MB = 3_000
#: The stages measured here, and the theme stages that run between them unmeasured.
MEASURED = (
    "corpus.assemble",
    "keywords.extract",
    "keywords.build",
    "themes.space",
    "map.layout",
    "map.trajectories",
    "overlays.position",
)
THEMES = ("themes.group", "themes.apply")


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]),
    }
    return subprocess.run(
        [sys.executable, "-m", "cartolex.cli", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.heavy
@pytest.mark.models("en", "fr")
def test_no_stage_makes_the_people_by_keywords_matrix_dense(tmp_path):
    from cartolex.demo.scale import write_scale_project

    root = tmp_path / "project"
    summary = write_scale_project(root, PEOPLE, seed=0, workers=4, led_works=(1, 1))
    assert summary.mapped == PEOPLE
    for setting in ("pinned_year=2026", 'corpus.assemble.parts=["title"]'):
        assert _cli("params", str(root), "--set", setting).returncode == 0
    peaks: dict[str, float] = {}
    order = [*MEASURED[:4], *THEMES, *MEASURED[4:]]
    for stage in order:
        out = _cli("build", str(root), "--only", stage, "--yes")
        assert out.returncode == 0, f"{stage}: {(out.stdout + out.stderr)[-2000:]}"
        record = json.loads((root / "derived" / stage / "run.json").read_text(encoding="utf-8"))
        measures = record["measures"]
        peaks[stage] = measures.get("own_memory_mb") or measures["peak_memory_mb"]
        # The measures, for the cost models (shown with pytest -s).
        print(json.dumps({"stage": stage, **record["measures"]}), flush=True)
        if stage == "keywords.build":
            kept = record["measures"]["counts"]["kept_keywords"]
            dense_mb = PEOPLE * kept * 8 / 2**20
            assert CAP_MB < dense_mb, (kept, dense_mb)  # the cap is below one dense matrix
    over = {s: peaks[s] for s in MEASURED if peaks[s] >= CAP_MB}
    assert not over, {"cap_mb": CAP_MB, "peaks": peaks}
