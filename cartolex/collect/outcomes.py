# SPDX-License-Identifier: MIT
"""What happened to each person in each collection: found, found nothing, or failed.

A finder that cannot collect for one person (a service that keeps failing, a
page cut short, an answer missing from the cache in ``cache_only`` mode) goes
on with the others and records the failure, with its cause, in a
``failures`` run of the slot's raw folder (``cartolex-raw/1``, one record per
person). Nothing else remembers it: the HTTP cache never keeps a failed answer,
and a later collection that reaches the person supersedes the failure. After
:data:`MAX_FAILURES_IN_A_ROW` failures in a row the service is taken to be down:
the job stops asking and keeps what it collected.

:func:`latest_outcomes` reads, for every person and finder, the latest
attempt: a success (the person is named by a finder's run) or a failure. The
coverage report (:mod:`cartolex.collect.coverage`) builds on it, so that a
failure is never shown as « no data ».
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cartolex.project.layout import ProjectLayout
from cartolex.project.models import ProjectFile

from .http import CacheMiss, CollectError, HttpClient, ServiceError, ServiceUnavailable
from .tables import RawWriter, iso, read_runs

__all__ = [
    "FAILURES_KIND",
    "MAX_FAILURES_IN_A_ROW",
    "Outcome",
    "failure_record",
    "latest_outcomes",
    "stops_the_job",
    "write_failures",
]

FAILURES_KIND = "failures"
#: After this many people failed in a row, a service is taken to be down.
MAX_FAILURES_IN_A_ROW = 3
#: The kinds of raw runs that name the people a finder reached, and how.
_SUCCESS_KINDS = ("openalex", "hal", "resolve", "institution", "snowball")


def stops_the_job(client: HttpClient, exc: CollectError) -> bool:
    """Whether a failure ends the whole job: a service asking for a wait longer than a job
    may block (a spent daily budget, a maintenance window)."""
    limit = client.settings.retry.max_retry_after
    return isinstance(exc, ServiceUnavailable) and (exc.retry_after or 0) > limit


def failure_record(
    person_id: str, finder: str, exc: CollectError, *, now: datetime | None = None
) -> dict[str, Any]:
    """The record of one person's failed collection: which finder, which service, the cause."""
    status = exc.status if isinstance(exc, ServiceError) else None
    host = exc.host if isinstance(exc, ServiceError) else None
    if isinstance(exc, CacheMiss):
        cause = f"not in the cache ({exc.kind.replace('_', ' ')}): collect without --cache-only"
        service = exc.service
    else:
        cause = str(exc)
        service = None
    return {
        "person_id": person_id,
        "finder": finder,
        "service": service,
        "host": host,
        "status": status,
        "error": type(exc).__name__,
        "cause": cause,
        "at": iso(now or datetime.now(timezone.utc)),
    }


def write_failures(
    layout: ProjectLayout,
    slot: str,
    finder: str,
    records: Iterable[Mapping[str, Any]],
    *,
    run_id: str | None = None,
    now: datetime | None = None,
) -> Path | None:
    """Write one ``failures`` run for *finder* (nothing when there is no failure)."""
    records = list(records)
    if not records:
        return None
    with RawWriter(layout, slot, FAILURES_KIND, {"finder": finder}, run_id=run_id, now=now) as out:
        for rec in records:
            out.add(dict(rec))
    return out.path


@dataclass(frozen=True)
class Outcome:
    """The latest attempt of one finder for one person."""

    finder: str
    ok: bool
    run_id: str
    slot: str
    cause: str = ""
    error: str = ""
    service: str | None = None


def _finder_of(kind: str) -> str:
    return {"openalex": "harvest"}.get(kind, kind)


def latest_outcomes(layout: ProjectLayout, config: ProjectFile) -> dict[str, dict[str, Outcome]]:
    """person id → finder → the latest attempt (runs compared by their time-ordered ids)."""
    out: dict[str, dict[str, Outcome]] = {}

    def offer(pid: str, outcome: Outcome) -> None:
        mine = out.setdefault(pid, {})
        old = mine.get(outcome.finder)
        if old is None or outcome.run_id >= old.run_id:
            mine[outcome.finder] = outcome

    for slot in (s.id for s in config.slots):
        for run in read_runs(layout, slot):
            if run.kind == FAILURES_KIND:
                finder = str(run.header.get("finder") or "")
                for rec in run.records():
                    offer(
                        rec["person_id"],
                        Outcome(
                            finder,
                            False,
                            run.run_id,
                            slot,
                            cause=str(rec.get("cause") or ""),
                            error=str(rec.get("error") or ""),
                            service=rec.get("service"),
                        ),
                    )
            elif run.kind in _SUCCESS_KINDS:
                for pid in _people_of(run):
                    offer(pid, Outcome(_finder_of(run.kind), True, run.run_id, slot))
    return out


def _people_of(run: Any) -> list[str]:
    """The people a finder's run reached: its header when it names them, else its records."""
    people = run.header.get("people")
    if isinstance(people, dict):
        return list(people)
    searched = run.header.get("searched")
    if isinstance(searched, list):
        return [str(p) for p in searched]
    if run.kind in ("resolve", "hal"):
        return sorted({r["person_id"] for r in run.records() if r.get("person_id")})
    return []
