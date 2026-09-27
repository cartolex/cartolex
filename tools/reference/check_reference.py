# SPDX-License-Identifier: MIT
"""One-shot numeric reference check: generate, run, compare with both stored runs.

Usage::

    python tools/reference/check_reference.py              # size S (and the merge)
    python tools/reference/check_reference.py --size L
    python tools/reference/check_reference.py --size S --update-baseline --reason "why"
    python tools/reference/check_reference.py --size S --regenerate --runner RUNNER.py

A check (the default) ensures the ``current`` environment (``envs.py``),
generates the demo world(s) of the size with the demo generator, runs
``run.py`` on them in that environment and compares the result twice:

- with the stored **baseline** in ``tests/baseline/<S|L|merge>``, made by this
  tree: every stage must be identical or within tolerance, with no ledger —
  this catches any accidental drift;
- with the stored **reference** in ``tests/reference/<S|L|merge>``, made by
  the released engine: every difference must be explained in
  ``tools/reference/explained.toml``.

The exit status is non-zero when either comparison fails (or the runs cannot
be compared). Size ``S`` also checks the merge of two ``S`` worlds (seeds 0
and 1).

``--update-baseline --reason TEXT`` rewrites the stored baseline from this
tree's run, after comparing it with the baseline it replaces: the reason goes
into the baseline's manifest and, with the comparison's summary, into
``tests/baseline/LOG.md``. It is the only way the baseline changes.

``--regenerate`` runs the same worlds in the ``released`` environment (the
released engine) with the runner given by ``--runner`` — the runner frozen with
the release that produced the reference, since ``run.py`` in this tree drives
this tree's engine only — and writes the stored reference; see
``docs/dev/reference.md`` for when that is legitimate.

Stdlib only at the top level: the numeric work happens in the pinned
environments. Working files go to ``.cache/reference/``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envs  # noqa: E402

ROOT = envs.ROOT
HERE = ROOT / "tools" / "reference"
STORED = ROOT / "tests" / "reference"
#: The stored run of this tree (the drift baseline) and its update log.
BASELINE = ROOT / "tests" / "baseline"
BASELINE_LOG = BASELINE / "LOG.md"
WORK = envs.CACHE
#: Demo worlds per reference: (size, seed) of the main world, and of the merge partner.
WORLDS = {"S": ("S", 0), "L": ("L", 0)}
MERGE = (("S", 0), ("S", 1))


def _python(env_name: str) -> Path:
    return envs.venv_python(envs.ensure_env(env_name))


def demo_world(python: Path, size: str, seed: int) -> tuple[Path, Path]:
    """Generate the demo world *size*/*seed* afresh; return its workspace and its truth file."""
    out = WORK / "worlds" / f"{size}-seed{seed}"
    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(python),
        "-m",
        "cartolex.demo",
        "create",
        "--size",
        size,
        "--seed",
        str(seed),
        "--out",
        str(out),
        "--corpus",
    ]
    subprocess.run(cmd, check=True, cwd=WORK, env=envs.fixed_env(), stdout=subprocess.DEVNULL)
    # The neutral world is in OUT (truth.json there), the corpus contract in OUT/workspace.
    return out / "workspace", out / "truth.json"


def run_engine(
    python: Path,
    runner: Path,
    out: Path,
    workspace: Path,
    truth: Path | None,
    *extra: str,
    log: Path,
) -> float:
    """Run the *runner* script in the environment of *python*; return seconds.

    The run's output is kept in *log*: nothing of it reaches this script's own
    output, so the last line printed stays the one-line summary the check
    runner shows.
    """
    cmd = [str(python), str(runner), "--workspace", str(workspace), "--out", str(out)]
    if truth is not None:
        cmd += ["--truth", str(truth)]
    cmd += list(extra)
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with log.open("w", encoding="utf-8") as handle:
        proc = subprocess.run(
            cmd, cwd=WORK, env=envs.fixed_env(), stdout=handle, stderr=subprocess.STDOUT
        )
    if proc.returncode != 0:
        tail = log.read_text(encoding="utf-8").strip().splitlines()[-15:]
        print("\n".join(tail))
        raise SystemExit(f"reference: the run failed (full log: {log})")
    return time.monotonic() - t0


#: Differences recorded with their reason; they pass as *explained*.
EXPLAINED = HERE / "explained.toml"


def compare(
    python: Path,
    reference: Path,
    current: Path,
    name: str,
    *,
    ledger: bool = True,
    kind: str = "reference",
) -> tuple[int, str]:
    """Run ``compare.py``; print its table; return its exit status and one-line summary.

    With *ledger*, recorded differences pass as *explained*; the baseline
    comparison uses none.
    """
    suffix = "" if kind == "reference" else f".{kind}"
    report = WORK / "reports" / f"{name}{suffix}.md"
    proc = subprocess.run(
        [
            str(python),
            str(HERE / "compare.py"),
            "--reference",
            str(reference),
            "--current",
            str(current),
            "--report",
            str(report),
            "--title",
            f"{kind.capitalize()} comparison — {name}",
            *(
                ["--explained", str(EXPLAINED), "--name", name]
                if ledger and EXPLAINED.is_file()
                else []
            ),
        ],
        cwd=WORK,
        env=envs.fixed_env(),
        capture_output=True,
        text=True,
    )
    sys.stdout.write(proc.stderr)
    sys.stdout.write(proc.stdout)
    summary = report.read_text(encoding="utf-8").strip().splitlines()[-1].strip("*")
    return proc.returncode, summary


def _jobs(size: str) -> list[tuple[str, list[tuple[str, int]]]]:
    jobs = [(size, [WORLDS[size]])]
    if size == "S":
        jobs.append(("merge", list(MERGE)))
    return jobs


def _check_reason(reason: str | None) -> str:
    text = " ".join((reason or "").split())
    if len(text.split()) < 3:
        raise SystemExit(
            "reference: --update-baseline needs --reason with a sentence saying why "
            "the baseline changes"
        )
    return text


def write_baseline(run: Path, name: str, reason: str) -> Path:
    """Replace ``tests/baseline/<name>`` with *run*, recording *reason* in its manifest."""
    target = BASELINE / name
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(run, target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["baseline"] = {"reason": reason, "date": time.strftime("%Y-%m-%d", time.gmtime())}
    manifest_path.write_text(
        json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target


def log_baseline(names: list[str], reason: str, comparisons: dict[str, str], env: dict) -> None:
    """Append one entry to ``tests/baseline/LOG.md``."""
    if not BASELINE_LOG.exists():
        BASELINE_LOG.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_LOG.write_text(
            "# Baseline updates\n\n"
            "Each entry says when the stored baseline (`tests/baseline/`) was rewritten, "
            "why, and how the new run compared with the one it replaced "
            "(`tools/reference/check_reference.py --update-baseline`).\n",
            encoding="utf-8",
        )
    date = time.strftime("%Y-%m-%d", time.gmtime())
    lines = [
        "",
        f"## {date} — {', '.join(names)}",
        "",
        f"- Reason: {reason}",
        f"- Engine: {env.get('engine_version')}, source fingerprint "
        f"`{str(env.get('engine_fingerprint', ''))[:16]}`",
    ]
    for name in names:
        lines.append(f"- {name}: {comparisons[name]}")
    with BASELINE_LOG.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Check the engine against the stored runs.")
    parser.add_argument("--size", choices=sorted(WORLDS), default="S", help="demo world size")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="rewrite the stored baseline from this tree's run (needs --reason)",
    )
    parser.add_argument("--reason", help="why the baseline changes (with --update-baseline)")
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="run in the released environment and rewrite the stored reference",
    )
    parser.add_argument(
        "--runner",
        type=Path,
        help=(
            "runner script for --regenerate: the runner frozen with the release that "
            "produced the reference (required with --regenerate)"
        ),
    )
    args = parser.parse_args(argv)
    if args.regenerate and args.runner is None:
        parser.error(
            "--regenerate needs --runner PATH: the runner frozen with the release that "
            "produced the reference (this tree's run.py drives this tree's engine only)"
        )
    if args.runner is not None and not args.regenerate:
        parser.error("--runner is only used with --regenerate")
    if args.regenerate and args.update_baseline:
        parser.error("--update-baseline and --regenerate write different stores: one at a time")
    if args.reason is not None and not args.update_baseline:
        parser.error("--reason is only used with --update-baseline")
    reason = _check_reason(args.reason) if args.update_baseline else ""
    runner = args.runner.resolve() if args.runner is not None else HERE / "run.py"
    if not runner.is_file():
        parser.error(f"runner not found: {runner}")

    WORK.mkdir(parents=True, exist_ok=True)
    env_name = "released" if args.regenerate else "current"
    python = _python(env_name)
    # The demo generator is part of this tree: worlds are always made with it.
    demo_python = _python("current")
    status = 0
    summaries = []
    updated: dict[str, str] = {}
    environment: dict = {}
    for name, worlds in _jobs(args.size):
        target = STORED / name if args.regenerate else WORK / "runs" / name
        if not args.regenerate and not (STORED / name / "manifest.json").is_file():
            print(f"{name}: no stored reference in {STORED / name}")
            summaries.append("no stored reference" if name == args.size else f"{name}: none stored")
            status = max(status, 1)
            continue
        baseline = BASELINE / name
        if (
            not (args.regenerate or args.update_baseline)
            and not (baseline / "manifest.json").is_file()
        ):
            print(
                f"{name}: no stored baseline in {baseline}: make one with "
                "--update-baseline --reason ..."
            )
            summaries.append(f"{name}: no stored baseline")
            status = max(status, 1)
            continue
        made = [demo_world(demo_python, size, seed) for size, seed in worlds]
        timings = WORK / "runs" / f"{name}.timings.json"
        timings.parent.mkdir(parents=True, exist_ok=True)
        extra: list[str] = ["--work-root", str(WORK / "work"), "--timings", str(timings)]
        if len(made) == 2:
            extra += ["--merge-with", str(made[1][0])]
            if made[1][1] is not None:
                extra += ["--merge-truth", str(made[1][1])]
        log = WORK / "logs" / f"{name}.log"
        seconds = run_engine(python, runner, target, made[0][0], made[0][1], *extra, log=log)
        peak = json.loads(timings.read_text(encoding="utf-8"))["peak_rss_mb"]
        print(f"{name}: run in {seconds:.1f}s, peak {peak} MB ({env_name})")
        if args.regenerate:
            summaries.append(f"{name}: reference written")
            continue
        if args.update_baseline:
            if (baseline / "manifest.json").is_file():
                _, line_b = compare(python, baseline, target, name, ledger=False, kind="baseline")
                before = f"against the previous baseline, {line_b}"
            else:
                before = "the first baseline"
            write_baseline(target, name, reason)
            environment = json.loads((target / "manifest.json").read_text(encoding="utf-8"))[
                "environment"
            ]
            updated[name] = before
            summaries.append(f"{name}: baseline written")
        else:
            rc_b, line_b = compare(python, baseline, target, name, ledger=False, kind="baseline")
            status = max(status, rc_b)
            summaries.append(f"{name} baseline: {line_b}")
        rc, line = compare(python, STORED / name, target, name)
        status = max(status, rc)
        summaries.append(f"{name} reference: {line}")
    if updated:
        log_baseline(list(updated), reason, updated, environment)
    # The last line is the one-line summary the check runner shows.
    print("; ".join(summaries))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
