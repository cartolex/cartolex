# SPDX-License-Identifier: MIT
"""Writing a project's files: atomic writes, fingerprints and guarded decision writes.

Every write goes to a temporary file in the target's folder, is flushed to disk,
then renamed over the target, so a reader sees the old file or the new one and
never a part of either. Decision files are written with the fingerprint of the
version the writer read: if the file changed in between, the write is refused
(:class:`StaleWrite`) and nothing is lost; an accepted write first moves the
previous version into ``decisions/history/``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .layout import ProjectLayout
from .lock import ensure_held

#: Serialises decision writes inside one process (the project lock covers other processes).
_DECISION_LOCK = threading.Lock()

__all__ = [
    "StaleWrite",
    "atomic_write_bytes",
    "fingerprint",
    "json_bytes",
    "read_model",
    "replace_path",
    "utc_stamp",
    "write_decision",
]


class StaleWrite(RuntimeError):
    """A decision file changed after the writer read it; nothing was written."""

    def __init__(self, path: Path, expected: str | None, found: str | None) -> None:
        self.path, self.expected, self.found = path, expected, found
        super().__init__(
            f"{path.name} changed since it was read (read {expected or 'no file'}, "
            f"now {found or 'no file'}); reload it and apply the change again"
        )


def utc_stamp(now: datetime | None = None) -> str:
    """A compact UTC time for file names and run ids: ``20260928T101200Z``."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fingerprint(path: Path) -> str | None:
    """``sha256:<hex>`` of a file's bytes, or ``None`` when it does not exist."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
    except FileNotFoundError:
        return None
    return "sha256:" + h.hexdigest()


def json_bytes(obj: Any) -> bytes:
    """The canonical text of a JSON file: two-space indent, UTF-8, a final newline."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json", by_alias=True, exclude_none=False)
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def atomic_write_bytes(path: Path, data: bytes, *, durable: bool = True) -> None:
    """Write *data* to *path* so that a reader never sees a partial file; *durable* also
    waits for the disk (off for a cache entry that can be fetched again: thousands of
    them would otherwise wait minutes)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            if durable:
                os.fsync(fh.fileno())
        replace_path(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    if durable:
        _fsync_dir(path.parent)


def replace_path(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
    """Rename *src* over *dst* (:func:`os.replace`), retried for a moment on Windows.

    There a file cannot be replaced, nor a folder renamed, while another thread or
    process has it (or a file inside it) open: a reader of the old version refuses
    the rename for as long as it reads. Elsewhere the rename is done once.
    """
    if sys.platform != "win32":
        os.replace(src, dst)
        return
    delay = 0.05
    for attempt in range(8):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def _fsync_dir(folder: Path) -> None:
    """Make a rename durable (POSIX); a no-op where directories cannot be opened."""
    if os.name != "posix":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def read_model(path: Path, model: type[BaseModel]) -> BaseModel:
    """Read and validate a JSON file against *model*; the error names the file."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc
    try:
        return model.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError, reworded with the file name
        raise ValueError(f"{path}: {exc}") from exc


def write_decision(
    layout: ProjectLayout,
    path: Path,
    data: bytes,
    *,
    expected: str | None,
    action: str,
    now: datetime | None = None,
) -> str:
    """Write a decision file if it is still the version the writer read.

    *expected* is the fingerprint the writer read (``None``: it read no file).
    The previous version, if any, is copied to the file's history folder as
    ``<UTC time>-<action><suffix>`` before the new version replaces it. Returns
    the new fingerprint. Raises :class:`StaleWrite` when the file changed, and
    :class:`~cartolex.project.lock.LockLost` when this process's lock on the
    project was overridden.
    """
    path = Path(path)
    ensure_held(layout.lock)
    with _DECISION_LOCK:
        return _write_decision(layout, path, data, expected=expected, action=action, now=now)


def _write_decision(
    layout: ProjectLayout,
    path: Path,
    data: bytes,
    *,
    expected: str | None,
    action: str,
    now: datetime | None,
) -> str:
    found = fingerprint(path)
    if found != expected:
        raise StaleWrite(path, expected, found)
    if found is not None:
        stamp = utc_stamp(now)
        slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in action)[:40] or "write"
        folder = layout.history_of(path)
        target = folder / f"{stamp}-{slug}{path.suffix}"
        n = 1
        while target.exists():
            n += 1
            target = folder / f"{stamp}-{slug}-{n}{path.suffix}"
        atomic_write_bytes(target, path.read_bytes())
    atomic_write_bytes(path, data)
    return "sha256:" + hashlib.sha256(data).hexdigest()
