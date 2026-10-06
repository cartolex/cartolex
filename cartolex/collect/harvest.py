# SPDX-License-Identifier: MIT
"""Harvest: the works of every confirmed person, into the source tables.

For each person whose identity is confirmed or accepted automatically, the
records in ``decisions/people.csv`` say where their works are:

* ``openalex:A…`` — every work of these author records, in the year window,
  through cursor pages of 100, and the records themselves (their
  affiliations, with years). From the API, the records of several people
  are asked for together, up to 50 a request, and each person gets the works
  their own records sign: what a request per person would give, in fewer
  requests;
* ``orcid:…`` — the works the person declared in the registry, fetched from
  the index by DOI, 50 at a time, and the registry record (employments).

A person's works from all their records are united, duplicates removed by DOI,
then by normalised title and year. Each work becomes a text with its title and
abstract (rebuilt from the inverted index), its language detected per part; its
authorships name every project person on it, at their rank, with ``last`` and
``corresponding`` when the service says so, and with the institutions the work
states for them, which also date their affiliations.

Everything received is kept in ``sources/<slot>/raw/openalex/`` and
``raw/orcid/``; the tables are rebuilt from these runs, a person's latest run
replacing their earlier ones. A long harvest writes a run every
:data:`CHUNK_PEOPLE` people or :data:`CHUNK_SECONDS` seconds, whichever comes
first, and saves a checkpoint of the people done: whatever happens to the job
afterwards, those runs are kept. A harvest stopped (a cancel, or failures in a
row) raises :class:`~cartolex.project.checkpoints.JobPaused` with that
checkpoint; given ``resume=True``, the same harvest (same people, records,
window and source) goes on with the people not yet done, those whose
collection failed included.

The years default to the slot's window (``years`` in ``project.json``). The
OpenAlex part comes from the API, or from a downloaded snapshot
(:mod:`cartolex.collect.snapshot`), read in one pass for the whole job: the
runs written are the same. A person whose collection fails is recorded with
the cause (:mod:`cartolex.collect.outcomes`) and the others go on; after three
failures in a row the harvest stops, keeps what it collected, and pauses with
the last error as its cause.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from cartolex.project import Project
from cartolex.project.checkpoints import Checkpoint, JobPaused, work_key
from cartolex.project.identity import merge_roots, merged_groups
from cartolex.project.tables import read_source_table

from .decisions import read_people, slot_window
from .http import CacheMiss, Cancelled, CollectError, Fetched, HttpClient, ServiceError
from .names import name_similarity, words
from .openalex import (
    AUTHOR_BATCH,
    OpenAlexApi,
    OpenAlexSource,
    Years,
    _work_authors,
    author_batches,
    bare_doi,
    doc_type,
    short_id,
)
from .orcid import declared_works, employments, registry_record
from .outcomes import MAX_FAILURES_IN_A_ROW, failure_record, stops_the_job, write_failures
from .people_import import _collection_slot
from .tables import (
    RawRun,
    RawWriter,
    RebuildReport,
    SourceBuilder,
    iso,
    parse_time,
    raw_folder,
    rebuild_sources,
)
from .text import DETECTED_LANGUAGES, abstract_from_inverted_index, detect_language, strip_markup

__all__ = [
    "CHUNK_PEOPLE",
    "CHUNK_SECONDS",
    "HarvestReport",
    "current_runs",
    "harvest",
    "harvest_checkpoint_options",
    "read_openalex_runs",
    "read_orcid_runs",
    "work_text",
]


#: People a harvest writes per run: a run closed is kept whatever happens to the job after.
CHUNK_PEOPLE = 2000
#: A harvest closes its run after this many seconds, even with fewer people in it.
CHUNK_SECONDS = 600.0
#: From a source that can gather a person's records unparsed (a snapshot), people are put
#: together in worker processes when a harvest has more than this many to do.
PARALLEL_FROM = 200
#: People sent to a worker at once.
PARALLEL_BATCH = 64
#: The kind of a paused harvest's checkpoint (``sources/<slot>/raw/checkpoints/``).
CHECKPOINT_KIND = "harvest"


def _checkpoint_folder(project: Project, slot: str) -> Any:
    return raw_folder(project.layout, slot) / "checkpoints"


def harvest_checkpoint_options(
    project: Project, checkpoint_id: str, *, slot: str | None = None
) -> dict[str, Any] | None:
    """The options of the paused harvest *checkpoint_id* (to resume it), or ``None``."""
    slot = _collection_slot(project, slot, "collection")
    try:
        cp = Checkpoint.by_id(_checkpoint_folder(project, slot), checkpoint_id)
    except ValueError:
        return None
    if cp.kind != CHECKPOINT_KIND:
        return None
    saved = cp.load()
    return None if saved is None else dict(saved.state.get("options") or {})


@dataclass
class HarvestReport:
    """What a harvest did."""

    people: int = 0
    works: dict[str, int] = field(default_factory=dict)
    out_of_window: int = 0
    declared_dois: int = 0
    dois_not_indexed: int = 0
    notes: list[str] = field(default_factory=list)
    rebuild: RebuildReport | None = None
    cancelled: bool = False
    #: The first run this job wrote (``run_ids``: every one, a run per chunk of people).
    run_id: str = ""
    run_ids: list[str] = field(default_factory=list)
    #: People an earlier, stopped run of the same harvest had done (skipped when resuming).
    resumed: int = 0
    #: People whose harvest failed, with the cause (see :mod:`cartolex.collect.outcomes`).
    failures: list[dict[str, Any]] = field(default_factory=list)
    #: Why the harvest stopped before the end, if it did.
    stopped: str | None = None
    #: Where OpenAlex's records came from: ``api`` or ``snapshot``.
    source: str = "api"

    def lines(self) -> list[str]:
        out = [f"{self.people} person(s) harvested, {sum(self.works.values())} work(s) received"]
        if self.resumed:
            out.append(f"{self.resumed} person(s) harvested before, by the stopped run resumed")
        out += [f"failed: {f['person_id']}: {f['cause']}" for f in self.failures]
        if self.stopped:
            out.append(self.stopped)
        if self.declared_dois:
            out.append(
                f"{self.declared_dois} DOI(s) declared in the registry, "
                f"{self.dois_not_indexed} not in the index"
            )
        if self.out_of_window:
            out.append(f"{self.out_of_window} declared work(s) outside the years asked for")
        if self.rebuild is not None:
            out.append(
                "tables: " + ", ".join(f"{n} {k}" for k, n in sorted(self.rebuild.rows.items()))
            )
            out += self.rebuild.warnings[:20]
        return out + self.notes


def _in_window(work: dict[str, Any], years: tuple[int | None, int | None] | None) -> bool:
    if years is None:
        return True
    year = work.get("publication_year")
    if not isinstance(year, int):
        return True
    first, last = years
    return (first is None or year >= first) and (last is None or year <= last)


class _BatchedWorks:
    """An OpenAlex source whose people's works are asked for a batch of people at a time.

    :meth:`load` asks for every work of a batch's records in one list; each person's
    question is then answered with the works their own records sign, in the list's order.
    A batch whose list fails, or holds a work none of its records signs (a record merged
    into another), is not kept: its people are asked for one by one, and a failure is
    recorded with the person. Every other question goes to the source.
    """

    def __init__(self, source: OpenAlexSource) -> None:
        self.source = source
        self.label = source.label
        self._ids: frozenset[str] = frozenset()
        self._years: Years = None
        self._works: list[tuple[dict[str, Any], set[str]]] = []
        self._fetched: Fetched | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.source, name)

    def load(self, author_ids: Iterable[str], years: Years) -> None:
        """Ask for the works of *author_ids* within *years* in one list (not for one record)."""
        self._ids, self._works, self._fetched = frozenset(), [], None
        ids = frozenset(a for a in author_ids if a)
        if len(ids) < 2:
            return
        try:
            fetched = self.source.works_by_authors(sorted(ids), years)
        except (ServiceError, CacheMiss):
            return
        works = [(w, _work_authors(w) & ids) for w in fetched.data]
        if any(not signed for _w, signed in works):
            return
        self._ids, self._years, self._works, self._fetched = ids, years, works, fetched

    def works_by_authors(self, author_ids: Sequence[str], years: Years) -> Fetched:
        wanted = {a for a in author_ids if a}
        if self._fetched is None or not wanted <= self._ids or years != self._years:
            return self.source.works_by_authors(author_ids, years)
        return replace(self._fetched, data=[w for w, signed in self._works if signed & wanted])


def _openalex_ids(records: Sequence[str]) -> list[str]:
    return [r.split(":", 1)[1] for r in records if r.startswith("openalex:")]


def _targets(project: Project, people: Sequence[str] | None) -> list[tuple[dict, list[str]]]:
    rows = read_source_table(project.layout.table("people"), "people").to_pylist()
    decisions = read_people(project.layout)
    roots = merge_roots(decisions)
    groups = merged_groups(roots)
    wanted = set(people) if people is not None else None
    out = []
    for row in rows:
        pid = row["person_id"]
        dec = decisions.get(pid, {})
        if wanted is not None and pid not in wanted:
            continue
        if pid in roots or dec.get("role") == "excluded":
            continue
        # A person's records are theirs and those of the rows merged into them, each
        # counted when its row's identity was accepted.
        records: list[str] = []
        for one in (pid, *groups.get(pid, ())):
            row_dec = decisions.get(one, {})
            if row_dec.get("identity") not in ("confirmed", "auto"):
                continue
            records.extend(
                r
                for r in (row_dec.get("records") or "").split(";")
                if r.startswith(("openalex:", "orcid:")) and r not in records
            )
        if records:
            out.append((row, records))
    return out


def harvest(
    project: Project,
    client: HttpClient,
    *,
    people: Sequence[str] | None = None,
    years: tuple[int | None, int | None] | None = None,
    slot: str | None = None,
    now: datetime | None = None,
    source: OpenAlexSource | None = None,
    batch: int = AUTHOR_BATCH,
    resume: bool = False,
    chunk_people: int = CHUNK_PEOPLE,
    chunk_seconds: float = CHUNK_SECONDS,
    jobs: int | None = None,
) -> HarvestReport:
    """Collect the works of every confirmed person (or of *people*) and rebuild the tables.

    *years* is the window ``(first, last)``, inclusive, either end open when
    ``None``; by default the slot's ``years``. *source* answers the OpenAlex
    requests (default: the API through *client*; a
    :class:`~cartolex.collect.snapshot.SnapshotSource` reads a snapshot); the
    registry is always asked through *client*. From a source asked request by
    request, the works of up to *batch* records of consecutive people are asked
    for in one list (``1``: each person's on their own). A person whose collection
    fails is recorded and the others go on.

    A run is written every *chunk_people* people or *chunk_seconds* seconds, with a
    checkpoint of the people done. A cancel, or :data:`MAX_FAILURES_IN_A_ROW` failures
    in a row, closes the run being written and raises
    :class:`~cartolex.project.checkpoints.JobPaused` (the person being harvested when it
    came is left out whole); *resume* goes on from that checkpoint with the people not
    yet done. A source read in passes (a snapshot) keeps what its passes found for a
    resumed job too, when it can (``keep_findings``).

    From a source that gathers a person's records without parsing them (a snapshot:
    ``person_inputs``), people are put together in *jobs* worker processes (by default,
    every processor but two, from :data:`PARALLEL_FROM` people on); the runs are the same.
    """
    now = now or datetime.now(timezone.utc)
    layout = project.layout
    if not layout.table("people").exists():
        raise FileNotFoundError("the project has no people yet: import a list first")
    slot = _collection_slot(project, slot, "collection")
    if years is None:
        years = slot_window(project.config, slot)
    targets = _targets(project, people)
    report = HarvestReport()
    source = source or OpenAlexApi(client)
    report.source = source.label
    window = list(years) if years else None
    # The work this harvest is: a resumed one must be asked the same.
    asked_for = [[p["person_id"], sorted(r)] for p, r in targets]
    key = work_key({"slot": slot, "years": window, "source": source.label, "people": asked_for})
    cp = Checkpoint(_checkpoint_folder(project, slot), CHECKPOINT_KIND, key)
    done: set[str] = set()
    if resume:
        saved = cp.load(now=now)
        done = set(saved.state.get("done") or ()) if saved is not None else set()
    else:
        cp.clear()
    report.resumed = len(done)
    options = {"people": list(people) if people else None, "years": window,
               "source": source.label}  # fmt: skip

    def checkpoint() -> None:
        cp.save({"options": options, "done": sorted(done), "runs": report.run_ids,
                 "total": len(targets)}, now=now)  # fmt: skip

    def paused(code: str, message: str, cause: BaseException | None = None) -> JobPaused:
        n = len(done)
        return JobPaused(code, f"{message}; {n} of {len(targets)} people are kept: resume to go on",
                         cp.id, params={"n": n, "total": len(targets)},
                         progress={"people": n, "total": len(targets)}, cause=cause)  # fmt: skip

    registry: dict[str, Any] = {}
    if hasattr(source, "keep_findings"):
        source.keep_findings(key, resume=resume)
    try:
        if hasattr(source, "prefetch") and targets:
            _prefetch(client, source, targets, registry)
    except Cancelled:
        checkpoint()
        raise paused("harvest_stopped", "the harvest stopped while reading the snapshot") from None
    batched = _BatchedWorks(source) if batch > 1 and not hasattr(source, "prefetch") else None
    starts: dict[int, list[str]] = {}
    if batched is not None:
        groups = [_openalex_ids(records) for _person, records in targets]
        starts = {b[0]: [a for i in b for a in groups[i]] for b in author_batches(groups, batch)}
    asked = batched if batched is not None else source
    chunk: dict[str, Any] = {}

    def open_chunk() -> None:
        oa = RawWriter(layout, slot, "openalex",
                       {"years": window, "people": {}, "source": source.label}, now=now)  # fmt: skip
        orcid = RawWriter(layout, slot, "orcid", {"years": window, "people": {}},
                          run_id=oa.run_id)  # fmt: skip
        chunk.update(oa=oa, orcid=orcid, people=[], failures=[], since=time.monotonic())

    def close_chunk(*, keep: bool = True) -> None:
        """Write the chunk's runs and failures, then the checkpoint (or drop them all)."""
        oa, orcid = chunk["oa"], chunk["orcid"]
        for writer in (oa, orcid):
            if keep and writer.header["people"]:
                writer.close()
            else:
                writer.discard()
        if not keep:
            return
        if oa.header["people"]:
            report.run_ids.append(oa.run_id)
            report.run_id = report.run_id or oa.run_id
        write_failures(layout, slot, "harvest", chunk["failures"], now=now)
        done.update(chunk["people"])
        checkpoint()

    open_chunk()
    in_a_row = 0
    started = time.monotonic()
    last_error: CollectError | None = None
    todo = [(k, p, r) for k, (p, r) in enumerate(targets) if p["person_id"] not in done]
    workers = jobs if jobs is not None else _workers(source, len(todo))
    if workers > 1 and hasattr(source, "person_inputs"):
        outcomes = _assembled_in_workers(client, source, todo, years, registry, report, workers)
    else:
        outcomes = _assembled_here(client, asked, todo, years, report, registry, batched, starts)
    try:
        for k, person, outcome in outcomes:
            pid = person["person_id"]
            client.check_cancel()
            _report_progress(client, k, len(targets), report, started, before=report.resumed)
            if outcome[0] == "error":
                exc = outcome[1]
                failure = failure_record(pid, "harvest", exc, now=now)
                report.failures.append(failure)
                chunk["failures"].append(failure)  # not done: a resumed harvest asks again
                in_a_row += 1
                last_error = exc
                if stops_the_job(client, exc) or in_a_row >= MAX_FAILURES_IN_A_ROW:
                    report.stopped = (
                        f"the harvest stopped after {in_a_row} failure(s) in a row; "
                        f"{len(targets) - k - 1} person(s) were not asked for"
                    )
                    break
                continue
            _, oa_lines, orcid_lines, meta, works = outcome
            in_a_row = 0
            for line in oa_lines:
                chunk["oa"].add_line(line)
            for line in orcid_lines:
                chunk["orcid"].add_line(line)
            chunk["oa"].header["people"][pid] = meta
            if meta["orcids"]:
                chunk["orcid"].header["people"][pid] = {"orcids": meta["orcids"]}
            chunk["people"].append(pid)
            report.people += 1
            report.works[pid] = works
            full = len(chunk["people"]) >= chunk_people
            if full or time.monotonic() - chunk["since"] >= chunk_seconds:
                close_chunk()
                open_chunk()
    except Cancelled:
        report.cancelled = True
    except BaseException:
        close_chunk(keep=False)  # the runs closed before are kept, with their checkpoint
        raise
    finally:
        outcomes.close()  # workers still busy are let go of
    close_chunk()
    if report.people:
        report.rebuild = rebuild_sources(layout, project.config)
    client.progress(1.0, "harvest done")
    if report.cancelled:
        raise paused("harvest_stopped", "the harvest was stopped; their works are kept")
    if report.stopped and last_error is not None:
        raise paused(
            "harvest_paused", f"{report.stopped} ({last_error})", cause=last_error
        ) from last_error
    cp.clear()
    if hasattr(source, "forget_findings"):
        source.forget_findings()
    return report


