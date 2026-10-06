# SPDX-License-Identifier: MIT
"""Build the installer kit: a small zip of launchers for people who do not program.

Usage::

    python tools/installer_zip.py                    # the version of pyproject.toml, into dist/
    python tools/installer_zip.py --version 1.0.0 --out dist
    python tools/installer_zip.py --wheel dist/cartolex-1.0.0-py3-none-any.whl --label 3f2a1c
                                                     # a test build that carries its wheel

The kit holds the files of ``installer/``: one launcher per system
(``Install cartolex.command`` for macOS, ``Install cartolex.bat`` with
``install-cartolex.ps1`` for Windows, ``install-cartolex.sh`` for Linux) and
the plain-language guides in English, French and Portuguese. The version to
install is written into the launchers here, so a kit always installs the
release it was built for. Scripts are marked executable, Windows files get
CRLF line endings, and the archive is the same byte for byte for the same
files and version. Nothing is downloaded or bundled: the launchers fetch
cartolex from the package index when they run.

A **test build** (``--wheel``) carries a cartolex wheel of the same version beside
the launchers, which install it instead of fetching cartolex (the libraries it
needs still come from the package index), and a ``build.txt`` naming the build
(``--label``, in the kit's name too; by default the commit of the wheel's build stamp,
see ``tools/build_stamp.py``) for the people who try it.

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


def wheel_stamp(wheel: Path) -> dict | None:
    """The build stamp a wheel carries (``cartolex/_data/build.json``, tools/build_stamp.py),
    or ``None``."""
    import json

    try:
        with zipfile.ZipFile(wheel) as zf:
            stamp = json.loads(zf.read("cartolex/_data/build.json"))
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        return None
    return stamp if isinstance(stamp, dict) and stamp.get("commit") else None


def build_kit(
    version: str,
    out_dir: Path,
    source: Path = SOURCE,
    *,
    wheel: Path | None = None,
    label: str | None = None,
    about: str = "",
) -> Path:
    """Write ``cartolex-installer-<version>.zip`` into *out_dir*; return its path.

    With *wheel* (a cartolex wheel of *version*), a test build: the wheel goes beside the
    launchers, and a ``build.txt`` says what the kit installs (*label*, also in the kit's
    name, and *about*: where the build comes from), with the wheel's build stamp: the
    commit and date the app shows. Without *label*, the commit's first seven characters
    are the label.
    """
    files = kit_files(version, source)
    if label is not None and not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,40}", label):
        raise ValueError(f"not a build label: {label!r}")
    if wheel is not None:
        wheel = Path(wheel)
        if not (wheel.name.startswith(f"cartolex-{version}-") and wheel.suffix == ".whl"):
            raise ValueError(f"{wheel.name} is not a cartolex {version} wheel")
        files[wheel.name] = (wheel.read_bytes(), 0o644)
        stamp = wheel_stamp(wheel)
        if label is None and stamp is not None:
            label = str(stamp["commit"])[:7]
        built = f"build {str(stamp['commit'])[:7]} of {stamp.get('date', '?')}" if stamp else ""
        text = (
            f"cartolex {version}, test build{f' {label}' if label else ''}\n"
            + (f"{built}\n" if built else "")
            + (f"{about}\n" if about else "")
            + f"\nThis kit installs the wheel beside it ({wheel.name}); the libraries it\n"
            "needs are downloaded during the installation. Follow the guide in your\n"
            "language (README-en.txt, LISEZMOI-fr.txt, LEIAME-pt-BR.txt).\n"
        )
        files["build.txt"] = (text.replace("\n", "\r\n").encode("utf-8"), 0o644)
    folder = f"cartolex-installer-{version}" + (f"-{label}" if label else "")
    path = Path(out_dir) / f"{folder}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, (data, mode) in sorted(files.items()):
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
    parser.add_argument("--wheel", type=Path, help="a test build: carry this cartolex wheel")
    parser.add_argument("--label", help="the test build's name (in the kit's name and build.txt)")
    parser.add_argument("--about", default="", help="a line for build.txt (where it comes from)")
    args = parser.parse_args(argv)
    version = args.version or project_version()
    try:
        path = build_kit(version, args.out, wheel=args.wheel, label=args.label, about=args.about)
    except (ValueError, OSError) as exc:
        print(f"installer kit: {exc}", file=sys.stderr)
        return 2
    if args.wheel and wheel_stamp(args.wheel) is None:
        print(
            "installer kit: the wheel carries no build stamp (python tools/build_stamp.py "
            "before building it): the app will not say which build it is",
            file=sys.stderr,
        )
    what = f"the wheel {args.wheel.name}" if args.wheel else f"cartolex {version}"
    print(f"{path} ({path.stat().st_size / 1024:.0f} KB), installs {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
