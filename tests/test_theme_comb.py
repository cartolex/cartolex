# SPDX-License-Identifier: MIT
"""The comb: each keyword on the level its texts support, and the names of a combed tree."""

from __future__ import annotations

import numpy as np
from scipy import sparse

from cartolex.atlas.hierarchy import LevelGroups
from cartolex.lexicon.theme_comb import calibrate, comb, keyword_spread, level_maps
from cartolex.lexicon.theme_tree import TOO_BROAD, propose_tree

# two themes (top nodes 0 and 1), each of two topics (finest nodes 0, 1 | 2, 3)
TERMS = [
    "a1", "a2", "a3", "a4",  # topic 0 (theme A)
    "a5", "a6", "a7", "a8",  # topic 1 (theme A)
    "b1", "b2", "b3", "b4",  # topic 2 (theme B)
    "b5", "b6", "b7", "b8",  # topic 3 (theme B)
    "wide",  # grouped in topic 0, used across theme A's two topics
    "field",  # grouped in topic 2, used in the texts of both themes
]  # fmt: skip
FINEST = np.array([0] * 4 + [1] * 4 + [2] * 4 + [3] * 4 + [0, 2])
LEVELS = [
    LevelGroups((np.array([0, 1, 2, 3, 4, 5, 6, 7, 16]), np.array([8, 9, 10, 11, 12, 13, 14, 15, 17]))),
    LevelGroups(
        (np.array([0, 1, 2, 3, 16]), np.array([4, 5, 6, 7]), np.array([8, 9, 10, 11, 17]), np.array([12, 13, 14, 15])),
        parent=np.array([0, 0, 1, 1]),
    ),
]  # fmt: skip


def _texts() -> sparse.csr_matrix:
    """Six texts per topic, each with three of its topic's keywords; « wide » and « field » spread."""
    rows = []
    for topic in range(4):
        for i in range(6):
            kws = [topic * 4 + (i + j) % 4 for j in range(3)]
            if topic < 2 and i < 3:
                kws.append(16)  # « wide »: half the texts of each topic of theme A
            if i % 2 == 0:
                kws.append(17)  # « field »: every other text of every topic
            rows.append(kws)
    D = np.zeros((len(rows), len(TERMS)))
    for r, kws in enumerate(rows):
        D[r, kws] = 1
    return sparse.csr_matrix(D)


def test_a_keyword_goes_up_to_the_node_its_texts_share_or_aside_when_everywhere():
    P, n = keyword_spread(_texts(), FINEST, 4)
    combed = comb(P, n, FINEST, level_maps(LEVELS), 0.5)
    level = dict(zip(TERMS, combed.level.tolist(), strict=True))
    node = dict(zip(TERMS, combed.node.tolist(), strict=True))
    assert all(level[t] == 2 for t in TERMS[:16])  # specific keywords stay on their topics
    assert [node[t] for t in ("a1", "a5", "b1", "b5")] == [0, 1, 2, 3]
    assert (level["wide"], node["wide"]) == (1, 0)  # theme A's, not one topic's
    assert level["field"] == 0  # used by both themes alike: too broad for any theme
    # a keyword used in fewer texts than the minimum keeps its topic
    few = comb(P, n, FINEST, level_maps(LEVELS), 0.5, min_texts=100)
    assert few.level.tolist() == [2] * len(TERMS) and few.node.tolist() == FINEST.tolist()


def test_the_calibration_keeps_a_theta_of_its_band():
    P, n = keyword_spread(_texts(), FINEST, 4)
    combed = calibrate(P, n, FINEST, level_maps(LEVELS))
    assert 0.1 <= combed.theta <= 0.25 and combed.counts(2)[0] == 1


def test_a_combed_proposal_places_keywords_on_any_level_and_names_without_repeats():
    P, n = keyword_spread(_texts(), FINEST, 4)
    combed = comb(P, n, FINEST, level_maps(LEVELS), 0.5)
    scores = np.ones(len(TERMS))
    scores[16] = scores[17] = 10.0  # the broad keywords are the most used
    scores[0] = 5.0  # « a1 » is topic 0's most used keyword
    doc = propose_tree(
        LEVELS,
        TERMS,
        scores,
        display_languages=("en",),
        placement=(combed.level, combed.node),
        spread=P,
    )
    assert doc["keywords"]["wide"] == "s0" and doc["keywords"]["a1"] == "c0"
    assert doc["set_aside"]["field"]["reason"] == TOO_BROAD
    names = {nd["id"]: nd["names"]["en"] for nd in doc["nodes"]}
    parent = {nd["id"]: nd["parent"] for nd in doc["nodes"]}
    assert names["s0"] == "wide"  # its own keyword, not a topic's
    assert "field" not in names.values()  # set aside, it names nothing
    for nid, up in parent.items():  # no « X › X »
        assert up is None or names[nid] != names[up]
    # without the comb the proposal is the grouping's, keywords on the topics
    plain = propose_tree(LEVELS, TERMS, scores, display_languages=("en",))
    assert set(plain["keywords"].values()) == {"c0", "c1", "c2", "c3"}
    assert plain["set_aside"] == {}


def test_a_combed_name_prefers_a_concept_or_object_on_a_tie_of_use():
    from cartolex.lexicon.labels import tree_names

    terms = ["studies", "salt marsh", "coastal erosion"]
    scores = [2.0, 2.0, 1.0]
    plain = tree_names([-1], [[0, 1, 2]], [[0, 1, 2]], terms, scores, {}, ["en"], "en")
    liked = tree_names(
        [-1], [[0, 1, 2]], [[0, 1, 2]], terms, scores, {}, ["en"], "en", preferred={"salt marsh"}
    )
    assert plain == [{"en": "studies"}] and liked == [{"en": "salt marsh"}]


def test_on_a_curated_tree_the_comb_suggests_moving_up_or_setting_aside():
    from cartolex.lexicon.theme_comb import tree_levels

    doc = {
        "nodes": [
            {"id": "s0", "parent": None},
            {"id": "s1", "parent": None},
            *[{"id": f"c{i}", "parent": "s0" if i < 2 else "s1"} for i in range(4)],
        ],
        "keywords": {t: f"c{int(p)}" for t, p in zip(TERMS, FINEST, strict=True)},
    }
    theta, found = tree_levels(doc, TERMS, _texts(), grid=(0.5,))
    by = {s.keyword: s for s in found}
    assert theta == 0.5 and set(by) == {"wide", "field"}
    assert (by["wide"].node, by["wide"].to) == ("c0", "s0") and by["wide"].share >= 0.5
    assert by["field"].to is None and by["field"].share < 0.5
