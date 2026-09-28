# SPDX-License-Identifier: MIT
"""The theme tree in the engine: reading a tree of any depth, and applying it (properties).

Random trees of depths 1 to 4 (``tests/themes_random.py``) are applied to random
usage matrices, and every weight is checked against the rules written out by
hand: a keyword counts toward its node and the nodes above it, down to its
attribution; ``0`` counts nowhere; shares sum to 1 per person and level.
"""

from __future__ import annotations

import random

import numpy as np
import pytest
from scipy import sparse
from themes_random import random_tree

from cartolex.atlas.hierarchy import build_hierarchy, level_groups
from cartolex.lexicon.subfields import concept_shade, subfield_color
from cartolex.lexicon.theme_tree import (
    LANGUAGES,
    EngineTree,
    apply_tree,
    default_level_names,
    node_colors,
    propose_tree,
    vocabulary_fingerprint,
)
from cartolex.project import models as project_models
from cartolex.project import themes as project_themes
from cartolex.project.models import ThemesFile
from cartolex.project.themes import canonical, node_level

EXTRA = ("not in the tree", "another one")


def _doc(tree: ThemesFile) -> dict:
    return tree.model_dump(mode="json", by_alias=True)


def _terms(tree: ThemesFile, rng: random.Random) -> list[str]:
    terms = sorted(set(tree.keywords) | set(tree.set_aside)) + list(EXTRA)
    rng.shuffle(terms)
    return terms


def _counts_to(tree: ThemesFile, keyword: str) -> int:
    n = tree.attribution.get(keyword)
    return node_level(tree, tree.keywords[keyword]) if n is None else n


def _ancestors(tree: ThemesFile, node: str) -> dict[int, str]:
    """{level: ancestor} of *node*, itself included."""
    parent = {n.id: n.parent for n in tree.nodes}
    out, current = {}, node
    while current is not None:
        out[node_level(tree, current)] = current
        current = parent[current]
    return out


def test_the_engine_shares_the_projects_constants():
    assert LANGUAGES == project_models.LANGUAGES
    for depth in range(1, 5):
        assert default_level_names(depth) == project_themes.default_level_names(depth)
    assert vocabulary_fingerprint(["b", "a", "a"]) == project_themes.vocabulary_fingerprint(
        ["a", "b"]
    )


@pytest.mark.parametrize("seed", range(40))
def test_a_tree_reads_over_the_vocabulary_as_its_rules_say(seed):
    rng = random.Random(seed)
    tree = random_tree(rng, depth=seed % 4 + 1)
    terms = _terms(tree, rng)
    engine = EngineTree.from_document(_doc(tree), terms)
    assert engine.depth == tree.depth
    assert [n.id for n in engine.nodes] == [n.id for n in canonical(tree).nodes]
    assert [n.level for n in engine.nodes] == [node_level(tree, n.id) for n in engine.nodes]
    for row, term in enumerate(terms):
        if term in tree.keywords:
            assert engine.nodes[engine.node_of[row]].id == tree.keywords[term]
            assert engine.counts_to[row] == _counts_to(tree, term)
        else:
            assert engine.node_of[row] == -1


def _brute_force(tree: ThemesFile, terms: list[str], X: np.ndarray) -> dict:
    """Every person's weight on every node, from the rules written out one keyword at a time."""
    out: dict[tuple[int, str], float] = {}
    for p in range(X.shape[0]):
        total = sum(X[p, j] for j, t in enumerate(terms) if t in tree.keywords)
        if total <= 0:
            continue
        for j, t in enumerate(terms):
            if t not in tree.keywords or X[p, j] == 0:
                continue
            up = _ancestors(tree, tree.keywords[t])
            for level in range(1, _counts_to(tree, t) + 1):
                key = (p, up[level])
                out[key] = out.get(key, 0.0) + X[p, j] / total
    return out


