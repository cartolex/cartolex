# SPDX-License-Identifier: MIT
"""``cartolex collect``: bring people and texts into a project from the command line.

::

    cartolex collect people FOLDER FILE [--mapping JSON|FILE] [--dry-run] [--slot ID]
    cartolex collect folder FOLDER DIR [--create-people] [--slot ID]
    cartolex collect corpus FOLDER INDEX [--root DIR] [--slot ID]
    cartolex collect resolve FOLDER [--auto] [--threshold X] [--people ID…] [--again] [SERVICES]
    cartolex collect confirm FOLDER PERSON [RECORD…] [--none]
    cartolex collect harvest FOLDER [--years FIRST-LAST] [--people ID…] [SERVICES]
    cartolex collect duplicates FOLDER
    cartolex collect merge FOLDER KEEP OTHER

``SERVICES`` are the options of every verb that reaches a service:
``--dry-run`` (print the estimate and what would leave the computer, send
nothing), ``--refresh`` / ``--cache-only``, ``--contact EMAIL`` (default:
``$CARTOLEX_CONTACT``), ``--openalex-key KEY`` (default:
``$OPENALEX_API_KEY``), and ``--services demo`` to run against the demo
services of a demo world (``--world SIZE:SEED``, default ``S:0``) or
``--services URL`` for demo services already running. The environment is
read here, at the edge, and passed in as settings.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["add_parser"]


def _open(folder: Path, write: bool = True):
    from cartolex.project import Project

    project = Project.open(folder, write=write)
    for note in getattr(project, "recovered", []):
        print(note)
    return project


# ── people, folders, corpora ─────────────────────────────────────────────────


def _mapping(text: str | None):
    from cartolex.collect.people_import import ImportMapping

    if not text:
        return None
    path = Path(text)
    raw = path.read_text(encoding="utf-8") if path.is_file() else text
    return ImportMapping.from_json(json.loads(raw))


def _people(args: argparse.Namespace) -> int:
    from cartolex.collect.people_import import import_people, propose_mapping, read_list

    source: Path | str = sys.stdin.read() if str(args.file) == "-" else Path(args.file)
    if args.dry_run:
        from cartolex.project import Project

        project = Project.open(args.folder)
        rows, delimiter = read_list(source)
        mapping = _mapping(args.mapping) or propose_mapping(rows, levels=project.config.levels)
        mapping.check()
        shown = {"\t": "tab", ",": "comma", ";": "semicolon"}[delimiter]
        header = "with a header" if mapping.has_header else "without a header"
        print(f"{len(rows) - mapping.has_header} row(s), {shown}-separated, {header}:")
        for line in mapping.describe():
            print(f"  {line}")
        print("the mapping as JSON (edit it and pass it with --mapping):")
        print(json.dumps(mapping.to_json(), ensure_ascii=False))
        return 0
    project = _open(args.folder)
    try:
        report = import_people(project, source, mapping=_mapping(args.mapping), slot=args.slot)
    finally:
        project.close()
    for line in report.lines():
        print(line)
    return 0


def _folder(args: argparse.Namespace) -> int:
    from cartolex.collect.people_import import import_folder

    project = _open(args.folder)
    try:
        report = import_folder(
            project, args.documents, slot=args.slot, create_people=args.create_people
        )
    finally:
        project.close()
    for line in report.lines():
        print(line)
    return 0


def _corpus(args: argparse.Namespace) -> int:
    from cartolex.collect.people_import import import_corpus

    project = _open(args.folder)
    try:
        report = import_corpus(project, args.index, root=args.root, slot=args.slot)
    finally:
        project.close()
    for line in report.lines():
        print(line)
    return 0


def _duplicates(args: argparse.Namespace) -> int:
    from cartolex.collect.people_import import find_duplicates
    from cartolex.project import Project

    proposals = find_duplicates(Project.open(args.folder))
    for d in proposals:
        print(f"{d.person_id}  {d.other_id}  {d.reason}")
    print(f"{len(proposals)} possible duplicate(s); none is merged unless you run merge")
    return 0


def _merge(args: argparse.Namespace) -> int:
    from cartolex.collect.people_import import confirm_merge

    project = _open(args.folder)
    try:
        confirm_merge(project, args.keep, args.other)
    finally:
        project.close()
    print(f"{args.other} is merged into {args.keep}; its name is one of {args.keep}'s aliases")
    return 0


def _confirm(args: argparse.Namespace) -> int:
    from cartolex.collect.resolve import confirm, confirm_none

    project = _open(args.folder)
    try:
        if args.none:
            confirm_none(project, args.person)
            print(f"{args.person}: no record")
        else:
            found = confirm(project, args.person, args.records)
            print(f"{args.person}: confirmed {', '.join(found)}")
    finally:
        project.close()
    return 0


# ── verbs that reach a service ───────────────────────────────────────────────


@contextlib.contextmanager
def _services(args: argparse.Namespace) -> Iterator[Any]:
    """The settings of the job: real services, demo services started here, or running ones."""
    from cartolex.collect.services import CollectSettings, local_settings

    contact = args.contact or os.environ.get("CARTOLEX_CONTACT") or None
    key = args.openalex_key or os.environ.get("OPENALEX_API_KEY") or None
    keys = {"openalex": key} if key else {}
    if args.services in (None, "real"):
        yield CollectSettings(contact=contact, api_keys=keys, user_agent="cartolex")
        return
    from cartolex.demo.services import DemoServices, endpoints_at

    if args.services == "demo":
        from cartolex.demo import generate

        size, _, seed = (args.world or "S:0").partition(":")
        with DemoServices(generate(size.upper(), int(seed or 0))) as demo:
            print(f"demo services of world {size.upper()}/{seed or 0} at {demo.base_url}")
            yield local_settings(demo.endpoints(), contact=contact, api_keys=keys)
        return
    yield local_settings(endpoints_at(args.services), contact=contact, api_keys=keys)


class _Printer:
    """Progress lines: when the message changes, at most every two seconds."""

    def __init__(self) -> None:
        self.last = ("", 0.0)

    def __call__(self, fraction: float, message: str) -> None:
        now = time.monotonic()
        if message != self.last[0] and (now - self.last[1] >= 2.0 or fraction >= 1.0):
            print(f"[{fraction:4.0%}] {message}", flush=True)
            self.last = (message, now)


def _run(args: argparse.Namespace, action: str) -> int:
    from cartolex.collect.http import Cancelled, HttpClient
    from cartolex.collect.privacy import plan_collection, record_job

    mode = "refresh" if args.refresh else ("cache_only" if args.cache_only else "normal")
    with _services(args) as settings:
        if args.dry_run:
            from cartolex.project import Project

            plan = plan_collection(
                Project.open(args.folder), action, settings, people=args.people or None
            )
            for line in plan.lines():
                print(line)
            return 0
        project = _open(args.folder)
        cancel = threading.Event()

        def on_interrupt(signum: int, frame: object) -> None:
            if cancel.is_set():
                raise KeyboardInterrupt
            print("stopping after the current request (Ctrl-C again to stop at once)", flush=True)
            cancel.set()

        previous = signal.signal(signal.SIGINT, on_interrupt)
        started = datetime.now(timezone.utc)
        client = HttpClient(
            settings,
            cache_dir=project.layout.cache_http,
            mode=mode,
            progress=_Printer(),
            cancel=cancel.is_set,
        )
        outcome, status = "succeeded", 0
        counts: dict[str, Any] = {}
        try:
            plan = plan_collection(project, action, settings, people=args.people or None)
            for line in plan.lines()[: 1 + len(plan.hosts)]:
                print(line)
            if action == "resolve":
                counts = _resolve(args, project, client)
            else:
                counts = _harvest(args, project, client)
        except Cancelled as exc:
            print(str(exc))
            outcome, status = "cancelled", 130
        except Exception:
            outcome = "failed"
            raise
        finally:
            signal.signal(signal.SIGINT, previous)
            counts.update({f"requests_{k}": v for k, v in client.counts.items()})
            record_job(
                project,
                action,
                started=started,
                outcome=outcome,
                counts=counts,
                egress=client.egress.summary(),
            )
            project.close()
        for e in client.egress.summary():
            print(
                f"sent to {e['service']} ({e['host']}): {e['requests']} request(s) carrying "
                + ", ".join(e["sends"])
            )
        return status


def _resolve(args: argparse.Namespace, project: Any, client: Any) -> dict[str, Any]:
    from cartolex.collect.resolve import resolve

    report = resolve(
        project,
        client,
        people=args.people or None,
        auto=args.auto,
        threshold=args.threshold,
        again=args.again,
    )
    for res in report.resolutions:
        records = ", ".join(res.records) if res.records else ""
        print(
            f"{res.person_id}  {res.status:<8} {res.name}" + (f"  → {records}" if records else "")
        )
        if res.status != "auto":
            for cand in res.candidates[: args.show]:
                print("    " + cand.describe())
            for reg in res.registry:
                print(f"    registry {reg.record}: {reg.with_doi} declared work(s) with a DOI")
        for note in res.notes:
            print(f"    note: {note}")
    counts = report.counts
    print(
        ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "nobody to resolve",
        f"({report.skipped} already decided)",
    )
    print("confirm with: cartolex collect confirm FOLDER PERSON RECORD… (or --none)")
    return {**counts, "skipped": report.skipped}


def _years(text: str | None) -> tuple[int | None, int | None] | None:
    """``2012-2026``, ``2012-`` (open end) or ``2020`` (one year)."""
    if not text:
        return None
    try:
        if "-" in text:
            first, last = text.split("-", 1)
            return (int(first) if first else None, int(last) if last else None)
        year = int(text)
    except ValueError:
        raise ValueError(f"--years wants FIRST-LAST, FIRST- or YEAR, not {text!r}") from None
    return (year, year)


def _harvest(args: argparse.Namespace, project: Any, client: Any) -> dict[str, Any]:
    from cartolex.collect.harvest import harvest

    report = harvest(project, client, people=args.people or None, years=_years(args.years))
    for line in report.lines():
        print(line)
    return {"people": report.people, "works": sum(report.works.values())}


# ── the parser ───────────────────────────────────────────────────────────────


def _service_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--dry-run", action="store_true", help="show the estimate and what leaves the computer"
    )
    cache = p.add_mutually_exclusive_group()
    cache.add_argument("--refresh", action="store_true", help="ignore cached answers, fetch again")
    cache.add_argument(
        "--cache-only", action="store_true", help="use cached answers only, never the network"
    )
    p.add_argument("--contact", help="your e-mail address, sent to services that ask for one")
    p.add_argument("--openalex-key", help="an OpenAlex API key (default: $OPENALEX_API_KEY)")
    p.add_argument(
        "--services",
        help="'demo' (the demo services of --world), or the URL of running demo services",
    )
    p.add_argument("--world", help="the demo world of --services demo, SIZE:SEED (default S:0)")
    p.add_argument("--people", nargs="+", metavar="ID", help="only these people")


def add_parser(sub: Any) -> None:
    """Add ``collect`` and its verbs to the ``cartolex`` parser."""
    collect = sub.add_parser("collect", help="bring people and texts into a project")
    verbs = collect.add_subparsers(dest="collect_verb", required=True)

    pe = verbs.add_parser("people", help="import a list of people (CSV file, or - for stdin)")
    pe.add_argument("folder", type=Path)
    pe.add_argument("file")
    pe.add_argument("--mapping", help="the column mapping, as JSON or a JSON file")
    pe.add_argument("--dry-run", action="store_true", help="show the mapping; import nothing")
    pe.add_argument("--slot", help="the collection slot (default: the first, or a new one)")
    pe.set_defaults(run=_people)

    fo = verbs.add_parser("folder", help="import a folder of documents (PDF, text)")
    fo.add_argument("folder", type=Path)
    fo.add_argument("documents", type=Path)
    fo.add_argument("--slot")
    fo.add_argument(
        "--create-people", action="store_true", help="a sub-folder naming nobody creates a person"
    )
    fo.set_defaults(run=_folder)

    co = verbs.add_parser("corpus", help="import a corpus in the engine's contract")
    co.add_argument("folder", type=Path)
    co.add_argument("index", type=Path)
    co.add_argument(
        "--root", type=Path, help="where txt_path is relative to (default: the index's folder)"
    )
    co.add_argument("--slot")
    co.set_defaults(run=_corpus)

    re_ = verbs.add_parser("resolve", help="find each person's records")
    re_.add_argument("folder", type=Path)
    re_.add_argument("--auto", action="store_true", help="accept single clear matches (to review)")
    re_.add_argument("--threshold", type=float, default=0.8)
    re_.add_argument("--again", action="store_true", help="also people accepted automatically")
    re_.add_argument("--show", type=int, default=3, help="candidates shown per person")
    _service_options(re_)
    re_.set_defaults(run=lambda a: _run(a, "resolve"))

    cf = verbs.add_parser("confirm", help="record which records are a person (or none)")
    cf.add_argument("folder", type=Path)
    cf.add_argument("person")
    cf.add_argument("records", nargs="*", help="openalex:A…, orcid:…, an id or a URL holding one")
    cf.add_argument("--none", action="store_true", help="no record exists for this person")
    cf.set_defaults(run=_confirm)

    ha = verbs.add_parser("harvest", help="collect the works of every confirmed person")
    ha.add_argument("folder", type=Path)
    ha.add_argument("--years", help="FIRST-LAST, FIRST- or YEAR (default: every year)")
    _service_options(ha)
    ha.set_defaults(run=lambda a: _run(a, "harvest"))

    du = verbs.add_parser("duplicates", help="people who may be one person")
    du.add_argument("folder", type=Path)
    du.set_defaults(run=_duplicates)

    me = verbs.add_parser("merge", help="record that OTHER is the same person as KEEP")
    me.add_argument("folder", type=Path)
    me.add_argument("keep")
    me.add_argument("other")
    me.set_defaults(run=_merge)
