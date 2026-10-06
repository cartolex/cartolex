# SPDX-License-Identifier: MIT
"""What the machine offers: available memory, and the peak memory a stage used.

Both use only the standard library. Available memory is read from
``/proc/meminfo`` on Linux, ``vm_stat`` on macOS and ``GlobalMemoryStatusEx``
on Windows; elsewhere it is unknown (``None``) and no budget is enforced unless
one is given.

The peak memory of a stage, on Linux, is the largest memory its process and their
descendants (its worker processes) held together while it ran: sampled every
:data:`SAMPLE_S`, each process counted by its proportional share (``Pss``: pages
two processes share, a forked worker's or a mapped file's, count once), and at
least the process's own high-water mark, which the kernel resets when the stage
starts. Elsewhere it is the process's peak since it started, an upper bound, and
the largest worker process that ran during the stage is added.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import threading

__all__ = ["PeakMemory", "available_memory_mb", "boot_id", "resident_memory_mb"]

_MB = 1024 * 1024
#: Seconds between two samples of a stage's processes' memory (Linux).
SAMPLE_S = 0.5


def available_memory_mb() -> float | None:
    """The memory available to a new job, in MB, or ``None`` when it cannot be read."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) / 1024
            return None
        if sys.platform == "darwin":
            out = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5, check=True
            ).stdout
            size = re.search(r"page size of (\d+) bytes", out)
            page = int(size.group(1)) if size else 4096
            free = 0
            for label in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
                found = re.search(rf"{label}:\s+(\d+)", out)
                free += int(found.group(1)) if found else 0
            return free * page / _MB
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
                return status.ullAvailPhys / _MB
            return None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def resident_memory_mb() -> float:
    """The memory this process holds now, in MB (0 where it cannot be read cheaply).

    A stage's peak memory is measured for the whole process, so the memory it
    may use is what is available plus what the process already holds.
    """
    if sys.platform.startswith("linux"):
        rss = _linux_status_kb("VmRSS")
        return rss / 1024 if rss is not None else 0.0
    return 0.0


def boot_id() -> str | None:
    """An id of the machine's current boot (``None`` when it cannot be read).

    Chunk checkpoints are not forced to disk, so a killed run is resumed only on
    the boot that wrote them: after a power cut, the run starts over.
    """
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as fh:
                return fh.read().strip() or None
        if sys.platform == "darwin":
            out = subprocess.run(
                ["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            return out or None
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _linux_status_kb(field: str) -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith(field + ":"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def _maxrss_mb(who: int) -> float | None:
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows
        return None
    peak = resource.getrusage(who).ru_maxrss
    return peak / _MB if sys.platform == "darwin" else peak / 1024  # bytes on macOS, KB elsewhere


def _windows_peak_mb() -> float | None:  # pragma: no cover - exercised on Windows only
    try:
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return counters.PeakWorkingSetSize / _MB
    except (OSError, AttributeError):
        return None
    return None


def _tree_memory_kb(root: int) -> int:
    """The memory process *root* and its descendants hold now, in KB: the sum of their
    proportional shares (their resident memory where the share cannot be read)."""
    parent_of: dict[int, int] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                stat = fh.read()
            parent_of[int(name)] = int(stat[stat.rindex(b")") + 2 :].split()[1])
        except (OSError, ValueError, IndexError):
            continue
    children: dict[int, list[int]] = {}
    for pid, ppid in parent_of.items():
        children.setdefault(ppid, []).append(pid)
    total, todo = 0, [root]
    while todo:
        pid = todo.pop()
        todo.extend(children.get(pid, ()))
        for path, field in (
            (f"/proc/{pid}/smaps_rollup", "Pss:"),
            (f"/proc/{pid}/status", "VmRSS:"),
        ):
            try:
                with open(path, encoding="ascii") as fh:
                    kb = next((int(line.split()[1]) for line in fh if line.startswith(field)), None)
            except (OSError, ValueError):
                kb = None
            if kb is not None:
                total += kb
                break
    return total


class _Sampler(threading.Thread):
    """Every :data:`SAMPLE_S` until stopped, the memory of this process's tree; the
    largest kept (KB)."""

    def __init__(self) -> None:
        super().__init__(name="cartolex-peak-memory", daemon=True)
        self.peak_kb = 0
        self._done = threading.Event()

    def run(self) -> None:
        root = os.getpid()
        while True:
            with contextlib.suppress(OSError):
                self.peak_kb = max(self.peak_kb, _tree_memory_kb(root))
            if self._done.wait(SAMPLE_S):
                return

    def stop(self) -> int:
        self._done.set()
        self.join()
        return self.peak_kb


class PeakMemory:
    """Measure the peak memory of a ``with`` block (see the module's notes on precision)."""

    def __init__(self) -> None:
        self.peak_mb: float | None = None
        self._children_before: float | None = None
        self._reset = False
        self._sampler: _Sampler | None = None

    def __enter__(self) -> PeakMemory:
        if sys.platform.startswith("linux"):
            with contextlib.suppress(OSError):
                with open("/proc/self/clear_refs", "w", encoding="ascii") as fh:
                    fh.write("5")  # reset the peak resident size of this process
                self._reset = True
            if os.path.exists("/proc/self/smaps_rollup"):
                self._sampler = _Sampler()
                self._sampler.start()
        if os.name == "posix" and self._sampler is None:
            import resource

            self._children_before = _maxrss_mb(resource.RUSAGE_CHILDREN)
        return self

    def __exit__(self, *exc: object) -> None:
        peak: float | None = None
        if self._reset:
            hwm = _linux_status_kb("VmHWM")
            peak = hwm / 1024 if hwm is not None else None
        if self._sampler is not None:
            tree = self._sampler.stop() / 1024
            self.peak_mb = round(max(peak or 0.0, tree), 1) if peak or tree else None
            return
        if peak is None:
            if os.name == "posix":
                import resource

                peak = _maxrss_mb(resource.RUSAGE_SELF)
            else:  # pragma: no cover - Windows
                peak = _windows_peak_mb()
        if os.name == "posix" and peak is not None:
            import resource

            children = _maxrss_mb(resource.RUSAGE_CHILDREN)
            if children is not None and children > (self._children_before or 0.0):
                peak += children
        self.peak_mb = round(peak, 1) if peak is not None else None
