# SPDX-License-Identifier: MIT
"""``cartolex collect``: bring people and texts into a project from the command line.

::

    cartolex collect people FOLDER FILE [--mapping JSON|FILE] [--dry-run] [--slot ID]
    cartolex collect folder FOLDER DIR [--create-people] [--slot ID]
    cartolex collect corpus FOLDER INDEX [--root DIR] [--slot ID]
    cartolex collect resolve FOLDER [--auto] [--threshold X] [--people ID…] [--again] [SERVICES]
    cartolex collect confirm FOLDER PERSON [RECORD…] [--none]
    cartolex collect harvest FOLDER [--years FIRST-LAST] [--people ID…] [--resume] [SERVICES]
    cartolex collect snapshot FOLDER SNAPSHOT [--years …] [--people ID…] [--resume] [SERVICES]
    cartolex collect snapshot-index SNAPSHOT [--jobs N] [--status]
    cartolex collect rebuild PROJECT [--workers N] [--scratch DIR]
    cartolex collect institutions FOLDER (--search NAME | --institution ID…) [--years …]
                                  [--min-works N] [--level TYPE=LEVEL…] [--resume] [--snapshot DIR]
                                  [SERVICES]
    cartolex collect institutions FOLDER --take all|A…|A1+A2… [--role ROLE]
    cartolex collect collaborators FOLDER [--rounds N] [--seeds ID…] [--cap N]
                                   [--max-authors N] [--snapshot DIR] [SERVICES]
    cartolex collect collaborators FOLDER --decide PERSON=DECISION…
    cartolex collect coverage FOLDER [--person ID] [--good N] [--json] [--exclude ID…]
                              [--add-documents ID DIR] [--retry [SERVICES]]
    cartolex collect window FOLDER FIRST-LAST|FIRST-|none [--slot ID]
    cartolex collect duplicates FOLDER [--limit N] [--merge-clear]
    cartolex collect merge FOLDER KEEP OTHER [--override-orcid]
    cartolex collect unmerge FOLDER PERSON…

``SERVICES`` are the options of every verb that reaches a service:
``--dry-run`` (print the estimate and what would leave the computer, send
nothing), ``--refresh`` / ``--cache-only``, ``--contact EMAIL`` (default:
``$CARTOLEX_CONTACT``), ``--openalex-key KEY`` (default:
``$OPENALEX_API_KEY``, else the key saved in the app's settings, in its folder
``--data-dir`` or the default one), and ``--services demo`` to run against the demo
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
    from cartolex.collect.decisions import read_people, update_people
    from cartolex.collect.duplicates import clear_groups, duplicate_pairs
    from cartolex.project.identity import AUTO_MERGE_NOTE, MergeRefused, merge_changes
    from cartolex.project.pairs import read_pairs

    project = _open(args.folder, write=args.merge_clear)
    try:
        pairs, facts = duplicate_pairs(project)
        decided = read_pairs(project.layout)
        shown = [p for p in pairs if (p.a, p.b) not in decided]
        for p in shown[: args.limit]:
            why = "; ".join(e["text"] for e in p.evidence)
            mark = "clear" if p.clear else "conflict" if p.conflict else ""
            print(f"{p.a}  {p.b}  {p.score:.2f}  {mark:8}  {why}")
        groups = clear_groups(pairs, facts, set(decided))
        merged = sum(len(g["merge"]) for g in groups)
        print(
            f"{len(shown)} possible duplicate pair(s), {sum(p.clear for p in shown)} clear; "
            f"merging the clear ones would merge {merged} person(s)"
        )
        if not args.merge_clear or not groups:
            return 0
        rows = read_people(project.layout)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        note = f"{AUTO_MERGE_NOTE} {stamp}"
        changes: dict[str, dict[str, str]] = {}
        orcids = {pid: next(iter(sorted(f.orcids)), None) for pid, f in facts.items()}
        for g in groups:
            for pid in (g["keep"], *g["merge"]):
                rows.setdefault(pid, {"person_id": pid, "merged_into": "", "records": ""})
            try:
                found = merge_changes(rows, g["keep"], g["merge"], orcids, note=note)
            except MergeRefused:
                continue
            changes.update({pid: {**c, "note": note} for pid, c in found.items()})
        update_people(project.layout, changes, action=f"merge {len(changes)} clear duplicates")
        print(f"merged {len(changes)} person(s); undo with: cartolex collect unmerge "
              f"{args.folder} {' '.join(sorted(changes))}")  # fmt: skip
    finally:
        project.close()
    return 0


def _merge(args: argparse.Namespace) -> int:
    from cartolex.collect.people_import import confirm_merge

    project = _open(args.folder)
    try:
        confirm_merge(project, args.keep, args.other, override=args.override_orcid)
    finally:
        project.close()
    print(f"{args.other} is merged into {args.keep}; its name is one of {args.keep}'s aliases")
    return 0


def _unmerge(args: argparse.Namespace) -> int:
    from cartolex.collect.people_import import undo_merge

    project = _open(args.folder)
    try:
        undone = undo_merge(project, args.people)
    finally:
        project.close()
    if not undone:
        print("nobody to unmerge: none of these people is merged, or has people merged into them")
        return 1
    print(f"{len(undone)} person(s) stand on their own again: {', '.join(undone)}")
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


def _saved_key(args: argparse.Namespace, service: str) -> str | None:
    """The key saved for *service* in the app's settings, in its folder (``--data-dir``, else
    the default one), or ``None``."""
    from cartolex.app.machine import MachineKeys
    from cartolex.app.server import default_data_dir

    return MachineKeys(args.data_dir or default_data_dir()).get(service)


@contextlib.contextmanager
def _services(args: argparse.Namespace) -> Iterator[Any]:
    """The settings of the job: real services, demo services started here, or running ones."""
    from cartolex.collect.services import CollectSettings, local_settings

    contact = args.contact or os.environ.get("CARTOLEX_CONTACT") or None
    key = args.openalex_key or os.environ.get("OPENALEX_API_KEY") or _saved_key(args, "openalex")
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
    """Progress lines: when the message changes, at most every two seconds, with the time
    left when the job knows it."""

    def __init__(self) -> None:
        self.last = ("", 0.0)

    def __call__(self, fraction: float, message: str, **detail: Any) -> None:
        now = time.monotonic()
        if message != self.last[0] and (now - self.last[1] >= 2.0 or fraction >= 1.0):
            eta = detail.get("eta_s")
            left = f" (about {_duration(eta)} left)" if eta else ""
            print(f"[{fraction:4.0%}] {message}{left}", flush=True)
            self.last = (message, now)


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


_RESUME_HARVEST = (
    "go on with a harvest of the same people that paused (stopped, or failures in a row): "
    "the people it wrote are skipped"
)


def _run(args: argparse.Namespace, action: str) -> int:
    from cartolex.collect.http import Cancelled, HttpClient
    from cartolex.collect.privacy import plan_collection, record_job
    from cartolex.project.checkpoints import JobPaused

    mode = "refresh" if args.refresh else ("cache_only" if args.cache_only else "normal")
    with _services(args) as settings:
        if args.dry_run:
            from cartolex.project import Project

            plan = plan_collection(
                Project.open(args.folder), action, settings, **_plan_options(args, action)
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

        # Ctrl-C, or a service manager stopping the job (SIGTERM): a clean stop first.
        previous = signal.signal(signal.SIGINT, on_interrupt)
        previous_term = signal.signal(signal.SIGTERM, on_interrupt)
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
            plan = plan_collection(project, action, settings, **_plan_options(args, action))
            for line in plan.lines()[: 1 + len(plan.hosts)]:
                print(line)
            counts = RUNNERS[action](args, project, client)
        except Cancelled as exc:
            print(str(exc))
            outcome, status = "cancelled", 130
        except JobPaused as paused:
            print(f"paused: {paused.message}")
            if paused.cause is not None:
                print(f"cause: {paused.cause}")
            print("go on with the same command and --resume")
            outcome, status = "paused", 75
        except Exception:
            outcome = "failed"
            raise
        finally:
            signal.signal(signal.SIGINT, previous)
            signal.signal(signal.SIGTERM, previous_term)
            counts.update({f"requests_{k}": v for k, v in client.counts.items()})
            record_job(
                project,
                action,
                started=started,
                outcome=outcome,
                counts=counts,
                egress=client.egress.summary(),
                phases=getattr(args, "phases_done", ()),
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


def _source(args: argparse.Namespace, client: Any) -> Any:
    """OpenAlex's records: from the snapshot folder given, else from the API."""
    folder = getattr(args, "snapshot", None)
    if not folder:
        from cartolex.collect.openalex import OpenAlexApi

        return OpenAlexApi(client)
    from cartolex.collect.snapshot import Snapshot, SnapshotSource

    def cancelled() -> bool:
        from cartolex.collect.http import Cancelled

        try:
            client.check_cancel()
        except Cancelled:
            return True
        return False

    from cartolex.project.layout import ProjectLayout

    # What the passes find waits on disk: in the folder given, else in the project's cache.
    spill = getattr(args, "spill", None) or ProjectLayout(Path(args.folder)).cache / "snapshot"
    return SnapshotSource(
        Snapshot(
            folder,
            progress=client.progress,
            cancel=cancelled,
            jobs=getattr(args, "jobs", None) or 1,
        ),
        spill=Path(spill),
    )


