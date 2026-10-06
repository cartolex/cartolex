# SPDX-License-Identifier: MIT
"""Writing a project's files: atomic writes, fingerprints and guarded decision writes.

Every write goes to a temporary file in the target's folder, is flushed to disk,
then renamed over the target, so a reader sees the old file or the new one and
never a part of either. Decision files are written with the fingerprint of the
version the writer read: if the file changed in between, the write is refused
(:class:`StaleWrite`) and nothing is lost; an accepted write first moves the
previous version into ``decisions/history/``.

A large file of CSV rows (``people.csv`` of a national project is megabytes) keeps
most of its earlier versions as **deltas**: ``<version>.csv.delta``, gzipped JSON
(``cartolex-history-delta/1``) saying how the latest earlier version kept whole turns
into it, as runs of that version's records and the records it did not have. Every
:data:`FULL_EVERY` versions, and whenever more than half of it is new, the version
is kept whole. A delta depends only on a file of the history, which never
changes: editing the current file by hand breaks nothing. :func:`read_version` gives
any version's bytes, checked against the hashes its delta keeps.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import os
import re
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
    "HistoryBroken",
    "StaleWrite",
    "atomic_write_bytes",
    "fingerprint",
    "json_bytes",
    "history_versions",
    "read_model",
    "read_version",
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
        name = f"{stamp}-{slug}"
        n = 1
        while (folder / f"{name}{path.suffix}").exists() or (
            folder / f"{name}{path.suffix}{DELTA_SUFFIX}"
        ).exists():
            n += 1
            name = f"{stamp}-{slug}-{n}"
        previous = path.read_bytes()
        delta = _delta_entry(folder, path, previous)
        if delta is not None:
            atomic_write_bytes(folder / f"{name}{path.suffix}{DELTA_SUFFIX}", delta)
        else:
            atomic_write_bytes(folder / f"{name}{path.suffix}", previous)
    atomic_write_bytes(path, data)
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ── earlier versions kept as deltas ──────────────────────────────────────────

#: A CSV decision file this large or larger keeps its earlier versions as deltas.
DELTA_ABOVE = 256 * 1024
#: One version in this many is kept whole, so a version is never more than that many
#: deltas away from one.
FULL_EVERY = 50
DELTA_FORMAT = "cartolex-history-delta/1"
DELTA_SUFFIX = ".delta"
_VERSION_NAME = re.compile(r"[\w-]{1,128}")


class HistoryBroken(RuntimeError):
    """An earlier version cannot be rebuilt: a delta does not apply to what follows it."""


def _records(data: bytes) -> list[bytes]:
    """CSV bytes as records, each with its line end (a quoted field may span lines)."""
    out: list[bytes] = []
    pending: list[bytes] = []
    quotes = 0
    for line in data.splitlines(keepends=True):
        pending.append(line)
        quotes += line.count(b'"')
        if quotes % 2 == 0:
            out.append(b"".join(pending))
            pending, quotes = [], 0
    if pending:
        out.append(b"".join(pending))
    return out


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _delta(old: bytes, new: bytes) -> dict[str, Any] | None:
    """How *new* turns into *old*: runs ``[start, count]`` of new's records and the text
    of the records new does not have; ``None`` when *old* is not UTF-8 text."""
    base = _records(new)
    first: dict[bytes, int] = {}
    for i, r in enumerate(base):
        first.setdefault(r, i)
    ops: list[Any] = []
    for r in _records(old):
        j = first.get(r)
        if j is None:
            try:
                text = r.decode("utf-8")
            except UnicodeDecodeError:
                return None
            if ops and isinstance(ops[-1], str):
                ops[-1] += text
            else:
                ops.append(text)
        elif ops and isinstance(ops[-1], list) and ops[-1][0] + ops[-1][1] == j:
            ops[-1][1] += 1
        else:
            ops.append([j, 1])
    return {"format": DELTA_FORMAT, "base": _sha(new), "result": _sha(old), "ops": ops}


def _apply(delta: dict[str, Any], base: bytes) -> bytes:
    if delta.get("format") != DELTA_FORMAT or _sha(base) != delta.get("base"):
        raise HistoryBroken("a delta does not apply to the version it was made from")
    records = _records(base)
    parts = [
        b"".join(records[op[0] : op[0] + op[1]]) if isinstance(op, list) else op.encode("utf-8")
        for op in delta["ops"]
    ]
    out = b"".join(parts)
    if _sha(out) != delta.get("result"):
        raise HistoryBroken("a delta did not give back the version it was made from")
    return out


def history_versions(folder: Path, path: Path) -> list[tuple[str, Path, bool]]:
    """The earlier versions of *path* kept in *folder*: (version, file, is a delta), oldest
    first (their names start with the second they were replaced)."""
    if not folder.is_dir():
        return []
    out = []
    whole, delta = path.suffix, path.suffix + DELTA_SUFFIX
    for f in folder.iterdir():
        if not f.is_file():
            continue
        if f.name.endswith(delta):
            out.append((f.name[: -len(delta)], f, True))
        elif f.name.endswith(whole):
            out.append((f.name[: -len(whole)], f, False))
    # By the second they were replaced (the name's start), then in the order they were written.
    return sorted(out, key=lambda e: (e[0][:16], e[1].stat().st_mtime_ns, e[0]))


def _delta_entry(folder: Path, path: Path, previous: bytes) -> bytes | None:
    """The delta to keep for *previous*, from the latest earlier version kept whole; or
    ``None``: keep it whole (no such version, too many deltas since, or more than half
    of it new)."""
    if path.suffix != ".csv" or len(previous) < DELTA_ABOVE:
        return None
    since = 0
    whole: tuple[str, Path, bool] | None = None
    for entry in reversed(history_versions(folder, path)):
        if not entry[2]:
            whole = entry
            break
        since += 1
    if whole is None or since >= FULL_EVERY - 1:
        return None
    delta = _delta(previous, whole[1].read_bytes())
    if delta is None:
        return None
    # A version that shares little with that one is kept whole: the next ones start from it.
    if sum(len(op) for op in delta["ops"] if isinstance(op, str)) > len(previous) // 2:
        return None
    delta["of"] = whole[0]
    return gzip.compress(json.dumps(delta, ensure_ascii=False).encode("utf-8"), 6, mtime=0)


def read_version(layout: ProjectLayout, path: Path, version: str) -> bytes | None:
    """The bytes of an earlier *version* of decision file *path* (``None``: no such
    version), kept whole or rebuilt from its delta; raises :class:`HistoryBroken` when
    the delta does not apply."""
    folder = layout.history_of(path)
    whole = folder / f"{version}{path.suffix}"
    if whole.is_file():
        return whole.read_bytes()
    kept = folder / f"{version}{path.suffix}{DELTA_SUFFIX}"
    if not kept.is_file():
        return None
    try:
        delta = json.loads(gzip.decompress(kept.read_bytes()))
    except (OSError, ValueError) as exc:
        raise HistoryBroken(f"{kept.name} cannot be read: {exc}") from exc
    of = delta.get("of")
    if not isinstance(of, str) or not _VERSION_NAME.fullmatch(of):
        raise HistoryBroken(f"{kept.name} names no version it was made from")
    base = folder / f"{of}{path.suffix}"
    if not base.is_file():
        raise HistoryBroken(f"{kept.name}: the version it was made from is gone")
    return _apply(delta, base.read_bytes())