@pytest.mark.parametrize("seed", range(60))
def test_every_keyword_counts_where_its_node_and_attribution_say(seed):
    rng = random.Random(1000 + seed)
    tree = random_tree(rng, depth=seed % 4 + 1)
    terms = _terms(tree, rng)
    nrng = np.random.default_rng(seed)
    n_people = int(nrng.integers(1, 25))
    X = nrng.random((n_people, len(terms))) * (nrng.random((n_people, len(terms))) < 0.4)
    X[nrng.integers(0, n_people)] = 0.0  # someone with no usage at all
    engine = EngineTree.from_document(_doc(tree), terms)
    applied = apply_tree(
        engine,
        sparse.csr_matrix(X),
        scores=X.sum(axis=0),
        researcher_ids=[f"p{i}" for i in range(n_people)],
        units=[f"u{i % 3}" for i in range(n_people)],
    )
    want = _brute_force(tree, terms, X)
    people = applied.people
    got = {
        (int(r[1:]), n): w
        for r, n, w in zip(people["researcher_id"], people["node"], people["weight"], strict=True)
    }
    assert got.keys() == {k for k, v in want.items() if v > 0}
    for key, w in got.items():
        assert w == pytest.approx(want[key], rel=1e-12, abs=1e-12)
    # levels: each node on its level; shares sum to 1 per person and level
    level = {n.id: n.level for n in engine.nodes}
    assert (people["level"] == people["node"].map(level)).all()
    if len(people):
        sums = people.groupby(["researcher_id", "level"])["share"].sum()
        assert np.allclose(sums.to_numpy(), 1.0)
    # a keyword with attribution 0 counts nowhere; a keyword never counts below its node
    for lv in range(1, tree.depth + 1):
        rows, _ = engine.counting(lv)
        counted_here = {terms[r] for r in rows.tolist()}
        assert not counted_here & {t for t, n in tree.attribution.items() if n == 0}
        assert all(node_level(tree, tree.keywords[t]) >= lv for t in counted_here)
        assert counted_here == {t for t in tree.keywords if _counts_to(tree, t) >= lv}
    # organisations sum their people
    orgs = applied.organisations
    for (unit, node), w in orgs.set_index(["unit", "node"])["weight"].items():
        mine = [got.get((p, node), 0.0) for p in range(n_people) if f"u{p % 3}" == unit]
        assert w == pytest.approx(sum(mine))
    if len(orgs):
        assert np.allclose(orgs.groupby(["unit", "level"])["share"].sum().to_numpy(), 1.0)
    # node weights: the people's weights summed, each rounded to six decimals on the way up
    total = {n: 0.0 for n in level}
    for (_, n), w in got.items():
        total[n] += w
    for entry in applied.nodes:
        assert entry["weight"] == pytest.approx(total[entry["id"]], abs=1e-5 * tree.depth)
    # keyword weights: the column sums of the shares
    counted = max(1, int((X[:, [terms.index(t) for t in tree.keywords]].sum(axis=1) > 0).sum()))
    for term, w, s in zip(
        applied.keywords["term"], applied.keywords["weight"], applied.keywords["share"], strict=True
    ):
        want_w = sum(
            X[p, terms.index(term)] / X[p, [terms.index(t) for t in tree.keywords]].sum()
            for p in range(n_people)
            if X[p, [terms.index(t) for t in tree.keywords]].sum() > 0
        )
        assert w == pytest.approx(round(want_w, 6), abs=2e-6)
        assert s == pytest.approx(round(want_w / counted, 6), abs=2e-6)


@pytest.mark.parametrize("seed", range(20))
def test_one_usage_is_described_as_a_person_is(seed):
    rng = random.Random(3000 + seed)
    tree = random_tree(rng, depth=seed % 4 + 1)
    terms = _terms(tree, rng)
    engine = EngineTree.from_document(_doc(tree), terms)
    nrng = np.random.default_rng(seed)
    x = nrng.random(len(terms)) * (nrng.random(len(terms)) < 0.5)
    applied = apply_tree(engine, x[None, :], scores=x, researcher_ids=["one"])
    rows = np.flatnonzero(x > 0)
    described = {
        engine.nodes[n].id: (w, s)
        for lw in engine.describe(rows, x[rows])
        for n, w, s in zip(lw.nodes.tolist(), lw.weights, lw.shares, strict=True)
    }
    table = {
        n: (w, s)
        for n, w, s in zip(
            applied.people["node"], applied.people["weight"], applied.people["share"], strict=True
        )
    }
    assert described.keys() == table.keys()
    for node, (w, s) in table.items():
        assert described[node][0] == pytest.approx(w) and described[node][1] == pytest.approx(s)