def _plan_options(args: argparse.Namespace, action: str) -> dict[str, Any]:
    """What the summary of *action* needs from the command line."""
    options: dict[str, Any] = {"people": getattr(args, "people", None) or None}
    folder = getattr(args, "snapshot", None)
    if folder:
        options["snapshot"] = Path(folder).name
    if action == "institutions":
        options.update(institutions=args.institution or (), search=args.search)
    elif action == "collaborators":
        options.update(rounds=args.rounds, seeds=len(args.seeds or ()) or None, cap=args.cap)
    return options


def _harvest(args: argparse.Namespace, project: Any, client: Any) -> dict[str, Any]:
    from cartolex.collect.harvest import harvest

    report = harvest(
        project,
        client,
        people=args.people or None,
        years=_years(args.years),
        source=_source(args, client),
        resume=getattr(args, "resume", False),
    )
    for line in report.lines():
        print(line)
    return {
        "people": report.people,
        "works": sum(report.works.values()),
        "failed": len(report.failures),
    }


def _levels(pairs: list[str] | None) -> dict[str, str] | None:
    """``TYPE=LEVEL`` pairs: the level of each type of institution."""
    if not pairs:
        return None
    out = {}
    for pair in pairs:
        typ, sep, level = pair.partition("=")
        if not sep or not level:
            raise ValueError(f"--level wants TYPE=LEVEL (facility=lab), not {pair!r}")
        out[typ.strip()] = level.strip()
    return out


