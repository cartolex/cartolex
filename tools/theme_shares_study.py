# SPDX-License-Identifier: MIT
"""Compare each person's theme shares from their keywords with the older shares by proximity.

Usage::

    python tools/theme_shares_study.py --baseline S          # a stored run: tests/baseline/S
    python tools/theme_shares_study.py --project FOLDER      # a project built at depth 2

Two readings of "how much of a person is in each theme" at the top level of a
two-level tree:

* **usage** (the theme tree's tables): the share of the person's keyword usage
  (TF) on the keywords counting toward each theme;
* **proximity** (``subfield_weights.csv``, written at depth 2 only): the cosine
  similarity of the person's SVD vector to each theme's seed keywords, clipped
  at 0 and normalised.

For every person with usage, the Spearman rank correlation between the two
vectors (over every theme; a theme a reading does not name counts 0), whether
the top theme is the same, and how many themes get a share in each reading
(medians). Prints the distribution. Nothing here changes a default.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _read(folder: Path, name: str) -> bytes:
    path = folder / name
    if path.exists():
        return path.read_bytes()
    with gzip.open(folder / f"{name}.gz", "rb") as fh:
        return fh.read()


def from_baseline(size: str) -> tuple[dict[str, dict[int, float]], dict[str, dict[int, float]]]:
    """(usage, proximity) theme shares per person of a stored run of the drift baseline."""
    from cartolex.lexicon.subfields import researcher_group_weights, term_to_group_maps

    run = ROOT / "tests" / "baseline" / size
    persons = json.loads(_read(run / "space", "persons.json"))
    terms = json.loads(_read(run / "space", "terms.json"))
    persons = persons["items"] if isinstance(persons, dict) else persons
    terms = terms["items"] if isinstance(terms, dict) else terms
    matrix = pd.read_csv(io.BytesIO(_read(run / "space", "matrix.csv")))
    applied = json.loads(_read(run / "apply", "applied.json"))
    to_concept, to_subfield, _, _ = term_to_group_maps(applied, terms)
    usage: dict[str, dict[int, float]] = {}
    for row, group in matrix.groupby("row"):
        scored = [(terms[c], tf) for c, tf in zip(group["col"], group["tf"], strict=True) if tf > 0]
        themes, _ = researcher_group_weights(
            scored, term_to_concept=to_concept, concept_to_subfield=to_subfield
        )
        usage[persons[int(row)]] = {w["id"]: w["weight"] for w in themes}
    weights = pd.read_csv(
        io.BytesIO(_read(run / "apply", "person_weights.csv")), dtype={"person": str}
    )
    proximity: dict[str, dict[int, float]] = {}
    for person, sid, w in zip(
        weights["person"], weights["subfield_id"], weights["weight"], strict=True
    ):
        proximity.setdefault(person, {})[int(sid)] = float(w)
    return usage, proximity


def from_project(folder: Path) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """(usage, proximity) theme shares per person of a project built at depth 2."""
    apply = folder / "derived" / "themes.apply"
    people = pd.read_parquet(apply / "theme_people.parquet")
    top = people[people["level"] == 1]
    usage: dict[str, dict[str, float]] = {}
    for rid, node, share in zip(top["researcher_id"], top["node"], top["share"], strict=True):
        usage.setdefault(rid, {})[node] = float(share)
    weights = pd.read_csv(apply / "subfield_weights.csv", dtype={"researcher_id": str})
    proximity: dict[str, dict[str, float]] = {}
    for rid, sid, w in zip(
        weights["researcher_id"], weights["subfield_id"], weights["weight"], strict=True
    ):
        proximity.setdefault(rid, {})[f"s{sid}"] = float(w)
    return usage, proximity


def compare(usage: dict, proximity: dict) -> dict[str, float]:
    """The distribution of the per-person rank correlations, and the top-theme agreement."""
    from scipy.stats import spearmanr

    themes = sorted({k for d in (*usage.values(), *proximity.values()) for k in d}, key=str)
    rhos, same, n_usage, n_proximity = [], [], [], []
    for person, mine in usage.items():
        if not mine:
            continue
        theirs = proximity.get(person, {})
        a = np.array([mine.get(t, 0.0) for t in themes])
        b = np.array([theirs.get(t, 0.0) for t in themes])
        n_usage.append(int((a > 0).sum()))
        n_proximity.append(int((b > 0).sum()))
        if a.std() > 0 and b.std() > 0:
            rhos.append(float(spearmanr(a, b).statistic))
        same.append(themes[int(np.argmax(a))] == themes[int(np.argmax(b))])
    q = np.quantile(rhos, [0.25, 0.5, 0.75]) if rhos else [float("nan")] * 3
    return {
        "people": len(same),
        "themes": len(themes),
        "rho_q1": round(float(q[0]), 2),
        "rho_median": round(float(q[1]), 2),
        "rho_q3": round(float(q[2]), 2),
        "same_top_theme": round(float(np.mean(same)), 2) if same else float("nan"),
        "themes_with_a_share_usage": float(np.median(n_usage)) if n_usage else float("nan"),
        "themes_with_a_share_proximity": float(np.median(n_proximity))
        if n_proximity
        else float("nan"),
    }


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--baseline", action="append", default=[], help="S, L")
    parser.add_argument("--project", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    rows = []
    for size in args.baseline:
        rows.append({"input": f"baseline {size}", **compare(*from_baseline(size))})
    for folder in args.project:
        rows.append({"input": folder.name, **compare(*from_project(folder))})
    if rows:
        keys = list(rows[0])
        print("| " + " | ".join(keys) + " |")
        print("|" + " --- |" * len(keys))
        for r in rows:
            print("| " + " | ".join(str(r[k]) for k in keys) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
