# SPDX-License-Identifier: MIT
"""Tests for the evidence-based term→subfield/concept aggregator.

A researcher's subfield/concept weights must come ONLY from terms they actually
used (variants folded), never from geometric SVD proximity. Synthetic hierarchy
docs only — no real data.
"""

from __future__ import annotations

from cartolex.lexicon.subfields import researcher_group_weights, term_to_group_maps


def _doc() -> dict:
    return {
        "subfields": [
            {"id": 0, "label": "A"},
            {"id": 1, "label": "B"},
        ],
        "concepts": [
            {"id": 0, "label": "c0", "subfield_id": 0, "term_indices": [0, 1]},
            {"id": 1, "label": "c1", "subfield_id": 1, "term_indices": [2]},
        ],
    }


_TERMS = ["alpha", "beta", "gamma", "delta"]


def _maps(doc: dict | None):
    t2c, c2s, _cl, _sl = term_to_group_maps(doc, _TERMS)
    return {"term_to_concept": t2c, "concept_to_subfield": c2s}


def test_aggregates_only_terms_in_the_lexicon() -> None:
    # delta (idx 3) belongs to no concept → ignored entirely.
    scored = [("alpha", 3.0), ("beta", 1.0), ("gamma", 2.0), ("delta", 5.0)]
    subfields, concepts = researcher_group_weights(scored, **_maps(_doc()))

    c = {x["id"]: x["weight"] for x in concepts}
    assert c == {0: round(4 / 6, 4), 1: round(2 / 6, 4)}
    s = {x["id"]: x["weight"] for x in subfields}
    assert s == {0: round(4 / 6, 4), 1: round(2 / 6, 4)}
    # Sorted by descending weight.
    assert [x["id"] for x in concepts] == [0, 1]


def test_folds_merge_variants_onto_canonical() -> None:
    doc = _doc()
    # idx 3 (delta) is declared a merge variant of idx 0 (alpha) under concept 0.
    doc["concepts"][0]["term_merges"] = [[0, 3]]
    # The researcher only "used" the variant string 'delta'.
    subfields, concepts = researcher_group_weights([("delta", 2.0)], **_maps(doc))
    assert {x["id"] for x in concepts} == {0}
    assert {x["id"] for x in subfields} == {0}
    assert concepts[0]["weight"] == 1.0


def test_drops_concepts_of_removed_subfields() -> None:
    doc = _doc()
    doc["subfields"][1]["keep"] = False  # subfield 1 dropped → its concept c1 gone
    _subfields, concepts = researcher_group_weights([("alpha", 1.0), ("gamma", 1.0)], **_maps(doc))
    assert {x["id"] for x in concepts} == {0}  # gamma (c1, dropped subfield) ignored


def test_accepts_dicts_and_strips_case() -> None:
    scored = [{"term": "ALPHA", "score": 1.0}, {"term": " gamma ", "score": 1.0}]
    _subfields, concepts = researcher_group_weights(scored, **_maps(_doc()))
    assert {x["id"] for x in concepts} == {0, 1}


def test_empty_when_no_match_or_stale_doc() -> None:
    assert researcher_group_weights([("delta", 1.0)], **_maps(_doc())) == ([], [])
    assert researcher_group_weights([("alpha", 1.0)], **_maps(None)) == ([], [])
    assert researcher_group_weights([], **_maps(_doc())) == ([], [])
