# SPDX-License-Identifier: MIT
"""Random theme trees, vocabularies and edits for the property tests of the theme tree.

A seeded :class:`random.Random` drives everything, so a failing case is
reproduced from its seed. The words are generic and invented.
"""

from __future__ import annotations

import random
from typing import Any

from cartolex.project.models import LANGUAGES, ThemesFile
from cartolex.project.themes import (
    REVIEW_STATES,
    Edit,
    canonical,
    create_node,
    default_level_names,
    delete_node,
    insert_level,
    merge_nodes,
    move_keywords,
    move_node,
    put_back,
    remove_level,
    rename_level,
    rename_node,
    set_aside,
    set_attribution,
    set_review,
    split_node,
    vocabulary_fingerprint,
    vocabulary_of,
)

WORDS = (
    "wave", "tide", "sediment", "estuary", "coral", "salinity", "current", "dune",
    "reef", "plankton", "harbour", "shore", "model", "survey", "flux", "storm",
)  # fmt: skip
POOL = tuple(
    [f"{a} {b}" for a in WORDS for b in WORDS if a != b]
    + ["érosion côtière", "maré alta", "houle déferlante", "água costeira", "zone intertidale"]
)
REASONS = ("", "too general", "a method, not a theme", "duplicate")


def random_names(rng: random.Random, *, at_least: int = 0) -> dict[str, str]:
    langs = rng.sample(LANGUAGES, rng.randint(at_least, len(LANGUAGES)))
    return {lang: f"{rng.choice(WORDS).title()} {rng.randint(1, 99)}" for lang in langs}


def random_tree(rng: random.Random, depth: int | None = None, *, size: int = 40) -> ThemesFile:
    """A valid tree in canonical form: random shape, ids, names, orders, placements."""
    depth = depth or rng.randint(1, 4)
    serial = iter(range(1, 10_000))
    nodes: list[dict[str, Any]] = []
    previous: list[str | None] = [None]
    for level in range(1, depth + 1):
        current: list[str] = []
        for parent in previous:
            count = rng.randint(1, 4) if parent is None else rng.randint(0, 3)
            for j in range(count):
                k = next(serial)
                nid = rng.choice([f"n{k}", f"s{k}", f"c{k}", f"x{k}_{rng.choice('ab')}"])
                order = j + 1 if rng.random() < 0.7 else rng.randint(0, 3)
                nodes.append(
                    {"id": nid, "parent": parent, "names": random_names(rng), "order": order}
                )
                current.append(nid)
        previous = current  # type: ignore[assignment]
        if level == 1 and rng.random() < 0.05:
            nodes, previous = [], []
            break
    leaves = [n["id"] for n in nodes if _level(nodes, n["id"]) == depth]
    vocab = rng.sample(POOL, rng.randint(0, size))
    keywords: dict[str, str] = {}
    aside: dict[str, dict[str, Any]] = {}
    for k in vocab:
        if leaves and rng.random() < 0.85:
            keywords[k] = rng.choice(leaves)
        else:
            origin = rng.choice([*leaves, None, "gone"]) if leaves else None
            aside[k] = {"from": origin, "reason": rng.choice(REASONS)}
            if rng.random() < 0.3:
                aside[k]["attribution"] = rng.randint(0, depth - 1)
    attribution = {k: rng.randint(0, depth - 1) for k in keywords if rng.random() < 0.3}
    review = {k: rng.choice(REVIEW_STATES) for k in vocab if rng.random() < 0.2}
    levels = [{"names": names} for names in default_level_names(depth)]
    for lv in levels:
        if rng.random() < 0.2:
            lv["names"] = {**lv["names"], rng.choice(LANGUAGES): f"Level {rng.randint(1, 9)}"}
    doc = {
        "depth": depth,
        "levels": levels,
        "nodes": nodes,
        "keywords": keywords,
        "attribution": attribution,
        "set_aside": aside,
        "review": review,
        "based_on": {
            "run": rng.choice([None, "themes.group/20260928T101500Z-3f2a"]),
            "vocabulary": rng.choice([None, vocabulary_fingerprint(vocab)]),
        },
    }
    return canonical(ThemesFile.model_validate(doc))


def _level(nodes: list[dict[str, Any]], node_id: str) -> int:
    by_id = {n["id"]: n for n in nodes}
    lv, node = 1, by_id[node_id]
    while node["parent"] is not None:
        lv, node = lv + 1, by_id[node["parent"]]
    return lv


def levels_of(tree: ThemesFile) -> dict[str, int]:
    nodes = [n.model_dump() for n in tree.nodes]
    return {n["id"]: _level(nodes, n["id"]) for n in nodes}


def leaves_of(tree: ThemesFile) -> list[str]:
    return [nid for nid, lv in levels_of(tree).items() if lv == tree.depth]


def next_vocabulary(rng: random.Random, tree: ThemesFile) -> tuple[list[str], list[str]]:
    """A new vocabulary: most of the tree's keywords, some dropped, some new ones. Returns (vocabulary, new)."""
    old = sorted(vocabulary_of(tree))
    keep = rng.random()
    kept = [k for k in old if rng.random() < keep]
    fresh = [k for k in POOL if k not in set(old)]
    new = rng.sample(fresh, min(len(fresh), rng.randint(0, 15)))
    return kept + new, new


