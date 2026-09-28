# SPDX-License-Identifier: MIT
"""Run every check that must pass before a merge, and print one line per check.

Usage::

    python tools/check.py            # everything (tests on every configured Python)
    python tools/check.py --quick    # lint, vocabulary, tests on one Python, reference S
    python tools/check.py --full     # also the large reference comparison
    python tools/check.py --only tests --pythons 3.12

Checks, in order: ``lint`` (ruff), ``vocab`` (banned terms in files and commit
messages), ``js`` (static checks of the web interface: parse, imports, literal
text, bans, vendored hashes, catalogues, contrast), ``tests`` (pytest on each
Python, in parallel), ``browser`` (the web interface in headless Chromium:
axe, keyboard, budgets, leaks), ``reference`` (the numeric comparison with the
stored reference run), ``docs`` (strict Sphinx build). Settings live in ``tools/check.toml``. Virtual environments are kept in
``.venvs/`` and rebuilt when ``pyproject.toml`` changes. Needs ``uv`` and
Python 3.11 or later to run this script itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tools" / "check.toml"
#: The pinned language models every test environment holds (the extraction's parsers).
MODELS = ROOT / "tools" / "requirements-models.txt"
VENVS = ROOT / ".venvs"
LOGS = ROOT / ".cache" / "check"
#: The browser checks' tools, installed into the quick Python's environment only.
BROWSER_TOOLS = ROOT / "tools" / "requirements-browser.txt"
ALL_CHECKS = ("lint", "vocab", "js", "tests", "browser", "reference", "docs")


@dataclass
class Result:
    """Outcome of one check."""

    name: str
    status: str  # "pass", "fail" or "skip"
    detail: str
    seconds: float


def load_config() -> dict:
    """Read ``tools/check.toml``."""
    return tomllib.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.is_file() else {}


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_venv(python: str, extras: str = "dev") -> Path:
    """Create or refresh ``.venvs/py<python>``: the project, its *extras* and the language models.

    The environment is rebuilt only when ``pyproject.toml``, the pinned models
    (``tools/requirements-models.txt``) or the extras change; the models are
    installed from their pinned wheels, checked against their hashes.
    """
    venv = VENVS / f"py{python}"
    stamp = venv / ".cartolex-stamp"
    want = f"{_file_hash(ROOT / 'pyproject.toml')}:{_file_hash(MODELS)}:{extras}"
    if stamp.is_file() and stamp.read_text() == want:
        return venv
    subprocess.run(
        ["uv", "venv", "--quiet", "--allow-existing", "--python", python, str(venv)],
        check=True,
    )
    subprocess.run(
        ["uv", "pip", "install", "--quiet", "-p", str(venv), "-e", f".[{extras}]"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        ["uv", "pip", "install", "--quiet", "-p", str(venv), "--require-hashes", "-r", str(MODELS)],
        cwd=ROOT,
        check=True,
    )
    stamp.write_text(want)
    return venv


def bin_of(venv: Path, name: str) -> str:
    """Path of an executable inside a virtual environment."""
    return str(venv / ("Scripts" if os.name == "nt" else "bin") / name)


def run(cmd: list[str], log: Path, env: dict | None = None) -> tuple[int, str]:
    """Run *cmd* from the repository root, save its output to *log*, return code and tail."""
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
    output = proc.stdout + proc.stderr
    log.write_text(output, encoding="utf-8")
    tail = [line for line in output.strip().splitlines() if line.strip()]
    return proc.returncode, (tail[-1] if tail else "")


def check_lint(dev: Path) -> Result:
    """ruff check and ruff format --check, with the pinned ruff."""
    t0 = time.monotonic()
    ruff = bin_of(dev, "ruff")
    rc1, tail1 = run([ruff, "check", ".", "--no-fix"], LOGS / "lint-check.log")
    rc2, tail2 = run([ruff, "format", "--check", "."], LOGS / "lint-format.log")
    ok = rc1 == 0 and rc2 == 0
    detail = "clean" if ok else f"check: {tail1} | format: {tail2}"
    return Result("lint", "pass" if ok else "fail", detail, time.monotonic() - t0)


def check_vocab(cfg: dict, dev: Path) -> Result:
    """Banned terms in the tree, the configured commit range and generated demo worlds."""
    t0 = time.monotonic()
    vcfg = cfg.get("vocab", {})
    cmd = [sys.executable, "tools/vocab_scan.py", "--strict"]
    if vcfg.get("commits"):
        cmd += ["--commits", vcfg["commits"]]
    if vcfg.get("tags"):
        cmd.append("--tags")
    rc, tail = run(cmd, LOGS / "vocab.log")
    if rc == 0 and "skipped" in tail:
        return Result("vocab", "skip", tail, time.monotonic() - t0)
    # Invented names can collide with real ones by chance: scan the demo worlds that
    # tests, the reference run and the docs use, exactly as they are generated.
    worlds = LOGS / "demo-worlds"
    shutil.rmtree(worlds, ignore_errors=True)
    for spec in vcfg.get("demo_worlds", []):
        size, seed, *rest = spec.split(":")
        languages = rest[0] if rest else "en,fr"
        name = f"{size}-{seed}" + (f"-{languages.replace(',', '')}" if rest else "")
        run(
            [
                bin_of(dev, "python"),
                "-m",
                "cartolex.demo",
                "create",
                "--size",
                size,
                "--seed",
                seed,
                "--languages",
                languages,
                "--out",
                str(worlds / name),
                "--corpus",
            ],
            LOGS / f"demo-{name}.log",
        )
    rc2, tail2 = (0, "")
    if worlds.is_dir():
        rc2, tail2 = run(
            [sys.executable, "tools/vocab_scan.py", "--paths", str(worlds)],
            LOGS / "vocab-demo.log",
        )
        tail2 = "demo worlds: " + tail2.removeprefix("vocab: ")
    ok = rc == 0 and rc2 == 0
    detail = tail + (f" · {tail2}" if tail2 else "")
    return Result("vocab", "pass" if ok else "fail", detail, time.monotonic() - t0)


def ensure_browser_tools(venv: Path) -> str | None:
    """Install Playwright into *venv* when it is missing; returns why it could not, or None."""
    probe = [bin_of(venv, "python"), "-c", "import playwright"]
    if subprocess.run(probe, capture_output=True).returncode == 0:
        return None
    proc = subprocess.run(
        ["uv", "pip", "install", "--quiet", "-p", str(venv), "-r", str(BROWSER_TOOLS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return "Playwright could not be installed (" + (proc.stderr.strip()[-200:] or "uv") + ")"
    return None


def check_js(dev: Path) -> Result:
    """The web interface's static checks (tools/ui_check.py)."""
    t0 = time.monotonic()
    ensure_browser_tools(dev)  # its Node parses the modules; without it, parse is skipped
    rc, tail = run([bin_of(dev, "python"), "tools/ui_check.py"], LOGS / "js.log")
    lines = (LOGS / "js.log").read_text(encoding="utf-8").splitlines()
    counts = [line for line in lines if line[:4] in ("PASS", "FAIL", "SKIP")]
    detail = f"{sum(line.startswith('PASS') for line in counts)}/{len(counts)} clean"
    skipped = [line.split()[1] for line in counts if line.startswith("SKIP")]
    if skipped:
        detail += f", skipped: {', '.join(skipped)} (no Node)"
    if rc != 0:
        detail = (
            tail
            if not counts
            else f"failed: {', '.join(c.split()[1] for c in counts if c.startswith('FAIL'))}"
        )
    return Result("js", "pass" if rc == 0 else "fail", detail, time.monotonic() - t0)


