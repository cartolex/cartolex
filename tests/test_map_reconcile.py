# SPDX-License-Identifier: MIT
"""Tests for the cross-cohort vocabulary reconciliation (cartolex.atlas.reconcile).

Synthetic math/physics vocabularies only — no real workspace data.
"""

from __future__ import annotations

import numpy as np
import pytest

from cartolex.atlas.reconcile import (
    CohortSense,
    ReconciliationTable,
    Thresholds,
    build_cohort_senses,
    build_naive_table,
    load_decisions,
    normalize_surface,
    pair_key,
    reconcile,
    save_decisions,
    sense_text,
    weighted_jaccard,
)


# ── R0 surface normalization ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Neural Networks", "neural network"),
        ("self-organisation", "self organization"),
        ("behaviour", "behavior"),
        ("modelling", "modeling"),
        ("fibre optics", "fiber optic"),
        ("réseaux", "réseau"),  # French plural via canonical_singular
        ("promised", "promised"),  # suffix blocklist: not a UK spelling
        ("ionisation energies", "ionization energy"),
        ("  Symplectic   Geometry ", "symplectic geometry"),
        ("colour centres", "color center"),
    ],
)
def test_normalize_surface_folding(raw: str, expected: str) -> None:
    assert normalize_surface(raw) == expected


def test_normalize_surface_idempotent() -> None:
    for raw in ("Self-Organisation", "colour centres", "spectral gap", "réseaux"):
        once = normalize_surface(raw)
        assert normalize_surface(once) == once


