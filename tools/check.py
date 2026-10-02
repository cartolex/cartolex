# SPDX-License-Identifier: MIT
"""Run every check that must pass before a merge, and print one line per check.

Usage::

    python tools/check.py            # everything (tests on every configured Python)
    python tools/check.py --quick    # lint, vocabulary, tests on one Python, reference S
    python tools/check.py --full     # also the large reference comparison
    python tools/check.py --only tests --pythons 3.12

Checks: ``lint`` (ruff), ``vocab`` (banned terms in files and commit
messages), ``js`` (static checks of the web interface: parse, imports, literal
text, bans, vendored hashes, catalogues, contrast), ``tests`` (pytest on each
Python, in worker processes), ``browser`` (the web interface in headless Chromium:
axe, keyboard, budgets, leaks), ``reference`` (the numeric comparison with the
stored reference run), ``docs`` (strict Sphinx build). They run at the same time,
the browser checks last, alone, since their budgets measure time. The reference and
the demo worlds' scan are skipped when nothing they read changed since they last
passed (``--fresh``, ``--full`` and ``--heavy`` run them anyway). Settings live in
``tools/check.toml``. Virtual environments are kept in ``.venvs/`` and rebuilt
when ``pyproject.toml`` changes. Needs ``uv`` and Python 3.11 or later to run this
script itself.
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
from concurrent.futures import ThreadPoolExecutor
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
#: The extras of the quick Python's environment: lint, docs and the browser checks share it.
DEV_EXTRAS = "dev,docs"
#: Numerical libraries run one thread in each test worker: the workers share the cores.
ONE_THREAD = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMBA_NUM_THREADS": "1",
}
#: What the reference run reads: everything but the web application above it (the layering
#: test keeps the packages below it from importing it), the reference tooling and the
#: dependencies.
REFERENCE_INPUTS = (
    "cartolex/",
    "tools/reference/",
    "pyproject.toml",
    "tools/requirements-models.txt",
)
REFERENCE_OUTSIDE = ("cartolex/app/",)


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


def inputs_key(prefixes: tuple[str, ...], outside: tuple[str, ...] = (), *extra: str) -> str:
    """A digest of the files git knows under *prefixes* (not under *outside*), as they are
    in the working tree, and of the *extra* strings."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", *prefixes],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    digest = hashlib.sha256()
    for rel in sorted({p for p in out.split("\0") if p}):
        path = ROOT / rel
        if any(rel.startswith(o) for o in outside) or not path.is_file():
            continue
        digest.update(rel.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    for text in extra:
        digest.update(text.encode("utf-8") + b"\0")
    return digest.hexdigest()


def passed_with(name: str, key: str) -> bool:
    """Whether the check *name* last passed on inputs of digest *key*."""
    mark = LOGS / f"{name}.passed"
    return mark.is_file() and mark.read_text(encoding="utf-8").strip() == key


def record_pass(name: str, key: str) -> None:
    (LOGS / f"{name}.passed").parent.mkdir(parents=True, exist_ok=True)
    (LOGS / f"{name}.passed").write_text(key + "\n", encoding="utf-8")


def ensure_venv(python: str, extras: str = "dev") -> Path:
    """Create or refresh ``.venvs/py<python>``: the project, its *extras* and the language models.

    The environment is rebuilt only when ``pyproject.toml``, the pinned models
    (``tools/requirements-models.txt``) or the extras change; the models are
    installed from their pinned wheels, checked against their hashes.
    """
    venv = VENVS / f"py{python}"
    if os.environ.get("CARTOLEX_VENVS_FROZEN") and (venv / "pyvenv.cfg").is_file():
        # Environments shared by several checkouts: use them as they are (the tree under
        # test comes first on the path), never reinstall while another check runs.
        return venv
    stamp = venv / ".cartolex-stamp"
    want = f"{_file_hash(ROOT / 'pyproject.toml')}:{_file_hash(MODELS)}:{extras}"
    if stamp.is_file() and stamp.read_text(encoding="utf-8") == want:
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
    stamp.write_text(want, encoding="utf-8")
    return venv


def bin_of(venv: Path, name: str) -> str:
    """Path of an executable inside a virtual environment."""
    return str(venv / ("Scripts" if os.name == "nt" else "bin") / name)


def run(cmd: list[str], log: Path, env: dict | None = None) -> tuple[int, str]:
    """Run *cmd* from the repository root, save its output to *log*, return code and tail."""
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env
    )
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


