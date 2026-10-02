# SPDX-License-Identifier: MIT
"""What is kept on this computer, never in a project: the keys (the AI provider's and
OpenAlex's) and the folder of a downloaded OpenAlex snapshot.

A key is personal and belongs to a machine: a project folder is shared, synced
and backed up, so no key is ever written there. The app keeps them in its own
folder (``<data_dir>/keys.json``, readable by its owner only), or in memory
when it has no folder. An environment variable given at launch
(``MISTRAL_API_KEY``, ``OPENALEX_API_KEY``) wins over a key saved here. The
AI clean-up and the collection read the saved key each time they start, so a
key saved while the app runs serves the next one. A hosted service has its
keys set by whoever runs it: none are saved from the interface.

The snapshot folder (:class:`MachineSnapshot`, ``<data_dir>/snapshot.json``) is a
path of this computer, like a key: collection reads OpenAlex from it when a
collection chooses to, and the speed it was last read at serves the estimates.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cartolex.project.files import atomic_write_bytes, json_bytes, replace_path

__all__ = [
    "KEY_SERVICES",
    "SNAPSHOT_READ_RATE",
    "MachineKeys",
    "MachineSnapshot",
    "SnapshotRefused",
]

#: The services a key can be saved for, and the environment variable that wins over it.
KEY_SERVICES = {"mistral": "MISTRAL_API_KEY", "openalex": "OPENALEX_API_KEY"}
#: The read speed assumed before a job has measured this computer's, in bytes a second (a
#: snapshot on an external hard disk, read four parts at a time).
SNAPSHOT_READ_RATE = 60e6
#: The least a job must read for its speed to be kept (a small read says little).
_RATE_MIN_BYTES = 1e9
#: Seconds a member of an indexed snapshot takes to read, assumed before a job measured it
#: (a seek and a short read on an external hard disk, and unpacking a megabyte).
SNAPSHOT_MEMBER_SECONDS = 0.015
#: The least members a job must read for its speed to be kept.
_RATE_MIN_MEMBERS = 2000


class MachineKeys:
    """The keys of this computer: read, saved, removed; never shown back whole."""

    def __init__(self, folder: Path | None) -> None:
        self.path = Path(folder) / "keys.json" if folder is not None else None
        self._memory: dict[str, str] = {}
        self._lock = threading.Lock()

    def _saved(self) -> dict[str, str]:
        if self.path is None:
            return dict(self._memory)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: str(v) for k, v in data.items() if k in KEY_SERVICES and str(v).strip()}

    def get(self, service: str) -> str | None:
        """The key in use for *service*: the environment's, else the one saved here."""
        env = os.environ.get(KEY_SERVICES[service], "").strip()
        return env or self._saved().get(service) or None

    def status(self, service: str) -> dict[str, Any]:
        """Whether a key is set, where it comes from, and its last four characters."""
        env = os.environ.get(KEY_SERVICES[service], "").strip()
        saved = self._saved().get(service)
        key = env or saved
        return {
            "set": bool(key),
            "source": "environment" if env else "saved" if saved else None,
            "env_var": KEY_SERVICES[service],
            "saved": bool(saved),
            "ends": key[-4:] if key and len(key) >= 8 else None,
        }

    def save(self, service: str, key: str | None) -> None:
        """Save *key* for *service* on this computer (``None`` removes it)."""
        with self._lock:
            data = self._saved()
            if key:
                data[service] = key.strip()
            else:
                data.pop(service, None)
            if self.path is None:
                self._memory = data
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".part")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            replace_path(tmp, self.path)


