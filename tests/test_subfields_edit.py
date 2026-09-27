# SPDX-License-Identifier: MIT
"""Tests for cartolex.lexicon.subfields_edit (schema 1.1 stash/trash engine).

All data is synthetic. Term indices are SVD row positions; merge groups
(``term_merges``) must travel with their canonical term.
"""

from __future__ import annotations

import pytest

from cartolex.lexicon.subfields import concept_shade, subfield_color
from cartolex.lexicon.subfields_edit import (
    carry_status,
    merge_concepts,
    merge_subfields,
    move_term,
    rename_concept,
    rename_subfield,
    restamp_colors,
    restore_concept,
    restore_term,
    set_term_status,
    stash_concept,
    stash_term,
    term_status,
    trash_concept,
    trash_subfield,
    trash_term,
    untrash_subfield,
    untrash_term,
    validate_doc,
)

_TERMS = ["t0", "t1", "t2", "t3", "t4", "t5"]


def _doc() -> dict:
    return {
        "schema_version": "1.0",
        "domain_title": "S",
        "subfields": [
            {"id": 0, "label": "SF0", "keep": True},
            {"id": 1, "label": "SF1", "keep": True},
        ],
        "concepts": [
            {
                "id": 0,
                "label": "C0",
                "subfield_id": 0,
                "term_indices": [0, 1, 2],
                "term_merges": [[1, 2]],
                "top_terms": ["ta", "tb"],
            },
            {"id": 1, "label": "C1", "subfield_id": 0, "term_indices": [3], "term_merges": []},
            {"id": 2, "label": "C2", "subfield_id": 1, "term_indices": [4, 5], "term_merges": []},
        ],
    }


def test_stash_and_restore_concept_to_other_subfield() -> None:
    doc = _doc()
    stash_concept(doc, 1)
    assert [c["id"] for c in doc["concepts"]] == [0, 2]
    assert doc["schema_version"] == "1.1"
    assert doc["stash"]["concepts"][0]["origin_subfield_id"] == 0
    assert validate_doc(doc, n_terms=6) == []

    restore_concept(doc, 1, subfield_id=1)
    c1 = next(c for c in doc["concepts"] if c["id"] == 1)
    assert c1["subfield_id"] == 1
    assert doc["stash"]["concepts"] == []
    assert validate_doc(doc, n_terms=6) == []


def test_stash_term_moves_merge_group_as_unit() -> None:
    doc = _doc()
    stash_term(doc, 2, terms_by_idx=["t0", "t1", "t2", "t3", "t4", "t5"])  # variant of group [1,2]
    c0 = doc["concepts"][0]
    assert c0["term_indices"] == [0]
    assert c0["term_merges"] == []
    entry = doc["stash"]["terms"][0]
    assert entry["term_index"] == 1  # canonical of the group
    assert entry["merge_group"] == [1, 2]
    assert entry["term"] == "t1"
    assert validate_doc(doc, n_terms=6) == []

    restore_term(doc, 1, concept_id=2)
    c2 = next(c for c in doc["concepts"] if c["id"] == 2)
    assert sorted(c2["term_indices"]) == [1, 2, 4, 5]
    assert c2["term_merges"] == [[1, 2]]
    assert validate_doc(doc, n_terms=6) == []


def test_trash_term_and_untrash() -> None:
    doc = _doc()
    trash_term(doc, 3)
    assert next(c for c in doc["concepts"] if c["id"] == 1)["term_indices"] == []
    assert doc["trash"]["terms"][0]["origin_concept_id"] == 1
    untrash_term(doc, 3, concept_id=1)
    assert next(c for c in doc["concepts"] if c["id"] == 1)["term_indices"] == [3]
    assert validate_doc(doc, n_terms=6) == []


def test_trash_subfield_takes_its_concepts() -> None:
    doc = _doc()
    trash_subfield(doc, 0)
    assert [s["id"] for s in doc["subfields"]] == [1]
    assert [c["id"] for c in doc["concepts"]] == [2]
    entry = doc["trash"]["subfields"][0]
    assert [c["id"] for c in entry["concepts"]] == [0, 1]
    assert validate_doc(doc, n_terms=6) == []

    untrash_subfield(doc, 0)
    assert sorted(s["id"] for s in doc["subfields"]) == [0, 1]
    assert sorted(c["id"] for c in doc["concepts"]) == [0, 1, 2]
    assert validate_doc(doc, n_terms=6) == []


