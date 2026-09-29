# SPDX-License-Identifier: MIT
"""The copilot's pictures of a theme tree: the map of the people and the treemap (PNG).

The layouts are cartolex's (:mod:`cartolex.atlas.reducers`): the theme tree's
own map (``tree``: themes as discs, people inside their heaviest theme), a
t-SNE of the people with scikit-learn (``tsne``), or UMAP where it can be
imported (``umap``). The colours are the map's (:func:`cartolex.lexicon.theme_tree.node_colors`).
matplotlib draws, with its file-only backend.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["METHODS", "draw_map", "draw_treemap", "layout", "squarify"]

#: The layouts of the people's map.
METHODS = ("tree", "tsne", "umap")


def _name(names: Mapping[str, str], language: str, fallback: str) -> str:
    if names.get(language):
        return names[language]
    return next((v for v in names.values() if v), fallback)


def layout(
    method: str,
    tree: Any,
    usage: Any,
    Z_people: np.ndarray,
    Z_terms: np.ndarray,
) -> np.ndarray:
    """The people's map positions (``n × 2``) by *method* (:data:`METHODS`).

    *tree* is an :class:`cartolex.lexicon.theme_tree.EngineTree`, *usage* the
    people × keywords usage.
    """
    from cartolex.atlas import reducers

    if method == "tree":
        xy, _ = reducers.fit_tree_layout(Z_people, Z_terms, tree=tree, usage=usage)
    elif method == "tsne":
        xy, _ = reducers.fit_anchored_tsne(
            Z_people, Z_terms, None, n_neighbors=15, metric="cosine", random_state=0
        )
    elif method == "umap":
        if not reducers.umap_available():
            raise RuntimeError("UMAP cannot be imported here: use method='tree' or 'tsne'")
        xy, _ = reducers.fit_researcher_umap(
            Z_people,
            Z_terms,
            n_neighbors=15,
            min_dist=0.1,
            n_components=2,
            metric="cosine",
            random_state=0,
        )
    else:
        raise ValueError(f"unknown layout {method!r}; known: {', '.join(METHODS)}")
    return np.asarray(xy, dtype=float)


def _pyplot() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def draw_map(
    path: Path,
    xy: np.ndarray,
    tree: Any,
    paths: np.ndarray,
    *,
    language: str,
    title: str = "",
) -> Path:
    """Draw the people at *xy*, coloured by their top-level node (*paths* from
    :func:`cartolex.atlas.tree_layout.people_paths`), each top-level node named at
    the middle of its people."""
    from cartolex.lexicon.theme_tree import node_colors

    plt = _pyplot()
    colors = node_colors(tree)
    top = paths[:, 0]
    fig, ax = plt.subplots(figsize=(10, 8), dpi=100)
    ax.scatter(
        xy[:, 0],
        xy[:, 1],
        s=26,
        c=[colors[i] if i >= 0 else "#999999" for i in top],
        edgecolors="#333333",
        linewidths=0.3,
        zorder=3,
    )
    for i in sorted(set(top.tolist()) - {-1}):
        members = xy[top == i]
        cx, cy = np.median(members, axis=0)
        name = _name(tree.nodes[i].names, language, tree.nodes[i].id)
        ax.text(
            cx,
            cy,
            f"{name}\n[{tree.nodes[i].id}] {len(members)}",
            fontsize=8,
            ha="center",
            va="bottom",
            zorder=2,
            bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": colors[i], "alpha": 0.6},
        )
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title or "People, coloured by their top-level theme", fontsize=11)
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def squarify(values: Sequence[float], x: float, y: float, w: float, h: float) -> list[tuple]:
    """Rectangles ``(x, y, w, h)`` of areas proportional to *values* (sorted, largest
    first) filling the box, rows chosen to keep them near square."""
    values = [float(v) for v in values]
    total = sum(values)
    if not values or total <= 0:
        return []
    scale = w * h / total
    areas = [v * scale for v in values]
    out: list[tuple] = []

    def worst(row: list[float], side: float) -> float:
        s = sum(row)
        return max(max(side * side * r / (s * s), (s * s) / (side * side * r)) for r in row)

    while areas:
        side = min(w, h)
        row = [areas.pop(0)]
        while areas and worst(row + [areas[0]], side) <= worst(row, side):
            row.append(areas.pop(0))
        s = sum(row)
        if w >= h:  # a column on the left
            cw = s / h if h else 0
            cy = y
            for a in row:
                ch = a / cw if cw else 0
                out.append((x, cy, cw, ch))
                cy += ch
            x, w = x + cw, w - cw
        else:  # a row on top
            rh = s / w if w else 0
            cx = x
            for a in row:
                cw2 = a / rh if rh else 0
                out.append((cx, y, cw2, rh))
                cx += cw2
            y, h = y + rh, h - rh
    return out


def draw_treemap(
    path: Path,
    doc: Mapping[str, Any],
    tree: Any,
    weights: Mapping[str, float],
    *,
    language: str,
    title: str = "",
) -> Path:
    """The treemap of the top two levels: each node's area is the usage weight of the
    keywords under it (*weights*: keyword → weight)."""
    from cartolex.lexicon.theme_tree import node_colors

    plt = _pyplot()
    colors = node_colors(tree)
    index = {n.id: i for i, n in enumerate(tree.nodes)}
    parent = {n["id"]: n.get("parent") for n in doc["nodes"]}
    weight: dict[str, float] = dict.fromkeys(parent, 0.0)
    for k, nid in (doc.get("keywords") or {}).items():
        w = float(weights.get(k, 0.0)) or 1e-6
        while nid is not None:
            weight[nid] += w
            nid = parent.get(nid)
    tops = sorted((n for n in parent if parent[n] is None), key=lambda n: -weight[n])
    tops = [n for n in tops if weight[n] > 0]
    fig, ax = plt.subplots(figsize=(12, 8), dpi=100)
    for (x, y, w, h), nid in zip(
        squarify([weight[n] for n in tops], 0, 0, 12, 8), tops, strict=True
    ):
        kids = sorted(
            (c for c in parent if parent[c] == nid and weight[c] > 0), key=lambda c: -weight[c]
        )
        pad = 0.06
        inner = squarify([weight[c] for c in kids], x + pad, y + 0.3, w - 2 * pad, h - 0.3 - pad)
        ax.add_patch(plt.Rectangle((x, y), w, h, fc=colors[index[nid]], ec="white", lw=2))
        for (cx, cy, cw, ch), kid in zip(inner, kids, strict=True):
            ax.add_patch(plt.Rectangle((cx, cy), cw, ch, fc=colors[index[kid]], ec="white", lw=0.6))
            if cw > 0.9 and ch > 0.3:
                ax.text(
                    cx + 0.05,
                    cy + 0.05,
                    _name(tree.nodes[index[kid]].names, language, kid)[:28],
                    fontsize=6,
                    va="top",
                    ha="left",
                    clip_on=True,
                )
        ax.text(
            x + 0.05,
            y + 0.05 if not kids else y + 0.05,
            f"[{nid}] {_name(tree.nodes[index[nid]].names, language, nid)}"[:40],
            fontsize=8,
            fontweight="bold",
            va="top",
            ha="left",
            clip_on=True,
        )
    ax.set_xlim(0, 12)
    ax.set_ylim(8, 0)
    ax.set_axis_off()
    ax.set_title(
        title or "Themes by usage (area: how much the people use their keywords)", fontsize=11
    )
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path
