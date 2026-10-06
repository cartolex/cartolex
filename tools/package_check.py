# SPDX-License-Identifier: MIT
"""Check what a built wheel and source archive hold: everything needed, nothing stray.

Usage::

    python tools/package_check.py dist/cartolex-*.whl dist/cartolex-*.tar.gz

The package's files are the files of ``cartolex/`` tracked by git (or, outside a
git checkout, the files found there, minus bytecode): the modules, the web
interface, the schemas, the prompt templates, the stop-word lists and the
vendored libraries with their licences. The wheel must hold exactly those plus
its metadata; the source archive must hold them plus what building a wheel
from it needs. Neither may hold tests, caches, review material or bytecode.
The documentation built by ``tools/build_docs.py`` and the build stamp written by
``tools/build_stamp.py`` are accepted when they are there, and required with ``--docs``
and ``--stamp`` (a release). Prints one line per problem and exits with 1
when there is any.

Stdlib only: this script runs under any Python 3.10 or later.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "cartolex"

#: Folder names that never belong in a distribution, at any depth.
FORBIDDEN_DIRS = frozenset(
    {
        "tests",
        "__pycache__",
        ".cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".venvs",
        "review",
        "node_modules",
        "_build",
    }
)
#: File suffixes and names that never belong in a distribution.
FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".swp", ".tmp", ".part", "~")
FORBIDDEN_NAMES = frozenset({".DS_Store", "Thumbs.db", ".lock"})
#: Files a vendored library must ship with (its licence next to its code).
VENDOR = f"{PACKAGE}/app/static/vendor"
#: The optional extras the metadata must declare.
EXTRAS = ("llm", "tsne", "dev", "docs")
#: What the source archive needs besides the package to build the wheel.
SDIST_FILES = ("pyproject.toml", "README.md", "LICENSE", "PKG-INFO")


#: Built, not tracked: the documentation the app serves (tools/build_docs.py), when built.
GENERATED = f"{PACKAGE}/app/static/docs"
#: Written, not tracked: the build stamp (tools/build_stamp.py), when written.
STAMP = f"{PACKAGE}/_data/build.json"


def package_files(root: Path = ROOT) -> set[str]:
    """The package's files, as POSIX paths relative to *root* (``cartolex/...``), with
    the built documentation and the build stamp when they are there."""
    built = root / GENERATED
    docs = (
        {p.relative_to(root).as_posix() for p in built.rglob("*") if p.is_file()}
        if built.is_dir()
        else set()
    )
    stamp = {STAMP} if (root / STAMP).is_file() else set()
    return _tracked(root) | docs | stamp


def _tracked(root: Path) -> set[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", PACKAGE],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        names = {n for n in out.decode("utf-8").split("\0") if n}
        if names:
            return {n for n in names if (root / n).is_file()}
    except (OSError, subprocess.CalledProcessError):
        pass
    base = root / PACKAGE
    return {
        p.relative_to(root).as_posix()
        for p in base.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix not in (".pyc", ".pyo")
        and not p.relative_to(root).as_posix().startswith(GENERATED + "/")
    }


def forbidden(name: str) -> bool:
    """Whether the archive member *name* is material that never ships."""
    path = PurePosixPath(name)
    if any(part in FORBIDDEN_DIRS for part in path.parts[:-1]):
        return True
    return path.name in FORBIDDEN_NAMES or path.name.endswith(FORBIDDEN_SUFFIXES)


def _vendor_problems(names: set[str]) -> list[str]:
    folders = {
        PurePosixPath(n).parts[4]
        for n in names
        if n.startswith(VENDOR + "/") and len(PurePosixPath(n).parts) > 5
    }
    return [
        f"vendored library without its licence: {VENDOR}/{f}/"
        for f in sorted(folders)
        if f"{VENDOR}/{f}/LICENSE" not in names
    ]


def check_wheel(path: Path, expected: set[str]) -> list[str]:
    """Problems of the wheel at *path* (empty when it holds exactly *expected* and its metadata)."""
    problems: list[str] = []
    with zipfile.ZipFile(path) as zf:
        members = {n for n in zf.namelist() if not n.endswith("/")}
        info = [n for n in members if n.endswith(".dist-info/METADATA")]
        metadata = Parser().parsestr(zf.read(info[0]).decode("utf-8")) if info else None
        entry = [n for n in members if n.endswith(".dist-info/entry_points.txt")]
        entry_points = zf.read(entry[0]).decode("utf-8") if entry else ""
    dist_info = {n for n in members if n.split("/", 1)[0].endswith(".dist-info")}
    files = members - dist_info
    problems += [f"missing from the wheel: {n}" for n in sorted(expected - files)]
    problems += [f"stray in the wheel: {n}" for n in sorted(files - expected)]
    problems += [f"never ships: {n}" for n in sorted(members) if forbidden(n)]
    problems += _vendor_problems(files)
    if not any(n.endswith(".dist-info/licenses/LICENSE") for n in dist_info):
        problems.append("the wheel's metadata has no LICENSE")
    if metadata is None:
        return [*problems, "the wheel has no METADATA"]
    if "cartolex = cartolex.cli:main" not in entry_points:
        problems.append("the wheel does not declare the cartolex command")
    extras = set(metadata.get_all("Provides-Extra") or [])
    problems += [f"extra not declared: [{e}]" for e in EXTRAS if e not in extras]
    for req in metadata.get_all("Requires-Dist") or []:
        if req.lower().startswith("mistralai") and "extra ==" not in req:
            problems.append(f"an optional library is a core dependency: {req}")
    return problems


def check_sdist(path: Path, expected: set[str]) -> list[str]:
    """Problems of the source archive at *path*."""
    with tarfile.open(path) as tf:
        members = {m.name for m in tf.getmembers() if m.isfile()}
    top = {n.split("/", 1)[0] for n in members}
    if len(top) != 1:
        return [f"the source archive has {len(top)} top folders, not one"]
    prefix = next(iter(top)) + "/"
    files = {n[len(prefix) :] for n in members}
    problems = [f"missing from the source archive: {n}" for n in SDIST_FILES if n not in files]
    problems += [f"missing from the source archive: {n}" for n in sorted(expected - files)]
    problems += [f"never ships: {n}" for n in sorted(files) if forbidden(n)]
    for n in sorted(files):
        if n.startswith(("tools/", "docs/", "deploy/", "installer/", ".github/")):
            problems.append(f"stray in the source archive: {n}")
        elif n.startswith(PACKAGE + "/") and n not in expected:
            problems.append(f"stray in the source archive: {n}")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("archives", nargs="+", type=Path, help="wheels (.whl) and sdists (.tar.gz)")
    parser.add_argument("--root", type=Path, default=ROOT, help="the checkout the files come from")
    parser.add_argument(
        "--docs", action="store_true", help="the built documentation must be there (a release)"
    )
    parser.add_argument(
        "--stamp", action="store_true", help="the build stamp must be there (a release)"
    )
    args = parser.parse_args(argv)
    expected = package_files(args.root)
    index = f"{GENERATED}/index.html"
    if args.docs and index not in expected:
        print(f"the documentation is not built: python tools/build_docs.py ({index})")
        return 1
    if args.stamp and STAMP not in expected:
        print(f"no build stamp: python tools/build_stamp.py ({STAMP})")
        return 1
    status = 0
    for archive in args.archives:
        if archive.suffix == ".whl":
            problems = check_wheel(archive, expected)
        elif archive.name.endswith(".tar.gz"):
            problems = check_sdist(archive, expected)
        else:
            problems = [f"not a wheel nor a source archive: {archive.name}"]
        for p in problems:
            print(f"{archive.name}: {p}")
        size = archive.stat().st_size / 1e6
        print(f"{archive.name}: {'ok' if not problems else 'FAILED'} ({size:.1f} MB)")
        status |= bool(problems)
    return status


if __name__ == "__main__":
    sys.exit(main())
