# SPDX-License-Identifier: MIT
"""Converters between a depth-2 theme tree and the engine's two-level curated document.

The apply stage reads a theme tree of any depth itself
(:mod:`cartolex.lexicon.theme_tree`). At depth 2 it also writes the engine's
two-level outputs, for the numeric reference and for migrating older projects:
the two-level apply (:func:`cartolex.lexicon.subfields.apply_subfield_files`)
reads a curated document with two levels, subfields, each holding concepts,
each holding term indices (rows of the lexical data). :func:`to_curated` writes
a depth-2 tree in that format, and :func:`from_curated` reads such a document —
or the grouping stage's two-level draft — into a tree.

The correspondence:

- nodes of level 1 are subfields, nodes of level 2 are concepts, and keyword
  texts are term indices through *terms*, the vocabulary in row order;
- a keyword on a level-2 node is a term of that concept; its attribution is
  the term's status: none is *defining*, ``1`` *subfield-only*, ``0``
  *ride-along*;
- the keywords on a level-1 node itself (broader than its topics) are the
  terms of one more concept of that subfield, named like it and marked
  ``theme_keywords_of``: each is *subfield-only* (``0``: *ride-along*), so it
  counts toward the subfield's share only, as the engine's subfield-only status
  always did, and that concept's own share is zero;
- ``label`` holds a node's name in the reference language, ``label_<lang>`` its
  other names;
- set-aside keywords are the document's trashed terms.

Reading a document the engine wrote (without the tree's keys), a subfield-only
term goes onto its subfield's node: that is what the status meant.

What the curated format has no place for travels in keys the engine ignores:
``theme_node`` on each subfield and concept (the node's id, names and order),
``set_aside`` on each trashed term, and ``theme_tree`` at the top (the tree's
format, depth, level names, review states and basis). :func:`from_curated`
reads them back, so ``from_curated(to_curated(tree, terms), terms).tree`` is
``canonical(tree)`` exactly. What a curated document holds that a tree does not
(merge variants, term statuses, groups not kept, stashed and trashed items,
pinned colours) is never dropped silently: :func:`from_curated` sets the
keywords aside with a reason and lists each case in its notes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .models import LANGUAGES, ThemeNode, ThemesFile
from .themes import canonical, default_level_names, vocabulary_of

__all__ = [
    "CURATED_SCHEMA_VERSION",
    "NOT_GROUPED",
    "STATUS_OF",
    "THEME_KEYWORDS_OF",
    "Imported",
    "from_curated",
    "to_curated",
]

#: The ``schema_version`` :func:`to_curated` writes (the engine's edited-document schema).
CURATED_SCHEMA_VERSION = "1.1"

#: The reason given to a keyword of the vocabulary that no group of the document holds.
NOT_GROUPED = "in no group of the curated document"

#: How many representative terms a concept lists (as the grouping stage's draft does).
TOP_TERMS = 15
#: How many seed terms a subfield lists (as the engine's apply stage derives them).
SUBFIELD_SEEDS = 20

#: The engine's term status of each attribution of a depth-2 tree (``None``: every level).
STATUS_OF: dict[int | None, str] = {None: "defining", 1: "subfield_only", 0: "ride_along"}
_ATTRIBUTION_OF = {v: k for k, v in STATUS_OF.items()}
_LIST_OF = {"subfield_only": "subfield_only_terms", "ride_along": "ride_along_terms"}
#: The key that marks the concept holding a level-1 node's own keywords.
THEME_KEYWORDS_OF = "theme_keywords_of"

_SUBFIELD_ID = re.compile(r"^s(0|[1-9][0-9]*)$")
_CONCEPT_ID = re.compile(r"^c(0|[1-9][0-9]*)$")


@dataclass(frozen=True)
class Imported:
    """The result of :func:`from_curated`.

    ``tree`` is the tree; ``notes`` says what it could not carry as it was;
    ``merges`` lists each merge variant of the document with the keyword it
    merges into, ``(variant, target)``, sorted: :meth:`keyword_rows` turns them
    into rows of ``decisions/keywords.csv``.
    """

    tree: ThemesFile
    notes: tuple[str, ...]
    merges: tuple[tuple[str, str], ...] = ()

    def keyword_rows(self, *, language: str = "", decided_at: str = "") -> list[dict[str, str]]:
        """The merges as ``keywords.csv`` rows: decision ``merge``, source ``person``."""
        return [
            {
                "term": variant,
                "language": language,
                "decision": "merge",
                "target": target,
                "reason": "merge variant in the curated document",
                "source": "person",
                "decided_at": decided_at,
            }
            for variant, target in self.merges
        ]


def _vocabulary(terms: Sequence[str]) -> dict[str, int]:
    index: dict[str, int] = {}
    twice: list[str] = []
    for i, t in enumerate(terms):
        if not isinstance(t, str):
            raise ValueError(f"the vocabulary holds a non-text entry at row {i}: {t!r}")
        if t in index:
            twice.append(t)
        else:
            index[t] = i
    if twice:
        raise ValueError(f"the vocabulary lists keyword(s) twice: {sorted(twice)[:5]}")
    return index


def _check_language(reference_language: str) -> None:
    if reference_language not in LANGUAGES:
        raise ValueError(f"unknown language {reference_language!r}; known: {list(LANGUAGES)}")


# ── tree → curated document ──────────────────────────────────────────────────


def _int_ids(node_ids: list[str], pattern: re.Pattern[str]) -> dict[str, int]:
    """Integer ids: ``s<k>``/``c<k>`` keep *k*; other nodes take the next free numbers in order."""
    out: dict[str, int] = {}
    for nid in node_ids:
        m = pattern.match(nid)
        if m:
            out[nid] = int(m.group(1))
    following = max(out.values(), default=-1) + 1
    for nid in node_ids:
        if nid not in out:
            out[nid] = following
            following += 1
    return out


def _labels(node: ThemeNode, reference_language: str) -> dict[str, str]:
    names = dict(node.names)
    label = names.get(reference_language) or next(
        (names[lang] for lang in LANGUAGES if names.get(lang)), node.id
    )
    out = {"label": label}
    for lang in LANGUAGES:
        if lang != reference_language and lang in names:
            out[f"label_{lang}"] = names[lang]
    return out


def _norm(term: Any) -> str:
    """How the engine matches a status string with a term."""
    return str(term).strip().lower()


def _status(tree: ThemesFile, keyword: str, on_top: bool) -> str:
    """The engine's status of a placed keyword of a depth-2 tree."""
    n = tree.attribution.get(keyword)
    if on_top:
        return "ride_along" if n == 0 else "subfield_only"
    return STATUS_OF[n]


