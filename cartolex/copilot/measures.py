# SPDX-License-Identifier: MIT
"""The measures of a theme tree the copilot reads before and after its changes.

Every measure is cartolex's own, on the bundled vectors:

- **fit** (:func:`cartolex.lexicon.theme_fit.borderline`, the theme editor's
  borderline list): for each placed keyword, the cosine to its own node's
  centroid (itself left out), to the nearest other node of the same level, and
  their difference, the *margin*. Per level: the mean cosine to the own node
  (*coherence*), the mean margin, the share of keywords nearer another node
  (*misplaced*) and of keywords within :data:`BORDER` of the border
  (*borderline*). A keyword alone in its node has no margin: it is left out of
  these, and *alone* says how many were;
- **sizes**: the nodes of each level and the keywords under them (smallest,
  median, largest), the keywords placed and set aside;
- **balance** (:func:`balance`): the keywords placed on the nodes of each level
  themselves, their spread (the coefficient of variation: 0 when every node
  holds as many) and the level's share of all placed keywords. A balanced tree
  has about as many keywords on each node of a level, and few on the top level
  (a keyword there is one the texts support no finer);
- **stability** (:func:`stability`): the grouping (:mod:`cartolex.atlas.clustering`,
  :mod:`cartolex.atlas.hierarchy`) redone on the space refitted without a
  share of the people (:func:`cartolex.atlas.reducers.compute_svd_embeddings`),
  compared with the grouping on everyone by the adjusted Rand index of the
  keywords' groups at each level, and each node's: the Jaccard index of its
  keywords with the closest group of its level in each sample;
- **truth** (:func:`truth_scores`), only where a truth exists (a demo world): the
  lexicon lab's B-cubed F1 of the top-level nodes against the true themes.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from .ops import levels as node_levels

__all__ = [
    "BORDER",
    "balance",
    "bcubed",
    "fit",
    "sizes",
    "stability",
    "summary",
    "truth_scores",
]

#: A keyword whose margin is below this sits on the border between two nodes.
BORDER = 0.05


def _top(doc: Mapping[str, Any]) -> dict[str, str]:
    parent = {n["id"]: n.get("parent") for n in doc["nodes"]}

    def top(nid: str) -> str:
        while parent.get(nid) is not None:
            nid = parent[nid]
        return nid

    return {k: top(n) for k, n in (doc.get("keywords") or {}).items() if n in parent}


def sizes(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Nodes per level and the keywords under them; keywords placed and set aside."""
    lv = node_levels(doc)
    parent = {n["id"]: n.get("parent") for n in doc["nodes"]}
    under: Counter[str] = Counter()
    for nid in (doc.get("keywords") or {}).values():
        while nid is not None:
            under[nid] += 1
            nid = parent.get(nid)
    out: dict[str, Any] = {
        "placed": len(doc.get("keywords") or {}),
        "set_aside": len(doc.get("set_aside") or {}),
        "levels": [],
    }
    for level in range(1, int(doc["depth"]) + 1):
        counts = [under[n] for n, v in lv.items() if v == level]
        out["levels"].append(
            {
                "level": level,
                "nodes": len(counts),
                "smallest": min(counts) if counts else 0,
                "median": float(statistics.median(counts)) if counts else 0.0,
                "largest": max(counts) if counts else 0,
                "empty": sum(1 for c in counts if c == 0),
            }
        )
    return out


