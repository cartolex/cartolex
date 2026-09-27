# SPDX-License-Identifier: MIT
"""The theme tree of ``decisions/themes.json``: operations, rebase and comparison.

A tree (:class:`~cartolex.project.models.ThemesFile`) has 1 to 4 levels of
nodes above the keywords; keywords hang under nodes of the deepest level, or
are set aside. Everything here is a pure function: it takes a tree and returns
a new one, never changing its argument, and every tree it returns passes the
``ThemesFile`` checks.

- **Operations** (:func:`rename_node`, :func:`move_keywords`, :func:`merge_nodes`,
  :func:`split_node`, :func:`insert_level`…) return an :class:`Edit`: the new
  tree and a short description of the change, used for undo and redo and as the
  action name of the saved version.
- **Rebase** (:func:`rebase`) carries a tree onto the vocabulary of a new build
  and returns the changes it made (the reconciliation list).
- **Comparison** (:func:`compare`) lists every keyword and node that differs
  between two trees; the reconciliation of a rebase is the comparison of the
  tree before and after it.

The rules are described in ``docs/dev/themes.md``; the file in
``docs/format/decisions.md``. Saving and versions are in
:mod:`cartolex.project.themes_versions`, the converters to and from the
engine's two-level curated document in :mod:`cartolex.project.themes_curated`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .models import LANGUAGES, ThemeNode, ThemesFile

__all__ = [
    "CHANGE_KINDS",
    "MAX_DEPTH",
    "NEW_WITHOUT_PLACE",
    "REVIEW_STATES",
    "SET_ASIDE",
    "Change",
    "Edit",
    "Rebased",
    "ThemeEditError",
    "canonical",
    "children",
    "compare",
    "create_node",
    "default_level_names",
    "delete_node",
    "insert_level",
    "keywords_under",
    "merge_nodes",
    "move_keywords",
    "move_node",
    "new_tree",
    "node_level",
    "put_back",
    "rebase",
    "remove_level",
    "rename_level",
    "rename_node",
    "set_aside",
    "set_review",
    "split_node",
    "vocabulary_fingerprint",
    "vocabulary_gaps",
    "vocabulary_of",
]

MAX_DEPTH = 4

#: The review states of a keyword (absent: nothing to check).
REVIEW_STATES = ("to_check", "reviewed")

#: The place of a set-aside keyword in a :class:`Change` (never a valid node id).
SET_ASIDE = "(set aside)"

#: The reason given to a new keyword set aside by a rebase because no place was proposed.
NEW_WITHOUT_PLACE = "new keyword, no place proposed"

_THEME = {"en": "Theme", "fr": "Thème", "pt": "Tema"}
_TOPIC = {"en": "Topic", "fr": "Sujet", "pt": "Tópico"}
_FIELD = {"en": "Field", "fr": "Champ", "pt": "Área"}
_DOMAIN = {"en": "Domain", "fr": "Domaine", "pt": "Domínio"}

#: Default level names by depth, from the top level down.
_DEFAULT_LEVELS: dict[int, tuple[dict[str, str], ...]] = {
    1: (_THEME,),
    2: (_THEME, _TOPIC),
    3: (_FIELD, _THEME, _TOPIC),
    4: (_DOMAIN, _FIELD, _THEME, _TOPIC),
}

_NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_AUTO_ID = re.compile(r"^n([1-9][0-9]*)$")


class ThemeEditError(ValueError):
    """An operation was refused: the tree is unchanged and the message says why."""


@dataclass(frozen=True)
class Edit:
    """The result of an operation: the new tree and a short description of the change.

    The description is plain English and short enough to name a saved version
    (``move 3 keywords to n7``); it is also what an undo list shows.
    """

    tree: ThemesFile
    description: str


#: The kinds of :class:`Change`.
CHANGE_KINDS = (
    "depth",
    "level_renamed",
    "node_added",
    "node_removed",
    "node_renamed",
    "node_moved",
    "node_reordered",
    "node_changed",
    "added",
    "removed",
    "moved",
    "set_aside_changed",
    "review",
)


@dataclass(frozen=True)
class Change:
    """One difference between two trees: a keyword, a node or a level that changed.

    ``kind`` is one of :data:`CHANGE_KINDS`. For keywords, ``before`` and
    ``after`` are places: a node id, or :data:`SET_ASIDE`. ``added`` has only
    ``after``, ``removed`` only ``before``. For nodes, ``node_added`` and
    ``node_removed`` give the parent, ``node_moved`` the old and new parent,
    ``node_reordered`` the old and new order, ``node_renamed`` the old and new
    names. ``review`` gives the old and new review states (``None``: none).
    """

    kind: str
    keyword: str | None = None
    node: str | None = None
    level: int | None = None
    before: Any = None
    after: Any = None

    def as_dict(self) -> dict[str, Any]:
        """The change as a JSON-ready mapping, without its empty fields."""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class Rebased:
    """The result of :func:`rebase`: the new tree, its description and the reconciliation list."""

    tree: ThemesFile
    description: str
    changes: tuple[Change, ...]


# ── defaults ─────────────────────────────────────────────────────────────────


def default_level_names(depth: int) -> list[dict[str, str]]:
    """The default names of a tree's levels at *depth* (1–4), from the top level down.

    Theme; Theme › Topic; Field › Theme › Topic; Domain › Field › Theme › Topic,
    in English, French and Portuguese.
    """
    _check_depth(depth)
    return [dict(names) for names in _DEFAULT_LEVELS[depth]]


def new_tree(depth: int = 2) -> ThemesFile:
    """An empty tree of *depth* levels, named with the defaults."""
    _check_depth(depth)
    return ThemesFile(
        depth=depth, levels=[{"names": names} for names in default_level_names(depth)]
    )


def _check_depth(depth: int) -> None:
    if not isinstance(depth, int) or not 1 <= depth <= MAX_DEPTH:
        raise ThemeEditError(f"a tree has 1 to {MAX_DEPTH} levels, not {depth!r}")


# ── reading a tree ───────────────────────────────────────────────────────────


def vocabulary_of(tree: ThemesFile) -> set[str]:
    """Every keyword the tree holds: placed under a node or set aside."""
    return set(tree.keywords) | set(tree.set_aside)


def vocabulary_fingerprint(vocabulary: Iterable[str]) -> str:
    """``sha256:<hex>`` of a vocabulary: the JSON list of its distinct keywords, sorted.

    The same set of keyword texts always gives the same fingerprint, whatever
    their order; it is what ``based_on.vocabulary`` records.
    """
    text = json.dumps(sorted(set(vocabulary)), ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def vocabulary_gaps(tree: ThemesFile, vocabulary: Iterable[str]) -> tuple[list[str], list[str]]:
    """Compare a tree with a vocabulary: ``(missing, extra)``, both sorted.

    *missing* are keywords of the vocabulary the tree neither places nor sets
    aside; *extra* are keywords of the tree the vocabulary does not have. A tree
    rebased on the vocabulary has neither.
    """
    vocab = set(vocabulary)
    held = vocabulary_of(tree)
    return sorted(vocab - held), sorted(held - vocab)


def node_level(tree: ThemesFile, node_id: str) -> int:
    """The level of a node: 1 for a top-level node, ``tree.depth`` for a node that holds keywords."""
    return _Work(tree).level(node_id)


def children(tree: ThemesFile, parent: str | None) -> list[ThemeNode]:
    """The children of *parent* (``None``: the top-level nodes), in their order."""
    work = _Work(tree)
    if parent is not None:
        work.node(parent)
    by_id = {n.id: n for n in tree.nodes}
    return [by_id[i] for i in work.children(parent)]


def keywords_under(tree: ThemesFile, node_id: str) -> list[str]:
    """The keywords placed anywhere under *node_id*, sorted."""
    work = _Work(tree)
    work.node(node_id)
    under = set(work.subtree(node_id))
    return sorted(k for k, n in tree.keywords.items() if n in under)


def canonical(tree: ThemesFile) -> ThemesFile:
    """The same tree in its canonical form, the form every operation returns.

    Nodes are listed in tree order (depth first; siblings by ``order``, then
    id); keywords, set-aside keywords and review states are sorted by text;
    names by language. Nothing else changes.
    """
    return _Work(tree).finish()


# ── the working copy ─────────────────────────────────────────────────────────


class _Work:
    """A mutable working copy of a tree, as plain JSON values."""

    def __init__(self, tree: ThemesFile) -> None:
        doc = tree.model_dump(mode="json", by_alias=True)
        self.doc = doc
        self.depth: int = doc["depth"]
        self.nodes: dict[str, dict[str, Any]] = {n["id"]: n for n in doc["nodes"]}
        self.keywords: dict[str, str] = doc["keywords"]
        self.set_aside: dict[str, dict[str, Any]] = doc["set_aside"]
        self.review: dict[str, str] = doc["review"]

    # ── lookups ──
    def node(self, node_id: str) -> dict[str, Any]:
        try:
            return self.nodes[node_id]
        except KeyError:
            raise ThemeEditError(f"no node {node_id!r} in the tree") from None

    def level(self, node_id: str) -> int:
        lv, node = 1, self.node(node_id)
        while node["parent"] is not None:
            lv, node = lv + 1, self.nodes[node["parent"]]
        return lv

    def leaf(self, node_id: str) -> dict[str, Any]:
        """A node of the deepest level (one that can hold keywords)."""
        node = self.node(node_id)
        if self.level(node_id) != self.depth:
            raise ThemeEditError(
                f"node {node_id!r} is on level {self.level(node_id)}; keywords go under "
                f"nodes of the deepest level ({self.depth})"
            )
        return node

    def children(self, parent: str | None) -> list[str]:
        kids = [n for n in self.nodes.values() if n["parent"] == parent]
        kids.sort(key=lambda n: (n["order"], n["id"]))
        return [n["id"] for n in kids]

    def subtree(self, node_id: str) -> list[str]:
        """*node_id* and every node under it."""
        out, todo = [], [node_id]
        while todo:
            nid = todo.pop()
            out.append(nid)
            todo.extend(self.children(nid))
        return out

    def at_level(self, level: int) -> list[str]:
        """The nodes of *level*, in tree order."""
        return [nid for nid in self.tree_order() if self.level(nid) == level]

    def keywords_of(self, node_id: str) -> list[str]:
        return [k for k, n in self.keywords.items() if n == node_id]

    def tree_order(self) -> list[str]:
        kids: dict[str | None, list[dict[str, Any]]] = {}
        for n in self.nodes.values():
            kids.setdefault(n["parent"], []).append(n)
        for group in kids.values():
            group.sort(key=lambda n: (n["order"], n["id"]))
        out: list[str] = []

        def walk(parent: str | None) -> None:
            for n in kids.get(parent, []):
                out.append(n["id"])
                walk(n["id"])

        walk(None)
        return out

    def subtree_counts(self) -> dict[str, int]:
        """Number of keywords placed under each node, its descendants included."""
        counts = dict.fromkeys(self.nodes, 0)
        for node_id in self.keywords.values():
            nid: str | None = node_id
            while nid is not None:
                counts[nid] += 1
                nid = self.nodes[nid]["parent"]
        return counts

    # ── changes ──
    def new_id(self) -> str:
        """A fresh ``n<k>`` id, also unused by set-aside origins (so a put back never lands elsewhere)."""
        taken = set(self.nodes) | {e.get("from") for e in self.set_aside.values()}
        top = max((int(m.group(1)) for i in taken if i and (m := _AUTO_ID.match(i))), default=0)
        return f"n{top + 1}"

    def take_id(self, node_id: str | None) -> str:
        if node_id is None:
            return self.new_id()
        if not isinstance(node_id, str) or not _NODE_ID.match(node_id):
            raise ThemeEditError(f"{node_id!r} is not a valid node id (letters, digits, - and _)")
        if node_id in self.nodes:
            raise ThemeEditError(f"node id {node_id!r} is already used")
        return node_id

    def place(
        self, node_id: str, parent: str | None, position: int | None, *, after: str | None = None
    ) -> None:
        """Put *node_id* among the children of *parent*: at *position*, right after *after*, or last."""
        node = self.nodes[node_id]
        node["parent"] = parent
        siblings = [i for i in self.children(parent) if i != node_id]
        if position is None and after is None:
            node["order"] = max((self.nodes[i]["order"] for i in siblings), default=0) + 1
            return
        if after is not None:
            index = siblings.index(after) + 1
        else:
            assert position is not None
            index = max(0, min(int(position), len(siblings)))
        siblings.insert(index, node_id)
        for rank, sid in enumerate(siblings, start=1):
            self.nodes[sid]["order"] = rank

    def finish(self) -> ThemesFile:
        doc = self.doc
        doc["depth"] = self.depth
        order = self.tree_order()
        for n in self.nodes.values():
            n["names"] = _ordered_names(n.get("names") or {})
        doc["nodes"] = [self.nodes[i] for i in order]
        doc["keywords"] = dict(sorted(self.keywords.items()))
        doc["set_aside"] = dict(sorted(self.set_aside.items()))
        doc["review"] = dict(sorted(self.review.items()))
        try:
            return ThemesFile.model_validate(doc)
        except ValueError as exc:  # pragma: no cover - an operation broke an invariant
            raise ThemeEditError(f"the edit would make an invalid tree: {exc}") from exc

    def edit(self, description: str) -> Edit:
        return Edit(self.finish(), description)


def _ordered_names(names: Mapping[str, str]) -> dict[str, str]:
    return {lang: names[lang] for lang in LANGUAGES if lang in names}


def _merged_names(current: Mapping[str, str], names: Mapping[str, str | None]) -> dict[str, str]:
    """*current* with the languages of *names* set; ``None`` or blank removes a language."""
    if not isinstance(names, Mapping):
        raise ThemeEditError("names are given as {language: name}")
    out = dict(current)
    for lang, name in names.items():
        if lang not in LANGUAGES:
            raise ThemeEditError(f"unknown language {lang!r}; known: {list(LANGUAGES)}")
        if name is None or not str(name).strip():
            out.pop(lang, None)
        else:
            out[lang] = str(name).strip()
    return _ordered_names(out)


def _new_names(names: Mapping[str, str | None] | None, what: str) -> dict[str, str]:
    out = _merged_names({}, names or {})
    if not out:
        raise ThemeEditError(f"{what} needs a name in at least one language")
    return out


def _keyword_list(keywords: Iterable[str] | str) -> list[str]:
    if isinstance(keywords, str):
        keywords = [keywords]
    out = list(dict.fromkeys(keywords))
    if not out:
        raise ThemeEditError("no keyword given")
    return out


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _sample(values: Iterable[str], n: int = 5) -> str:
    values = sorted(values)
    shown = ", ".join(repr(v) for v in values[:n])
    return shown + (f" and {len(values) - n} more" if len(values) > n else "")


# ── operations ───────────────────────────────────────────────────────────────


def rename_node(tree: ThemesFile, node_id: str, names: Mapping[str, str | None]) -> Edit:
    """Set a node's name in each language of *names*; ``None`` or a blank name removes that language."""
    work = _Work(tree)
    node = work.node(node_id)
    node["names"] = _merged_names(node.get("names") or {}, names)
    return work.edit(f"rename {node_id}")