def _check_case(tree: ThemesFile, top: set[str]) -> None:
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    for keyword, node_id in tree.keywords.items():
        status = _status(tree, keyword, node_id in top)
        other = seen.setdefault((node_id, _norm(keyword)), (keyword, status))
        if other[1] != status:
            raise ValueError(
                f"keywords {other[0]!r} and {keyword!r} of node {node_id!r} differ only by case "
                "and have different attributions; the curated document cannot tell them apart"
            )


def _carried(node: ThemeNode) -> dict[str, Any]:
    dump = node.model_dump(mode="json", by_alias=True)
    dump.pop("parent", None)
    return dump


def to_curated(
    tree: ThemesFile,
    terms: Sequence[str],
    *,
    reference_language: str = "en",
    domain_title: str = "",
    scores: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """The engine's curated document for a depth-2 *tree*.

    *terms* is the vocabulary in the lexical data's row order; the tree must be
    rebased on it (it places or sets aside every keyword of *terms*, and holds
    no other). Subfields keep the number of an ``s<k>`` node id and concepts of a
    ``c<k>`` one (so a tree imported from a draft keeps the draft's numbers and
    colours); other nodes take the next free numbers in tree order. Each concept
    lists its first :data:`TOP_TERMS` keywords as ``top_terms`` — by descending
    *scores* when given (ties by row), else by row — and each subfield the first
    :data:`SUBFIELD_SEEDS` of its concepts' ``top_terms`` as seeds, as the apply
    stage derives them. Nodes without keywords stay, as empty groups.

    A keyword on a level-2 node is a term of its concept, and its attribution
    the term's status (:data:`STATUS_OF`): ``1`` lists it in the concept's
    ``subfield_only_terms``, ``0`` in its ``ride_along_terms``. The keywords on
    a level-1 node itself go to one more concept of that subfield, after its
    topics, named like the subfield, numbered after every other concept and
    marked :data:`THEME_KEYWORDS_OF`: each is subfield-only (``0``: ride-along),
    so the lexicon weights are those of subfield-only terms and that concept's
    share is zero. A set-aside keyword's attribution is its trashed entry's
    ``status``. The engine matches statuses without case, so two keywords of one
    node that differ only by case must share their attribution.
    """
    if tree.depth != 2:
        raise ValueError(f"the curated document has two levels; this tree has {tree.depth}")
    _check_language(reference_language)
    index = _vocabulary(terms)
    held = vocabulary_of(tree)
    unknown = sorted(held - index.keys())
    if unknown:
        raise ValueError(
            f"{len(unknown)} keyword(s) of the tree are not in the vocabulary "
            f"(rebase the tree first): {unknown[:5]}"
        )
    missing = sorted(index.keys() - held)
    if missing:
        raise ValueError(
            f"{len(missing)} keyword(s) of the vocabulary are neither placed nor set aside "
            f"(rebase the tree first): {missing[:5]}"
        )
    tree = canonical(tree)
    top = [n for n in tree.nodes if n.parent is None]
    second = [n for n in tree.nodes if n.parent is not None]
    subfield_id = _int_ids([n.id for n in top], _SUBFIELD_ID)
    concept_id = _int_ids([n.id for n in second], _CONCEPT_ID)
    held: dict[str, list[str]] = {}
    for keyword, node_id in tree.keywords.items():
        held.setdefault(node_id, []).append(keyword)
    top_ids = {n.id for n in top}
    _check_case(tree, top_ids)
    following = max(concept_id.values(), default=-1) + 1
    own_id: dict[str, int] = {}
    for node in top:
        if held.get(node.id):
            own_id[node.id] = following
            following += 1

    def rank(row: int) -> tuple[float, int]:
        return (-float(scores.get(terms[row], 0.0)), row) if scores else (0.0, row)

    def concept(
        cid: int, node: ThemeNode, sid: int, keywords: list[str], extra: dict[str, Any]
    ) -> dict[str, Any]:
        on_top = node.id in top_ids
        lists: dict[str, list[str]] = {}
        for k in keywords:
            status = _status(tree, k, on_top)
            if status != "defining":
                lists.setdefault(_LIST_OF[status], []).append(k)
        indices = sorted(index[k] for k in keywords)
        return {
            "id": cid,
            **_labels(node, reference_language),
            "subfield_id": sid,
            "term_indices": indices,
            "term_merges": [],
            "top_terms": [terms[i] for i in sorted(indices, key=rank)[:TOP_TERMS]],
            **{key: sorted(ks) for key, ks in sorted(lists.items())},
            **extra,
        }

    concepts: list[dict[str, Any]] = []
    for theme in top:
        sid = subfield_id[theme.id]
        for node in second:
            if node.parent == theme.id:
                concepts.append(
                    concept(
                        concept_id[node.id],
                        node,
                        sid,
                        held.get(node.id, []),
                        {"theme_node": _carried(node)},
                    )
                )
        if theme.id in own_id:
            concepts.append(
                concept(own_id[theme.id], theme, sid, held[theme.id], {THEME_KEYWORDS_OF: theme.id})
            )
    subfields: list[dict[str, Any]] = []
    for node in top:
        seeds: list[str] = []
        for c in concepts:
            if c["subfield_id"] == subfield_id[node.id]:
                seeds.extend(t for t in c["top_terms"] if t not in seeds)
        subfields.append(
            {
                "id": subfield_id[node.id],
                **_labels(node, reference_language),
                "keep": True,
                "is_other": False,
                "top_terms": seeds[:SUBFIELD_SEEDS],
                "theme_node": _carried(node),
            }
        )
    trashed = []
    for keyword, entry in tree.set_aside.items():
        carried = entry.model_dump(mode="json", by_alias=True)
        carried.pop("attribution", None)
        trashed.append(
            {
                "term_index": index[keyword],
                "term": keyword,
                "origin_concept_id": concept_id.get(entry.source, -1) if entry.source else -1,
                "merge_group": None,
                "status": STATUS_OF[entry.attribution],
                "set_aside": carried,
            }
        )
    meta = tree.model_dump(mode="json", by_alias=True)
    for key in ("nodes", "keywords", "attribution", "set_aside"):
        meta.pop(key)
    return {
        "schema_version": CURATED_SCHEMA_VERSION,
        "domain_title": domain_title,
        "status": "curated",
        "subfields": subfields,
        "concepts": concepts,
        "stash": {"concepts": [], "terms": []},
        "trash": {"subfields": [], "concepts": [], "terms": trashed},
        "theme_tree": meta,
    }


# ── curated document → tree ──────────────────────────────────────────────────


def _names_from_labels(entry: Mapping[str, Any], reference_language: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for lang in LANGUAGES:
        if lang == reference_language:
            value = entry.get("label") or entry.get(f"label_{lang}")
        else:
            value = entry.get(f"label_{lang}")
        if isinstance(value, str) and value.strip():
            names[lang] = value.strip()
    return names


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _status_reader(concept: Mapping[str, Any]) -> Any:
    """term -> its attribution from the concept's status lists (ride-along wins, as in the engine)."""
    ride = {_norm(t) for t in concept.get("ride_along_terms") or []}
    only = {_norm(t) for t in concept.get("subfield_only_terms") or []}

    def status(term: str) -> int | None:
        t = _norm(term)
        return 0 if t in ride else 1 if t in only else None

    return status


class _Claims:
    """Which keyword the document places where; a keyword is claimed once."""

    def __init__(self, terms: Sequence[str]) -> None:
        self.terms = terms
        self.where: dict[str, str] = {}

    def take(self, row: Any, where: str) -> str:
        try:
            i = int(row)
        except (TypeError, ValueError):
            raise ValueError(f"{where}: {row!r} is not a term index") from None
        if not 0 <= i < len(self.terms):
            raise ValueError(
                f"{where}: term index {i} is outside the vocabulary (0 to {len(self.terms) - 1})"
            )
        term = self.terms[i]
        if term in self.where:
            raise ValueError(f"keyword {term!r} is in both {self.where[term]} and {where}")
        self.where[term] = where
        return term


def from_curated(
    doc: Mapping[str, Any], terms: Sequence[str], *, reference_language: str = "en"
) -> Imported:
    """A depth-2 tree from a curated document or a draft, and notes on what changed form.

    *terms* is the vocabulary in the document's row order. Subfields become
    nodes ``s<id>`` and concepts nodes ``c<id>`` (or the ids a ``theme_node`` key
    carries), named from ``label`` (the reference language) and
    ``label_<lang>``, in the document's order; level names are the defaults
    unless the document carries the tree's. Every keyword of *terms* ends up
    placed or set aside:

    - a merge variant is set aside (merges now live in ``keywords.csv``) and
      listed in :attr:`Imported.merges`;
    - the keywords of a subfield not kept, of stashed or trashed items, and
      keywords no group holds are set aside with the reason;
    - the terms of a concept marked :data:`THEME_KEYWORDS_OF` go onto its
      subfield's node (a ride-along one with attribution ``0``);
    - in a concept the engine wrote (without a ``theme_node`` key), a
      subfield-only term goes onto its subfield's node: it counts toward the
      subfield only, as the status meant; in a concept :func:`to_curated`
      wrote, it stays on the concept's node with attribution ``1``;
    - other term statuses become attributions (:data:`STATUS_OF`), kept in the
      set-aside entry of a keyword set aside; a status naming no keyword of
      its concept is dropped;
    - pinned colours have no place in a tree and are dropped.

    Each case is listed in :attr:`Imported.notes`. A document that places a
    keyword twice, or names a term index outside *terms*, is refused.
    """
    _check_language(reference_language)
    _vocabulary(terms)
    claims = _Claims(terms)
    notes: list[str] = []
    meta = doc.get("theme_tree")
    tree_doc: dict[str, Any] = {
        "depth": 2,
        "levels": [{"names": names} for names in default_level_names(2)],
    }
    if isinstance(meta, Mapping):
        if meta.get("depth", 2) != 2:
            raise ValueError(f"the document carries a tree of depth {meta.get('depth')}, not 2")
        tree_doc.update({k: v for k, v in meta.items() if k != "attribution"})

    subfields = list(doc.get("subfields") or [])
    subfield_ids = [int(sf.get("id", i)) for i, sf in enumerate(subfields)]
    if len(set(subfield_ids)) != len(subfield_ids):
        raise ValueError("the document has two subfields with the same id")
    kept = {sid for sid, sf in zip(subfield_ids, subfields, strict=True) if sf.get("keep", True)}

    nodes: list[dict[str, Any]] = []
    subfield_node: dict[int, str] = {}
    pinned = 0
    for sid, sf in zip(subfield_ids, subfields, strict=True):
        if sid not in kept:
            continue
        carried = sf.get("theme_node") if isinstance(sf.get("theme_node"), Mapping) else {}
        node = {**carried, "id": carried.get("id") or f"s{sid}", "parent": None}
        node.setdefault("names", _names_from_labels(sf, reference_language))
        node.setdefault("order", len(subfield_node) + 1)
        nodes.append(node)
        subfield_node[sid] = node["id"]
        pinned += bool(sf.get("pinned_color"))
    dropped_groups = [sid for sid in subfield_ids if sid not in kept]

    keywords: dict[str, str] = {}
    attribution: dict[str, int] = {}
    aside: dict[str, dict[str, Any]] = {}
    merges: list[tuple[str, str]] = []
    concept_node: dict[int, str] = {}
    seen_concepts: set[int] = set()
    placed_in: dict[int, int] = {}
    not_kept = stale_statuses = broadened = 0

    def set_aside_as(term: str, origin: str | None, reason: str, n: int | None) -> None:
        aside[term] = {"from": origin, "reason": reason}
        if n is not None:
            aside[term]["attribution"] = n

    for i, c in enumerate(doc.get("concepts") or []):
        cid = int(c.get("id", i))
        if cid in seen_concepts:
            raise ValueError(f"the document has two concepts with id {cid}")
        seen_concepts.add(cid)
        sid = int(c.get("subfield_id", -1))
        if sid not in subfield_ids:
            raise ValueError(f"concept {cid} points to an unknown subfield {sid}")
        where = f"concept {cid}"
        status = _status_reader(c)
        if sid not in kept:
            for row in c.get("term_indices") or []:
                term = claims.take(row, where)
                set_aside_as(term, None, "its group was not kept", status(term))
                not_kept += 1
            continue
        theme = subfield_node[sid]
        carried = c.get("theme_node") if isinstance(c.get("theme_node"), Mapping) else {}
        if c.get(THEME_KEYWORDS_OF):  # the subfield's own keywords
            concept_node[cid] = theme
            node_id: str = theme
        else:
            placed_in[sid] = placed_in.get(sid, 0) + 1
            node = {**carried, "id": carried.get("id") or f"c{cid}", "parent": theme}
            node.setdefault("names", _names_from_labels(c, reference_language))
            node.setdefault("order", placed_in[sid])
            nodes.append(node)
            concept_node[cid] = node_id = node["id"]
        # a concept this module wrote carries its node; one the engine wrote does not
        written_here = bool(carried)
        canonical_of = {int(x): int(g[0]) for g in c.get("term_merges") or [] for x in list(g)[1:]}
        mine: list[str] = []
        for row in c.get("term_indices") or []:
            term = claims.take(row, where)
            if int(row) in canonical_of:
                target = terms[canonical_of[int(row)]]
                set_aside_as(term, node_id, f"merge variant of {target!r}", None)
                merges.append((term, target))
                continue
            mine.append(term)
            n = status(term)
            if node_id == theme:
                keywords[term] = theme
                if n == 0:
                    attribution[term] = 0
            elif n == 1 and not written_here:
                keywords[term] = theme  # a subfield-only term of the engine: the subfield's
                broadened += 1
            else:
                keywords[term] = node_id
                if n is not None:
                    attribution[term] = n
        named = {_norm(t) for t in mine}
        stale_statuses += sum(
            1 for key in _LIST_OF.values() for t in c.get(key) or [] if _norm(t) not in named
        )

    held_aside = {"stashed": 0, "trashed": 0}
    for label in ("stashed", "trashed"):
        section = doc.get("stash" if label == "stashed" else "trash") or {}
        reason = f"{label} in the curated document"
        for entry in section.get("terms") or []:
            unit = entry.get("merge_group") or [entry.get("term_index")]
            n = _ATTRIBUTION_OF.get(entry.get("status") or "defining")
            for row in unit:
                term = claims.take(row, f"the {label} keywords")
                carried = entry.get("set_aside")
                if isinstance(carried, Mapping):
                    set_aside_as(term, carried.get("from"), carried.get("reason") or "", n)
                    aside[term].update({k: v for k, v in carried.items() if k not in aside[term]})
                else:
                    origin = concept_node.get(int(entry.get("origin_concept_id", -1)))
                    set_aside_as(term, origin, reason, n)
                    held_aside[label] += 1
        held = [e.get("concept") or {} for e in section.get("concepts") or []]
        held += [c for e in section.get("subfields") or [] for c in e.get("concepts") or []]
        for c in held:
            status = _status_reader(c)
            for row in c.get("term_indices") or []:
                term = claims.take(row, f"the {label} groups")
                set_aside_as(term, None, reason, status(term))
                held_aside[label] += 1

    ungrouped = [t for t in terms if t not in claims.where]
    for t in ungrouped:
        aside[t] = {"from": None, "reason": NOT_GROUPED}

    review = dict(tree_doc.get("review") or {})
    stale = sorted(k for k in review if k not in keywords and k not in aside)
    for k in stale:
        del review[k]
    tree_doc.update(
        nodes=nodes, keywords=keywords, attribution=attribution, set_aside=aside, review=review
    )
    tree = canonical(ThemesFile.model_validate(tree_doc))

    if dropped_groups:
        notes.append(
            f"{_plural(len(dropped_groups), 'group')} not kept ({dropped_groups[:10]}): "
            f"{_plural(not_kept, 'keyword')} set aside"
        )
    if merges:
        notes.append(
            f"{_plural(len(merges), 'merge variant')} set aside (merges belong in keywords.csv)"
        )
    for label, n in held_aside.items():
        if n:
            notes.append(f"{_plural(n, 'keyword')} {label} in the document: set aside")
    if ungrouped:
        notes.append(f"{_plural(len(ungrouped), 'keyword')} in no group: set aside")
    if broadened:
        notes.append(
            f"{_plural(broadened, 'subfield-only term')} placed on {'its' if broadened == 1 else 'their'} "
            "subfield's node"
        )
    if stale_statuses:
        what = "term status" if stale_statuses == 1 else "term statuses"
        notes.append(f"{stale_statuses} {what} naming no keyword of their concept dropped")
    if pinned:
        notes.append(f"pinned colours of {_plural(pinned, 'subfield')} dropped")
    if stale:
        notes.append(
            f"review states of {_plural(len(stale), 'keyword')} the tree does not hold dropped"
        )
    return Imported(tree, tuple(notes), tuple(sorted(merges)))