def _report_progress(
    client: HttpClient,
    done: int,
    total: int,
    report: HarvestReport,
    started: float,
    *,
    before: int = 0,
) -> None:
    """How far the harvest is: the people done, the texts received, the requests sent, and
    the time left at the pace so far (*before*: people done by a stopped run, not timed)."""
    elapsed = time.monotonic() - started
    texts = sum(report.works.values())
    client.progress(
        done / max(1, total),
        f"person {done + 1} of {total}, {texts} texts received",
        code="harvest_people",
        params={
            "n": done,
            "total": total,
            "texts": texts,
            "requests": sum(client.egress.requests.values()),
        },
        eta_s=(
            round(elapsed / (done - before) * (total - done))
            if done > before and elapsed >= 5
            else None
        ),
    )


def _workers(source: Any, people: int) -> int:
    if not hasattr(source, "person_inputs") or people <= PARALLEL_FROM:
        return 1
    return max(1, (os.cpu_count() or 2) - 2)


Outcome = tuple[int, dict[str, Any], tuple[Any, ...]]


def _assembled_here(
    client: HttpClient,
    source: Any,
    todo: Sequence[tuple[int, dict[str, Any], list[str]]],
    years: tuple[int | None, int | None] | None,
    report: HarvestReport,
    registry: dict[str, Any],
    batched: Any,
    starts: Mapping[int, list[str]],
) -> Iterator[Outcome]:
    """Each person's outcome, in order, put together in this process: ``("ok", run lines of
    OpenAlex, of the registry, the person's header, works)`` or ``("error", exception)``."""
    for k, person, records in todo:
        if batched is not None and k in starts:
            batched.load(starts[k], years)
        try:
            oa, orcid, meta = _harvest_person(
                client, source, person, records, years, report, registry
            )
        except (ServiceError, CacheMiss) as exc:
            yield k, person, ("error", exc)
            continue
        works = sum(1 for x in oa if x["type"] == "work")
        yield k, person, ("ok", [RawWriter.line(x) for x in oa],
                          [RawWriter.line(x) for x in orcid], meta, works)  # fmt: skip


