# SPDX-License-Identifier: MIT
"""How well each keyword fits its node of a theme tree, in the keywords' space.

Two readings of one measure, for the theme editor:

- **borderline keywords** (:func:`borderline`): for each placed keyword, how
  much closer it is to its own node than to the nearest other node of the same
  level. The *margin* is ``cos(k, own) − cos(k, other)``, where ``own`` is the
  centroid of the other keywords of its node (the keyword itself left out) and
  ``other`` the centroid of the best other node. A small margin sits on the
  border; a negative one is closer to another node than to its own.
- **suggested places** (:func:`suggestions`): for a keyword set aside (or one
  to check), the nodes whose centroids are nearest, with their cosine.

*Why this measure.* The grouping cuts the same space with Ward's method on the
L2-normalised keyword vectors and their normalised centroids
(:mod:`cartolex.atlas.hierarchy`), so a cosine to a normalised centroid is the
closeness the grouping itself used: the margin says how near a keyword came to
being grouped elsewhere. Leaving the keyword out of its own centroid removes
the pull it has on it (strong for small nodes, a two-keyword node would
otherwise always look tight). Comparing nodes of one level keeps a node from
competing with its own parent. A silhouette over every pair of keywords would
say much the same at a cost that grows with the square of the vocabulary; this
costs one product of the keywords by the nodes of a level.

A node's centroid is the mean of the normalised vectors of the keywords it
holds, on it or under it, renormalised. Keywords outside the space (not in its
vocabulary, or with a zero vector) are ignored. A keyword alone in its node (at
the level compared) has no margin: without it, its node has no centroid. It is
left out of the list; :func:`alone` counts these keywords, so a measure over the
list can say how many it leaves out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = ["Borderline", "Suggestion", "alone", "borderline", "suggestions"]


@dataclass(frozen=True)
class Borderline:
    """A placed keyword and how firmly it belongs to its node."""

    keyword: str
    node: str  # its node at the level compared
    level: int
    other: str  # the nearest other node of that level
    own: float  # cosine to its node's centroid, itself left out
    near: float  # cosine to the other node's centroid
    margin: float  # own − near


@dataclass(frozen=True)
class Suggestion:
    """A node to put a keyword in, and its cosine to the node's centroid."""

    node: str
    score: float


def _normalised(Z: np.ndarray) -> np.ndarray:
    Z = np.asarray(Z, dtype=np.float64)
    norms = np.linalg.norm(Z, axis=1, keepdims=True)
    return np.divide(Z, norms, out=np.zeros_like(Z), where=norms > 0)


class _Tree:
    """The nodes of a ``cartolex-themes/1`` document, their levels and the keywords' rows."""

    def __init__(
        self, doc: Mapping[str, Any], terms: Sequence[str], Zn: np.ndarray | None = None
    ) -> None:
        nodes = list(doc.get("nodes") or [])
        self.parent = {n["id"]: n.get("parent") for n in nodes}
        self.order = [n["id"] for n in nodes]
        self.level: dict[str, int] = {}
        for nid in self.order:
            self._level(nid)
        self.Zn = Zn
        row = {t: i for i, t in enumerate(terms)}
        if Zn is not None:  # a zero vector is outside the space
            present = np.flatnonzero(np.any(Zn != 0, axis=1))
            row = {terms[i]: int(i) for i in present}
        self.placed = {
            k: v for k, v in (doc.get("keywords") or {}).items() if k in row and v in self.parent
        }
        self.row = row

    def _level(self, nid: str) -> int:
        if nid not in self.level:
            up = self.parent.get(nid)
            self.level[nid] = 1 if up is None or up not in self.parent else self._level(up) + 1
        return self.level[nid]

    def ancestor(self, nid: str, at: int) -> str | None:
        """*nid*'s node at level *at* (itself, or above it); ``None`` when *nid* is above *at*."""
        if self.level[nid] < at:
            return None
        while self.level[nid] > at:
            nid = self.parent[nid]
        return nid

    def sums(self, Zn: np.ndarray, at: int | None) -> tuple[list[str], np.ndarray, np.ndarray]:
        """The nodes of level *at* (every node when ``None``, own keywords only), their vector
        sums and member counts."""
        ids = [n for n in self.order if at is None or self.level[n] == at]
        pos = {n: j for j, n in enumerate(ids)}
        total = np.zeros((len(ids), Zn.shape[1]))
        count = np.zeros(len(ids))
        for k, nid in self.placed.items():
            node = nid if at is None else self.ancestor(nid, at)
            if node is None or node not in pos:
                continue
            total[pos[node]] += Zn[self.row[k]]
            count[pos[node]] += 1
        return ids, total, count


