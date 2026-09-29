# SPDX-License-Identifier: MIT
"""Operations on a theme tree held as a plain document (``cartolex-themes/1``).

The kit edits the tree where the project package cannot be imported (it
validates its documents with a library a code sandbox may lack), so these are
the project's operations (:mod:`cartolex.project.themes`) on plain
dictionaries, with the same rules: the carry rule of attributions, new node ids
``n<k>``, siblings ordered by ``order``. Each takes a document and returns a
new one, never changing its argument, and raises :class:`OpRefused` with the
reason when the operation cannot apply.

Every operation is also written in the JSON form of ``POST /api/themes/ops``
(:func:`apply`), the form a result carries back: the application replays it
with its own functions, so what the kit computed and what the project
records cannot differ silently.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = ["LANGUAGES", "OP_KINDS", "OpRefused", "apply", "level_of", "levels", "tree_order"]

LANGUAGES = ("en", "fr", "pt")
#: The operations a result may carry (the ``op`` of ``POST /api/themes/ops``).
OP_KINDS = (
    "rename_node",
    "move_keywords",
    "move_node",
    "merge_nodes",
    "split_node",
    "create_node",
    "delete_node",
    "set_aside",
    "put_back",
    "set_attribution",
)
_NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_AUTO_ID = re.compile(r"^n([1-9][0-9]*)$")


class OpRefused(ValueError):
    """An operation that cannot apply to the tree, with the reason."""


def tree_order(doc: Mapping[str, Any]) -> list[str]:
    """The node ids depth first, siblings by their ``order`` then id."""
    kids: dict[str | None, list[Mapping[str, Any]]] = {}
    for n in doc["nodes"]:
        kids.setdefault(n.get("parent"), []).append(n)
    for group in kids.values():
        group.sort(key=lambda n: (int(n.get("order", 0)), str(n["id"])))
    out: list[str] = []

    def walk(parent: str | None) -> None:
        for n in kids.get(parent, []):
            out.append(str(n["id"]))
            walk(str(n["id"]))

    walk(None)
    return out


def levels(doc: Mapping[str, Any]) -> dict[str, int]:
    """The level of every node (1 on top)."""
    parent = {n["id"]: n.get("parent") for n in doc["nodes"]}
    out: dict[str, int] = {}
    for nid in tree_order(doc):
        p = parent[nid]
        out[nid] = 1 if p is None else out[p] + 1
    return out


def level_of(doc: Mapping[str, Any], node_id: str) -> int:
    """The level of *node_id*."""
    return levels(doc)[node_id]


def _sample(values: Iterable[str], n: int = 5) -> str:
    values = sorted(values)
    shown = ", ".join(repr(v) for v in values[:n])
    return shown + (f" and {len(values) - n} more" if len(values) > n else "")


def _names(current: Mapping[str, str], names: Mapping[str, str | None]) -> dict[str, str]:
    if not isinstance(names, Mapping):
        raise OpRefused("names are given as {language: name}")
    out = dict(current)
    for lang, name in names.items():
        if lang not in LANGUAGES:
            raise OpRefused(f"unknown language {lang!r}; known: {list(LANGUAGES)}")
        if name is None or not str(name).strip():
            out.pop(lang, None)
        else:
            out[lang] = str(name).strip()
    return {lang: out[lang] for lang in LANGUAGES if lang in out}


def _new_names(names: Mapping[str, str | None] | None, what: str) -> dict[str, str]:
    out = _names({}, names or {})
    if not out:
        raise OpRefused(f"{what} needs a name in at least one language")
    return out


def _keywords(value: Iterable[str] | str) -> list[str]:
    out = list(dict.fromkeys([value] if isinstance(value, str) else value))
    if not out:
        raise OpRefused("no keyword given")
    return out


class _Work:
    """A working copy of a document."""

    def __init__(self, doc: Mapping[str, Any]) -> None:
        self.doc = copy.deepcopy(dict(doc))
        for key, empty in (
            ("keywords", {}),
            ("attribution", {}),
            ("set_aside", {}),
            ("review", {}),
        ):
            self.doc.setdefault(key, copy.deepcopy(empty))
        self.nodes: dict[str, dict[str, Any]] = {n["id"]: n for n in self.doc["nodes"]}
        self.keywords: dict[str, str] = self.doc["keywords"]
        self.attribution: dict[str, int] = self.doc["attribution"]
        self.aside: dict[str, dict[str, Any]] = self.doc["set_aside"]
        self.depth = int(self.doc["depth"])

    def node(self, node_id: str) -> dict[str, Any]:
        if node_id not in self.nodes:
            raise OpRefused(f"no node {node_id!r} in the tree")
        return self.nodes[node_id]

    def level(self, node_id: str) -> int:
        lv, node = 1, self.node(node_id)
        while node.get("parent") is not None:
            lv, node = lv + 1, self.nodes[node["parent"]]
        return lv

    def children(self, parent: str | None) -> list[str]:
        kids = [n for n in self.nodes.values() if n.get("parent") == parent]
        kids.sort(key=lambda n: (int(n.get("order", 0)), str(n["id"])))
        return [n["id"] for n in kids]

    def own(self, node_id: str) -> list[str]:
        return [k for k, n in self.keywords.items() if n == node_id]

    def relocate(self, keyword: str, node_id: str) -> None:
        if self.keywords.get(keyword) != node_id and self.attribution.get(keyword):
            del self.attribution[keyword]
        self.keywords[keyword] = node_id

    def new_id(self) -> str:
        taken = set(self.nodes) | {e.get("from") for e in self.aside.values()}
        top = max((int(m.group(1)) for i in taken if i and (m := _AUTO_ID.match(i))), default=0)
        return f"n{top + 1}"

    def take_id(self, node_id: str | None) -> str:
        if node_id is None:
            return self.new_id()
        if not isinstance(node_id, str) or not _NODE_ID.match(node_id):
            raise OpRefused(f"{node_id!r} is not a valid node id (letters, digits, - and _)")
        if node_id in self.nodes:
            raise OpRefused(f"node id {node_id!r} is already used")
        return node_id

    def place(
        self, node_id: str, parent: str | None, position: int | None, *, after: str | None = None
    ) -> None:
        node = self.nodes[node_id]
        node["parent"] = parent
        siblings = [i for i in self.children(parent) if i != node_id]
        if position is None and after is None:
            node["order"] = (
                max((int(self.nodes[i].get("order", 0)) for i in siblings), default=0) + 1
            )
            return
        index = (
            siblings.index(after) + 1
            if after is not None
            else max(0, min(int(position or 0), len(siblings)))
        )
        siblings.insert(index, node_id)
        for rank, sid in enumerate(siblings, start=1):
            self.nodes[sid]["order"] = rank

    def finish(self) -> dict[str, Any]:
        doc = self.doc
        order = tree_order({"nodes": list(self.nodes.values())})
        doc["nodes"] = [self.nodes[i] for i in order]
        doc["keywords"] = dict(sorted(self.keywords.items()))
        doc["attribution"] = dict(sorted(self.attribution.items()))
        doc["set_aside"] = dict(sorted(self.aside.items()))
        doc["review"] = dict(sorted((doc.get("review") or {}).items()))
        return doc


def apply(doc: Mapping[str, Any], op: Mapping[str, Any]) -> dict[str, Any]:
    """*doc* with the operation *op* (the JSON form of ``POST /api/themes/ops``) applied."""
    kind = op.get("op")
    if kind not in OP_KINDS:
        raise OpRefused(f"unknown operation {kind!r}; known: {', '.join(OP_KINDS)}")
    w = _Work(doc)
    try:
        _APPLY[kind](w, op)
    except KeyError as exc:
        raise OpRefused(f"{kind} misses the field {exc.args[0]!r}") from None
    return w.finish()


def _rename_node(w: _Work, op: Mapping[str, Any]) -> None:
    node = w.node(op["node_id"])
    node["names"] = _names(node.get("names") or {}, op["names"])


def _move_keywords(w: _Work, op: Mapping[str, Any]) -> None:
    w.node(op["node_id"])
    terms = _keywords(op["keywords"])
    aside = [k for k in terms if k in w.aside]
    if aside:
        raise OpRefused(f"set aside, put back instead: {_sample(aside)}")
    unknown = [k for k in terms if k not in w.keywords]
    if unknown:
        raise OpRefused(f"not in the tree: {_sample(unknown)}")
    for k in terms:
        w.relocate(k, op["node_id"])


def _move_node(w: _Work, op: Mapping[str, Any]) -> None:
    nid, parent = op["node_id"], op.get("parent")
    node = w.node(nid)
    lv = w.level(nid)
    if parent is None:
        if lv != 1:
            raise OpRefused(f"node {nid!r} is on level {lv}; only level 1 is on top")
    elif w.level(parent) != lv - 1:
        raise OpRefused(f"node {nid!r} is on level {lv}; its parent must be on level {lv - 1}")
    if parent == node.get("parent") and op.get("position") is None:
        raise OpRefused(f"node {nid!r} is already there; give a position to reorder it")
    w.place(nid, parent, op.get("position"))


def _merge_nodes(w: _Work, op: Mapping[str, Any]) -> None:
    source, target = op["source"], op["target"]
    w.node(source)
    w.node(target)
    if source == target:
        raise OpRefused("a node cannot be merged into itself")
    if w.level(source) != w.level(target):
        raise OpRefused(f"{source!r} and {target!r} are not on the same level")
    for k in w.own(source):
        w.relocate(k, target)
    for kid in w.children(source):
        w.place(kid, target, None)
    for entry in w.aside.values():
        if entry.get("from") == source:
            entry["from"] = target
    del w.nodes[source]


def _split_node(w: _Work, op: Mapping[str, Any]) -> None:
    nid = op["node_id"]
    node = w.node(nid)
    kids, own = w.children(nid), w.own(nid)
    parts = list(op["parts"])
    ids = op.get("ids")
    if not parts:
        raise OpRefused("a split needs at least one part")
    if ids is not None and len(ids) != len(parts):
        raise OpRefused(f"{len(ids)} id(s) given for {len(parts)} part(s)")
    seen: set[str] = set()
    groups = []
    for part in parts:
        group = list(dict.fromkeys(part["members"]))
        if not group:
            raise OpRefused("each part of a split holds at least one member")
        stray = [m for m in group if m not in kids and m not in own]
        if stray:
            raise OpRefused(f"not held by {nid!r}: {_sample(stray)}")
        if set(group) & set(kids) & set(own):
            raise OpRefused(f"both a keyword on {nid!r} and the id of one of its nodes")
        if set(group) & seen:
            raise OpRefused(f"in two parts of the split: {_sample(set(group) & seen)}")
        seen.update(group)
        groups.append(group)
    if len(seen) == len(kids) + len(own):
        raise OpRefused(f"a split leaves at least one keyword or node in {nid!r}")
    previous = nid
    for i, (part, group) in enumerate(zip(parts, groups, strict=True)):
        new_id = w.take_id(ids[i] if ids is not None else None)
        w.nodes[new_id] = {
            "id": new_id,
            "parent": node.get("parent"),
            "names": _new_names(part.get("names"), "a part of a split"),
            "order": 0,
        }
        w.place(new_id, node.get("parent"), None, after=previous)
        previous = new_id
        members = set(group)
        for k in [k for k in own if k in members]:
            w.relocate(k, new_id)
        for rank, kid in enumerate([k for k in kids if k in members], start=1):
            w.nodes[kid]["parent"] = new_id
            w.nodes[kid]["order"] = rank


def _create_node(w: _Work, op: Mapping[str, Any]) -> None:
    parent = op.get("parent")
    if parent is not None and w.level(parent) >= w.depth:
        raise OpRefused(f"node {parent!r} is on the deepest level; no node goes below it")
    new_id = w.take_id(op.get("node_id"))
    w.nodes[new_id] = {
        "id": new_id,
        "parent": parent,
        "names": _new_names(op.get("names"), "a new node"),
        "order": 0,
    }
    w.place(new_id, parent, op.get("position"))


def _delete_node(w: _Work, op: Mapping[str, Any]) -> None:
    nid = op["node_id"]
    w.node(nid)
    if w.children(nid) or w.own(nid):
        raise OpRefused(f"node {nid!r} is not empty: move, merge or set aside what it holds first")
    del w.nodes[nid]


def _set_aside(w: _Work, op: Mapping[str, Any]) -> None:
    terms = _keywords(op["keywords"])
    unknown = [k for k in terms if k not in w.keywords and k not in w.aside]
    if unknown:
        raise OpRefused(f"not in the tree: {_sample(unknown)}")
    reason = str(op.get("reason") or "").strip()
    for k in terms:
        if k in w.keywords:
            entry: dict[str, Any] = {"from": w.keywords.pop(k), "reason": reason}
            if k in w.attribution:
                entry["attribution"] = w.attribution.pop(k)
            w.aside[k] = entry
        else:
            w.aside[k]["reason"] = reason


def _put_back(w: _Work, op: Mapping[str, Any]) -> None:
    terms = _keywords(op["keywords"])
    unknown = [k for k in terms if k not in w.aside]
    if unknown:
        raise OpRefused(f"not set aside: {_sample(unknown)}")
    target_node = op.get("node_id")
    if target_node is not None:
        w.node(target_node)
    targets = {}
    for k in terms:
        target = target_node or w.aside[k].get("from")
        if target is None or target not in w.nodes:
            raise OpRefused(f"no place to put back {k!r} (name a target node)")
        targets[k] = target
    for k, target in targets.items():
        entry = w.aside.pop(k)
        w.keywords[k] = target
        n = entry.get("attribution")
        if n == 0 or (n and target == entry.get("from") and n < w.level(target)):
            w.attribution[k] = n


def _set_attribution(w: _Work, op: Mapping[str, Any]) -> None:
    value = op.get("levels")
    if value is not None and (
        not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < w.depth
    ):
        raise OpRefused(f"an attribution is None or 0 to {w.depth - 1}, not {value!r}")
    terms = _keywords(op["keywords"])
    aside = [k for k in terms if k in w.aside]
    if aside:
        raise OpRefused(f"set aside, put back first: {_sample(aside)}")
    unknown = [k for k in terms if k not in w.keywords]
    if unknown:
        raise OpRefused(f"not in the tree: {_sample(unknown)}")
    if value is not None:
        high = [k for k in terms if value >= w.level(w.keywords[k])]
        if high:
            raise OpRefused(f"an attribution stays below the level of the node: {_sample(high)}")
    for k in terms:
        if value is None:
            w.attribution.pop(k, None)
        else:
            w.attribution[k] = value


_APPLY = {
    "rename_node": _rename_node,
    "move_keywords": _move_keywords,
    "move_node": _move_node,
    "merge_nodes": _merge_nodes,
    "split_node": _split_node,
    "create_node": _create_node,
    "delete_node": _delete_node,
    "set_aside": _set_aside,
    "put_back": _put_back,
    "set_attribution": _set_attribution,
}