def _institutions_run(args: argparse.Namespace, project: Any, client: Any) -> dict[str, Any]:
    from cartolex.collect.institutions import find_institutions, propose_people

    source = _source(args, client)
    if args.search:
        found = find_institutions(source, args.search)
        for inst in found:
            parents = f"; part of {', '.join(inst['parents'])}" if inst["parents"] else ""
            ror = f"  ROR {inst['ror']}" if inst["ror"] else ""
            print(
                f"{inst['id']}  {inst['name']} ({inst['type']}){ror}  {inst['works_count']} works{parents}"
            )
        print(f"{len(found)} institution(s); propose people with: --institution ID (or its ROR)")
        return {"institutions": len(found)}
    from cartolex.project.checkpoints import JobPaused

    try:
        proposal = propose_people(
            project,
            source,
            args.institution,
            years=_years(args.years),
            min_works=args.min_works,
            levels=_levels(args.level),
            resume=args.resume,
        )
    except JobPaused as paused:
        print(f"paused: {paused.message}")
        print("go on with the same command and --resume")
        return {"paused": paused.code, **paused.progress}
    for line in proposal.lines(show=args.show):
        print(line)
    print("take people with: --take all, or --take RECORD… (A1+A2 takes two records as one)")
    return {"proposed": len(proposal.people), "works": proposal.works}


