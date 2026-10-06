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


def test_a_test_build_carries_its_wheel_and_says_what_it_is(tmp_path: Path) -> None:
    wheel = tmp_path / "cartolex-1.2.3-py3-none-any.whl"
    wheel.write_bytes(b"PK fake wheel")
    path = installer_zip.build_kit(
        "1.2.3", tmp_path / "out", wheel=wheel, label="3f2a1c", about="from commit 3f2a1c"
    )
    assert path.name == "cartolex-installer-1.2.3-3f2a1c.zip"
    with zipfile.ZipFile(path) as zf:
        data = {i.filename.split("/", 1)[1]: zf.read(i) for i in zf.infolist()}
    assert data[wheel.name] == b"PK fake wheel"
    assert set(data) == set(installer_zip.FILES) | {wheel.name, "build.txt"}
    assert b"test build 3f2a1c" in data["build.txt"] and b"from commit 3f2a1c" in data["build.txt"]
    other = tmp_path / "cartolex-9.9.9-py3-none-any.whl"
    other.write_bytes(b"PK")
    with pytest.raises(ValueError, match="not a cartolex 1.2.3 wheel"):
        installer_zip.build_kit("1.2.3", tmp_path / "out", wheel=other)
    with pytest.raises(ValueError, match="not a build label"):
        installer_zip.build_kit("1.2.3", tmp_path / "out", wheel=wheel, label="../x")


def test_a_stamped_wheel_names_its_build(tmp_path: Path) -> None:
    import json

    wheel = tmp_path / "cartolex-1.2.3-py3-none-any.whl"
    stamp = {"format": "cartolex-build/1", "commit": "0123456789abcdef" * 2, "date": "2026-10-06"}
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("cartolex/_data/build.json", json.dumps(stamp))
    path = installer_zip.build_kit("1.2.3", tmp_path / "out", wheel=wheel)
    assert path.name == "cartolex-installer-1.2.3-0123456.zip"  # the commit is the label
    with zipfile.ZipFile(path) as zf:
        text = next(zf.read(i) for i in zf.infolist() if i.filename.endswith("build.txt"))
    assert b"build 0123456 of 2026-10-06" in text


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_shell_launchers_parse() -> None:
    for name in ("install-cartolex.sh", "Install cartolex.command"):
        proc = subprocess.run(
            ["bash", "-n", str(REPO_ROOT / "installer" / name)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr
