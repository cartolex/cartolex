# SPDX-License-Identifier: MIT
"""Write the build stamp a wheel carries: the commit and its date, so two builds of one
version are told apart (the app shows it in its settings menu, its About page and the
diagnostic).

Usage::

    python tools/build_stamp.py           # cartolex/_data/build.json from the checkout's HEAD
    python tools/build_stamp.py --clear   # remove it (the app then asks git, in a checkout)

Run it just before building a wheel (``uv build``); the file is not tracked by git. Stdlib
only: this script runs under any Python 3.10 or later.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAMP = ROOT / "cartolex" / "_data" / "build.json"
FORMAT = "cartolex-build/1"


def head(root: Path = ROOT) -> dict[str, str]:
    """The checkout's last commit: its full hash and its date (``YYYY-MM-DD``)."""
    out = subprocess.run(
        ["git", "log", "-1", "--format=%H %cs"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return {"commit": out[0], "date": out[1]}


def write_stamp(path: Path = STAMP, root: Path = ROOT) -> dict[str, str]:
    """Write the stamp of *root*'s last commit into *path*; returns it."""
    stamp = head(root)
    path.write_text(json.dumps({"format": FORMAT, **stamp}, indent=2) + "\n", encoding="utf-8")
    return stamp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--clear", action="store_true", help="remove the stamp")
    args = parser.parse_args(argv)
    if args.clear:
        STAMP.unlink(missing_ok=True)
        print(f"build stamp removed: {STAMP.relative_to(ROOT)}")
        return 0
    try:
        stamp = write_stamp()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"build stamp: git could not name the commit ({exc})", file=sys.stderr)
        return 1
    print(f"build stamp: {stamp['commit'][:7]} of {stamp['date']} in {STAMP.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
