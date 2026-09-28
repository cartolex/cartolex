# SPDX-License-Identifier: MIT
"""The ``cartolex`` command.

::

    cartolex init FOLDER --name NAME --field TITLE [--description TEXT] [--languages en,fr]
    cartolex status FOLDER
    cartolex build FOLDER [--dry-run] [--only STAGE…] [--force STAGE…] [--yes]
    cartolex params FOLDER [--set STAGE.NAME=VALUE …]
    cartolex versions FOLDER [--pin ID | --try-another --seed N]
    cartolex project validate FOLDER
    cartolex project unlock FOLDER
    cartolex models list
    cartolex models add LANG… [--yes]
    cartolex collect people|folder|corpus|resolve|confirm|harvest|duplicates|merge FOLDER …
    cartolex demo create --size S --seed 0 --out DIR [--corpus]

Each verb prints what it did and exits with 0 on success, 1 when the project
refuses the action or a build fails (the message says why), 2 on a usage error,
130 when a build was cancelled. ``cartolex build`` reads the AI key from
``MISTRAL_API_KEY``, asks before a stage that reaches the network or costs money
(``--yes`` accepts), prints « phase k of n » at least every ten seconds, and
stops cleanly at the first Ctrl-C (the second one stops at once).
``cartolex collect`` is described in :mod:`cartolex.cli_collect`.
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


def _status(args: argparse.Namespace) -> int:
    from cartolex.build import status
    from cartolex.project import Project

    project = Project.open(args.folder)
    for st in status(project).values():
        print(st.describe())
    return 0


def _ask(request: object) -> bool:
    if not sys.stdin.isatty():
        print(f"refused (no terminal to ask; use --yes): {request}", file=sys.stderr)
        return False
    answer = input(f"{request}\nRun it? [y/N] ").strip().lower()
    return answer in ("y", "yes")


class _Printer:
    """Progress on the terminal: a line per phase, every 10 % and on each heartbeat."""

    def __init__(self) -> None:
        self.last: tuple[int, int] = (0, -1)

    def __call__(self, event: object) -> None:
        tenth = int(getattr(event, "stage_fraction", 0.0) * 10)
        key = (getattr(event, "phase", 0), tenth)
        if key != self.last or getattr(event, "heartbeat", False):
            self.last = key
            print(f"[{getattr(event, 'elapsed_s', 0.0):6.0f}s] {event}", flush=True)


def _build(args: argparse.Namespace) -> int:
    import os
    import signal
    import threading

    from cartolex.build import build, plan
    from cartolex.build.engine import AIAccess, engine_registry
    from cartolex.project import Project

    targets = args.only or None
    if args.dry_run:
        project = Project.open(args.folder)
        print(plan(project, targets, force=args.force or ()).describe())
        return 0
    project = Project.open(args.folder, write=True)
    for note in project.recovered:
        print(note)
    registry = engine_registry(AIAccess(api_key=os.environ.get("MISTRAL_API_KEY") or None))
    cancel = threading.Event()

    def on_interrupt(signum: int, frame: object) -> None:
        if cancel.is_set():
            raise KeyboardInterrupt
        print("stopping at the next safe point (Ctrl-C again to stop at once)", flush=True)
        cancel.set()

    previous = signal.signal(signal.SIGINT, on_interrupt)
    try:
        result = build(
            project,
            targets,
            registry=registry,
            force=args.force or (),
            consent=(lambda request: True) if args.yes else _ask,
            progress=_Printer(),
            cancel=cancel,
        )
    finally:
        signal.signal(signal.SIGINT, previous)
        project.close()
    print(result.summary())
    if result.outcome == "cancelled":
        return 130
    return 0 if result.outcome == "succeeded" and not result.refused else 1


def _value(text: str) -> object:
    import json

    try:
        return json.loads(text)
    except ValueError:
        return text


def _params(args: argparse.Namespace) -> int:
    from cartolex.build import STAGES
    from cartolex.build.params import ParamsError, check_params
    from cartolex.build.validity import effective_params
    from cartolex.project import Project

    project = Project.open(args.folder, write=bool(args.set))
    try:
        if args.set:
            params, fp = project.read_params()
            stages = {k: dict(v) for k, v in params.stages.items()}
            top: dict[str, object] = {}
            for item in args.set:
                key, sep, text = item.partition("=")
                if not sep:
                    raise ValueError(f"--set wants STAGE.NAME=VALUE or seed=N, not {item!r}")
                if key in ("seed", "pinned_year"):
                    top[key] = _value(text)
                    continue
                stage, _, name = key.rpartition(".")
                if not stage:
                    raise ValueError(f"--set wants STAGE.NAME=VALUE, not {item!r}")
                stages.setdefault(stage, {})[name] = _value(text)
            updated = params.model_validate(
                {**params.model_dump(mode="json", by_alias=True), **top, "stages": stages}
            )
            problems = check_params(updated, STAGES)
            if problems:
                raise ParamsError(problems)
            project.save_params(updated, expected=fp, action="set " + ", ".join(args.set))
            print(f"decisions/params.json: set {', '.join(args.set)}")
        for stage_id, resolved in effective_params(project, STAGES).items():
            stage = STAGES[stage_id]
            if not resolved.values:
                continue
            print(f"{stage.id} ({stage.name})")
            for name, pv in resolved.values.items():
                origin = f"rule {pv.rule}" if pv.source == "rule" else pv.source
                shown = (
                    "waits for " + ", ".join(resolved.unknown[name])
                    if name in resolved.unknown
                    else repr(pv.value)
                )
                print(f"  {name} = {shown}  ({origin})")
        return 0
    finally:
        project.close()


def _versions(args: argparse.Namespace) -> int:
    from cartolex.project import Project
    from cartolex.project.maps import pin, read_maps, save_maps, try_another

    changing = args.pin is not None or args.try_another
    project = Project.open(args.folder, write=changing)
    try:
        maps, fp = read_maps(project.layout)
        if args.pin is not None:
            maps = pin(maps, args.pin)
            save_maps(project.layout, maps, expected=fp, action=f"pin {args.pin}")
            print(f"pinned {args.pin}: the next build draws the map with it")
        elif args.try_another:
            maps, version = try_another(maps, seed=args.seed, note=args.note or "")
            save_maps(project.layout, maps, expected=fp, action=f"try {version}")
            print(f"added {version} (seed {args.seed}); pin it to use it")
        if not maps.versions:
            print("no map version yet: the first build adds and pins v1")
        for v in maps.versions:
            mark = "*" if v.id == maps.pinned else " "
            params = f" {v.layout.params}" if v.layout.params else ""
            print(
                f"{mark} {v.id}  {v.layout.method} seed {v.layout.seed}{params}  "
                f"shows {', '.join(v.shows)}  {v.created_at:%Y-%m-%d}  {v.note}".rstrip()
            )
        return 0
    finally:
        project.close()


def _demo(argv: list[str]) -> int:
    from cartolex.demo.cli import main as demo_main

    return demo_main(argv)


def _install_command(requirement: str) -> list[str] | None:
    """The command that installs *requirement* into this interpreter's environment, or None."""
    import importlib.util
    import shutil

    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install", "--no-deps", requirement]
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, "--no-deps", requirement]
    return None


