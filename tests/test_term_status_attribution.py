# SPDX-License-Identifier: MIT
"""Three-status term attribution: defining / subfield-only / ride-along.

A concept may declare two optional lists of term strings:

- ``subfield_only_terms``: broader than the concept but within its subfield's
  scope → counts toward the SUBFIELD share only.
- ``ride_along_terms``: broader than both → display only, no share anywhere.

Absent lists = every term is defining (legacy behaviour, guarded here).
Synthetic hierarchy docs only — no real data.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from cartolex.lexicon.subfields import (
    compute_lexicon_weights,
    curated_term_maps,
    researcher_group_weights,
    term_to_group_maps,
    term_to_subfield_direct,
)

_TERMS = ["alpha", "beta", "gamma", "delta", "epsilon"]


def _doc() -> dict:
    """Concept c0 (subfield 0) owns alpha/beta/gamma; c1 (subfield 1) owns delta."""
    return {
        "subfields": [
            {"id": 0, "label": "A"},
            {"id": 1, "label": "B"},
        ],
        "concepts": [
            {"id": 0, "label": "c0", "subfield_id": 0, "term_indices": [0, 1, 2]},
            {"id": 1, "label": "c1", "subfield_id": 1, "term_indices": [3]},
        ],
    }


def _status_doc() -> dict:
    """beta = ride-along, gamma = subfield-only (both on c0)."""
    doc = _doc()
    doc["concepts"][0]["ride_along_terms"] = ["beta"]
    doc["concepts"][0]["subfield_only_terms"] = ["gamma"]
    return doc


def _maps(doc: dict | None, *, direct: bool = False) -> dict:
    t2c, c2s, _cl, _sl = term_to_group_maps(doc, _TERMS)
    out = {"term_to_concept": t2c, "concept_to_subfield": c2s}
    if direct:
        out["term_to_subfield_direct"] = term_to_subfield_direct(doc, _TERMS)
    return out


# ── term_to_group_maps / term_to_subfield_direct ──────────────────────────────


def test_maps_exclude_non_defining_terms_from_term_to_concept() -> None:
    t2c, c2s, _cl, _sl = term_to_group_maps(_status_doc(), _TERMS)
    assert t2c == {"alpha": 0, "delta": 1}
    assert c2s == {0: 0, 1: 1}


def test_term_to_subfield_direct_maps_subfield_only_terms_to_host_subfield() -> None:
    assert term_to_subfield_direct(_status_doc(), _TERMS) == {"gamma": 0}
    # Legacy doc: no statuses → empty direct map.
    assert term_to_subfield_direct(_doc(), _TERMS) == {}
    assert term_to_subfield_direct(None, _TERMS) == {}


def test_status_lists_match_case_insensitively_and_stripped() -> None:
    doc = _doc()
    doc["concepts"][0]["ride_along_terms"] = ["  BETA "]
    doc["concepts"][0]["subfield_only_terms"] = [" Gamma"]
    t2c, _c2s, _cl, _sl = term_to_group_maps(doc, _TERMS)
    assert t2c == {"alpha": 0, "delta": 1}
    assert term_to_subfield_direct(doc, _TERMS) == {"gamma": 0}


def test_maps_unchanged_without_status_lists() -> None:
    t2c, c2s, _cl, _sl = term_to_group_maps(_doc(), _TERMS)
    assert t2c == {"alpha": 0, "beta": 0, "gamma": 0, "delta": 1}
    assert c2s == {0: 0, 1: 1}


# ── researcher_group_weights ──────────────────────────────────────────────────


def test_ride_along_contributes_nothing_and_shares_renormalise() -> None:
    scored = [("alpha", 1.0), ("beta", 2.0), ("gamma", 3.0), ("delta", 4.0)]
    subfields, concepts = researcher_group_weights(scored, **_maps(_status_doc(), direct=True))
    # Concepts: alpha (1.0) vs delta (4.0) — beta and gamma excluded.
    assert {x["id"]: x["weight"] for x in concepts} == {0: 0.2, 1: 0.8}
    # Subfields: sf0 = alpha 1.0 + gamma 3.0 (direct), sf1 = delta 4.0.
    assert {x["id"]: x["weight"] for x in subfields} == {0: 0.5, 1: 0.5}


def test_subfield_only_term_boosts_subfield_not_concept() -> None:
    subfields, concepts = researcher_group_weights(
        [("gamma", 2.0)], **_maps(_status_doc(), direct=True)
    )
    assert concepts == []
    assert subfields == [{"id": 0, "weight": 1.0}]


def test_without_direct_map_subfield_only_terms_are_simply_ignored() -> None:
    # Existing callers (no new argument) keep current behaviour: excluded terms
    # vanish from both levels.
    scored = [("alpha", 1.0), ("beta", 2.0), ("gamma", 3.0), ("delta", 4.0)]
    subfields, concepts = researcher_group_weights(scored, **_maps(_status_doc()))
    assert {x["id"]: x["weight"] for x in concepts} == {0: 0.2, 1: 0.8}
    assert {x["id"]: x["weight"] for x in subfields} == {0: 0.2, 1: 0.8}


def test_legacy_doc_weights_unchanged() -> None:
    # Regression guard: absent lists = every term defining, exact legacy values.
    scored = [("alpha", 3.0), ("beta", 1.0), ("gamma", 2.0), ("delta", 5.0)]
    subfields, concepts = researcher_group_weights(scored, **_maps(_doc(), direct=True))
    assert {x["id"]: x["weight"] for x in concepts} == {
        0: round(6 / 11, 4),
        1: round(5 / 11, 4),
    }
    assert {x["id"]: x["weight"] for x in subfields} == {
        0: round(6 / 11, 4),
        1: round(5 / 11, 4),
    }


def test_no_match_still_empty_with_direct_map() -> None:
    assert researcher_group_weights([("epsilon", 1.0)], **_maps(_status_doc(), direct=True)) == (
        [],
        [],
    )


# ── compute_lexicon_weights (share stamping at apply) ────────────────────────


def _weights_X() -> np.ndarray:
    # r0: alpha=1, beta=1, gamma=2 (curated row sum 4); r1: delta=1.
    return np.array(
        [
            [1.0, 1.0, 2.0, 0.0, 9.0],
            [0.0, 0.0, 0.0, 1.0, 9.0],
        ]
    )


def test_lexicon_weights_legacy_doc_unchanged() -> None:
    doc = _doc()
    df = compute_lexicon_weights(doc, _weights_X(), _TERMS)
    by_cid = {int(c["id"]): c for c in doc["concepts"]}
    assert by_cid[0]["weight"] == 1.0 and by_cid[0]["share"] == 0.5
    assert by_cid[1]["weight"] == 1.0 and by_cid[1]["share"] == 0.5
    by_sid = {int(s["id"]): s for s in doc["subfields"]}
    assert by_sid[0]["weight"] == 1.0 and by_sid[1]["weight"] == 1.0
    assert set(df["term"]) == {"alpha", "beta", "gamma", "delta"}


def test_lexicon_weights_apply_statuses_to_shares_but_keep_term_rows() -> None:
    doc = _status_doc()
    df = compute_lexicon_weights(doc, _weights_X(), _TERMS)

    by_cid = {int(c["id"]): c for c in doc["concepts"]}
    # Concept c0 = defining alpha only (0.25); beta (ride) + gamma (sf-only) out.
    assert by_cid[0]["weight"] == 0.25 and by_cid[0]["share"] == 0.125
    assert by_cid[1]["weight"] == 1.0 and by_cid[1]["share"] == 0.5

    by_sid = {int(s["id"]): s for s in doc["subfields"]}
    # Subfield 0 = concept rollup (0.25) + gamma direct (0.5); beta excluded.
    assert by_sid[0]["weight"] == 0.75 and by_sid[0]["share"] == 0.375
    assert by_sid[1]["weight"] == 1.0 and by_sid[1]["share"] == 0.5

    # Display term rows keep everything, weights untouched.
    w = dict(zip(df["term"], df["weight"], strict=True))
    assert w == {"alpha": 0.25, "beta": 0.25, "gamma": 0.5, "delta": 1.0}


# ── curated display set ───────────────────────────────────────────────────────


def test_curated_term_maps_keep_all_statuses_displayable() -> None:
    _canon, curated = curated_term_maps(_status_doc(), _TERMS)
    assert curated == {"alpha", "beta", "gamma", "delta"}


# ── apply passthrough + seed refresh ──────────────────────────────────────────


def test_refresh_subfield_top_terms_excludes_ride_along_seeds() -> None:
    from cartolex.lexicon.subfields_edit import refresh_subfield_top_terms

    doc = {
        "subfields": [{"id": 0, "label": "A", "top_terms": []}],
        "concepts": [
            {
                "id": 0,
                "subfield_id": 0,
                "term_indices": [0, 1, 2],
                "top_terms": ["alpha", "Beta", "gamma"],
                "ride_along_terms": ["beta"],
                "subfield_only_terms": ["gamma"],
            }
        ],
    }
    refresh_subfield_top_terms(doc)
    # Ride-alongs must not steer the assignment centroid; subfield-only terms
    # are within the subfield's scope and stay.
    assert doc["subfields"][0]["top_terms"] == ["alpha", "gamma"]


def test_apply_passes_status_lists_through_to_applied_artifact(tmp_path) -> None:
    from cartolex.atlas.model_files import save_embeddings, save_lexical_data
    from cartolex.atlas.types import Embeddings, LexicalData
    from cartolex.lexicon.subfields import apply_subfield_files

    terms = ["a1", "a2", "b1", "b2"]
    X = np.array(
        [
            [1.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 1.0],
        ]
    )
    meta_ind = pd.DataFrame({"researcher_id": ["r0", "r1"]})
    data = LexicalData(
        X=X, terms=terms, individuals=list(meta_ind["researcher_id"]), meta_ind=meta_ind, X_tf=X
    )
    Z = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    emb = Embeddings(Z_ind=Z[:2], Z_terms=Z, umap_ind=None, umap_terms=None)
    lex_p = tmp_path / "lexical_data.json"
    emb_p = tmp_path / "embeddings.json"
    save_lexical_data(data, lex_p)
    save_embeddings(emb, emb_p)

    curated = tmp_path / "subfields.json"
    curated.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "domain_title": "Domain X",
                "subfields": [
                    {"id": 0, "label": "A", "keep": True, "top_terms": ["a1", "a2"]},
                    {"id": 1, "label": "B", "keep": True, "top_terms": ["b1", "b2"]},
                ],
                "concepts": [
                    {
                        "id": 0,
                        "label": "cA",
                        "subfield_id": 0,
                        "term_indices": [0, 1],
                        "top_terms": ["a1", "a2"],
                        "subfield_only_terms": ["a2"],
                        "ride_along_terms": ["a1"],
                    },
                    {
                        "id": 1,
                        "label": "cB",
                        "subfield_id": 1,
                        "term_indices": [2, 3],
                        "top_terms": ["b1", "b2"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    applied = apply_subfield_files(
        curated_json=curated,
        lexical_data_json=lex_p,
        embeddings_json=emb_p,
        final_json_out=tmp_path / "out.json",
    )
    on_disk = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    for doc in (applied, on_disk):
        c0 = next(c for c in doc["concepts"] if int(c["id"]) == 0)
        assert c0["subfield_only_terms"] == ["a2"]
        assert c0["ride_along_terms"] == ["a1"]
        c1 = next(c for c in doc["concepts"] if int(c["id"]) == 1)
        assert "subfield_only_terms" not in c1 and "ride_along_terms" not in c1


def test_index_anchored_claims_beat_stale_top_terms():
    """A term whose INDEX lives in concept B must map to B even when an earlier
    concept still lists the string in its stale ``top_terms`` (field bug: terms
    moved by curation were stolen back on every string-keyed surface)."""
    from cartolex.lexicon.subfields import term_to_group_maps

    doc = {
        "subfields": [{"id": 0, "label": "A"}, {"id": 1, "label": "B"}],
        "concepts": [
            {
                "id": 10,
                "subfield_id": 0,
                "label": "First",
                "term_indices": [0],
                "top_terms": ["alpha", "beta"],
            },
            {
                "id": 20,
                "subfield_id": 1,
                "label": "Second",
                "term_indices": [1],
                "top_terms": ["beta"],
            },
        ],
    }
    t2c, _c2s, _cl, _sl = term_to_group_maps(doc, ["alpha", "beta"])
    assert t2c["beta"] == 20
    assert t2c["alpha"] == 10
