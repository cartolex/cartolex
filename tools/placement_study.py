# SPDX-License-Identifier: MIT
"""Compare nearest-neighbour placement with UMAP's ``transform`` on a demo world.

Usage::

    python tools/placement_study.py --size S --seed 0 [--k 15] [--out .cache/placement]

Runs the engine on a generated demo world with the numeric reference's settings
(``tools/reference/run.py``) up to the map layout, then places four kinds of
points both ways and prints a Markdown table:

* **people, left out one at a time**: each mapped person placed from the others,
  compared with their fitted position (UMAP's own ``transform`` of the same vector
  is shown alongside);
* **keywords**: placed on the people's map (today: UMAP ``transform``);
* **projected people**: the demo's projected set, from their texts;
* **determinism**: the same placement in one chunk and in chunks of one row.

Distances are divided by the map's scale (the root mean square distance of the
people from their centre). ``neighbours`` is the share of a placed point's 10
nearest people on the map that are also among its 10 nearest people in the SVD
space. Nothing here changes a default; it is evidence for choosing one.
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

from cartolex.atlas.placement import place  # noqa: E402
from cartolex.demo import generate  # noqa: E402


def _scale(xy: np.ndarray) -> float:
    return float(np.sqrt(((xy - xy.mean(axis=0)) ** 2).sum(axis=1).mean()))


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--size", default="S")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--k", type=int, default=15)
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
    data, emb = engine._lexical()
    from cartolex.atlas.model_files import load_layout_model

    umap_model = load_layout_model(engine.files.layout_model_json)
    Z_ind, xy_ind = np.asarray(emb.Z_ind), np.asarray(emb.umap_ind)
    Z_terms, xy_terms_umap = np.asarray(emb.Z_terms), np.asarray(emb.umap_terms)
    scale = _scale(xy_ind)
    rows, timings = [], {}

    t0 = time.perf_counter()
    loo = place(Z_ind, Z_ind, xy_ind, k=args.k, exclude_self=True).xy
    timings["people_knn"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    self_umap = np.asarray(umap_model.transform(Z_ind))
    timings["people_umap"] = time.perf_counter() - t0
    rows.append(_row("people left out · nearest neighbours vs fitted", loo, xy_ind, scale))
    rows.append(_row("people · UMAP transform vs fitted", self_umap, xy_ind, scale))

    t0 = time.perf_counter()
    terms_knn = place(Z_terms, Z_ind, xy_ind, k=args.k).xy
    timings["terms_knn"] = time.perf_counter() - t0
    rows.append(
        _row("keywords · nearest neighbours vs UMAP transform", terms_knn, xy_terms_umap, scale)
    )

    proj = engine.projection()
    for key in [k for k in proj if k.endswith("_coords")]:
        name = key[: -len("_coords")]
        Z_new = np.asarray(proj[key].values)
        xy_umap = np.asarray(proj[f"{name}_xy"].values)
        t0 = time.perf_counter()
        xy_knn = place(Z_new, Z_ind, xy_ind, k=args.k).xy
        timings[f"{name}_knn"] = time.perf_counter() - t0
        rows.append(
            _row(
                f"projected ({name}) · nearest neighbours vs UMAP transform", xy_knn, xy_umap, scale
            )
        )
        rows[-1]["neighbours_knn"] = _neighbour_share(xy_knn, Z_new, xy_ind, Z_ind)
        rows[-1]["neighbours_umap"] = _neighbour_share(xy_umap, Z_new, xy_ind, Z_ind)

    rows[0]["neighbours_knn"] = _neighbour_share(loo, Z_ind, xy_ind, Z_ind)
    rows[1]["neighbours_umap"] = _neighbour_share(self_umap, Z_ind, xy_ind, Z_ind)
    rows[2]["neighbours_knn"] = _neighbour_share(terms_knn, Z_terms, xy_ind, Z_ind)
    rows[2]["neighbours_umap"] = _neighbour_share(xy_terms_umap, Z_terms, xy_ind, Z_ind)

    whole = place(Z_terms, Z_ind, xy_ind, k=args.k, chunk=1 << 20).xy
    one_by_one = place(Z_terms, Z_ind, xy_ind, k=args.k, chunk=1).xy
    determinism = float(np.abs(whole - one_by_one).max())

    report = {
        "world": {
            "size": args.size,
            "seed": args.seed,
            "people": int(len(Z_ind)),
            "keywords": int(len(Z_terms)),
        },
        "k": args.k,
        "scale": scale,
        "rows": rows,
        "timings_s": timings,
        "determinism_max_abs_diff": determinism,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"World {args.size}/{args.seed}: {len(Z_ind)} people, {len(Z_terms)} keywords; k = {args.k}"
    )
    print("| comparison | n | median | p90 | max | neighbours (kNN) | neighbours (UMAP) |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in rows:
        nk = r.get("neighbours_knn")
        nu = r.get("neighbours_umap")
        print(
            f"| {r['what']} | {r['n']} | {r['median']:.3f} | {r['p90']:.3f} | {r['max']:.3f} | "
            f"{'' if nk is None else f'{nk:.2f}'} | {'' if nu is None else f'{nu:.2f}'} |"
        )
    print(f"determinism (one chunk vs one row at a time): max |Δ| = {determinism:.1e}")
    print("timings (s): " + ", ".join(f"{k} {v:.3f}" for k, v in timings.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
