# SPDX-License-Identifier: MIT
"""Jobs: builds and collections that run outside the request (4d).

:class:`JobRunner` is the interface: submit, status, progress, cancel, list,
events. :class:`LocalJobRunner` runs each job in a thread of the app's
process; a queue (another process, another machine) can stand behind the same
interface. A job belongs to a project and to an *exclusivity group*: one job
per project and group at a time (builds and collections share the group
``work``); a second one is refused with :class:`JobConflict`, naming the job
that runs.

Every job writes ``logs/jobs/<job id>.jsonl`` in its project: a ``job`` line
(its kind, the process, a digest of the machine's name and its boot, never a
name or a text), the
events of its work (a build writes its own: phases, stage ends, counts, times)
and a ``job-end`` line. After a restart, a job whose log has no end and whose
process is gone is reported as ``interrupted``, never as running.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from cartolex.build.machine import boot_id
from cartolex.build.records import new_run_id

__all__ = [
    "ACTIVE_STATES",
    "JOB_STATES",
    "JobConflict",
    "JobControl",
    "JobInfo",
    "JobRunner",
    "LocalJobRunner",
    "read_job_logs",
]

#: The states of a job. ``cancelling``: a cancel was asked and the job has not stopped yet.
JOB_STATES = (
    "queued",
    "running",
    "cancelling",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
)
ACTIVE_STATES = frozenset({"queued", "running", "cancelling"})
#: Events kept in memory per job (the log file keeps them all).
MAX_EVENTS = 2_000


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class JobInfo:
    """What the API says about a job: never a name or a text, only states, counts and times."""

    id: str
    kind: str
    project: str
    state: str
    title: str = ""
    submitted_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    group: str = "work"
    #: The title as a code of the interface's catalogues (``job.title.<code>``) and
    #: its parameters; ``title`` stays the English words.
    title_code: str = ""
    title_params: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "project": self.project,
            "state": self.state,
            "title": self.title,
            "title_code": self.title_code,
            "title_params": dict(self.title_params or {}),
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
        }


class JobConflict(RuntimeError):
    """A job of the same group runs on the project; it is named."""

    def __init__(self, running: JobInfo) -> None:
        self.running = running
        super().__init__(
            f"a {running.kind} job ({running.id}) is already running on this project; "
            "wait for it or cancel it"
        )


class JobControl:
    """What a job's work gets: its id, the cancel event, progress and events.

    ``progress(data)`` replaces the job's progress (a mapping of plain values:
    stage, fractions, ETA); ``event(name, **data)`` appends to its log.
    """

    def __init__(
        self,
        job_id: str,
        log: Path,
        cancel: threading.Event,
        on_progress: Callable[[dict[str, Any]], None],
        on_event: Callable[[dict[str, Any]], None],
    ) -> None:
        self.job_id = job_id
        self.log = log
        self.cancel = cancel
        self._on_progress = on_progress
        self._on_event = on_event
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def progress(self, data: Mapping[str, Any]) -> None:
        self._on_progress(dict(data))

    def event(self, name: str, *, write: bool = True, **data: Any) -> None:
        """Record an event (and append it to the job's log unless *write* is false)."""
        line = {"at": _stamp(), "event": name, **data}
        if write:
            with self._lock:
                self.log.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(line) + "\n")
        self._on_event(line)


#: A job's work: runs in the job, returns its result (plain values), raises on failure.
Work = Callable[[JobControl], Mapping[str, Any]]


class JobRunner(Protocol):
    """Runs jobs outside requests; a local thread runner or a queue behind the same calls."""

    def submit(
        self,
        *,
        project: str,
        jobs_dir: Path,
        kind: str,
        work: Work,
        title: str = "",
        group: str = "work",
        title_code: str = "",
        title_params: Mapping[str, Any] | None = None,
    ) -> JobInfo: ...

    def status(self, job_id: str) -> JobInfo | None: ...

    def progress(self, job_id: str) -> dict[str, Any] | None: ...

    def cancel(self, job_id: str) -> JobInfo | None: ...

    def list(self, project: str | None = None) -> list[JobInfo]: ...

    def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]: ...

    def running(self, project: str, group: str = "work") -> JobInfo | None: ...

    def shutdown(self, timeout: float = 10.0) -> None: ...


