# SPDX-License-Identifier: MIT
"""Generations of a stage's results: the swap, its journal, and recovery after a kill."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cartolex.project import Project, ProjectLayout
from cartolex.project.files import json_bytes
from cartolex.project.generations import (
    CHUNKS,
    STAGING_MARKER,
    generation_run_id,
    recover,
    swap_in,
)

STAGE = "keywords.extract"
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
RUNS = ["20260928T100000Z-aaaa", "20260928T100100Z-bbbb", "20260928T100200Z-cccc"]


class Killed(BaseException):
    """Stands for the process dying at a step: nothing after it runs, nothing is cleaned."""


def _layout(tmp_path: Path) -> ProjectLayout:
    project = Project.init(
        tmp_path / "proj", name="Swap test", domain_title="Coastal systems", now=NOW
    )
    project.close()
    return project.layout


def _record(run_id: str) -> bytes:
    return json_bytes({"format": "cartolex-run/1", "stage": STAGE, "run_id": run_id})


def _stage_run(layout: ProjectLayout, run_id: str) -> Path:
    """A staging folder as a running stage leaves it: results, a marker and checkpoints."""
    staging = layout.staging(STAGE, run_id)
    (staging / CHUNKS).mkdir(parents=True)
    (staging / CHUNKS / "000000.done").write_bytes(b"")
    (staging / STAGING_MARKER).write_text("{}", encoding="utf-8")
    (staging / "result.txt").write_text(f"result of {run_id}\n", encoding="utf-8")
    return staging


def _swap(layout: ProjectLayout, run_id: str, kill_at: str | None = None) -> None:
    def probe(step: str) -> None:
        if step == kill_at:
            raise Killed(step)

    _stage_run(layout, run_id)
    layout.attempt(STAGE).parent.mkdir(parents=True, exist_ok=True)
    layout.attempt(STAGE).write_text("{}", encoding="utf-8")  # a failure the swap supersedes
    swap_in(layout, STAGE, run_id, _record(run_id), probe=probe)


def _assert_whole(layout: ProjectLayout, current: str, previous: str | None) -> None:
    """Every folder holds one whole generation, and nothing of the swap is left."""
    assert generation_run_id(layout.stage(STAGE)) == current
    assert (layout.stage(STAGE) / "result.txt").read_text() == f"result of {current}\n"
    assert not (layout.stage(STAGE) / STAGING_MARKER).exists()
    assert not (layout.stage(STAGE) / CHUNKS).exists()
    assert generation_run_id(layout.previous(STAGE)) == previous
    assert not layout.journal.exists()
    leftovers = list(layout.staging_root.iterdir()) if layout.staging_root.exists() else []
    assert leftovers == []


def test_a_swap_keeps_one_previous_generation(tmp_path):
    layout = _layout(tmp_path)
    _swap(layout, RUNS[0])
    _assert_whole(layout, RUNS[0], None)
    assert not layout.attempt(STAGE).exists()
    _swap(layout, RUNS[1])
    _assert_whole(layout, RUNS[1], RUNS[0])
    _swap(layout, RUNS[2])
    _assert_whole(layout, RUNS[2], RUNS[1])
    assert json.loads((layout.stage(STAGE) / "run.json").read_text())["run_id"] == RUNS[2]


STEPS_AFTER_RECORD = [
    "swap:recorded",
    "swap:cleaned",
    "swap:aside",
    "swap:previous",
    "swap:placed",
    "swap:attempt-cleared",
    "swap:journal-removed",
]


@pytest.mark.parametrize("step", STEPS_AFTER_RECORD)
def test_a_swap_killed_after_its_record_is_completed(tmp_path, step):
    layout = _layout(tmp_path)
    _swap(layout, RUNS[0])
    _swap(layout, RUNS[1])
    with pytest.raises(Killed):
        _swap(layout, RUNS[2], kill_at=step)
    done = recover(layout)
    if step != "swap:journal-removed":
        assert done and "completed" in done[0]
    _assert_whole(layout, RUNS[2], RUNS[1])
    assert not layout.attempt(STAGE).exists()
    assert recover(layout) == []  # recovering twice changes nothing


@pytest.mark.parametrize(
    "step", [s for s in STEPS_AFTER_RECORD if s not in ("swap:aside", "swap:previous")]
)
def test_a_first_swap_killed_after_its_record_is_completed(tmp_path, step):
    layout = _layout(tmp_path)
    with pytest.raises(Killed):
        _swap(layout, RUNS[0], kill_at=step)
    recover(layout)
    _assert_whole(layout, RUNS[0], None)


def test_a_swap_killed_before_its_record_is_undone_and_can_resume(tmp_path):
    layout = _layout(tmp_path)
    _swap(layout, RUNS[0])
    _swap(layout, RUNS[1])
    with pytest.raises(Killed):
        _swap(layout, RUNS[2], kill_at="swap:journal")
    done = recover(layout)
    assert done and "undid" in done[0]
    assert generation_run_id(layout.stage(STAGE)) == RUNS[1]
    assert generation_run_id(layout.previous(STAGE)) == RUNS[0]
    assert not layout.journal.exists()
    staging = layout.staging(STAGE, RUNS[2])  # kept: a new run can resume from its chunks
    assert (staging / STAGING_MARKER).exists() and (staging / CHUNKS / "000000.done").exists()
    assert layout.attempt(STAGE).exists()  # the older failure is still the last attempt


def test_a_swap_whose_new_generation_was_lost_is_undone(tmp_path):
    layout = _layout(tmp_path)
    _swap(layout, RUNS[0])
    _swap(layout, RUNS[1])
    with pytest.raises(Killed):
        _swap(layout, RUNS[2], kill_at="swap:previous")
    assert not layout.stage(STAGE).exists()  # killed between two renames
    shutil.rmtree(layout.staging(STAGE, RUNS[2]))  # the new generation is gone
    done = recover(layout)
    assert done and "undid" in done[0]
    _assert_whole(layout, RUNS[1], RUNS[0])


def test_recover_removes_leftovers_and_keeps_resumable_runs(tmp_path):
    layout = _layout(tmp_path)
    (layout.staging_root / f"{STAGE}.{RUNS[0]}.discard").mkdir(parents=True)
    (layout.staging_root / f"{STAGE}.{RUNS[1]}").mkdir()  # no marker: never started
    _stage_run(layout, RUNS[2])
    done = recover(layout)
    assert len(done) == 2
    assert [p.name for p in layout.staging_root.iterdir()] == [f"{STAGE}.{RUNS[2]}"]


def test_opening_for_writing_repairs_an_interrupted_swap(tmp_path):
    layout = _layout(tmp_path)
    _swap(layout, RUNS[0])
    with pytest.raises(Killed):
        _swap(layout, RUNS[1], kill_at="swap:previous")
    reader = Project.open(layout.root)  # reading repairs nothing
    assert reader.recovered == [] and layout.journal.exists()
    with Project.open(layout.root, write=True) as project:
        assert project.recovered and "completed" in project.recovered[0]
    _assert_whole(layout, RUNS[1], RUNS[0])
