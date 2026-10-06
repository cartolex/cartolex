# SPDX-License-Identifier: MIT
"""Running a build: stage contexts, progress, consent, cancel, and the safe swap of results.

:func:`build` computes the :func:`~cartolex.build.plan` and runs exactly the
stages it says will run, one at a time, in order. Each stage runs in its own
staging folder and is swapped into place only when it succeeds
(:mod:`cartolex.project.generations`); a failure or a cancel removes the staging
folder and changes nothing else. Before anything runs, the build refuses the
stages that exceed the memory budget (unless allowed) and asks consent for each
stage that reaches the network or costs money; a refused stage does not run,
nor does anything that depends on it.

Progress is reported through a callback as « phase k of n », never backwards,
with a heartbeat at least every ten seconds. Cancel is cooperative: an event
checked between chunks and between stages. A cancelled build ends in « nothing
changed » or « finished before the cancel ».
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..project.files import StaleWrite, atomic_write_bytes, json_bytes, replace_path
from ..project.generations import CHUNKS, STAGING_MARKER, recover, remove_tree, swap_in
from ..project.models import Measures, RunRecord
from .machine import PeakMemory, boot_id
from .params import ProjectSizes
from .planning import BuildPlan, PlanItem, RunInputs, plan, run_inputs
from .records import marker_bytes, new_run_id, record_bytes, scan_staging, write_attempt
from .stages import STAGES, Estimate, Registry, Stage
from .validity import _View

if TYPE_CHECKING:
    from ..project.layout import ProjectLayout
    from ..project.project import Project

__all__ = [
    "BuildResult",
    "Cancelled",
    "ConsentRequest",
    "Progress",
    "StageContext",
    "StageRefused",
    "build",
]

#: The longest silence between two progress events, in seconds.
MAX_HEARTBEAT_S = 10.0

#: A job id names a file of ``logs/jobs/``: letters, digits, ``-`` and ``_``.
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now() -> datetime:
    """The UTC time, to the second (as run records show it)."""
    return datetime.now(timezone.utc).replace(microsecond=0)


class Cancelled(Exception):
    """The build was cancelled; raised inside a stage at a cancel check."""


class StageRefused(RuntimeError):
    """A stage cannot run on this project: an impossible parameter, a missing upstream result."""


# ── progress ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Progress:
    """One progress event: « phase k of n », how far the stage and the build are, and a message.

    ``fraction`` (the whole build) never decreases, nor does ``stage_fraction``
    within a phase. A ``heartbeat`` repeats the last event to say the build is alive.
    """

    phase: int
    phases: int
    stage: str
    name: str
    stage_fraction: float
    fraction: float
    message: str
    elapsed_s: float
    heartbeat: bool = False

    def __str__(self) -> str:
        text = f"phase {self.phase} of {self.phases}: {self.name} ({self.stage_fraction:.0%})"
        return text + (f", {self.message}" if self.message else "")


class _Reporter:
    """Sends progress events, keeps them monotonic, and beats while a stage is silent."""

    def __init__(
        self,
        callback: Callable[[Progress], None] | None,
        weights: list[float],
        heartbeat_s: float,
    ) -> None:
        self._callback = callback
        self._weights = weights
        self._total = sum(weights) or 1.0
        self._heartbeat_s = heartbeat_s
        self._lock = threading.Lock()
        self._last: Progress | None = None
        self._last_sent = time.monotonic()
        self._t0 = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: BaseException | None = None

    def start(self) -> None:
        if self._callback is None:
            return
        self._thread = threading.Thread(target=self._beat, name="cartolex-heartbeat", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _beat(self) -> None:
        tick = min(self._heartbeat_s / 4, 0.5)
        while not self._stop.wait(tick):
            with self._lock:
                if self._last is None or time.monotonic() - self._last_sent < self._heartbeat_s:
                    continue
                event = replace(self._last, heartbeat=True, elapsed_s=self._elapsed())
                try:
                    self._send(event)
                except Exception as exc:  # the caller's callback; reported, never fatal here
                    self.error = self.error or exc

    def _elapsed(self) -> float:
        return round(time.monotonic() - self._t0, 3)

    def _send(self, event: Progress) -> None:
        self._last = event
        self._last_sent = time.monotonic()
        if self._callback is not None:
            self._callback(event)

    def update(
        self,
        phase: int,
        stage: Stage,
        stage_fraction: float,
        message: str = "",
    ) -> None:
        with self._lock:
            if self.error is not None:  # the callback failed in the heartbeat thread
                error, self.error = self.error, None
                raise error
            last = self._last
            if last is not None and phase < last.phase:
                return
            f = min(1.0, max(0.0, float(stage_fraction)))
            if last is not None and phase == last.phase:
                f = max(f, last.stage_fraction)
            done = sum(self._weights[: phase - 1])
            overall = (done + self._weights[phase - 1] * f) / self._total
            if last is not None:
                overall = max(overall, last.fraction)
            event = Progress(
                phase=phase,
                phases=len(self._weights),
                stage=stage.id,
                name=stage.name,
                stage_fraction=round(f, 4),
                fraction=round(min(1.0, overall), 4),
                message=message,
                elapsed_s=self._elapsed(),
            )
            self._send(event)


# ── the stage context ────────────────────────────────────────────────────────


class StageContext:
    """What a stage's runner gets: where to write, what to read, its parameters, and the build.

    A runner writes its results into :attr:`out` (its staging folder) and reads
    the results of the stages before it (directly upstream or not) from
    :meth:`folder`, and their run records from :meth:`record`. It reports
    progress with :meth:`progress`, checks for a cancel with
    :meth:`check_cancel` (or polls :attr:`cancel_requested`), and a long stage
    loops over :meth:`chunks` so a killed run resumes from its last chunk. It
    returns counts to record (``{"candidates_en": 5214}``), or puts them in
    :attr:`counts`.
    """

    def __init__(
        self,
        *,
        project: Project,
        stage: Stage,
        run_id: str,
        out: Path,
        params: dict[str, Any],
        sizes: ProjectSizes,
        identity: dict[str, Any],
        upstream: Mapping[str, Path],
        report: Callable[[float, str], None],
        cancel: threading.Event | None,
        probe: Callable[[str], None],
        records: Mapping[str, RunRecord] | None = None,
    ) -> None:
        self.project = project
        self.stage = stage
        self.run_id = run_id
        self.out = out
        self.params = params
        self.sizes = sizes
        self.identity = identity
        self.upstream = dict(upstream)
        self.records = dict(records or {})
        self.counts: dict[str, int] = {}
        self.warnings: list[str] = []
        self._report = report
        self._cancel = cancel
        self._probe = probe

    @property
    def layout(self) -> ProjectLayout:
        return self.project.layout

    def folder(self, stage_id: str) -> Path:
        """The results of *stage_id*: this stage's staging folder, or an upstream stage's results."""
        if stage_id == self.stage.id:
            return self.out
        try:
            return self.upstream[stage_id]
        except KeyError:
            raise KeyError(
                f"{self.stage.id} does not read {stage_id!r} (upstream here: {sorted(self.upstream)})"
            ) from None

    def record(self, stage_id: str) -> RunRecord | None:
        """The run record of the results of *stage_id* this run reads (``None``: none)."""
        return self.records.get(stage_id)

    @property
    def cancel_requested(self) -> bool:
        """Whether the build was asked to stop."""
        return self._cancel is not None and self._cancel.is_set()

    def progress(self, fraction: float, message: str = "") -> None:
        """Report how far this stage is, from 0 to 1 (a smaller value than before is ignored)."""
        self._report(fraction, message)

    def check_cancel(self) -> None:
        """Raise :class:`Cancelled` when the build was asked to stop."""
        if self._cancel is not None and self._cancel.is_set():
            raise Cancelled(f"{self.stage.id} was cancelled")

    def warn(self, message: str) -> None:
        """Record a warning in the run's ``run.json``."""
        self.warnings.append(str(message))

    def chunk_dir(self, index: int) -> Path:
        """A folder for chunk *index*'s own files, emptied before the chunk (re)starts."""
        path = self.out / CHUNKS / f"{index:06d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def chunks(self, total: int) -> Iterator[int]:
        """Yield the chunks still to do, 0 to *total* − 1, checkpointing each one done.

        A chunk counts as done when the loop body finishes and asks for the next
        one. A killed run resumes after its last done chunk; a cancel is checked
        before each chunk.
        """
        folder = self.out / CHUNKS
        meta = folder / "chunks.json"
        try:
            known = json.loads(meta.read_text(encoding="utf-8")).get("total")
        except (OSError, ValueError, AttributeError):
            known = None
        if known != total:
            remove_tree(folder)
            folder.mkdir(parents=True)
            atomic_write_bytes(meta, json_bytes({"total": total}))
        done = {i for i in range(total) if (folder / f"{i:06d}.done").exists()}
        if done:
            self.warn(f"resumed a killed run: {len(done)} of {total} chunks were already done")
        for i in range(total):
            if i in done:
                continue
            self.check_cancel()
            remove_tree(folder / f"{i:06d}")
            self.progress(len(done) / total if total else 1.0)
            self._probe(f"chunk:{self.stage.id}:{i}")
            yield i
            # Like the chunk's own files, a checkpoint is not forced to disk: it survives a
            # killed process, and a run resumes only on the same boot of the machine.
            (folder / f"{i:06d}.done").touch()
            done.add(i)
        self.progress(1.0)


