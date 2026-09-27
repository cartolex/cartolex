# SPDX-License-Identifier: MIT
"""Create the two pinned environments the reference run uses.

``released``
    The locked dependencies (``requirements-ref.txt``) plus the engine wheel
    built from the released tag :data:`RELEASED_TAG`. This environment
    produces the stored reference.
``current``
    The same locked dependencies plus this working tree, installed in
    editable mode without dependencies, and the pinned language models of the
    keyword extraction (``tools/requirements-models.txt``). This environment
    is compared with the stored reference and the stored baseline.

Both use the same pinned interpreter (:data:`PYTHON`, a uv-managed build), so
the only difference between them is the engine code. Environments live in
``.venvs/ref-<name>`` and are rebuilt when the lock, the interpreter or the
installed engine changes. Usage::

    python tools/reference/envs.py            # ensure both
    python tools/reference/envs.py current    # ensure one
    python tools/reference/envs.py --rebuild released

Stdlib only: this script runs under any Python 3.10 or later.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "tools" / "reference" / "requirements-ref.txt"
#: The pinned language models of the keyword extraction (``current`` only).
MODELS = ROOT / "tools" / "requirements-models.txt"
VENVS = ROOT / ".venvs"
CACHE = ROOT / ".cache" / "reference"
WHEELS = CACHE / "wheels"

#: The released engine the stored reference was generated with.
RELEASED_TAG = "v0.7.2"
#: Interpreter of both environments (uv-managed, so it is the same build everywhere).
PYTHON = "3.12.14"
#: Settings every reference run uses: one thread everywhere, fixed hashing, zone and locale.
FIXED_ENV = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMBA_NUM_THREADS": "1",
    "PYTHONHASHSEED": "0",
    "TZ": "UTC",
    "LC_ALL": "C.UTF-8",
}
ENV_NAMES = ("released", "current")
STAMP_NAME = ".reference-stamp.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def venv_dir(name: str) -> Path:
    """Directory of the reference environment *name*."""
    return VENVS / f"ref-{name}"


def venv_python(venv: Path) -> Path:
    """The interpreter inside *venv*."""
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def fixed_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """*base* (default: this process's environment) with the fixed run settings applied."""
    env = dict(os.environ if base is None else base)
    env.update(FIXED_ENV)
    env.setdefault("MPLBACKEND", "Agg")
    return env


def _uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("reference: uv is required (https://docs.astral.sh/uv/)")
    return uv


def _run(cmd: list[str], **kwargs) -> None:
    subprocess.run(cmd, check=True, **kwargs)


def _tag_commit(tag: str) -> str:
    out = subprocess.run(
        ["git", "rev-parse", f"{tag}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def build_released_wheel(tag: str = RELEASED_TAG) -> Path:
    """Build (once) the engine wheel of *tag* from a ``git archive`` of that tag.

    The wheel lands in ``.cache/reference/wheels/<commit>/``; a second call
    returns the cached file.
    """
    commit = _tag_commit(tag)
    out_dir = WHEELS / commit
    existing = sorted(out_dir.glob("*.whl")) if out_dir.is_dir() else []
    if existing:
        return existing[0]
    with tempfile.TemporaryDirectory(prefix="ref-src-") as tmp:
        archive = Path(tmp) / "src.tar"
        with archive.open("wb") as handle:
            _run(["git", "archive", "--format=tar", commit], cwd=ROOT, stdout=handle)
        src = Path(tmp) / "src"
        src.mkdir()
        with tarfile.open(archive) as tar:
            if sys.version_info >= (3, 12):
                tar.extractall(src, filter="data")
            else:  # pragma: no cover - Python 3.10/3.11
                tar.extractall(src)
        out_dir.mkdir(parents=True, exist_ok=True)
        _run([_uv(), "build", "--quiet", "--wheel", "--out-dir", str(out_dir), str(src)])
    wheels = sorted(out_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"reference: expected one wheel in {out_dir}, found {len(wheels)}")
    return wheels[0]


def _wanted_stamp(name: str, python: str) -> dict[str, str]:
    stamp = {"env": name, "python": python, "lock_sha256": _sha256_file(LOCK)}
    if name == "released":
        stamp["engine"] = f"wheel:{RELEASED_TAG}:{_tag_commit(RELEASED_TAG)}"
    else:
        stamp["engine"] = f"editable:{ROOT}"
        stamp["pyproject_sha256"] = _sha256_file(ROOT / "pyproject.toml")
        stamp["models_sha256"] = _sha256_file(MODELS)
    return stamp


def ensure_env(name: str, *, python: str = PYTHON, rebuild: bool = False) -> Path:
    """Create or refresh the reference environment *name*; return its directory."""
    if name not in ENV_NAMES:
        raise SystemExit(f"reference: unknown environment {name!r} (expected {ENV_NAMES})")
    venv = venv_dir(name)
    stamp_path = venv / STAMP_NAME
    wanted = _wanted_stamp(name, python)
    if not rebuild and stamp_path.is_file():
        try:
            if json.loads(stamp_path.read_text(encoding="utf-8")) == wanted:
                return venv
        except ValueError:
            pass
    uv = _uv()
    _run([uv, "venv", "--quiet", "--clear", "--managed-python", "--python", python, str(venv)])
    py = str(venv_python(venv))
    _run([uv, "pip", "sync", "--quiet", "-p", py, str(LOCK)])
    if name == "released":
        wheel = build_released_wheel()
        _run([uv, "pip", "install", "--quiet", "--no-deps", "-p", py, str(wheel)])
        record = {**wanted, "wheel": wheel.name, "wheel_sha256": _sha256_file(wheel)}
        (venv / "reference-wheel.json").write_text(
            json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
        )
    else:
        _run([uv, "pip", "install", "--quiet", "--no-deps", "-p", py, "-e", str(ROOT)])
        _run([uv, "pip", "install", "--quiet", "-p", py, "--require-hashes", "-r", str(MODELS)])
    stamp_path.write_text(json.dumps(wanted, indent=2, sort_keys=True), encoding="utf-8")
    return venv


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Create the pinned reference environments.")
    parser.add_argument("names", nargs="*", help=f"environments to ensure (default: {ENV_NAMES})")
    parser.add_argument("--rebuild", action="store_true", help="recreate even when up to date")
    parser.add_argument("--python", default=PYTHON, help=f"interpreter request (default {PYTHON})")
    args = parser.parse_args(argv)
    for name in args.names or ENV_NAMES:
        venv = ensure_env(name, python=args.python, rebuild=args.rebuild)
        print(f"{name}: {venv_python(venv)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
