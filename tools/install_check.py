# SPDX-License-Identifier: MIT
"""Install the built wheel into fresh environments and use it as a new user would.

Usage::

    python tools/install_check.py                       # Python 3.10 and 3.14
    python tools/install_check.py --pythons 3.12 --wheel dist/cartolex-*.whl
    python tools/install_check.py --keep                # leave the folders for a look
    python tools/install_check.py --cold-cache          # download everything, as a first install

For each Python it creates a new virtual environment with uv, installs the wheel
(built from this checkout unless ``--wheel`` is given, and checked with
``tools/package_check.py``), installs the language models of the demo world
(``cartolex models add en fr --yes``), runs ``cartolex --help``, writes the XS
demo world as a project and builds it with ``cartolex build``, then starts
``cartolex api`` on a free port and fetches ``/api/health``. It prints the time
and disk size of each step and removes the environments. It reaches the
network (the package index and the language models' releases) and needs
`uv <https://docs.astral.sh/uv/>`_. Exits with 1 when a step fails.

Stdlib only: this script runs under any Python 3.10 or later.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import package_check  # noqa: E402

DEMO_PROJECT = """
import sys
from cartolex.demo import generate
from cartolex.demo.project import write_project
write_project(generate(size="XS", seed=0), sys.argv[1]).close()
"""
LISTENING = re.compile(r"cartolex is running: (http://[^/\s]+)/")


@dataclass
class Report:
    """What one Python measured."""

    python: str
    steps: list[tuple[str, bool, float, str]] = field(default_factory=list)
    ok: bool = True

    def add(self, name: str, ok: bool, seconds: float, detail: str = "") -> bool:
        self.steps.append((name, ok, seconds, detail))
        self.ok &= ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {name:<16} {seconds:7.1f}s  {detail}", flush=True)
        return ok


def folder_mb(path: Path) -> float:
    """Disk size of *path* in MB (sum of the file sizes)."""
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total / 1e6


def longest_path(root: Path) -> int:
    """Length of the longest path under *root*, relative to it (Windows' path limit)."""
    return max((len(p.relative_to(root).as_posix()) for p in root.rglob("*")), default=0)


def run(
    cmd: list[str], *, env: dict | None = None, timeout: float = 1800
) -> tuple[bool, float, str]:
    """Run *cmd*; whether it succeeded, its time and the last line of its output."""
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    lines = [x for x in (proc.stdout + proc.stderr).splitlines() if x.strip()]
    tail = lines[-1].strip() if lines else ""
    if proc.returncode != 0:
        tail = " | ".join(x.strip() for x in lines[-5:])
    return proc.returncode == 0, time.monotonic() - t0, tail[:300]


def build_wheel(out: Path) -> Path:
    """Build the wheel of this checkout (through its source archive) into *out*."""
    subprocess.run(["uv", "build", "--quiet", "--out-dir", str(out), str(ROOT)], check=True)
    (wheel,) = out.glob("cartolex-*.whl")
    return wheel


def health(base: str, timeout: float = 10) -> str:
    with urllib.request.urlopen(f"{base}/api/health", timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8")).get("status", "")


def serve_and_probe(cartolex: str, project: Path, data_dir: Path, env: dict) -> tuple[bool, str]:
    """Start ``cartolex api`` on a free port, fetch ``/api/health``, stop it."""
    proc = subprocess.Popen(
        [cartolex, "api", str(project), "--port", "0", "--data-dir", str(data_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        deadline = time.monotonic() + 120
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                return False, f"the server stopped (exit {proc.poll()})"
            found = LISTENING.search(line)
            if found:
                status = health(found.group(1))
                return status == "ok", f"{found.group(1)}/api/health: {status!r}"
        return False, "no listening line within 120 s"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def check_python(python: str, wheel: Path, work: Path) -> Report:
    """Every step for one Python, in *work* (removed by the caller)."""
    report = Report(python)
    print(f"Python {python}", flush=True)
    venv = work / f"venv-{python}"
    exe = venv / ("Scripts" if os.name == "nt" else "bin")
    env = {**os.environ, "PYTHONUTF8": "1", "MPLBACKEND": "Agg", "VIRTUAL_ENV": str(venv)}
    env["PATH"] = str(exe) + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONPATH", None)  # the installed package only, never this checkout
    ok, s, tail = run(["uv", "venv", "--quiet", "--python", python, str(venv)])
    if not report.add("venv", ok, s, tail):
        return report
    py = str(exe / ("python.exe" if os.name == "nt" else "python"))
    ok, s, tail = run(["uv", "pip", "install", "--quiet", "-p", py, str(wheel)])
    if not report.add("install", ok, s, f"environment {folder_mb(venv):.0f} MB" if ok else tail):
        return report
    before = folder_mb(venv)
    cartolex = str(exe / ("cartolex.exe" if os.name == "nt" else "cartolex"))
    ok, s, tail = run([cartolex, "models", "add", "en", "fr", "--yes"], env=env)
    detail = f"+{folder_mb(venv) - before:.0f} MB, environment {folder_mb(venv):.0f} MB"
    if not report.add("models add", ok, s, detail if ok else tail):
        return report
    t0 = time.monotonic()
    proc = subprocess.run(
        [cartolex, "--help"], capture_output=True, text=True, encoding="utf-8", env=env
    )
    ok = proc.returncode == 0 and "usage: cartolex" in proc.stdout
    report.add("--help", ok, time.monotonic() - t0, "" if ok else proc.stderr.strip()[-300:])
    project = work / f"project-{python}"
    ok, s, tail = run([py, "-I", "-c", DEMO_PROJECT, str(project)], env=env)
    if not report.add("demo XS", ok, s, tail):
        return report
    ok, s, tail = run([cartolex, "build", str(project), "--yes"], env=env)
    detail = f"project {folder_mb(project):.0f} MB, longest path {longest_path(project)} characters"
    if not report.add("build", ok, s, detail if ok else tail):
        return report
    t0 = time.monotonic()
    try:
        ok, detail = serve_and_probe(cartolex, project, work / f"data-{python}", env)
    except OSError as exc:
        ok, detail = False, str(exc)
    report.add("api /health", ok, time.monotonic() - t0, detail)
    return report


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--pythons", default="3.10,3.14", help="comma-separated versions")
    parser.add_argument("--wheel", type=Path, help="the wheel to install (default: build one)")
    parser.add_argument("--work", type=Path, help="the working folder (default: a temporary one)")
    parser.add_argument("--keep", action="store_true", help="keep the working folder")
    parser.add_argument(
        "--cold-cache",
        action="store_true",
        help="an empty uv cache in the working folder: every package is downloaded, as for a "
        "first install",
    )
    args = parser.parse_args(argv)
    if shutil.which("uv") is None:
        print("install check: uv is required (https://docs.astral.sh/uv/)", file=sys.stderr)
        return 2
    work = Path(args.work or tempfile.mkdtemp(prefix="cartolex-install-"))
    work.mkdir(parents=True, exist_ok=True)
    if args.cold_cache:
        os.environ["UV_CACHE_DIR"] = str(work / "uv-cache")
    reports: list[Report] = []
    try:
        t0 = time.monotonic()
        wheel = args.wheel or build_wheel(work / "dist")
        expected = package_check.package_files(ROOT)
        if args.wheel:
            # A wheel built elsewhere (the release workflow's build job, which checks them
            # with --docs --stamp) brings the built documentation and the build stamp,
            # which this checkout need not have.
            with zipfile.ZipFile(wheel) as zf:
                expected |= {
                    n
                    for n in zf.namelist()
                    if n.startswith(package_check.GENERATED + "/") or n == package_check.STAMP
                }
        problems = package_check.check_wheel(wheel, expected)
        size = wheel.stat().st_size / 1e6
        print(f"wheel {wheel.name}: {size:.1f} MB, {time.monotonic() - t0:.1f}s", flush=True)
        for p in problems:
            print(f"  {p}")
        if problems:
            return 1
        for python in (p.strip() for p in args.pythons.split(",") if p.strip()):
            reports.append(check_python(python, wheel, work))
            if not args.keep:
                for name in (f"venv-{python}", f"project-{python}", f"data-{python}"):
                    shutil.rmtree(work / name, ignore_errors=True)
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"kept: {work}")
    failed = [r.python for r in reports if not r.ok]
    print("install check: " + (f"FAILED on {', '.join(failed)}" if failed else "all passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