def _compared(
    tree: _Tree, level: int | None
) -> list[tuple[int, list[str], np.ndarray, np.ndarray, list[tuple[str, str]]]]:
    """Per level compared: its nodes, their sums and counts, and the keywords compared there
    with their node at that level."""
    levels = sorted({tree.level[n] for n in tree.placed.values()}) if level is None else [level]
    out = []
    for lv in levels:
        assert tree.Zn is not None
        ids, total, count = tree.sums(tree.Zn, lv)
        keywords = [
            (k, tree.ancestor(n, lv))
            for k, n in tree.placed.items()
            if (tree.level[n] == lv if level is None else tree.ancestor(n, lv) is not None)
        ]
        out.append((lv, ids, total, count, [(k, n) for k, n in keywords if n is not None]))
    return out


def alone(
    doc: Mapping[str, Any],
    terms: Sequence[str],
    Z: np.ndarray,
    *,
    level: int | None = None,
) -> list[str]:
    """The placed keywords alone in their node at the level compared (as in
    :func:`borderline`): they have no margin, and the list leaves them out."""
    tree = _Tree(doc, terms, _normalised(Z))
    out: list[str] = []
    for _, ids, _, count, keywords in _compared(tree, level):
        pos = {n: j for j, n in enumerate(ids)}
        out += [k for k, n in keywords if count[pos[n]] < 2]
    return sorted(out)


def borderline(
    doc: Mapping[str, Any],
    terms: Sequence[str],
    Z: np.ndarray,
    *,
    level: int | None = None,
) -> list[Borderline]:
    """Every placed keyword's margin between its node and the nearest other node, smallest first.

    *doc* is a tree document, *terms* the space's keywords in the rows of *Z*
    (the keywords' vectors). With *level*, each keyword is compared at that
    level through its node's ancestor there (keywords placed above it are left
    out); without, at the level of its own node. A keyword alone in its node
    there has no margin and is left out (:func:`alone`). Ties go to the keyword's text.
    """
    Zn = _normalised(Z)
    tree = _Tree(doc, terms, Zn)
    out: list[Borderline] = []
    for lv, ids, total, count, keywords in _compared(tree, level):
        if len(ids) < 2:
            continue
        pos = {n: j for j, n in enumerate(ids)}
        C = _normalised(total)
        keywords = [(k, n) for k, n in keywords if count[pos[n]] >= 2]
        if not keywords:
            continue
        rows = np.array([tree.row[k] for k, _ in keywords])
        mine = np.array([pos[n] for _, n in keywords])
        V = Zn[rows]
        sims = V @ C.T
        own_sum = total[mine] - V
        own = np.einsum("ij,ij->i", V, _normalised(own_sum))
        sims[np.arange(len(rows)), mine] = -np.inf
        sims[:, count == 0] = -np.inf
        best = np.argmax(sims, axis=1)
        near = sims[np.arange(len(rows)), best]
        for i, (k, n) in enumerate(keywords):
            if not np.isfinite(near[i]):
                continue
            out.append(
                Borderline(
                    keyword=k,
                    node=n,
                    level=lv,
                    other=ids[int(best[i])],
                    own=round(float(own[i]), 4),
                    near=round(float(near[i]), 4),
                    margin=round(float(own[i] - near[i]), 4),
                )
            )
    out.sort(key=lambda b: (b.margin, b.keyword))
    return out


def suggestions(
    doc: Mapping[str, Any],
    terms: Sequence[str],
    Z: np.ndarray,
    keywords: Iterable[str],
    *,
    top: int = 3,
) -> dict[str, list[Suggestion]]:
    """The *top* nodes nearest each keyword: the nodes that hold keywords on them, by cosine.

    A node's centroid is that of the keywords placed on it (a keyword being
    asked about is left out of its own node). A keyword outside the space gets
    an empty list.
    """
    tree = _Tree(doc, terms)
    Zn = _normalised(Z)
    ids, total, count = tree.sums(Zn, None)
    out: dict[str, list[Suggestion]] = {}
    wanted = list(dict.fromkeys(keywords))
    known = [k for k in wanted if k in tree.row]
    for k in wanted:
        out[k] = []
    if not known or not ids:
        return out
    pos = {n: j for j, n in enumerate(ids)}
    rows = np.array([tree.row[k] for k in known])
    V = Zn[rows]
    sims = V @ _normalised(total).T
    for i, k in enumerate(known):
        nid = tree.placed.get(k)
        if nid is not None:  # its own node, without it
            j = pos[nid]
            rest = total[j] - V[i]
            sims[i, j] = float(V[i] @ _normalised(rest[None, :])[0]) if count[j] > 1 else -np.inf
    sims[:, count == 0] = -np.inf
    n = min(top, len(ids))
    for i, k in enumerate(known):
        order = np.lexsort((np.arange(len(ids)), -sims[i]))[:n]
        out[k] = [
            Suggestion(ids[int(j)], round(float(sims[i, j]), 4))
            for j in order
            if np.isfinite(sims[i, j])
        ]
    return out
