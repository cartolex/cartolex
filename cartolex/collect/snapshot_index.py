# SPDX-License-Identifier: MIT
"""An index of a downloaded OpenAlex snapshot, so that a job reads only what it asks for.

Each part OpenAlex ships is one gzip stream: a record in its middle is reached only by
unpacking everything before it, and a person's works are spread over every part (a record
sits in the partition of the date it last changed), so without an index every question is
a pass over the whole snapshot. Building the index cuts each part of the works and of the
authors into small gzip members (about :data:`BLOCK_BYTES` of whole lines each: the file is
still a gzip of the same JSON lines, which anything reads as before) and records which
members hold the works of each author, institution, DOI and work id, and each author's
record. A query then unpacks only the members that may hold what it asks, and tests their
lines as a scan does: the same records come back (:meth:`SnapshotIndex.members`).

Its files, beside the data::

    <folder>/cartolex-index/index.json                  the release, the parts, the keys
    <folder>/cartolex-index/<entity>/blocks.npy         each member's offset and length
    <folder>/cartolex-index/<entity>/first.npy          each part's first member
    <folder>/cartolex-index/<entity>/<key>/NN.keys.npy  the keys of bucket NN, sorted
    <folder>/cartolex-index/<entity>/<key>/NN.refs.npy  the member of each (part, member)
    <folder>/cartolex-index/journal.jsonl               while building: the parts done

Building (:func:`build_index`) reads and rewrites the works and the authors once, which
takes hours on an external disk; it can be stopped and resumed. A part is replaced only
once its new copy is written and synced, and what was found in it is trusted only from
the last point where everything written was synced: a part after it is cut again (cutting
a part already cut gives the same lines). The parts then differ in size from the manifests
of OpenAlex: :meth:`cartolex.collect.snapshot.Snapshot.check` accepts the sizes the index
records.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "BLOCK_BYTES",
    "FORMAT",
    "INDEX_DIR",
    "KEYS",
    "BuildReport",
    "SnapshotIndex",
    "build_index",
    "index_state",
    "indexed_sizes",
]

FORMAT = "cartolex-snapshot-index/1"
INDEX_DIR = "cartolex-index"
#: Uncompressed bytes of whole lines a member holds, about (a line longer is a member alone).
BLOCK_BYTES = 1 << 20
#: The compression of the members: the level of the parts OpenAlex ships (same size).
LEVEL = 6
#: Postings are kept in this many buckets per key (by key modulo), each sorted by key.
BUCKETS = 64
#: A posting's reference: the part's number in its high bits, the member's in the low ones.
_MEMBER_BITS = 20
_MAX_PARTS = 1 << (32 - _MEMBER_BITS)
_MAX_MEMBERS = 1 << _MEMBER_BITS
#: The keys of each entity indexed (the institutions, small, are read whole).
KEYS: dict[str, tuple[str, ...]] = {
    "works": ("id", "author", "institution", "doi"),
    "authors": ("id",),
}
#: Parts between two points where everything written is synced (and can be trusted).
SYNC_EVERY = 16
#: Members closer than this in a part are read in one go.
_GAP = 256 << 10

_POSTING = np.dtype([("key", "<u8"), ("ref", "<u4")])
_BLOCK = np.dtype([("offset", "<u8"), ("length", "<u4")])
_OWN_ID = {
    "works": re.compile(rb'"id"\s*:\s*"https://openalex\.org/(W\d+)"'),
    "authors": re.compile(rb'"id"\s*:\s*"https://openalex\.org/(A\d+)"'),
}
_AUTHOR = re.compile(rb"openalex\.org/(A\d+)")
_INSTITUTION = re.compile(rb"openalex\.org/(I\d+)")
_DOI = re.compile(rb'"doi"\s*:\s*"https?://(?:dx\.)?doi\.org/([^"]+)"', re.IGNORECASE)


def doi_key(doi: bytes) -> int:
    """The number a DOI is indexed under: a hash of it in lower case (two DOIs may share
    one: a query tests the lines it reads, so a shared number only costs a member read)."""
    return int.from_bytes(hashlib.blake2b(doi.lower(), digest_size=8).digest(), "little")


def _id_number(value: bytes | str) -> int:
    return int(value[1:])


def _line_keys(entity: str, data: bytes) -> dict[str, set[int]]:
    """The keys of a member's lines: what :func:`cartolex.collect.snapshot._tests` looks
    for in a raw line (every author and institution id of a work, its DOIs), and the
    records' own ids."""
    out = {"id": {_id_number(m) for m in _OWN_ID[entity].findall(data)}}
    if entity == "works":
        out["author"] = {_id_number(m) for m in set(_AUTHOR.findall(data))}
        out["institution"] = {_id_number(m) for m in set(_INSTITUTION.findall(data))}
        out["doi"] = {doi_key(m) for m in set(_DOI.findall(data))}
    return out


