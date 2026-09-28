# SPDX-License-Identifier: MIT
"""Compare the ways of placing points on a finished map, on a demo world.

Usage::

    python tools/placement_study.py --size S --seed 0 [--k 8] [--out .cache/placement]

Runs the engine on a generated demo world with the numeric reference's settings
(``tools/reference/run.py``) up to the map layout, then places the same points
three ways:

* **UMAP**: UMAP's own ``transform`` (the method the engine used before; the
  study fits a UMAP of its own on the people, with the layout's settings, and
  checks it gives the engine's map);
* **average**: the weighted mean of the ``k`` nearest people (a lab switch only:
  the engine never uses it);
* **heaviest group**: the engine's placement (:mod:`cartolex.atlas.placement`).

Points: the mapped people left out one at a time (compared with their fitted
positions), the keywords, and the demo's projected people. Distances are in
map radii (the root mean square distance of the people from their centre).
``neighbours`` is the share of a placed point's 10 nearest people on the map
that are also among its 10 nearest in the SVD space (higher is better);
``stranded`` the share of points farther than the link radius from every one of
their ``k`` nearest people on the map (in the empty space between places). Nothing
here changes a default; it is evidence for choosing one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "reference"))

import numpy as np  # noqa: E402
import run as ref  # noqa: E402

from cartolex.atlas.placement import LINK_RADIUS, K, map_radius, place  # noqa: E402
from cartolex.demo import generate  # noqa: E402


def _unit(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1
    return m / n


def _neighbour_share(
    xy: np.ndarray, z: np.ndarray, anchor_xy: np.ndarray, anchor_z: np.ndarray, *, m: int = 10
) -> float:
    """Mean share of each point's m nearest anchors on the map also nearest in SVD space."""
    m = min(m, len(anchor_xy) - 1)
    d_map = ((xy[:, None, :] - anchor_xy[None, :, :]) ** 2).sum(axis=2)
    d_svd = 1 - _unit(z) @ _unit(anchor_z).T
    near_map = np.argsort(d_map, axis=1, kind="stable")[:, :m]
    near_svd = np.argsort(d_svd, axis=1, kind="stable")[:, :m]
    return float(
        np.mean([len(set(a) & set(b)) / m for a, b in zip(near_map, near_svd, strict=True)])
    )


def _row(name: str, a: np.ndarray, b: np.ndarray, scale: float) -> dict:
    d = np.linalg.norm(a - b, axis=1) / scale
    return {
        "what": name,
        "n": int(len(d)),
        "median": float(np.median(d)),
        "p90": float(np.quantile(d, 0.9)),
        "max": float(d.max()),
    }


def _stranded(
    xy: np.ndarray, neighbours: np.ndarray, anchor_xy: np.ndarray, radius: float
) -> float:
    """Share of points farther than *radius* from every one of their neighbours on the map."""
    d = np.linalg.norm(anchor_xy[neighbours] - xy[:, None, :], axis=2)
    return float(np.mean(d.min(axis=1) > radius))