def test_a_tree_is_refused_when_it_does_not_fit_the_vocabulary():
    tree = {
        "depth": 2,
        "levels": [{"names": {}}, {"names": {}}],
        "nodes": [
            {"id": "a", "parent": None, "names": {}, "order": 1},
            {"id": "b", "parent": "a", "names": {}, "order": 1},
        ],
        "keywords": {"one": "b", "two": "a"},
        "attribution": {"one": 1},
    }
    engine = EngineTree.from_document(tree, ["two", "one", "three"])
    assert engine.counts_to.tolist() == [1, 1, 0] and engine.node_of.tolist() == [0, 1, -1]
    with pytest.raises(ValueError, match="not in the vocabulary"):
        EngineTree.from_document(tree, ["one"])
    with pytest.raises(ValueError, match="attribution"):
        EngineTree.from_document({**tree, "attribution": {"two": 1}}, ["one", "two"])
    with pytest.raises(ValueError, match="unknown parent"):
        EngineTree.from_document(
            {**tree, "nodes": [*tree["nodes"], {"id": "c", "parent": "x"}]}, ["one", "two"]
        )
    with pytest.raises(ValueError, match="below the tree's depth"):
        EngineTree.from_document(
            {**tree, "nodes": [*tree["nodes"], {"id": "c", "parent": "b"}]}, ["one", "two"]
        )
    with pytest.raises(ValueError, match="1 to 4"):
        EngineTree.from_document({**tree, "depth": 5}, ["one", "two"])


def test_colours_are_a_hue_per_top_node_and_a_shade_per_descendant():
    doc = {
        "depth": 3,
        "levels": [{"names": {}}] * 3,
        "nodes": [
            {"id": "s4", "parent": None, "order": 1},
            {"id": "m", "parent": "s4", "order": 1},
            {"id": "c1", "parent": "m", "order": 1},
            {"id": "c2", "parent": "m", "order": 2},
            {"id": "extra", "parent": None, "order": 2},
            {"id": "s0", "parent": None, "order": 3},
        ],
        "keywords": {},
    }
    engine = EngineTree.from_document(doc, [])
    colors = dict(zip([n.id for n in engine.nodes], node_colors(engine), strict=True))
    assert colors["s4"] == subfield_color(4) and colors["s0"] == subfield_color(0)
    assert colors["extra"] == subfield_color(5)  # the next free palette entry
    base = subfield_color(4)
    assert [colors[i] for i in ("m", "c1", "c2")] == [concept_shade(base, j, 3) for j in range(3)]
    again = EngineTree.from_document(doc, ["x"])
    assert node_colors(again) == node_colors(engine)


@pytest.mark.parametrize("depth", [1, 2, 3, 4])
def test_the_proposal_is_a_canonical_tree_of_the_project(depth):
    rng = np.random.default_rng(depth)
    n = 300
    Z = rng.normal(size=(n, 8))
    finest = rng.integers(0, 40, size=n)
    sizes = [5, 12, 25, 40][-depth:] if depth > 1 else [40]
    levels = level_groups(Z, finest, sizes)
    assert len(levels) == depth
    terms = [f"kw {i:03d}" for i in range(n)]
    doc = propose_tree(
        levels,
        terms,
        rng.random(n),
        display_languages=("fr", "en", "pt"),
        label_maps={"fr": {terms[0]: "mot 0"}},
        run="themes.group/x",
    )
    tree = ThemesFile.model_validate(doc)
    assert canonical(tree).model_dump(mode="json", by_alias=True) == doc
    assert tree.depth == depth and set(tree.keywords) == set(terms)
    assert all(node_level(tree, n) == depth for n in tree.keywords.values())
    assert tree.based_on.run == "themes.group/x"
    prefix = {1: "s", depth: "c"} if depth > 1 else {1: "s"}
    for node in tree.nodes:
        lv = node_level(tree, node.id)
        assert node.id.startswith(prefix.get(lv, f"m{lv}-"))
        assert {"en", "fr", "pt"} == set(node.names)


def test_two_levels_are_the_hierarchys_cut():
    rng = np.random.default_rng(7)
    Z = rng.normal(size=(200, 6))
    finest = rng.integers(0, 17, size=200)
    top, bottom = level_groups(Z, finest, [5, 17])
    terms = [str(i) for i in range(200)]
    h = build_hierarchy(Z, terms, rng.random(200), cluster_labels=finest, target_subfields=5)
    assert [int(p) for p in bottom.parent] == [c["subfield_id"] for c in h["concepts"]]
    assert [r.tolist() for r in bottom.rows] == [c["term_indices"] for c in h["concepts"]]
    assert len(top.rows) == len(h["subfields"]) and top.parent is None