def balance(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Per level: the keywords on its nodes themselves (mean, smallest, largest), their
    spread (coefficient of variation) and the level's share of all placed keywords."""
    lv = node_levels(doc)
    own: Counter[str] = Counter((doc.get("keywords") or {}).values())
    placed = max(1, len(doc.get("keywords") or {}))
    out = []
    for level in range(1, int(doc["depth"]) + 1):
        counts = [own[n] for n, v in lv.items() if v == level]
        mean = float(np.mean(counts)) if counts else 0.0
        cv = float(np.std(counts) / mean) if counts and mean else 0.0
        out.append(
            {
                "level": level,
                "own_mean": round(mean, 1),
                "own_smallest": min(counts) if counts else 0,
                "own_largest": max(counts) if counts else 0,
                "spread": round(cv, 3),
                "share": round(sum(counts) / placed, 4),
            }
        )
    return {"levels": out}


def fit(
    doc: Mapping[str, Any], terms: Sequence[str], Z: np.ndarray, *, border: float = BORDER
) -> dict[str, Any]:
    """Per level: coherence, mean margin, the shares of misplaced and borderline keywords, and
    how many keywords alone in their node were left out of them (``alone``)."""
    from cartolex.lexicon.theme_fit import alone, borderline

    out = []
    for level in range(1, int(doc["depth"]) + 1):
        items = borderline(doc, terms, Z, level=level)
        lone = len(alone(doc, terms, Z, level=level))
        if not items:
            out.append({"level": level, "keywords": 0, "alone": lone})
            continue
        own = np.array([b.own for b in items])
        margin = np.array([b.margin for b in items])
        out.append(
            {
                "level": level,
                "keywords": len(items),
                "coherence": round(float(own.mean()), 4),
                "margin": round(float(margin.mean()), 4),
                "misplaced": round(float((margin < 0).mean()), 4),
                "borderline": round(float((margin < border).mean()), 4),
                "alone": lone,
            }
        )
    return {"levels": out}


def bcubed(doc: Mapping[str, Any], theme_of: Mapping[str, str]) -> float:
    """B-cubed F1 of the top-level nodes against *theme_of* (keyword → true theme).

    Only keywords with a theme count; a keyword set aside is a group of its own.
    """
    place = _top(doc)
    aside = doc.get("set_aside") or {}
    items = [k for k in theme_of if k in place or k in aside]
    if not items:
        return 0.0
    group = {k: place.get(k, f"aside:{k}") for k in items}
    by_group: dict[str, list[str]] = defaultdict(list)
    by_theme: dict[str, list[str]] = defaultdict(list)
    for k in items:
        by_group[group[k]].append(k)
        by_theme[theme_of[k]].append(k)
    precision = recall = 0.0
    for k in items:
        both = sum(1 for j in by_group[group[k]] if theme_of[j] == theme_of[k])
        precision += both / len(by_group[group[k]])
        recall += both / len(by_theme[theme_of[k]])
    p, r = precision / len(items), recall / len(items)
    return round(2 * p * r / (p + r), 4) if p + r else 0.0


def truth_scores(doc: Mapping[str, Any], truth: Mapping[str, str] | None) -> dict[str, Any] | None:
    """The lab's score against a truth (keyword → theme), or ``None`` without one."""
    if not truth:
        return None
    return {"bcubed_f1": bcubed(doc, truth), "keywords_with_a_theme": len(truth)}


def summary(
    doc: Mapping[str, Any],
    terms: Sequence[str],
    Z: np.ndarray,
    *,
    truth: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Every measure of *doc* but stability (sizes, balance, fit, truth), as one JSON-ready
    dict."""
    out = {"sizes": sizes(doc), "balance": balance(doc), "fit": fit(doc, terms, Z)}
    scores = truth_scores(doc, truth)
    if scores is not None:
        out["truth"] = scores
    return out


def _ari(a: np.ndarray, b: np.ndarray) -> float:
    from sklearn.metrics import adjusted_rand_score

    return float(adjusted_rand_score(a, b))


def _labels(level_rows: Sequence[np.ndarray], n: int) -> np.ndarray:
    out = np.full(n, -1, dtype=int)
    for g, rows in enumerate(level_rows):
        out[np.asarray(rows, dtype=int)] = g
    return out


def group_levels(
    Z_terms: np.ndarray, level_sizes: Sequence[int], *, components: int = 50, ward: Any = None
) -> list[np.ndarray]:
    """Each keyword's group at every level (from the top), by the grouping of cartolex (with
    *ward*, the project's :class:`~cartolex.atlas.clustering.WardOptions`)."""
    from cartolex.atlas.clustering import fit_agglomerative_labels, prepare_cluster_embeddings
    from cartolex.atlas.hierarchy import level_groups

    Zn = prepare_cluster_embeddings(np.asarray(Z_terms, dtype=float), components)
    finest = fit_agglomerative_labels(Zn, n_clusters=int(level_sizes[-1]), ward=ward)
    groups = level_groups(Z_terms, finest, list(level_sizes), ward=ward)
    return [_labels(g.rows, len(Z_terms)) for g in groups]


def stability(
    X: Any,
    terms: Sequence[str],
    level_sizes: Sequence[int],
    *,
    dimensions: int,
    drop: float = 0.1,
    draws: int = 3,
    seed: int = 0,
    components: int = 50,
    ward: Any = None,
    doc: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """How much the grouping at *level_sizes* holds when a share *drop* of the people is left out.

    The space is refitted (cartolex's SVD) on everyone and on *draws* samples
    without a random *drop* of the people; the grouping is redone on each, and
    compared with everyone's by the adjusted Rand index of each level. Returns
    the mean and the lowest index per level (1: the same groups). With *doc* (a
    tree), also each of its nodes' stability (``nodes``, the lowest first): the
    Jaccard index of its keywords with the closest group of the same level in
    each sample (mean and lowest; 1: a group of the sample holds exactly them).
    """
    import tempfile
    from pathlib import Path

    import pandas as pd
    from scipy import sparse

    from cartolex.atlas.reducers import compute_svd_embeddings
    from cartolex.atlas.types import LexicalData

    X = sparse.csr_matrix(X)
    n_people = X.shape[0]
    rng = np.random.default_rng(seed)

    def grouping(rows: np.ndarray, folder: Path) -> list[np.ndarray]:
        data = LexicalData(
            X=X[rows],
            terms=list(terms),
            individuals=[str(i) for i in range(len(rows))],
            meta_ind=pd.DataFrame(index=range(len(rows))),
        )
        emb = compute_svd_embeddings(data, n_components=dimensions, model_path=folder / "svd.json")
        return group_levels(emb.Z_terms, level_sizes, components=components, ward=ward)

    members = _node_rows(doc, terms, len(level_sizes)) if doc is not None else {}
    per_node: dict[str, list[float]] = {nid: [] for nid in members}
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        full = grouping(np.arange(n_people), folder)
        per_level: list[list[float]] = [[] for _ in level_sizes]
        keep = max(2, int(round(n_people * (1.0 - drop))))
        for _ in range(int(draws)):
            rows = np.sort(rng.choice(n_people, size=keep, replace=False))
            again = grouping(rows, folder)
            for lv, (a, b) in enumerate(zip(full, again, strict=True)):
                per_level[lv].append(_ari(a, b))
            for nid, (lv, keyword_rows) in members.items():
                per_node[nid].append(_best_jaccard(keyword_rows, again[lv - 1]))
    out: dict[str, Any] = {
        "drop": drop,
        "draws": int(draws),
        "levels": [
            {
                "level": lv + 1,
                "groups": int(level_sizes[lv]),
                "ari_mean": round(float(np.mean(v)), 4),
                "ari_lowest": round(float(np.min(v)), 4),
            }
            for lv, v in enumerate(per_level)
        ],
    }
    if doc is not None:
        nodes = [
            {
                "node": nid,
                "level": members[nid][0],
                "keywords": int(len(members[nid][1])),
                "jaccard_mean": round(float(np.mean(v)), 4),
                "jaccard_lowest": round(float(np.min(v)), 4),
            }
            for nid, v in per_node.items()
            if v
        ]
        out["nodes"] = sorted(nodes, key=lambda x: (x["jaccard_mean"], x["node"]))
    return out


def _node_rows(
    doc: Mapping[str, Any], terms: Sequence[str], depth: int
) -> dict[str, tuple[int, np.ndarray]]:
    """Each node of *doc* down to level *depth*: its level and the rows of the keywords under it."""
    row = {t: i for i, t in enumerate(terms)}
    lv = node_levels(doc)
    parent = {n["id"]: n.get("parent") for n in doc["nodes"]}
    under: dict[str, list[int]] = defaultdict(list)
    for k, nid in (doc.get("keywords") or {}).items():
        if k not in row:
            continue
        while nid is not None:
            under[nid].append(row[k])
            nid = parent.get(nid)
    return {
        nid: (lv[nid], np.asarray(sorted(r), dtype=int))
        for nid, r in under.items()
        if nid in lv and lv[nid] <= depth
    }


def _best_jaccard(rows: np.ndarray, labels: np.ndarray) -> float:
    """The Jaccard index of the keywords *rows* with the group (of *labels*) closest to them."""
    sizes = Counter(int(x) for x in labels.tolist() if x >= 0)
    inside = Counter(int(x) for x in labels[rows].tolist() if x >= 0)
    best = 0.0
    for g, both in inside.items():
        best = max(best, both / (len(rows) + sizes[g] - both))
    return best
