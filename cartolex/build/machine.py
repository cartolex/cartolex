# SPDX-License-Identifier: MIT
"""What the machine offers: available memory, and the peak memory a stage used.

Both use only the standard library. Available memory is read from
``/proc/meminfo`` on Linux, ``vm_stat`` on macOS and ``GlobalMemoryStatusEx``
on Windows; elsewhere it is unknown (``None``) and no budget is enforced unless
one is given.

The peak memory of a stage is its process's peak resident memory while it ran:
exact on Linux (the kernel's high-water mark is reset when the stage starts),
and on other systems the process's peak since it started, an upper bound. When
worker processes ran during the stage, the largest of them is added.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys

__all__ = ["PeakMemory", "available_memory_mb", "boot_id"]

_MB = 1024 * 1024


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


class PeakMemory:
    """Measure the peak memory of a ``with`` block (see the module's notes on precision)."""

    def __init__(self) -> None:
        self.peak_mb: float | None = None
        self._children_before: float | None = None
        self._reset = False

    def __enter__(self) -> PeakMemory:
        if sys.platform.startswith("linux"):
            with contextlib.suppress(OSError):
                with open("/proc/self/clear_refs", "w", encoding="ascii") as fh:
                    fh.write("5")  # reset the peak resident size of this process
                self._reset = True
        if os.name == "posix":
            import resource

            self._children_before = _maxrss_mb(resource.RUSAGE_CHILDREN)
        return self

    def __exit__(self, *exc: object) -> None:
        peak: float | None = None
        if self._reset:
            hwm = _linux_status_kb("VmHWM")
            peak = hwm / 1024 if hwm is not None else None
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
