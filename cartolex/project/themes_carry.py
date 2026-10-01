# SPDX-License-Identifier: MIT
"""A curated theme tree carried onto a new grouping, and how the two differ.

When the grouping runs again with other settings on the same vocabulary (a
clustering-only change, previewed in the editor's playground), the proposal is
a new tree. :func:`carry_curation` puts it in place of the curated tree while
keeping what the curator did wherever a node **continues**: a node of the
proposal continues a node of the curated tree when more than half of the old
node's keywords are under the new one and more than half of the new one's come
from the old (each old node continued once at most, see :func:`continuations`).

- **names**: a node that continues takes the old node's names; the levels keep
  the names given to them, from the top (a name still the default of its place
  becomes the default of its new place, a new level takes its default);
- **set aside**: a keyword the curator set aside (not the grouping, as too broad) stays set aside, with its
  reason, from the node that continues its old one (else from none);
- **attributions**: ``0`` (counted nowhere) always comes back; any other comes
  back when the keyword's node continues its old node and stays below its level
  (the carry rule of :mod:`cartolex.project.themes`);
- **reviews**: a keyword reviewed or kept stays so when its node continues its
  old one, or it stays set aside.

Everything else is the proposal's: where each keyword sits, the nodes, the
keywords it sets aside as too broad. :func:`against` counts how a proposal
differs from a tree at the top level: themes split, themes merged, keywords
that would move to another theme.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .models import ThemesFile
from .themes import default_level_names

__all__ = [
    "CONTINUES",
    "GROUPING_REASONS",
    "SPLIT_SHARE",
    "Carried",
    "against",
    "carry_curation",
    "continuations",
]

#: A node continues another when more than this share of each one's keywords are the other's.
CONTINUES = 0.5

#: The reasons the grouping itself sets keywords aside with (``cartolex.lexicon.theme_tree``'s
#: ``TOO_BROAD``): such a keyword follows the new proposal, not the curated tree.
GROUPING_REASONS = frozenset({"too broad for any theme"})

#: A theme is split (merged) when at least two themes of the other tree each hold at least this
#: share of its keywords.
SPLIT_SHARE = 0.25


@dataclass
class Carried:
    """The carried tree and what was carried: counts by kind (``names``, ``set_aside``,
    ``attributions``, ``reviews``) and what could not be (``dropped``: attributions whose
    node does not continue)."""

    tree: ThemesFile
    counts: dict[str, int] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"tree": self.tree.model_dump(mode="json", by_alias=True), "carried": self.counts}


def _levels(tree: ThemesFile) -> dict[str, int]:
    parent = {n.id: n.parent for n in tree.nodes}
    out: dict[str, int] = {}

    def level(nid: str) -> int:
        if nid not in out:
            up = parent[nid]
            out[nid] = 1 if up is None else level(up) + 1
        return out[nid]

    for nid in parent:
        level(nid)
    return out


def _under(tree: ThemesFile) -> dict[str, set[str]]:
    """Each node's keywords, on it or under it."""
    parent = {n.id: n.parent for n in tree.nodes}
    out: dict[str, set[str]] = defaultdict(set)
    for keyword, nid in tree.keywords.items():
        cur: str | None = nid
        while cur is not None:
            out[cur].add(keyword)
            cur = parent.get(cur)
    return out


def continuations(old: ThemesFile, new: ThemesFile) -> dict[str, str]:
    """Each node of *new* that continues a node of *old*: new id → old id.

    Any level of *old* may be continued (a tree with a level more keeps its top
    and its bottom levels), each old node by one new node at most: the pairs
    with the larger Jaccard index of their keywords first, then the ones on the
    nearer levels.
    """
    old_level, new_level = _levels(old), _levels(new)
    old_under, new_under = _under(old), _under(new)
    parent = {n.id: n.parent for n in old.nodes}
    chain: dict[str, list[str]] = {}
    for keyword, nid in old.keywords.items():
        line, cur = [], nid
        while cur is not None:
            line.append(cur)
            cur = parent.get(cur)
        chain[keyword] = line
    pairs: list[tuple[float, int, str, str]] = []
    for nid, keywords in new_under.items():
        counts = Counter(o for k in keywords for o in chain.get(k, ()))
        for o, both in counts.items():
            if both > CONTINUES * len(keywords) and both > CONTINUES * len(old_under[o]):
                jaccard = both / (len(keywords) + len(old_under[o]) - both)
                pairs.append((-jaccard, abs(new_level[nid] - old_level[o]), nid, o))
    out: dict[str, str] = {}
    taken: set[str] = set()
    for _j, _d, nid, o in sorted(pairs):
        if nid in out or o in taken:
            continue
        out[nid] = o
        taken.add(o)
    return out


def _relevel(levels: list[Any], old_depth: int, new_depth: int) -> list[dict[str, Any]]:
    """The levels of *new_depth* named from *levels* (a tree of *old_depth*), from the top."""
    old_defaults, new_defaults = default_level_names(old_depth), default_level_names(new_depth)
    out: list[dict[str, Any]] = []
    for place in range(new_depth):
        if place >= old_depth:
            out.append({"names": dict(new_defaults[place])})
            continue
        names = dict(levels[place].names)
        out.append(
            {
                "names": {
                    lang: new_defaults[place].get(lang, name)
                    if name == old_defaults[place].get(lang)
                    else name
                    for lang, name in names.items()
                }
            }
        )
    return out