# ── consent and the result ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ConsentRequest:
    """What the build asks before a stage that reaches the network or costs money."""

    stage: str
    name: str
    network: bool
    paid: bool
    note: str
    estimate: Estimate | None

    def __str__(self) -> str:
        what = " and ".join(
            w for w, on in (("reaches the network", self.network), ("costs money", self.paid)) if on
        )
        return f"{self.name} ({self.stage}) {what}: {self.note}"


@dataclass(frozen=True)
class BuildResult:
    """What a build did: the runs swapped into place, what it refused, and how it ended."""

    plan: BuildPlan
    outcome: Literal["succeeded", "failed", "cancelled"]
    ran: tuple[RunRecord, ...]
    refused: dict[str, str] = field(default_factory=dict)
    failed: tuple[str, str] | None = None
    not_run: tuple[str, ...] = ()
    job_id: str = ""

    @property
    def ran_ids(self) -> tuple[str, ...]:
        return tuple(r.stage for r in self.ran)

    @property
    def changed(self) -> bool:
        """Whether any stage's results were replaced."""
        return bool(self.ran)

    def summary(self) -> str:
        """How the build ended, in words."""
        done = ", ".join(self.ran_ids)
        refused = (
            "; not run: " + "; ".join(f"{s} ({why})" for s, why in self.refused.items())
            if self.refused
            else ""
        )
        if self.outcome == "cancelled":
            if not self.ran:
                return "cancelled: nothing changed" + refused
            return f"cancelled: finished before the cancel: {done}; nothing else changed" + refused
        if self.outcome == "failed":
            stage, error = self.failed or ("?", "")
            before = f"finished before: {done}; " if self.ran else "nothing changed; "
            return f"{stage} failed ({error}); {before}its previous results are kept" + refused
        if not self.ran:
            return ("nothing to run" if self.refused else "everything is up to date") + refused
        return f"built: {done}" + refused