@dataclass
class _Job:
    info: JobInfo
    cancel: threading.Event = field(default_factory=threading.Event)
    events: list[dict[str, Any]] = field(default_factory=list)
    thread: threading.Thread | None = None


class LocalJobRunner:
    """Runs each job in a thread of this process; one job per project and group at a time."""

    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()
        self._closed = False

    # ── submitting ──
    def submit(
        self,
        *,
        project: str,
        jobs_dir: Path,
        kind: str,
        work: Work,
        title: str = "",
        group: str = "work",
        title_code: str = "",
        title_params: Mapping[str, Any] | None = None,
    ) -> JobInfo:
        with self._lock:
            if self._closed:
                raise RuntimeError("the app is stopping; no new job starts")
            busy = self._running_locked(project, group)
            if busy is not None:
                raise JobConflict(busy)
            job_id = new_run_id()
            info = JobInfo(
                id=job_id,
                kind=kind,
                project=project,
                state="queued",
                title=title,
                submitted_at=_stamp(),
                group=group,
                title_code=title_code,
                title_params=dict(title_params or {}),
            )
            job = _Job(info)
            # Known here before its log is written: a reader that takes the logs and then
            # the runner's list never sees this job as a log without an end.
            self._jobs[job_id] = job
        log = Path(jobs_dir) / f"{job_id}.jsonl"
        control = JobControl(
            job_id,
            log,
            job.cancel,
            lambda data, j=job: self._set_progress(j, data),
            lambda line, j=job: self._add_event(j, line),
        )
        control.event(
            "job",
            job=job_id,
            kind=kind,
            title_code=title_code,
            title_params=dict(title_params or {}),
            pid=os.getpid(),
            host=host_digest(),
            boot=boot_id(),
        )
        job.thread = threading.Thread(
            target=self._run, args=(job, work, control), name=f"cartolex-job-{job_id}", daemon=True
        )
        job.thread.start()
        return job.info

    def _run(self, job: _Job, work: Work, control: JobControl) -> None:
        self._update(job, state="running", started_at=_stamp())
        state, result, error = "succeeded", None, None
        try:
            result = dict(work(control) or {})
            outcome = result.get("outcome")
            if outcome in ("failed", "cancelled"):
                state = outcome
                error = result.get("error") if outcome == "failed" else None
            elif job.cancel.is_set() and outcome is None:
                state = "cancelled"
        except BaseException as exc:  # a job never takes the server down
            state, error = ("cancelled", None) if job.cancel.is_set() else ("failed", None)
            if state == "failed":
                error = _error_text(exc)
        finally:
            finished = _stamp()
            self._update(job, state=state, finished_at=finished, result=result, error=error)
            control.event("job-end", state=state)

    # ── reading ──
    def _update(self, job: _Job, **changes: Any) -> None:
        with self._lock:
            job.info = replace(job.info, **changes)

    def _set_progress(self, job: _Job, data: dict[str, Any]) -> None:
        with self._lock:
            job.info = replace(job.info, progress=data)

    def _add_event(self, job: _Job, line: dict[str, Any]) -> None:
        with self._lock:
            job.events.append(line)
            if len(job.events) > MAX_EVENTS:
                del job.events[: len(job.events) - MAX_EVENTS]

    def _view(self, job: _Job) -> JobInfo:
        info = job.info
        if job.cancel.is_set() and info.state in ("queued", "running"):
            info = replace(info, state="cancelling")
        return info

    def status(self, job_id: str) -> JobInfo | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._view(job) if job else None

    def progress(self, job_id: str) -> dict[str, Any] | None:
        info = self.status(job_id)
        return info.progress if info else None

    def cancel(self, job_id: str) -> JobInfo | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.info.state in ("queued", "running"):
                job.cancel.set()
            return self._view(job)

    def list(self, project: str | None = None) -> list[JobInfo]:
        with self._lock:
            jobs = [self._view(j) for j in self._jobs.values()]
        if project is not None:
            jobs = [j for j in jobs if j.project == project]
        return sorted(jobs, key=lambda j: j.id, reverse=True)

    def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return []
            return [dict(e, seq=i) for i, e in enumerate(job.events) if i >= after]

    def _running_locked(self, project: str, group: str) -> JobInfo | None:
        for job in self._jobs.values():
            info = job.info
            if info.project == project and info.group == group and info.state in ACTIVE_STATES:
                return self._view(job)
        return None

    def running(self, project: str, group: str = "work") -> JobInfo | None:
        with self._lock:
            return self._running_locked(project, group)

    def wait(self, job_id: str, timeout: float | None = None) -> JobInfo | None:
        """Wait for a job to end (tests, the command line); returns its last state."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job.thread is None:
            return self.status(job_id)
        job.thread.join(timeout)
        return self.status(job_id)

    def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel every job and wait for them (at most *timeout* seconds in all)."""
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel.set()
        deadline = time.monotonic() + timeout
        for job in jobs:
            if job.thread is not None:
                job.thread.join(max(0.0, deadline - time.monotonic()))