def rename_level(tree: ThemesFile, level: int, names: Mapping[str, str | None]) -> Edit:
    """Set the name of *level* (1 = top) in each language of *names*; a level keeps at least one."""
    work = _Work(tree)
    if not isinstance(level, int) or not 1 <= level <= work.depth:
        raise ThemeEditError(f"the tree has levels 1 to {work.depth}, not {level!r}")
    entry = work.doc["levels"][level - 1]
    merged = _merged_names(entry["names"], names)
    if not merged:
        raise ThemeEditError("a level keeps a name in at least one language")
    entry["names"] = merged
    return work.edit(f"rename level {level}")


def move_keywords(tree: ThemesFile, keywords: Iterable[str] | str, node_id: str) -> Edit:
    """Move placed keywords under *node_id*, a node of the deepest level.

    A set-aside keyword is not moved: :func:`put_back` places it.
    """
    work = _Work(tree)
    work.leaf(node_id)
    terms = _keyword_list(keywords)
    aside = [k for k in terms if k in work.set_aside]
    if aside:
        raise ThemeEditError(f"set aside, put back instead: {_sample(aside)}")
    unknown = [k for k in terms if k not in work.keywords]
    if unknown:
        raise ThemeEditError(f"not in the tree: {_sample(unknown)}")
    for k in terms:
        work.keywords[k] = node_id
    return work.edit(f"move {_count(len(terms), 'keyword')} to {node_id}")