def _three_ways(z, anchor_z, anchor_xy, umap_xy, *, k, exclude_self=False):
    """UMAP's positions (given), the plain average and the heaviest group, for vectors *z*."""
    placed = place(z, anchor_z, anchor_xy, k=k, exclude_self=exclude_self)
    average = np.einsum("ik,ikj->ij", placed.neighbour_weights, anchor_xy[placed.neighbours])
    return {"UMAP": umap_xy, "average": average, "heaviest group": placed.xy}, placed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--size", default="S")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--k", type=int, default=K)
    parser.add_argument("--out", type=Path, default=ROOT / ".cache" / "placement")
    args = parser.parse_args(argv)

    out = args.out / f"{args.size}-{args.seed}"
    if out.exists():
        import shutil

        shutil.rmtree(out)
    world = generate(args.size, args.seed)
    world.write(out / "world")
    world.write_corpus(out / "workspace")
    (out / "cwd").mkdir()
    os.chdir(out / "cwd")  # some engine modules write into the current folder
    ref.block_network()
    judge = ref.make_judge(ref.load_truth(out / "world" / "truth.json"))
    engine = ref.Engine(out / "workspace", judge=judge, domain_id="demo")
    for stage in (
        "extract",
        "triage",
        "build",
        "roster",
        "space",
        "group",
        "layout",
        "draft",
        "apply",
    ):
        getattr(engine, stage)()
    _, emb = engine._lexical()
    Z_ind, xy_ind = np.asarray(emb.Z_ind), np.asarray(emb.umap_ind)
    Z_terms = np.asarray(emb.Z_terms)
    scale = map_radius(xy_ind)
    radius = LINK_RADIUS * scale
    umap = _umap_of_the_map(Z_ind, xy_ind)

    groups: dict[str, tuple] = {}
    t0 = time.perf_counter()
    ways, placed = _three_ways(
        Z_ind, Z_ind, xy_ind, np.asarray(umap.transform(Z_ind)), k=args.k, exclude_self=True
    )
    groups["people, left out one at a time"] = (ways, placed, Z_ind, xy_ind)
    ways, placed = _three_ways(
        Z_terms, Z_ind, xy_ind, np.asarray(umap.transform(Z_terms)), k=args.k
    )
    groups["keywords"] = (ways, placed, Z_terms, None)
    proj = engine.projection()
    for key in [k for k in proj if k.endswith("_coords")]:
        name = key[: -len("_coords")]
        Z_new = np.asarray(proj[key].values)
        ways, placed = _three_ways(
            Z_new, Z_ind, xy_ind, np.asarray(umap.transform(Z_new)), k=args.k
        )
        groups[f"projected ({name})"] = (ways, placed, Z_new, None)
    elapsed = time.perf_counter() - t0

    rows = []
    for what, (ways, placed, z, fitted) in groups.items():
        for method, xy in ways.items():
            row = {
                "points": what,
                "method": method,
                "n": int(len(xy)),
                "neighbours": _neighbour_share(xy, z, xy_ind, Z_ind),
                "stranded": _stranded(xy, placed.neighbours, xy_ind, radius),
            }
            if fitted is not None:
                row["shift_median"] = float(np.median(np.linalg.norm(xy - fitted, axis=1)) / scale)
            elif method != "UMAP":
                row["shift_median"] = float(
                    np.median(np.linalg.norm(xy - ways["UMAP"], axis=1)) / scale
                )
            rows.append(row)

    whole = place(Z_terms, Z_ind, xy_ind, k=args.k, chunk=1 << 20).xy
    one_by_one = place(Z_terms, Z_ind, xy_ind, k=args.k, chunk=1).xy
    determinism = float(np.abs(whole - one_by_one).max())
    t0 = time.perf_counter()
    place(Z_terms, Z_ind, xy_ind, k=args.k)
    placing = time.perf_counter() - t0

    report = {
        "world": {
            "size": args.size,
            "seed": args.seed,
            "people": int(len(Z_ind)),
            "keywords": int(len(Z_terms)),
        },
        "k": args.k,
        "link_radius": LINK_RADIUS,
        "map_radius": scale,
        "rows": rows,
        "seconds_all_three_ways": elapsed,
        "seconds_placing_the_keywords": placing,
        "determinism_max_abs_diff": determinism,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"World {args.size}/{args.seed}: {len(Z_ind)} people, {len(Z_terms)} keywords; "
        f"k = {args.k}, link radius {LINK_RADIUS} map radii"
    )
    print("| points | method | n | neighbours | stranded | median shift |")
    print("| --- | --- | ---: | ---: | ---: | ---: |")
    for r in rows:
        shift = r.get("shift_median")
        print(
            f"| {r['points']} | {r['method']} | {r['n']} | {r['neighbours']:.3f} | "
            f"{100 * r['stranded']:.1f} % | {'' if shift is None else f'{shift:.3f}'} |"
        )
    print(f"determinism (one chunk vs one row at a time): max |Δ| = {determinism:.1e}")
    print(f"placing the {len(Z_terms)} keywords: {placing:.3f} s")
    return 0


def _umap_of_the_map(Z_ind: np.ndarray, xy_ind: np.ndarray):
    """A UMAP fitted on the people as the layout stage does; checked to give the engine's map."""
    import umap
    from threadpoolctl import threadpool_limits

    from cartolex.atlas.driver import DEFAULTS

    params = {
        "n_neighbors": max(2, min(DEFAULTS.umap_n_neighbors, len(Z_ind) - 1)),
        "min_dist": DEFAULTS.umap_min_dist,
        "n_components": DEFAULTS.umap_n_components,
        "metric": DEFAULTS.umap_metric,
        "random_state": DEFAULTS.umap_random_state,
        "n_epochs": DEFAULTS.umap_n_epochs,
        "spread": DEFAULTS.umap_spread,
        "set_op_mix_ratio": DEFAULTS.umap_set_op_mix_ratio,
        "local_connectivity": DEFAULTS.umap_local_connectivity,
        "repulsion_strength": DEFAULTS.umap_repulsion_strength,
        "negative_sample_rate": DEFAULTS.umap_negative_sample_rate,
    }
    with threadpool_limits(limits=1):
        reducer = umap.UMAP(**params)
        fitted = reducer.fit_transform(Z_ind)
    if not np.array_equal(fitted, xy_ind):
        raise SystemExit("placement study: the UMAP fitted here does not give the engine's map")
    return reducer


if __name__ == "__main__":
    raise SystemExit(main())
