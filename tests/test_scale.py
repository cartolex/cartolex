# SPDX-License-Identifier: MIT
"""The shared resources: a budget, ordered worker processes, scratch folders, arrays."""

from __future__ import annotations

import os
import subprocess
import sys

import numpy as np

from cartolex.scale import Budget, ordered_map, pid_alive, scratch_folder, sorted_unique
from cartolex.scale.scratch import _host


def _square(x: int) -> int:
    return x * x


def test_a_budget_is_what_is_given_else_a_share_of_the_computer():
    given = Budget.for_machine(memory_mb=2000, workers=3, scratch=None)
    assert (given.memory_mb, given.workers, given.scratch) == (2000, 3, None)
    found = Budget.for_machine()
    assert 0 < found.memory_mb <= 12_288 and found.workers >= 1
    assert given.workers_for(600, parent_mb=500) == 2
    assert given.workers_for(5000) == 1


def test_ordered_map_keeps_the_items_order_in_workers_or_here():
    items = list(range(40))
    assert list(ordered_map(_square, items, workers=1)) == [x * x for x in items]
    assert list(ordered_map(_square, items, workers=2, ahead=1)) == [x * x for x in items]


def test_a_scratch_folder_sweeps_the_ones_killed_processes_left(tmp_path):
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)  # fmt: skip
    pid = int(dead.stdout)
    assert not pid_alive(pid) and pid_alive(os.getpid())
    left = tmp_path / f"rebuild-{_host()}-{pid}-abc"
    left.mkdir()
    (left / "work.sqlite").write_bytes(b"x")
    running = tmp_path / f"rebuild-{_host()}-{os.getpid()}-def"
    running.mkdir()
    elsewhere = tmp_path / f"rebuild-ffffff-{pid}-ghi"
    elsewhere.mkdir()
    other_job = tmp_path / f"extract-{_host()}-{pid}-jkl"
    other_job.mkdir()

    made = scratch_folder("rebuild", tmp_path)

    assert made.is_dir() and made.parent == tmp_path
    assert made.name.startswith(f"rebuild-{_host()}-{os.getpid()}-")
    assert not left.exists()
    assert running.exists() and elsewhere.exists() and other_job.exists()


def test_sorted_unique_gives_what_np_unique_gives():
    rng = np.random.default_rng(0)
    for values in (
        np.zeros(0, dtype=np.int64),
        np.array([7], dtype=np.int32),
        rng.integers(0, 40, 1_000),
        rng.integers(0, 2**60, 10_000).astype(np.uint64),
        np.array([[3, 1], [1, 3]]),
        np.array([2.0, np.nan, 1.0, np.nan]),  # floats: np.unique's own (one NaN)
    ):
        expected, got = np.unique(values), sorted_unique(values)
        assert got.dtype == expected.dtype and np.array_equal(got, expected, equal_nan=True)