def _deny_list_text() -> str:
    """The term list's text, for the digest of what the demo worlds' scan read."""
    for path in (
        os.environ.get("CARTOLEX_DENYLIST"),
        str(Path.home() / ".config" / "cartolex-dev" / "deny-list.txt"),
    ):
        if path and Path(path).is_file():
            return Path(path).read_text(encoding="utf-8", errors="replace")
    return ""


def check_vocab(cfg: dict, dev: Path, fresh: bool = False) -> Result:
    """Banned terms in the tree, the configured commit range and generated demo worlds (these
    only when the generator, the scanner, the list or the worlds changed since they passed)."""
    t0 = time.monotonic()
    vcfg = cfg.get("vocab", {})
    cmd = [
        sys.executable,
        "tools/vocab_scan.py",
        "--strict",
        "--cache",
        str(LOGS / "vocab-tree.json"),
    ]
    if vcfg.get("commits"):
        cmd += ["--commits", vcfg["commits"]]
    if vcfg.get("tags"):
        cmd.append("--tags")
    rc, tail = run(cmd, LOGS / "vocab.log")
    if rc == 0 and "skipped" in tail:
        return Result("vocab", "skip", tail, time.monotonic() - t0)
    # Invented names can collide with real ones by chance: scan the demo worlds that
    # tests, the reference run and the docs use, exactly as they are generated, with every
    # name their demo services invent (outside co-authors, homonyms, institutions).
    key = inputs_key(
        ("cartolex/demo/", "tools/vocab_scan.py"),
        (),
        _deny_list_text(),
        json.dumps(vcfg.get("demo_worlds", [])),
    )
    if not fresh and passed_with("vocab-demo", key):
        detail = f"{tail} · demo worlds: unchanged since they passed"
        return Result("vocab", "pass" if rc == 0 else "fail", detail, time.monotonic() - t0)
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
                "--layer",
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
    if rc2 == 0:
        record_pass("vocab-demo", key)
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
        encoding="utf-8",
        errors="replace",
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


def check_browser(dev: Path, quick: bool, workers: int = 1) -> Result:
    """The web interface in headless Chromium (tests/browser), in *workers* processes;
    --quick leaves out slow tests."""
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
        *(["-n", str(workers), "--dist", "loadscope"] if workers > 1 else []),
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
            f"at most {max(r['api_calls'] for r in rows)} API calls"
        )
    leaks = data.get("leaks")
    if leaks:
        g, p = leaks["growth"], leaks["growth_percent"]
        parts.append(
            f"after {leaks['rounds']} round trips: nodes {g['nodes']:+.0f}, "
            f"listeners {g['listeners']:+.0f}, heap {p['heap']:+.1f} %"
        )
    return " · ".join(parts)


