# SPDX-License-Identifier: MIT
"""Fit the stages' cost models to measured builds, and check the estimates against them.

Usage::

    python tools/cost_fit.py FILE.jsonl [FILE.jsonl …] [--fit]

Reads the measures of ``tools/scale_study.py build`` (one line per stage and
world) and prints, for each stage, the estimate of its cost model in
``cartolex.build.STAGES`` against each measure (time and peak memory, and their
ratio). With ``--fit``, also fits ``fixed + per_unit × driver^exponent`` to the
measures of each stage (least squares on the logarithm of the ratio, so every
world weighs the same) and prints the fitted values and their ratios.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parent.parent


def _rows(files: list[str]) -> list[dict]:
    rows = []
    for name in files:
        for line in Path(name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("exit") == 0 and row.get("seconds") is not None:
                    rows.append(row)
    return rows


def _driver(stage: str, counts: dict) -> float | None:
    from cartolex.build import STAGES

    model = STAGES[stage].cost
    value = counts.get(model.driver)
    return float(value) if value else None


def _estimate(stage: str, x: float) -> tuple[float, float]:
    from cartolex.build import STAGES
    from cartolex.build.params import ProjectSizes

    model = STAGES[stage].cost
    est = model.estimate(ProjectSizes(**{model.driver: int(x)}), None)
    return est.seconds or 0.0, est.peak_memory_mb or 0.0


def _fit(x: np.ndarray, y: np.ndarray, exponent: tuple[float, float]) -> tuple[float, float, float]:
    """fixed ≥ 0, per_unit ≥ 0 and an exponent in *exponent* minimising the log ratios."""

    def residual(p: np.ndarray) -> np.ndarray:
        fixed, log_unit, e = p
        est = fixed + math.exp(log_unit) * x**e
        return np.log(est) - np.log(y)

    best = None
    for e0 in np.linspace(exponent[0], exponent[1], 5):
        start = np.array(
            [max(y.min() * 0.5, 1e-3), math.log(max(y.max(), 1e-3) / x.max() ** e0), e0]
        )
        got = least_squares(
            residual,
            start,
            bounds=([0.0, -60.0, exponent[0]], [max(y.max(), 1e-3), 60.0, exponent[1]]),
        )
        if best is None or got.cost < best.cost:
            best = got
    fixed, log_unit, e = best.x
    return float(fixed), float(math.exp(log_unit)), float(e)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="+")
    parser.add_argument("--fit", action="store_true")
    args = parser.parse_args(argv)
    rows = _rows(args.files)
    stages: dict[str, list[dict]] = {}
    for r in rows:
        stages.setdefault(r["stage"], []).append(r)
    worst: dict[str, float] = {}
    for stage, items in stages.items():
        from cartolex.build import STAGES

        model = STAGES[stage].cost
        print(f"\n## {stage} (driver: {model.driver})")
        print("| world | driver | seconds | est. | ratio | peak MB | est. | ratio |")
        print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        pts = []
        for r in sorted(items, key=lambda r: _driver(stage, r["counts"]) or 0):
            x = _driver(stage, r["counts"])
            if x is None:
                continue
            s_est, m_est = _estimate(stage, x)
            rs, rm = s_est / max(r["seconds"], 1e-6), m_est / max(r["peak_mb"], 1e-6)
            worst[stage] = max(worst.get(stage, 1.0), rs, 1 / rs, rm, 1 / rm)
            pts.append((x, r["seconds"], r["peak_mb"]))
            print(
                f"| {r['label']} | {x:.3g} | {r['seconds']:.1f} | {s_est:.1f} | {rs:.2f} "
                f"| {r['peak_mb']:.0f} | {m_est:.0f} | {rm:.2f} |"
            )
        if args.fit and len(pts) >= 2:
            x = np.array([p[0] for p in pts])
            s = np.array([max(p[1], 1e-3) for p in pts])
            m = np.array([p[2] for p in pts])
            fs = _fit(x, s, (0.5, 2.0))
            fm = _fit(x, m, (0.5, 2.0))
            print(
                f"fit: seconds = {fs[0]:.3g} + {fs[1]:.3g} × {model.driver}^{fs[2]:.2f}; "
                f"memory MB = {fm[0]:.3g} + {fm[1]:.3g} × {model.driver}^{fm[2]:.2f}"
            )
            ratios = [
                ((fs[0] + fs[1] * xi ** fs[2]) / si, (fm[0] + fm[1] * xi ** fm[2]) / mi)
                for xi, si, mi in zip(x, s, m, strict=True)
            ]
            print("fitted ratios: " + ", ".join(f"{a:.2f}/{b:.2f}" for a, b in ratios))
    print(
        "\nworst ratio per stage (current models): "
        + json.dumps({k: round(v, 2) for k, v in worst.items()})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
