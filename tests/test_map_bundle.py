# SPDX-License-Identifier: MIT
"""Tests for the portable cohort-bundle serializer (cartolex.atlas.map_bundle).

Synthetic math/physics vocabularies only — no real workspace data.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cartolex.atlas.map_bundle import (
    BUNDLE_SCHEMA,
    TAXONOMY_SCHEMA,
    CohortBundle,
    build_bundle,
    read_bundle,
    validate_bundle,
    validate_taxonomy,
    write_bundle,
)
from cartolex.atlas.map_merge import CohortInput, assemble_joint_matrix
from cartolex.atlas.reconcile import build_cohort_senses, build_naive_table


def _cohort_c2() -> CohortInput:
    return CohortInput(
        cohort_id="c2",
        terms=["spectral gap", "markov chain", "phase transition", "spectrum"],
        X_tf=np.array(
            [
                [2.0, 1.0, 3.0, 0.0],
                [0.0, 2.0, 1.0, 4.0],
            ]
        ),
        researcher_ids=["r000000", "r000001"],
        units=["GRP1", "GRP2"],
    )


def _cohort_c1() -> CohortInput:
    return CohortInput(
        cohort_id="c1",
        terms=["phase transition", "laser", "spectrum"],
        X_tf=np.array(
            [
                [1.0, 0.0, 2.0],
                [0.0, 3.0, 1.0],
                [2.0, 1.0, 0.0],
            ]
        ),
        researcher_ids=["r000000", "r000001", "r000002"],
        units=["GRP2", "GRP3", "GRP3"],
    )


TAXONOMY_DOC = {
    "schema": TAXONOMY_SCHEMA,
    "cohort_id": "c2",
    "cohort_name": "mathématiques",
    "weights_basis": "svd-100",
    "clustering_signature": "abc123",
    "subfields": [{"id": 0, "label": "Stochastics", "label_fr": "Stochastique", "color": "#fff"}],
    "concepts": [
        {
            "id": 0,
            "label": "Stochastics",
            "label_fr": "Stochastique",
            "subfield_id": 0,
            "terms": ["markov chain", "spectral gap"],
        }
    ],
}


# ── build_bundle basics ─────────────────────────────────────────────────────


def test_build_bundle_computes_vocabulary_and_counters() -> None:
    bundle = build_bundle(_cohort_c2(), producer="test-suite", build_date="2026-07-12")
    assert bundle.meta["schema"] == BUNDLE_SCHEMA
    assert bundle.meta["cohort_id"] == "c2"
    assert bundle.meta["producer"] == "test-suite"
    assert bundle.meta["build_date"] == "2026-07-12"
    assert "profile" not in bundle.meta  # opaque profile absent when not given
    assert bundle.meta["counters"] == {"n_entities": 2, "n_terms": 4, "n_term_rows": 6}
    assert set(bundle.vocabulary["term"]) == {
        "spectral gap",
        "markov chain",
        "phase transition",
        "spectrum",
    }
    row = bundle.vocabulary.set_index("term").loc["markov chain"]
    assert row["n_entities"] == 2
    assert row["tf_total"] == pytest.approx(3.0)


def test_build_bundle_carries_opaque_profile_untouched() -> None:
    bundle = build_bundle(_cohort_c2(), profile={"tier": "external", "note": "consumer-only"})
    assert bundle.meta["profile"] == {"tier": "external", "note": "consumer-only"}


# ── Round-trip: directory and zip ───────────────────────────────────────────


@pytest.mark.parametrize("suffix", ["", ".zip"])
def test_round_trip_matrices_terms_ids_units(tmp_path: Path, suffix: str) -> None:
    cohort = _cohort_c2()
    scores = cohort.X_tf * 2.0  # fake boosted score track
    bundle = build_bundle(cohort, scores=scores, taxonomy=TAXONOMY_DOC, decisions={"a::b": "merge"})

    dest = tmp_path / ("bundle" + suffix)
    write_bundle(bundle, dest)
    loaded = read_bundle(dest)

    reconstructed = loaded.to_cohort_input()
    assert reconstructed.cohort_id == cohort.cohort_id
    assert reconstructed.terms == sorted(cohort.terms)
    # Reorder original columns to match the reconstructed (sorted) term order.
    col_of = {t: j for j, t in enumerate(cohort.terms)}
    expected = cohort.X_tf[:, [col_of[t] for t in reconstructed.terms]]
    np.testing.assert_allclose(reconstructed.X_tf, expected)
    assert reconstructed.researcher_ids == sorted(cohort.researcher_ids)
    row_of = {r: i for i, r in enumerate(cohort.researcher_ids)}
    expected_units = [cohort.units[row_of[r]] for r in reconstructed.researcher_ids]
    assert reconstructed.units == expected_units

    assert loaded.taxonomy == TAXONOMY_DOC
    assert loaded.decisions == {"a::b": "merge"}


def test_round_trip_score_track_and_missing_scores(tmp_path: Path) -> None:
    cohort = _cohort_c2()
    scores = cohort.X_tf * 3.0
    bundle = build_bundle(cohort, scores=scores)
    write_bundle(bundle, tmp_path / "bundle")
    loaded = read_bundle(tmp_path / "bundle")
    merged = loaded.terms_long.merge(
        bundle.terms_long, on=["entity_id", "term"], suffixes=("_loaded", "_built")
    )
    np.testing.assert_allclose(merged["score_loaded"], merged["score_built"])
    assert (merged["score_loaded"] > 0).all()

    # No scores at all -> NaN/empty tolerated end to end.
    bundle_noscore = build_bundle(cohort)
    write_bundle(bundle_noscore, tmp_path / "bundle_noscore")
    loaded_noscore = read_bundle(tmp_path / "bundle_noscore")
    assert loaded_noscore.terms_long["score"].isna().all()
    assert loaded_noscore.vocabulary["score_total"].isna().all()


def test_round_trip_preserves_zero_usage_terms(tmp_path: Path) -> None:
    # A term no entity uses has no entity_terms.csv row but must survive the
    # round-trip through vocabulary.csv (the vocabulary is computed from the
    # CohortInput, not from the sparse rows).
    cohort = CohortInput(
        cohort_id="c2",
        terms=["spectral gap", "unused term"],
        X_tf=np.array([[2.0, 0.0], [1.0, 0.0]]),
        researcher_ids=["r000000", "r000001"],
    )
    bundle = build_bundle(cohort)
    assert bundle.meta["counters"] == {"n_entities": 2, "n_terms": 2, "n_term_rows": 2}
    write_bundle(bundle, tmp_path / "bundle")
    reconstructed = read_bundle(tmp_path / "bundle").to_cohort_input()
    assert reconstructed.terms == ["spectral gap", "unused term"]
    np.testing.assert_allclose(reconstructed.X_tf, cohort.X_tf)
    assert reconstructed.units == ["", ""]  # units=None round-trips as ""


def test_entities_extra_facet_columns_pass_through(tmp_path: Path) -> None:
    bundle = build_bundle(_cohort_c2())
    bundle.entities["cohort_facet"] = ["senior", "junior"]  # consumer-added facet
    write_bundle(bundle, tmp_path / "bundle")
    loaded = read_bundle(tmp_path / "bundle")
    assert list(loaded.entities.columns) == ["entity_id", "unit", "cohort_facet"]
    assert sorted(loaded.entities["cohort_facet"]) == ["junior", "senior"]


# ── Forward-compat ──────────────────────────────────────────────────────────


def test_reader_preserves_unknown_meta_keys_and_ignores_extra_files(tmp_path: Path) -> None:
    bundle = build_bundle(_cohort_c2())
    dest = tmp_path / "bundle"
    write_bundle(bundle, dest)

    meta_path = dest / "bundle_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["future_field"] = {"nested": [1, 2, 3]}
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    (dest / "future_envelope.json").write_text("{}", encoding="utf-8")

    loaded = read_bundle(dest)
    assert loaded.meta["future_field"] == {"nested": [1, 2, 3]}
    # The unknown file is silently ignored, not surfaced anywhere in the model.
    assert not hasattr(loaded, "future_envelope")


# ── Determinism ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("suffix", ["", ".zip"])
def test_write_bundle_is_byte_identical_across_writes(tmp_path: Path, suffix: str) -> None:
    bundle = build_bundle(
        _cohort_c2(),
        scores=_cohort_c2().X_tf * 2.0,
        taxonomy=TAXONOMY_DOC,
        decisions={"a::b": "merge"},
        build_date="2026-07-12",
        producer="test-suite",
    )
    dest1 = tmp_path / ("one" + suffix)
    dest2 = tmp_path / ("two" + suffix)
    write_bundle(bundle, dest1)
    write_bundle(bundle, dest2)

    if suffix == ".zip":
        assert dest1.read_bytes() == dest2.read_bytes()
    else:
        for name in (
            "bundle_meta.json",
            "entity_terms.csv",
            "entities.csv",
            "vocabulary.csv",
            "taxonomy.json",
            "decisions.json",
        ):
            assert (dest1 / name).read_bytes() == (dest2 / name).read_bytes(), name


# ── Schema mismatch ──────────────────────────────────────────────────────────


def test_read_bundle_rejects_unsupported_schema_major(tmp_path: Path) -> None:
    bundle = build_bundle(_cohort_c2())
    dest = tmp_path / "bundle"
    write_bundle(bundle, dest)
    meta_path = dest / "bundle_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema"] = "map_bundle/4"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ValueError, match="map_bundle/4") as excinfo:
        read_bundle(dest)
    assert BUNDLE_SCHEMA in str(excinfo.value)


def test_read_bundle_rejects_the_previous_major(tmp_path: Path) -> None:
    """Bundles of the previous major version (another identity key) are not read."""
    dest = tmp_path / "bundle"
    write_bundle(build_bundle(_cohort_c2()), dest)
    meta_path = dest / "bundle_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema"] = "map_bundle/1"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported bundle schema major"):
        read_bundle(dest)


def test_read_bundle_reports_schema_mismatch_before_missing_file(tmp_path: Path) -> None:
    # A future major version may restructure the required-files list entirely;
    # a schema-major mismatch must be reported as such, not masked by a
    # "missing required file(s)" error computed against *this* engine's list.
    bundle = build_bundle(_cohort_c2())
    dest = tmp_path / "bundle"
    write_bundle(bundle, dest)
    meta_path = dest / "bundle_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema"] = "map_bundle/4"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    (dest / "vocabulary.csv").unlink()  # also missing a file this engine requires

    with pytest.raises(ValueError, match="Unsupported bundle schema major") as excinfo:
        read_bundle(dest)
    assert "missing required file" not in str(excinfo.value).lower()


# ── validate_bundle failure modes ──────────────────────────────────────────


def _valid_bundle() -> CohortBundle:
    return build_bundle(_cohort_c2())


def test_validate_bundle_rejects_phantom_entity_id() -> None:
    bundle = _valid_bundle()
    bad_row = pd.DataFrame([{"entity_id": "ghost", "term": "spectrum", "tf": 1.0, "score": np.nan}])
    bundle.terms_long = pd.concat([bundle.terms_long, bad_row], ignore_index=True)
    with pytest.raises(ValueError, match="entity"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_duplicate_entity_term_pair() -> None:
    bundle = _valid_bundle()
    dup = bundle.terms_long.iloc[[0]]
    bundle.terms_long = pd.concat([bundle.terms_long, dup], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_negative_tf() -> None:
    bundle = _valid_bundle()
    bundle.terms_long.loc[0, "tf"] = -1.0
    with pytest.raises(ValueError, match="negative"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_vocabulary_terms_mismatch() -> None:
    bundle = _valid_bundle()
    extra = pd.DataFrame(
        [{"entity_id": "r000000", "term": "not in vocab", "tf": 1.0, "score": np.nan}]
    )
    bundle.terms_long = pd.concat([bundle.terms_long, extra], ignore_index=True)
    with pytest.raises(ValueError, match="vocabulary"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_duplicate_entity_id_in_entities() -> None:
    bundle = _valid_bundle()
    dup_row = bundle.entities.iloc[[0]]
    bundle.entities = pd.concat([bundle.entities, dup_row], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate entity_id"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_duplicate_term_in_vocabulary() -> None:
    bundle = _valid_bundle()
    dup_row = bundle.vocabulary.iloc[[0]]
    bundle.vocabulary = pd.concat([bundle.vocabulary, dup_row], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate term"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_missing_cohort_id() -> None:
    bundle = _valid_bundle()
    del bundle.meta["cohort_id"]
    with pytest.raises(ValueError, match="cohort_id"):
        validate_bundle(bundle)


def test_validate_bundle_rejects_empty_cohort_id() -> None:
    bundle = _valid_bundle()
    bundle.meta["cohort_id"] = ""
    with pytest.raises(ValueError, match="cohort_id"):
        validate_bundle(bundle)


def test_validate_bundle_accepts_a_freshly_built_bundle() -> None:
    validate_bundle(_valid_bundle())  # no raise


# ── End-to-end: bundles feed the merge pipeline exactly like in-memory inputs ──


def test_end_to_end_matches_in_memory_pipeline(tmp_path: Path) -> None:
    cohorts = [_cohort_c2(), _cohort_c1()]
    bundles_dir = tmp_path / "bundles"
    read_back: list[CohortInput] = []
    for cohort in cohorts:
        bundle = build_bundle(cohort)
        dest = bundles_dir / cohort.cohort_id
        write_bundle(bundle, dest)
        read_back.append(read_bundle(dest).to_cohort_input())

    def _joint(inputs: list[CohortInput]):
        senses = [
            build_cohort_senses(s.cohort_id, s.terms, s.X_tf)
            for s in sorted(inputs, key=lambda s: s.cohort_id)
        ]
        table = build_naive_table(senses)
        return assemble_joint_matrix(inputs, table)

    joint_original = _joint(cohorts)
    joint_from_bundles = _joint(read_back)

    assert joint_from_bundles.sense_ids == joint_original.sense_ids
    # Row order may differ only if researcher ids differ, which they don't here.
    assert joint_from_bundles.researcher_ids == joint_original.researcher_ids
    np.testing.assert_allclose(joint_from_bundles.T, joint_original.T)


# ── validate_taxonomy ───────────────────────────────────────────────────────


def test_validate_taxonomy_accepts_the_documented_shape() -> None:
    validate_taxonomy(TAXONOMY_DOC)  # no raise


def test_validate_taxonomy_rejects_missing_schema() -> None:
    doc = dict(TAXONOMY_DOC)
    del doc["schema"]
    with pytest.raises(ValueError, match="map_taxonomy/1"):
        validate_taxonomy(doc)


# ── Example smoke ────────────────────────────────────────────────────────────


def test_merge_two_cohorts_example_runs_offline(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parent.parent / "examples" / "merge_two_cohorts.py"
    result = subprocess.run(
        [sys.executable, str(example)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "joint" in result.stdout.lower() or "shape" in result.stdout.lower()