def check_browser(dev: Path, quick: bool) -> Result:
    """The web interface in headless Chromium (tests/browser); --quick leaves out slow tests."""
    t0 = time.monotonic()
    why = ensure_browser_tools(dev)
    if why:
        return Result("browser", "skip", why, time.monotonic() - t0)
    measures = LOGS / "ui-measures.json"
    measures.unlink(missing_ok=True)
    cmd = [
        bin_of(dev, "python"),
        "-m",
        "pytest",
        "-q",
        "-rs",
        "-p",
        "no:cacheprovider",
        "tests/browser",
        "-m",
        "browser and not slow" if quick else "browser",
    ]
    env = {
        **os.environ,
        "CARTOLEX_UI_MEASURES": str(measures),
        "PYTHONPYCACHEPREFIX": str(ROOT / ".cache" / "pycache" / "browser"),
        "PYTHONPATH": os.pathsep.join(
            p for p in (str(ROOT), os.environ.get("PYTHONPATH", "")) if p
        ),
    }
    rc, tail = run(cmd, LOGS / "browser.log", env=env)
    summary = tail.strip("= ")
    if rc == 0 and " passed" not in summary and "skipped" in summary:
        reasons = [
            line.split(": ", 1)[-1]
            for line in (LOGS / "browser.log").read_text(encoding="utf-8").splitlines()
            if line.startswith("SKIPPED")
        ]
        return Result("browser", "skip", reasons[0] if reasons else summary, time.monotonic() - t0)
    detail = summary
    if measures.is_file():
        detail += " · " + _measures_summary(json.loads(measures.read_text(encoding="utf-8")))
    return Result("browser", "pass" if rc == 0 else "fail", detail, time.monotonic() - t0)