# ── the state of an index ────────────────────────────────────────────────────────────


def _folder(root: Path) -> Path:
    return Path(root) / INDEX_DIR


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def index_state(root: Path) -> dict[str, Any] | None:
    """The index of the snapshot at *root*: ``None`` without one; else its ``state``
    (``complete``, or ``building``: stopped or running), its release and when it was built,
    and the parts cut so far."""
    folder = _folder(root)
    done = _read_json(folder / "index.json")
    if done is not None and done.get("format") == FORMAT and done.get("complete"):
        parts = sum(len(e["parts"]) for e in done["entities"].values())
        return {"state": "complete", "release": done.get("release"),
                "built_at": done.get("built_at"), "parts": parts, "done": parts}  # fmt: skip
    plan = _read_json(folder / "building.json")
    if plan is None:
        return None
    total = sum(len(v) for v in plan.get("parts", {}).values())
    done = [k for k in _journal(folder)[0] if k != ("", "")]  # not the sync point
    return {"state": "building", "release": plan.get("release"), "built_at": None,
            "parts": total, "done": len(done)}  # fmt: skip


def indexed_sizes(root: Path) -> dict[tuple[str, str], int]:
    """``(entity, part)`` → the size of the part once cut, for every part the index (built,
    or being built) has cut: the sizes :meth:`Snapshot.check` accepts beside the manifests'."""
    folder = _folder(root)
    done = _read_json(folder / "index.json")
    out: dict[tuple[str, str], int] = {}
    if done is not None and done.get("format") == FORMAT:
        for entity, e in (done.get("entities") or {}).items():
            for p in e.get("parts") or []:
                out[(entity, p["path"])] = int(p["size"])
    if (folder / "building.json").is_file():
        _, entries = _journal(folder, trusted_only=False)
        for entry in entries:
            out[(entry["entity"], entry["path"])] = int(entry["size"])
    return out