def _assembled_in_workers(
    client: HttpClient,
    source: Any,
    todo: Sequence[tuple[int, dict[str, Any], list[str]]],
    years: tuple[int | None, int | None] | None,
    registry: dict[str, Any],
    report: HarvestReport,
    workers: int,
) -> Iterator[Outcome]:
    """The outcomes :func:`_assembled_here` gives, in the same order, the people put together
    in *workers* processes: this process gathers each one's records unparsed and asks the
    registry (whose failures are the person's), a worker parses them and writes the lines."""
    import multiprocessing
    from collections import deque
    from concurrent.futures import ProcessPoolExecutor

    from .snapshot import ignore_stop_signals

    context = multiprocessing.get_context("spawn")
    pool = ProcessPoolExecutor(workers, mp_context=context, initializer=ignore_stop_signals)
    queue: deque[tuple[list[tuple[Any, ...]], Any]] = deque()
    try:
        for start in range(0, len(todo), PARALLEL_BATCH):
            block, payloads = [], []
            for k, person, records in todo[start : start + PARALLEL_BATCH]:
                prepared = _person_payload(client, source, person, records, years, registry)
                block.append((k, person, prepared))
                if prepared[0] == "inputs":
                    payloads.append(prepared[1])
            queue.append((block, pool.submit(_assemble_batch, payloads)))
            while len(queue) > 2 * workers:
                yield from _given_back(queue.popleft(), report)
        while queue:
            yield from _given_back(queue.popleft(), report)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _person_payload(
    client: HttpClient,
    source: Any,
    person: dict[str, Any],
    records: list[str],
    years: tuple[int | None, int | None] | None,
    registry: dict[str, Any],
) -> tuple[str, Any]:
    """What a worker needs to put one person together, or the registry's failure."""
    orcids = [r.split(":", 1)[1] for r in records if r.startswith("orcid:")]
    declared: dict[str, Any] = {}
    full: dict[str, Any] = {}
    try:
        for orcid in orcids:
            got = registry[orcid] if orcid in registry else declared_works(client, orcid)
            declared[orcid] = got
            if got is not None:
                full[orcid] = registry_record(client, orcid)
    except (ServiceError, CacheMiss) as exc:
        return "error", exc
    dois = {w.doi for got in declared.values() if got is not None for w in got[0] if w.doi}
    inputs = source.person_inputs(_openalex_ids(records), dois, years)
    return "inputs", {"person": person, "records": records, "years": years,
                      "declared": declared, "full": full, "inputs": inputs}  # fmt: skip


