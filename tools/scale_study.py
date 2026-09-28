# SPDX-License-Identifier: MIT
"""Measure every stage of a build on a streamed synthetic world.

Usage::

    python tools/scale_study.py world --people 10000 --out DIR [--seed 0] [--workers 4]
    python tools/scale_study.py build DIR --results FILE.jsonl [--stages S …] [--label L]
    python tools/scale_study.py table FILE.jsonl …

``world`` writes a project with a streamed world of that many mapped people
(:mod:`cartolex.demo.scale`), the year pinned to 2026 (``--led-works`` and
``--parts`` make a lighter world: works per person, text parts read). ``build`` runs the project's stages one at a time,
each in a fresh process (``cartolex build DIR --only STAGE``, so the earlier
stages are kept), and appends one JSON line per stage to *FILE*: the stage's
own measures from its ``run.json`` (wall seconds, peak memory, counts), the
process's whole peak memory, the disk the project uses afterwards, and the exit
status. A stage that fails or is killed (a memory cap) is recorded with its exit
status and the last lines of its output, and the build stops there. Run it
under a memory-capped runner. ``table`` prints the measures as a Markdown table.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STAGES = (
    "corpus.assemble",
    "keywords.extract",
    "keywords.build",
    "themes.space",
    "themes.group",
    "themes.apply",
    "map.layout",
    "map.trajectories",
    "overlays.position",
)


def _du_mb(path: Path) -> float:
    total = 0
    for dirpath, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_blocks * 512
            except OSError:
                pass
    return round(total / 2**20, 1)


def cmd_world(args: argparse.Namespace) -> int:
    from cartolex.demo.scale import write_scale_project

    t0 = time.perf_counter()

    def progress(phase: str, done: int, total: int) -> None:
        print(f"[{time.perf_counter() - t0:7.0f}s] {phase}: {done}/{total}", flush=True)

    summary = write_scale_project(
        args.out,
        args.people,
        seed=args.seed,
        workers=args.workers,
        led_works=tuple(args.led_works) if args.led_works else None,
        progress=progress,
    )
    settings = ["pinned_year=2026"]
    if args.parts:
        settings.append("corpus.assemble.parts=" + json.dumps(args.parts))
    for setting in settings:
        subprocess.run(
            [sys.executable, "-m", "cartolex.cli", "params", str(args.out), "--set", setting],
            check=True,
            capture_output=True,
        )
    info = {
        **summary.as_dict(),
        "seed": args.seed,
        "seconds": round(time.perf_counter() - t0, 1),
        "peak_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "disk_mb": _du_mb(Path(args.out)),
    }
    print(json.dumps(info))
    return 0


def _record(folder: Path, stage: str) -> dict:
    path = folder / "derived" / stage / "run.json"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    measures = doc.get("measures") or {}
    return {
        "run_id": doc.get("run_id"),
        "seconds": measures.get("seconds"),
        "peak_mb": measures.get("peak_memory_mb"),
        "counts": measures.get("counts") or {},
        "parameters": {k: v.get("value") for k, v in (doc.get("parameters") or {}).items()},
    }


def cmd_build(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    results = Path(args.results)
    results.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]),
    }
    status = 0
    for stage in args.stages or STAGES:
        before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        t0 = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-m", "cartolex.cli", "build", str(folder), "--only", stage, "--yes"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        wall = time.perf_counter() - t0
        after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        line = {
            "label": args.label,
            "stage": stage,
            "exit": proc.returncode,
            "wall_s": round(wall, 1),
            # ru_maxrss of the children is a running maximum: exact when it grew.
            "process_peak_mb": round(after / 1024, 1) if after > before else None,
            "disk_mb": _du_mb(folder),
            **_record(folder, stage),
        }
        if proc.returncode != 0:
            line["tail"] = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
        with results.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
        print(json.dumps({k: v for k, v in line.items() if k != "parameters"}), flush=True)
        if proc.returncode != 0:
            status = proc.returncode
            break
    return 0 if status == 0 else 1


def cmd_table(args: argparse.Namespace) -> int:
    rows = []
    for name in args.files:
        for line in Path(name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    print("| world | stage | exit | seconds | stage peak MB | process peak MB | disk MB |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for r in rows:
        print(
            f"| {r.get('label', '')} | {r['stage']} | {r['exit']} | {r.get('seconds') or r['wall_s']} "
            f"| {r.get('peak_mb')} | {r.get('process_peak_mb')} | {r.get('disk_mb')} |"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("world", help="write a streamed synthetic world as a project")
    w.add_argument("--people", type=int, required=True)
    w.add_argument("--seed", type=int, default=0)
    w.add_argument("--workers", type=int, default=1)
    w.add_argument("--out", type=Path, required=True)
    w.add_argument("--led-works", type=int, nargs=2, metavar=("LOW", "HIGH"))
    w.add_argument("--parts", nargs="+", help="the text parts the lexicon reads")
    w.set_defaults(run=cmd_world)
    b = sub.add_parser("build", help="run and measure each stage in a fresh process")
    b.add_argument("folder", type=Path)
    b.add_argument("--results", type=Path, required=True)
    b.add_argument("--stages", nargs="+", choices=STAGES)
    b.add_argument("--label", default="")
    b.set_defaults(run=cmd_build)
    t = sub.add_parser("table", help="print measures as a Markdown table")
    t.add_argument("files", nargs="+")
    t.set_defaults(run=cmd_table)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
