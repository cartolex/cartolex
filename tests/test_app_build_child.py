# SPDX-License-Identifier: MIT
"""A build the app starts runs in a process of its own, under the app's lock
(cartolex.app.build_run): the child writes as the app's delegate, never takes or
removes the lock, and a child the computer stops is said as such."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path

import pytest
from _build_fakes import make_project

from cartolex.app.build_run import run_build
from cartolex.build.engine import EngineOptions
from cartolex.project.lock import read_lock

REPO = Path(__file__).resolve().parent.parent

DELEGATE = """
import json, sys
from cartolex.project import Project
from cartolex.project.lock import LockInfo, LockLost, ensure_held
try:
    with Project.open(sys.argv[1], write=True, holder=LockInfo(**json.loads(sys.argv[2]))) as p:
        ensure_held(p.layout.lock)
    print("wrote")
except LockLost:
    print("lost")
"""


def _delegate(root: Path, holder: dict) -> str:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(REPO), os.environ.get("PYTHONPATH", "")]),
    }
    done = subprocess.run(
        [sys.executable, "-c", DELEGATE, str(root), json.dumps(holder)],
        capture_output=True, text=True, env=env, check=True,
    )  # fmt: skip
    return done.stdout.strip()


def test_a_child_writes_under_the_apps_lock_and_stops_when_it_names_another(tmp_path):
    project = make_project(tmp_path / "p")
    try:
        holder = asdict(project.lock_info)
        assert _delegate(project.layout.root, holder) == "wrote"
        assert read_lock(project.layout.lock) == project.lock_info  # left as it was
        # Another application overrode the lock: the delegate refuses to write.
        other = {**holder, "pid": holder["pid"] + 1, "app": "another"}
        project.layout.lock.write_text(json.dumps(other) + "\n", encoding="utf-8")
        assert _delegate(project.layout.root, holder) == "lost"
        project.layout.lock.write_text(json.dumps(holder) + "\n", encoding="utf-8")
    finally:
        project.close()


def _die(*args: object, **kwargs: object) -> None:
    os.kill(os.getpid(), signal.SIGKILL)


class _Control:
    job_id = "test-child"

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.seen: list[dict] = []

    def progress(self, data: dict) -> None:
        self.seen.append(data)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL")
def test_a_child_the_computer_stops_is_said_so(tmp_path):
    project = make_project(tmp_path / "p")
    recipe = {
        "options": EngineOptions(),
        "extensions": [("dies", (), {"corpus.assemble": {"run": _die}})],
        "ai_key": None,
    }
    try:
        with pytest.raises(RuntimeError, match="stopped by the system"):
            run_build(
                project,
                recipe,
                _Control(),
                registry=None,
                targets=["corpus.assemble"],
                consent=[],
                force=["corpus.assemble"],
                budget_mb=1e9,
                allow_over_budget=True,
                heartbeat_s=5.0,
                year=2026,
                job_id="test-child",
            )
        assert read_lock(project.layout.lock) == project.lock_info
    finally:
        project.close()
