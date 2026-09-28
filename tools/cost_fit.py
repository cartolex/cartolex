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


SIZES = ("people", "texts", "characters", "kept_keywords", "mapped_units")
#: Stages fitted with a second, linear size besides their driver.
EXTRA = {"keywords.extract": "texts"}


def _rows(files: list[str]) -> list[dict]:
    """The successful measures, a later file's measure of a (world, stage) replacing an earlier one."""
    rows: dict[tuple[str, str], dict] = {}
    for name in files:
        for line in Path(name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("exit") == 0 and row.get("seconds") is not None:
                    rows[(row["label"], row["stage"])] = row
    return list(rows.values())


def _sizes(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Each world's sizes, from the counts its stages recorded."""
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        mine = out.setdefault(r["label"], {})
        for name in SIZES:
            if name in r["counts"]:
                mine[name] = int(r["counts"][name])
    return out


def _estimate(stage: str, sizes: dict[str, int]) -> tuple[float, float]:
    from cartolex.build import STAGES
    from cartolex.build.params import ProjectSizes

    est = STAGES[stage].estimate(ProjectSizes(**sizes), None)
    return est.seconds or 0.0, est.peak_memory_mb or 0.0


def _fit_extra(x: np.ndarray, t: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """fixed, per driver unit and per extra unit (all ≥ 0, linear) minimising the log ratios."""

    def residual(p: np.ndarray) -> np.ndarray:
        return np.log(p[0] + p[1] * x + p[2] * t) - np.log(y)

    got = least_squares(
        residual,
        np.array([y.min() * 0.5, y.max() / x.max() / 2, y.max() / t.max() / 2]),
        bounds=([1e-6, 0.0, 0.0], [max(y.max(), 1e-3), np.inf, np.inf]),
    )
    return tuple(float(v) for v in got.x)  # type: ignore[return-value]


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
    from cartolex.build import STAGES

    rows = _rows(args.files)
    sizes = _sizes(rows)
    stages: dict[str, list[dict]] = {}
    for r in rows:
        stages.setdefault(r["stage"], []).append(r)
    worst: dict[str, float] = {}
    for stage in [s.id for s in STAGES if s.id in stages]:
        items = stages[stage]
        model = STAGES[stage].cost
        print(f"\n## {stage} (driver: {model.driver})")
        print("| world | driver | seconds | est. | ratio | peak MB | est. | ratio |")
        print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        pts = []
        for r in sorted(items, key=lambda r: sizes[r["label"]].get(model.driver, 0)):
            size = sizes[r["label"]]
            x = size.get(model.driver)
            if not x:
                continue
            s_est, m_est = _estimate(stage, size)
            rs, rm = s_est / max(r["seconds"], 1e-6), m_est / max(r["peak_mb"], 1e-6)
            worst[stage] = max(worst.get(stage, 1.0), rs, 1 / rs, rm, 1 / rm)
            pts.append((x, size.get(EXTRA.get(stage, model.driver), x), r["seconds"], r["peak_mb"]))
            print(
                f"| {r['label']} | {x:.3g} | {r['seconds']:.1f} | {s_est:.1f} | {rs:.2f} "
                f"| {r['peak_mb']:.0f} | {m_est:.0f} | {rm:.2f} |"
            )
        if args.fit and len(pts) >= 2:
            x = np.array([p[0] for p in pts], dtype=float)
            t = np.array([p[1] for p in pts], dtype=float)
            sec = np.array([max(p[2], 1e-3) for p in pts])
            mem = np.array([p[3] for p in pts])
            if stage in EXTRA:
                fs, fm = _fit_extra(x, t, sec), _fit_extra(x, t, mem)
                print(
                    f"fit: seconds = {fs[0]:.3g} + {fs[1]:.3g} × {model.driver} + {fs[2]:.3g} × "
                    f"{EXTRA[stage]}; memory MB = {fm[0]:.3g} + {fm[1]:.3g} × {model.driver} "
                    f"+ {fm[2]:.3g} × {EXTRA[stage]}"
                )
                ratios = [
                    ((fs[0] + fs[1] * a + fs[2] * b) / c, (fm[0] + fm[1] * a + fm[2] * b) / d)
                    for a, b, c, d in zip(x, t, sec, mem, strict=True)
                ]
            else:
                fs, fm = _fit(x, sec, (0.5, 2.0)), _fit(x, mem, (0.5, 2.0))
                print(
                    f"fit: seconds = {fs[0]:.3g} + {fs[1]:.3g} × {model.driver}^{fs[2]:.2f}; "
                    f"memory MB = {fm[0]:.3g} + {fm[1]:.3g} × {model.driver}^{fm[2]:.2f}"
                )
                ratios = [
                    ((fs[0] + fs[1] * a ** fs[2]) / c, (fm[0] + fm[1] * a ** fm[2]) / d)
                    for a, c, d in zip(x, sec, mem, strict=True)
                ]
            print("fitted ratios: " + ", ".join(f"{a:.2f}/{b:.2f}" for a, b in ratios))
    print(
        "\nworst ratio per stage (current models): "
        + json.dumps({k: round(v, 2) for k, v in worst.items()})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