def move_node(
    tree: ThemesFile, node_id: str, parent: str | None, position: int | None = None
) -> Edit:
    """Move a node, with everything under it, under *parent* (``None``: the top level).

    A node keeps its level: *parent* is on the level just above it. *position*
    is its 0-based place among its new siblings (default: last); moving a node
    within its parent needs a position.
    """
    work = _Work(tree)
    node = work.node(node_id)
    lv = work.level(node_id)
    if parent is None:
        if lv != 1:
            raise ThemeEditError(f"node {node_id!r} is on level {lv}; only level 1 is on top")
    else:
        work.node(parent)
        if work.level(parent) != lv - 1:
            raise ThemeEditError(
                f"node {node_id!r} is on level {lv}; its parent must be on level {lv - 1}"
            )
    if parent == node["parent"] and position is None:
        raise ThemeEditError(f"node {node_id!r} is already there; give a position to reorder it")
    same = parent == node["parent"]
    work.place(node_id, parent, position)
    if same:
        return work.edit(f"reorder {node_id}")
    return work.edit(f"move {node_id} under {parent}" if parent else f"move {node_id} to the top")


def merge_nodes(tree: ThemesFile, source: str, target: str) -> Edit:
    """Merge *source* into *target*, two nodes of the same level.

    What *source* holds (its child nodes, or its keywords) moves under *target*,
    after what *target* already holds; *source* is removed. Set-aside keywords
    that came from *source* now come from *target*.
    """
    work = _Work(tree)
    work.node(source)
    work.node(target)
    if source == target:
        raise ThemeEditError("a node cannot be merged into itself")
    if work.level(source) != work.level(target):
        raise ThemeEditError(
            f"{source!r} (level {work.level(source)}) and {target!r} "
            f"(level {work.level(target)}) are not on the same level"
        )
    for k in work.keywords_of(source):
        work.keywords[k] = target
    for kid in work.children(source):
        work.place(kid, target, None)
    for entry in work.set_aside.values():
        if entry.get("from") == source:
            entry["from"] = target
    del work.nodes[source]
    return work.edit(f"merge {source} into {target}")


