# SPDX-License-Identifier: MIT
"""Run records: run ids, ``run.json``, the last failed attempt, and running stages.

A successful run's record is ``derived/<stage>/run.json``. The last failed or
cancelled attempt of a stage is recorded in ``derived/.attempts/<stage>.json``
(the same record, with its outcome and an ``error``) and forgotten when a later
run succeeds. A running stage keeps a marker in its staging folder naming its
process and the project lock it runs under: a stage is *running* while that
lock is still held by that live process.
"""

from __future__ import annotations

import json
import secrets
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..project.files import atomic_write_bytes, json_bytes, utc_stamp
from ..project.generations import CHUNKS, DISCARD_SUFFIX, STAGING_MARKER
from ..project.layout import ProjectLayout
from ..project.lock import _pid_alive, read_lock
from ..project.models import RUN_ID_PATTERN, STAGE_IDS, RunRecord
from .machine import boot_id

__all__ = [
    "MARKER_FORMAT",
    "StagingFolder",
    "new_run_id",
    "read_attempt",
    "read_record",
    "record_bytes",
    "scan_staging",
    "write_attempt",
]

#: The format id of a staging folder's marker.
MARKER_FORMAT = "cartolex-staging/1"


def new_run_id(now: datetime | None = None) -> str:
    """A new run id: the UTC time and random hex digits, ``20260928T101200Z-7c1e2a``."""
    return f"{utc_stamp(now)}-{secrets.token_hex(3)}"


def record_bytes(record: RunRecord) -> bytes:
    """The canonical text of a run record (``error`` only when there is one)."""
    data = record.model_dump(mode="json", by_alias=True, exclude_none=False)
    if data.get("error") is None:
        data.pop("error", None)
    return json_bytes(data)


def _read(path: Path) -> RunRecord | None:
    try:
        return RunRecord.model_validate_json(Path(path).read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None


def read_record(layout: ProjectLayout, stage_id: str) -> RunRecord | None:
    """The record of the run that produced a stage's current results (``None``: none, or unreadable)."""
    record = _read(layout.run_json(stage_id))
    if record is not None and (record.stage != stage_id or record.outcome != "succeeded"):
        return None
    return record


def read_attempt(layout: ProjectLayout, stage_id: str) -> RunRecord | None:
    """The record of a stage's last failed or cancelled attempt, if any."""
    record = _read(layout.attempt(stage_id))
    if record is not None and (record.stage != stage_id or record.outcome == "succeeded"):
        return None
    return record


def write_attempt(layout: ProjectLayout, record: RunRecord) -> None:
    """Record a failed or cancelled attempt."""
    if record.outcome == "succeeded":
        raise ValueError("an attempt record is for a failed or cancelled run")
    atomic_write_bytes(layout.attempt(record.stage), record_bytes(record))


# ── staging folders ──────────────────────────────────────────────────────────


def marker_bytes(
    *,
    stage: str,
    run_id: str,
    key: str,
    started_at: str,
    pid: int,
    host: str,
    lock_since: str | None,
    boot: str | None,
) -> bytes:
    return json_bytes(
        {
            "format": MARKER_FORMAT,
            "stage": stage,
            "run_id": run_id,
            "key": key,
            "started_at": started_at,
            "pid": pid,
            "host": host,
            "lock_since": lock_since,
            "boot": boot,
        }
    )


@dataclass(frozen=True)
class StagingFolder:
    """A staging folder: a run in progress, or what a killed run left to resume from."""

    stage: str
    run_id: str
    path: Path
    marker: dict[str, Any]
    running: bool

    @property
    def key(self) -> str | None:
        return self.marker.get("key")

    def resumable(self, key: str) -> bool:
        """Whether a new run with *key* may resume from this folder's checkpoints.

        The inputs, parameters and code must be the same (*key*), and the machine
        must not have restarted since (checkpoints are not forced to disk).
        """
        if self.running or self.key != key:
            return False
        boot, now = self.marker.get("boot"), boot_id()
        return boot is None or now is None or boot == now

    def chunks_done(self) -> tuple[int, int | None]:
        """How many chunks are checkpointed, and out of how many (``None``: not known)."""
        folder = self.path / CHUNKS
        try:
            meta = json.loads((folder / "chunks.json").read_text(encoding="utf-8"))
            total = int(meta["total"])
        except (OSError, ValueError, KeyError, TypeError):
            total = None
        done = len(list(folder.glob("*.done"))) if folder.is_dir() else 0
        return done, total


def _alive(marker: dict[str, Any], layout: ProjectLayout) -> bool:
    """Whether the process that wrote *marker* still runs the stage under the project's lock."""
    held = read_lock(layout.lock)
    if held is None:
        return False
    if (held.pid, held.host, held.since) != (
        marker.get("pid"),
        marker.get("host"),
        marker.get("lock_since"),
    ):
        return False
    if held.host != socket.gethostname():
        return True  # a lock taken on another host cannot be checked from here
    return _pid_alive(held.pid)


def scan_staging(layout: ProjectLayout) -> dict[str, list[StagingFolder]]:
    """The staging folders of each stage, newest first."""
    found: dict[str, list[StagingFolder]] = {}
    root = layout.staging_root
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir(), reverse=True):
        if not entry.is_dir() or entry.name.endswith(DISCARD_SUFFIX):
            continue
        stage, _, run_id = entry.name.rpartition(".")
        if stage not in STAGE_IDS or not RUN_ID_PATTERN.match(run_id):
            continue
        try:
            marker = json.loads((entry / STAGING_MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(marker, dict) or marker.get("format") != MARKER_FORMAT:
            continue
        found.setdefault(stage, []).append(
            StagingFolder(stage, run_id, entry, marker, _alive(marker, layout))
        )
    return found
