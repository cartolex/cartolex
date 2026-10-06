# SPDX-License-Identifier: MIT
"""Distances to take away: the nearest of each, the whole similarity matrix, the vectors.

Each is a file written by a job into ``outputs/exports/`` under a dated name, for the
people on the map (every one, or those of a list: the people the map's filters keep) or
for the organisations of one level:

- ``neighbours``: the *k* nearest of each (``source``, ``target``, ``rank``,
  ``similarity``), CSV up to a million rows, else Parquet;
- ``similarity``: the cosine of every pair, written by blocks of rows, never whole in
  memory: CSV (a square table with the ids as header) up to four million cells, else
  ``.npz`` (``similarity``: float32, ``ids``, ``names`` when named). Above ten million
  cells it must be confirmed, its size said first;
- ``vectors``: each one's vector (the people's as the space stage stored them; an
  organisation's, the mean of its members' of length one), CSV up to five million
  values, else Parquet.

People are named, or given pseudonyms (``s1``, ``s2``… in a shuffled order) without their
names, as the person exporting answered; organisations are always named.
"""

from __future__ import annotations

import contextlib
import csv
import math
import random
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["CONFIRM_CELLS", "ExportPlan", "plan_export", "write_export"]

#: Above this many cells a full matrix is written only once confirmed.
CONFIRM_CELLS = 10_000_000
#: The largest matrix written as CSV (cells), edge list (rows) and vectors (values).
CSV_CELLS = 4_000_000
CSV_ROWS = 1_000_000
CSV_VALUES = 5_000_000
#: The bytes of similarities computed at a time (a block of rows against every column).
BLOCK_BYTES = 64 * 1024 * 1024
#: Bytes per cell of a CSV matrix (a similarity with four decimals and its comma), and of a
#: CSV edge (two ids, a rank and a similarity), for the estimates.
CSV_CELL_BYTES = 7
CSV_EDGE_BYTES = 28
STEMS = {"neighbours": "neighbours", "similarity": "similarity", "vectors": "vectors"}


@dataclass
class ExportPlan:
    """What an export will write: how many items, cells (or rows, or values), in which
    format, its size, and whether it must be confirmed first."""

    kind: str
    of: str
    count: int
    cells: int
    format: str
    bytes: int
    confirm: bool

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "of": self.of, "count": self.count, "cells": self.cells,
                "format": self.format, "bytes": self.bytes, "confirm": self.confirm}  # fmt: skip


@dataclass
class _Items:
    ids: list[str]
    names: list[str] | None
    vectors: np.ndarray  # (n, d) float32, of length one
    rows: np.ndarray | None  # the people's rows in the space (None: organisations)


def _items(view: Any, of: str, ids: list[str] | None, level: str | None, names: bool) -> _Items:
    from .space_index import rows_of

    if of == "organisation":
        level = view.level_of(level)
        org_ids, vectors = view.org_vectors(level)
        if ids is not None:
            keep = [i for i, o in enumerate(org_ids) if o in set(ids)]
            org_ids, vectors = [org_ids[i] for i in keep], vectors[keep]
        return _Items(org_ids, [view.orgs[o].get("name") or o for o in org_ids], vectors, None)
    rows = rows_of(view, ids)
    if not names:
        rows = rows.copy()
        random.SystemRandom().shuffle(rows)  # an order that says nothing
        labels = [f"s{k + 1}" for k in range(len(rows))]
        who = None
    else:
        labels = [view.person[r] for r in rows.tolist()]
        who = [view.name[r] for r in rows.tolist()]
    vectors = np.asarray(view.space.vectors[rows], dtype=np.float32)
    return _Items(labels, who, vectors.reshape(len(rows), -1), rows)


def _count(view: Any, of: str, ids: list[str] | None, level: str | None) -> tuple[int, int]:
    from .space_index import rows_of

    d = int(view.space.vectors.shape[1])
    if of == "organisation":
        level = view.level_of(level)
        org_ids, _ = view.org_vectors(level)
        return (len(org_ids) if ids is None else len(set(org_ids) & set(ids))), d
    return len(rows_of(view, ids)), d