def split_node(
    tree: ThemesFile,
    node_id: str,
    parts: Sequence[tuple[Iterable[str], Mapping[str, str | None]]],
    ids: Sequence[str] | None = None,
) -> Edit:
    """Split a node by a partition of what it holds.

    Each part is ``(members, names)``: the members (keywords, for a node of the
    deepest level; child node ids otherwise) go to a new sibling node with those
    names, placed right after *node_id*, in the order of *parts*. The members no
    part names stay in *node_id*, which must keep at least one. *ids* optionally
    names the new nodes.
    """
    work = _Work(tree)
    node = work.node(node_id)
    leaf = work.level(node_id) == work.depth
    held = work.keywords_of(node_id) if leaf else work.children(node_id)
    what = "keyword" if leaf else "node"
    if not parts:
        raise ThemeEditError("a split needs at least one part")
    if ids is not None and len(ids) != len(parts):
        raise ThemeEditError(f"{len(ids)} id(s) given for {len(parts)} part(s)")
    seen: set[str] = set()
    groups: list[list[str]] = []
    for members, _names in parts:
        group = [members] if isinstance(members, str) else list(dict.fromkeys(members))
        if not group:
            raise ThemeEditError("each part of a split holds at least one member")
        stray = [m for m in group if m not in held]
        if stray:
            raise ThemeEditError(f"not held by {node_id!r}: {_sample(stray)}")
        twice = [m for m in group if m in seen]
        if twice:
            raise ThemeEditError(f"in two parts of the split: {_sample(twice)}")
        seen.update(group)
        groups.append(group)
    if len(seen) == len(held):
        raise ThemeEditError(f"a split leaves at least one {what} in {node_id!r}")
    previous = node_id
    for i, ((_members, names), group) in enumerate(zip(parts, groups, strict=True)):
        new_id = work.take_id(ids[i] if ids is not None else None)
        work.nodes[new_id] = {
            "id": new_id,
            "parent": node["parent"],
            "names": _new_names(names, "a part of a split"),
            "order": 0,
        }
        work.place(new_id, node["parent"], None, after=previous)
        previous = new_id
        if leaf:
            for k in group:
                work.keywords[k] = new_id
        else:
            for rank, kid in enumerate([k for k in held if k in set(group)], start=1):
                work.nodes[kid]["parent"] = new_id
                work.nodes[kid]["order"] = rank
    return work.edit(f"split {node_id} into {len(parts) + 1}")


