# SPDX-License-Identifier: MIT
"""Build the installer kit: a small zip of launchers for people who do not program.

Usage::

    python tools/installer_zip.py                    # the version of pyproject.toml, into dist/
    python tools/installer_zip.py --version 1.0.0 --out dist

The kit holds the files of ``installer/``: one launcher per system
(``Install cartolex.command`` for macOS, ``Install cartolex.bat`` with
``install-cartolex.ps1`` for Windows, ``install-cartolex.sh`` for Linux) and
the plain-language guides in English, French and Portuguese. The version to
install is written into the launchers here, so a kit always installs the
release it was built for. Scripts are marked executable, Windows files get
CRLF line endings, and the archive is the same byte for byte for the same
files and version. Nothing is downloaded or bundled: the launchers fetch
cartolex from the package index when they run.

Stdlib only: this script runs under any Python 3.10 or later.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "installer"
PLACEHOLDER = "@CARTOLEX_VERSION@"
#: The kit's files: published name → (mode, line endings).
FILES = {
    "Install cartolex.command": (0o755, "\n"),
    "install-cartolex.sh": (0o755, "\n"),
    "Install cartolex.bat": (0o644, "\r\n"),
    "install-cartolex.ps1": (0o644, "\r\n"),
    "README-en.txt": (0o644, "\r\n"),
    "LISEZMOI-fr.txt": (0o644, "\r\n"),
    "LEIAME-pt-BR.txt": (0o644, "\r\n"),
}
#: A release version (PEP 440 public versions); anything else is refused.
VERSION = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")
#: The fixed time of every member, so the same files give the same archive.
STAMP = (2026, 1, 1, 0, 0, 0)


def project_version(root: Path = ROOT) -> str:
    """The version in ``pyproject.toml``."""
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version = "([^"]+)"', text, flags=re.MULTILINE)
    if found is None:
        raise SystemExit("installer kit: no version in pyproject.toml")
    return found.group(1)


def kit_files(version: str, source: Path = SOURCE) -> dict[str, tuple[bytes, int]]:
    """Each file of the kit for *version*: its bytes and its mode."""
    if not VERSION.match(version):
        raise ValueError(f"not a release version: {version!r}")
    out: dict[str, tuple[bytes, int]] = {}
    for name, (mode, newline) in FILES.items():
        text = (source / name).read_text(encoding="utf-8")
        text = text.replace("\r\n", "\n").replace(PLACEHOLDER, version)
        out[name] = (text.replace("\n", newline).encode("utf-8"), mode)
    return out


def build_kit(version: str, out_dir: Path, source: Path = SOURCE) -> Path:
    """Write ``cartolex-installer-<version>.zip`` into *out_dir*; return its path."""
    folder = f"cartolex-installer-{version}"
    path = Path(out_dir) / f"{folder}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, (data, mode) in sorted(kit_files(version, source).items()):
            info = zipfile.ZipInfo(f"{folder}/{name}", date_time=STAMP)
            info.external_attr = (0o100000 | mode) << 16  # a regular file with its mode
            info.create_system = 3  # Unix, so unzip tools apply the mode
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return path


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--version", help="the release to install (default: pyproject.toml's)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="the output folder")
    args = parser.parse_args(argv)
    version = args.version or project_version()
    try:
        path = build_kit(version, args.out)
    except ValueError as exc:
        print(f"installer kit: {exc}", file=sys.stderr)
        return 2
    print(f"{path} ({path.stat().st_size / 1024:.0f} KB), installs cartolex {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
