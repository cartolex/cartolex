# SPDX-License-Identifier: MIT
"""The resources a computer gives to cartolex: memory, worker processes, a scratch folder."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Budget", "resident_mb", "total_memory_mb"]

#: The memory a budget takes by default: this share of the computer's, at most the cap.
MEMORY_SHARE = 0.4
MEMORY_CAP_MB = 12_288


def total_memory_mb() -> int | None:
    """The computer's memory in MB, or ``None`` when it cannot be read."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) // 1024
            return None
        if sys.platform == "darwin":
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            return int(out) // (1024 * 1024) if out.isdigit() else None
        if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys // (1024 * 1024))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def resident_mb() -> float | None:
    """The memory this process holds now, in MB (its peak where the system does not say
    the present; ``None`` when it cannot be read)."""
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError):
        return None
    return peak / 2**20 if sys.platform == "darwin" else peak / 1024


@dataclass(frozen=True)
class Budget:
    """What a computer gives to cartolex's long jobs.

    *memory_mb* is the memory a job may use in all, its worker processes included;
    *workers* how many worker processes it may run; *scratch* a folder on a fast local
    disk for its temporary files (``None``: the project's ``cache/``). A job sizes its
    batches and its worker pool from the budget; its results never depend on it.
    """

    memory_mb: int
    workers: int
    scratch: Path | None = None

    @classmethod
    def for_machine(
        cls,
        *,
        memory_mb: int | None = None,
        workers: int | None = None,
        scratch: Path | None = None,
    ) -> Budget:
        """This computer's budget: what is given, else 40 % of its memory (at most
        12 GB) and three quarters of its processors less one."""
        if memory_mb is None:
            total = total_memory_mb()
            memory_mb = min(MEMORY_CAP_MB, int(total * MEMORY_SHARE)) if total else 4096
        if workers is None:
            workers = max(1, (os.cpu_count() or 2) * 3 // 4 - 1)
        return cls(int(memory_mb), max(1, int(workers)), Path(scratch) if scratch else None)

    @classmethod
    def given(cls) -> Budget | None:
        """The budget this process was given for this computer (:func:`give_budget`: the
        app's Settings › Build), or ``None``."""
        return _GIVEN

    def workers_for(self, worker_mb: float, parent_mb: float = 0.0) -> int:
        """How many workers of *worker_mb* each fit, beside a parent holding *parent_mb*."""
        room = self.memory_mb - parent_mb
        return max(1, min(self.workers, int(room // max(worker_mb, 1.0))))


#: The budget the person set for this computer, given to this process (see :func:`give_budget`).
_GIVEN: Budget | None = None


def give_budget(budget: Budget | None) -> None:
    """Give this process the budget the person set for this computer (the app does, from
    Settings › Build): the jobs it starts without a budget of their own (a collection's
    rebuild of the tables) take its scratch folder and workers. ``None``: none given."""
    global _GIVEN
    _GIVEN = budget