class _JobLog:
    """``logs/jobs/<job id>.jsonl``: one line per event, stage names, counts and times only."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")  # noqa: SIM115 - closed in close()

    def write(self, event: str, **data: Any) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self._fh.write(json.dumps({"at": stamp, "event": event, **data}) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


# ── running one stage ────────────────────────────────────────────────────────


def _attempt(
    stage: Stage,
    run_id: str,
    started: datetime,
    inputs: RunInputs,
    outcome: Literal["failed", "cancelled"],
    error: str,
) -> RunRecord:
    return RunRecord(
        stage=stage.id,
        run_id=run_id,
        outcome=outcome,
        started_at=started,
        finished_at=_now(),
        code=inputs.code,
        parameters=inputs.resolved.values,
        inputs=[*inputs.stages, *inputs.files],
        identity=inputs.identity,
        error=error,
    )


def _prepare_staging(
    project: Project, stage: Stage, run_id: str, key: str, started: datetime
) -> Path:
    """The staging folder of a new run: a killed run's folder when it can resume, else a new one."""
    layout = project.layout
    target = layout.staging(stage.id, run_id)
    reuse = None
    for folder in scan_staging(layout).get(stage.id, []):
        if folder.running:
            raise RuntimeError(f"{stage.id} is already running (run {folder.run_id})")
        if reuse is None and stage.chunked and folder.resumable(key):
            reuse = folder.path
        else:
            remove_tree(folder.path)
    for stray in layout.staging_root.glob(f"{stage.id}.*"):  # folders without a marker
        if stray.is_dir() and stray != reuse and not (stray / STAGING_MARKER).exists():
            remove_tree(stray)
    if reuse is not None:
        replace_path(reuse, target)
    else:
        target.mkdir(parents=True)
    lock = project.lock_info
    atomic_write_bytes(
        target / STAGING_MARKER,
        marker_bytes(
            stage=stage.id,
            run_id=run_id,
            key=key,
            started_at=started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            pid=lock.pid if lock else os.getpid(),
            host=lock.host if lock else socket.gethostname(),
            lock_since=lock.since if lock else None,
            boot=boot_id(),
        ),
    )
    return target