def create_node(
    tree: ThemesFile,
    parent: str | None,
    names: Mapping[str, str | None],
    *,
    node_id: str | None = None,
    position: int | None = None,
) -> Edit:
    """Create an empty node under *parent* (``None``: on the top level).

    *position* is its 0-based place among its siblings (default: last); the id
    is ``n<k>``, the next free number, unless *node_id* is given.
    """
    work = _Work(tree)
    if parent is not None:
        work.node(parent)
        if work.level(parent) >= work.depth:
            raise ThemeEditError(
                f"node {parent!r} is on the deepest level ({work.depth}); it holds keywords, not nodes"
            )
    new_id = work.take_id(node_id)
    work.nodes[new_id] = {
        "id": new_id,
        "parent": parent,
        "names": _new_names(names, "a new node"),
        "order": 0,
    }
    work.place(new_id, parent, position)
    return work.edit(f"create {new_id}")


def delete_node(tree: ThemesFile, node_id: str) -> Edit:
    """Delete an empty node (no child nodes, no keywords).

    Set-aside keywords that came from it keep that origin: putting them back
    then needs a target.
    """
    work = _Work(tree)
    work.node(node_id)
    if work.children(node_id) or work.keywords_of(node_id):
        raise ThemeEditError(
            f"node {node_id!r} is not empty: move, merge or set aside what it holds first"
        )
    del work.nodes[node_id]
    return work.edit(f"delete {node_id}")


