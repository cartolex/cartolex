# SPDX-License-Identifier: MIT
"""Command line of the demo world.

::

    python -m cartolex.demo create --size S --seed 0 --out DIR [--corpus]
    python -m cartolex.demo services --size S --seed 0 [--port 8765] [--people-list FILE] [--list-only]
    python -m cartolex.demo scale --people 100000 --seed 0 --out DIR [--workers 4]
"""

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
    services = sub.add_parser(
        "services", help="serve the demo bibliographic services on this computer (until Ctrl-C)"
    )
    services.add_argument("--size", default="S", type=str.upper, choices=sorted(SIZES))
    services.add_argument("--seed", default=0, type=int, help="the world's seed (default 0)")
    services.add_argument("--languages", default="en,fr", help="en,fr (default) or en,fr,pt")
    services.add_argument(
        "--layer-seed", default=0, type=int, help="the bibliographic layer's seed (default 0)"
    )
    services.add_argument("--port", default=0, type=int, help="port (default: a free one)")
    services.add_argument(
        "--people-list", type=Path, help="also write the world's people as a list to import"
    )
    services.add_argument(
        "--list-only", action="store_true", help="write the list and exit without serving"
    )
    scale = sub.add_parser(
        "scale", help="write a large world (10⁴ to 10⁶ people) straight into a new project"
    )
    scale.add_argument("--people", required=True, type=int, help="mapped people")
    scale.add_argument("--seed", default=0, type=int, help="random seed (default 0)")
    scale.add_argument("--out", required=True, type=Path, help="the project folder (new)")
    scale.add_argument("--languages", default="en,fr", help="en,fr (default) or en,fr,pt")
    scale.add_argument(
        "--workers", default=1, type=int, help="processes composing the texts (default 1)"
    )
    args = parser.parse_args(argv)
    if args.command == "services":
        return _services(args)
    if args.command == "scale":
        return _scale(args)

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


def _scale(args: argparse.Namespace) -> int:
    from .scale import write_scale_project

    started = time.perf_counter()
    try:
        summary = write_scale_project(
            args.out, args.people, seed=args.seed, languages=args.languages, workers=args.workers
        )
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"scale world {args.people}/{args.seed}: {summary.mapped} mapped people, "
        f"{summary.applicants} applicants, {summary.groups} groups, {summary.texts} texts, "
        f"{summary.characters} characters -> {args.out}"
    )
    print(f"done in {time.perf_counter() - started:.1f}s")
    return 0


def write_people_list(rows: list[dict[str, str]], path: Path) -> None:
    """Write an import list (CSV, UTF-8) of the demo world's people."""
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _services(args: argparse.Namespace, stop: object = None) -> int:
    import threading

    from .services import DemoServices

    try:
        world = generate(size=args.size, seed=args.seed, languages=args.languages)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    services = DemoServices(world, layer_seed=args.layer_seed, port=args.port)
    if args.people_list:
        write_people_list(services.bibliography.people_rows(), args.people_list)
        print(f"people list: {len(world.people)} people -> {args.people_list}")
    if args.list_only:
        return 0
    event = stop if isinstance(stop, threading.Event) else threading.Event()
    with services:
        print(f"demo services of world {args.size}/{args.seed} at {services.base_url}")
        for name, url in services.endpoints().items():
            print(f"  {name:<10} {url}")
        print("collect with: cartolex collect … --services " + services.base_url, flush=True)
        try:
            while not event.wait(0.5):
                pass
        except KeyboardInterrupt:
            print("stopped")
    return 0