def _institutions(args: argparse.Namespace) -> int:
    if not args.take:
        if not args.search and not args.institution:
            raise ValueError("give --search NAME, --institution ID… or --take")
        return _run(args, "institutions")
    from cartolex.collect.institutions import take_people

    project = _open(args.folder)
    try:
        report = take_people(
            project,
            "all" if args.take == ["all"] else args.take,
            role=args.role,
            levels=_levels(args.level),
        )
    finally:
        project.close()
    for line in report.lines():
        print(line)
    print("collect their works with: cartolex collect harvest FOLDER")
    return 0


def _collaborators_run(args: argparse.Namespace, project: Any, client: Any) -> dict[str, Any]:
    from cartolex.collect.snowball import snowball

    # Each phase in the job's record (where the time went), and said as it ends.
    args.phases_done = []
    sent = dict(client.counts)

    def on_phase(phase: str, **detail: Any) -> None:
        now = dict(client.counts)
        line = {"phase": phase, **detail, "requests": now["sent"] - sent["sent"]}
        line["cached"] = now["cached"] - sent["cached"]
        sent.update(now)
        args.phases_done.append(line)
        print(f"{phase}: {line['seconds']} s, {line['requests']} request(s)", flush=True)

    report = snowball(
        project,
        _source(args, client),
        rounds=args.rounds,
        seeds=args.seeds or None,
        years=_years(args.years),
        cap=args.cap,
        max_authors=args.max_authors,
        progress=client.progress,
        on_phase=on_phase,
    )
    for line in report.lines(show=args.show):
        print(line)
    print(
        "they are 'context' (their texts shape the lexicon, they are not on the map); "
        "decide with --decide PERSON=mapped|context|projected|no|later"
    )
    return {"collaborators": len(report.collaborators), "rounds": len(report.rounds)}


def _collaborators(args: argparse.Namespace) -> int:
    if not args.decide:
        return _run(args, "collaborators")
    from cartolex.collect.snowball import decide_collaborators

    decisions = {}
    for pair in args.decide:
        pid, sep, decision = pair.partition("=")
        if not sep:
            raise ValueError(f"--decide wants PERSON=DECISION, not {pair!r}")
        decisions[pid.strip()] = decision.strip()
    project = _open(args.folder)
    try:
        changed = decide_collaborators(project, decisions)
    finally:
        project.close()
    print(f"{len(decisions)} decision(s) recorded; {changed} role(s) changed")
    return 0


def _snapshot(args: argparse.Namespace) -> int:
    return _run(args, "harvest")


def _snapshot_index(args: argparse.Namespace) -> int:
    """Index a snapshot (or say how far its index is): once per release, hours on an
    external disk; stopped (Ctrl-C), the same command goes on from where it was."""
    import os
    import time

    from cartolex.collect.http import Cancelled
    from cartolex.collect.snapshot_index import build_index, index_state

    if args.status:
        state = index_state(args.snapshot)
        if state is None:
            print(f"{args.snapshot}: not indexed")
        else:
            print(f"{args.snapshot}: {state['state']}, release {state['release']}, "
                  f"{state['done']} of {state['parts']} part(s) cut")  # fmt: skip
        return 0
    jobs = args.jobs or max(1, (os.cpu_count() or 2) - 2)
    shown = [-1.0, 0.0]

    def progress(fraction: float, message: str) -> None:
        now = time.monotonic()
        if fraction - shown[0] >= 0.005 or now - shown[1] >= 60:
            print(f"[{100 * fraction:5.1f}%] {message}", flush=True)
            shown[:] = [fraction, now]

    print(f"indexing {args.snapshot} with {jobs} worker(s); Ctrl-C stops, the same command "
          "goes on", flush=True)  # fmt: skip
    stop = threading.Event()

    def on_interrupt(signum: int, frame: object) -> None:
        if stop.is_set():
            raise KeyboardInterrupt
        print("stopping after the parts being cut (Ctrl-C again to stop at once)", flush=True)
        stop.set()

    previous = signal.signal(signal.SIGINT, on_interrupt)
    previous_term = signal.signal(signal.SIGTERM, on_interrupt)
    try:
        report = build_index(args.snapshot, jobs=jobs, progress=progress, cancel=stop.is_set)
    except (KeyboardInterrupt, Cancelled):
        print("stopped: run the same command to go on")
        return 130
    finally:
        signal.signal(signal.SIGINT, previous)
        signal.signal(signal.SIGTERM, previous_term)
    for line in report.lines():
        print(line)
    return 0 if report.complete else 1


