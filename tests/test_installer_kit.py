# SPDX-License-Identifier: MIT
"""The installer kit: the launchers of each system, the release they install, the guides."""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import installer_zip  # noqa: E402


def test_the_kit_pins_the_release_and_keeps_each_system_s_conventions(tmp_path: Path) -> None:
    path = installer_zip.build_kit("1.2.3", tmp_path)
    assert path.name == "cartolex-installer-1.2.3.zip"
    with zipfile.ZipFile(path) as zf:
        members = {i.filename.split("/", 1)[1]: i for i in zf.infolist()}
        data = {name: zf.read(info) for name, info in members.items()}
    assert set(members) == set(installer_zip.FILES)
    for name in ("install-cartolex.sh", "install-cartolex.ps1"):
        assert (
            b'"@CARTOLEX_VERSION@"' not in data[name] and b"'@CARTOLEX_VERSION@'" not in data[name]
        )
    assert b'PIN="1.2.3"' in data["install-cartolex.sh"]
    assert b"$Pin = '1.2.3'" in data["install-cartolex.ps1"]
    for name in ("install-cartolex.sh", "Install cartolex.command"):
        assert (members[name].external_attr >> 16) & 0o111, f"{name} is not executable"
        assert b"\r\n" not in data[name]
    assert data["Install cartolex.bat"].count(b"\n") == data["Install cartolex.bat"].count(b"\r\n")
    assert installer_zip.build_kit("1.2.3", tmp_path / "again").read_bytes() == path.read_bytes()
    with pytest.raises(ValueError):
        installer_zip.build_kit("1.2.3; rm -rf /", tmp_path)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_shell_launchers_parse() -> None:
    for name in ("install-cartolex.sh", "Install cartolex.command"):
        proc = subprocess.run(
            ["bash", "-n", str(REPO_ROOT / "installer" / name)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr
