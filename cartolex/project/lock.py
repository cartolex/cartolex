# SPDX-License-Identifier: MIT
"""The project lock: one application writes a project at a time.

``.lock`` is created atomically and holds its owner (process id, host,
application, start time). A second opener gets :class:`LockHeld`, naming the
holder. When the holder's process no longer runs on this host, the lock is
**stale**: :class:`StaleLock` says so and names the command that removes it.
A lock is never removed silently, and a lock held on another host is never
judged stale (its process cannot be checked from here).
"""

from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .layout import ProjectLayout

__all__ = ["LockHeld", "LockInfo", "ProjectLock", "StaleLock", "read_lock", "remove_stale_lock"]


@dataclass(frozen=True)
class LockInfo:
    pid: int
    host: str
    app: str
    since: str


class LockHeld(RuntimeError):
    """Another running application holds the project."""

    def __init__(self, path: Path, info: LockInfo | None) -> None:
        self.path, self.info = path, info
        who = (
            f"{info.app} (process {info.pid} on {info.host}, since {info.since})"
            if info
            else "an unreadable lock file"
        )
        super().__init__(f"the project is open in {who}: {path}")


class StaleLock(LockHeld):
    """The lock's process no longer runs on this host."""

    def __init__(self, path: Path, info: LockInfo) -> None:
        super().__init__(path, info)
        self.args = (
            f"the project's lock is stale: {info.app} (process {info.pid}, since {info.since}) "
            f"no longer runs on this host. If no other window has the project open, remove "
            f"it with: cartolex project unlock {path.parent}",
        )


def read_lock(path: Path) -> LockInfo | None:
    """The owner recorded in a lock file, or ``None`` when it cannot be read."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return LockInfo(int(raw["pid"]), str(raw["host"]), str(raw["app"]), str(raw["since"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _pid_alive(pid: int) -> bool:
    """Whether a process with this id runs on this host."""
    if pid <= 0:
        return False
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ProjectLock:
    """Hold a project's ``.lock`` for the life of this object (or a ``with`` block)."""

    def __init__(self, layout: ProjectLayout, app: str) -> None:
        self.path = layout.lock
        self.app = app
        self.info: LockInfo | None = None

    def acquire(self) -> ProjectLock:
        info = LockInfo(
            pid=os.getpid(),
            host=socket.gethostname(),
            app=self.app,
            since=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            held = read_lock(self.path)
            if held and held.host == info.host and not _pid_alive(held.pid):
                raise StaleLock(self.path, held) from None
            raise LockHeld(self.path, held) from None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(info), fh)
            fh.write("\n")
        self.info = info
        return self

    def release(self) -> None:
        if self.info is None:
            return
        held = read_lock(self.path)
        if held == self.info:
            self.path.unlink(missing_ok=True)
        self.info = None

    def __enter__(self) -> ProjectLock:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


def remove_stale_lock(layout: ProjectLayout) -> LockInfo | None:
    """Remove a lock whose process is gone (the explicit ``unlock`` command).

    An unreadable lock file is removed (it names no holder to protect). Refuses
    when the holder still runs on this host, or when it was taken on another
    host (it cannot be checked from here). Returns the removed holder.
    """
    if not layout.lock.exists():
        raise FileNotFoundError(f"no lock at {layout.lock}")
    held = read_lock(layout.lock)
    if held is None:
        layout.lock.unlink()
        return None
    if held.host != socket.gethostname():
        raise LockHeld(layout.lock, held)
    if _pid_alive(held.pid):
        raise LockHeld(layout.lock, held)
    layout.lock.unlink()
    return held