def carry_curation(curated: ThemesFile, proposal: ThemesFile) -> Carried:
    """*proposal* with the curator's names, set-asides, attributions and reviews of *curated*
    carried wherever a node continues (see the module's rules)."""
    cont = continuations(curated, proposal)
    old_of = dict(cont)
    new_of = {old: new for new, old in cont.items()}
    names = {n.id: dict(n.names) for n in curated.nodes}
    data = proposal.model_dump(mode="json", by_alias=True)
    counts = Counter[str]()
    for node in data["nodes"]:
        old = old_of.get(node["id"])
        if old is not None and names.get(old) and names[old] != node.get("names"):
            node["names"] = names[old]
            counts["names"] += 1
    data["levels"] = _relevel(curated.levels, curated.depth, proposal.depth)
    new_level = _levels(proposal)
    keywords: dict[str, str] = dict(data["keywords"])
    set_aside: dict[str, Any] = dict(data.get("set_aside") or {})
    for k, entry in curated.set_aside.items():
        if k not in keywords and k not in set_aside:
            continue  # not in the proposal's vocabulary
        if entry.reason in GROUPING_REASONS:
            continue
        keywords.pop(k, None)
        source = new_of.get(entry.source) if entry.source else None
        carried: dict[str, Any] = {"from": source, "reason": entry.reason}
        if entry.attribution is not None and entry.attribution < proposal.depth:
            carried["attribution"] = entry.attribution
        set_aside[k] = carried
        counts["set_aside"] += 1
    attribution: dict[str, int] = {
        k: n for k, n in (data.get("attribution") or {}).items() if k in keywords
    }
    for k, n in curated.attribution.items():
        nid = keywords.get(k)
        if nid is None:
            continue
        stays = old_of.get(nid) == curated.keywords.get(k)
        if (n == 0 or stays) and n < new_level[nid]:
            attribution[k] = n
            counts["attributions"] += 1
        else:
            counts["dropped"] += 1
    review: dict[str, str] = {}
    for k, state in curated.review.items():
        if state == "to_check":
            continue
        if k in set_aside and k in curated.set_aside and k not in proposal.set_aside:
            review[k] = state
        elif k in keywords and old_of.get(keywords[k]) == curated.keywords.get(k):
            review[k] = state
        else:
            continue
        counts["reviews"] += 1
    data.update(
        keywords=keywords,
        set_aside=dict(sorted(set_aside.items())),
        attribution=dict(sorted(attribution.items())),
        review=dict(sorted(review.items())),
        saved=None,
    )
    tree = ThemesFile.model_validate(data)
    return Carried(
        tree,
        {k: counts.get(k, 0) for k in ("names", "set_aside", "attributions", "reviews", "dropped")},
    )


def _tops(tree: ThemesFile) -> dict[str, str]:
    """Each placed keyword's top-level node."""
    parent = {n.id: n.parent for n in tree.nodes}
    top: dict[str, str] = {}

    def top_of(nid: str) -> str:
        if nid not in top:
            up = parent[nid]
            top[nid] = nid if up is None else top_of(up)
        return top[nid]

    return {k: top_of(nid) for k, nid in tree.keywords.items()}


def against(tree: ThemesFile, proposal: ThemesFile | Mapping[str, Any]) -> dict[str, int]:
    """How *proposal* differs from *tree* at the top level: ``split`` (themes of *tree* whose
    keywords at least two themes of the proposal each hold a quarter of), ``merged`` (themes
    of the proposal that gather at least two of *tree*'s that way), ``moved`` (keywords whose
    theme in the proposal is not where most of their old theme went), ``placed`` (keywords
    placed in both), ``set_aside`` and ``put_back`` (keywords that would leave or enter a
    theme)."""
    if not isinstance(proposal, ThemesFile):
        proposal = ThemesFile.model_validate(proposal)
    old, new = _tops(tree), _tops(proposal)
    both = [k for k in old if k in new]
    grid: dict[str, Counter[str]] = defaultdict(Counter)
    back: dict[str, Counter[str]] = defaultdict(Counter)
    for k in both:
        grid[old[k]][new[k]] += 1
        back[new[k]][old[k]] += 1

    def many(row: Counter[str]) -> bool:
        total = sum(row.values())
        return sum(1 for v in row.values() if v >= SPLIT_SHARE * total) >= 2

    main = {o: row.most_common(1)[0][0] for o, row in grid.items()}
    return {
        "placed": len(both),
        "split": sum(1 for row in grid.values() if many(row)),
        "merged": sum(1 for row in back.values() if many(row)),
        "moved": sum(1 for k in both if new[k] != main[old[k]]),
        "set_aside": sum(1 for k in old if k not in new and k in proposal.set_aside),
        "put_back": sum(1 for k in new if k not in old and k in tree.set_aside),
    }