def _journal(
    folder: Path, *, trusted_only: bool = True
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """The parts the journal says are done (up to its last sync point when *trusted_only*),
    and every entry; the spill sizes of that point are in the done map under ``("", "")``."""
    path = folder / "journal.jsonl"
    entries: list[dict[str, Any]] = []
    done: dict[tuple[str, str], dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            break  # a line cut by a stop: what follows is not trusted
        if entry.get("sync"):
            for e in pending:
                done[(e["entity"], e["path"])] = e
            pending = []
            done[("", "")] = entry
            continue
        entries.append(entry)
        pending.append(entry)
    if not trusted_only:
        for e in pending:
            done[(e["entity"], e["path"])] = e
    return done, entries


# ── building ─────────────────────────────────────────────────────────────────────────


@dataclass
class BuildReport:
    """What a build did: the parts cut (now and before), the bytes read, the members and
    postings written, the seconds it took."""

    parts: int = 0
    resumed: int = 0
    bytes_in: int = 0
    members: int = 0
    postings: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    complete: bool = False

    def lines(self) -> list[str]:
        out = [f"{self.parts} part(s) cut ({self.resumed} done before), {self.members} members, "
               f"{self.bytes_in / 1e9:,.1f} GB read in {self.seconds / 60:,.0f} min"]  # fmt: skip
        out += [f"  {k}: {n:,} postings" for k, n in sorted(self.postings.items())]
        out.append("the index is complete" if self.complete else "stopped: run it again to go on")
        return out


def _cut_part(
    source: str, target: str, entity: str, block_bytes: int, level: int
) -> dict[str, Any]:
    """Cut one part into members of whole lines, written to *target* (synced): their
    offsets and lengths, the lines and bytes read, and each key's postings (key, member).
    Reads a part already cut the same way (a gzip of several members). Runs in a worker."""
    offsets: list[int] = []
    lengths: list[int] = []
    keys: dict[str, list[np.ndarray]] = {k: [] for k in KEYS[entity]}
    members: dict[str, list[np.ndarray]] = {k: [] for k in KEYS[entity]}
    lines = bytes_in = 0
    pos = 0
    pending = bytearray()

    def emit(data: bytes, out: Any) -> None:
        nonlocal pos, lines
        member = len(offsets)
        if member >= _MAX_MEMBERS:
            raise ValueError(f"{source}: more than {_MAX_MEMBERS} members in one part")
        packer = zlib.compressobj(level, zlib.DEFLATED, 31)
        packed = packer.compress(data) + packer.flush()
        out.write(packed)
        offsets.append(pos)
        lengths.append(len(packed))
        pos += len(packed)
        lines += data.count(b"\n")
        for name, found in _line_keys(entity, data).items():
            if found:
                keys[name].append(np.fromiter(found, dtype=np.uint64, count=len(found)))
                members[name].append(np.full(len(found), member, dtype=np.uint32))

    with open(source, "rb") as raw, open(target, "wb") as out:
        unpack = zlib.decompressobj(31)
        fed = False  # the current member has begun (and must end)
        while True:
            chunk = raw.read(8 << 20)
            if not chunk:
                break
            bytes_in += len(chunk)
            while chunk:
                fed = True
                pending += unpack.decompress(chunk)
                if unpack.eof:  # one member ends: another may follow
                    chunk = unpack.unused_data
                    unpack = zlib.decompressobj(31)
                    fed = False
                else:
                    chunk = b""
                start = 0
                while len(pending) - start >= block_bytes:
                    cut = pending.find(b"\n", start + block_bytes - 1)
                    if cut < 0:
                        break
                    emit(bytes(pending[start : cut + 1]), out)
                    start = cut + 1
                if start:
                    del pending[:start]
        if fed and not unpack.eof:
            raise ValueError(f"{source}: the gzip stream is cut short")
        if pending:
            if not pending.endswith(b"\n"):
                pending += b"\n"
            emit(bytes(pending), out)
        out.flush()
        os.fsync(out.fileno())
    return {
        "offsets": np.asarray(offsets, dtype=np.uint64),
        "lengths": np.asarray(lengths, dtype=np.uint32),
        "lines": lines,
        "bytes_in": bytes_in,
        "size": pos,
        "keys": {
            k: (
                np.concatenate(keys[k]) if keys[k] else np.empty(0, np.uint64),
                np.concatenate(members[k]) if members[k] else np.empty(0, np.uint32),
            )
            for k in keys
        },  # fmt: skip
    }


def _parts_to_index(snapshot: Any) -> dict[str, list[tuple[str, int]]]:
    """Each indexed entity's parts, ``(updated_date=…/part_….gz, manifest size)``, in the
    order that numbers them (from the manifests, else the parts found)."""
    from .snapshot import _listed_parts

    out: dict[str, list[tuple[str, int]]] = {}
    for entity in KEYS:
        folder = snapshot.entity_dir(entity)
        if folder is None:
            raise FileNotFoundError(f"{snapshot.root} has no {entity}: the index needs them")
        listed = _listed_parts(folder / "manifest.json")
        if listed is None:
            listed = [(f"{p.path.parent.name}/{p.path.name}", p.size)
                      for p in snapshot.partitions(entity)]  # fmt: skip
        out[entity] = sorted(listed)
        if len(listed) > _MAX_PARTS:
            raise ValueError(f"{entity}: more than {_MAX_PARTS} parts")
    return out


class _Spill:
    """The postings found so far, appended per key and bucket to files that are sorted
    once every part is cut; truncated back to a sync point when a build resumes."""

    def __init__(self, folder: Path, sizes: Mapping[str, int] | None) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, Any] = {}
        known = dict(sizes or {})
        for path in folder.glob("*/*.bin"):  # back to the last sync point
            rel = f"{path.parent.name}/{path.name}"
            size = known.get(rel, 0)
            if path.stat().st_size != size:
                with open(path, "r+b") as fh:
                    fh.truncate(size)

    def _file(self, rel: str) -> Any:
        fh = self._files.get(rel)
        if fh is None:
            path = self.folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = self._files[rel] = open(path, "ab")  # noqa: SIM115 (kept open while building)
        return fh

    def add(self, entity: str, key: str, part: int, found: tuple[np.ndarray, np.ndarray]) -> int:
        values, members = found
        if not len(values):
            return 0
        rows = np.empty(len(values), dtype=_POSTING)
        rows["key"] = values
        rows["ref"] = (np.uint32(part) << np.uint32(_MEMBER_BITS)) | members
        buckets = values % BUCKETS
        order = np.argsort(buckets, kind="stable")
        rows, buckets = rows[order], buckets[order]
        edges = np.flatnonzero(np.diff(buckets)) + 1
        for start, end in zip(np.r_[0, edges], np.r_[edges, len(rows)], strict=True):
            self._file(f"{entity}.{key}/{int(buckets[start]):02d}.bin").write(
                rows[start:end].tobytes()
            )
        return len(rows)

    def sync(self) -> dict[str, int]:
        """Write everything down; the size of every file (the sync point)."""
        for fh in self._files.values():
            fh.flush()
            os.fsync(fh.fileno())
        return {f"{p.parent.name}/{p.name}": p.stat().st_size for p in self.folder.glob("*/*.bin")}

    def close(self) -> None:
        for fh in self._files.values():
            fh.close()
        self._files.clear()


def _append_journal(folder: Path, entry: Mapping[str, Any], *, sync: bool = False) -> None:
    with open(folder / "journal.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        if sync:
            fh.flush()
            os.fsync(fh.fileno())


def build_index(
    root: Path | str,
    *,
    jobs: int = 1,
    block_bytes: int = BLOCK_BYTES,
    level: int = LEVEL,
    progress: Callable[[float, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    stop_after: int | None = None,
) -> BuildReport:
    """Build (or go on building) the index of the snapshot at *root*: every part of the
    works and the authors cut into members, in *jobs* worker processes, then the postings
    sorted. The snapshot must be complete. *cancel* is asked between parts; *stop_after*
    stops after that many parts (to try a build). Returns what was done."""
    from .http import Cancelled
    from .snapshot import Snapshot

    started = time.perf_counter()
    root = Path(root)
    snapshot = Snapshot(root)
    check = snapshot.check()
    if not check.complete:
        raise ValueError(
            f"{root}: the snapshot is not complete ({check.missing} part(s) missing, "
            f"absent: {', '.join(check.absent) or 'none'}): finish its download first"
        )
    folder = _folder(root)
    folder.mkdir(exist_ok=True)
    plan_path = folder / "building.json"
    parts = _parts_to_index(snapshot)
    plan = {"format": FORMAT, "release": check.release, "block_bytes": block_bytes,
            "level": level, "parts": {e: [p for p, _ in v] for e, v in parts.items()}}  # fmt: skip
    old = _read_json(plan_path)
    if old is not None and {k: old.get(k) for k in plan} != plan:
        raise ValueError(
            f"{folder} holds a build of another release or with other settings: remove "
            "the folder to start again"
        )
    if (folder / "index.json").is_file():
        (folder / "index.json").unlink()  # built again: the old one no longer says the truth
    plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")

    done, _ = _journal(folder)
    point = done.pop(("", ""), {})
    spill = _Spill(folder / "spill", point.get("spill"))
    report = BuildReport(resumed=len(done))
    todo = [(e, n, rel, size) for e, items in parts.items()
            for n, (rel, size) in enumerate(items) if (e, rel) not in done]  # fmt: skip
    total = sum(size for *_, size in todo) or 1
    read = 0
    since_sync = 0

    def say(message: str) -> None:
        if progress is not None:
            progress(min(1.0, read / total), message)

    def finish_part(entity: str, number: int, rel: str, result: Mapping[str, Any]) -> None:
        nonlocal read, since_sync
        part = snapshot.entity_dir(entity) / rel  # type: ignore[operator]
        tmp = part.with_name(part.name + ".cutting")
        blocks = np.empty(len(result["offsets"]), dtype=_BLOCK)
        blocks["offset"], blocks["length"] = result["offsets"], result["lengths"]
        table = folder / entity / "parts" / f"{number:05d}.npy"
        table.parent.mkdir(parents=True, exist_ok=True)
        np.save(table, blocks, allow_pickle=False)
        for key, found in result["keys"].items():
            report.postings[f"{entity}.{key}"] = report.postings.get(f"{entity}.{key}", 0) + (
                spill.add(entity, key, number, found)
            )
        os.replace(tmp, part)  # the new copy is synced: it replaces the part
        _append_journal(folder, {"entity": entity, "path": rel, "part": number,
                                 "size": int(result["size"]), "members": len(blocks),
                                 "lines": int(result["lines"])})  # fmt: skip
        report.parts += 1
        report.members += len(blocks)
        report.bytes_in += int(result["bytes_in"])
        read += int(result["bytes_in"])
        since_sync += 1
        if since_sync >= SYNC_EVERY:
            sync_point()

    def sync_point() -> None:
        nonlocal since_sync
        for table in (folder / e / "parts" for e in KEYS):
            if table.is_dir():
                fd = os.open(table, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        sizes = spill.sync()
        _append_journal(folder, {"sync": True, "spill": sizes}, sync=True)
        since_sync = 0

    limit = len(todo) if stop_after is None else min(len(todo), stop_after)
    work = todo[:limit]
    try:
        if jobs > 1 and len(work) > 1:
            import multiprocessing
            from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

            context = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=jobs, mp_context=context) as pool:
                queue = list(work)
                running: dict[Any, tuple[str, int, str]] = {}
                while queue or running:
                    while queue and len(running) < jobs + 1:
                        entity, number, rel, _ = queue.pop(0)
                        part = snapshot.entity_dir(entity) / rel  # type: ignore[operator]
                        future = pool.submit(_cut_part, str(part),
                                             str(part.with_name(part.name + ".cutting")),
                                             entity, block_bytes, level)  # fmt: skip
                        running[future] = (entity, number, rel)
                    finished, _ = wait(running, timeout=1.0, return_when=FIRST_COMPLETED)
                    for future in finished:
                        entity, number, rel = running.pop(future)
                        finish_part(entity, number, rel, future.result())
                        say(f"index: {entity} {rel}")
                    if cancel is not None and cancel():
                        for future in running:
                            future.cancel()
                        queue.clear()
                        # Let the parts being cut finish (their copies are written whole).
                        for future in list(running):
                            entity, number, rel = running.pop(future)
                            try:
                                finish_part(entity, number, rel, future.result())
                            except Exception:  # noqa: BLE001 (stopping: it is cut again later)
                                pass
                        raise Cancelled("the index build was stopped; run it again to go on")
        else:
            for entity, number, rel, _ in work:
                if cancel is not None and cancel():
                    raise Cancelled("the index build was stopped; run it again to go on")
                part = snapshot.entity_dir(entity) / rel  # type: ignore[operator]
                tmp = part.with_name(part.name + ".cutting")
                finish_part(entity, number, rel,
                            _cut_part(str(part), str(tmp), entity, block_bytes, level))  # fmt: skip
                say(f"index: {entity} {rel}")
    finally:
        sync_point()
        spill.close()
        for entity in KEYS:  # copies left by a stop are cut again
            base = snapshot.entity_dir(entity)
            for tmp in base.glob("updated_date=*/*.cutting") if base else ():
                tmp.unlink(missing_ok=True)
        report.seconds = time.perf_counter() - started
    if limit < len(todo):
        return report
    say("index: sorting the postings")
    _finish(root, folder, parts, plan, report)
    report.complete = True
    report.seconds = time.perf_counter() - started
    return report


def _finish(
    root: Path,
    folder: Path,
    parts: Mapping[str, Sequence[tuple[str, int]]],
    plan: Mapping[str, Any],
    report: BuildReport,
) -> None:
    """Every part is cut: sort each bucket of postings, gather the members' tables, and
    write ``index.json`` (the index is then complete)."""
    done, _ = _journal(folder)
    done.pop(("", ""), None)
    entities: dict[str, Any] = {}
    for entity, items in parts.items():
        tables = [
            np.load(folder / entity / "parts" / f"{n:05d}.npy", allow_pickle=False)
            for n in range(len(items))
        ]
        first = np.zeros(len(items) + 1, dtype=np.uint64)
        first[1:] = np.cumsum([len(t) for t in tables])
        every = np.concatenate(tables) if tables else np.empty(0, _BLOCK)
        np.save(folder / entity / "blocks.npy", every, allow_pickle=False)
        np.save(folder / entity / "first.npy", first, allow_pickle=False)
        shutil.rmtree(folder / entity / "parts")
        keys: dict[str, Any] = {}
        for key in KEYS[entity]:
            out = folder / entity / key
            out.mkdir(parents=True, exist_ok=True)
            postings = distinct = 0
            per_key: list[np.ndarray] = []
            for bucket in range(BUCKETS):
                spilled = folder / "spill" / f"{entity}.{key}" / f"{bucket:02d}.bin"
                rows = np.fromfile(spilled, dtype=_POSTING) if spilled.is_file() else (
                    np.empty(0, _POSTING))  # fmt: skip
                rows = np.unique(rows)  # sorted by key, then member; each pair once
                np.save(out / f"{bucket:02d}.keys.npy", rows["key"], allow_pickle=False)
                np.save(out / f"{bucket:02d}.refs.npy", rows["ref"], allow_pickle=False)
                postings += len(rows)
                if len(rows):
                    starts = np.flatnonzero(np.r_[True, rows["key"][1:] != rows["key"][:-1]])
                    counts = np.diff(np.r_[starts, len(rows)])
                    distinct += len(counts)
                    per_key.append(counts[:: max(1, len(counts) // 20000)])
            sample = np.concatenate(per_key) if per_key else np.zeros(1, np.int64)
            keys[key] = {
                "postings": int(postings),
                "distinct": int(distinct),
                "members_per_key": {
                    q: float(np.percentile(sample, p))
                    for q, p in (("p50", 50), ("p90", 90), ("p99", 99))
                },  # fmt: skip
            }
        entities[entity] = {
            "parts": [
                {
                    "path": rel,
                    "source_size": int(size),
                    "size": int(done[(entity, rel)]["size"]),
                    "members": int(done[(entity, rel)]["members"]),
                }
                for rel, size in items
            ],  # fmt: skip
            "members": int(first[-1]),
            "bytes": int(sum(done[(entity, rel)]["size"] for rel, _ in items)),
            "keys": keys,
        }
    index = {
        "format": FORMAT,
        "release": plan["release"],
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "block_bytes": plan["block_bytes"],
        "level": plan["level"],
        "member_bits": _MEMBER_BITS,
        "buckets": BUCKETS,
        "entities": entities,
        "complete": True,
    }
    tmp = folder / "index.json.part"
    tmp.write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, folder / "index.json")
    shutil.rmtree(folder / "spill", ignore_errors=True)
    for name in ("journal.jsonl", "building.json"):
        (folder / name).unlink(missing_ok=True)


# ── reading ──────────────────────────────────────────────────────────────────────────


class SnapshotIndex:
    """A complete index of a snapshot, opened to answer which members may hold what a
    query asks (:meth:`members`)."""

    def __init__(self, root: Path, data: Mapping[str, Any]) -> None:
        self.root = Path(root)
        self.folder = _folder(root)
        self.data = dict(data)
        self.release = str(data["release"])
        self._blocks: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    @classmethod
    def open(cls, root: Path | str, release: str | None = None) -> SnapshotIndex | None:
        """The index of *root* when it is complete and of *release* (else ``None``: a scan
        reads everything, as without an index)."""
        data = _read_json(_folder(Path(root)) / "index.json")
        if data is None or data.get("format") != FORMAT or not data.get("complete"):
            return None
        if release is not None and data.get("release") != release:
            return None
        return cls(Path(root), data)

    def parts(self, entity: str) -> list[dict[str, Any]]:
        return list((self.data["entities"].get(entity) or {}).get("parts") or [])

    def stale(self, entity_dir: Path, entity: str) -> list[str]:
        """The parts whose size is not the one the index wrote (a part replaced since)."""
        out = []
        for p in self.parts(entity):
            try:
                if (entity_dir / p["path"]).stat().st_size != p["size"]:
                    out.append(p["path"])
            except OSError:
                out.append(p["path"])
        return out

    def supports(self, query: Any) -> bool:
        """Whether the index answers *query*: works or authors by the keys indexed (a
        search by name, by ROR id or of everything reads the whole entity)."""
        if query.entity not in KEYS or query.everything or query.names or query.rors:
            return False
        if query.entity == "authors":
            return bool(query.ids) and not (query.author_ids or query.lineage or query.dois)
        return bool(query.ids or query.author_ids or query.lineage or query.dois)

    def _wanted(self, query: Any) -> dict[str, np.ndarray]:
        out: dict[str, Iterable[int]] = {}
        if query.ids:
            out["id"] = [_id_number(i) for i in query.ids]
        if query.entity == "works":
            if query.author_ids:
                out["author"] = [_id_number(a) for a in query.author_ids]
            if query.lineage:
                out["institution"] = [_id_number(i) for i in query.lineage]
            if query.dois:
                out["doi"] = [doi_key(d.encode()) for d in query.dois]
        return {k: np.unique(np.fromiter(v, dtype=np.uint64)) for k, v in out.items()}

    @staticmethod
    def _load(path: Path, mmap: bool) -> np.ndarray:
        try:
            return np.load(path, mmap_mode="r" if mmap else None, allow_pickle=False)
        except ValueError:  # an empty array cannot be mapped
            return np.load(path, allow_pickle=False)

    def _refs(self, entity: str, key: str, wanted: np.ndarray) -> np.ndarray:
        found: list[np.ndarray] = []
        folder = self.folder / entity / key
        buckets = wanted % BUCKETS
        for bucket in np.unique(buckets):
            values = wanted[buckets == bucket]
            # Many keys: the bucket's keys in memory; a few: searched where they lie.
            keys = self._load(folder / f"{int(bucket):02d}.keys.npy", len(values) <= 2000)
            if not len(keys):
                continue
            refs = self._load(folder / f"{int(bucket):02d}.refs.npy", True)
            lo = np.searchsorted(keys, values, "left")
            hi = np.searchsorted(keys, values, "right")
            for a, b in zip(lo[hi > lo], hi[hi > lo], strict=True):
                found.append(np.asarray(refs[a:b]))
        return np.unique(np.concatenate(found)) if found else np.empty(0, np.uint32)

    def _table(self, entity: str) -> tuple[np.ndarray, np.ndarray]:
        if entity not in self._blocks:
            folder = self.folder / entity
            self._blocks[entity] = (self._load(folder / "blocks.npy", True),
                                    np.load(folder / "first.npy", allow_pickle=False))  # fmt: skip
        return self._blocks[entity]

    def members(self, query: Any) -> dict[str, list[tuple[int, int]]]:
        """``updated_date=…/part_….gz`` → the ``(offset, length)`` of each member of that part
        that may hold a record *query* accepts, in order; parts with none are left out."""
        entity = query.entity
        wanted = self._wanted(query)
        refs = [self._refs(entity, key, values) for key, values in wanted.items() if len(values)]
        if not refs:
            return {}
        every = np.unique(np.concatenate(refs))
        blocks, first = self._table(entity)
        parts = self.parts(entity)
        number = every >> np.uint32(_MEMBER_BITS)
        member = every & np.uint32(_MAX_MEMBERS - 1)
        out: dict[str, list[tuple[int, int]]] = {}
        edges = np.flatnonzero(np.diff(number)) + 1
        for start, end in zip(np.r_[0, edges], np.r_[edges, len(every)], strict=True):
            n = int(number[start])
            rows = blocks[int(first[n]) + member[start:end].astype(np.int64)]
            out[parts[n]["path"]] = [(int(o), int(length)) for o, length in
                                     zip(rows["offset"], rows["length"], strict=True)]  # fmt: skip
        return out

    def estimate(self, entity: str, key: str, keys: int, *, quantile: str = "p90") -> int:
        """About how many members *keys* keys of *key* take (each at *quantile* of the
        members a key takes), at most every member of the entity."""
        e = self.data["entities"].get(entity) or {}
        per = ((e.get("keys") or {}).get(key) or {}).get("members_per_key") or {}
        return int(min(e.get("members") or 0, keys * float(per.get(quantile) or 1.0)))


def read_spans(path: str, members: Sequence[tuple[int, int]]) -> Iterable[bytes]:
    """The lines of the members of *path* at *members* (``(offset, length)``, in order),
    each member's lines as one block; members close together are read in one go."""
    with open(path, "rb") as fh:
        i = 0
        while i < len(members):
            start = members[i][0]
            j = i
            end = members[i][0] + members[i][1]
            while j + 1 < len(members) and members[j + 1][0] - end <= _GAP:
                j += 1
                end = members[j][0] + members[j][1]
            fh.seek(start)
            data = fh.read(end - start)
            for offset, length in members[i : j + 1]:
                yield zlib.decompress(data[offset - start : offset - start + length], 31)
            i = j + 1
