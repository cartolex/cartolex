# SPDX-License-Identifier: MIT
"""Combing a theme tree: each keyword on the level its texts support.

A keyword the grouping put on a node of the finest level may be used in texts
of many themes (a method, a driver of change, a word of the whole field). The
comb reads, for each keyword, the texts that use it, and where each of those
texts sits in the tree *without* that keyword:

1. **a text's position** is the share of its other keywords on each node of
   the finest level (each text carries one unit; a text with no other placed
   keyword gives no evidence);
2. **a keyword's spread** is the sum of those positions over its texts: its
   use distributed over the finest nodes (:func:`keyword_spread`);
3. **its place** (:func:`comb`) is the lowest node whose subtree holds at
   least ``θ`` of that use, looking from the finest level up at the node with
   the most use on each level. A node's share is read above what any keyword
   would give it (*relative*: ``(s − b) / (1 − b)``, ``b`` the node's share of
   all the keywords' use), so that one ``θ`` means the same on a level of
   three nodes and on one of five hundred. A keyword that no top-level node
   holds at ``θ`` is **too broad for any theme**: it counts nowhere. A keyword
   used in fewer than :data:`MIN_TEXTS` texts keeps its node.

``θ`` is calibrated (:func:`calibrate`) so that every level holds about as
many keywords per node as the finest one, within :data:`THETA_GRID`.

The unit is the text (a document), not the person: a person working on two
themes does not make their keywords look broad. The measures behind these
choices are in ``docs/dev/themes-engine.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy import sparse

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_THETA",
    "MAX_CELLS",
    "MIN_TEXTS",
    "THETA_GRID",
    "Combed",
    "LevelSuggestion",
    "calibrate",
    "calibration_curve",
    "comb",
    "corpus_texts",
    "document_keywords",
    "keyword_spread",
    "level_maps",
    "load_text_keywords",
    "save_text_keywords",
    "tree_levels",
]

#: A keyword used in fewer texts keeps its node: too little evidence to move it.
MIN_TEXTS = 5
#: θ at depth 1, where no balance between levels can choose it.
DEFAULT_THETA = 0.2
#: The θ values the calibration tries (outside this band the demo worlds lose
#: specific keywords, or keep broad ones: see the measures).
THETA_GRID = tuple(float(x) for x in np.round(np.arange(0.10, 0.251, 0.025), 3))
#: The largest keywords × finest nodes spread the comb holds (8 bytes a cell): above
#: it the proposal is not combed.
MAX_CELLS = 50_000_000
#: The texts read with the vectorizer at a time.
TEXT_CHUNK = 2000


# ── the texts ────────────────────────────────────────────────────────────────


def document_keywords(
    texts: Sequence[str],
    *,
    vectorizer: Any,
    alias_to_canon: Mapping[str, str],
    terms: Sequence[str],
) -> sparse.csr_matrix:
    """``texts × terms``: 1 where the text uses the keyword (any of its forms), else 0.

    The texts are read with the vocabulary's own vectorizer and its forms
    folded onto the keywords, as the trajectories read them.
    """
    from .canonicalization import fold_tfidf_to_canonical

    lowered = [str(t).lower() for t in terms]
    concepts = sorted(set(alias_to_canon.values()) | set(lowered))
    folded = fold_tfidf_to_canonical(
        X_expanded=vectorizer.transform(list(texts)),
        expanded_terms=vectorizer.get_feature_names_out(),
        canonical_terms=concepts,
        alias_to_canon=dict(alias_to_canon),
    ).tocsc()
    col = {t: j for j, t in enumerate(concepts)}
    D = folded[:, np.array([col[t] for t in lowered], dtype=np.int64)].tocsr()
    D.data = np.ones_like(D.data, dtype=np.float64)
    D.eliminate_zeros()
    return D.astype(np.float64)


def corpus_texts(
    index_csvs: Iterable[Path],
    *,
    vectorizer_json: Path,
    aliases_csv: Path,
    terms: Sequence[str],
) -> sparse.csr_matrix:
    """``texts × terms`` over the texts of the corpus indexes, each text once.

    A text several people signed appears in the index once per person: it is
    read once (by its file). Texts are read :data:`TEXT_CHUNK` at a time.
    """
    import pandas as pd

    from cartolex.atlas.model_files import load_vectorizer

    files: list[Path] = []
    seen: set[Path] = set()
    for index in map(Path, index_csvs):
        if not index.exists():
            continue
        df = pd.read_csv(index)
        if "txt_path" not in df.columns:
            continue
        for value in df["txt_path"].dropna().astype(str):
            p = Path(value)
            p = (p if p.is_absolute() else index.parent / p).resolve()
            if p not in seen:
                seen.add(p)
                files.append(p)
    vectorizer = load_vectorizer(vectorizer_json)
    al = pd.read_csv(aliases_csv, dtype=str, keep_default_na=False)
    alias = {
        a.strip().lower(): c.strip().lower()
        for a, c in zip(al["alias"], al["canonical"], strict=True)
    }
    parts = []
    for start in range(0, len(files), TEXT_CHUNK):
        texts = []
        for p in files[start : start + TEXT_CHUNK]:
            try:
                texts.append(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                texts.append("")
        parts.append(
            document_keywords(texts, vectorizer=vectorizer, alias_to_canon=alias, terms=terms)
        )
    if not parts:
        return sparse.csr_matrix((0, len(terms)))
    return sparse.vstack(parts, format="csr")


# ── the spread and the comb ──────────────────────────────────────────────────


def keyword_spread(
    D: sparse.spmatrix, finest: np.ndarray, n_nodes: int
) -> tuple[np.ndarray, np.ndarray]:
    """Each keyword's use over the finest nodes, from its texts' other keywords.

    *D* is texts × keywords (presence), *finest* each keyword's node on the
    finest level (``-1``: not placed; such a keyword positions no text).
    Returns ``(P, n)``: ``P[k]`` sums, over the texts using keyword ``k``, the
    shares of the text's *other* placed keywords on each node (each text one
    unit), and ``n[k]`` counts those texts.
    """
    D = sparse.csr_matrix(D, dtype=np.float64)
    finest = np.asarray(finest, dtype=np.int64)
    placed = finest >= 0
    rows_placed = np.flatnonzero(placed)
    F = sparse.csr_matrix(
        (np.ones(len(rows_placed)), (rows_placed, finest[rows_placed])),
        shape=(len(finest), n_nodes),
    )
    C = (D @ F).tocsr()  # texts × nodes: the text's placed keywords on each node
    size = np.asarray(C.sum(axis=1)).ravel()
    P = np.zeros((len(finest), n_nodes))
    n = np.zeros(len(finest))
    Dt = D.T.tocsr()
    # leaving a placed keyword out leaves size − 1 others; an unplaced one, size
    for own, rows in ((True, rows_placed), (False, np.flatnonzero(~placed))):
        if not len(rows):
            continue
        others = size - 1 if own else size
        w = np.divide(1.0, others, out=np.zeros_like(size), where=others > 0)
        W = Dt[rows] @ sparse.diags(w)
        P[rows] = (W @ C).toarray()
        n[rows] = Dt[rows] @ (w > 0).astype(float)
        if own:  # the keyword itself, once on its own node in each of its texts
            P[rows, finest[rows]] -= np.asarray(W.sum(axis=1)).ravel()
    np.clip(P, 0.0, None, out=P)
    return P, n


def level_maps(levels: Sequence[Any]) -> list[np.ndarray]:
    """Each finest node's position on every level, from the top (the last: the identity).

    *levels* are :class:`cartolex.atlas.hierarchy.LevelGroups`, from the top.
    """
    maps = [np.arange(len(levels[-1].rows))]
    for lv in range(len(levels) - 1, 0, -1):
        maps.insert(0, np.asarray(levels[lv].parent, dtype=np.int64)[maps[0]])
    return maps


@dataclass(frozen=True)
class Combed:
    """Where the comb puts each keyword.

    ``level[k]``: the level (1 on top) of its node, ``0`` when it is too broad
    for any theme, ``-1`` when it was not placed; ``node[k]``: its node's
    position on that level (``-1`` for ``0`` and ``-1``); ``texts[k]``: the
    texts that gave evidence; ``share[k]``: the share (relative) of its use its
    node holds, or, too broad, the best a top-level node holds; ``theta``: the
    ``θ`` used on the finest level.
    """

    level: np.ndarray
    node: np.ndarray
    texts: np.ndarray
    theta: float = 0.0
    share: np.ndarray | None = None

    def counts(self, depth: int) -> dict[int, int]:
        """Keywords per level, ``0`` for too broad."""
        return {lv: int((self.level == lv).sum()) for lv in range(depth + 1)}


def comb(
    P: np.ndarray,
    n: np.ndarray,
    finest: np.ndarray,
    maps: Sequence[np.ndarray],
    theta: float | Sequence[float],
    *,
    min_texts: int = MIN_TEXTS,
    relative: bool = True,
) -> Combed:
    """Place each keyword on the lowest node whose subtree holds ≥ θ of its use.

    *P*, *n* from :func:`keyword_spread`; *finest* each keyword's finest node;
    *maps* from :func:`level_maps` (a cell above a level maps to ``-1`` there);
    *theta* one value, or one per level from the top. From the finest level up, the node with the most use on the level
    is taken when its share reaches that level's ``θ``; no level reaching it:
    too broad (level ``0``). With *relative* (the engine's reading), a node's
    share is read above the share ``b`` all the keywords' use gives it:
    ``(s − b) / (1 − b)``. A keyword with fewer than *min_texts* texts of
    evidence keeps its node.
    """
    depth = len(maps)
    th = np.full(depth, float(theta)) if np.isscalar(theta) else np.asarray(theta, dtype=float)
    if len(th) != depth:
        raise ValueError(f"{depth} level(s) but {len(th)} θ")
    finest = np.asarray(finest, dtype=np.int64)
    total = P.sum(axis=1)
    share = np.divide(P, total[:, None], out=np.zeros_like(P), where=total[:, None] > 0)
    base = P.sum(axis=0) / max(float(P.sum()), 1e-300)
    evidence = (finest >= 0) & (np.asarray(n) >= min_texts) & (total > 0)
    level = np.where(finest >= 0, depth, -1).astype(np.int64)
    node = finest.copy()
    level[evidence] = 0
    node[evidence] = -1
    waiting = evidence.copy()
    rows = np.arange(len(finest))
    held = np.zeros(len(finest))
    for lv in range(depth, 0, -1):
        m = np.asarray(maps[lv - 1], dtype=np.int64)
        cells = np.flatnonzero(m >= 0)
        M = sparse.csr_matrix(
            (np.ones(len(cells)), (cells, m[cells])), shape=(len(m), int(m.max(initial=-1)) + 1)
        )
        S = np.asarray(M.T @ share.T).T  # keywords × nodes of the level
        if relative:
            b = M.T @ base
            S = (S - b) / np.maximum(1.0 - b, 1e-12)
        best = S.argmax(axis=1)
        top = S[rows, best] if S.shape[1] else np.zeros(len(rows))
        ok = waiting & (top >= th[lv - 1] - 1e-12)
        level[ok] = lv
        node[ok] = best[ok]
        held[ok] = top[ok]
        if lv == 1:
            held[waiting & ~ok] = top[waiting & ~ok]
        waiting &= ~ok
    return Combed(level=level, node=node, texts=np.asarray(n), theta=float(th[-1]), share=held)


def _per_node(c: Combed, nodes: Sequence[int]) -> np.ndarray:
    counts = c.counts(len(nodes))
    return np.array([counts[lv] / max(nodes[lv - 1], 1) for lv in range(1, len(nodes) + 1)])


def _balance_error(per_node: np.ndarray) -> float:
    """Over the levels above the finest, the summed |log| of their keywords per node to the finest's."""
    return float(np.abs(np.log((per_node[:-1] + 0.5) / (per_node[-1] + 0.5))).sum())


def calibrate(
    P: np.ndarray,
    n: np.ndarray,
    finest: np.ndarray,
    maps: Sequence[np.ndarray],
    *,
    grid: Sequence[float] = THETA_GRID,
    min_texts: int = MIN_TEXTS,
) -> Combed:
    """The comb at the θ of *grid* that best balances the keywords per node across levels.

    Every level should hold about as many keywords per node as the finest,
    so a top level of few nodes holds few keywords. The θ kept is the one
    whose levels are closest to that (:func:`_balance_error`), the smallest
    on a tie. At depth 1 there is nothing to balance: :data:`DEFAULT_THETA`.
    """
    depth = len(maps)
    if depth == 1:
        return comb(P, n, finest, maps, DEFAULT_THETA, min_texts=min_texts)
    nodes = [int(np.max(m, initial=-1)) + 1 for m in maps]
    best: tuple[float, Combed] | None = None
    for t in grid:
        c = comb(P, n, finest, maps, float(t), min_texts=min_texts)
        err = _balance_error(_per_node(c, nodes))
        if best is None or err < best[0] - 1e-12:
            best = (err, c)
    assert best is not None
    return best[1]


def calibration_curve(
    P: np.ndarray,
    n: np.ndarray,
    finest: np.ndarray,
    maps: Sequence[np.ndarray],
    *,
    grid: Sequence[float] = THETA_GRID,
    min_texts: int = MIN_TEXTS,
) -> dict[str, Any]:
    """What :func:`calibrate` weighed: for each θ of *grid*, the keywords per level and per node,
    the too broad, and the balance error; with the θ it keeps.

    ``{"theta": kept θ, "points": [{"theta", "keywords" (per level, from the top),
    "per_node" (per level), "too_broad", "error"}]}``; ``error`` is ``None`` at depth 1, where
    :data:`DEFAULT_THETA` is kept.
    """
    depth = len(maps)
    nodes = [int(np.max(m, initial=-1)) + 1 for m in maps]
    points: list[dict[str, Any]] = []
    for t in grid:
        c = comb(P, n, finest, maps, float(t), min_texts=min_texts)
        counts = c.counts(depth)
        per_node = _per_node(c, nodes)
        points.append(
            {
                "theta": float(t),
                "keywords": [counts[lv] for lv in range(1, depth + 1)],
                "per_node": [round(float(x), 3) for x in per_node],
                "too_broad": counts[0],
                "error": None if depth == 1 else round(_balance_error(per_node), 4),
            }
        )
    kept = calibrate(P, n, finest, maps, grid=grid, min_texts=min_texts).theta
    return {"theta": float(kept), "points": points}


# ── suggestions on a tree of any shape ───────────────────────────────────────


@dataclass(frozen=True)
class LevelSuggestion:
    """A placed keyword the texts would put higher: on an ancestor (``to``), or nowhere."""

    keyword: str
    node: str  # where the tree puts it
    to: str | None  # the ancestor its texts support; None: too broad for any theme
    share: float  # the (relative) share of its use that node holds, or the best top node's
    texts: int


def tree_levels(
    doc: Mapping[str, Any],
    terms: Sequence[str],
    D: sparse.spmatrix,
    *,
    grid: Sequence[float] = THETA_GRID,
    min_texts: int = MIN_TEXTS,
) -> tuple[float, list[LevelSuggestion]]:
    """The comb read on a curated tree: the keywords whose texts support a higher node.

    *doc* is a ``cartolex-themes/1`` tree over *terms* (the columns of *D*,
    texts × keywords). Every node is a cell: a keyword's texts are placed by
    their other keywords' nodes. The comb (at the θ of *grid* that balances
    the tree's levels, :func:`calibrate`) is read for each placed keyword; one
    it takes to a strict ancestor of its node is suggested « move up », one no
    top-level node holds is suggested « too broad for any theme ». Moves to a
    node elsewhere are the borderline list's business, not this one's. The
    suggestions come with the largest shares first (too broad: the smallest
    best share first).
    """
    nodes = [str(n["id"]) for n in doc.get("nodes") or []]
    parent = {str(n["id"]): n.get("parent") for n in doc.get("nodes") or []}
    level: dict[str, int] = {}

    def level_of(nid: str) -> int:
        if nid not in level:
            up = parent.get(nid)
            level[nid] = 1 if up is None else level_of(str(up)) + 1
        return level[nid]

    depth = max((level_of(n) for n in nodes), default=0)
    if not depth:
        return 0.0, []
    cell = {nid: j for j, nid in enumerate(nodes)}
    row = {t: i for i, t in enumerate(terms)}
    finest = np.full(len(terms), -1, dtype=np.int64)
    for k, nid in (doc.get("keywords") or {}).items():
        if k in row and nid in cell:
            finest[row[k]] = cell[nid]
    by_level = {lv: [n for n in nodes if level_of(n) == lv] for lv in range(1, depth + 1)}
    position = {n: i for lv in by_level.values() for i, n in enumerate(lv)}

    def ancestor(nid: str, lv: int) -> str | None:
        if level_of(nid) < lv:
            return None
        while level_of(nid) > lv:
            nid = str(parent[nid])
        return nid

    maps = []
    for lv in range(1, depth + 1):
        m = np.full(len(nodes), -1, dtype=np.int64)
        for j, nid in enumerate(nodes):
            a = ancestor(nid, lv)
            if a is not None:
                m[j] = position[a]
        maps.append(m)
    if len(terms) * len(nodes) > MAX_CELLS or D.shape[1] != len(terms):
        return 0.0, []
    P, n = keyword_spread(D, finest, len(nodes))
    combed = calibrate(P, n, finest, maps, grid=grid, min_texts=min_texts)
    out: list[LevelSuggestion] = []
    for i in np.flatnonzero(finest >= 0):
        own = nodes[finest[i]]
        lv, pos = int(combed.level[i]), int(combed.node[i])
        share = round(float(combed.share[i]), 4) if combed.share is not None else 0.0
        if lv == 0:
            out.append(LevelSuggestion(terms[i], own, None, share, int(n[i])))
        elif lv < level_of(own):
            to = by_level[lv][pos]
            if ancestor(own, lv) == to:
                out.append(LevelSuggestion(terms[i], own, to, share, int(n[i])))
    out.sort(key=lambda s: (s.to is None, -s.share if s.to else s.share, s.keyword))
    return combed.theta, out


def save_text_keywords(path: Path, D: sparse.spmatrix, terms: Sequence[str]) -> None:
    """Store the texts × keywords presence matrix (the comb's reading of the texts)."""
    D = sparse.csr_matrix(D)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        np.savez_compressed(
            fh,
            indices=D.indices.astype(np.int32),
            indptr=D.indptr.astype(np.int64),
            shape=np.asarray(D.shape, dtype=np.int64),
            terms=np.asarray(len(terms), dtype=np.int64),
        )


def load_text_keywords(path: Path) -> sparse.csr_matrix:
    """The matrix :func:`save_text_keywords` stored."""
    with np.load(path, allow_pickle=False) as a:
        shape = tuple(int(x) for x in a["shape"])
        return sparse.csr_matrix(
            (np.ones(len(a["indices"])), a["indices"], a["indptr"]), shape=shape
        )