def set_aside(tree: ThemesFile, keywords: Iterable[str] | str, reason: str = "") -> Edit:
    """Set keywords aside, each with the node it came from and *reason*.

    For a keyword already set aside, only the reason changes.
    """
    work = _Work(tree)
    terms = _keyword_list(keywords)
    unknown = [k for k in terms if k not in work.keywords and k not in work.set_aside]
    if unknown:
        raise ThemeEditError(f"not in the tree: {_sample(unknown)}")
    reason = str(reason or "").strip()
    for k in terms:
        if k in work.keywords:
            work.set_aside[k] = {"from": work.keywords.pop(k), "reason": reason}
        else:
            work.set_aside[k]["reason"] = reason
    return work.edit(f"set aside {_count(len(terms), 'keyword')}")


def put_back(tree: ThemesFile, keywords: Iterable[str] | str, node_id: str | None = None) -> Edit:
    """Put set-aside keywords back: under *node_id*, or where each came from."""
    work = _Work(tree)
    terms = _keyword_list(keywords)
    unknown = [k for k in terms if k not in work.set_aside]
    if unknown:
        raise ThemeEditError(f"not set aside: {_sample(unknown)}")
    if node_id is not None:
        work.leaf(node_id)
    targets: dict[str, str] = {}
    lost: list[str] = []
    for k in terms:
        target = node_id or work.set_aside[k].get("from")
        if target is None or target not in work.nodes or work.level(target) != work.depth:
            lost.append(k)
        else:
            targets[k] = target
    if lost:
        raise ThemeEditError(f"no place to put back (name a target node): {_sample(lost)}")
    for k, target in targets.items():
        del work.set_aside[k]
        work.keywords[k] = target
    return work.edit(f"put back {_count(len(terms), 'keyword')}")


def set_review(tree: ThemesFile, keywords: Iterable[str] | str, state: str | None) -> Edit:
    """Set the review state of keywords: ``"to_check"``, ``"reviewed"``, or ``None`` (none)."""
    if state is not None and state not in REVIEW_STATES:
        raise ThemeEditError(f"unknown review state {state!r}; known: {list(REVIEW_STATES)}")
    work = _Work(tree)
    terms = _keyword_list(keywords)
    unknown = [k for k in terms if k not in work.keywords and k not in work.set_aside]
    if unknown:
        raise ThemeEditError(f"not in the tree: {_sample(unknown)}")
    for k in terms:
        if state is None:
            work.review.pop(k, None)
        else:
            work.review[k] = state
    n = _count(len(terms), "keyword")
    if state is None:
        return work.edit(f"clear the review of {n}")
    return work.edit(f"mark {n} {state.replace('_', ' ')}")


# ── depth ────────────────────────────────────────────────────────────────────


def _relevel(
    levels: list[dict[str, Any]], old_depth: int, new_depth: int, source: list[int | None]
) -> list[dict[str, Any]]:
    """The levels of a tree whose depth changes: *source* maps each new level to an old one.

    A new level takes the default name of its place. An old level keeps the
    names someone gave it; a name still equal to the default of its old place
    becomes the default of its new place.
    """
    old_defaults, new_defaults = _DEFAULT_LEVELS[old_depth], _DEFAULT_LEVELS[new_depth]
    out: list[dict[str, Any]] = []
    for place, old in enumerate(source):
        if old is None:
            out.append({"names": dict(new_defaults[place])})
            continue
        level = dict(levels[old])
        level["names"] = {
            lang: new_defaults[place].get(lang, name)
            if name == old_defaults[old].get(lang)
            else name
            for lang, name in level["names"].items()
        }
        out.append(level)
    return out


def insert_level(
    tree: ThemesFile, at: int, root_names: Mapping[str, str | None] | None = None
) -> Edit:
    """Add a level at position *at* (1 = new top level, ``depth + 1`` = new bottom level).

    Every node of level ``at - 1`` gets one new child, named like it, that takes
    over everything it held (its child nodes, or its keywords); at the top, one
    new root takes over every top-level node (named *root_names*, or the new
    level's name). Set-aside keywords that came from a node of the old deepest
    level now come from its new child. The tree is one level deeper and every
    keyword keeps its path; the level names follow :func:`_relevel`.
    """
    work = _Work(tree)
    depth = work.depth
    if depth >= MAX_DEPTH:
        raise ThemeEditError(f"the tree already has {MAX_DEPTH} levels")
    if not isinstance(at, int) or not 1 <= at <= depth + 1:
        raise ThemeEditError(f"a level is inserted at a position from 1 to {depth + 1}, not {at!r}")
    new_depth = depth + 1
    if at == 1:
        roots = work.children(None)
        if roots:
            rid = work.new_id()
            names = (
                _new_names(root_names, "the new root")
                if root_names
                else dict(_DEFAULT_LEVELS[new_depth][0])
            )
            work.nodes[rid] = {"id": rid, "parent": None, "names": names, "order": 1}
            for r in roots:
                work.nodes[r]["parent"] = rid
    else:
        if root_names:
            raise ThemeEditError(
                "root_names only names the new root of a level inserted at the top"
            )
        for pid in work.at_level(at - 1):
            parent = work.nodes[pid]
            cid = work.new_id()
            held_nodes = work.children(pid)
            held_keywords = work.keywords_of(pid) if at - 1 == depth else []
            work.nodes[cid] = {
                "id": cid,
                "parent": pid,
                "names": dict(parent.get("names") or {}),
                "order": 1,
            }
            for kid in held_nodes:
                work.nodes[kid]["parent"] = cid
            for k in held_keywords:
                work.keywords[k] = cid
            if at - 1 == depth:
                for entry in work.set_aside.values():
                    if entry.get("from") == pid:
                        entry["from"] = cid
    source: list[int | None] = list(range(depth))
    source.insert(at - 1, None)
    work.doc["levels"] = _relevel(work.doc["levels"], depth, new_depth, source)
    work.depth = new_depth
    return work.edit(f"insert level {at}")


