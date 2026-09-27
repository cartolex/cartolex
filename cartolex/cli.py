# SPDX-License-Identifier: MIT
"""The ``cartolex`` command.

::

    cartolex init FOLDER --name NAME --field TITLE [--description TEXT] [--languages en,fr]
    cartolex project validate FOLDER
    cartolex project unlock FOLDER
    cartolex demo create --size S --seed 0 --out DIR [--corpus]

Each verb prints what it did in one or two lines and exits with 0 on success,
1 when the project refuses the action (the message says why), 2 on a usage
error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

__all__ = ["main"]


def _languages(text: str) -> tuple[str, ...]:
    from cartolex.project.models import LANGUAGES

    langs = tuple(x.strip() for x in text.split(",") if x.strip())
    bad = [x for x in langs if x not in LANGUAGES]
    if bad or not langs:
        raise argparse.ArgumentTypeError(
            f"languages must be a comma-separated list of {', '.join(LANGUAGES)}"
        )
    return langs


def _init(args: argparse.Namespace) -> int:
    from cartolex.project import Project

    project = Project.init(
        args.folder,
        name=args.name,
        domain_title=args.field,
        domain_description=args.description,
        corpus_languages=args.languages,
        reference_language=args.reference,
    )
    project.close()
    print(f"created project {args.name!r} in {args.folder}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    from cartolex.project.validate import validate_project

    problems = validate_project(args.folder)
    for p in problems:
        print(p)
    if problems:
        print(f"{len(problems)} problem(s) in {args.folder}")
        return 1
    print(f"{args.folder}: valid")
    return 0


def _unlock(args: argparse.Namespace) -> int:
    from cartolex.project import ProjectLayout, remove_stale_lock

    held = remove_stale_lock(ProjectLayout(args.folder))
    who = f"{held.app} (process {held.pid}, since {held.since})" if held else "an unreadable lock"
    print(f"removed the stale lock of {who}")
    return 0


def _demo(argv: list[str]) -> int:
    from cartolex.demo.cli import main as demo_main

    return demo_main(argv)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cartolex", description="Map a research field from the texts of its people."
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    init = sub.add_parser("init", help="create a project in an empty folder")
    init.add_argument("folder", type=Path)
    init.add_argument("--name", required=True, help="the project's name")
    init.add_argument("--field", required=True, help="the field studied (its exact title)")
    init.add_argument(
        "--description",
        default="",
        help="two or three lines on what is in scope; the AI receives this and the terms, nothing else",
    )
    init.add_argument(
        "--languages", type=_languages, default=("en",), help="corpus languages, e.g. en,fr,pt"
    )
    init.add_argument(
        "--reference", default="en", choices=("en", "fr", "pt"), help="reference language"
    )
    init.set_defaults(run=_init)

    project = sub.add_parser("project", help="check or repair a project folder")
    psub = project.add_subparsers(dest="action", required=True)
    validate = psub.add_parser("validate", help="check every file against the format")
    validate.add_argument("folder", type=Path)
    validate.set_defaults(run=_validate)
    unlock = psub.add_parser("unlock", help="remove a lock whose process is gone")
    unlock.add_argument("folder", type=Path)
    unlock.set_defaults(run=_unlock)

    sub.add_parser("demo", help="generate the synthetic demo world", add_help=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point of the ``cartolex`` command; returns the exit status."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["demo"]:
        return _demo(argv[1:])
    args = _parser().parse_args(argv)
    try:
        return args.run(args)
    except (FileExistsError, FileNotFoundError, PermissionError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
