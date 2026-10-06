# SPDX-License-Identifier: MIT
"""The project lock: one application writes a project at a time.

``.lock`` is created atomically and holds its owner (process id, host,
application, start time). A second opener gets :class:`LockHeld`, naming the
holder. A lock left by a process that no longer runs on this host (the app was
force-quit, its terminal closed, the computer crashed) is **taken over**: the
opener replaces it, under a short-lived ``.lock.takeover`` file so two openers
never both take it. A process id that now belongs to another process (this one,
or one started after the lock was taken) counts as gone. A lock held on another
host is never taken over (its process cannot be checked from here).

A person may still **override** a lock (``force``: the holder is stuck, or
runs on a computer that is off). The holder it replaced, if it still runs,
then finds the lock no longer names it: :func:`ensure_held` (called before
every decision write and every swap of results) raises :class:`LockLost`, so
it stops writing instead of mixing its writes with the new holder's.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import sys
import threading
import time
from collections.abc import Generator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from cartolex.scale.scratch import pid_alive as _pid_alive

from .layout import ProjectLayout

__all__ = [
    "LockHeld",
    "LockInfo",
    "LockLost",
    "ProjectLock",
    "ensure_held",
    "read_lock",
    "remove_stale_lock",
]

_log = logging.getLogger("cartolex.project")

#: A process that started this long after the lock was taken cannot be its holder
#: (its id was reused); the margin absorbs clock adjustments.
_REUSE_MARGIN = 300.0
#: A lock file that still names no holder after this long was left half written.
_UNREADABLE_AGE = 30.0
#: A takeover file older than this was left by a taker that died mid-way.
_TAKEOVER_AGE = 10.0
_TAKEOVER_WAIT = 15.0  # longer than _TAKEOVER_AGE: a dead taker's file is always cleared

#: The lock files this process holds, with what it wrote in them (a holder with this
#: process's id is only alive if listed).
_HELD: dict[str, LockInfo] = {}
_HELD_GUARD = threading.Lock()


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
        if info is None:
            who = "an application whose lock file cannot be read yet"
        elif info.host == socket.gethostname():
            who = f"another {info.app} on this computer (process {info.pid}, since {info.since})"
        else:
            who = f"{info.app} (process {info.pid} on {info.host}, since {info.since})"
        super().__init__(f"the project is open in {who}: {path}")

    @property
    def here(self) -> bool:
        """Whether the holder runs on this computer."""
        return self.info is not None and self.info.host == socket.gethostname()


class LockLost(RuntimeError):
    """This process held the project, and another application overrode its lock."""

    def __init__(self, path: Path, info: LockInfo | None) -> None:
        self.path, self.info = path, info
        who = (
            f"{info.app} (process {info.pid} on {info.host}, since {info.since})"
            if info
            else "no application (the lock was removed)"
        )
        super().__init__(
            f"the project's lock now names {who}: this application no longer writes to it; "
            f"open the project again: {path}"
        )


def ensure_held(path: Path) -> None:
    """Refuse to write when this process held the lock at *path* and lost it.

    A process that never took this lock (a reader, a tool) is not checked.
    """
    with _HELD_GUARD:
        mine = _HELD.get(_key(path))
    if mine is None:
        return
    now = read_lock(path)
    if now != mine:
        raise LockLost(path, now)


def read_lock(path: Path) -> LockInfo | None:
    """The owner recorded in a lock file, or ``None`` when it cannot be read."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return LockInfo(int(raw["pid"]), str(raw["host"]), str(raw["app"]), str(raw["since"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _process_started(pid: int) -> float | None:
    """When process *pid* started (seconds since the epoch), where the system tells."""
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]  # creation, exit, kernel, user
            if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return ticks / 1e7 - 11_644_473_600  # 100 ns since 1601 → seconds since 1970
        finally:
            kernel32.CloseHandle(handle)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        boot = next(
            line
            for line in Path("/proc/stat").read_text().splitlines()
            if line.startswith("btime ")
        )
        ticks = int(stat.rsplit(")", 1)[1].split()[19])  # field 22, after the command's name
        return int(boot.split()[1]) + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None  # no /proc (macOS): the process id alone decides


def _taken_at(info: LockInfo) -> float | None:
    try:
        stamp = datetime.strptime(info.since, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc).timestamp()


def _key(path: Path) -> str:
    return os.path.realpath(path)


def _holder_gone(path: Path, held: LockInfo) -> bool:
    """Whether the process that took *held* no longer runs (only checkable on this host)."""
    if held.host != socket.gethostname():
        return False
    if held.pid == os.getpid():
        with _HELD_GUARD:
            return _key(path) not in _HELD  # a former process that had this process's id
    if not _pid_alive(held.pid):
        return True
    started, taken = _process_started(held.pid), _taken_at(held)
    return started is not None and taken is not None and started > taken + _REUSE_MARGIN