def remove_level(tree: ThemesFile, at: int) -> Edit:
    """Remove level *at* (1 = top): its nodes dissolve into their parents.

    What a removed node held (its child nodes, or its keywords) goes to its
    parent — or to the top level, when *at* is 1 — in the order of the removed
    nodes, then of what each held. Set-aside keywords that came from a removed
    node of the deepest level now come from its parent. The removed nodes'
    names are lost (the previous tree keeps them). The level names follow
    :func:`_relevel`.
    """
    work = _Work(tree)
    depth = work.depth
    if depth <= 1:
        raise ThemeEditError("a tree keeps at least one level")
    if not isinstance(at, int) or not 1 <= at <= depth:
        raise ThemeEditError(f"the tree has levels 1 to {depth}, not {at!r}")
    doomed = work.at_level(at)
    new_children: dict[str | None, list[str]] = {}
    for nid in doomed:
        parent = work.nodes[nid]["parent"]
        bucket = new_children.setdefault(parent, [])
        if at == depth:
            for k in work.keywords_of(nid):
                work.keywords[k] = parent  # at >= 2: a parent exists
            for entry in work.set_aside.values():
                if entry.get("from") == nid:
                    entry["from"] = parent
        else:
            bucket.extend(work.children(nid))
    for parent, kids in new_children.items():
        for rank, kid in enumerate(kids, start=1):
            work.nodes[kid]["parent"] = parent
            work.nodes[kid]["order"] = rank
    for nid in doomed:
        del work.nodes[nid]
    source: list[int | None] = [i for i in range(depth) if i != at - 1]
    work.doc["levels"] = _relevel(work.doc["levels"], depth, depth - 1, source)
    work.depth = depth - 1
    return work.edit(f"remove level {at}")


# ── rebase ───────────────────────────────────────────────────────────────────


