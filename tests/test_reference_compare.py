# SPDX-License-Identifier: MIT
"""Unit tests of the reference comparison (``tools/reference/compare.py``).

Fast and engine-free: small synthetic runs are written with the runner's own
artifact writers, then compared unchanged, with floating-point noise, and with
real perturbations.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools" / "reference"


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


compare = _load("reference_compare", "compare.py")
runner = _load("reference_run", "run.py")


# ── metrics ──────────────────────────────────────────────────────────────────


def test_adjusted_rand_index_known_values() -> None:
    assert compare.adjusted_rand_index([0, 0, 1, 1, 2], [0, 0, 1, 1, 2]) == pytest.approx(1.0)
    assert compare.adjusted_rand_index([0, 0, 1, 1, 2], [5, 5, 3, 3, 9]) == pytest.approx(1.0)
    assert compare.adjusted_rand_index([0, 0, 1, 1], [0, 1, 0, 1]) == pytest.approx(-0.5)
    assert compare.adjusted_rand_index([0, 0, 0, 1, 1, 1], [0, 0, 1, 1, 2, 2]) < 0.5


def test_procrustes_ignores_rotation_scale_and_shift() -> None:
    rng = np.random.default_rng(0)
    pts = rng.normal(size=(50, 2))
    angle = 0.7
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    moved = 3.0 * pts @ rot + np.array([5.0, -2.0])
    assert compare.procrustes_disparity(pts, moved) < 1e-12
    assert compare.procrustes_disparity(pts, rng.normal(size=(50, 2))) > 0.1


def test_knn_preservation() -> None:
    rng = np.random.default_rng(1)
    pts = rng.normal(size=(60, 2))
    assert compare.knn_preservation(pts, pts.copy()) == pytest.approx(1.0)
    assert compare.knn_preservation(pts, rng.permutation(pts)) < 0.5


def test_principal_angles_see_subspaces_not_bases() -> None:
    rng = np.random.default_rng(2)
    Z = rng.normal(size=(40, 3))
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    assert compare.principal_angles(Z, Z @ q).max() < 1e-7
    assert compare.principal_angles(Z, rng.normal(size=(40, 3))).max() > 0.1


def test_embedding_sign_flips_are_not_differences() -> None:
    rng = np.random.default_rng(3)
    Z = rng.normal(size=(30, 4))
    flipped = Z * np.array([1.0, -1.0, 1.0, -1.0])
    verdict, _ = compare.compare_arrays(Z, flipped, "embedding")
    assert verdict == "within tolerance"
    verdict, detail = compare.compare_arrays(Z, Z + 1e-3, "embedding")
    assert verdict == "different", detail


def test_layout_tolerance() -> None:
    rng = np.random.default_rng(4)
    xy = rng.normal(size=(40, 2))
    assert compare.compare_arrays(xy, xy * (1 + 1e-12), "layout")[0] == "within tolerance"
    assert compare.compare_arrays(xy, xy + rng.normal(scale=0.5, size=xy.shape), "layout")[0] == (
        "different"
    )


def test_labels_renamed_versus_moved() -> None:
    assert compare.compare_labels([0, 0, 1, 2], [2, 2, 0, 1])[0] == "within tolerance"
    verdict, detail = compare.compare_labels([0, 0, 1, 2], [0, 1, 1, 2])
    assert verdict == "different" and "ARI" in detail


def test_documents_structure_and_floats() -> None:
    doc = {"a": [1, 2.5, {"b": "x"}], "c": 0.1}
    noisy = {"a": [1, 2.5 * (1 + 1e-12), {"b": "x"}], "c": 0.1}
    assert compare.compare_documents(doc, noisy)[0] == "within tolerance"
    assert compare.compare_documents(doc, {**doc, "c": 0.2})[0] == "different"
    assert compare.compare_documents(doc, {**doc, "d": 1})[0] == "different"
    assert compare.compare_documents(doc, {"a": [1, 2.5, {"b": "y"}], "c": 0.1})[0] == "different"


def test_strings_set_then_order() -> None:
    assert compare.compare_strings(["a", "b"], ["a", "c"])[0] == "different"
    verdict, detail = compare.compare_strings(["a", "b"], ["b", "a"])
    assert verdict == "different" and "order" in detail


# ── whole runs ───────────────────────────────────────────────────────────────


def _artifacts(scale: float = 1.0, *, label_shift: bool = False, extra_row: bool = False) -> dict:
    """Two stages of synthetic artifacts; *scale* multiplies every float."""
    rng = np.random.default_rng(7)
    terms = ["alpha", "beta", "gamma", "delta"]
    frame = pd.DataFrame({"term": terms, "score": [0.5, 0.25, 0.125, 0.0625]})
    frame["score"] *= scale
    if extra_row:
        frame = pd.concat([frame, pd.DataFrame({"term": ["eps"], "score": [0.01]})])
    labels = [0, 0, 1, 1] if not label_shift else [0, 1, 1, 1]
    coords = rng.normal(size=(4, 2)) * scale
    return {
        "first": {
            "terms": runner.Table(frame, keys=["term"]),
            "vocabulary": runner.Strings(terms),
            "summary": runner.Document({"n": 4, "mean": 0.2 * scale}),
        },
        "second": {
            "labels": runner.Labels(labels),
            "xy": runner.Array(coords, role="layout"),
            "copy": runner.HashOnly(runner.Strings(terms)),
        },
    }


def _write_run(root: Path, artifacts: dict) -> Path:
    root.mkdir(parents=True)
    manifest = {
        "format": runner.FORMAT,
        "mode": "single",
        "stages": list(artifacts),
        "artifacts": {s: runner.write_stage(root, s, arts) for s, arts in artifacts.items()},
        "inputs": {"workspace_sha256": "0" * 64},
        "settings": {},
        "environment": {"python": "3.12"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_identical_runs(tmp_path: Path) -> None:
    ref = _write_run(tmp_path / "ref", _artifacts())
    cur = _write_run(tmp_path / "cur", _artifacts())
    report = compare.compare_runs(ref, cur)
    assert [s.verdict for s in report.stages] == ["identical", "identical"]
    assert report.summary_line() == "2 stages: 2 identical"
    assert compare.main(["--reference", str(ref), "--current", str(cur)]) == 0


def test_tiny_change_is_within_tolerance(tmp_path: Path) -> None:
    ref = _write_run(tmp_path / "ref", _artifacts())
    cur = _write_run(tmp_path / "cur", _artifacts(scale=1 + 1e-12))
    report = compare.compare_runs(ref, cur)
    assert report.verdict == "within tolerance", compare.render_markdown(report)
    assert "within tolerance" in report.summary_line()
    assert compare.main(["--reference", str(ref), "--current", str(cur)]) == 0


def test_perturbed_run_is_different(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ref = _write_run(tmp_path / "ref", _artifacts())
    cur = _write_run(tmp_path / "cur", _artifacts(scale=1.01, label_shift=True, extra_row=True))
    report = compare.compare_runs(ref, cur)
    by_name = {a.name: a for s in report.stages for a in s.artifacts}
    assert by_name["terms"].verdict == "different"
    assert "1 added" in by_name["terms"].detail
    assert by_name["summary"].verdict == "different"
    assert by_name["labels"].verdict == "different"
    assert by_name["copy"].verdict == "identical"
    out_md = tmp_path / "report.md"
    rc = compare.main(["--reference", str(ref), "--current", str(cur), "--report", str(out_md)])
    assert rc == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == "2 stages: 0 identical, 2 different (first, second)"
    assert "| first | different |" in out_md.read_text(encoding="utf-8")


def test_missing_artifact_and_other_inputs(tmp_path: Path) -> None:
    ref = _write_run(tmp_path / "ref", _artifacts())
    reduced = _artifacts()
    del reduced["second"]["xy"]
    cur = _write_run(tmp_path / "cur", reduced)
    report = compare.compare_runs(ref, cur)
    assert report.stages[1].verdict == "different"
    assert "absent from current" in report.stages[1].summary

    other = tmp_path / "other"
    shutil.copytree(ref, other)
    manifest = json.loads((other / "manifest.json").read_text(encoding="utf-8"))
    manifest["inputs"]["workspace_sha256"] = "1" * 64
    (other / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert compare.main(["--reference", str(ref), "--current", str(other)]) == 2


def test_canonical_hash_ignores_storage_precision(tmp_path: Path) -> None:
    """Large arrays are stored as float32, but their hash is of the float64 values."""
    big = np.random.default_rng(5).normal(size=(runner.FLOAT32_ABOVE_ELEMENTS + 1,))
    name_a, meta_a = runner.Array(big).store(tmp_path / "a")
    name_b, meta_b = runner.Array(big + 1e-9).store(tmp_path / "b")
    assert meta_a["stored_dtype"] == "float32"
    assert meta_a["sha256"] != meta_b["sha256"]
    ref = compare.read_array(tmp_path / name_a)
    assert ref.dtype == np.float32 and ref.shape == big.shape
    verdict, _ = compare.compare_arrays(ref, compare.read_array(tmp_path / name_b), "values")
    assert verdict == "within tolerance"


#: The ledger is TOML, read with ``tomllib``: the reference tools run on Python 3.11 or later
#: (the pinned reference interpreter), never on the oldest supported Python.
needs_tomllib = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="the reference tools read the ledger with tomllib"
)


def _ledger(path: Path, entries: list[dict]) -> Path:
    lines = []
    for e in entries:
        lines.append("[[difference]]")
        lines += [f'{k} = "{v}"' for k, v in e.items()]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@needs_tomllib
def test_recorded_difference_is_explained_and_passes(tmp_path: Path) -> None:
    ref = _write_run(tmp_path / "ref", _artifacts())
    cur = _write_run(tmp_path / "cur", _artifacts(extra_row=True))
    manifest = json.loads((cur / "manifest.json").read_text(encoding="utf-8"))
    sha = manifest["artifacts"]["first"]["terms"]["sha256"]
    ledger = _ledger(
        tmp_path / "explained.toml",
        [
            {
                "reference": "S",
                "stage": "first",
                "artifact": "terms",
                "sha256": sha,
                "reason": "one more candidate term",
            }
        ],
    )
    report = compare.compare_runs(ref, cur, compare.load_explained(ledger, "S"))
    by_name = {a.name: a for s in report.stages for a in s.artifacts}
    assert by_name["terms"].verdict == "explained"
    assert "one more candidate term" in by_name["terms"].detail
    assert report.summary_line() == "2 stages: 1 identical, 1 explained"
    args = ["--reference", str(ref), "--current", str(cur), "--explained", str(ledger)]
    assert compare.main([*args, "--name", "S"]) == 0
    # The same ledger does not apply to another reference.
    assert compare.main([*args, "--name", "L"]) == 1


@needs_tomllib
def test_other_change_or_stale_entry_is_not_hidden(tmp_path: Path) -> None:
    ref = _write_run(tmp_path / "ref", _artifacts())
    cur = _write_run(tmp_path / "cur", _artifacts(extra_row=True))
    ledger = _ledger(
        tmp_path / "explained.toml",
        [
            {
                "reference": "S",
                "stage": "first",
                "artifact": "terms",
                "sha256": "0" * 64,
                "reason": "a different change was recorded",
            },
            {
                "reference": "S",
                "stage": "second",
                "artifact": "xy",
                "sha256": "1" * 64,
                "reason": "no longer happens",
            },
        ],
    )
    report = compare.compare_runs(ref, cur, compare.load_explained(ledger, "S"))
    by_name = {a.name: a for s in report.stages for a in s.artifacts}
    assert by_name["terms"].verdict == "different"
    assert report.verdict == "different"
    assert any("no longer matches: second/xy" in n for n in report.notes)


@needs_tomllib
def test_ledger_entry_needs_a_reason(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path / "explained.toml",
        [{"reference": "S", "stage": "first", "artifact": "terms", "sha256": "0" * 64}],
    )
    with pytest.raises(ValueError, match="needs a reason"):
        compare.load_explained(ledger, "S")


@needs_tomllib
def test_a_stage_is_explained_by_its_upstream_cause(tmp_path: Path) -> None:
    """A stage entry explains every change of a stage whose upstream stage changed."""
    ref = _write_run(tmp_path / "ref", _artifacts())
    cur = _write_run(tmp_path / "cur", _artifacts(scale=1.01, label_shift=True, extra_row=True))
    manifest = json.loads((cur / "manifest.json").read_text(encoding="utf-8"))
    entries = [
        {
            "reference": "S",
            "stage": "first",
            "artifact": name,
            "sha256": manifest["artifacts"]["first"][name]["sha256"],
            "reason": "new candidate terms",
        }
        for name in ("terms", "summary")
    ]
    entries.append(
        {"reference": "S", "stage": "second", "upstream": "first", "reason": "other terms"}
    )
    ledger = _ledger(tmp_path / "explained.toml", entries)
    report = compare.compare_runs(ref, cur, compare.load_explained(ledger, "S"))
    by_name = {a.name: a for s in report.stages for a in s.artifacts}
    assert by_name["labels"].verdict == "explained"
    assert by_name["labels"].detail.endswith("explained by an upstream change (first)")
    assert "second: explained by an upstream change (first): other terms" in report.notes
    assert by_name["copy"].verdict == "identical"
    assert report.summary_line() == "2 stages: 0 identical, 2 explained"
    assert not any("no longer matches" in n for n in report.notes)


@needs_tomllib
def test_an_upstream_cause_that_did_not_change_explains_nothing(tmp_path: Path) -> None:
    ref = _write_run(tmp_path / "ref", _artifacts())
    changed = _artifacts()
    changed["second"]["labels"] = runner.Labels([0, 1, 1, 1])
    cur = _write_run(tmp_path / "cur", changed)
    ledger = _ledger(
        tmp_path / "explained.toml",
        [{"reference": "S", "stage": "second", "upstream": "first", "reason": "other terms"}],
    )
    report = compare.compare_runs(ref, cur, compare.load_explained(ledger, "S"))
    assert report.stages[1].verdict == "different"
    assert any("no longer matches: second (upstream first)" in n for n in report.notes)
    # A later stage is never the cause of an earlier one.
    changed["first"]["summary"] = runner.Document({"n": 5})
    cur2 = _write_run(tmp_path / "cur2", changed)
    ledger = _ledger(
        tmp_path / "explained2.toml",
        [{"reference": "S", "stage": "first", "upstream": "second", "reason": "backwards"}],
    )
    report = compare.compare_runs(ref, cur2, compare.load_explained(ledger, "S"))
    assert report.stages[0].verdict == "different"


@needs_tomllib
def test_stage_entries_are_checked(tmp_path: Path) -> None:
    both = _ledger(
        tmp_path / "both.toml",
        [
            {
                "reference": "S",
                "stage": "second",
                "artifact": "labels",
                "sha256": "0" * 64,
                "upstream": "first",
                "reason": "ambiguous",
            }
        ],
    )
    with pytest.raises(ValueError, match="not both"):
        compare.load_explained(both, "S")
    neither = _ledger(
        tmp_path / "neither.toml", [{"reference": "S", "stage": "second", "reason": "why"}]
    )
    with pytest.raises(ValueError, match="upstream cause"):
        compare.load_explained(neither, "S")


def test_regenerating_needs_the_frozen_runner(capsys: pytest.CaptureFixture[str]) -> None:
    """``--regenerate`` refuses to run without ``--runner``, before building anything."""
    check = _load("reference_check", "check_reference.py")
    with pytest.raises(SystemExit) as exc:
        check.main(["--size", "S", "--regenerate"])
    assert exc.value.code == 2
    assert "--runner" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        check.main(["--size", "S", "--runner", "run.py"])


def test_the_baseline_changes_only_with_a_reason(capsys: pytest.CaptureFixture[str]) -> None:
    """``--update-baseline`` needs a reason, and it never combines with ``--regenerate``."""
    check = _load("reference_check", "check_reference.py")
    for argv in (
        ["--update-baseline", "--regenerate", "--runner", "run.py"],
        ["--reason", "a reason without an update"],
    ):
        with pytest.raises(SystemExit) as exc:
            check.main(argv)
        assert exc.value.code == 2
    with pytest.raises(SystemExit, match="needs --reason"):
        check._check_reason("")
    with pytest.raises(SystemExit, match="needs --reason"):
        check._check_reason("because")
    assert check._check_reason("  outputs  move\n on purpose ") == "outputs move on purpose"


def test_a_baseline_update_records_its_reason(tmp_path: Path, monkeypatch) -> None:
    check = _load("reference_check", "check_reference.py")
    monkeypatch.setattr(check, "BASELINE", tmp_path / "baseline")
    monkeypatch.setattr(check, "BASELINE_LOG", tmp_path / "baseline" / "LOG.md")
    run = _write_run(tmp_path / "run", _artifacts())
    target = check.write_baseline(run, "S", "outputs move on purpose")
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["baseline"]["reason"] == "outputs move on purpose"
    assert compare.compare_runs(target, run).verdict == "identical"
    env = {"engine_version": "1.0", "engine_fingerprint": "f" * 64}
    check.log_baseline(["S"], "outputs move on purpose", {"S": "the first baseline"}, env)
    check.log_baseline(["S"], "a second reason here", {"S": "2 stages: 2 identical"}, env)
    log = (tmp_path / "baseline" / "LOG.md").read_text(encoding="utf-8")
    assert log.startswith("# Baseline updates")
    assert log.count("\n## ") == 2
    assert "- Reason: outputs move on purpose" in log and "- S: 2 stages: 2 identical" in log


def test_changed_artifact_without_stored_data(tmp_path: Path) -> None:
    """A changed artifact whose stored file is missing is reported, not a crash."""
    ref = _write_run(tmp_path / "ref", _artifacts())
    changed = _artifacts()
    changed["first"]["summary"] = runner.Document({"n": 999, "label": "changed"})
    cur = _write_run(tmp_path / "cur", changed)
    manifest = json.loads((ref / "manifest.json").read_text(encoding="utf-8"))
    (ref / manifest["artifacts"]["first"]["summary"]["file"]).unlink()
    report = compare.compare_runs(ref, cur)
    by_name = {a.name: a for a in report.stages[0].artifacts}
    assert by_name["summary"].verdict == "different"
    assert "stored data missing" in by_name["summary"].detail


def test_merge_runs_are_compared_by_their_worlds(tmp_path: Path) -> None:
    """Changed cohort bundles are a note; changed cohort worlds make runs incomparable."""
    cohorts = [
        {"domain_id": d, "workspace_sha256": d * 64, "truth_sha256": None, "bundle": {"meta": d}}
        for d in "ab"
    ]
    ref = _write_run(tmp_path / "ref", _artifacts())
    manifest = json.loads((ref / "manifest.json").read_text(encoding="utf-8"))
    manifest["inputs"] = {"cohorts": cohorts}
    (ref / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    cur = tmp_path / "cur"
    shutil.copytree(ref, cur)
    changed = json.loads(json.dumps(manifest))
    changed["inputs"]["cohorts"][0]["bundle"]["meta"] = "other"
    (cur / "manifest.json").write_text(json.dumps(changed), encoding="utf-8")
    report = compare.compare_runs(ref, cur)
    assert report.comparable and report.verdict == "identical"
    assert any("cohort a: bundle artifacts changed upstream (meta)" in n for n in report.notes)

    # A merge stage may be explained by its cohorts' bundles.
    merged = _artifacts()
    merged["second"]["labels"] = runner.Labels([0, 1, 1, 1])
    cur_merge = _write_run(tmp_path / "cur_merge", merged)
    cur_manifest = json.loads((cur_merge / "manifest.json").read_text(encoding="utf-8"))
    cur_manifest["inputs"] = changed["inputs"]
    (cur_merge / "manifest.json").write_text(json.dumps(cur_manifest), encoding="utf-8")
    if sys.version_info >= (3, 11):
        ledger = _ledger(
            tmp_path / "explained.toml",
            [{"reference": "m", "stage": "second", "upstream": "bundle", "reason": "new bundles"}],
        )
        report = compare.compare_runs(ref, cur_merge, compare.load_explained(ledger, "m"))
        assert report.stages[1].verdict == "explained"

    changed["inputs"]["cohorts"][1]["workspace_sha256"] = "c" * 64
    (cur / "manifest.json").write_text(json.dumps(changed), encoding="utf-8")
    assert not compare.compare_runs(ref, cur).comparable