def _measures_summary(data: dict) -> str:
    parts = []
    rows = data.get("budgets", {}).get("navigations", [])
    if rows:
        slowest = max(rows, key=lambda r: r["ready_ms"])
        parts.append(
            f"slowest route {slowest['ready_ms']:.0f} ms ({slowest['page']}), "
            f"at most {max(r['requests'] for r in rows)} requests"
        )
    leaks = data.get("leaks")
    if leaks:
        g, p = leaks["growth"], leaks["growth_percent"]
        parts.append(
            f"after {leaks['rounds']} round trips: nodes {g['nodes']:+.0f}, "
            f"listeners {g['listeners']:+.0f}, heap {p['heap']:+.1f} %"
        )
    return " · ".join(parts)


def check_tests(pythons: list[str], jobs: int) -> Result:
    """pytest on every Python, at most *jobs* at a time; one summary per version."""
    t0 = time.monotonic()
    pending = [(py, ensure_venv(py)) for py in pythons]
    running: list[tuple[str, subprocess.Popen, Path]] = []
    parts: dict[str, str] = {}
    ok = True
    while pending or running:
        while pending and len(running) < max(1, jobs):
            py, venv = pending.pop(0)
            log = LOGS / f"tests-py{py}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                bin_of(venv, "python"),
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                # The models are installed with the environment: a test that
                # needs one must run, never be skipped.
                "--require-models",
                # The browser tests run once, in the `browser` check.
                "-m",
                "not browser",
            ]
            # Each Python keeps its bytecode out of the source tree, so suites running
            # side by side never see each other's cache files. The tree under test comes
            # first on the path, in the processes the tests start too: an environment
            # shared by several checkouts may have its editable install in another one.
            env = {
                **os.environ,
                "PYTHONPYCACHEPREFIX": str(ROOT / ".cache" / "pycache" / py),
                "PYTHONPATH": os.pathsep.join(
                    p for p in (str(ROOT), os.environ.get("PYTHONPATH", "")) if p
                ),
            }
            with log.open("w", encoding="utf-8") as handle:
                proc = subprocess.Popen(
                    cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, env=env
                )
            running.append((py, proc, log))
        py, proc, log = running.pop(0)
        rc = proc.wait()
        lines = [x for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
        parts[py] = f"{py}: {lines[-1].strip('= ') if lines else 'no output'}"
        ok &= rc == 0
    summary = " · ".join(parts[py] for py in pythons)
    return Result("tests", "pass" if ok else "fail", summary, time.monotonic() - t0)


def check_reference(full: bool) -> Result:
    """The numeric comparison with the stored reference run."""
    t0 = time.monotonic()
    script = ROOT / "tools" / "reference" / "check_reference.py"
    if not script.is_file():
        return Result("reference", "skip", "no reference tooling yet", 0.0)
    sizes = ["S", "L"] if full else ["S"]
    parts, ok = [], True
    for size in sizes:
        # The engine on the demo workspace, then the same world built as a project.
        for via, label in (([], size), (["--via-project"], f"{size} via a project")):
            rc, tail = run(
                [sys.executable, str(script), "--size", size, *via],
                LOGS / f"reference-{size}{'-project' if via else ''}.log",
            )
            parts.append(f"{label}: {tail}")
            ok &= rc == 0
    return Result("reference", "pass" if ok else "fail", " · ".join(parts), time.monotonic() - t0)


def check_docs() -> Result:
    """Strict Sphinx build: any warning fails."""
    t0 = time.monotonic()
    if not (ROOT / "docs" / "conf.py").is_file():
        return Result("docs", "skip", "no docs/conf.py", 0.0)
    venv = ensure_venv("3.12", extras="dev,docs")
    out = ROOT / "docs" / "_build" / "html"
    shutil.rmtree(out, ignore_errors=True)
    rc, tail = run(
        [bin_of(venv, "sphinx-build"), "-W", "--keep-going", "-q", "-b", "html", "docs", str(out)],
        LOGS / "docs.log",
    )
    detail = "strict build clean" if rc == 0 else tail
    return Result("docs", "pass" if rc == 0 else "fail", detail, time.monotonic() - t0)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Run the pre-merge checks.")
    parser.add_argument(
        "--quick", action="store_true", help="one Python, small reference, no slow browser test"
    )
    parser.add_argument("--full", action="store_true", help="also the large reference")
    parser.add_argument("--pythons", help="comma-separated versions, e.g. 3.10,3.12")
    parser.add_argument("--only", nargs="+", choices=ALL_CHECKS, help="run only these checks")
    args = parser.parse_args(argv)

    if shutil.which("uv") is None:
        print("check: uv is required (https://docs.astral.sh/uv/)", file=sys.stderr)
        return 2
    cfg = load_config()
    pythons = cfg.get("tests", {}).get("pythons", ["3.12"])
    if args.pythons:
        pythons = [p.strip() for p in args.pythons.split(",") if p.strip()]
    elif args.quick:
        pythons = [cfg.get("tests", {}).get("quick", "3.12")]
    wanted = args.only or list(ALL_CHECKS)

    results: list[Result] = []
    dev = ensure_venv(cfg.get("tests", {}).get("quick", "3.12"))
    for name in wanted:
        if name == "lint":
            results.append(check_lint(dev))
        elif name == "vocab":
            results.append(check_vocab(cfg, dev))
        elif name == "js":
            results.append(check_js(dev))
        elif name == "browser":
            results.append(check_browser(dev, args.quick))
        elif name == "tests":
            results.append(check_tests(pythons, int(cfg.get("tests", {}).get("jobs", 2))))
        elif name == "reference":
            results.append(check_reference(args.full))
        elif name == "docs":
            results.append(check_docs())

    width = max(len(r.name) for r in results)
    for r in results:
        print(f"{r.status.upper():4}  {r.name:<{width}}  {r.seconds:6.1f}s  {r.detail}")
    failed = [r.name for r in results if r.status == "fail"]
    print("check: " + ("FAILED: " + ", ".join(failed) if failed else "all passed"))
    print(f"logs: {LOGS.relative_to(ROOT)}/")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
