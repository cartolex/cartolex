# SPDX-License-Identifier: MIT
"""The ``cartolex`` command.

::

    cartolex                                   (the app: same as `cartolex app`)
    cartolex app [FOLDER] [--port N] [--no-browser] [--services demo [--world SIZE:SEED]]
    cartolex api [FOLDER] [--host H] [--port N] [--allowed-host NAME…] [--projects-root DIR]
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
130 when a build was cancelled. ``cartolex app`` starts the app on a free
loopback port and opens the browser with a launch link that works once;
``cartolex api`` serves it without a browser, for hosting. ``cartolex build``
and the app read the AI key from
``MISTRAL_API_KEY``, asks before a stage that reaches the network or costs money
(``--yes`` accepts), prints « phase k of n » at least every ten seconds, and
stops cleanly at the first Ctrl-C (the second one stops at once).
``cartolex collect`` is described in :mod:`cartolex.cli_collect`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cartolex.app.extensions import Extension

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
    from cartolex.build.engine import AIAccess, EngineOptions, engine_registry
    from cartolex.project import Project

    targets = args.only or None
    if args.dry_run:
        project = Project.open(args.folder)
        print(plan(project, targets, force=args.force or ()).describe())
        return 0
    project = Project.open(args.folder, write=True)
    for note in project.recovered:
        print(note)
    registry = engine_registry(
        AIAccess(api_key=os.environ.get("MISTRAL_API_KEY") or None),
        EngineOptions(rejects_folder=_data_dir(args) / "rejects"),
    )
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


def _data_dir(args: argparse.Namespace) -> Path:
    """The app's own folder on this computer (``--data-dir``, else the default one)."""
    from cartolex.app.server import default_data_dir

    return Path(args.data_dir) if getattr(args, "data_dir", None) else default_data_dir()


def _rejects(args: argparse.Namespace) -> int:
    """``cartolex rejects``: the machine's rejection cache, and a shipped list made from it."""
    import json

    from cartolex.lexicon.rejects import FORMAT, MachineRejects, export_list

    machine = MachineRejects(_data_dir(args) / "rejects")
    if args.action == "show":
        counts = machine.counts()
        print(f"{machine.folder}: " + (", ".join(f"{k} {v}" for k, v in counts.items()) or "empty"))
        return 0
    if args.action == "clear":
        print(f"{machine.clear(args.language)} terms removed from {machine.folder}")
        return 0
    folders = args.projects
    if not folders:  # the projects this computer's app opened lately
        recent = _data_dir(args) / "recent.json"
        doc = json.loads(recent.read_text(encoding="utf-8")) if recent.is_file() else {}
        folders = [Path(p["path"]) for p in doc.get("projects", []) if Path(p["path"]).is_dir()]
    names = _project_names(folders)
    lists = export_list(machine, min_projects=args.min_projects, names=names)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for lang, terms in lists.items():
        doc = {"format": FORMAT, "language": lang, "terms": terms}
        (out / f"{lang}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{out / f'{lang}.json'}: {len(terms)} terms")
    print("review every term before copying a list into cartolex/_data/rejects/")
    return 0


def _project_names(folders: Sequence[Path]) -> list[str]:
    """The people's and organisations' names of the projects in *folders*."""
    import pyarrow.parquet as pq

    from cartolex.project import Project

    names: list[str] = []
    for folder in folders:
        layout = Project.open(folder).layout
        for table, columns in (
            ("people", ("first_name", "last_name")),
            ("organisations", ("name", "acronym")),
        ):
            path = layout.table(table)
            if not path.exists():
                continue
            have = [c for c in columns if c in pq.read_schema(path).names]
            data = pq.read_table(path, columns=have).to_pydict()
            names += [str(v) for c in have for v in data[c] if v]
    return names


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
            maps, version = try_another(
                maps, seed=args.seed, method=args.method, note=args.note or ""
            )
            save_maps(project.layout, maps, expected=fp, action=f"try {version}")
            how = f"{args.method} layout, " if args.method else ""
            print(f"added {version} ({how}seed {args.seed}); pin it to use it")
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


def _collection(args: argparse.Namespace, stack: object) -> object:
    """The app's collection: the bibliographic services, or the demo services of a demo world
    (``--services demo``), started for as long as the app runs (*stack* stops them)."""
    import os

    from cartolex.app.collect_service import ServiceCollection
    from cartolex.collect.services import CollectSettings, local_settings

    contact = os.environ.get("CARTOLEX_CONTACT") or None
    key = os.environ.get("OPENALEX_API_KEY") or None
    keys = {"openalex": key} if key else {}
    services = getattr(args, "services", None)
    if services in (None, "real"):
        return ServiceCollection(
            CollectSettings(contact=contact, api_keys=keys, user_agent="cartolex")
        )
    from cartolex.demo.services import DemoServices, endpoints_at

    if services == "demo":
        from cartolex.demo import generate

        size, _, seed = (args.world or "S:0").partition(":")
        demo = stack.enter_context(  # type: ignore[attr-defined]
            DemoServices(generate(size.upper(), int(seed or 0)))
        )
        print(f"demo services of world {size.upper()}/{seed or 0} at {demo.base_url}")
        endpoints = demo.endpoints()
    else:
        endpoints = endpoints_at(services)
    return ServiceCollection(
        local_settings(endpoints, contact=contact, api_keys=keys),
        local=True,
        label="demo services (on this computer)",
    )