def check_tests(
    venvs: list[tuple[str, Path]],
    jobs: int,
    heavy: str | None = None,
    workers: int = 1,
    dist: str = "loadscope",
) -> Result:
    """pytest on every Python of *venvs*, at most *jobs* at a time, each in *workers*
    processes sharing the tests by *dist* (``loadscope``: a module's tests, hence its
    fixtures, on one worker); one summary per version.

    *heavy* names the Python that also runs the tests marked ``heavy`` (large
    measures, memory-capped, an hour or more): only with ``--heavy``, before a release.
    """
    t0 = time.monotonic()
    pythons = [py for py, _venv in venvs]
    pending = list(venvs)
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
                *(["--heavy"] if py == heavy else []),
                *(["-n", str(workers), "--dist", dist] if workers > 1 else []),
            ]
            # Each Python keeps its bytecode out of the source tree, so suites running
            # side by side never see each other's cache files. The tree under test comes
            # first on the path, in the processes the tests start too: an environment
            # shared by several checkouts may have its editable install in another one.
            env = {
                **os.environ,
                **(ONE_THREAD if workers > 1 else {}),
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
        lines = [
            x for x in log.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()
        ]
        parts[py] = f"{py}: {lines[-1].strip('= ') if lines else 'no output'}"
        ok &= rc == 0
    summary = " · ".join(parts[py] for py in pythons)
    return Result("tests", "pass" if ok else "fail", summary, time.monotonic() - t0)


def check_reference(full: bool, fresh: bool = False) -> Result:
    """The numeric comparison with the stored reference run, unless nothing it reads changed
    since it last passed (*fresh* runs it anyway)."""
    t0 = time.monotonic()
    script = ROOT / "tools" / "reference" / "check_reference.py"
    if not script.is_file():
        return Result("reference", "skip", "no reference tooling yet", 0.0)
    sizes = ["S", "L"] if full else ["S"]
    key = inputs_key(REFERENCE_INPUTS, REFERENCE_OUTSIDE, ",".join(sizes))
    if not fresh and passed_with("reference", key):
        detail = f"{'+'.join(sizes)}: nothing it reads changed since it passed"
        return Result("reference", "pass", detail, time.monotonic() - t0)
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
    if ok:
        record_pass("reference", key)
    return Result("reference", "pass" if ok else "fail", " · ".join(parts), time.monotonic() - t0)


def check_docs(venv: Path) -> Result:
    """Strict Sphinx build: any warning fails."""
    t0 = time.monotonic()
    if not (ROOT / "docs" / "conf.py").is_file():
        return Result("docs", "skip", "no docs/conf.py", 0.0)
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
    parser.add_argument(
        "--heavy",
        action="store_true",
        help="also the tests marked heavy (large measures, memory-capped; an hour or more)",
    )
    parser.add_argument("--pythons", help="comma-separated versions, e.g. 3.10,3.12")
    parser.add_argument("--only", nargs="+", choices=ALL_CHECKS, help="run only these checks")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="run the reference and the demo worlds' scan even when nothing they read changed",
    )
    args = parser.parse_args(argv)

    # Every tool this script starts reads and writes UTF-8, whatever the system's code page
    # (Windows): their logs are read back as UTF-8.
    os.environ.setdefault("PYTHONUTF8", "1")
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

    tcfg = cfg.get("tests", {})
    quick = tcfg.get("quick", "3.12")
    fresh = args.fresh or args.full or args.heavy
    # The environments first, one after the other: two checks never build one at once. The
    # quick Python's runs lint, the demo worlds of vocab, js, the browser checks and the docs.
    needs_dev = {"lint", "vocab", "js", "browser", "docs"} & set(wanted)
    dev = ensure_venv(quick, DEV_EXTRAS) if needs_dev else Path()
    venvs = (
        [(py, ensure_venv(py, DEV_EXTRAS if py == quick else "dev")) for py in pythons]
        if "tests" in wanted
        else []
    )
    heavy = quick if args.heavy else None
    # The heavy measures time and memory: one process then.
    workers = 1 if args.heavy else int(tcfg.get("workers", 1))
    runs = {
        "lint": lambda: check_lint(dev),
        "vocab": lambda: check_vocab(cfg, dev, fresh),
        "js": lambda: check_js(dev),
        "tests": lambda: check_tests(
            venvs, int(tcfg.get("jobs", 2)), heavy, workers, tcfg.get("dist", "loadscope")
        ),
        "reference": lambda: check_reference(args.full, fresh),
        "docs": lambda: check_docs(dev),
        "browser": lambda: check_browser(
            dev, args.quick, int(cfg.get("browser", {}).get("workers", 1))
        ),
    }
    done: dict[str, Result] = {}
    together = [n for n in wanted if n != "browser"]
    with ThreadPoolExecutor(max_workers=max(1, len(together))) as pool:
        futures = {n: pool.submit(runs[n]) for n in together}
        for name, future in futures.items():
            done[name] = future.result()
    if "browser" in wanted:  # alone: its budgets measure time
        done["browser"] = runs["browser"]()
    results = [done[n] for n in wanted]

    width = max(len(r.name) for r in results)
    for r in results:
        print(f"{r.status.upper():4}  {r.name:<{width}}  {r.seconds:6.1f}s  {r.detail}")
    failed = [r.name for r in results if r.status == "fail"]
    print("check: " + ("FAILED: " + ", ".join(failed) if failed else "all passed"))
    print(f"logs: {LOGS.relative_to(ROOT)}/")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
