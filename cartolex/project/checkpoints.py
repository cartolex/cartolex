# SPDX-License-Identifier: MIT
"""Checkpoints of long jobs: where a job got to, kept so that it can stop and go on later.

A long job (a collection that reads thousands of pages) saves its state every
so often with :meth:`Checkpoint.save`: the position it reached (a cursor, a
page number) and what it computed so far (its aggregates). When it must stop
before the end (the person asks it to, or a request still fails after its
retries), it saves once more and raises :class:`JobPaused`: the job ends
*paused*, not failed, with the cause and the checkpoint's id. A new job given
that id loads the state (:meth:`Checkpoint.load`) and continues; its result is
the one an uninterrupted run gives. A finished job removes its checkpoint
(:meth:`Checkpoint.clear`).

A checkpoint is one gzip-compressed JSON file written atomically (a reader
never sees half of it); it names its *kind* and the *key* of the work it
belongs to (a digest of what was asked), so a state is never loaded into
another job's work. The job decides how old a state may be (a cursor a
service no longer honours): :attr:`Saved.age_s` says how old it is.

The jobs runner of the app (:mod:`cartolex.app.jobs`) turns :class:`JobPaused`
into the ``paused`` state; the code below the app raises it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .files import atomic_write_bytes

__all__ = ["CHECKPOINT_FORMAT", "Checkpoint", "JobPaused", "Saved", "work_key"]

CHECKPOINT_FORMAT = "cartolex-checkpoint/1"


def work_key(what: Mapping[str, Any]) -> str:
    """A short digest of what a job was asked to do (its query), to tell checkpoints apart."""
    text = json.dumps(what, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Saved:
    """A loaded checkpoint: its state, when it was saved and how old it is."""

    state: dict[str, Any]
    saved_at: datetime
    age_s: float


class Checkpoint:
    """The checkpoint of one piece of work: ``<folder>/<kind>-<key>.json.gz``."""

    def __init__(self, folder: Path, kind: str, key: str) -> None:
        self.folder = Path(folder)
        self.kind = kind
        self.key = key
        self.path = self.folder / f"{kind}-{key}.json.gz"

    @property
    def id(self) -> str:
        """The checkpoint's id, as a resumed job names it: ``<kind>-<key>``."""
        return f"{self.kind}-{self.key}"

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, state: Mapping[str, Any], *, now: datetime | None = None) -> None:
        """Keep *state* (plain JSON values), replacing the previous one in one step."""
        when = now or datetime.now(timezone.utc)
        doc = {
            "format": CHECKPOINT_FORMAT,
            "kind": self.kind,
            "key": self.key,
            "saved_at": when.isoformat(),
            "state": state,
        }
        data = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        atomic_write_bytes(self.path, gzip.compress(data, compresslevel=3, mtime=0))

    def load(self, *, now: datetime | None = None) -> Saved | None:
        """The saved state, or ``None`` when there is none (or it cannot be read, or it
        belongs to other work)."""
        try:
            doc = json.loads(gzip.decompress(self.path.read_bytes()).decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, EOFError, ValueError, UnicodeDecodeError):
            return None
        if (
            not isinstance(doc, dict)
            or doc.get("format") != CHECKPOINT_FORMAT
            or doc.get("kind") != self.kind
            or doc.get("key") != self.key
            or not isinstance(doc.get("state"), dict)
        ):
            return None
        try:
            saved_at = datetime.fromisoformat(doc["saved_at"])
        except (KeyError, TypeError, ValueError):
            return None
        if saved_at.tzinfo is None:
            return None
        age = ((now or datetime.now(timezone.utc)) - saved_at).total_seconds()
        return Saved(doc["state"], saved_at, max(0.0, age))

    def clear(self) -> None:
        """Remove the checkpoint (the work is done, or starts again)."""
        self.path.unlink(missing_ok=True)

    @classmethod
    def by_id(cls, folder: Path, checkpoint_id: str) -> Checkpoint:
        """The checkpoint *checkpoint_id* (``<kind>-<key>``) in *folder*."""
        kind, sep, key = checkpoint_id.rpartition("-")
        if not sep or not kind or not key.isalnum():
            raise ValueError(f"{checkpoint_id!r} is not a checkpoint id")
        return cls(folder, kind, key)


@dataclass
class JobPaused(Exception):
    """A job stopped before the end with its state saved: it can be resumed.

    *code* and *params* say why, for the interface's catalogues (``message`` is
    the same in English); *checkpoint* is the id a resumed job is given;
    *progress* says how far it got (plain values); *cause* is the exception
    that stopped it, if any.
    """

    code: str
    message: str
    checkpoint: str
    params: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    cause: BaseException | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message