def _models(args: argparse.Namespace) -> int:
    import subprocess

    from cartolex.lexicon import language_models as lm

    if args.action == "list":
        for lang in lm.supported_languages():
            model = lm.spec(lang)
            found = lm.installed_version(lang)
            state = (
                "installed"
                if found == model.version
                else (
                    f"version {found} installed, {model.version} needed"
                    if found
                    else "not installed"
                )
            )
            print(f"{lang}  {model.identity:<28} {model.licence:<14} {state}")
        return 0
    status = 0
    for lang in args.languages:
        model = lm.spec(lang)
        if lm.installed_version(lang) == model.version:
            print(f"{lang}: {model.identity} is already installed")
            continue
        print(
            f"{lang}: {model.identity}, licence {model.licence}, downloads {model.wheel} "
            f"from the spaCy models' releases on GitHub"
        )
        if not args.yes:
            if not sys.stdin.isatty():
                print("error: no terminal to ask on; pass --yes to accept", file=sys.stderr)
                return 1
            if input("install it? [y/N] ").strip().lower() not in ("y", "yes"):
                print(f"{lang}: not installed")
                status = 1
                continue
        command = _install_command(model.requirement)
        if command is None:
            print(f"error: no installer found; run: {model.pip_command}", file=sys.stderr)
            return 1
        if subprocess.run(command, check=False).returncode != 0:
            print(f"error: installing {model.identity} failed", file=sys.stderr)
            status = 1
            continue
        print(f"{lang}: {model.identity} installed")
    return status


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

    st = sub.add_parser("status", help="the state of every stage, with the reasons")
    st.add_argument("folder", type=Path)
    st.set_defaults(run=_status)

    bd = sub.add_parser("build", help="run what needs to run (--dry-run: say what would)")
    bd.add_argument("folder", type=Path)
    bd.add_argument("--dry-run", action="store_true", help="show the plan; change nothing")
    bd.add_argument("--only", nargs="+", metavar="STAGE", help="these stages and what they need")
    bd.add_argument("--force", nargs="+", metavar="STAGE", help="run these even if up to date")
    bd.add_argument("--yes", action="store_true", help="accept the stages that ask consent")
    bd.set_defaults(run=_build)

    pa = sub.add_parser("params", help="the effective parameters and where they come from")
    pa.add_argument("folder", type=Path)
    pa.add_argument(
        "--set",
        nargs="+",
        metavar="STAGE.NAME=VALUE",
        help="set parameters in decisions/params.json (values read as JSON), or seed=N",
    )
    pa.set_defaults(run=_params)

    ve = sub.add_parser("versions", help="the map versions; pin one or try another layout")
    ve.add_argument("folder", type=Path)
    group = ve.add_mutually_exclusive_group()
    group.add_argument("--pin", metavar="ID", help="pin this version")
    group.add_argument("--try-another", action="store_true", help="add a version, another seed")
    ve.add_argument("--seed", type=int, default=1, help="the seed of --try-another")
    ve.add_argument("--note", help="a note on the new version")
    ve.set_defaults(run=_versions)

    project = sub.add_parser("project", help="check or repair a project folder")
    psub = project.add_subparsers(dest="action", required=True)
    validate = psub.add_parser("validate", help="check every file against the format")
    validate.add_argument("folder", type=Path)
    validate.set_defaults(run=_validate)
    unlock = psub.add_parser("unlock", help="remove a lock whose process is gone")
    unlock.add_argument("folder", type=Path)
    unlock.set_defaults(run=_unlock)

    models = sub.add_parser("models", help="the language models extraction needs")
    msub = models.add_subparsers(dest="action", required=True)
    mlist = msub.add_parser(
        "list", help="each language's model, its licence, whether it is installed"
    )
    mlist.set_defaults(run=_models)
    madd = msub.add_parser("add", help="install the pinned model of one or more languages")
    madd.add_argument("languages", nargs="+", choices=("en", "fr", "pt"))
    madd.add_argument("--yes", action="store_true", help="install without asking")
    madd.set_defaults(run=_models)

    from cartolex.cli_collect import add_parser as add_collect

    add_collect(sub)

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
    except (
        FileExistsError,
        FileNotFoundError,
        PermissionError,
        ValueError,
        RuntimeError,
        KeyError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