def _error_text(exc: BaseException) -> str:
    """An error in words: its type and message (messages of cartolex name no person)."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


# ── logs of earlier jobs ─────────────────────────────────────────────────────


def host_digest(name: str | None = None) -> str:
    """This machine, in a job log: a digest of its name (a name can be a person's)."""
    raw = (name or socket.gethostname()).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:16]


def _pid_alive(pid: int) -> bool:
    from cartolex.project.lock import _pid_alive as alive

    return alive(pid)


def read_job_logs(jobs_dir: Path, project: str, *, limit: int = 20) -> list[JobInfo]:
    """The most recent jobs recorded in *jobs_dir* (``logs/jobs/``), newest first.

    A log without an end whose process is gone (or ran before the machine
    restarted) is ``interrupted``; one whose process still runs elsewhere on
    this machine is ``running``.
    """
    folder = Path(jobs_dir)
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.jsonl"), reverse=True)[:limit]
    out: list[JobInfo] = []
    here, boot = host_digest(), boot_id()
    for path in files:
        events = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            continue
        if not events:
            continue
        head = next((e for e in events if e.get("event") == "job"), None)
        kind = head.get("kind", "build") if head else "build"
        start = events[0].get("at")
        ends = [e for e in events if e.get("event") in ("job-end", "end")]
        result: dict[str, Any] | None = None
        build_end = next((e for e in reversed(events) if e.get("event") == "end"), None)
        if build_end is not None:
            result = {"outcome": build_end.get("outcome"), "ran": build_end.get("ran", [])}
        if ends:
            last = ends[-1]
            state = last.get("state") or last.get("outcome") or "succeeded"
            finished = last.get("at")
        else:
            finished = None
            alive = (
                head is not None
                and head.get("host") == here
                and head.get("boot") == boot
                and int(head.get("pid", -1)) != os.getpid()
                and _pid_alive(int(head.get("pid", -1)))
            )
            state = "running" if alive else "interrupted"
        out.append(
            JobInfo(
                id=path.stem,
                kind=kind,
                project=project,
                state=state if state in JOB_STATES else "failed",
                title_code=str(head.get("title_code") or "") if head else "",
                title_params=dict(head.get("title_params") or {}) if head else {},
                submitted_at=start or "",
                started_at=start,
                finished_at=finished,
                result=result,
            )
        )
    return out