class SnapshotRefused(ValueError):
    """A folder that cannot be saved as the snapshot: *reason* is ``relative`` (not a full
    path), ``not_folder`` or ``no_snapshot``."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class MachineSnapshot:
    """The OpenAlex snapshot folder of this computer, and the speed it was last read at.

    The folder is checked against its manifests (:meth:`cartolex.collect.snapshot.Snapshot.check`)
    when it is saved and when its state is asked, at most every :attr:`ttl` seconds (a
    check looks at every part). Its state is ``ready`` (every part the manifests list is
    there), ``incomplete`` (some are missing, or an entity cartolex reads), or ``missing``
    (the folder is not there: a disk not plugged in).
    """

    ttl = 30.0

    def __init__(self, folder: Path | None) -> None:
        self.path = Path(folder) / "snapshot.json" if folder is not None else None
        self._memory: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._checked: tuple[float, str, Any] | None = None

    def _saved(self) -> dict[str, Any]:
        if self.path is None:
            return dict(self._memory)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) and isinstance(data.get("folder"), str) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self._checked = None
        if self.path is None:
            self._memory = data
        elif data:
            atomic_write_bytes(self.path, json_bytes(data))
        else:
            self.path.unlink(missing_ok=True)

    def folder(self) -> Path | None:
        """The folder saved, or ``None``."""
        saved = self._saved().get("folder")
        return Path(saved) if saved else None

    def read_rate(self) -> tuple[float, bool]:
        """Bytes a second a job reads the snapshot at, and whether this computer measured it."""
        rate = self._saved().get("read_rate")
        if isinstance(rate, int | float) and rate > 0:
            return float(rate), True
        return SNAPSHOT_READ_RATE, False

    def member_seconds(self) -> tuple[float, bool]:
        """Seconds a member of the indexed snapshot takes to read, and whether this computer
        measured it."""
        value = self._saved().get("member_seconds")
        if isinstance(value, int | float) and value > 0:
            return float(value), True
        return SNAPSHOT_MEMBER_SECONDS, False

    def index(self) -> Any:
        """The complete index of the saved snapshot, opened, or ``None``."""
        from cartolex.collect.snapshot_index import SnapshotIndex

        found = self.check()
        if found is None or not found.indexed:
            return None
        return SnapshotIndex.open(found.root, found.release)

    def check(self) -> Any:
        """The saved folder's :class:`~cartolex.collect.snapshot.SnapshotCheck`, or ``None``
        when none is saved or the folder holds no snapshot now."""
        from cartolex.collect.snapshot import Snapshot

        folder = self.folder()
        if folder is None:
            return None
        with self._lock:
            now = time.monotonic()
            if (
                self._checked
                and self._checked[1] == str(folder)
                and now - self._checked[0] < self.ttl
            ):
                return self._checked[2]
            try:
                found = Snapshot(folder).check()
            except (FileNotFoundError, OSError):
                found = None
            self._checked = (now, str(folder), found)
            return found

    def status(self) -> dict[str, Any] | None:
        """The folder, its state, its release and sizes, the read speed; ``None`` without one."""
        folder = self.folder()
        if folder is None:
            return None
        found = self.check()
        rate, measured = self.read_rate()
        out: dict[str, Any] = {
            "folder": str(folder),
            "state": "missing" if found is None else "ready" if found.complete else "incomplete",
            "read_rate": round(rate),
            "rate_measured": measured,
            "measured_at": self._saved().get("measured_at"),
        }
        if found is not None:
            out.update(found.to_json())
        return out

    def save(self, folder: str | None) -> None:
        """Save *folder* (``None`` removes it); refused (:class:`SnapshotRefused`) when it is
        not a full path or holds no OpenAlex snapshot. The speed measured is kept for the
        same folder."""
        from cartolex.collect.snapshot import Snapshot

        data: dict[str, Any] = {}
        if folder:
            path = Path(folder).expanduser()
            if not path.is_absolute():
                raise SnapshotRefused("relative", "give the folder's full path")
            if not path.is_dir():
                raise SnapshotRefused("not_folder", f"{path} is not a folder")
            try:
                Snapshot(path.resolve())
            except FileNotFoundError as exc:
                raise SnapshotRefused("no_snapshot", str(exc)) from None
            data["folder"] = str(path.resolve())
        with self._lock:
            saved = self._saved()
            if data and saved.get("folder") == data["folder"]:
                data.update({k: saved[k] for k in ("read_rate", "measured_at") if k in saved})
            self._write(data)

    def record_rate(self, bytes_read: int, seconds: float, members: int = 0) -> None:
        """Keep the speed a job read the snapshot at (when it read enough to tell): bytes a
        second for whole parts, seconds a member through the index."""
        if seconds <= 0 or (
            members < _RATE_MIN_MEMBERS if members else bytes_read < _RATE_MIN_BYTES
        ):
            return
        with self._lock:
            data = self._saved()
            if not data:
                return
            if members:
                data["member_seconds"] = round(seconds / members, 5)
            else:
                data["read_rate"] = round(bytes_read / seconds)
            data["measured_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            checked = self._checked
            self._write(data)
            self._checked = checked  # the folder did not change
