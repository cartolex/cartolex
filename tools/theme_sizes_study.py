# SPDX-License-Identifier: MIT
"""Measures for the default theme sizes: what the comb leaves on each level, and how stable.

Usage (on a project built up to ``themes.group``, in the space it was built with)::

    python tools/theme_sizes_study.py FOLDER [--top 10,15,20] [--per-group 10,20,30,40]

For each ``top_groups`` × ``keywords_per_group`` (the rule that sets the level
sizes, ``cartolex.build.params.theme_level_sizes``, at the project's depth),
the grouping is cut and combed as ``themes.group`` does (the comb calibrated for
the project's space unit) and measured:

- the finest-level nodes with fewer than 5 keywords before the comb, and left
  with fewer than 5 after it (and the share of them);
- the share of the placed keywords above the finest level, and of all the
  keywords set aside as too broad;
- stability: the space refitted without a tenth of its units (the people, or
  the texts for a space of texts; two draws), each node's Jaccard index with
  the closest group of its level in the refit (uncombed groups, and the combed
  nodes' keywords), the median per level.

Only numbers are printed. Nothing here changes a default.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SMALL = 5


def _record(root: Path, stage: str) -> dict:
    return json.loads((root / "derived" / stage / "run.json").read_text(encoding="utf-8"))


def _value(record: dict, name: str):
    return (record.get("parameters") or {}).get(name, {}).get("value")


def _best_jaccard(rows: np.ndarray, labels: np.ndarray) -> float:
    sizes = Counter(int(x) for x in labels.tolist() if x >= 0)
    inside = Counter(int(x) for x in labels[rows].tolist() if x >= 0)
    return max((c / (len(rows) + sizes[g] - c) for g, c in inside.items()), default=0.0)


def main(argv=None) -> int:
    from cartolex.atlas.model_files import load_embeddings, load_lexical_data
    from cartolex.atlas.reducers import compute_svd_embeddings, compute_text_svd_embeddings
    from cartolex.atlas.types import LexicalData
    from cartolex.build.engine import comb_options
    from cartolex.build.params import theme_depth, theme_level_sizes
    from cartolex.copilot.measures import group_levels
    from cartolex.lexicon import theme_comb as tc

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--top", default="10,15,20")
    ap.add_argument("--per-group", default="10,20,30,40")
    ap.add_argument("--draws", type=int, default=2)
    args = ap.parse_args(argv)
    root = args.folder
    space, group = _record(root, "themes.space"), _record(root, "themes.group")
    unit = str(_value(space, "space_unit") or "person")
    models = root / "derived" / "themes.space" / "models"
    data = load_lexical_data(models / "lexical_data.json")
    Z = load_embeddings(models / "embeddings.json").Z_terms
    D = tc.load_text_keywords(root / "derived" / "themes.group" / "text_keywords.npz")
    kept = int(_record(root, "keywords.build")["measures"]["counts"]["kept_keywords"])
    units = int(_record(root, "corpus.assemble")["measures"]["counts"]["mapped_units"])
    depth = theme_depth(kept, units)
    params = {k: v["value"] for k, v in (group.get("parameters") or {}).items()}
    # the comb as the current defaults calibrate it for this space (the record may predate them)
    cal = tc.CALIBRATION[unit]
    opts = dataclasses.replace(
        comb_options(params), grid=cal.grid, one_level=cal.one_level, sideways=cal.sideways
    )
    components = int(params.get("cluster_dimensions") or 50)
    dims = Z.shape[1]
    X = data.X.tocsr()
    print(
        f"# {root.name}: space of {unit}s, {len(data.terms)} keywords, {D.shape[0]} texts, "
        f"{X.shape[0]} people, depth {depth}; comb {opts.sideways}, grid {opts.grid[0]}–"
        f"{opts.grid[-1]}, θ at depth 1 {opts.one_level}"
    )

    def refit(rows: np.ndarray | None, folder: Path) -> np.ndarray:
        if rows is None:
            return Z
        people = rows if unit == "person" else np.arange(X.shape[0])
        lex = LexicalData(
            X=X[people],
            terms=list(data.terms),
            individuals=[str(i) for i in range(len(people))],
            meta_ind=pd.DataFrame(index=range(len(people))),
        )
        if unit == "text":
            return compute_text_svd_embeddings(
                lex, D[rows], n_components=dims, model_path=folder / "svd.json"
            ).Z_terms
        return compute_svd_embeddings(
            lex, n_components=dims, model_path=folder / "svd.json"
        ).Z_terms

    n_units = X.shape[0] if unit == "person" else D.shape[0]
    rng = np.random.default_rng(0)
    with tempfile.TemporaryDirectory() as tmp:
        refits = [
            refit(np.sort(rng.choice(n_units, int(round(0.9 * n_units)), replace=False)), Path(tmp))
            for _ in range(args.draws)
        ]
    rows_out = []
    for top in (int(x) for x in args.top.split(",")):
        for per in (int(x) for x in args.per_group.split(",")):
            if depth == 1 and per != int(args.per_group.split(",")[0]):
                continue
            sizes = list(theme_level_sizes(kept, depth, top, per))
            labels = group_levels(Z, sizes, components=components, ward=None)
            finest = labels[-1]
            maps = [np.zeros(sizes[-1], dtype=np.int64) for _ in sizes]
            for lv in range(len(sizes)):
                for g in range(sizes[-1]):
                    rows = np.flatnonzero(finest == g)
                    maps[lv][g] = labels[lv][rows[0]] if len(rows) else -1
            P, n = tc.keyword_spread(D, finest, sizes[-1])
            c = tc.comb_with(P, n, finest, maps, opts)
            on_finest = Counter(int(x) for x in c.node[c.level == len(sizes)])
            small = sum(1 for g in range(sizes[-1]) if on_finest.get(g, 0) < SMALL)
            placed = c.level > 0
            above = float((placed & (c.level < len(sizes))).sum() / max(placed.sum(), 1))
            aside = float((c.level == 0).sum() / len(c.level))
            again = [group_levels(Zr, sizes, components=components, ward=None) for Zr in refits]
            out = {
                "top_groups": top,
                "keywords_per_group": per if depth > 1 else None,
                "sizes": "›".join(map(str, sizes)),
                "θ": c.theta,
                f"finest < {SMALL} before": int(
                    (np.bincount(finest, minlength=sizes[-1]) < SMALL).sum()
                ),
                f"finest < {SMALL}": small,
                f"share finest < {SMALL}": round(small / sizes[-1], 3),
                "above finest": round(above, 3),
                "set aside": round(aside, 3),
            }
            # each combed keyword's node on every level at or above its own
            up = np.full((len(sizes), len(c.level)), -1)
            for k in np.flatnonzero(c.level > 0):
                own = int(c.level[k]) - 1
                g = int(np.flatnonzero(maps[own] == c.node[k])[0])  # a finest node under it
                up[: own + 1, k] = [maps[lv][g] for lv in range(own + 1)]
            for lv in range(len(sizes)):
                groups = [np.flatnonzero(labels[lv] == g) for g in np.unique(labels[lv])]
                combed = [np.flatnonzero(up[lv] == g) for g in range(sizes[lv])]
                out[f"L{lv + 1} group J"] = round(
                    float(
                        np.median(
                            [np.mean([_best_jaccard(r, a[lv]) for a in again]) for r in groups]
                        )
                    ),
                    3,
                )
                out[f"L{lv + 1} node J"] = round(
                    float(
                        np.median(
                            [
                                np.mean([_best_jaccard(r, a[lv]) for a in again])
                                for r in combed
                                if len(r)
                            ]
                        )
                    ),
                    3,
                )
            rows_out.append(out)
            print(json.dumps(out, ensure_ascii=False))
    print(pd.DataFrame(rows_out).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
