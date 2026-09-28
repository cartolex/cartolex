# SPDX-License-Identifier: MIT
"""Command line: ``python -m cartolex.demo create --size S --seed 0 --out DIR [--corpus]``."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .generator import generate
from .model import SIZES


def main(argv: list[str] | None = None) -> int:
    """Entry point of ``python -m cartolex.demo``; returns the exit status."""
    parser = argparse.ArgumentParser(
        prog="python -m cartolex.demo",
        description="Generate the synthetic demo world (an invented research community).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="write a demo world to a folder")
    create.add_argument(
        "--size", default="S", type=str.upper, choices=sorted(SIZES), help="world size (default S)"
    )
    create.add_argument("--seed", default=0, type=int, help="random seed (default 0)")
    create.add_argument("--out", required=True, type=Path, help="output folder")
    create.add_argument(
        "--corpus",
        action="store_true",
        help="also write the engine's corpus contract in OUT/workspace",
    )
    create.add_argument("--force", action="store_true", help="replace a demo world already in OUT")
    create.add_argument(
        "--bodies",
        action="store_true",
        help="also write a long body for every work (full texts)",
    )
    create.add_argument(
        "--languages",
        default="en,fr",
        help="languages of the texts: en,fr (default) or en,fr,pt",
    )
    args = parser.parse_args(argv)

    started = time.perf_counter()
    try:
        world = generate(
            size=args.size, seed=args.seed, languages=args.languages, bodies=args.bodies
        )
    except ValueError as exc:
        parser.error(str(exc))
    try:
        manifest = world.write(args.out, overwrite=args.force)
        corpus = (
            world.write_corpus(args.out / "workspace", overwrite=args.force)
            if args.corpus
            else None
        )
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    counts = manifest["counts"]
    print(
        f"demo world {args.size}/{args.seed}: {counts['cohort']} people in the cohort, "
        f"{counts['applicants']} in projected sets, {counts['groups']} groups, "
        f"{counts['works']} works ({counts['works_fr']} in French"
        + (f", {counts['works_pt']} in Portuguese" if "works_pt" in counts else "")
        + "), "
        f"{counts['words']} words -> {args.out}"
    )
    if corpus is not None:
        manual = corpus["manual"]
        print(
            f"corpus contract: {manual['rows']} index rows, {manual['texts']} texts, "
            f"{manual['people']} people -> {args.out / 'workspace'}"
        )
    print(f"done in {time.perf_counter() - started:.1f}s")
    return 0
