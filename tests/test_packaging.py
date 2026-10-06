# SPDX-License-Identifier: MIT
"""The built distributions hold everything the package needs and nothing else.

The source archive and the wheel are built from a copy of the checkout's files
(offline: the build backend comes from uv's cache, filled when the test
environment was installed), then checked by ``tools/package_check.py``: every
module and data file present (interface, schemas, prompts, stop-word lists,
vendored libraries with their licences), no tests, caches or review material,
the optional libraries declared as extras.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import package_check  # noqa: E402

TOP_FILES = ("pyproject.toml", "README.md", "LICENSE", "MANIFEST.in", "CHANGELOG.md")


def test_the_built_archives_hold_the_package_and_nothing_else(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is needed to build the archives")
    expected = package_check.package_files(REPO_ROOT)
    src = tmp_path / "src"
    for name in [*TOP_FILES, "CITATION.cff", "codemeta.json", *sorted(expected)]:
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / name, src / name)
    # A stray file next to the sources, as a checkout may have: it must not ship.
    (src / "tests").mkdir()
    (src / "tests" / "test_x.py").write_text("", encoding="utf-8")
    out = tmp_path / "dist"
    subprocess.run(
        [uv, "build", "--quiet", "--offline", "--out-dir", str(out), str(src)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    (wheel,) = out.glob("cartolex-*.whl")
    (sdist,) = out.glob("cartolex-*.tar.gz")
    assert package_check.check_wheel(wheel, expected) == []
    assert package_check.check_sdist(sdist, expected) == []


def test_a_missing_or_stray_file_is_reported(tmp_path: Path) -> None:
    expected = {"cartolex/__init__.py", "cartolex/app/static/vendor/lib/LICENSE"}
    wheel = tmp_path / "cartolex-0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("cartolex/__init__.py", "")
        zf.writestr("cartolex/app/static/vendor/lib/lib.js", "")
        zf.writestr("cartolex/__pycache__/x.cpython-312.pyc", "")
        zf.writestr("cartolex-0.dist-info/METADATA", "Name: cartolex\nRequires-Dist: mistralai\n")
    problems = "\n".join(package_check.check_wheel(wheel, expected))
    assert "missing from the wheel: cartolex/app/static/vendor/lib/LICENSE" in problems
    assert "stray in the wheel: cartolex/app/static/vendor/lib/lib.js" in problems
    assert "never ships: cartolex/__pycache__/x.cpython-312.pyc" in problems
    assert "vendored library without its licence" in problems
    assert "an optional library is a core dependency: mistralai" in problems


def test_the_citation_files_agree_with_the_package_metadata() -> None:
    """Authors, licence, repository and version are the same in pyproject, CITATION.cff and
    codemeta."""
    import json
    import re

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    authors_block = pyproject.split("\nauthors = [", 1)[1].split("\n]", 1)[0]
    authors = re.findall(r'name = "([^"]+)"', authors_block)
    cff = (REPO_ROOT / "CITATION.cff").read_text(encoding="utf-8")
    given = re.findall(r'given-names: "([^"]+)"', cff)
    family = re.findall(r'family-names: "([^"]+)"', cff)
    meta = json.loads((REPO_ROOT / "codemeta.json").read_text(encoding="utf-8"))
    assert [f"{g} {f}" for g, f in zip(given, family, strict=True)] == authors
    assert [f"{a['givenName']} {a['familyName']}" for a in meta["author"]] == authors
    assert 'license = "MIT"' in pyproject and "license: MIT" in cff
    assert meta["license"].endswith("/MIT")
    repo = re.search(r'Source = "([^"]+)"', pyproject).group(1)
    assert f'repository-code: "{repo}"' in cff and meta["codeRepository"] == repo
    version = re.search(r'(?m)^version = "([^"]+)"', pyproject).group(1)
    assert f'version: "{version}"' in cff and meta["version"] == version