def plan_export(
    view: Any,
    kind: str,
    of: str,
    *,
    ids: list[str] | None = None,
    level: str | None = None,
    k: int = 10,
) -> ExportPlan:
    """The plan of an export: what it holds, its format and size (estimated)."""
    n, d = _count(view, of, ids, level)
    if kind == "neighbours":
        cells = n * min(k, max(0, n - 1))
        fmt = "csv" if cells <= CSV_ROWS else "parquet"
        size = cells * (CSV_EDGE_BYTES if fmt == "csv" else 12)
    elif kind == "similarity":
        cells = n * n
        fmt = "csv" if cells <= CSV_CELLS else "npz"
        size = cells * (CSV_CELL_BYTES if fmt == "csv" else 4)
    else:
        cells = n * d
        fmt = "csv" if cells <= CSV_VALUES else "parquet"
        size = cells * (10 if fmt == "csv" else 4)
    return ExportPlan(kind, of, n, cells, fmt, int(size),
                      kind == "similarity" and cells > CONFIRM_CELLS)  # fmt: skip


def _blocks(n: int) -> int:
    """Rows per block, so that a block of similarities stays near ``BLOCK_BYTES``."""
    return max(16, min(4096, BLOCK_BYTES // max(1, 4 * n)))


def write_export(
    project: Any,
    view: Any,
    kind: str,
    of: str,
    *,
    names: bool,
    ids: list[str] | None = None,
    level: str | None = None,
    k: int = 10,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path | None:
    """Write the export into ``outputs/exports/``; answers its path, or ``None`` when
    *cancelled()* stopped it (nothing is left behind then)."""
    from cartolex.site.exports import _dated

    say = progress or (lambda fraction: None)
    stop = cancelled or (lambda: False)
    plan = plan_export(view, kind, of, ids=ids, level=level, k=k)
    items = _items(view, of, ids, level, names or of == "organisation")
    who = "people" if of == "person" else "organisations"
    target = _dated(project, f"{STEMS[kind]}-{who}", f".{plan.format}")
    part = target.with_name(f".{target.name}.part")
    try:
        if kind == "neighbours":
            done = _neighbours(items, part, plan.format, k, say, stop)
        elif kind == "similarity":
            done = _similarity(items, part, plan.format, say, stop)
        else:
            done = _vectors(project, items, part, plan.format, say, stop)
        if not done:
            return None
        part.replace(target)
        return target
    finally:
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)


def _head(items: _Items) -> list[str]:
    return ["id", "name"] if items.names is not None else ["id"]


def _who(items: _Items, i: int) -> list[str]:
    return [items.ids[i], items.names[i]] if items.names is not None else [items.ids[i]]


def _neighbours(items: _Items, path: Path, fmt: str, k: int, say: Any, stop: Any) -> bool:
    import pyarrow as pa
    import pyarrow.parquet as pq

    z = items.vectors
    n = len(items.ids)
    k = min(k, max(0, n - 1))
    step = _blocks(n)
    named = items.names is not None
    writer: Any = None
    fh: Any = None
    out: Any = None
    if fmt == "csv":
        fh = open(path, "w", encoding="utf-8", newline="")  # noqa: SIM115 - closed below
        out = csv.writer(fh)
        head = ["source", "target", "rank", "similarity"]
        out.writerow([*head[:2], "source_name", "target_name", *head[2:]] if named else head)
    try:
        for start in range(0, n, step):
            if stop():
                return False
            sim = z[start : start + step] @ z.T
            rows = np.arange(sim.shape[0])
            sim[rows, start + rows] = -np.inf
            best = (
                np.argpartition(-sim, k - 1, axis=1)[:, :k] if k else np.zeros((len(rows), 0), int)
            )
            near = np.take_along_axis(sim, best, axis=1)
            order = np.argsort(-near, axis=1, kind="stable")
            best = np.take_along_axis(best, order, axis=1)
            near = np.take_along_axis(near, order, axis=1)
            src = np.repeat(np.arange(start, start + len(rows)), k)
            dst = best.reshape(-1)
            sims = np.round(near.reshape(-1).astype(np.float64), 4)
            ranks = np.tile(np.arange(1, k + 1), len(rows))
            if fmt == "csv":
                for s, t, r, v in zip(src.tolist(), dst.tolist(), ranks.tolist(), sims.tolist(),
                                      strict=True):  # fmt: skip
                    extra = [items.names[s], items.names[t]] if named else []
                    out.writerow([items.ids[s], items.ids[t], *extra, r, v])
            else:
                cols = {
                    "source": pa.array([items.ids[s] for s in src.tolist()], pa.string()),
                    "target": pa.array([items.ids[t] for t in dst.tolist()], pa.string()),
                }
                if named:
                    cols["source_name"] = pa.array([items.names[s] for s in src.tolist()])
                    cols["target_name"] = pa.array([items.names[t] for t in dst.tolist()])
                cols["rank"] = pa.array(ranks.astype(np.int16))
                cols["similarity"] = pa.array(near.reshape(-1).astype(np.float32))
                table = pa.table(cols)
                if writer is None:
                    writer = pq.ParquetWriter(path, table.schema)
                writer.write_table(table)
            say(min(1.0, (start + len(rows)) / max(1, n)))
        if fmt != "csv" and writer is None:
            pq.write_table(pa.table({"source": pa.array([], pa.string()),
                                     "target": pa.array([], pa.string())}), path)  # fmt: skip
        return True
    finally:
        if fh is not None:
            fh.close()
        if writer is not None:
            writer.close()


def _similarity(items: _Items, path: Path, fmt: str, say: Any, stop: Any) -> bool:
    z = items.vectors
    n = len(items.ids)
    step = _blocks(n)
    if fmt == "csv":
        with open(path, "w", encoding="utf-8", newline="") as fh:
            out = csv.writer(fh)
            out.writerow([*_head(items), *items.ids])
            for start in range(0, n, step):
                if stop():
                    return False
                sim = np.clip(z[start : start + step] @ z.T, -1.0, 1.0)
                for r, row in enumerate(np.round(sim.astype(np.float64), 4).tolist()):
                    out.writerow([*_who(items, start + r), *row])
                say(min(1.0, (start + sim.shape[0]) / max(1, n)))
        return True
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        width = max((len(i) for i in items.ids), default=1)
        with zf.open("ids.npy", "w") as fh:
            np.lib.format.write_array(fh, np.asarray(items.ids, dtype=f"<U{width}"))
        if items.names is not None:
            width = max((len(i) for i in items.names), default=1)
            with zf.open("names.npy", "w") as fh:
                np.lib.format.write_array(fh, np.asarray(items.names, dtype=f"<U{width}"))
        with zf.open("similarity.npy", "w", force_zip64=True) as fh:
            np.lib.format.write_array_header_1_0(
                fh, {"descr": "<f4", "fortran_order": False, "shape": (n, n)}
            )
            for start in range(0, n, step):
                if stop():
                    return False
                sim = np.clip(z[start : start + step] @ z.T, -1.0, 1.0)
                fh.write(np.ascontiguousarray(sim, dtype="<f4").tobytes())
                say(min(1.0, (start + sim.shape[0]) / max(1, n)))
    return True


def _vectors(project: Any, items: _Items, path: Path, fmt: str, say: Any, stop: Any) -> bool:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if items.rows is not None:  # people: the vectors as the space stage stored them
        from cartolex.atlas.model_files import load_embeddings

        layout = project.layout
        raw = load_embeddings(layout.stage("themes.space") / "models" / "embeddings.json").Z_ind
        z = np.asarray(raw, dtype=np.float64)[items.rows]
    else:
        z = items.vectors.astype(np.float64)
    if stop():
        return False
    d = z.shape[1] if z.ndim == 2 else 0
    digits = max(1, len(str(d)))
    names = [f"v{j + 1:0{digits}d}" for j in range(d)]
    if fmt == "csv":
        with open(path, "w", encoding="utf-8", newline="") as fh:
            out = csv.writer(fh)
            out.writerow([*_head(items), *names])
            for i, row in enumerate(z.tolist()):
                out.writerow([*_who(items, i), *(f"{v:.6g}" for v in row)])
    else:
        cols: dict[str, Any] = {"id": pa.array(items.ids, pa.string())}
        if items.names is not None:
            cols["name"] = pa.array(items.names, pa.string())
        for j, name in enumerate(names):
            cols[name] = pa.array(z[:, j].astype(np.float32))
        pq.write_table(pa.table(cols), path)
    say(1.0)
    return True


def human_size(n: int) -> str:
    """A size in bytes, in words (``115 GB``)."""
    if n < 1000:
        return f"{n} B"
    unit = min(4, int(math.log(n, 1000)))
    return f"{n / 1000**unit:.3g} {('B', 'kB', 'MB', 'GB', 'TB')[unit]}"
