# SPDX-License-Identifier: MIT
"""Converters between a depth-2 theme tree and the engine's two-level curated document.

The engine's apply stage (:func:`cartolex.lexicon.subfields.apply_subfield_files`)
reads a curated document with two levels: subfields, each holding concepts, each
holding term indices (rows of the lexical data). Until that stage reads a theme
tree itself, :func:`to_curated` writes a depth-2 tree in that format, and
:func:`from_curated` reads such a document — or the deterministic draft of the
grouping stage — into a tree.

The correspondence:

- nodes of level 1 are subfields, nodes of level 2 are concepts, and keyword
  texts are term indices through *terms*, the vocabulary in row order;
- ``label`` holds a node's name in the reference language, ``label_<lang>`` its
  other names;
- set-aside keywords are the document's trashed terms.

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

_SUBFIELD_ID = re.compile(r"^s(0|[1-9][0-9]*)$")
_CONCEPT_ID = re.compile(r"^c(0|[1-9][0-9]*)$")


@dataclass(frozen=True)
class Imported:
    """The result of :func:`from_curated`: the tree, and what it could not carry as it was."""

    tree: ThemesFile
    notes: tuple[str, ...]


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
    rows: dict[str, list[int]] = {}
    for keyword, node_id in tree.keywords.items():
        rows.setdefault(node_id, []).append(index[keyword])

    def rank(row: int) -> tuple[float, int]:
        return (-float(scores.get(terms[row], 0.0)), row) if scores else (0.0, row)

    concepts: list[dict[str, Any]] = []
    for node in second:
        indices = sorted(rows.get(node.id, []))
        concepts.append(
            {
                "id": concept_id[node.id],
                **_labels(node, reference_language),
                "subfield_id": subfield_id[node.parent],  # type: ignore[index]
                "term_indices": indices,
                "term_merges": [],
                "top_terms": [terms[i] for i in sorted(indices, key=rank)[:TOP_TERMS]],
                "theme_node": _carried(node),
            }
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
    trashed = [
        {
            "term_index": index[keyword],
            "term": keyword,
            "origin_concept_id": concept_id.get(entry.source, -1) if entry.source else -1,
            "merge_group": None,
            "status": "defining",
            "set_aside": entry.model_dump(mode="json", by_alias=True),
        }
        for keyword, entry in tree.set_aside.items()
    ]
    meta = tree.model_dump(mode="json", by_alias=True)
    for key in ("nodes", "keywords", "set_aside"):
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

    - a merge variant is set aside (merges now live in ``keywords.csv``);
    - the keywords of a subfield not kept, of stashed or trashed items, and
      keywords no group holds are set aside with the reason;
    - term statuses and pinned colours have no place in a tree and are dropped.

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
        tree_doc.update(meta)

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
    aside: dict[str, dict[str, Any]] = {}
    concept_node: dict[int, str] = {}
    seen_concepts: set[int] = set()
    placed_in: dict[int, int] = {}
    variants = statuses = not_kept = 0
    for i, c in enumerate(doc.get("concepts") or []):
        cid = int(c.get("id", i))
        if cid in seen_concepts:
            raise ValueError(f"the document has two concepts with id {cid}")
        seen_concepts.add(cid)
        sid = int(c.get("subfield_id", -1))
        if sid not in subfield_ids:
            raise ValueError(f"concept {cid} points to an unknown subfield {sid}")
        where = f"concept {cid}"
        if sid not in kept:
            for row in c.get("term_indices") or []:
                aside[claims.take(row, where)] = {"from": None, "reason": "its group was not kept"}
                not_kept += 1
            continue
        carried = c.get("theme_node") if isinstance(c.get("theme_node"), Mapping) else {}
        placed_in[sid] = placed_in.get(sid, 0) + 1
        node = {**carried, "id": carried.get("id") or f"c{cid}", "parent": subfield_node[sid]}
        node.setdefault("names", _names_from_labels(c, reference_language))
        node.setdefault("order", placed_in[sid])
        nodes.append(node)
        concept_node[cid] = node["id"]
        canonical_of = {int(x): int(g[0]) for g in c.get("term_merges") or [] for x in list(g)[1:]}
        for row in c.get("term_indices") or []:
            term = claims.take(row, where)
            if int(row) in canonical_of:
                reason = f"merge variant of {terms[canonical_of[int(row)]]!r}"
                aside[term] = {"from": node["id"], "reason": reason}
                variants += 1
            else:
                keywords[term] = node["id"]
        statuses += bool(c.get("subfield_only_terms") or c.get("ride_along_terms"))

    held_aside = {"stashed": 0, "trashed": 0}
    for label in ("stashed", "trashed"):
        section = doc.get("stash" if label == "stashed" else "trash") or {}
        reason = f"{label} in the curated document"
        for entry in section.get("terms") or []:
            unit = entry.get("merge_group") or [entry.get("term_index")]
            for row in unit:
                term = claims.take(row, f"the {label} keywords")
                if isinstance(entry.get("set_aside"), Mapping):
                    aside[term] = dict(entry["set_aside"])
                else:
                    origin = concept_node.get(int(entry.get("origin_concept_id", -1)))
                    aside[term] = {"from": origin, "reason": reason}
                    held_aside[label] += 1
        held = [e.get("concept") or {} for e in section.get("concepts") or []]
        held += [c for e in section.get("subfields") or [] for c in e.get("concepts") or []]
        for c in held:
            for row in c.get("term_indices") or []:
                aside[claims.take(row, f"the {label} groups")] = {"from": None, "reason": reason}
                held_aside[label] += 1

    ungrouped = [t for t in terms if t not in claims.where]
    for t in ungrouped:
        aside[t] = {"from": None, "reason": NOT_GROUPED}

    review = dict(tree_doc.get("review") or {})
    stale = sorted(k for k in review if k not in keywords and k not in aside)
    for k in stale:
        del review[k]
    tree_doc.update(nodes=nodes, keywords=keywords, set_aside=aside, review=review)
    tree = canonical(ThemesFile.model_validate(tree_doc))

    if dropped_groups:
        notes.append(
            f"{_plural(len(dropped_groups), 'group')} not kept ({dropped_groups[:10]}): "
            f"{_plural(not_kept, 'keyword')} set aside"
        )
    if variants:
        notes.append(
            f"{_plural(variants, 'merge variant')} set aside (merges belong in keywords.csv)"
        )
    for label, n in held_aside.items():
        if n:
            notes.append(f"{_plural(n, 'keyword')} {label} in the document: set aside")
    if ungrouped:
        notes.append(f"{_plural(len(ungrouped), 'keyword')} in no group: set aside")
    if statuses:
        notes.append(
            f"term statuses of {_plural(statuses, 'concept')} dropped: a theme tree has none"
        )
    if pinned:
        notes.append(f"pinned colours of {_plural(pinned, 'subfield')} dropped")
    if stale:
        notes.append(
            f"review states of {_plural(len(stale), 'keyword')} the tree does not hold dropped"
        )
    return Imported(tree, tuple(notes))