def random_proposals(rng: random.Random, tree: ThemesFile, new: list[str]) -> dict[str, str | None]:
    leaves = leaves_of(tree)
    proposals: dict[str, str | None] = {
        k: (rng.choice([*leaves, None]) if leaves else None) for k in new
    }
    # proposals for keywords the tree already holds are ignored
    for k in rng.sample(sorted(vocabulary_of(tree)), min(3, len(vocabulary_of(tree)))):
        proposals[k] = rng.choice([*leaves, None]) if leaves else None
    return proposals


def random_edit(rng: random.Random, tree: ThemesFile) -> Edit | None:
    """One random operation with valid arguments, or ``None`` when the chosen one has none."""
    levels = levels_of(tree)
    ids = list(levels)
    leaves = [i for i in ids if levels[i] == tree.depth]
    placed = sorted(tree.keywords)
    aside = sorted(tree.set_aside)
    held = placed + aside
    kind = rng.choice(
        [
            "rename_node", "rename_level", "move_keywords", "move_node", "merge", "split",
            "create", "delete", "set_aside", "put_back", "review", "insert", "remove", "attribution",
        ]
    )  # fmt: skip
    if kind == "rename_node" and ids:
        names: dict[str, str | None] = dict(random_names(rng))
        if rng.random() < 0.3:
            names[rng.choice(LANGUAGES)] = None
        return rename_node(tree, rng.choice(ids), names)
    if kind == "rename_level":
        return rename_level(tree, rng.randint(1, tree.depth), random_names(rng, at_least=1))
    if kind == "move_keywords" and placed and leaves:
        return move_keywords(
            tree, rng.sample(placed, rng.randint(1, len(placed))), rng.choice(leaves)
        )
    if kind == "move_node" and ids:
        nid = rng.choice(ids)
        lv = levels[nid]
        parents: list[str | None] = [None] if lv == 1 else [i for i in ids if levels[i] == lv - 1]
        parent = rng.choice(parents)
        current = next(n.parent for n in tree.nodes if n.id == nid)
        position = rng.randint(0, 5) if parent == current or rng.random() < 0.5 else None
        return move_node(tree, nid, parent, position)
    if kind == "merge" and ids:
        nid = rng.choice(ids)
        same = [i for i in ids if levels[i] == levels[nid] and i != nid]
        if same:
            return merge_nodes(tree, nid, rng.choice(same))
    if kind == "split" and ids:
        nid = rng.choice(ids)
        if levels[nid] == tree.depth:
            members = [k for k in placed if tree.keywords[k] == nid]
        else:
            members = [n.id for n in tree.nodes if n.parent == nid]
        if len(members) >= 2:
            rng.shuffle(members)
            cut = sorted(rng.sample(range(1, len(members)), rng.randint(1, len(members) - 1)))
            groups = [members[a:b] for a, b in zip([0, *cut], [*cut, len(members)], strict=True)]
            return split_node(tree, nid, [(g, random_names(rng, at_least=1)) for g in groups[1:]])
    if kind == "create":
        parents = [None, *[i for i in ids if levels[i] < tree.depth]]
        position = rng.choice([None, rng.randint(0, 4)])
        return create_node(
            tree, rng.choice(parents), random_names(rng, at_least=1), position=position
        )
    if kind == "delete":
        used = set(tree.keywords.values()) | {n.parent for n in tree.nodes}
        empty = [i for i in ids if i not in used]
        if empty:
            return delete_node(tree, rng.choice(empty))
    if kind == "set_aside" and held:
        return set_aside(
            tree, rng.sample(held, rng.randint(1, min(5, len(held)))), rng.choice(REASONS)
        )
    if kind == "put_back" and aside and leaves:
        chosen = rng.sample(aside, rng.randint(1, min(5, len(aside))))
        if rng.random() < 0.5:
            return put_back(tree, chosen, rng.choice(leaves))
        backable = [k for k in chosen if tree.set_aside[k].source in leaves]
        if backable:
            return put_back(tree, backable)
    if kind == "attribution" and placed:
        levels_ = rng.choice([None, *range(tree.depth)])
        return set_attribution(
            tree, rng.sample(placed, rng.randint(1, min(5, len(placed)))), levels_
        )
    if kind == "review" and held:
        state = rng.choice([*REVIEW_STATES, None])
        return set_review(tree, rng.sample(held, rng.randint(1, min(5, len(held)))), state)
    if kind == "insert" and tree.depth < 4:
        return insert_level(tree, rng.randint(1, tree.depth + 1))
    if kind == "remove" and tree.depth > 1:
        return remove_level(tree, rng.randint(1, tree.depth))
    return None


def normalized_orders(tree: ThemesFile) -> ThemesFile:
    """The same tree with each group of siblings numbered 1, 2, 3… in its current order."""
    doc = tree.model_dump(mode="json", by_alias=True)
    groups: dict[str | None, list[dict[str, Any]]] = {}
    for n in doc["nodes"]:  # canonical: already in sibling order
        groups.setdefault(n["parent"], []).append(n)
    for group in groups.values():
        for rank, n in enumerate(group, start=1):
            n["order"] = rank
    return canonical(ThemesFile.model_validate(doc))
