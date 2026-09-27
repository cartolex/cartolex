# SPDX-License-Identifier: MIT
"""The ``cartolex`` command: init, project validate, project unlock, demo."""

from __future__ import annotations

import json
import socket

from cartolex.cli import main
from cartolex.demo import generate
from cartolex.demo.project import write_project
from cartolex.project import ProjectLayout


def test_init_then_validate(tmp_path, capsys):
    folder = tmp_path / "p"
    assert main(["init", str(folder), "--name", "N", "--field", "F", "--languages", "en,pt"]) == 0
    assert json.loads((folder / "project.json").read_text())["languages"]["corpus"] == ["en", "pt"]
    assert not (folder / ".lock").exists()
    assert main(["project", "validate", str(folder)]) == 0
    assert "valid" in capsys.readouterr().out
    assert main(["init", str(folder), "--name", "N", "--field", "F"]) == 1


def test_validate_lists_every_problem(tmp_path, capsys):
    world = generate("XS", 0)
    project = write_project(world, tmp_path / "demo")
    project.close()
    layout = ProjectLayout(tmp_path / "demo")
    assert main(["project", "validate", str(layout.root)]) == 0
    layout.params_json.write_text(
        json.dumps({"format": "cartolex-params/1", "stages": {"nope": {}}})
    )
    rows = layout.people_csv.read_text().splitlines()
    rows.append("ghost,mapped,,confirmed,,,,")
    layout.people_csv.write_text("\n".join(rows) + "\n")
    capsys.readouterr()
    assert main(["project", "validate", str(layout.root)]) == 1
    out = capsys.readouterr().out
    assert "decisions/params.json" in out and "unknown stage" in out
    assert "unknown person 'ghost'" in out
    assert "2 problem(s)" in out


def test_unlock_removes_only_a_stale_lock(tmp_path, capsys):
    folder = tmp_path / "p"
    main(["init", str(folder), "--name", "N", "--field", "F"])
    lock = folder / ".lock"
    lock.write_text(
        json.dumps({"pid": 2**22 + 777, "host": socket.gethostname(), "app": "x", "since": "s"})
    )
    assert main(["project", "unlock", str(folder)]) == 0 and not lock.exists()
    assert main(["project", "unlock", str(folder)]) == 1
    assert "no lock" in capsys.readouterr().err


def test_demo_is_delegated(tmp_path):
    assert (
        main(["demo", "create", "--size", "XS", "--seed", "0", "--out", str(tmp_path / "w")]) == 0
    )
    assert (tmp_path / "w" / "manifest.json").exists()