# ── R1 sense building ─────────────────────────────────────────────────────────
def test_build_cohort_senses_folds_variants_and_contexts() -> None:
    terms = ["colour centre", "color center", "photonics", "laser"]
    # 6 researchers; the two spelling variants are used by different researchers,
    # so the folded sense's df is the union of their users.
    X_tf = np.array(
        [
            [2.0, 0.0, 1.0, 0.0],
            [0.0, 3.0, 1.0, 0.0],
            [1.0, 0.0, 0.0, 1.0],
            [0.0, 0.0, 1.0, 1.0],
            [0.0, 1.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    senses = build_cohort_senses("c1", terms, X_tf, concept_of_term={"laser": "Optics"})
    by_surface = {s.surface: s for s in senses}
    folded = by_surface["color center"]
    assert folded.source_terms == ["color center", "colour centre"]
    assert folded.df == 4  # researchers 0, 1, 2, 4
    assert folded.tf_total == pytest.approx(7.0)
    assert by_surface["laser"].concept_label == "Optics"
    assert folded.context, "folded sense should have a PPMI context"
    assert all(w > 0 for _, w in folded.context)


def test_build_cohort_senses_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="does not match vocabulary"):
        build_cohort_senses("c1", ["a", "b"], np.zeros((3, 5)))


def test_sense_text_mentions_concept_and_context() -> None:
    s = CohortSense(
        cohort_id="c2",
        surface="spectral gap",
        source_terms=["spectral gap"],
        df=5,
        tf_total=9.0,
        context=[("eigenvalue", 2.0), ("markov chain", 1.5)],
        concept_label="Spectral theory",
    )
    text = sense_text(s)
    assert "spectral gap" in text and "Spectral theory" in text and "eigenvalue" in text


def test_weighted_jaccard_bounds() -> None:
    a = [("eigenvalue", 1.0), ("operator", 1.0)]
    assert weighted_jaccard(a, a) == pytest.approx(1.0)
    assert weighted_jaccard(a, [("laser", 1.0)]) == 0.0


# ── R2/R3 reconciliation logic ─────────────────────────────────────────────────
def _sense(
    cohort: str, surface: str, context: list[str], *, df: int = 5, concept: str = ""
) -> CohortSense:
    return CohortSense(
        cohort_id=cohort,
        surface=surface,
        source_terms=[surface],
        df=df,
        tf_total=float(df),
        context=[(c, 1.0) for c in context],
        concept_label=concept,
    )


def test_same_surface_polysemy_splits() -> None:
    # "spectrum" means operator spectrum in math (c2), emission spectrum in physics (c1).
    a = _sense("c2", "spectrum", ["eigenvalue", "operator", "hilbert space", "resolvent"])
    b = _sense("c1", "spectrum", ["emission", "wavelength", "laser", "photoluminescence"])
    table = reconcile([[a], [b]])
    assert set(table.groups) == {"spectrum#c1", "spectrum#c2"}
    assert table.stats["same_surface"]["split"] == 1
    assert table.sense_of("c2", "spectrum") == "spectrum#c2"
    assert table.sense_of("c1", "spectrum") == "spectrum#c1"


def test_same_surface_shared_context_merges() -> None:
    # "phase transition" carries the same meaning in both cohorts.
    shared = ["critical exponent", "order parameter", "universality"]
    a = _sense("c2", "phase transition", [*shared, "percolation"])
    b = _sense("c1", "phase transition", [*shared, "ising model"])
    table = reconcile([[a], [b]])
    assert set(table.groups) == {"phase transition"}
    members = table.groups["phase transition"]
    assert {m["cohort_id"] for m in members} == {"c1", "c2"}
    assert table.stats["same_surface"]["merged"] == 1
    assert table.sense_of("c2", "phase transition") == "phase transition"
    assert table.sense_cohorts()["phase transition"] == ["c1", "c2"]


def test_gray_zone_pending_defaults_to_split_and_cache_overrides() -> None:
    # 1 shared context of 5+5 ⇒ weighted Jaccard 1/9 ≈ 0.11 — between the
    # split (0.05) and merge (0.15) thresholds ⇒ gray ⇒ pending, kept split.
    a = _sense("c2", "diffusion", ["heat kernel", "brownian motion", "pde", "semigroup", "graph"])
    b = _sense("c1", "diffusion", ["brownian motion", "colloid", "tracer", "viscosity", "msd"])
    table = reconcile([[a], [b]])
    assert set(table.groups) == {"diffusion#c1", "diffusion#c2"}
    assert table.stats["same_surface"]["pending"] == 1
    assert len(table.pending) == 1
    entry = table.pending[0]
    assert entry["kind"] == "ss"
    assert 0.05 < entry["scores"]["jaccard"] < 0.15

    # An adjudication cache entry overrides the gray zone.
    decisions = {entry["key"]: "merge"}
    table2 = reconcile([[a], [b]], decisions=decisions)
    assert set(table2.groups) == {"diffusion"}
    assert table2.decisions_applied == decisions
    assert not table2.pending


def test_cross_surface_synonym_merge_with_embedder() -> None:
    shared = ["martingale", "markov chain", "stationarity"]
    a = _sense("c2", "stochastic process", [*shared, "ito calculus"])
    b = _sense("c1", "random process", [*shared, "noise"])
    # Distinct-context sense that must NOT merge with anything.
    c = _sense("c1", "quantum entanglement", ["bell inequality", "qubit", "decoherence"])

    def embedder(texts: list[str]) -> np.ndarray:
        vecs = []
        for t in texts:
            if t.startswith(("stochastic process", "random process")):
                vecs.append([1.0, 0.05, 0.0])  # near-identical pair
            else:
                vecs.append([0.0, 0.0, 1.0])
        return np.asarray(vecs)

    table = reconcile([[a], [b, c]], embedder=embedder, embedder_fingerprint="fake/1")
    merged = [gid for gid, members in table.groups.items() if len(members) == 2]
    assert len(merged) == 1
    gid = merged[0]
    assert table.sense_of("c2", "stochastic process") == gid
    assert table.sense_of("c1", "random process") == gid
    assert table.sense_of("c1", "quantum entanglement") == "quantum entanglement"
    assert table.stats["cross_surface"]["merged"] == 1
    assert table.embedder_fingerprint == "fake/1"


def test_transitive_conflict_breaks_component() -> None:
    # Forced merges c2~c1 and c1~c3 with a forced split c2≁c3 ⇒ conflict ⇒
    # the whole component is broken back to singletons for re-adjudication.
    a = _sense("c2", "manifold", ["curvature", "riemannian", "geodesic"])
    b = _sense("c1", "manifold", ["curvature", "riemannian", "phase space"])
    c = _sense("c3", "manifold", ["curvature", "riemannian", "attractor"])
    decisions = {
        pair_key("ss", a, b): "merge",
        pair_key("ss", b, c): "merge",
        pair_key("ss", a, c): "split",
    }
    table = reconcile([[a], [b], [c]], decisions=decisions)
    assert set(table.groups) == {"manifold#c1", "manifold#c3", "manifold#c2"}
    assert len(table.conflicts) == 1
    assert table.stats["n_conflicts"] == 1
    # The broken merge edges are queued for re-adjudication.
    conflict_pending = [p for p in table.pending if p["scores"].get("conflict")]
    assert len(conflict_pending) == 2


def test_determinism_and_serialization_roundtrip(tmp_path) -> None:
    shared = ["critical exponent", "order parameter", "universality"]
    cohorts = [
        [
            _sense("c2", "phase transition", [*shared, "percolation"]),
            _sense("c2", "spectrum", ["eigenvalue", "operator", "hilbert space"]),
        ],
        [
            _sense("c1", "phase transition", [*shared, "ising model"]),
            _sense("c1", "spectrum", ["emission", "wavelength", "laser"]),
        ],
    ]
    t1 = reconcile(cohorts)
    t2 = reconcile(cohorts)
    assert t1.to_json_dict() == t2.to_json_dict()

    path = t1.write_json(tmp_path / "table.json")
    import json

    loaded = ReconciliationTable.from_json_dict(json.loads(path.read_text(encoding="utf-8")))
    assert loaded.to_json_dict() == t1.to_json_dict()
    assert loaded.sense_ids == t1.sense_ids

    # A table of the previous format version (another member key) is refused.
    old = {**t1.to_json_dict(), "schema": "map_reconcile/1"}
    with pytest.raises(ValueError, match="map_reconcile/2"):
        ReconciliationTable.from_json_dict(old)


def test_decisions_roundtrip_and_validation(tmp_path) -> None:
    path = save_decisions({"ss::x::c1::c2": "merge"}, tmp_path / "decisions.json")
    assert load_decisions(path) == {"ss::x::c1::c2": "merge"}
    assert load_decisions(tmp_path / "missing.json") == {}
    path.write_text('{"schema": "wrong", "decisions": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="map_reconcile_decisions/1"):
        load_decisions(path)


def test_naive_table_merges_all_same_surface_without_splits() -> None:
    a = _sense("c2", "spectrum", ["eigenvalue", "operator"])
    b = _sense("c1", "spectrum", ["emission", "wavelength"])  # polysemy — naive merges anyway
    table = build_naive_table([[a], [b]])
    assert set(table.groups) == {"spectrum"}
    assert table.stats.get("naive") is True
    assert table.sense_of("c2", "spectrum") == "spectrum"


def test_thresholds_serialization() -> None:
    th = Thresholds()
    d = th.to_dict()
    assert d["tau_merge"] == pytest.approx(0.60)
    assert d["cross_cos_auto"] == pytest.approx(0.90)
