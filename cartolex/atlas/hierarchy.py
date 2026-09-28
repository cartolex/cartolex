# SPDX-License-Identifier: MIT
"""Deterministic keyword hierarchy: keywords → concepts → subfields.

No LLM. **Concepts ARE the term clusters** (``run_clustering`` — agglomerative Ward at
``n_concepts``, full coverage): each distinct cluster is one concept, so the number of
concepts is the operator's ``n_concepts`` knob and re-running the clustering propagates
here. Subfields
(``k_sub``) are a Ward cut over the concept centroids — the top grouping level; the
most-general, largest subfield is flagged ``general``.

Each node is seeded by its **dominant keyword** (the member term with the highest
``global_score``), a first-pass label the operator refines while curating the draft
(``cartolex.lexicon.subfields``). See INTEGRATION.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.preprocessing import normalize

from .clustering import ward_labels


def _centroid(Zn: np.ndarray, idx) -> np.ndarray:
    return normalize(Zn[np.asarray(idx)].mean(axis=0).reshape(1, -1))[0]


def group_subfields(concept_centroids: np.ndarray, *, target: int) -> np.ndarray:
    """Group concept centroids into proto-subfields by a Ward cut at exactly ``target``.

    ``target`` is the **number of subfields** (a tunable granularity lever), honoured exactly:
    Ward links the L2-normalised concept centroids and the dendrogram is cut into ``target``
    clusters (``maxclust``). The only time fewer than ``target`` come back is when there are
    fewer concepts than ``target`` — then each concept stands alone. Returns a dense 0-based
    label per concept (aligned to ``concept_centroids``). Deterministic.

    A forced count is used deliberately. An earlier "soft cap" picked a coarser cut whenever a
    ``k < target`` had a more isolated dendrogram gap, but in any real domain the broad
    top-level split dominates every gap, so it collapsed to 2–3 subfields and the ``target``
    lever did nothing. Forcing the count keeps granularity predictable and adjustable.
    """
    C = np.asarray(concept_centroids, dtype=float)
    n = C.shape[0]
    if n <= 1:
        return np.zeros(n, dtype=int)
    target = max(1, min(int(target), n))
    if target == 1:
        return np.zeros(n, dtype=int)
    if target >= n:
        return np.arange(n, dtype=int)  # asked for ≥ n subfields → each concept stands alone
    return ward_labels(normalize(C), target)


@dataclass(frozen=True)
class LevelGroups:
    """The groups of one level of a theme tree, numbered by their position.

    ``rows`` holds each group's keyword rows: on the finest level in increasing
    order, above it the rows of its children one child after the other.
    ``parent`` gives each group's position on the level above (``None`` on the
    top level).
    """

    rows: tuple[np.ndarray, ...]
    parent: np.ndarray | None = None


def level_groups(
    Z_terms: np.ndarray, finest_labels: np.ndarray | list[int], level_sizes: list[int] | tuple
) -> list[LevelGroups]:
    """The groups of every level of a theme tree, from the top, over the finest groups given.

    *finest_labels* is each keyword's group on the finest level (the term
    clustering; a negative label: no group). Each coarser level is the Ward
    cut (:func:`group_subfields`) of the L2-normalised centroids of the level
    below at that level's size, ``level_sizes[l]`` (the finest size, the last
    one, is the clustering's and is not used here). At two levels this is
    exactly :func:`build_hierarchy`'s cut of the concepts into subfields.
    """
    Zn = normalize(np.asarray(Z_terms, dtype=float))
    labels = np.asarray(finest_labels, dtype=int)
    numbers = sorted({int(x) for x in labels.tolist() if int(x) >= 0})
    levels = [LevelGroups(tuple(np.flatnonzero(labels == c) for c in numbers))]
    for size in reversed(list(level_sizes)[:-1]):
        below = levels[0]
        n = len(below.rows)
        if n >= 2:
            centroids = np.array([_centroid(Zn, np.sort(r)) for r in below.rows])
            sub_of = group_subfields(centroids, target=int(size))
        else:
            sub_of = np.zeros(n, dtype=int)
        upper = sorted(set(sub_of.tolist()))
        position = {g: i for i, g in enumerate(upper)}
        rows = tuple(
            np.concatenate([below.rows[p] for p in range(n) if sub_of[p] == g]) for g in upper
        )
        levels[0] = LevelGroups(below.rows, np.array([position[int(g)] for g in sub_of], dtype=int))
        levels.insert(0, LevelGroups(rows))
    return levels


def assign_subfields(
    concepts: list[dict[str, Any]],
    Zn: np.ndarray,
    gscore: np.ndarray,
    terms: list[str],
    *,
    target_subfields: int,
) -> list[dict[str, Any]]:
    """Group ``concepts`` (each a dict with ``term_indices``) into ``target_subfields`` subfields.

    Cuts the concept centroids with :func:`group_subfields` (exactly ``target_subfields``, or
    fewer only when there are fewer concepts), sets each concept's
    ``subfield_id`` (its position in ``concepts`` is its id), and returns the subfields list —
    each seeded by its dominant keyword with ``generality`` and a ``general`` flag on the most
    general+largest. Used by :func:`build_hierarchy`.
    """
    n = len(concepts)
    if n == 0:
        return []
    gscore = np.asarray(gscore, dtype=float)
    global_centroid = normalize(Zn.mean(axis=0).reshape(1, -1))[0]
    ccent = np.array([_centroid(Zn, c["term_indices"]) for c in concepts])
    sub_of = group_subfields(ccent, target=target_subfields) if n >= 2 else np.zeros(n, dtype=int)

    def dominant(idx) -> str:
        idx = np.asarray(idx)
        return terms[int(idx[int(np.argmax(gscore[idx]))])]

    def top_terms(idx, cap=15):
        order = np.asarray(idx)[np.argsort(gscore[np.asarray(idx)])[::-1]]
        return [terms[i] for i in order[:cap]]

    subfields: list[dict[str, Any]] = []
    for si, sid in enumerate(sorted(set(sub_of.tolist()))):
        cpos = [p for p, s in enumerate(sub_of.tolist()) if s == sid]
        all_terms = np.array([ti for p in cpos for ti in concepts[p]["term_indices"]])
        cen = _centroid(ccent, cpos)
        subfields.append(
            {
                "id": si,
                "label": dominant(all_terms),
                "generality": round(float(cen @ global_centroid), 3),
                "general": False,
                "concept_ids": [int(concepts[p].get("id", p)) for p in cpos],
                "top_terms": top_terms(all_terms),
            }
        )
        for p in cpos:
            concepts[p]["subfield_id"] = si
    if subfields:
        biggest = max(subfields, key=lambda s: (s["generality"], len(s["concept_ids"])))
        biggest["general"] = True
    return subfields


def build_hierarchy(
    Z_terms: np.ndarray,
    terms: list[str],
    global_score: np.ndarray | None = None,
    *,
    cluster_labels: np.ndarray | list[int],
    target_subfields: int = 12,
) -> dict[str, Any]:
    """Concept/subfield hierarchy whose concepts are the term clusters.

    ``cluster_labels`` is the per-term cluster id (aligned to ``terms``) from the
    clustering stage; each
    distinct non-negative cluster becomes a concept (negative ids — noise — are dropped, but
    agglomerative clustering produces none). Subfields are the :func:`group_subfields`
    Ward cut over the concept centroids at exactly ``target_subfields`` (the same grouping the
    clustering stage writes to ``proto_subfields.json``); each node is seeded by its dominant
    keyword. Returns ``concepts``/``subfields`` (with ``label``, ``generality``; subfields also
    ``general``) and ``dropped_term_indices``.
    """
    Z_terms = np.asarray(Z_terms, dtype=float)
    n = Z_terms.shape[0]
    Zn = normalize(Z_terms)
    gscore = np.asarray(global_score, dtype=float) if global_score is not None else np.zeros(n)
    global_centroid = normalize(Zn.mean(axis=0).reshape(1, -1))[0]
    labels = np.asarray(cluster_labels)

    def dominant_kw(idx) -> str:
        idx = np.asarray(idx)
        return terms[int(idx[int(np.argmax(gscore[idx]))])]

    def top_terms(idx, cap=15):
        order = np.asarray(idx)[np.argsort(gscore[np.asarray(idx)])[::-1]]
        return [terms[i] for i in order[:cap]]

    # ── concepts = the term clusters (full coverage; drop only −1 noise) ──
    concepts: list[dict[str, Any]] = []
    for c in sorted({int(x) for x in labels.tolist() if int(x) >= 0}):
        idx = np.where(labels == c)[0]
        if len(idx) >= 1:
            concepts.append({"term_indices": [int(i) for i in idx]})
    dropped = sorted(int(i) for i in np.where(labels < 0)[0])
    if not concepts:
        return {"concepts": [], "subfields": [], "dropped_term_indices": dropped}

    ccent = np.array([_centroid(Zn, c["term_indices"]) for c in concepts])

    # ── distance-threshold cut of the concept centroids → subfields (≤ target) ──
    subfields = assign_subfields(concepts, Zn, gscore, terms, target_subfields=target_subfields)

    for ci, c in enumerate(concepts):
        c["id"] = ci
        c["label"] = dominant_kw(c["term_indices"])
        c["generality"] = round(float(ccent[ci] @ global_centroid), 3)
        c["size"] = len(c["term_indices"])
        c["top_terms"] = top_terms(c["term_indices"])
        # c["subfield_id"] set by assign_subfields above

    return {
        "concepts": concepts,
        "subfields": subfields,
        "dropped_term_indices": dropped,
    }