def test_trash_concept_then_validate_blocks_duplicate_restore() -> None:
    doc = _doc()
    trash_concept(doc, 2)
    assert validate_doc(doc, n_terms=6) == []
    # Simulate a corrupt doc: the same concept both active and trashed.
    doc["concepts"].append({"id": 2, "label": "dup", "subfield_id": 1, "term_indices": []})
    assert any("Duplicate concept ids" in e for e in validate_doc(doc, n_terms=6))


def test_merge_concepts_folds_terms_and_merges() -> None:
    doc = _doc()
    merge_concepts(doc, src_id=0, dst_id=2)
    assert [c["id"] for c in doc["concepts"]] == [1, 2]
    c2 = next(c for c in doc["concepts"] if c["id"] == 2)
    assert sorted(c2["term_indices"]) == [0, 1, 2, 4, 5]
    assert c2["term_merges"] == [[1, 2]]
    assert validate_doc(doc, n_terms=6) == []


def test_merge_subfields_moves_concepts() -> None:
    doc = _doc()
    merge_subfields(doc, src_id=0, dst_id=1)
    assert [s["id"] for s in doc["subfields"]] == [1]
    assert all(c["subfield_id"] == 1 for c in doc["concepts"])
    assert validate_doc(doc, n_terms=6) == []
    with pytest.raises(ValueError):
        merge_subfields(doc, 1, 1)


def test_rename() -> None:
    doc = _doc()
    rename_subfield(doc, 0, label="Soft Matter", label_fr="Matière molle")
    rename_concept(doc, 1, label_fr="Concept un")
    assert doc["subfields"][0]["label"] == "Soft Matter"
    assert doc["subfields"][0]["label_fr"] == "Matière molle"
    assert next(c for c in doc["concepts"] if c["id"] == 1)["label_fr"] == "Concept un"
    assert next(c for c in doc["concepts"] if c["id"] == 1)["label"] == "C1"


def test_restamp_colors_follows_moves() -> None:
    doc = _doc()
    # Move C1 to SF1, then restamp: its shade must derive from SF1's hue.
    stash_concept(doc, 1)
    restore_concept(doc, 1, subfield_id=1)
    restamp_colors(doc)
    sf1 = next(s for s in doc["subfields"] if s["id"] == 1)
    assert sf1["color"] == subfield_color(1)
    mine = [c for c in doc["concepts"] if c["subfield_id"] == 1]
    assert sorted(sf1["concept_ids"]) == sorted(c["id"] for c in mine)
    for j, c in enumerate(mine):
        assert c["color"] == concept_shade(subfield_color(1), j, len(mine))


def test_restamp_colors_honours_pinned_color() -> None:
    # A curator-pinned subfield colour (e.g. a field-consistent scheme) overrides the palette
    # and its concepts shade from it — so the scheme survives re-apply and bundle import.
    doc = _doc()
    sf0 = next(s for s in doc["subfields"] if s["id"] == 0)
    sf0["pinned_color"] = "#0d8a6a"
    restamp_colors(doc)
    assert sf0["color"] == "#0d8a6a"  # pinned, not subfield_color(0)
    mine = [c for c in doc["concepts"] if c["subfield_id"] == 0]
    for j, c in enumerate(mine):
        assert c["color"] == concept_shade("#0d8a6a", j, len(mine))
    # A subfield WITHOUT a pinned colour still falls back to the palette.
    sf1 = next(s for s in doc["subfields"] if s["id"] == 1)
    assert sf1["color"] == subfield_color(1)


def test_validate_catches_partition_violations() -> None:
    doc = _doc()
    stash_term(doc, 3)
    # Corrupt: the stashed index also re-appears in an active concept.
    doc["concepts"][0]["term_indices"].append(3)
    errs = validate_doc(doc, n_terms=6)
    assert any("appears in both" in e for e in errs)

    doc2 = _doc()
    doc2["concepts"][0]["subfield_id"] = 99
    assert any("unknown subfield" in e for e in validate_doc(doc2, n_terms=6))

    doc3 = _doc()
    doc3["concepts"][0]["term_indices"] = [0, 99]
    assert any("out of range" in e for e in validate_doc(doc3, n_terms=6))


def test_unknown_ids_raise() -> None:
    doc = _doc()
    with pytest.raises(ValueError):
        stash_concept(doc, 42)
    with pytest.raises(ValueError):
        stash_term(doc, 42)
    with pytest.raises(ValueError):
        restore_term(doc, 42, concept_id=0)


# ── Term statuses (three-status attribution model) ───────────────────────────