def _left_half_written(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime > _UNREADABLE_AGE
    except OSError:
        return False


@contextlib.contextmanager
def _takeover(path: Path) -> Generator[None, None, None]:
    """Hold ``.lock.takeover`` beside *path*: one taker at a time."""
    mark = path.with_name(path.name + ".takeover")
    deadline = time.monotonic() + _TAKEOVER_WAIT
    while True:
        try:
            os.close(os.open(mark, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
            break
        except FileExistsError:
            try:
                if time.time() - mark.stat().st_mtime > _TAKEOVER_AGE:
                    mark.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                raise LockHeld(path, read_lock(path)) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        mark.unlink(missing_ok=True)


def _remove_if_gone(path: Path, seen: LockInfo | None, *, force: bool = False) -> bool:
    """Remove the lock at *path* if it is still *seen* and its holder is gone.

    Runs under the takeover file, so no other taker replaces the lock between
    the check and the removal. *force* removes it even if its holder runs (an
    override, asked for by a person who was warned). Returns whether a lock was
    removed.
    """
    with _takeover(path):
        if not path.exists():
            return False
        now = read_lock(path)
        if now != seen:
            return False
        if not force:
            gone = _left_half_written(path) if now is None else _holder_gone(path, now)
            if not gone:
                return False
        path.unlink(missing_ok=True)
        return True


class ProjectLock:
    """Hold a project's ``.lock`` for the life of this object (or a ``with`` block).

    With *holder*, write under the lock another process holds: the application that
    started this one to do some of its work (a build). The lock must name *holder*; it
    is never taken, overridden or removed here, and :func:`ensure_held` checks before
    every write that it still names *holder*.
    """

    def __init__(self, layout: ProjectLayout, app: str, *, holder: LockInfo | None = None) -> None:
        self.path = layout.lock
        self.app = app
        self.holder = holder
        self.info: LockInfo | None = None
        #: The holder of a lock left behind that :meth:`acquire` replaced (``None``: none).
        self.replaced: LockInfo | None = None

    def acquire(self, *, force: bool = False) -> ProjectLock:
        """Take the lock; a lock left behind is taken over, and with *force* any lock.

        *force* overrides a holder that may still run (see the module's text):
        only for a person who was told what it risks.
        """
        if self.holder is not None:
            now = read_lock(self.path)
            if now != self.holder:
                raise LockLost(self.path, now)
            self.info = self.holder
            with _HELD_GUARD:
                _HELD[_key(self.path)] = self.holder
            return self
        info = LockInfo(
            pid=os.getpid(),
            host=socket.gethostname(),
            app=self.app,
            since=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        for _ in range(3):
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                fd = None
            if fd is None:
                self._clear(read_lock(self.path), force=force)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(asdict(info), fh)
                fh.write("\n")
            self.info = info
            with _HELD_GUARD:
                _HELD[_key(self.path)] = info
            return self
        raise LockHeld(self.path, read_lock(self.path))

    def _clear(self, held: LockInfo | None, *, force: bool) -> None:
        """Remove the lock *held* if its holder is gone (or *force*); else raise LockHeld."""
        if force:
            gone = True
        elif held is None:
            gone = _left_half_written(self.path)
        else:
            gone = _holder_gone(self.path, held)
        if not gone:
            raise LockHeld(self.path, held)
        if not _remove_if_gone(self.path, held, force=force):
            return  # another opener changed it first: look again
        self.replaced = held
        who = (
            f"{held.app} (process {held.pid} on {held.host}, since {held.since})"
            if held
            else "an unreadable lock"
        )
        _log.warning(
            f"overrode the lock of {who}"
            if force
            else f"removed the lock left by {who}, which no longer runs",
            extra={
                "event": "lock_overridden" if force else "lock_taken_over",
                "path": str(self.path),
                "previous_pid": held.pid if held else None,
                "previous_since": held.since if held else None,
            },
        )

    def release(self) -> None:
        if self.info is None:
            return
        held = read_lock(self.path)
        if held == self.info and self.holder is None:
            self.path.unlink(missing_ok=True)
        with _HELD_GUARD:
            _HELD.pop(_key(self.path), None)
        self.info = None

    def __enter__(self) -> ProjectLock:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


def remove_stale_lock(layout: ProjectLayout, *, force: bool = False) -> LockInfo | None:
    """Remove a lock whose process is gone (the explicit ``unlock`` command).

    Opening a project already takes such a lock over; this removes it without
    opening. An unreadable lock file is removed (it names no holder to protect).
    Refuses when the holder still runs on this host, or when it was taken on
    another host (it cannot be checked from here), unless *force* (an override:
    see the module's text). Returns the removed holder.
    """
    if not layout.lock.exists():
        raise FileNotFoundError(f"no lock at {layout.lock}")
    held = read_lock(layout.lock)
    if held is None:
        layout.lock.unlink()
        return None
    gone = force or _holder_gone(layout.lock, held)
    if not gone or not _remove_if_gone(layout.lock, held, force=force):
        raise LockHeld(layout.lock, held)
    return held
