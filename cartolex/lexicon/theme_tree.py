# SPDX-License-Identifier: MIT
"""The theme tree inside the engine: 1 to 4 levels, keywords on nodes of any level.

The engine reads and writes a tree as the project's own document,
``cartolex-themes/1`` (the shape of ``decisions/themes.json``, keywords keyed
by their text), without importing the project package:

- :func:`draft_themes` (the grouping stage) writes the machine proposal: the
  finest level is the term clustering, each coarser level a Ward cut of the
  centroids of the level below (:func:`cartolex.atlas.hierarchy.level_groups`),
  every node named, in each language, after its most used keyword that has a
  form in that language;
- :class:`EngineTree` reads a tree over the rows of the lexical data: each
  keyword's node and how many levels, from the top, its usage counts toward
  (its node's level, lowered by its attribution; ``0``: nowhere);
- :func:`apply_themes` (the apply stage) computes, for every level, each
  node's weight and share for each person and organisation, the keywords'
  lexicon weights and each node's top keywords, and writes them as tables.

The people × keywords matrix is never made dense: the usage shares stay
sparse, and the keywords' weights are summed by chunks of keywords
(:func:`keyword_weights`) in the order numpy sums the dense matrix, so that
the numbers are those of the dense computation bit for bit.

The rules are in ``docs/dev/themes-engine.md``; the file format of a tree in
``docs/format/decisions.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from scipy import sparse

if TYPE_CHECKING:
    from cartolex.context import RunContext

logger = logging.getLogger(__name__)

__all__ = [
    "APPLIED_FORMAT",
    "CHUNK_BYTES",
    "LANGUAGES",
    "TOP_KEYWORDS",
    "TREE_FORMAT",
    "AppliedThemes",
    "EngineTree",
    "LevelWeights",
    "TreeNode",
    "apply_theme_files",
    "apply_themes",
    "apply_tree",
    "default_level_names",
    "draft_themes",
    "held_usage",
    "keyword_weights",
    "node_colors",
    "propose_tree",
    "read_tree",
    "row_totals",
    "usage_shares",
    "vocabulary_fingerprint",
    "write_theme_draft",
]

#: The format of a theme tree (the project's ``decisions/themes.json``).
TREE_FORMAT = "cartolex-themes/1"
#: The format of the applied tree the apply stage writes.
APPLIED_FORMAT = "cartolex-themes-applied/1"
#: The deepest tree.
MAX_DEPTH = 4
#: How many top keywords each node of an applied tree lists (as a concept's ``top_terms``).
TOP_KEYWORDS = 15
#: The memory a chunk of keyword columns made dense may take.
CHUNK_BYTES = 64 * 2**20
#: The languages a tree names its nodes and levels in (the project's).
LANGUAGES = ("en", "fr", "pt")

_THEME = {"en": "Theme", "fr": "Thème", "pt": "Tema"}
_TOPIC = {"en": "Topic", "fr": "Sujet", "pt": "Tópico"}
_FIELD = {"en": "Field", "fr": "Champ", "pt": "Área"}
_DOMAIN = {"en": "Domain", "fr": "Domaine", "pt": "Domínio"}
_DEFAULT_LEVELS: dict[int, tuple[dict[str, str], ...]] = {
    1: (_THEME,),
    2: (_THEME, _TOPIC),
    3: (_FIELD, _THEME, _TOPIC),
    4: (_DOMAIN, _FIELD, _THEME, _TOPIC),
}


def default_level_names(depth: int) -> list[dict[str, str]]:
    """The default names of a tree's levels at *depth* (1–4), from the top (the project's)."""
    if depth not in _DEFAULT_LEVELS:
        raise ValueError(f"a theme tree has 1 to {MAX_DEPTH} levels, not {depth!r}")
    return [dict(names) for names in _DEFAULT_LEVELS[depth]]


def vocabulary_fingerprint(vocabulary: Sequence[str]) -> str:
    """``sha256:<hex>`` of the JSON list of the distinct keywords, sorted (the project's rule)."""
    text = json.dumps(sorted(set(vocabulary)), ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── usage shares, never dense ─────────────────────────────────────────────────


def held_usage(X: Any, columns: Sequence[int] | np.ndarray) -> sparse.csr_matrix:
    """*X* (people × keywords, sparse or dense) restricted to *columns*: a canonical float CSR."""
    cols = np.asarray(columns, dtype=np.int64)
    if sparse.issparse(X):
        H = sparse.csr_matrix(X)[:, cols]
    else:
        H = sparse.csr_matrix(np.asarray(X, dtype=float)[:, cols])
    H = sparse.csr_matrix(H, dtype=np.float64)
    H.sum_duplicates()
    H.sort_indices()
    return H


def row_totals(H: sparse.csr_matrix) -> np.ndarray:
    """Each person's usage over the columns of *H*, added in column order.

    This is the order numpy follows for the row sums of the people × keywords
    matrix made dense over the columns (a matrix numpy lays out column by
    column when it selects columns), so the totals are its bit for bit.
    """
    return np.asarray(H @ np.ones(H.shape[1]), dtype=float)


def usage_shares(H: sparse.csr_matrix, totals: np.ndarray) -> sparse.csr_matrix:
    """*H* with each person's row divided by their total (rows without usage stay empty)."""
    S = H.copy()
    per_row = np.repeat(totals, np.diff(S.indptr))
    with np.errstate(invalid="ignore", divide="ignore"):
        S.data = np.where(per_row > 0, S.data / per_row, 0.0)
    S.eliminate_zeros()
    return S


def keyword_weights(
    H: sparse.csr_matrix, totals: np.ndarray, *, chunk_bytes: int = CHUNK_BYTES
) -> np.ndarray:
    """The sum of the people's shares on each column of *H*, by chunks of keywords.

    A chunk of columns is made dense for every person, laid out column by
    column, and summed per column with numpy's own summation: exactly the
    column sums of the whole share matrix made dense (the numbers the engine's
    lexicon weights always had), with at most *chunk_bytes* dense at a time.
    """
    n, m = H.shape
    per = max(1, int(chunk_bytes) // (8 * max(1, n)))
    C = H.tocsc()
    parts = []
    for a in range(0, m, per):
        B = C[:, a : a + per].toarray(order="F")
        with np.errstate(invalid="ignore", divide="ignore"):
            S = np.where(totals[:, None] > 0, B / totals[:, None], 0.0)
        parts.append(np.asfortranarray(S).sum(axis=0))
    return np.concatenate(parts) if parts else np.zeros(0)


# ── the tree over the rows of the lexical data ───────────────────────────────


@dataclass(frozen=True)
class TreeNode:
    """A node of a tree: its id, parent, level (1 on top), order among its siblings and names."""

    id: str
    parent: str | None
    level: int
    order: int
    names: Mapping[str, str]


@dataclass(frozen=True)
class LevelWeights:
    """A usage's weights on one level: node indices (into ``EngineTree.nodes``), weights, shares."""

    level: int
    nodes: np.ndarray
    weights: np.ndarray
    shares: np.ndarray


@dataclass(frozen=True, eq=False)
class EngineTree:
    """A theme tree of 1 to 4 levels over the keyword rows of the lexical data.

    ``nodes`` are in tree order (depth first, siblings by ``order`` then id).
    ``node_of[row]`` is the index of the keyword's node, ``-1`` when the tree
    does not place it (set aside, or not in the tree); ``counts_to[row]`` is
    how many levels, from the top, its usage counts toward: its node's level,
    or its attribution (``0``: none). A keyword counts toward the ancestor of
    its node at every level up to ``counts_to``.
    """

    depth: int
    level_names: tuple[Mapping[str, str], ...]
    nodes: tuple[TreeNode, ...]
    terms: tuple[str, ...]
    node_of: np.ndarray
    counts_to: np.ndarray
    based_on: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        index = {n.id: i for i, n in enumerate(self.nodes)}
        parent = np.array(
            [-1 if n.parent is None else index[n.parent] for n in self.nodes], dtype=np.int64
        )
        level = np.array([n.level for n in self.nodes], dtype=np.int64)
        ancestor = {}
        for lv in range(1, self.depth + 1):
            anc = np.full(len(self.nodes), -1, dtype=np.int64)
            for i in range(len(self.nodes)):
                j = i
                while j >= 0 and level[j] > lv:
                    j = parent[j]
                if j >= 0 and level[j] == lv:
                    anc[i] = j
            ancestor[lv] = anc
        children: list[list[int]] = [[] for _ in self.nodes]
        for i, p in enumerate(parent.tolist()):
            if p >= 0:
                children[p].append(i)
        post: list[int] = []

        def walk(i: int) -> None:
            for c in children[i]:
                walk(c)
            post.append(i)

        for i in range(len(self.nodes)):
            if parent[i] < 0:
                walk(i)
        rank = np.empty(len(self.nodes), dtype=np.int64)
        rank[np.array(post, dtype=np.int64)] = np.arange(len(post))
        at_level = {lv: np.flatnonzero(level == lv) for lv in range(1, self.depth + 1)}
        position = np.zeros(len(self.nodes), dtype=np.int64)
        for idx in at_level.values():
            position[idx] = np.arange(len(idx))
        for name, value in (
            ("index", index),
            ("parent_index", parent),
            ("level_of", level),
            ("_ancestor", ancestor),
            ("children", tuple(tuple(c) for c in children)),
            ("post_rank", rank),
            ("_at_level", at_level),
            ("position", position),
        ):
            object.__setattr__(self, name, value)

    # ── reading ──
    @classmethod
    def from_document(cls, doc: Mapping[str, Any], terms: Sequence[str]) -> EngineTree:
        """Read a ``cartolex-themes/1`` document over *terms*, the lexical data's rows.

        Refuses a document that is not a tree of 1 to 4 levels, or that holds
        a keyword *terms* does not have (rebase the tree first). A keyword of
        *terms* the tree does not hold is not counted anywhere.
        """
        fmt = doc.get("format", TREE_FORMAT)
        if fmt != TREE_FORMAT:
            raise ValueError(f"not a {TREE_FORMAT} tree (format {fmt!r})")
        depth = int(doc.get("depth", 0))
        if not 1 <= depth <= MAX_DEPTH:
            raise ValueError(f"a theme tree has 1 to {MAX_DEPTH} levels, not {depth}")
        levels = list(doc.get("levels") or [])
        if len(levels) != depth:
            raise ValueError(f"depth is {depth} but {len(levels)} level(s) are named")
        raw = list(doc.get("nodes") or [])
        by_id: dict[str, Mapping[str, Any]] = {}
        for n in raw:
            nid = str(n["id"])
            if nid in by_id:
                raise ValueError(f"two nodes have the id {nid!r}")
            by_id[nid] = n
        kids: dict[str | None, list[Mapping[str, Any]]] = {}
        for n in raw:
            parent = n.get("parent")
            if parent is not None and parent not in by_id:
                raise ValueError(f"node {n['id']!r} has an unknown parent {parent!r}")
            kids.setdefault(parent, []).append(n)
        for group in kids.values():
            group.sort(key=lambda n: (int(n.get("order", 0)), str(n["id"])))
        nodes: list[TreeNode] = []

        def walk(parent: str | None, level: int) -> None:
            for n in kids.get(parent, []):
                if level > depth:
                    raise ValueError(f"node {n['id']!r} sits below the tree's depth {depth}")
                nodes.append(
                    TreeNode(
                        str(n["id"]),
                        parent,
                        level,
                        int(n.get("order", 0)),
                        dict(n.get("names") or {}),
                    )
                )
                walk(str(n["id"]), level + 1)

        walk(None, 1)
        if len(nodes) != len(raw):
            raise ValueError("the nodes of the tree hold a cycle")
        index = {n.id: i for i, n in enumerate(nodes)}
        row_of = {t: i for i, t in enumerate(terms)}
        keywords = dict(doc.get("keywords") or {})
        aside = dict(doc.get("set_aside") or {})
        unknown = sorted((set(keywords) | set(aside)) - row_of.keys())
        if unknown:
            raise ValueError(
                f"{len(unknown)} keyword(s) of the tree are not in the vocabulary "
                f"(rebase the tree first): {unknown[:5]}"
            )
        node_of = np.full(len(terms), -1, dtype=np.int64)
        counts_to = np.zeros(len(terms), dtype=np.int64)
        attribution = dict(doc.get("attribution") or {})
        for keyword, node_id in keywords.items():
            if node_id not in index:
                raise ValueError(f"keyword {keyword!r} points to an unknown node {node_id!r}")
            row = row_of[keyword]
            level = nodes[index[node_id]].level
            n = attribution.get(keyword)
            if n is not None and not 0 <= int(n) < level:
                raise ValueError(
                    f"keyword {keyword!r}: an attribution counts toward 0 to {level - 1} level(s)"
                )
            node_of[row] = index[node_id]
            counts_to[row] = level if n is None else int(n)
        missing = len(terms) - len(keywords) - len(aside)
        if missing:
            logger.info(
                "%d keyword(s) of the vocabulary are not in the tree: not counted.", missing
            )
        return cls(
            depth=depth,
            level_names=tuple(dict(lv.get("names") or {}) for lv in levels),
            nodes=tuple(nodes),
            terms=tuple(terms),
            node_of=node_of,
            counts_to=counts_to,
            based_on=dict(doc.get("based_on") or {}),
        )

    # ── structure ──
    def at_level(self, level: int) -> np.ndarray:
        """The indices of the nodes of *level*, in tree order."""
        return self._at_level[level]  # type: ignore[attr-defined]

    def ancestors(self, level: int) -> np.ndarray:
        """Each node's ancestor on *level* (itself on its own level; ``-1`` above it)."""
        return self._ancestor[level]  # type: ignore[attr-defined]

    def counting(self, level: int) -> tuple[np.ndarray, np.ndarray]:
        """``(rows, nodes)``: the keyword rows counting toward *level*, and the node each counts toward."""
        rows = np.flatnonzero((self.node_of >= 0) & (self.counts_to >= level))
        return rows, self.ancestors(level)[self.node_of[rows]]

    def held(self) -> np.ndarray:
        """The keyword rows the tree places (on a node of any level), in increasing order."""
        return np.flatnonzero(self.node_of >= 0)

    def membership(self, level: int, columns: np.ndarray) -> sparse.csr_matrix:
        """``len(columns) × nodes of level``: 1 where the column's keyword counts toward the node."""
        n_level = len(self.at_level(level))
        cols = np.asarray(columns, dtype=np.int64)
        nodes = self.node_of[cols]
        ok = (nodes >= 0) & (self.counts_to[cols] >= level)
        which = np.flatnonzero(ok)
        target = self.position[self.ancestors(level)[nodes[which]]]  # type: ignore[attr-defined]
        return sparse.csr_matrix((np.ones(len(which)), (which, target)), shape=(len(cols), n_level))

    def stop_node(self) -> np.ndarray:
        """Per keyword row, the deepest node its usage counts toward (``-1``: none)."""
        out = np.full(len(self.terms), -1, dtype=np.int64)
        rows = np.flatnonzero((self.node_of >= 0) & (self.counts_to >= 1))
        for lv in range(1, self.depth + 1):
            sel = rows[self.counts_to[rows] == lv]
            out[sel] = self.ancestors(lv)[self.node_of[sel]]
        return out

    def describe(self, rows: np.ndarray, values: np.ndarray) -> list[LevelWeights]:
        """The weights at every level of one usage: *values* on keyword *rows*.

        The usage is divided by its sum over the keywords the tree places; a
        node's weight is the share on the keywords counting toward it, its
        share that weight over the level's. Levels without usage are left out.
        """
        rows = np.asarray(rows, dtype=np.int64)
        values = np.asarray(values, dtype=float)
        placed = self.node_of[rows] >= 0
        rows, values = rows[placed], values[placed]
        total = float(values.sum())
        out: list[LevelWeights] = []
        if total <= 0:
            return out
        share = values / total
        for lv in range(1, self.depth + 1):
            ok = self.counts_to[rows] >= lv
            nodes = self.ancestors(lv)[self.node_of[rows[ok]]]
            if not len(nodes):
                continue
            pos = self.position[nodes]  # type: ignore[attr-defined]
            w = np.bincount(pos, weights=share[ok], minlength=len(self.at_level(lv)))
            keep = np.flatnonzero(w > 0)
            if not len(keep):
                continue
            out.append(LevelWeights(lv, self.at_level(lv)[keep], w[keep], w[keep] / w[keep].sum()))
        return out


def read_tree(path: Path, terms: Sequence[str]) -> EngineTree:
    """Read a tree file (``cartolex-themes/1``) over *terms*."""
    return EngineTree.from_document(json.loads(Path(path).read_text(encoding="utf-8")), terms)


# ── colours ──────────────────────────────────────────────────────────────────


def node_colors(tree: EngineTree) -> list[str]:
    """One colour per node: a hue per top-level node, a shade of it per descendant.

    A top-level node ``s<k>`` takes palette entry ``k``; other top-level nodes
    the next free entries, in tree order. Each descendant takes a shade of its
    top node's hue by its place among that node's descendants in tree order:
    at two levels, the subfields' and concepts' colours of the engine.
    """
    import re

    from .subfields import concept_shade, subfield_color

    top = [i for i, n in enumerate(tree.nodes) if n.level == 1]
    numbers: dict[int, int] = {}
    for i in top:
        m = re.match(r"^s(0|[1-9][0-9]*)$", tree.nodes[i].id)
        if m:
            numbers[i] = int(m.group(1))
    following = max(numbers.values(), default=-1) + 1
    for i in top:
        if i not in numbers:
            numbers[i] = following
            following += 1
    colors = [""] * len(tree.nodes)
    ancestor = tree.ancestors(1)
    for i in top:
        base = subfield_color(numbers[i])
        colors[i] = base
        below = [j for j in range(len(tree.nodes)) if j != i and ancestor[j] == i]
        for rank, j in enumerate(below):
            colors[j] = concept_shade(base, rank, len(below))
    return colors


# ── the proposal of the grouping stage ───────────────────────────────────────


def _node_ids(depth: int) -> list[str]:
    """The id prefix of each level: ``s`` on top, ``c`` on the finest, ``m<l>-`` between."""
    if depth == 1:
        return ["s"]
    return ["s", *[f"m{lv}-" for lv in range(2, depth)], "c"]


def propose_tree(
    levels: Sequence[Any],
    terms: Sequence[str],
    scores: np.ndarray,
    *,
    reference_language: str = "en",
    display_languages: Sequence[str] = ("fr", "en"),
    forms: Mapping[str, Mapping[str, str]] | None = None,
    run: str | None = None,
    preferred: Collection[str] = (),
) -> dict[str, Any]:
    """The proposal document (``cartolex-themes/1``) of groups on every level.

    *levels* are :class:`cartolex.atlas.hierarchy.LevelGroups`, from the top.
    Node ids are ``s<k>`` on the top level, ``c<k>`` on the finest and
    ``m<l>-<k>`` on a level ``l`` between them, ``k`` being the group's position
    on its level; siblings are ordered by it. A node is named in each language
    after the most used keyword (the highest *scores*) that has a form in that
    language in *forms* (``{language: {keyword: form}}``, see
    :func:`cartolex.lexicon.labels.node_names`; on a tie, a keyword of
    *preferred*: a concept or an object of study), and siblings' names are kept
    distinct. Keywords sit on the finest level, without attribution; a
    keyword in no group is set aside. ``based_on`` records *run* and the
    vocabulary's fingerprint. The document is in the canonical form.
    """
    from .labels import distinct_names, node_names

    depth = len(levels)
    if not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"a theme tree has 1 to {MAX_DEPTH} levels, not {depth}")
    scores = np.asarray(scores, dtype=float)
    langs = [
        lang for lang in LANGUAGES if lang == reference_language or lang in tuple(display_languages)
    ]
    prefix = _node_ids(depth)

    def top_terms(rows: np.ndarray, cap: int = TOP_KEYWORDS) -> list[str]:
        order = np.asarray(rows)[np.argsort(scores[np.asarray(rows)])[::-1]]
        return [terms[i] for i in order[:cap]]

    names: list[list[dict[str, str]]] = []
    for group in levels:
        mine = [
            node_names(r, terms, scores, forms or {}, langs, reference_language, preferred)
            for r in group.rows
        ]
        siblings: dict[int, list[int]] = {}
        for p in range(len(group.rows)):
            siblings.setdefault(-1 if group.parent is None else int(group.parent[p]), []).append(p)
        for members in siblings.values():
            distinct_names([mine[p] for p in members], [top_terms(group.rows[p]) for p in members])
        names.append(mine)

    nodes: list[dict[str, Any]] = []

    def walk(lv: int, parent_pos: int | None, parent_id: str | None) -> None:
        group = levels[lv]
        members = (
            list(range(len(group.rows)))
            if parent_pos is None
            else [p for p in range(len(group.rows)) if int(group.parent[p]) == parent_pos]
        )
        for order, p in enumerate(members, start=1):
            nid = f"{prefix[lv]}{p}"
            nodes.append({"id": nid, "parent": parent_id, "names": names[lv][p], "order": order})
            if lv + 1 < depth:
                walk(lv + 1, p, nid)

    walk(0, None, None)
    keywords: dict[str, str] = {}
    for p, rows in enumerate(levels[-1].rows):
        for r in np.asarray(rows).tolist():
            keywords[terms[int(r)]] = f"{prefix[-1]}{p}"
    aside = {
        t: {"from": None, "reason": "in no group of the grouping"}
        for t in terms
        if t not in keywords
    }
    return {
        "format": TREE_FORMAT,
        "depth": depth,
        "levels": [{"names": dict(n)} for n in default_level_names(depth)],
        "nodes": nodes,
        "keywords": dict(sorted(keywords.items())),
        "attribution": {},
        "set_aside": dict(sorted(aside.items())),
        "review": {},
        "based_on": {"run": run, "vocabulary": vocabulary_fingerprint(terms)},
        "saved": None,
    }


def draft_themes(
    ctx: RunContext, *, level_sizes: Sequence[int], run: str | None = None
) -> dict[str, Any]:
    """Grouping stage: write the proposal tree (``ctx.paths.themes_draft_json``) and return it.

    Needs the term clustering (``ctx.paths.terms_clustered_csv``, its groups
    are the finest level) and the SVD. *level_sizes* are the groups per level,
    from the top; the coarser levels are cut from the finest. *run* names the
    run in the tree's ``based_on``.
    """
    paths, settings = ctx.paths, ctx.settings
    with ctx.threads.applied():
        return write_theme_draft(
            lexical_data_json=paths.lexical_data_json,
            embeddings_json=paths.embeddings_json,
            term_clusters_csv=paths.terms_clustered_csv,
            pairs_csv=paths.refined_pairs_csv,
            draft_json_out=paths.themes_draft_json,
            level_sizes=level_sizes,
            reference_language=settings.reference_language,
            display_languages=settings.display_languages,
            run=run,
            categories_json=paths.keyword_categories_json,
        )


def write_theme_draft(
    *,
    lexical_data_json: Path,
    embeddings_json: Path,
    term_clusters_csv: Path,
    pairs_csv: Path | None,
    draft_json_out: Path,
    level_sizes: Sequence[int],
    reference_language: str = "en",
    display_languages: Sequence[str] = ("fr", "en"),
    run: str | None = None,
    categories_json: Path | None = None,
) -> dict[str, Any]:
    """Write the proposal tree from explicit files (see :func:`draft_themes`).

    *categories_json* holds the keywords' categories (lower-case term → category):
    a node's name prefers a concept or an object of study on a tie of use."""
    from cartolex.atlas.hierarchy import level_groups
    from cartolex.atlas.model_files import load_embeddings, load_lexical_data

    from .categories import NAMING_PREFERRED
    from .labels import keyword_forms
    from .subfields import _load_term_cluster_labels, _require_lexical_models

    _require_lexical_models(lexical_data_json, embeddings_json)
    data = load_lexical_data(lexical_data_json)
    emb = load_embeddings(embeddings_json)
    terms = [str(t) for t in data.terms]
    labels = _load_term_cluster_labels(term_clusters_csv, terms)
    scores = np.asarray(data.X.sum(axis=0)).ravel()
    levels = level_groups(emb.Z_terms, labels, list(level_sizes))
    categories: dict[str, str] = {}
    if categories_json is not None and categories_json.is_file():
        categories = json.loads(categories_json.read_text(encoding="utf-8"))
    doc = propose_tree(
        levels,
        terms,
        scores,
        reference_language=reference_language,
        display_languages=display_languages,
        forms=keyword_forms(pairs_csv, terms, display_languages, reference_language),
        run=run,
        preferred={t for t in terms if categories.get(t.strip().lower()) in NAMING_PREFERRED},
    )
    draft_json_out.parent.mkdir(parents=True, exist_ok=True)
    draft_json_out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", "utf-8")
    logger.info(
        "Wrote the theme proposal (%d level(s), %s groups) to %s",
        len(levels),
        " › ".join(str(len(lv.rows)) for lv in levels),
        draft_json_out,
    )
    return doc


# ── applying a tree ──────────────────────────────────────────────────────────


@dataclass
class AppliedThemes:
    """What applying a tree gives: the nodes, and the tables of the apply stage.

    ``nodes`` holds one entry per node in tree order (id, parent, level,
    order, names, colour, weight, share, keywords on the node, keywords
    counted, top keywords, and the map position when people positions were
    given). ``keywords``, ``people`` and ``organisations`` are the tables
    (see :func:`apply_tree`); the last two are empty when not asked for.
    """

    tree: EngineTree
    nodes: list[dict[str, Any]]
    keywords: pd.DataFrame
    people: pd.DataFrame
    organisations: pd.DataFrame
    people_counted: int
    weights_basis: str = "tf"

    def document(self, *, source: str = "") -> dict[str, Any]:
        """The applied tree as JSON (``cartolex-themes-applied/1``)."""
        return {
            "format": APPLIED_FORMAT,
            "depth": self.tree.depth,
            "levels": [
                {"level": lv, "names": dict(names)}
                for lv, names in enumerate(self.tree.level_names, start=1)
            ],
            "source": source,
            "based_on": dict(self.tree.based_on),
            "weights_basis": self.weights_basis,
            "people_counted": self.people_counted,
            "nodes": self.nodes,
        }


KEYWORD_COLUMNS = ["term", "term_index", "node", "level", "counts_to", "weight", "share"]
PEOPLE_COLUMNS = ["researcher_id", "person_id", "level", "node", "weight", "share"]
ORGANISATION_COLUMNS = ["unit", "level", "node", "weight", "share", "people"]


def apply_tree(
    tree: EngineTree,
    X: Any,
    *,
    scores: np.ndarray,
    researcher_ids: Sequence[str],
    units: Sequence[str] | None = None,
    person_ids: Mapping[str, str] | None = None,
    person_xy: np.ndarray | None = None,
    tables: bool = True,
    weights_basis: str = "tf",
    chunk_bytes: int = CHUNK_BYTES,
) -> AppliedThemes:
    """Apply *tree* to the usage *X* (people × keywords, sparse or dense, never made dense).

    - Each person's usage is divided by its sum over the keywords the tree
      places (every placed keyword, whatever its attribution): each person with
      usage carries one unit.
    - A person's weight on a level-``k`` node is their share on the keywords
      counting toward it; its share is that weight over their weights on level
      ``k``. An organisation (a value of *units*) sums its people's weights.
    - A keyword's weight is the sum of the people's shares on it, its share
      that weight over the people counted (rounded to six decimals).
    - A node's weight is the weights of the keywords whose counting stops at
      it (by the post-order of their nodes, then by row) plus its children's
      weights, each rounded to six decimals, and its share that sum over the
      people counted: the engine's two-level rule, at any depth.
    - Its top keywords are the :data:`TOP_KEYWORDS` keywords counting toward it
      with the highest *scores* (ties by row).
    - With *person_xy* (a map position per person) each node gets the mean
      position of its people, weighted by their weights on it.

    *tables* false skips the people and organisation tables (the positions are
    still computed).
    """
    n_people = int(X.shape[0])
    if len(researcher_ids) != n_people:
        raise ValueError("one researcher id per row of the usage matrix is needed")
    scores = np.asarray(scores, dtype=float)
    held = tree.held()
    depth = tree.depth
    member = {lv: tree.membership(lv, held) for lv in range(1, depth + 1)}
    H = held_usage(X, held)
    totals = row_totals(H)
    counted = int((totals > 0).sum())
    term_w = keyword_weights(H, totals, chunk_bytes=chunk_bytes)
    xy = None if person_xy is None else np.asarray(person_xy, dtype=float)
    level_weights: dict[int, sparse.csr_matrix] = {}
    if tables or xy is not None:
        shares = usage_shares(H, totals)
        for lv, M in member.items():
            level_weights[lv] = sparse.csr_matrix(shares @ M)
    total = float(counted) or 1.0
    weight_of = np.zeros(len(tree.terms))
    weight_of[held] = term_w

    # keyword table: by node (tree order), then by row
    order = held[np.lexsort((held, tree.node_of[held]))]
    keywords = pd.DataFrame(
        {
            "term": [tree.terms[i] for i in order],
            "term_index": order.astype(int),
            "node": [tree.nodes[tree.node_of[i]].id for i in order],
            "level": [tree.nodes[tree.node_of[i]].level for i in order],
            "counts_to": tree.counts_to[order].astype(int),
            "weight": [round(float(weight_of[i]), 6) for i in order],
            "share": [round(float(weight_of[i]) / total, 6) for i in order],
        },
        columns=KEYWORD_COLUMNS,
    )

    # node weights: the direct keywords, then the children, each rounded
    stop = tree.stop_node()
    direct_rows = np.flatnonzero(stop >= 0)
    direct_rows = direct_rows[
        np.lexsort((direct_rows, tree.post_rank[tree.node_of[direct_rows]]))  # type: ignore[attr-defined]
    ]
    direct: dict[int, list[int]] = {}
    for r in direct_rows.tolist():
        direct.setdefault(int(stop[r]), []).append(r)
    raw = [0.0] * len(tree.nodes)
    rounded = [0.0] * len(tree.nodes)
    for i in np.argsort(tree.post_rank).tolist():  # type: ignore[attr-defined]
        acc = 0.0
        for r in direct.get(i, []):
            acc += float(weight_of[r])
        for c in tree.children[i]:  # type: ignore[attr-defined]
            acc += rounded[c]
        raw[i] = acc
        rounded[i] = round(acc, 6)

    # top keywords, keywords on each node and counted toward it
    tops: dict[int, list[str]] = {}
    n_counted = np.zeros(len(tree.nodes), dtype=np.int64)
    for lv in range(1, depth + 1):
        rows, nodes = tree.counting(lv)
        np.add.at(n_counted, nodes, 1)
        ranked = np.lexsort((rows, -scores[rows], nodes))
        for r, nd in zip(rows[ranked].tolist(), nodes[ranked].tolist(), strict=True):
            bucket = tops.setdefault(int(nd), [])
            if len(bucket) < TOP_KEYWORDS:
                bucket.append(tree.terms[r])
    on_node = np.bincount(tree.node_of[held], minlength=len(tree.nodes))
    colors = node_colors(tree)
    mass: dict[int, np.ndarray] = {}
    at_xy: dict[int, np.ndarray] = {}
    if xy is not None:
        for lv, W in level_weights.items():
            mass[lv] = np.asarray(W.sum(axis=0)).ravel()
            at_xy[lv] = np.asarray(W.T @ xy)
    entries: list[dict[str, Any]] = []
    for i, n in enumerate(tree.nodes):
        entry: dict[str, Any] = {
            "id": n.id,
            "parent": n.parent,
            "level": n.level,
            "order": n.order,
            "names": dict(n.names),
            "color": colors[i],
            "weight": rounded[i],
            "share": round(raw[i] / total, 6),
            "keywords": int(on_node[i]),
            "keywords_counted": int(n_counted[i]),
            "top_keywords": tops.get(i, []),
        }
        if xy is not None:
            pos = int(tree.position[i])  # type: ignore[attr-defined]
            m = float(mass[n.level][pos])
            entry["x"] = float(at_xy[n.level][pos, 0] / m) if m > 0 else None
            entry["y"] = float(at_xy[n.level][pos, 1] / m) if m > 0 else None
        entries.append(entry)

    people = pd.DataFrame(columns=PEOPLE_COLUMNS)
    organisations = pd.DataFrame(columns=ORGANISATION_COLUMNS)
    if tables:
        people = _people_table(tree, level_weights, researcher_ids, person_ids or {})
        organisations = _organisation_table(tree, people, researcher_ids, units)
    return AppliedThemes(
        tree=tree,
        nodes=entries,
        keywords=keywords,
        people=people,
        organisations=organisations,
        people_counted=counted,
        weights_basis=weights_basis,
    )


def _people_table(
    tree: EngineTree,
    level_weights: Mapping[int, sparse.csr_matrix],
    researcher_ids: Sequence[str],
    person_ids: Mapping[str, str],
) -> pd.DataFrame:
    """One row per (person, node) with a weight: by person, level, then node in tree order."""
    parts = []
    for lv, W in level_weights.items():
        coo = W.tocoo()
        keep = coo.data > 0
        r, c, w = coo.row[keep], coo.col[keep], coo.data[keep]
        level_total = np.asarray(W.sum(axis=1)).ravel()
        parts.append((r, np.full(len(r), lv), c, w, w / level_total[r]))
    if not parts:
        return pd.DataFrame(columns=PEOPLE_COLUMNS)
    r, lv, pos, w, s = (np.concatenate([p[i] for p in parts]) for i in range(5))
    order = np.lexsort((pos, lv, r))
    r, lv, pos, w, s = r[order], lv[order], pos[order], w[order], s[order]
    node_ids = np.array([n.id for n in tree.nodes], dtype=object)
    node = np.empty(len(r), dtype=object)
    for k in range(1, tree.depth + 1):
        here = lv == k
        node[here] = node_ids[tree.at_level(k)[pos[here]]]
    rids = np.array([str(x) for x in researcher_ids], dtype=object)[r]
    external = np.array([person_ids.get(str(x), "") for x in researcher_ids], dtype=object)[r]
    return pd.DataFrame(
        {
            "researcher_id": rids,
            "person_id": external,
            "level": lv.astype(int),
            "node": node,
            "weight": w,
            "share": s,
        },
        columns=PEOPLE_COLUMNS,
    )


def _organisation_table(
    tree: EngineTree,
    people: pd.DataFrame,
    researcher_ids: Sequence[str],
    units: Sequence[str] | None,
) -> pd.DataFrame:
    if units is None or people.empty:
        return pd.DataFrame(columns=ORGANISATION_COLUMNS)
    unit_of = {
        str(r): ("" if u is None else str(u)) for r, u in zip(researcher_ids, units, strict=True)
    }
    frame = people.assign(unit=people["researcher_id"].map(unit_of))
    grouped = (
        frame.groupby(["unit", "level", "node"], sort=False)
        .agg(weight=("weight", "sum"), people=("researcher_id", "nunique"))
        .reset_index()
    )
    level_sum = grouped.groupby(["unit", "level"])["weight"].transform("sum")
    grouped["share"] = grouped["weight"] / level_sum
    rank = {n.id: i for i, n in enumerate(tree.nodes)}
    grouped["_rank"] = grouped["node"].map(rank)
    grouped = grouped.sort_values(["unit", "level", "_rank"], kind="stable")
    return grouped[ORGANISATION_COLUMNS].reset_index(drop=True)


def apply_themes(
    ctx: RunContext,
    *,
    tree_json: Path | None = None,
    tables: bool = True,
    person_ids: Mapping[str, str] | None = None,
) -> AppliedThemes:
    """Apply stage: apply a tree and write the applied tree and its tables.

    The tree is *tree_json*, else ``ctx.paths.themes_json`` when it exists,
    else the proposal (``ctx.paths.themes_draft_json``). Writes
    ``ctx.paths.themes_tree_json`` (the tree applied, as read),
    ``ctx.paths.themes_applied_json``, ``ctx.paths.theme_keywords_csv``,
    ``ctx.paths.theme_people_parquet`` and
    ``ctx.paths.theme_organisations_parquet``. The weights come from the TF
    track when ``ctx.settings.weights_basis`` is ``"tf"`` (as the two-level
    apply). When the map is drawn, the nodes get their map positions.

    With *tables* false the tree applied before (``ctx.paths.themes_tree_json``)
    is applied again and only ``ctx.paths.themes_applied_json`` is written: the
    layout does this once the map is drawn, to place the nodes on it.
    *person_ids* maps the engine's researcher ids to other ids, written beside
    them in the people table.
    """
    paths = ctx.paths
    tree_out: Path | None = paths.themes_tree_json
    if not tables:
        tree_json, tree_out = paths.themes_tree_json, None
        source = _applied_source(paths.themes_applied_json)
    elif tree_json is not None:
        source = "given"
    elif paths.themes_json.exists():
        tree_json, source = paths.themes_json, "decisions"
    else:
        tree_json, source = paths.themes_draft_json, "draft"
    with ctx.threads.applied():
        return apply_theme_files(
            tree_json=tree_json,
            lexical_data_json=paths.lexical_data_json,
            embeddings_json=paths.embeddings_json,
            tree_out=tree_out,
            applied_out=paths.themes_applied_json,
            keywords_out=paths.theme_keywords_csv if tables else None,
            people_out=paths.theme_people_parquet if tables else None,
            organisations_out=paths.theme_organisations_parquet if tables else None,
            weights_basis=ctx.settings.weights_basis,
            person_ids=person_ids,
            source=source,
        )


def _applied_source(path: Path) -> str:
    try:
        return str(json.loads(Path(path).read_text(encoding="utf-8")).get("source", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def apply_theme_files(
    *,
    tree_json: Path,
    lexical_data_json: Path,
    embeddings_json: Path,
    tree_out: Path | None,
    applied_out: Path,
    keywords_out: Path | None = None,
    people_out: Path | None = None,
    organisations_out: Path | None = None,
    weights_basis: str = "tf",
    person_ids: Mapping[str, str] | None = None,
    source: str = "",
) -> AppliedThemes:
    """Apply the tree of *tree_json* and write the results to the files given (see :func:`apply_themes`)."""
    from cartolex.atlas.model_files import load_embeddings, load_lexical_data

    from .subfields import _require_lexical_models, _researcher_id_series

    if not Path(tree_json).exists():
        raise FileNotFoundError(f"{tree_json} not found: run the grouping stage first.")
    _require_lexical_models(lexical_data_json, embeddings_json)
    data = load_lexical_data(lexical_data_json)
    emb = load_embeddings(embeddings_json)
    terms = [str(t) for t in data.terms]
    text = Path(tree_json).read_text(encoding="utf-8")
    tree = EngineTree.from_document(json.loads(text), terms)
    X = data.X
    basis = "tfidf"
    if weights_basis == "tf" and getattr(data, "X_tf", None) is not None:
        X, basis = data.X_tf, "tf"
    elif weights_basis == "tf":
        logger.warning("weights_basis='tf' but the lexical data has no TF track: using TF-IDF.")
    tables = people_out is not None or organisations_out is not None
    meta = data.meta_ind
    units = meta["unit"].fillna("").astype(str).tolist() if "unit" in meta.columns else None
    applied = apply_tree(
        tree,
        X,
        scores=np.asarray(data.X.sum(axis=0)).ravel(),
        researcher_ids=_researcher_id_series(meta).tolist(),
        units=units,
        person_ids=person_ids,
        person_xy=emb.umap_ind,
        tables=tables,
        weights_basis=basis,
    )
    applied_out.parent.mkdir(parents=True, exist_ok=True)
    if tree_out is not None:
        tree_out.parent.mkdir(parents=True, exist_ok=True)
        tree_out.write_text(text, encoding="utf-8")
    applied_out.write_text(
        json.dumps(applied.document(source=source), ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    if keywords_out is not None:
        applied.keywords.to_csv(keywords_out, index=False)
    if people_out is not None:
        applied.people.to_parquet(people_out, index=False)
    if organisations_out is not None:
        applied.organisations.to_parquet(organisations_out, index=False)
    logger.info(
        "Applied a theme tree of %d level(s) (%d nodes) to %d people → %s",
        tree.depth,
        len(tree.nodes),
        applied.people_counted,
        applied_out,
    )
    return applied