def _coverage_retry(args: argparse.Namespace, project: Any, client: Any) -> dict[str, Any]:
    from cartolex.collect.coverage import retry_failed

    reports = retry_failed(
        project, client, people=args.people or None, source=_source(args, client)
    )
    for finder, report in reports.items():
        lines = report.lines() if hasattr(report, "lines") else [str(report)]
        print(f"{finder}: " + "; ".join(lines[:3]))
    return {"retried": sum(1 for r in reports.values() if hasattr(r, "lines"))}


def _coverage(args: argparse.Namespace) -> int:
    from cartolex.collect.coverage import (
        add_documents,
        coverage_report,
        exclude_person,
        person_sheet,
    )

    if args.retry:
        return _run(args, "coverage")
    if args.exclude or args.add_documents:
        if args.dry_run:
            print("would change: " + ", ".join(args.exclude or [args.add_documents[0]]))
            return 0
        project = _open(args.folder)
        try:
            for pid in args.exclude or ():
                exclude_person(project, pid)
                print(f"{pid}: excluded")
            if args.add_documents:
                pid, folder = args.add_documents
                report = add_documents(project, pid, Path(folder))
                for line in report.lines():
                    print(line)
        finally:
            project.close()
        return 0
    from cartolex.project import Project

    project = Project.open(args.folder)
    if args.person:
        sheet = person_sheet(project, args.person, good=args.good)
        if args.json:
            print(json.dumps(sheet, ensure_ascii=False, indent=1))
            return 0
        print(f"{sheet['person_id']}  {sheet['name']}: {sheet['state']}")
        print(
            f"  {sheet['with_abstract']} text(s) with an abstract, "
            f"{sheet['titles_only']} title(s) only"
        )
        if sheet["cause_text"]:
            print(f"  first blocking cause: {sheet['cause_text']}")
        for src in sheet["sources"]:
            print(f"  source used: {src['finder']} ({src['texts']} text(s))")
        for d in sheet["discarded"]:
            print(f"  discarded: {d['what']}: {d['why']}")
        for a in sheet["attempts"]:
            print(f"  latest {a['finder']}: {'ok' if a['ok'] else 'failed: ' + a['cause']}")
        print("  actions: " + ", ".join(sheet["actions"]))
        return 0
    report = coverage_report(project, good=args.good)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    states = ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in report["states"].items())
    print(f"{report['people']} people ({report['excluded']} excluded): {states}")
    print(f"good from {report['good']} text(s) with an abstract")
    for cause, n in report["causes"].items():
        print(f"  {n} × {cause.replace('_', ' ')}")
    for p in report["persons"]:
        if p["state"] != "good":
            print(f"  {p['person_id']}  {p['state']:<8} {p['name']}: {p['cause_text']}")
    return 0


def _rebuild(args: argparse.Namespace) -> int:
    import time

    from cartolex.collect.tables import rebuild_sources

    project = _open(args.folder)
    try:
        started = time.monotonic()
        report = rebuild_sources(
            project.layout, project.config, jobs=args.workers, scratch=args.scratch
        )
    finally:
        project.close()
    rows = ", ".join(f"{n} {name}" for name, n in report.rows.items())
    print(f"tables rebuilt from {report.runs} run(s) in {time.monotonic() - started:.0f} s: {rows}")
    for warning in report.warnings[:10]:
        print(f"  {warning}")
    if len(report.warnings) > 10:
        print(f"  … and {len(report.warnings) - 10} other warning(s)")
    return 0


def _window(args: argparse.Namespace) -> int:
    from cartolex.collect.people_import import _collection_slot
    from cartolex.project.models import YearWindow

    project = _open(args.folder)
    try:
        slot = _collection_slot(project, args.slot, "collection")
        years = None if args.years.lower() in ("none", "all") else _years(args.years)
        config = project.config
        slots = [
            s.model_copy(
                update={"years": YearWindow(first=years[0], last=years[1]) if years else None}
            )
            if s.id == slot
            else s
            for s in config.slots
        ]
        project.save_config(
            config.model_copy(update={"slots": slots}), action=f"years of slot {slot}"
        )
    finally:
        project.close()
    shown = "every year" if years is None else f"{years[0] or '…'}–{years[1] or '…'}"
    print(f"slot {slot}: {shown}")
    return 0