def _run_stage(
    project: Project,
    registry: Registry,
    stage: Stage,
    *,
    year: int | None,
    report: Callable[[float, str], None],
    cancel: threading.Event | None,
    probe: Callable[[str], None],
    off: frozenset[str] = frozenset(),
) -> RunRecord:
    layout = project.layout
    probe(f"stage:start:{stage.id}")
    prepared: list[str] = []
    failure: str | None = None
    if stage.prepare is not None:
        try:
            prepared = stage.prepare(project)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
    view = _View.read(project, registry, year, off)
    inputs = run_inputs(view, stage)
    started = _now()
    run_id = new_run_id(started)
    if failure is not None:
        write_attempt(layout, _attempt(stage, run_id, started, inputs, "failed", failure))
        raise StageRefused(failure)
    problems = [f"{up} has no results" for up in inputs.missing_upstream]
    problems += [
        f"{stage.id}.{name}: its rule needs {', '.join(needs)}, which no stage reported"
        for name, needs in inputs.resolved.unknown.items()
    ]
    problems += inputs.resolved.problems(stage, view.sizes, project.config)
    if problems:
        message = "; ".join(problems)
        write_attempt(layout, _attempt(stage, run_id, started, inputs, "failed", message))
        raise StageRefused(message)
    out = _prepare_staging(project, stage, run_id, inputs.key, started)
    probe(f"stage:staged:{stage.id}")
    readable = [
        u
        for u in registry.ids
        if u in registry.upstream_of(stage.id)
        and view.records.get(u) is not None
        and registry[u].skip_reason(project.config, view.params) is None
    ]
    upstream = {u: layout.stage(u) for u in readable}
    ctx = StageContext(
        project=project,
        stage=stage,
        run_id=run_id,
        out=out,
        params=inputs.resolved.plain(),
        sizes=view.sizes,
        identity=inputs.identity,
        upstream=upstream,
        report=report,
        cancel=cancel,
        probe=probe,
        records={u: view.records[u] for u in readable},  # type: ignore[misc]
    )
    for note in prepared:
        ctx.warn(note)
    t0 = time.monotonic()
    try:
        with PeakMemory() as peak:
            returned = stage.run(ctx)
    except (Cancelled, KeyboardInterrupt) as exc:
        remove_tree(out)
        write_attempt(layout, _attempt(stage, run_id, started, inputs, "cancelled", str(exc)))
        raise
    except BaseException as exc:
        remove_tree(out)
        error = f"{type(exc).__name__}: {exc}"
        write_attempt(layout, _attempt(stage, run_id, started, inputs, "failed", error))
        raise
    seconds = round(time.monotonic() - t0, 3)
    probe(f"stage:ran:{stage.id}")
    counts = {**ctx.counts, **{k: int(v) for k, v in (returned or {}).items()}}
    for name in stage.cost.sizes() if stage.cost is not None else ():
        if name not in counts:
            value = view.sizes.get(name)
            if value is not None:
                counts[name] = value
    record = RunRecord(
        stage=stage.id,
        run_id=run_id,
        outcome="succeeded",
        started_at=started,
        finished_at=_now(),
        code=inputs.code,
        parameters=inputs.resolved.values,
        inputs=[*inputs.stages, *inputs.files],
        identity=inputs.identity,
        measures=Measures(seconds=seconds, peak_memory_mb=peak.peak_mb, counts=counts),
        warnings=ctx.warnings,
    )
    swap_in(layout, stage.id, run_id, record_bytes(record), probe=probe)
    probe(f"stage:done:{stage.id}")
    return record


