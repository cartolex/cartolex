# SPDX-License-Identifier: MIT
"""Scratch folders: a job's temporary files, on a fast local disk.

A job removes its folder when it ends; a job that was killed (out of memory, the
computer shut down) cannot. Each folder is therefore named after the computer and
the process that made it, ``<prefix>-<host>-<pid>-<random>``, and making a folder
first removes the ones of the same prefix left by a process of this computer that
no longer runs. A folder of another computer (a shared scratch disk), or of a
process still running, is left alone.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

__all__ = ["pid_alive", "scratch_folder"]


def pid_alive(pid: int) -> bool:
    """Whether a process with this id runs on this computer."""
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


def _host() -> str:
    return hashlib.blake2b(socket.gethostname().encode("utf-8"), digest_size=3).hexdigest()


def scratch_folder(prefix: str, parent: Path | str) -> Path:
    """A new, empty folder for *prefix*'s temporary files under *parent* (created if
    needed), after removing the ones a killed process of this computer left there."""
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    host = _host()
    for old in parent.glob(f"{prefix}-{host}-*-*"):
        pid = old.name[len(prefix) + len(host) + 2 :].split("-", 1)[0]
        if old.is_dir() and pid.isdigit() and not pid_alive(int(pid)):
            shutil.rmtree(old, ignore_errors=True)
    return Path(tempfile.mkdtemp(prefix=f"{prefix}-{host}-{os.getpid()}-", dir=parent))
