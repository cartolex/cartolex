# SPDX-License-Identifier: MIT
"""A project's results do not depend on the worker processes its build uses."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cartolex.atlas import driver
from cartolex.build import build
from cartolex.build.engine import AIAccess, EngineOptions, engine_registry
from cartolex.demo import generate
from cartolex.demo.project import write_project
from cartolex.lexicon import extract_stream, io_helpers, theme_comb
from cartolex.scale import Budget

YEAR = 2026


#: A run id (recorded as the provenance of some files): different in every build.
RUN_ID = re.compile(rb"\d{8}T\d{6}Z-[0-9a-f]{6}")


def _results(root: Path) -> dict[str, bytes]:
    derived = root / "derived"
    return {
        p.relative_to(derived).as_posix(): RUN_ID.sub(b"<run>", p.read_bytes())
        for p in sorted(derived.rglob("*"))
        if p.is_file()
        and p.name != "run.json"
        and not p.relative_to(derived).parts[0].startswith(".")
    }


@pytest.mark.models("en", "fr")
def test_the_results_do_not_depend_on_the_workers(tmp_path, monkeypatch) -> None:
    # Small blocks: every parallel step gives its workers several blocks.
    monkeypatch.setattr(extract_stream, "TASK_TEXTS", 16)
    monkeypatch.setattr(io_helpers, "_COUNT_TEXTS", 7)
    monkeypatch.setattr(theme_comb, "TEXT_CHUNK", 16)
    monkeypatch.setattr(driver, "TRAJECTORY_CHUNK", 5)
    monkeypatch.setattr(driver, "TRAJECTORY_TEXTS", 16)
    results = {}
    for workers in (1, 3):
        root = tmp_path / f"w{workers}"
        project = write_project(generate("XS", 0), root)
        options = EngineOptions(budget=Budget(memory_mb=16_000, workers=workers))
        result = build(
            project, registry=engine_registry(AIAccess(), options), year=YEAR, budget_mb=1e9
        )
        project.close()
        assert result.outcome == "succeeded", result.summary()
        results[workers] = _results(root)
    assert results[1].keys() == results[3].keys()
    differ = [name for name in results[1] if results[1][name] != results[3][name]]
    assert not differ, differ
