# SPDX-License-Identifier: MIT
"""Who writes with whom (:mod:`cartolex.app.coauthors`), on hand-made authorships: a work
counts once per pair, a work with too many authors adds no pair, the authors outside the
project are counted, and each ring around someone goes through the ring before."""

from __future__ import annotations

import numpy as np

from cartolex.app.coauthors import Graph, build_arrays, rings


def _graph(works: list[list[int]], totals: list[int], n: int, max_authors: int = 4) -> Graph:
    text = [t for t, people in enumerate(works) for _ in sorted(set(people))]
    ent = [p for people in works for p in sorted(set(people))]
    inside = np.array([len(set(p)) for p in works])
    arrays = build_arrays(np.array(text), np.array(ent), n, np.array(totals), inside, max_authors)
    return Graph(ids=[f"p{i}" for i in range(n)], arrays=arrays, max_authors=max_authors)


def test_pairs_count_works_together_and_leave_large_works_out():
    # works: 0 = {0,1,2}, 1 = {0,1} with one author outside, 2 = {1,3} but nine authors in all
    g = _graph([[0, 1, 2], [0, 1], [1, 3]], totals=[3, 3, 9], n=5)
    nb, cnt = g.links(0)
    assert dict(zip(nb.tolist(), cnt.tolist(), strict=True)) == {1: 2, 2: 1}
    assert nb.tolist() == [1, 2]  # the strongest first
    assert dict(zip(*map(np.ndarray.tolist, g.links(1)), strict=True)) == {0: 2, 2: 1}
    assert g.links(3)[0].tolist() == [] and g.links(4)[0].tolist() == []
    assert g.stat("large", 1) == 1 and g.stat("large", 3) == 1 and g.stat("large", 0) == 0
    assert g.stat("texts", 0) == 2 and g.stat("texts", 1) == 2
    assert g.stat("outside", 0) == 1 and g.stat("outside", 2) == 0
    assert g.pairs == 3


def test_each_ring_goes_through_the_one_before():
    # 0 writes with 1 and 2; 1 and 2 both write with 3; 2 writes with 4 (twice); 4 with 5
    works = [[0, 1], [0, 2], [1, 3], [2, 3], [2, 4], [2, 4], [4, 5]]
    g = _graph(works, totals=[2] * len(works), n=6)
    one, two, three = rings(g, 0, depth=3)
    assert sorted(one.codes.tolist()) == [1, 2] and one.paths.tolist() == [1, 1]
    assert two.codes.tolist() == [3, 4]  # 3 through two partners, then 4
    assert two.paths.tolist() == [2, 1] and two.weight.tolist() == [2, 2]
    assert sorted(two.via(0)) == [1, 2] and two.via(1) == [2]
    frm, to, w = two.edges
    assert sorted(zip(frm.tolist(), to.tolist(), w.tolist(), strict=True)) == [
        (1, 3, 1), (2, 3, 1), (2, 4, 2)
    ]  # fmt: skip
    assert three.codes.tolist() == [5] and three.via(0) == [4]
    assert rings(g, 5, depth=3)[2].codes.tolist() == [0, 3]  # 5, then 4, then 2, then 0 and 3