# ── the build ────────────────────────────────────────────────────────────────


def _allowed_over(allow: bool | Iterable[str], stage_id: str) -> bool:
    if isinstance(allow, bool):
        return allow
    return stage_id in set(allow)


def _freeze_identity(project: Project, reason: str, log: Any) -> None:
    """Freeze the project's identity (``docs/format/project-json.md``), logging a stale write."""
    try:
        project.freeze_identity(reason)
    except StaleWrite:
        log.write("warning", message=f"project.json changed: identity not frozen ({reason})")


def build(
    project: Project,
    targets: Iterable[str] | None = None,
    *,
    registry: Registry | None = None,
    force: Iterable[str] = (),
    budget_mb: float | None = None,
    allow_over_budget: bool | Iterable[str] = False,
    consent: Callable[[ConsentRequest], bool] | None = None,
    progress: Callable[[Progress], None] | None = None,
    cancel: threading.Event | None = None,
    heartbeat_s: float = 5.0,
    year: int | None = None,
    probe: Callable[[str], None] | None = None,
    job_id: str | None = None,
    memory_mb: float | None = None,
) -> BuildResult:
    """Run what :func:`~cartolex.build.plan` says, with the same arguments.

    The project must be open for writing. *allow_over_budget* lets stages
    (all, or the named ones) run although their estimate exceeds the memory
    budget. *consent* is asked, before anything runs, for each stage that
    reaches the network or costs money; without a callback, or without a yes,
    an opt-in stage is skipped as if switched off (the stages after it run
    without it) and any other such stage is refused. *progress* receives :class:`Progress` events, at least every
    *heartbeat_s* seconds (at most ten). *cancel* stops the build at the next
    chunk or stage. *probe*, for tests, is called with the name of each step.
    *job_id* names the job's log, ``logs/jobs/<job id>.jsonl`` (default: a new
    run id); a job runner passes its own id, so the log it started is the one
    the build appends to.
    """
    if not project.writable:
        raise PermissionError("a build writes: open the project with write=True")
    if not 0 < heartbeat_s <= MAX_HEARTBEAT_S:
        raise ValueError(f"heartbeat_s must be in (0, {MAX_HEARTBEAT_S:g}] seconds")
    if job_id is not None and not _JOB_ID.match(job_id):
        raise ValueError(f"a job id is letters, digits, '-' and '_' only: {job_id!r}")
    registry = registry or STAGES
    probe = probe or (lambda _: None)
    project.recovered += recover(project.layout)
    the_plan = plan(
        project,
        targets,
        registry=registry,
        force=force,
        budget_mb=budget_mb,
        year=year,
        memory_mb=memory_mb,
    )
    # Consent is asked once per stage, before anything runs. An opt-in stage without
    # it is skipped, as if switched off: the stages after it run without it.
    answers: dict[str, bool] = {}
    for item in the_plan.items:
        too_large = item.over_budget and not _allowed_over(allow_over_budget, item.stage)
        if item.action == "run" and item.needs_consent and item.refusal is None and not too_large:
            stage = registry[item.stage]
            request = ConsentRequest(
                stage.id, stage.name, stage.network, stage.paid, stage.consent_note, item.estimate
            )
            answers[item.stage] = consent is not None and bool(consent(request))
    off = frozenset(s for s, yes in answers.items() if not yes and registry[s].opt_in)
    if off:
        the_plan = plan(
            project,
            targets,
            registry=registry,
            force=force,
            budget_mb=budget_mb,
            year=year,
            off=off,
            memory_mb=memory_mb,
        )

    refused: dict[str, str] = {}
    runnable: list[PlanItem] = []
    for item in the_plan.items:
        if item.action != "run":
            continue
        own = item.refusal
        if own is None and item.over_budget and not _allowed_over(allow_over_budget, item.stage):
            own = item.blocked
        before = registry.upstream_of(item.stage)
        inherited = next((u for u in registry.ids if u in refused and u in before), None)
        if own is not None:
            refused[item.stage] = own
        elif inherited is not None:
            refused[item.stage] = f"depends on {inherited}, which does not run"
        elif item.needs_consent and not answers.get(item.stage, False):
            refused[item.stage] = "no consent"
        else:
            runnable.append(item)

    job_id = job_id or new_run_id()
    log = _JobLog(project.layout.jobs / f"{job_id}.jsonl")
    log.write(
        "start",
        job=job_id,
        run=[i.stage for i in runnable],
        keep=list(the_plan.to_keep),
        skip=list(the_plan.to_skip),
        refused=sorted(refused),
    )
    known = [i.estimate.seconds for i in runnable if i.estimate and i.estimate.seconds]
    fallback = sum(known) / len(known) if known else 1.0
    weights = [
        max(0.05, (i.estimate.seconds if i.estimate and i.estimate.seconds else fallback))
        for i in runnable
    ]
    reporter = _Reporter(progress, weights, heartbeat_s)
    ran: list[RunRecord] = []
    outcome: Literal["succeeded", "failed", "cancelled"] = "succeeded"
    failed: tuple[str, str] | None = None
    reporter.start()
    if runnable and project.has_curation():
        _freeze_identity(project, "curation decisions exist", log)
    try:
        for k, item in enumerate(runnable, start=1):
            stage = registry[item.stage]
            if cancel is not None and cancel.is_set():
                outcome = "cancelled"
                break
            reporter.update(k, stage, 0.0, "started")
            log.write("phase", phase=k, phases=len(runnable), stage=stage.id)

            def report(fraction: float, message: str, _k: int = k, _s: Stage = stage) -> None:
                reporter.update(_k, _s, fraction, message)

            try:
                record = _run_stage(
                    project,
                    registry,
                    stage,
                    year=year,
                    off=off,
                    report=report,
                    cancel=cancel,
                    probe=probe,
                )
            except Cancelled:
                outcome = "cancelled"
                log.write("cancelled", stage=stage.id)
                break
            except Exception as exc:
                outcome = "failed"
                failed = (stage.id, f"{type(exc).__name__}: {exc}")
                log.write("failed", stage=stage.id, error=type(exc).__name__)
                break
            except BaseException:
                log.write("cancelled", stage=stage.id)
                raise
            ran.append(record)
            if record.stage == "keywords.triage":
                _freeze_identity(project, "first AI answers", log)
            reporter.update(k, stage, 1.0, "done")
            log.write(
                "stage-end",
                stage=stage.id,
                run_id=record.run_id,
                seconds=record.measures.seconds,
                peak_memory_mb=record.measures.peak_memory_mb,
                counts=record.measures.counts,
                warnings=len(record.warnings),
            )
    finally:
        reporter.close()
        log.write("end", outcome=outcome, ran=[r.stage for r in ran])
        log.close()
    done = {r.stage for r in ran} | ({failed[0]} if failed else set())
    not_run = tuple(i.stage for i in runnable if i.stage not in done)
    return BuildResult(the_plan, outcome, tuple(ran), refused, failed, not_run, job_id)