def _app_settings(args: argparse.Namespace, *, hosted: bool, stack: object = None) -> object:
    import os

    from cartolex.app import AppSettings
    from cartolex.app.server import default_data_dir
    from cartolex.build.engine import AIAccess

    key = os.environ.get("MISTRAL_API_KEY") or None
    names = [e.settings_dir_name for e in args.extensions if e.settings_dir_name]
    data_dir = args.data_dir or default_data_dir(names[0] if names else "cartolex")
    return AppSettings(
        mode="hosted" if hosted else "local",
        project=None if hosted or args.folder is None else args.folder,
        projects_root=getattr(args, "projects_root", None),
        data_dir=data_dir,
        allowed_hosts=tuple(getattr(args, "allowed_host", None) or ()),
        secure_cookies=bool(getattr(args, "secure_cookies", False)),
        ai_access=AIAccess(api_key=key) if key else None,
        collection=_collection(args, stack) if stack is not None else None,  # type: ignore[arg-type]
    )


def _app(args: argparse.Namespace) -> int:
    import contextlib

    from cartolex.app.server import serve

    with contextlib.ExitStack() as stack:
        settings = _app_settings(args, hosted=False, stack=stack)
        return serve(
            settings,  # type: ignore[arg-type]
            args.extensions,
            host="127.0.0.1",
            port=args.port,
            open_browser=not args.no_browser,
        )


def _api(args: argparse.Namespace) -> int:
    from cartolex.app.server import serve

    hosted = args.projects_root is not None
    if hosted and args.folder is not None:
        raise ValueError("give a project folder, or --projects-root for many projects, not both")
    import contextlib

    with contextlib.ExitStack() as stack:
        settings = _app_settings(args, hosted=hosted, stack=stack)
        return serve(
            settings,  # type: ignore[arg-type]
            args.extensions,
            host=args.host,
            port=args.port,
            open_browser=False,
        )


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


def _services_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--services",
        help="where collection goes: the bibliographic services (default), 'demo' (the demo "
        "services of --world, on this computer), or the URL of running demo services",
    )
    p.add_argument("--world", help="the demo world of --services demo, SIZE:SEED (default S:0)")


def _parser(extensions: Sequence[Extension] = ()) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cartolex", description="Map a research field from the texts of its people."
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    app = sub.add_parser("app", help="open the app in the browser (the default)")
    app.add_argument("folder", type=Path, nargs="?", help="the project to open")
    app.add_argument("--port", type=int, default=0, help="the port (default: a free one)")
    app.add_argument("--no-browser", action="store_true", help="print the link, open nothing")
    app.add_argument("--data-dir", type=Path, help="the app's own folder (recent projects)")
    _services_options(app)
    app.set_defaults(run=_app)

    api = sub.add_parser("api", help="serve the app without a browser (hosting)")
    api.add_argument("folder", type=Path, nargs="?", help="the project to serve")
    api.add_argument("--host", default="127.0.0.1", help="the address to listen on")
    api.add_argument("--port", type=int, default=8000, help="the port (0: a free one)")
    api.add_argument(
        "--allowed-host",
        action="append",
        metavar="NAME",
        help="a host name the app answers to (repeat for several); loopback names always work "
        "for one project",
    )
    api.add_argument(
        "--projects-root", type=Path, help="serve every project in this folder (hosted mode)"
    )
    api.add_argument("--secure-cookies", action="store_true", help="behind HTTPS: Secure cookies")
    api.add_argument("--data-dir", type=Path, help="the app's own folder")
    _services_options(api)
    api.set_defaults(run=_api)

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
    bd.add_argument("--data-dir", type=Path, help="the app's own folder, with the rejection cache")
    bd.set_defaults(run=_build)

    rj = sub.add_parser("rejects", help="the terms rejected automatically on this computer")
    rsub = rj.add_subparsers(dest="action", required=True)
    rshow = rsub.add_parser("show", help="how many terms the cache holds, per language")
    rclear = rsub.add_parser("clear", help="empty the cache (of one language)")
    rclear.add_argument("--language", help="only this language")
    rexport = rsub.add_parser(
        "export", help="a list to ship, from the cache: never answers seen in several projects"
    )
    rexport.add_argument("--min-projects", type=int, default=2, help="seen in this many projects")
    rexport.add_argument("--out", default="rejects-export", help="the folder to write into")
    rexport.add_argument(
        "--projects",
        nargs="+",
        type=Path,
        metavar="FOLDER",
        help="projects whose people's and organisations' names are left out (default: the "
        "projects the app opened lately)",
    )
    for p in (rshow, rclear, rexport):
        p.add_argument("--data-dir", type=Path, help="the app's own folder")
        p.set_defaults(run=_rejects)

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
    ve.add_argument(
        "--method",
        choices=("umap", "tsne", "tree"),
        help="the layout method of --try-another (default: the pinned version's)",
    )
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
    taken = set(sub.choices)
    for ext in extensions:
        for verb in ext.cli:
            if verb.name in taken:
                raise ValueError(f"extension {ext.id!r}: the verb {verb.name!r} is taken")
            taken.add(verb.name)
            p = sub.add_parser(verb.name, help=verb.help)
            if verb.configure is not None:
                verb.configure(p)
            p.set_defaults(run=verb.run)
    return parser


def _tolerant_output() -> None:
    """Print any text, even where the output's encoding lacks some characters.

    A Windows console or a redirected output may use a legacy code page: a name
    or a term outside it is written as an escape instead of stopping the command.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="backslashreplace")
            except (AttributeError, ValueError, OSError):
                pass


def main(argv: list[str] | None = None, *, extensions: Sequence[Extension] = ()) -> int:
    """Entry point of the ``cartolex`` command; returns the exit status.

    A host application passes its *extensions*: their command verbs are added,
    and ``cartolex app`` / ``cartolex api`` run the app with them.
    """
    _tolerant_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["app"]
    if argv[:1] == ["demo"]:
        return _demo(argv[1:])
    args = _parser(extensions).parse_args(argv)
    args.extensions = tuple(extensions)
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