def test_set_term_status_writes_exclusive_lists_and_validates() -> None:
    doc = _doc()
    set_term_status(doc, 0, "ride_along", _TERMS)
    assert doc["concepts"][0]["ride_along_terms"] == ["t0"]
    assert term_status(doc["concepts"][0], "t0") == "ride_along"
    set_term_status(doc, 0, "subfield_only", _TERMS)
    assert "ride_along_terms" not in doc["concepts"][0]
    assert doc["concepts"][0]["subfield_only_terms"] == ["t0"]
    set_term_status(doc, 0, "defining", _TERMS)
    assert "subfield_only_terms" not in doc["concepts"][0]
    assert validate_doc(doc, terms_by_idx=_TERMS) == []


def test_set_term_status_targets_the_canonical_of_a_merge_group() -> None:
    doc = _doc()
    set_term_status(doc, 2, "ride_along", _TERMS)  # t2 is a variant of t1
    assert doc["concepts"][0]["ride_along_terms"] == ["t1"]


def test_set_term_status_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="Unknown term status"):
        set_term_status(_doc(), 0, "hub", _TERMS)


def test_carry_rule() -> None:
    assert carry_status("ride_along", 0, 1) == "ride_along"
    assert carry_status("subfield_only", 0, 0) == "subfield_only"
    assert carry_status("subfield_only", 0, 1) == "defining"
    assert carry_status("subfield_only", None, 1) == "defining"
    assert carry_status("defining", 0, 0) == "defining"
    assert carry_status(None, 0, 0) == "defining"


def test_move_term_carries_ride_along_and_resets_subfield_only_across_subfields() -> None:
    doc = _doc()
    set_term_status(doc, 0, "ride_along", _TERMS)
    set_term_status(doc, 3, "subfield_only", _TERMS)  # concept 1, subfield 0
    move_term(doc, 0, 2, _TERMS)  # subfield 0 → subfield 1
    move_term(doc, 3, 2, _TERMS)
    c2 = doc["concepts"][-1]
    assert c2["ride_along_terms"] == ["t0"]
    assert "subfield_only_terms" not in c2  # reset across subfields
    assert "ride_along_terms" not in doc["concepts"][0]  # erased from the source
    assert validate_doc(doc, terms_by_idx=_TERMS) == []


def test_move_term_within_subfield_keeps_subfield_only() -> None:
    doc = _doc()
    set_term_status(doc, 3, "subfield_only", _TERMS)
    move_term(doc, 3, 0, _TERMS)  # concept 1 → concept 0, both in subfield 0
    assert doc["concepts"][0]["subfield_only_terms"] == ["t3"]


def test_stash_and_restore_term_round_trips_status_through_the_carry_rule() -> None:
    doc = _doc()
    set_term_status(doc, 1, "subfield_only", _TERMS)
    stash_term(doc, 1, _TERMS)
    entry = doc["stash"]["terms"][0]
    assert entry["status"] == "subfield_only" and entry["merge_group"] == [1, 2]
    assert "subfield_only_terms" not in doc["concepts"][0]
    restore_term(doc, 1, 1)  # concept 1, same subfield 0 → status kept
    assert doc["concepts"][1]["subfield_only_terms"] == ["t1"]
    trash_term(doc, 1, _TERMS)
    untrash_term(doc, 1, 2)  # concept 2, subfield 1 → reset
    assert "subfield_only_terms" not in doc["concepts"][2]
    assert validate_doc(doc, terms_by_idx=_TERMS) == []


def test_detaching_from_a_statused_concept_needs_the_vocabulary() -> None:
    doc = _doc()
    set_term_status(doc, 0, "ride_along", _TERMS)
    with pytest.raises(ValueError, match="terms_by_idx is required"):
        stash_term(doc, 0)


def test_merge_concepts_unions_status_lists() -> None:
    doc = _doc()
    set_term_status(doc, 0, "ride_along", _TERMS)
    set_term_status(doc, 3, "ride_along", _TERMS)
    merge_concepts(doc, 1, 0)
    assert sorted(doc["concepts"][0]["ride_along_terms"]) == ["t0", "t3"]


def test_validate_reports_conflicting_and_stale_status_strings() -> None:
    doc = _doc()
    doc["concepts"][0]["ride_along_terms"] = ["t0", "ghost"]
    doc["concepts"][0]["subfield_only_terms"] = ["T0 "]
    problems = validate_doc(doc, terms_by_idx=_TERMS)
    assert any("both subfield-only and ride-along" in p for p in problems)
    assert any("'ghost' is not one of its terms" in p for p in problems)
    # Without the vocabulary only the conflict is checkable.
    assert [p for p in validate_doc(doc) if "ghost" in p] == []
