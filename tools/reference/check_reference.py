# SPDX-License-Identifier: MIT
"""One-shot numeric reference check: generate, run, compare with both stored runs.

Usage::

    python tools/reference/check_reference.py              # size S (and the merge)
    python tools/reference/check_reference.py --size L
    python tools/reference/check_reference.py --via-project    # the same through a project build
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

``--via-project`` writes each world as a cartolex project too and runs the
engine through the project build (``run.py --project``): the result must be
identical to the stored baseline in every artifact (no tolerance), and its
differences with the stored reference explained, as for the workspace run.

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


def demo_world(python: Path, size: str, seed: int, *, project: bool = False) -> tuple[Path, Path]:
    """Generate the demo world *size*/*seed* afresh; return its workspace and its truth file.

    With *project*, the world is also written as a cartolex project in ``OUT/project``.
    """
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
    if project:
        script = (
            "import sys\n"
            "from cartolex.demo import generate\n"
            "from cartolex.demo.project import write_project\n"
            "write_project(generate(sys.argv[1], int(sys.argv[2])), sys.argv[3]).close()\n"
        )
        subprocess.run(
            [str(python), "-c", script, size, str(seed), str(out / "project")],
            check=True,
            cwd=WORK,
            env=envs.fixed_env(),
            stdout=subprocess.DEVNULL,
        )
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


def identical(python: Path, reference: Path, current: Path, name: str) -> tuple[bool, str]:
    """Whether every artifact of *current* is identical to *reference* (no ledger, no tolerance)."""
    report = WORK / "reports" / f"{name}.json"
    proc = subprocess.run(
        [
            str(python),
            str(HERE / "compare.py"),
            "--reference",
            str(reference),
            "--current",
            str(current),
            "--json",
            str(report),
        ],
        cwd=WORK,
        env=envs.fixed_env(),
        capture_output=True,
        text=True,
    )
    if not report.is_file():
        return False, f"not comparable ({proc.stdout.strip().splitlines()[-1:]})"
    data = json.loads(report.read_text(encoding="utf-8"))
    stages = data["stages"]
    odd = [
        f"{s['stage']}/{a['name']}"
        for s in stages
        for a in s["artifacts"]
        if a["verdict"] != "identical"
    ]
    if not data["comparable"] or odd:
        return False, "not identical: " + (", ".join(odd[:8]) or "not comparable")
    return True, f"{len(stages)} stage{'' if len(stages) == 1 else 's'}: all identical"


def compare(
    python: Path,
    reference: Path,
    current: Path,
    name: str,
    *,
    ledger: bool = True,
    kind: str = "reference",
    report_name: str | None = None,
) -> tuple[int, str]:
    """Run ``compare.py``; print its table; return its exit status and one-line summary.

    With *ledger*, recorded differences pass as *explained*; the baseline
    comparison uses none. *name* is the stored run's name (in the ledger);
    *report_name* names the report (default: *name*).
    """
    suffix = "" if kind == "reference" else f".{kind}"
    report = WORK / "reports" / f"{report_name or name}{suffix}.md"
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
        "--via-project",
        action="store_true",
        help="write the worlds as projects and run the engine through the project build",
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
    if args.via_project and (args.regenerate or args.update_baseline):
        parser.error("--via-project checks this tree; it writes no stored run")
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
        run_name = f"{name}-project" if args.via_project else name
        target = STORED / name if args.regenerate else WORK / "runs" / run_name
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
        made = [
            demo_world(demo_python, size, seed, project=args.via_project) for size, seed in worlds
        ]
        timings = WORK / "runs" / f"{run_name}.timings.json"
        timings.parent.mkdir(parents=True, exist_ok=True)
        extra: list[str] = ["--work-root", str(WORK / "work"), "--timings", str(timings)]
        if args.via_project:
            extra += ["--project", str(made[0][0].parent / "project")]
        if len(made) == 2:
            extra += ["--merge-with", str(made[1][0])]
            if made[1][1] is not None:
                extra += ["--merge-truth", str(made[1][1])]
            if args.via_project:
                extra += ["--merge-project", str(made[1][0].parent / "project")]
        log = WORK / "logs" / f"{run_name}.log"
        seconds = run_engine(python, runner, target, made[0][0], made[0][1], *extra, log=log)
        peak = json.loads(timings.read_text(encoding="utf-8"))["peak_rss_mb"]
        how = " through a project" if args.via_project else ""
        print(f"{name}: run{how} in {seconds:.1f}s, peak {peak} MB ({env_name})")
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
        elif args.via_project:
            same, line_b = identical(python, baseline, target, f"{run_name}.baseline")
            status = max(status, 0 if same else 1)
            summaries.append(f"{run_name} baseline: {line_b}")
        else:
            rc_b, line_b = compare(python, baseline, target, name, ledger=False, kind="baseline")
            status = max(status, rc_b)
            summaries.append(f"{name} baseline: {line_b}")
        rc, line = compare(python, STORED / name, target, name, report_name=run_name)
        status = max(status, rc)
        summaries.append(f"{run_name} reference: {line}")
    if updated:
        log_baseline(list(updated), reason, updated, environment)
    # The last line is the one-line summary the check runner shows.
    print("; ".join(summaries))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
