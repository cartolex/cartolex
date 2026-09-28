# SPDX-License-Identifier: MIT
"""The run of a million people: what it will cost, and the script that runs it.

Usage::

    python tools/million.py plan [--people 1000000] [--keywords 10000]
    python tools/million.py run --out DIR [--people 1000000] [--workers 16] [--results FILE.jsonl]

``plan`` prints, for a streamed world of that many mapped people, the sizes the
build will see (texts, characters, mapped units, kept keywords, from the
averages of the measured streamed worlds), each stage's expected time and peak
memory from the cost models of ``cartolex.build.STAGES`` (fitted on measured
builds of 10³ to 10⁵ people, ``docs/sizes.md``), the disk the project takes, and
the machine it needs.

``run`` writes the world with *workers* processes composing the texts, then
runs each stage in a fresh process (``tools/scale_study.py build``), recording
its time, peak memory, counts and the disk used; the parse cache is removed
after the extraction (a cache, several gigabytes at this size). Run it under a
memory cap on the machine ``plan`` names.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Averages of the streamed worlds (measured at 10³, 10⁴ and 10⁵ mapped people).
MAPPED_SHARE = 0.97  # mapped people with at least one text in the fit slot
TEXTS_PER_PERSON = 6.3  # texts in the fit slot per such person (their own and co-authored)
CHARACTERS_PER_TEXT = 1334  # title and abstract
#: Disk per unit, in MB (measured): the corpus's text files, the parse cache, the rest.
DISK_MB_PER_TEXT = 0.0044
PARSE_MB_PER_TEXT = 0.0019
OTHER_MB_PER_PERSON = 0.06


def sizes(people: int, keywords: int) -> dict[str, int]:
    """The sizes a build of *people* mapped people sees."""
    texts = round(people * MAPPED_SHARE * TEXTS_PER_PERSON)
    return {
        "people": round(people * MAPPED_SHARE),
        "texts": texts,
        "characters": round(texts * CHARACTERS_PER_TEXT),
        "kept_keywords": int(keywords),
        "mapped_units": round(people * MAPPED_SHARE),
    }


def _fmt_s(s: float | None) -> str:
    if s is None:
        return "?"
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


def cmd_plan(args: argparse.Namespace) -> int:
    from cartolex.build import STAGES
    from cartolex.build.params import ProjectSizes

    size = sizes(args.people, args.keywords)
    print(f"sizes: {size}")
    print("| stage | driver | time | peak memory |")
    print("| --- | --- | ---: | ---: |")
    total = 0.0
    peak = 0.0
    for stage in STAGES:
        if stage.id == "keywords.triage":
            continue
        est = stage.estimate(ProjectSizes(**size), None)
        total += est.seconds or 0.0
        peak = max(peak, est.peak_memory_mb or 0.0)
        driver = stage.cost.driver if stage.cost else "?"
        mem = f"{(est.peak_memory_mb or 0) / 1024:.1f} GB"
        print(f"| {stage.id} | {driver} = {size.get(driver)} | {_fmt_s(est.seconds)} | {mem} |")
    disk = (
        size["texts"] * (DISK_MB_PER_TEXT + PARSE_MB_PER_TEXT) + args.people * OTHER_MB_PER_PERSON
    ) / 1024
    ram = max(32.0, 1.5 * peak / 1024)
    print(f"total: {_fmt_s(total)}; largest stage peak: {peak / 1024:.1f} GB; disk: {disk:.0f} GB")
    print(
        f"machine: {ram:.0f} GB of memory or more, {1.5 * disk:.0f} GB of free disk on a file "
        f"system with {size['texts'] * 1.2 / 1e6:.0f} million free inodes (one file per text), "
        "8 cores or more (the numeric steps use every core; the world is written in parallel)"
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    results = Path(args.results or out.with_suffix(".jsonl"))
    study = [sys.executable, str(ROOT / "tools" / "scale_study.py")]
    cmd_plan(args)
    subprocess.run(
        [
            *study,
            "world",
            "--people",
            str(args.people),
            "--out",
            str(out),
            "--workers",
            str(args.workers),
        ],
        check=True,
    )
    first = ["corpus.assemble", "keywords.extract"]
    rest = [
        "keywords.build",
        "themes.space",
        "themes.group",
        "themes.apply",
        "map.layout",
        "map.trajectories",
        "overlays.position",
    ]
    for stages in (first, rest):
        done = subprocess.run(
            [
                *study,
                "build",
                str(out),
                "--results",
                str(results),
                "--label",
                "million",
                "--stages",
                *stages,
            ]
        )
        if stages is first:
            shutil.rmtree(out / "cache" / "parse", ignore_errors=True)
        if done.returncode != 0:
            return done.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, run in (("plan", cmd_plan), ("run", cmd_run)):
        p = sub.add_parser(name)
        p.add_argument("--people", type=int, default=1_000_000)
        p.add_argument("--keywords", type=int, default=10_000, help="kept keywords (the ceiling)")
        if name == "run":
            p.add_argument("--out", required=True)
            p.add_argument("--workers", type=int, default=16)
            p.add_argument("--results")
        p.set_defaults(run=run)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