def rebase(
    tree: ThemesFile,
    vocabulary: Iterable[str],
    proposals: Mapping[str, str | None],
    *,
    run: str | None = None,
) -> Rebased:
    """Carry *tree* onto a new vocabulary (the kept keywords of a new build).

    - a keyword the tree already holds stays where it is (placed, or set aside);
    - a new keyword goes to the node *proposals* names for it, marked
      ``to_check``; a proposal of ``None`` sets it aside instead (reason
      :data:`NEW_WITHOUT_PLACE`), also marked ``to_check``. Every new keyword
      needs an entry in *proposals*, naming a node of the deepest level:
      nothing is placed by default;
    - a keyword that vanished is removed, with its review state; a set-aside
      keyword that vanished is dropped;
    - a node whose subtree held keywords before and holds none after is removed,
      with the nodes under it. Nodes that were already empty stay.

    Nothing else changes: no node is renamed, moved or renumbered, and set-aside
    origins stay as they were (possibly naming a removed node). ``based_on``
    records *run* and the vocabulary's fingerprint. The reconciliation list
    (:attr:`Rebased.changes`) is :func:`compare` of the two trees: one entry per
    added or removed keyword and per removed node.
    """
    vocab: set[str] = set()
    for k in vocabulary:
        if not isinstance(k, str) or not k.strip():
            raise ThemeEditError(f"a keyword is a non-empty text, not {k!r}")
        vocab.add(k)
    work = _Work(tree)
    held = set(work.keywords) | set(work.set_aside)
    new = sorted(vocab - held)
    unproposed = [k for k in new if k not in proposals]
    if unproposed:
        raise ThemeEditError(
            f"{_count(len(unproposed), 'new keyword')} without a proposal "
            f"(give a node, or None to set it aside): {_sample(unproposed)}"
        )
    bad = [
        k
        for k in new
        if proposals[k] is not None
        and (proposals[k] not in work.nodes or work.level(proposals[k]) != work.depth)
    ]
    if bad:
        raise ThemeEditError(
            "proposed places that are not nodes of the deepest level: "
            + ", ".join(f"{k!r} → {proposals[k]!r}" for k in bad[:5])
        )
    before = work.subtree_counts()
    for k in [k for k in work.keywords if k not in vocab]:
        del work.keywords[k]
    for k in [k for k in work.set_aside if k not in vocab]:
        del work.set_aside[k]
    for k in [k for k in work.review if k not in vocab]:
        del work.review[k]
    for k in new:
        place = proposals[k]
        if place is None:
            work.set_aside[k] = {"from": None, "reason": NEW_WITHOUT_PLACE}
        else:
            work.keywords[k] = place
        work.review[k] = "to_check"
    after = work.subtree_counts()
    emptied = {n for n in work.nodes if before[n] > 0 and after[n] == 0}
    doomed = {d for n in emptied for d in work.subtree(n)}
    for nid in doomed:
        del work.nodes[nid]
    work.doc["based_on"] = {"run": run, "vocabulary": vocabulary_fingerprint(vocab)}
    result = work.finish()
    changes = compare(tree, result)
    if not changes and result.based_on == tree.based_on:
        return Rebased(tree, "rebase: no change", ())
    added = sum(c.kind == "added" for c in changes)
    removed = sum(c.kind == "removed" for c in changes)
    nodes = sum(c.kind == "node_removed" for c in changes)
    description = f"rebase: +{added} -{removed} keywords"
    if nodes:
        description += f", -{_count(nodes, 'node')}"
    return Rebased(result, description, changes)


# ── comparison ───────────────────────────────────────────────────────────────


def _place(tree: ThemesFile, keyword: str) -> str | None:
    if keyword in tree.keywords:
        return tree.keywords[keyword]
    if keyword in tree.set_aside:
        return SET_ASIDE
    return None


def compare(before: ThemesFile, after: ThemesFile) -> tuple[Change, ...]:
    """Every difference between two trees, keyword by keyword and node by node.

    Levels come first, then nodes (by id), then keywords (by text). A node or a
    keyword appears once per aspect that changed (a node both renamed and moved
    appears twice). ``based_on`` is not compared.
    """
    out: list[Change] = []
    if before.depth != after.depth:
        out.append(Change("depth", before=before.depth, after=after.depth))
    for i, (lb, la) in enumerate(zip(before.levels, after.levels, strict=False), start=1):
        if lb != la:
            out.append(
                Change("level_renamed", level=i, before=dict(lb.names), after=dict(la.names))
            )
    nb = {n.id: n for n in before.nodes}
    na = {n.id: n for n in after.nodes}
    for nid in sorted(nb.keys() | na.keys()):
        b, a = nb.get(nid), na.get(nid)
        if b is None:
            assert a is not None
            out.append(Change("node_added", node=nid, after=a.parent))
            continue
        if a is None:
            out.append(Change("node_removed", node=nid, before=b.parent))
            continue
        if b == a:
            continue
        seen = False
        if b.names != a.names:
            out.append(Change("node_renamed", node=nid, before=dict(b.names), after=dict(a.names)))
            seen = True
        if b.parent != a.parent:
            out.append(Change("node_moved", node=nid, before=b.parent, after=a.parent))
            seen = True
        elif b.order != a.order:
            out.append(Change("node_reordered", node=nid, before=b.order, after=a.order))
            seen = True
        if not seen:
            out.append(Change("node_changed", node=nid))
    words = (
        set(before.keywords) | set(before.set_aside) | set(after.keywords) | set(after.set_aside)
    )
    for k in sorted(words):
        pb, pa = _place(before, k), _place(after, k)
        if pb is None:
            out.append(Change("added", keyword=k, after=pa))
            continue
        if pa is None:
            out.append(Change("removed", keyword=k, before=pb))
            continue
        if pb != pa:
            out.append(Change("moved", keyword=k, before=pb, after=pa))
        elif pb == SET_ASIDE and before.set_aside[k] != after.set_aside[k]:
            out.append(
                Change(
                    "set_aside_changed",
                    keyword=k,
                    before=before.set_aside[k].model_dump(mode="json", by_alias=True),
                    after=after.set_aside[k].model_dump(mode="json", by_alias=True),
                )
            )
        rb, ra = before.review.get(k), after.review.get(k)
        if rb != ra:
            out.append(Change("review", keyword=k, before=rb, after=ra))
    return tuple(out)
