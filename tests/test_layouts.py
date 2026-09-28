# SPDX-License-Identifier: MIT
"""The layout methods a map version may name: UMAP, t-SNE (openTSNE) and the theme tree."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import sparse

from cartolex.atlas import reducers
from cartolex.atlas.tree_layout import FILL, people_paths, tree_layout
from cartolex.lexicon.theme_tree import EngineTree
from cartolex.project.maps import add_version, try_another
from cartolex.project.models import MapsFile


def _world(seed: int = 0):
    """Four themes of three topics each; 120 people, each using mostly one topic."""
    rng = np.random.default_rng(seed)
    nodes, keywords, terms = [], {}, []
    for t in range(4):
        nodes.append({"id": f"t{t}", "parent": None, "order": t, "names": {}})
        for s in range(3):
            nodes.append({"id": f"t{t}s{s}", "parent": f"t{t}", "order": s, "names": {}})
            for k in range(5):
                term = f"term {t} {s} {k}"
                terms.append(term)
                keywords[term] = f"t{t}s{s}"
    doc = {
        "format": "cartolex-themes/1",
        "depth": 2,
        "levels": [{"names": {}}, {"names": {}}],
        "nodes": nodes,
        "keywords": keywords,
    }
    tree = EngineTree.from_document(doc, terms)
    topic = rng.integers(0, 12, size=120)
    usage = np.zeros((120, len(terms)))
    for i, s in enumerate(topic):
        usage[i, s * 5 : s * 5 + 5] = rng.random(5) + 0.5
        usage[i, rng.integers(0, len(terms), size=2)] += 0.2
    usage[7] = 0.0  # no keyword of the tree: placed by the nearest centroid
    Z = usage @ rng.normal(size=(len(terms), 6)) + rng.normal(scale=0.05, size=(120, 6))
    Z[7] = Z[8]
    return tree, sparse.csr_matrix(usage), Z, topic


def test_people_follow_their_heaviest_theme_down_the_tree():
    tree, usage, Z, topic = _world()
    paths = people_paths(tree, usage, Z)
    ids = np.array([n.id for n in tree.nodes], dtype=object)
    for i in (0, 1, 2, 50):
        assert ids[paths[i, 1]] == f"t{topic[i] // 3}s{topic[i] % 3}"
        assert ids[paths[i, 0]] == f"t{topic[i] // 3}"
    assert ids[paths[7, 1]] == ids[paths[8, 1]]  # the nearest people's node
    assert (paths >= 0).all()
    assert all(tree.parent_index[paths[i, 1]] == paths[i, 0] for i in range(120))


def test_the_tree_layout_nests_people_in_their_themes():
    tree, usage, Z, _ = _world()
    paths = people_paths(tree, usage, Z)
    xy = tree_layout(tree, paths, Z)
    assert xy.shape == (120, 2) and np.isfinite(xy).all()
    assert np.array_equal(xy, tree_layout(tree, paths, Z))  # deterministic
    # themes do not overlap: every person is nearer their theme's centre than others'
    centres = {t: xy[paths[:, 0] == t].mean(axis=0) for t in np.unique(paths[:, 0])}
    for i in range(120):
        own = np.linalg.norm(xy[i] - centres[paths[i, 0]])
        assert all(own <= np.linalg.norm(xy[i] - c) for t, c in centres.items())
    # the map's scale: the themes fill a share of a disc of radius √n
    assert np.hypot(xy[:, 0], xy[:, 1]).max() <= np.sqrt(120) * (1 + 1e-9)
    assert 0 < FILL < 1


def test_the_tree_layout_through_compute_umap():
    tree, usage, Z, _ = _world(1)
    from cartolex.atlas.types import Embeddings

    emb = Embeddings(
        Z_ind=Z,
        Z_terms=np.random.default_rng(2).normal(size=(9, 6)),
        umap_ind=None,
        umap_terms=None,
    )
    out = reducers.compute_umap(
        emb, n_neighbors=10, min_dist=0.1, n_components=2, metric="cosine", random_state=0,
        layout="tree", tree=tree, usage=usage,
    )  # fmt: skip
    assert out.umap_ind.shape == (120, 2) and out.umap_terms.shape == (9, 2)
    with pytest.raises(ValueError, match="theme tree"):
        reducers.compute_umap(
            emb, n_neighbors=10, min_dist=0.1, n_components=2, metric="cosine",
            random_state=0, layout="tree",
        )  # fmt: skip


def test_the_tsne_layout_is_seeded_and_places_the_terms():
    pytest.importorskip("openTSNE")
    assert reducers.opentsne_available()
    rng = np.random.default_rng(3)
    Z = np.vstack([rng.normal(loc=c, size=(40, 5)) for c in (-3.0, 0.0, 3.0)])
    terms = rng.normal(size=(15, 5))
    a, ta = reducers.fit_tsne_layout(Z, terms, perplexity=30, random_state=4)
    b, tb = reducers.fit_tsne_layout(Z, terms, perplexity=30, random_state=4)
    assert a.shape == (120, 2) and ta.shape == (15, 2)
    assert np.array_equal(a, b) and np.array_equal(ta, tb)
    small, _ = reducers.fit_tsne_layout(Z[:10], terms, perplexity=30, random_state=0)
    assert small.shape == (10, 2)  # the perplexity is capped


def test_another_method_starts_from_its_own_defaults():
    maps, v1 = add_version(MapsFile(), params={"n_neighbors": 15})
    maps, v2 = try_another(maps, seed=3, method="tree")
    maps, v3 = try_another(maps, seed=4)
    by_id = {v.id: v for v in maps.versions}
    assert by_id[v2].layout.method == "tree" and by_id[v2].layout.params == {}
    assert by_id[v3].layout.method == "umap" and by_id[v3].layout.params == {"n_neighbors": 15}
    assert maps.pinned == v1