def _given_back(
    entry: tuple[list[tuple[Any, ...]], Any], report: HarvestReport
) -> Iterator[Outcome]:
    block, future = entry
    results = iter(future.result())
    for k, person, prepared in block:
        if prepared[0] == "error":
            yield k, person, prepared
            continue
        oa, orcid, meta, works, counts, notes = next(results)
        report.declared_dois += counts[0]
        report.out_of_window += counts[1]
        report.dois_not_indexed += counts[2]
        report.notes += notes
        yield k, person, ("ok", oa, orcid, meta, works)


def _assemble_batch(payloads: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """In a worker: each person of *payloads* put together, their run lines written."""
    from .snapshot import PackedSource

    out = []
    for payload in payloads:
        mine = HarvestReport()
        oa, orcid, meta = _harvest_person(
            None,  # type: ignore[arg-type]  (the registry was asked before)
            PackedSource(payload["inputs"]),
            payload["person"],
            payload["records"],
            payload["years"],
            mine,
            payload["declared"],
            registry_records=payload["full"],
        )
        works = sum(1 for x in oa if x["type"] == "work")
        counts = (mine.declared_dois, mine.out_of_window, mine.dois_not_indexed)
        out.append(([RawWriter.line(x) for x in oa], [RawWriter.line(x) for x in orcid], meta,
                    works, counts, mine.notes))  # fmt: skip
    return out


def _prefetch(
    client: HttpClient,
    source: Any,
    targets: list[tuple[dict, list[str]]],
    registry: dict[str, Any],
) -> None:
    """For a source read in passes (the snapshot): the registry first, for the DOIs people
    declared, then one pass for every record and work of the job."""
    author_ids: list[str] = []
    dois: set[str] = set()
    for _person, records in targets:
        for record in records:
            scheme, value = record.split(":", 1)
            if scheme == "openalex":
                author_ids.append(value)
            elif scheme == "orcid" and value not in registry:
                client.check_cancel()
                try:
                    registry[value] = declared_works(client, value)
                except (ServiceError, CacheMiss):
                    continue  # asked again with the person, where a failure is recorded
                if registry[value] is not None:
                    dois |= {w.doi for w in registry[value][0] if w.doi}
    source.prefetch(author_ids=author_ids, dois=dois)


def _harvest_person(
    client: HttpClient,
    source: OpenAlexSource,
    person: dict[str, Any],
    records: list[str],
    years: tuple[int | None, int | None] | None,
    report: HarvestReport,
    registry: dict[str, Any],
    registry_records: Mapping[str, Any] | None = None,
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    pid = person["person_id"]
    oa_ids = _openalex_ids(records)
    orcids = [r.split(":", 1)[1] for r in records if r.startswith("orcid:")]
    names = [[person["last_name"], person["first_name"] or ""]] + [
        [a["last_name"], a["first_name"] or ""] for a in person.get("aliases") or []
    ]
    meta: dict[str, Any] = {
        "records": records,
        "names": names,
        "orcid": person.get("orcid") or (orcids[0] if orcids else None),
        "orcids": orcids,
        "dois": [],
    }
    oa_lines: list[dict] = []
    orcid_lines: list[dict] = []
    for aid in oa_ids:
        fetched = source.author(aid)
        if fetched is None:
            report.notes.append(f"{pid}: the record {aid} no longer exists")
            continue
        oa_lines.append(
            {
                "type": "author",
                "person_id": pid,
                "retrieved_at": iso(fetched.retrieved_at),
                "record": fetched.data,
            }
        )
    if oa_ids:
        fetched = source.works_by_authors(oa_ids, years)
        for work in fetched.data:
            oa_lines.append(
                {
                    "type": "work",
                    "via": "openalex",
                    "person_id": pid,
                    "retrieved_at": iso(fetched.retrieved_at),
                    "record": work,
                }
            )
    for orcid in orcids:
        got = registry[orcid] if orcid in registry else declared_works(client, orcid)
        if got is None:
            report.notes.append(f"{pid}: the registry has no record for {orcid}")
            continue
        declared, fetched = got
        orcid_lines.append(
            {
                "type": "works",
                "person_id": pid,
                "orcid": orcid,
                "retrieved_at": iso(fetched.retrieved_at),
                "record": fetched.data,
            }
        )
        if registry_records is not None and orcid in registry_records:
            rec = registry_records[orcid]
        else:
            rec = registry_record(client, orcid)
        if rec is not None:
            orcid_lines.append(
                {
                    "type": "record",
                    "person_id": pid,
                    "orcid": orcid,
                    "retrieved_at": iso(rec.retrieved_at),
                    "record": rec.data,
                }
            )
        dois = sorted({w.doi for w in declared if w.doi})
        meta["dois"] += [d for d in dois if d not in meta["dois"]]
        report.declared_dois += len(dois)
        found = set()
        for work, answer in source.works_by_dois(dois):
            doi = bare_doi(work.get("doi"))
            found.add(doi)
            if not _in_window(work, years):
                report.out_of_window += 1
                continue
            oa_lines.append(
                {
                    "type": "work",
                    "via": "orcid",
                    "person_id": pid,
                    "retrieved_at": iso(answer.retrieved_at),
                    "record": work,
                }
            )
        report.dois_not_indexed += len(set(dois) - found)
    # What the coverage says of a person without texts, read from the run's header: the
    # works their records hold in the index, and the works received.
    meta["works"] = [
        sum(
            int((x["record"] or {}).get("works_count") or 0)
            for x in oa_lines
            if x["type"] == "author"
        ),
        sum(1 for x in oa_lines if x["type"] == "work"),
    ]
    return oa_lines, orcid_lines, meta


# ── readers ──────────────────────────────────────────────────────────────────


def current_runs(runs: list[RawRun]) -> set[str]:
    """The ids of the runs that are some person's latest (the others are superseded whole)."""
    latest: dict[str, str] = {}
    for run in runs:
        for pid in run.header.get("people") or {}:
            latest[pid] = run.run_id
    return set(latest.values())


def _current(runs: list[RawRun]) -> tuple[dict[str, dict], dict[str, str]]:
    """Each person's latest run's header entry, and the id of that run."""
    latest: dict[str, str] = {}
    meta: dict[str, dict] = {}
    for run in runs:
        for pid, entry in (run.header.get("people") or {}).items():
            latest[pid] = run.run_id
            meta[pid] = entry
    return meta, latest


#: A run heavier than this is read in worker processes (when the rebuild has several).
PARALLEL_BYTES = 64 << 20
#: Bytes of whole lines a worker reads at once.
_BLOCK = 8 << 20

_Maps = tuple[
    Mapping[str, set[str]],
    Mapping[str, list[str]],
    Mapping[str, "str | None"],
    Mapping[str, list[tuple[str, str]]],
]
_READING: dict[str, Any] = {}


def _set_reading(latest: Mapping[str, str], maps: _Maps | None) -> None:
    """In a worker: what every block needs, sent once."""
    _READING.update(latest=latest, maps=maps)


def _kept_line(rec: dict, maps: _Maps | None) -> dict:
    if maps is not None and rec.get("type") == "work":
        return _placed_work(rec, *maps)
    return rec


def _read_block(task: tuple[str, bytes]) -> list[tuple[str, dict]]:
    """In a worker: the lines of a block of run *run_id* that belong to their person's latest
    run, each work made smaller (:func:`_placed_work`)."""
    run_id, block = task
    latest, maps = _READING["latest"], _READING["maps"]
    out = []
    # Lines end at "\n" only: a record may hold other line separators (U+2028…) unescaped.
    for line in block.decode("utf-8").split("\n"):
        if not line.strip():
            continue
        rec = json.loads(line)
        who = rec.get("person_id")
        if latest.get(who) == run_id:
            out.append((who, _kept_line(rec, maps)))
    return out


def _run_lines(
    run: RawRun, latest: Mapping[str, str], maps: _Maps | None, jobs: int
) -> Iterator[tuple[str, dict]]:
    """``(person, line)`` for the lines of *run* that are their person's latest, in order;
    a heavy run read in *jobs* worker processes."""
    from cartolex.scale import ordered_map

    from .digests import DIGESTERS, _blocks

    digests = run.digests
    fresh = digests is not None and run.kind in DIGESTERS and digests.fresh(run)
    path = digests.path(run) if fresh else run.path
    if jobs <= 1 or path.stat().st_size < PARALLEL_BYTES:
        for rec in run.records():
            who = rec.get("person_id")
            if latest.get(who) == run.run_id:
                yield who, _kept_line(rec, maps)
        return
    if not fresh and digests is not None and run.kind in DIGESTERS:
        yield from _run_lines(run, latest, maps, 1)  # digested as it is read
        return
    blocks = _blocks(path, _BLOCK)
    next(blocks, None)  # the header
    tasks = ((run.run_id, block) for block in blocks)
    for lines in ordered_map(
        _read_block, tasks, workers=jobs, initializer=_set_reading, initargs=(latest, maps)
    ):
        yield from lines


def _current_lines(
    runs: list[RawRun],
    latest: Mapping[str, str],
    maps: _Maps | None = None,
    jobs: int = 1,
) -> Iterator[tuple[str, list[tuple[RawRun, dict]]]]:
    """Each person's lines of their latest run, a person at a time: runs in time order, a
    run's people in the order it holds them (a harvest writes each person's lines together).
    Only one person's lines are in memory at once, each work with only its project people
    when *maps* are given (:func:`_placed_work`)."""
    current = set(latest.values())
    for run in runs:
        if run.run_id not in current:
            continue
        pid, lines = None, []
        for who, rec in _run_lines(run, latest, maps, jobs):
            if who != pid and lines:
                yield pid, lines
                lines = []
            pid = who
            lines.append((run, rec))
        if lines:
            yield pid, lines


def _org(builder: SourceBuilder, slot: str, inst: dict[str, Any], at: datetime) -> str | None:
    seen = builder.memo.setdefault("openalex-institutions", {})
    lineage = inst.get("lineage") or []
    known = (slot, inst.get("id"), inst.get("display_name"), inst.get("ror"),
             inst.get("country_code"), *lineage)  # fmt: skip
    args = seen.get(known)
    if args is None:  # the same institution comes back on many authorships: read it once
        iid = short_id(inst.get("id"))
        name = inst.get("display_name")
        if not iid or not name:
            seen[known] = args = {}
        else:
            ids = {"openalex": iid}
            if inst.get("ror"):
                ids["ror"] = str(inst["ror"]).rsplit("/", 1)[-1]
            parents = [short_id(x) for x in lineage]
            seen[known] = args = {
                "keys": [f"openalex:{iid}"],
                "name": name,
                "ids": ids,
                "country": inst.get("country_code") or None,
                "parent_keys": [f"openalex:{p}" for p in parents if p and p != iid],
            }
    if not args:
        return None
    return builder.organisation(slot=slot, source="openalex", retrieved_at=at, **args)


def _languages(title: str, abstract: str, declared: str | None) -> tuple[str, str]:
    """The language of the title and of the abstract: the abstract is detected; the title, too
    short to tell alone, takes the abstract's; the service's own language fills a gap."""
    declared = (declared or "").lower()
    fallback = declared if declared in DETECTED_LANGUAGES else "und"
    lang_abstract = detect_language(abstract) if abstract else "und"
    if lang_abstract == "und":
        lang_abstract = fallback
    if abstract:
        return lang_abstract, lang_abstract
    lang_title = detect_language(title)
    return (lang_title if lang_title != "und" else fallback), lang_abstract


def _alphabetical(authorships: list[dict[str, Any]]) -> bool:
    """Four authors or more in alphabetical order of surname: first and last mean nothing."""
    if len(authorships) < 4:
        return False
    previous = None
    for a in authorships:  # in order, up to the first out of it (most lists stop at once)
        name = (a.get("author") or {}).get("display_name") or a.get("raw_author_name") or ""
        parts = words(name)
        surname = parts[-1] if parts else ""
        if previous is not None and surname < previous:
            return False
        previous = surname
    return True


def read_openalex_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """The reader of harvests: texts, parts, authorships, organisations, dated affiliations.

    The records are read a person at a time (:func:`_current_lines`): a harvest of
    millions of records is never held in memory."""
    meta, latest = _current(runs)
    owners: dict[str, set[str]] = defaultdict(set)
    for pid, entry in meta.items():
        for record in entry.get("records") or []:
            if record.startswith("openalex:"):
                owners[record.split(":", 1)[1]].add(pid)
    # Who declared each DOI, in person order: a work's DOI finds them at once.
    doi_owners: dict[str, list[str]] = defaultdict(list)
    for pid in sorted(meta):
        for doi in sorted(set(meta[pid].get("dois") or [])):
            doi_owners[doi].append(pid)
    orcid_of = {pid: entry.get("orcid") for pid, entry in meta.items()}
    names_of = {pid: [tuple(n) for n in entry.get("names") or []] for pid, entry in meta.items()}
    gone = {pid for pid in meta if not builder.known_person(pid)}
    for pid in sorted(gone):
        builder.warnings.append(f"{pid} is no longer in the tables; their harvest is left out")

    # Workers placing the people on each work need the registry only of who declared DOIs.
    declared = {p for pids in doi_owners.values() for p in pids}
    maps: _Maps = (
        owners,
        doi_owners,
        {p: orcid_of.get(p) for p in declared},
        {p: names_of.get(p, []) for p in declared},
    )
    jobs = getattr(builder, "jobs", 1)
    for pid, own in _current_lines(runs, latest, maps, jobs):
        if pid in gone:
            continue
        for run, rec in own:
            if rec["type"] != "author":
                continue
            at = parse_time(rec["retrieved_at"])
            for aff in rec["record"].get("affiliations") or []:
                oid = _org(builder, run.slot, aff.get("institution") or {}, at)
                years = [y for y in aff.get("years") or [] if isinstance(y, int)]
                if oid:
                    builder.affiliation(
                        pid,
                        oid,
                        min(years) if years else None,
                        max(years) if years else None,
                        "openalex",
                    )
        works = [(run, rec) for run, rec in own if rec["type"] == "work"]
        works.sort(key=lambda x: (not x[1]["record"].get("doi"), x[1]["record"].get("id") or ""))
        for run, rec in works:
            tid = _text(builder, run.slot, pid, rec)
            if tid is not None:
                _link(builder, run.slot, tid, rec)
                builder.count("works read")


def work_text(work: dict[str, Any]) -> dict[str, Any]:
    """A work's title and abstract as a text holds them: markup stripped, the abstract rebuilt
    from the inverted index, each part's language (the costly part of reading a harvest,
    kept in the run's digest, :mod:`cartolex.collect.digests`)."""
    title, title_format = strip_markup(work.get("title") or work.get("display_name") or "")
    error = False
    try:
        raw_abstract = abstract_from_inverted_index(work.get("abstract_inverted_index"))
    except ValueError:
        error = True
        raw_abstract = ""
    abstract, abstract_format = strip_markup(raw_abstract)
    lang_title, lang_abstract = (
        _languages(title, abstract, work.get("language")) if title else ("und", "und")
    )
    return {
        "title": title,
        "title_format": title_format,
        "abstract": abstract,
        "abstract_format": abstract_format,
        "abstract_error": error,
        "lang_title": lang_title,
        "lang_abstract": lang_abstract,
    }


def _text(builder: SourceBuilder, slot: str, pid: str, rec: dict[str, Any]) -> str | None:
    work = rec["record"]
    at = parse_time(rec["retrieved_at"])
    text = rec.get("text") or work_text(work)
    title, title_format = text["title"], text["title_format"]
    wid = short_id(work.get("id"))
    if not title or not wid:
        builder.warnings.append(f"a work without a title or an id was left out ({wid})")
        return None
    if text["abstract_error"]:
        builder.warnings.append(f"{wid}: its abstract could not be rebuilt; left out")
    abstract, abstract_format = text["abstract"], text["abstract_format"]
    doi = bare_doi(work.get("doi"))
    year = work.get("publication_year") if isinstance(work.get("publication_year"), int) else None
    keys = ([f"doi:{doi}"] if doi else []) + [
        f"openalex:{wid}",
        f"title:{pid}:{' '.join(words(title))}|{year}",
    ]
    lang_title, lang_abstract = text["lang_title"], text["lang_abstract"]
    tid = builder.text(
        slot=slot,
        keys=keys,
        title=title,
        doc_type=doc_type(work),
        source="openalex" if rec.get("via") == "openalex" else "orcid",
        retrieved_at=at,
        year=year,
        date=work.get("publication_date") or None,
        doi=doi,
        ids={"openalex": wid},
        n_authors=rec["authors"]["n"],
    )
    builder.part(
        tid,
        part="title",
        language=lang_title,
        provider="openalex",
        content=title,
        format=title_format,
        retrieved_at=at,
    )
    builder.part(
        tid,
        part="abstract",
        language=lang_abstract,
        provider="openalex",
        content=abstract,
        format=abstract_format,
        retrieved_at=at,
    )
    return tid


_AUTHOR_URL = "https://openalex.org/A"


def _placed_work(
    rec: dict[str, Any],
    owners: Mapping[str, set[str]],
    doi_owners: Mapping[str, list[str]],
    orcid_of: Mapping[str, str | None],
    names_of: Mapping[str, list[tuple[str, str]]],
) -> dict[str, Any]:
    """A work's line with its authorships replaced by the project people on it, at their
    rank: by their records, else by their registry (the work's DOI declared, and the ORCID
    or the name on the authorship). A work can have thousands of authors; only the
    authorships of project people are kept, so a person's works hold little memory."""
    work = rec["record"]
    auths = work.get("authorships") or []
    doi = bare_doi(work.get("doi"))
    at_rank: dict[str, int] = {}
    for k, a in enumerate(auths, start=1):
        aid = (a.get("author") or {}).get("id")
        if not aid:
            continue
        # Most ids are written in full (``https://openalex.org/A123``): looked up as they are.
        if aid.startswith(_AUTHOR_URL) and aid[len(_AUTHOR_URL) :].isdigit():
            mine = owners.get(aid[len(_AUTHOR_URL) - 1 :])
        else:
            mine = owners.get(short_id(aid) or "")
        for pid in sorted(mine or ()):
            at_rank.setdefault(pid, k)
    missing: list[str] = []
    if doi:
        for pid in doi_owners.get(doi, ()):
            if pid in at_rank:
                continue
            for k, a in enumerate(auths, start=1):
                shown = a.get("author") or {}
                orcid = (shown.get("orcid") or "").rsplit("/", 1)[-1] or None
                name = shown.get("display_name") or a.get("raw_author_name") or ""
                if (orcid and orcid == orcid_of.get(pid)) or name_similarity(
                    names_of.get(pid, []), name
                )[0] >= 0.75:
                    at_rank[pid] = k
                    break
            else:
                missing.append(pid)
    placed = sorted(at_rank.items(), key=lambda kv: kv[1])
    authors = {
        "n": len(auths),
        "unknown_last": bool(work.get("is_authors_truncated")) or _alphabetical(auths),
        "flagged": any(a.get("is_corresponding") for a in auths),
        "placed": placed,
        "missing": missing,
        "at": {
            k: {
                "institutions": auths[k - 1].get("institutions") or [],
                "is_corresponding": auths[k - 1].get("is_corresponding"),
            }
            for _pid, k in placed
        },
    }
    slim = {key: value for key, value in work.items() if key != "authorships"}
    return {**rec, "record": slim, "authors": authors}


def _link(builder: SourceBuilder, slot: str, tid: str, rec: dict[str, Any]) -> None:
    """Every project person on this work (:func:`_placed_work`), at their rank."""
    work = rec["record"]
    authors = rec["authors"]
    at = parse_time(rec["retrieved_at"])
    year = work.get("publication_year") if isinstance(work.get("publication_year"), int) else None
    for pid in authors["missing"]:
        builder.warnings.append(
            f"{pid}: a work declared in the registry does not show them among its authors"
        )
    for pid, k in authors["placed"]:
        if not builder.known_person(pid):
            continue
        a = authors["at"][k]
        orgs = [o for o in (_org(builder, slot, i, at) for i in a["institutions"]) if o]
        builder.authorship(
            tid,
            pid,
            position=k,
            orgs=orgs,
            last=None if authors["unknown_last"] else k == authors["n"],
            corresponding=bool(a["is_corresponding"]) if authors["flagged"] else None,
        )
        for oid in orgs:
            builder.affiliation(pid, oid, year, year, "stated")


def read_orcid_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """The reader of registry answers: the employments people declared, as dated affiliations
    to the organisations of the same name they are already affiliated with."""
    meta, latest = _current(runs)
    for pid, own in _current_lines(runs, latest, None, getattr(builder, "jobs", 1)):
        if not builder.known_person(pid):
            continue
        for _run, rec in own:
            if rec["type"] != "record":
                continue
            for emp in employments(rec["record"]):
                target = words(emp.organisation)
                mine = [
                    (source != "openalex", oid)
                    for oid, name, source in builder.affiliated_orgs(pid)
                    if words(name) == target
                ]
                oid = min(mine)[1] if mine else builder.org_by_name(emp.organisation)
                if oid is None:
                    builder.count("employments without a known organisation")
                    continue
                builder.affiliation(pid, oid, emp.start, emp.end, "orcid")