#: The work of each verb that reaches a service, once the job is set up.
RUNNERS = {
    "resolve": lambda args, project, client: _resolve(args, project, client),
    "harvest": lambda args, project, client: _harvest(args, project, client),
    "institutions": lambda args, project, client: _institutions_run(args, project, client),
    "collaborators": lambda args, project, client: _collaborators_run(args, project, client),
    "coverage": lambda args, project, client: _coverage_retry(args, project, client),
}


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
    p.add_argument(
        "--openalex-key",
        help="an OpenAlex API key (default: $OPENALEX_API_KEY, else the key saved in the app)",
    )
    p.add_argument(
        "--data-dir", type=Path, help="the app's own folder, where its settings keep the keys"
    )
    p.add_argument(
        "--services",
        help="'demo' (the demo services of --world), or the URL of running demo services",
    )
    p.add_argument("--world", help="the demo world of --services demo, SIZE:SEED (default S:0)")
    p.add_argument("--people", nargs="+", metavar="ID", help="only these people")
    p.add_argument(
        "--jobs", type=int, default=1, help="snapshot parts read at once, in worker processes"
    )
    p.add_argument(
        "--spill",
        type=Path,
        help="where a snapshot reading keeps what it finds meanwhile (default: the project's "
        "cache/snapshot); a disk fast at random reads helps a national harvest",
    )


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
    cf.add_argument(
        "records", nargs="*", help="openalex:A…, orcid:…, hal:<idHAL>, an id or a URL holding one"
    )
    cf.add_argument("--none", action="store_true", help="no record exists for this person")
    cf.set_defaults(run=_confirm)

    ha = verbs.add_parser("harvest", help="collect the works of every confirmed person")
    ha.add_argument("folder", type=Path)
    ha.add_argument("--years", help="FIRST-LAST, FIRST- or YEAR (default: the slot's window)")
    ha.add_argument("--resume", action="store_true", help=_RESUME_HARVEST)
    _service_options(ha)
    ha.set_defaults(run=lambda a: _run(a, "harvest"))

    sn = verbs.add_parser(
        "snapshot", help="collect the works of every confirmed person from an OpenAlex snapshot"
    )
    sn.add_argument("folder", type=Path)
    sn.add_argument("snapshot", type=Path, help="the snapshot folder you downloaded")
    sn.add_argument("--years", help="FIRST-LAST, FIRST- or YEAR (default: the slot's window)")
    sn.add_argument("--resume", action="store_true", help=_RESUME_HARVEST)
    _service_options(sn)
    sn.set_defaults(run=_snapshot)

    si = verbs.add_parser(
        "snapshot-index",
        help="index a downloaded OpenAlex snapshot, so that a collection reads only what it asks",
    )
    si.add_argument("snapshot", type=Path, help="the snapshot folder you downloaded")
    si.add_argument(
        "--jobs", type=int, default=None, help="parts cut at once (default: processors less two)"
    )
    si.add_argument("--status", action="store_true", help="say how far the index is, and stop")
    si.set_defaults(run=_snapshot_index)

    it = verbs.add_parser(
        "institutions", help="propose the people of institutions and their units, then take them"
    )
    it.add_argument("folder", type=Path)
    it.add_argument("--search", help="find the institutions that bear this name")
    it.add_argument(
        "--institution", nargs="+", metavar="ID", help="OpenAlex institution ids or ROR ids"
    )
    it.add_argument("--years", help="FIRST-LAST, FIRST- or YEAR (default: the slot's window)")
    it.add_argument("--min-works", type=int, default=2, help="works there, at least (default 2)")
    it.add_argument(
        "--level", nargs="+", metavar="TYPE=LEVEL", help="the level of a type of institution"
    )
    it.add_argument("--show", type=int, default=50, help="people shown (default 50)")
    it.add_argument(
        "--resume",
        action="store_true",
        help="go on with a reading of works that paused (stopped, or a page kept failing)",
    )
    it.add_argument(
        "--take",
        nargs="+",
        metavar="RECORD",
        help="take people of the latest proposal: all, or records",
    )
    it.add_argument(
        "--role",
        default="mapped",
        choices=("mapped", "context", "projected", "excluded"),
        help="the role of the people taken (default mapped)",
    )
    it.add_argument("--snapshot", type=Path, help="read OpenAlex from this snapshot folder")
    _service_options(it)
    it.set_defaults(run=_institutions)

    cb = verbs.add_parser(
        "collaborators", help="propose the co-authors of the seeds, round by round"
    )
    cb.add_argument("folder", type=Path)
    cb.add_argument("--rounds", type=int, default=1, help="how many rounds (default 1)")
    cb.add_argument("--seeds", nargs="+", metavar="ID", help="the seeds (default: mapped people)")
    cb.add_argument("--cap", type=int, help="people proposed at most (default: params.json, 200)")
    cb.add_argument(
        "--max-authors", type=int, help="works with more authors are left out (default 25)"
    )
    cb.add_argument("--years", help="FIRST-LAST, FIRST- or YEAR (default: the slot's window)")
    cb.add_argument("--show", type=int, default=30, help="collaborators shown (default 30)")
    cb.add_argument(
        "--decide",
        nargs="+",
        metavar="PERSON=DECISION",
        help="mapped, context, projected, no, later",
    )
    cb.add_argument("--snapshot", type=Path, help="read OpenAlex from this snapshot folder")
    _service_options(cb)
    cb.set_defaults(run=_collaborators)

    cv = verbs.add_parser("coverage", help="what was collected for whom, and why not")
    cv.add_argument("folder", type=Path)
    cv.add_argument("--person", help="the sheet of one person")
    cv.add_argument("--good", type=int, help="texts with an abstract for a good profile")
    cv.add_argument("--json", action="store_true", help="print the report as JSON")
    cv.add_argument("--retry", action="store_true", help="collect again for the people who failed")
    cv.add_argument("--exclude", nargs="+", metavar="ID", help="exclude these people")
    cv.add_argument(
        "--add-documents", nargs=2, metavar=("ID", "DIR"), help="a folder of one person's documents"
    )
    cv.add_argument("--snapshot", type=Path, help="retry from this snapshot folder")
    _service_options(cv)
    cv.set_defaults(run=_coverage)

    rb = verbs.add_parser("rebuild", help="rebuild the source tables from the raw runs")
    rb.add_argument("folder", type=Path)
    rb.add_argument("--workers", type=int, help="worker processes reading heavy runs")
    rb.add_argument(
        "--scratch", type=Path, help="a folder on a fast local disk for the rebuild's database"
    )
    rb.set_defaults(run=_rebuild)

    wi = verbs.add_parser("window", help="set the years a collection slot collects by default")
    wi.add_argument("folder", type=Path)
    wi.add_argument("years", help="FIRST-LAST, FIRST-, YEAR, or none (every year)")
    wi.add_argument("--slot", help="the collection slot (default: the first)")
    wi.set_defaults(run=_window)

    du = verbs.add_parser("duplicates", help="people who may be one person")
    du.add_argument("folder", type=Path)
    du.add_argument("--limit", type=int, default=50, help="pairs printed (the most likely first)")
    du.add_argument(
        "--merge-clear",
        action="store_true",
        help="merge the clear pairs in one step (undone with unmerge)",
    )
    du.set_defaults(run=_duplicates)

    me = verbs.add_parser("merge", help="record that OTHER is the same person as KEEP")
    me.add_argument("folder", type=Path)
    me.add_argument("keep")
    me.add_argument("other")
    me.add_argument(
        "--override-orcid",
        action="store_true",
        help="merge even though the two have different ORCIDs (you know they are one person)",
    )
    me.set_defaults(run=_merge)

    um = verbs.add_parser("unmerge", help="undo merges: these people stand on their own again")
    um.add_argument("folder", type=Path)
    um.add_argument("people", nargs="+", metavar="PERSON")
    um.set_defaults(run=_unmerge)
