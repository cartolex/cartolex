# SPDX-License-Identifier: MIT
"""Borderline keywords and suggested places (``cartolex.lexicon.theme_fit``) on a small space."""

from __future__ import annotations

import numpy as np
import pytest

from cartolex.lexicon.theme_fit import alone, borderline, suggestions

# Two directions, two nodes: "bridge" sits between them, nearer the other node than its own.
TERMS = ["a1", "a2", "a3", "b1", "b2", "bridge", "loose"]
Z = np.array([[1, 0.1], [1, 0.0], [1, -0.1], [0.1, 1], [0.0, 1], [0.3, 1], [0.7, 0.7]], dtype=float)
DOC = {
    "nodes": [{"id": "s1", "parent": None}, {"id": "s2", "parent": None}],
    "keywords": {"a1": "s1", "a2": "s1", "a3": "s1", "b1": "s2", "b2": "s2", "bridge": "s1"},
    "set_aside": {"loose": {"from": None, "reason": ""}},
}


def test_the_margin_leaves_the_keyword_out_and_ranks_the_border_first():
    found = borderline(DOC, TERMS, Z)
    assert [b.keyword for b in found][0] == "bridge"
    first = found[0]
    assert (first.node, first.other) == ("s1", "s2") and first.margin < 0
    assert first.margin == pytest.approx(first.own - first.near, abs=1e-3)
    # its own centroid is that of a1..a3 alone: the cosine of "bridge" to (1, 0)
    assert first.own == pytest.approx(0.3 / np.hypot(0.3, 1), abs=1e-3)
    assert all(b.margin > 0 for b in found[1:])
    assert "loose" not in {b.keyword for b in found}


def test_suggestions_rank_the_nearest_nodes():
    found = suggestions(DOC, TERMS, Z, ["loose", "b1", "unknown"], top=3)
    assert [s.node for s in found["b1"]] == ["s2", "s1"]
    assert len(found["loose"]) == 2 and found["unknown"] == []
    assert found["loose"][0].score >= found["loose"][1].score


def test_a_keyword_alone_in_its_node_has_no_margin_and_is_counted_apart():
    """Leave-one-out: alone in its node, a keyword has no centroid to compare with (not a
    cosine of 1 to itself); it is left out of the list and of the measures, which count it."""
    from cartolex.copilot.measures import fit

    doc = {**DOC, "depth": 1, "nodes": [*DOC["nodes"], {"id": "s3", "parent": None}]}
    doc["keywords"] = {**DOC["keywords"], "loose": "s3"}
    doc["set_aside"] = {}
    found = borderline(doc, TERMS, Z)
    assert "loose" not in {b.keyword for b in found} and alone(doc, TERMS, Z) == ["loose"]
    level = fit(doc, TERMS, Z)["levels"][0]
    assert level["alone"] == 1 and level["keywords"] == len(found) == 6
    assert level["margin"] == pytest.approx(np.mean([b.margin for b in found]), abs=1e-3)
