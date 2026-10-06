# SPDX-License-Identifier: MIT
"""The tables of a project: the Parquet data model of ``sources/tables/`` and the CSV decisions.

:data:`SOURCE_SCHEMAS` fixes each source table's columns and types, and
:data:`SOURCE_KEYS` its key (see ``docs/format/sources.md``). A table is
written sorted by its key, atomically, and checked when read: a missing column,
a wrong type, a duplicate key or an unsorted table is refused with the reason.
Extra columns are allowed (a newer minor version may add optional ones), and an
optional column missing from an older file reads as empty.

:data:`DECISION_TABLES` lists the columns and allowed values of each CSV
decision file (see ``docs/format/decisions.md``).
"""

from __future__ import annotations

import contextlib
import csv
import io
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ..lexicon.categories import CATEGORIES
from .files import atomic_write_bytes, replace_path

__all__ = [
    "DECISION_TABLES",
    "PRIVATE_PARTS",
    "ROW_GROUP",
    "SOURCE_KEYS",
    "SOURCE_SCHEMAS",
    "DecisionTable",
    "SourceTableWriter",
    "TableError",
    "check_source_file",
    "empty_table",
    "find_ids",
    "id_keys",
    "read_decision_csv",
    "read_source_table",
    "rows_to_table",
    "shareable_parts",
    "source_key",
    "write_decision_csv",
    "write_source_rows",
    "write_source_table",
]

_UTC = pa.timestamp("us", tz="UTC")
_IDS = pa.map_(pa.string(), pa.string())

SOURCE_SCHEMAS: dict[str, pa.Schema] = {
    "texts": pa.schema(
        [
            ("text_id", pa.string()),
            ("slot", pa.string()),
            ("position", pa.int64()),
            ("year", pa.int32()),
            ("date", pa.string()),
            ("doc_type", pa.string()),
            ("title", pa.string()),
            ("doi", pa.string()),
            ("ids", _IDS),
            ("version_of", pa.string()),
            ("n_authors", pa.int32()),
            ("source", pa.string()),
            ("retrieved_at", _UTC),
        ]
    ),
    "text_parts": pa.schema(
        [
            ("text_id", pa.string()),
            ("part", pa.string()),
            ("language", pa.string()),
            ("provider", pa.string()),
            ("format", pa.string()),
            ("content", pa.large_string()),
            ("retrieved_at", _UTC),
        ]
    ),
    "people": pa.schema(
        [
            ("person_id", pa.string()),
            ("last_name", pa.string()),
            ("first_name", pa.string()),
            ("orcid", pa.string()),
            ("ids", pa.map_(pa.string(), pa.list_(pa.string()))),
            ("source", pa.string()),
            ("columns", _IDS),
            (
                "aliases",
                pa.list_(
                    pa.struct(
                        [
                            ("last_name", pa.string()),
                            ("first_name", pa.string()),
                            ("source", pa.string()),
                        ]
                    )
                ),
            ),
            ("retrieved_at", _UTC),
        ]
    ),
    "organisations": pa.schema(
        [
            ("org_id", pa.string()),
            ("name", pa.string()),
            ("acronym", pa.string()),
            ("level", pa.string()),
            ("parents", pa.list_(pa.string())),
            ("ids", _IDS),
            ("country", pa.string()),
            ("location", pa.struct([("lat", pa.float64()), ("lon", pa.float64())])),
            ("source", pa.string()),
            ("retrieved_at", _UTC),
        ]
    ),
    "affiliations": pa.schema(
        [
            ("person_id", pa.string()),
            ("org_id", pa.string()),
            ("start_year", pa.int32()),
            ("end_year", pa.int32()),
            ("source", pa.string()),
        ]
    ),
    "authorships": pa.schema(
        [
            ("text_id", pa.string()),
            ("person_id", pa.string()),
            ("position", pa.int32()),
            ("orgs", pa.list_(pa.string())),
            ("last", pa.bool_()),
            ("corresponding", pa.bool_()),
        ]
    ),
}

#: The key of each source table: unique, and the order rows are stored in.
SOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "texts": ("text_id",),
    "text_parts": ("text_id", "part", "language", "provider"),
    "people": ("person_id",),
    "organisations": ("org_id",),
    "affiliations": ("person_id", "org_id", "start_year", "source"),
    "authorships": ("text_id", "person_id"),
}

#: Columns that must never be null.
_REQUIRED: dict[str, tuple[str, ...]] = {
    "texts": ("text_id", "slot", "position", "doc_type", "title", "source"),
    "text_parts": ("text_id", "part", "language", "provider", "format", "content"),
    "people": ("person_id", "last_name", "source"),
    "organisations": ("org_id", "name", "source"),
    "affiliations": ("person_id", "org_id", "source"),
    "authorships": ("text_id", "person_id", "position"),
}

_ALLOWED: dict[tuple[str, str], frozenset[str]] = {
    ("text_parts", "part"): frozenset({"title", "abstract", "body", "full"}),
    ("text_parts", "format"): frozenset({"plain", "jats", "latex"}),
}


#: Text parts that never leave the project: a ``body`` or a ``full`` part is a full text
#: (collected on request, or a user's own document). A shared output — a site, a bundle, an
#: export, a share — carries titles and abstracts at most (see ``docs/format/sources.md``).
PRIVATE_PARTS: frozenset[str] = frozenset({"body", "full"})


def shareable_parts(table: pa.Table) -> pa.Table:
    """The rows of a ``text_parts`` table that a shared output may carry (no private part)."""
    if table.num_rows == 0:
        return table
    keep = pc.invert(pc.is_in(table["part"], value_set=pa.array(sorted(PRIVATE_PARTS))))
    return table.filter(keep)


class TableError(ValueError):
    """A table breaks its schema; the message names the file and the rule."""


def empty_table(name: str) -> pa.Table:
    """An empty table with the schema of source table *name*."""
    return SOURCE_SCHEMAS[name].empty_table()


def _conform(name: str, table: pa.Table, where: str, *, partial: bool = False) -> pa.Table:
    """*table*'s columns cast to the schema's types; an optional column missing reads as
    empty. With *partial* (some of the columns read), the columns absent are left out."""
    for fld in SOURCE_SCHEMAS[name]:
        if fld.name not in table.column_names:
            if partial:
                continue
            if fld.name in _REQUIRED[name]:
                raise TableError(f"{where}: column {fld.name!r} is missing")
            # An optional column a newer minor version added: an older file reads it as empty.
            table = table.append_column(fld, pa.nulls(table.num_rows, type=fld.type))
        got = table.schema.field(fld.name).type
        if got != fld.type:
            try:
                table = table.set_column(
                    table.column_names.index(fld.name), fld.name, table[fld.name].cast(fld.type)
                )
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
                raise TableError(
                    f"{where}: column {fld.name!r} is {got}, expected {fld.type}"
                ) from exc
    return table


def _check(name: str, table: pa.Table, where: str, *, partial: bool = False) -> pa.Table:
    table = _conform(name, table, where, partial=partial)
    present = set(table.column_names)
    for col in _REQUIRED[name]:
        if col in present and table[col].null_count:
            raise TableError(f"{where}: column {col!r} has {table[col].null_count} empty value(s)")
    for (tname, col), allowed in _ALLOWED.items():
        if tname == name and col in present:
            bad = set(pc.unique(table[col]).to_pylist()) - allowed
            if bad:
                raise TableError(f"{where}: column {col!r} has unknown value(s) {sorted(bad)[:5]}")
    keys = SOURCE_KEYS[name]
    n = table.num_rows
    if n > 1 and present.issuperset(keys):
        order = pc.sort_indices(
            table.select(list(keys)),
            sort_keys=[(k, "ascending") for k in keys],  # nulls last: the default
        )
        if not pc.all(pc.equal(order, pa.array(range(n), type=order.type))).as_py():
            raise TableError(f"{where}: rows are not sorted by {', '.join(keys)}")
        groups = table.select(list(keys)).group_by(list(keys)).aggregate([([], "count_all")])
        if groups.num_rows != n:
            dup = groups.filter(pc.greater(groups["count_all"], 1)).slice(0, 1).to_pylist()[0]
            dup.pop("count_all", None)
            raise TableError(f"{where}: key {dup} repeats")
    return table


#: Rows per row group of a source table's Parquet file.
ROW_GROUP = 64_000


def write_source_table(path: Path, name: str, table: pa.Table) -> None:
    """Sort *table* by its key, check it and write it atomically as Parquet."""
    keys = SOURCE_KEYS[name]
    ordered = table.sort_by([(k, "ascending") for k in keys]) if table.num_rows else table
    ordered = _check(name, ordered, str(path))
    columns = [f.name for f in SOURCE_SCHEMAS[name]] + [
        c for c in ordered.column_names if c not in SOURCE_SCHEMAS[name].names
    ]
    ordered = ordered.select(columns)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        pq.write_table(ordered, tmp, compression="zstd", row_group_size=ROW_GROUP)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        replace_path(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _column(fld: pa.Field, rows: Sequence[Mapping[str, object]]) -> pa.Array:
    values = [r.get(fld.name) for r in rows]
    if pa.types.is_map(fld.type):
        values = [
            sorted((str(k), v) for k, v in x.items()) if isinstance(x, Mapping) else x
            for x in values
        ]
    elif pa.types.is_list(fld.type):
        values = [list(x) if x is not None and not isinstance(x, list) else x for x in values]
    return pa.array(values, type=fld.type)


def rows_to_table(name: str, rows: Sequence[Mapping[str, object]]) -> pa.Table:
    """Rows of source table *name* (dicts; a map column may be given as a dict) as an Arrow
    table with its schema."""
    schema = SOURCE_SCHEMAS[name]
    return pa.table({fld.name: _column(fld, rows) for fld in schema})


def source_key(name: str, row: Mapping[str, object]) -> tuple:
    """A row's key as source table *name* is sorted: strings by code point, numbers by value,
    an empty value last (Arrow's order)."""
    return tuple(
        (row.get(k) is None, row.get(k) if row.get(k) is not None else 0) for k in SOURCE_KEYS[name]
    )


class SourceTableWriter:
    """Writes source table *name* a row group at a time, from rows already in key order.

    Each row group is checked as :func:`write_source_table` checks a whole table, and the
    key's order and uniqueness across row groups too: memory holds one row group, whatever
    the table's size. The file appears whole, atomically, at :meth:`close`; leaving a
    ``with`` block on an exception leaves the previous file in place.
    """

    def __init__(self, path: Path, name: str) -> None:
        self.path, self.name = Path(path), name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        os.close(fd)
        self._tmp = tmp
        self._writer = pq.ParquetWriter(tmp, SOURCE_SCHEMAS[name], compression="zstd")
        self._pending: list[Mapping[str, object]] = []
        self._last: tuple | None = None
        self.rows = 0

    def add(self, row: Mapping[str, object]) -> None:
        """Append one row (a dict with the table's columns)."""
        self._pending.append(row)
        if len(self._pending) >= ROW_GROUP:
            self._flush()

    def extend(self, rows: Iterable[Mapping[str, object]]) -> None:
        """Append rows in key order."""
        for row in rows:
            self.add(row)

    def _flush(self) -> None:
        if not self._pending:
            return
        rows, self._pending = self._pending, []
        first = source_key(self.name, rows[0])
        if self._last is not None and not self._last < first:
            keys = ", ".join(SOURCE_KEYS[self.name])
            if first == self._last:
                raise TableError(f"{self.path}: key {rows[0]!r} repeats")
            raise TableError(f"{self.path}: rows are not sorted by {keys}")
        table = _check(self.name, rows_to_table(self.name, rows), str(self.path))
        self._last = source_key(self.name, rows[-1])
        self._writer.write_table(table, row_group_size=ROW_GROUP)
        self.rows += len(rows)

    def close(self) -> int:
        """Write the last row group and put the file in place; returns the rows written."""
        try:
            self._flush()
            self._writer.close()
            with open(self._tmp, "rb") as fh:
                os.fsync(fh.fileno())
            replace_path(self._tmp, self.path)
        except BaseException:
            self.discard()
            raise
        return self.rows

    def discard(self) -> None:
        """Forget the rows written: the previous file stays."""
        with contextlib.suppress(Exception):
            self._writer.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self._tmp)

    def __enter__(self) -> SourceTableWriter:
        return self

    def __exit__(self, exc_type: object, *rest: object) -> None:
        if exc_type is None:
            self.close()
        else:
            self.discard()


def write_source_rows(path: Path, name: str, rows: Iterable[Mapping[str, object]]) -> int:
    """Write source table *name* from *rows* already in key order (see
    :class:`SourceTableWriter`); returns the number of rows."""
    with SourceTableWriter(path, name) as writer:
        writer.extend(rows)
    return writer.rows


#: The table files already checked, as they were (path, size, modification time).
_CHECKED: set[tuple[str, int, int]] = set()


def _null_count(pf: pq.ParquetFile, column: str) -> int | None:
    """The empty values of a flat *column*, from the file's footer (``None``: not recorded)."""
    meta = pf.metadata
    leaf = next((j for j in range(meta.num_columns) if meta.schema.column(j).path == column), None)
    if leaf is None:
        return None
    total = 0
    for i in range(meta.num_row_groups):
        stats = meta.row_group(i).column(leaf).statistics
        if stats is None or not stats.has_null_count:
            return None
        total += stats.null_count
    return total


def check_source_file(path: Path, name: str) -> None:
    """Check source table *name*'s file as :func:`write_source_table` checks a table, once
    per version of the file: its columns and types, its required values (counted in the
    file's footer), its allowed values, and its key's order and uniqueness (reading only
    those columns)."""
    path = Path(path)
    stat = path.stat()
    stamp = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if stamp in _CHECKED:
        return
    where = str(path)
    pf = pq.ParquetFile(path)
    try:
        schema = pf.schema_arrow
        for col in _REQUIRED[name]:
            if col not in schema.names:
                raise TableError(f"{where}: column {col!r} is missing")
        _conform(name, schema.empty_table(), where, partial=True)
        light = set(SOURCE_KEYS[name]) | {c for (t, c) in _ALLOWED if t == name}
        for col in _REQUIRED[name]:
            if col in light:
                continue
            nulls = _null_count(pf, col)
            if nulls is None:
                light.add(col)  # not recorded: read and counted
            elif nulls:
                raise TableError(f"{where}: column {col!r} has {nulls} empty value(s)")
        _check(name, pf.read(columns=sorted(light & set(schema.names))), where, partial=True)
    finally:
        pf.close()
    _CHECKED.add(stamp)


def read_source_table(
    path: Path,
    name: str,
    columns: list[str] | None = None,
    *,
    filters: list | None = None,
) -> pa.Table:
    """Read source table *name* (all columns unless *columns* is given; with *filters*,
    pyarrow's, only the rows that match), its file checked first
    (:func:`check_source_file`). Only the columns asked are read."""
    check_source_file(path, name)
    if columns is None:
        return _conform(name, pq.read_table(path, filters=filters), str(path))
    present = set(pq.read_schema(path).names)
    table = pq.read_table(path, columns=[c for c in columns if c in present], filters=filters)
    table = _conform(name, table, str(path), partial=True)
    for col in columns:  # an optional column an older file lacks reads as empty
        if col not in table.column_names:
            fld = SOURCE_SCHEMAS[name].field(col)
            table = table.append_column(fld, pa.nulls(table.num_rows, type=fld.type))
    return table.select(columns)


# ── ids as columns ───────────────────────────────────────────────────────────

#: Ids turned into bytes at a time.
_ID_BATCH = 65_536


def id_keys(column: pa.Array | pa.ChunkedArray) -> np.ndarray:
    """The ids of *column* (a table's key, in its order) as UTF-8 bytes in one NumPy
    array, which :func:`find_ids` searches when the ids are sorted: a few bytes per id,
    never a Python string for each."""
    n = len(column)
    width = (pc.max(pc.binary_length(column)).as_py() or 1) if n else 1
    out = np.empty(n, dtype=f"S{max(width, 1)}")
    start = 0
    for chunk in column.chunks if isinstance(column, pa.ChunkedArray) else [column]:
        for offset in range(0, len(chunk), _ID_BATCH):
            ids = chunk.slice(offset, _ID_BATCH).to_pylist()
            out[start : start + len(ids)] = [i.encode("utf-8") for i in ids]
            start += len(ids)
    return out


def find_ids(keys: np.ndarray, ids: Sequence[str] | pa.Array | pa.ChunkedArray) -> np.ndarray:
    """Where each of *ids* is in the sorted *keys* (:func:`id_keys`); ``-1``: not there."""
    if isinstance(ids, pa.Array | pa.ChunkedArray):
        ids = ids.to_pylist()
    encoded = [i.encode("utf-8") for i in ids]
    if not len(keys) or not encoded:
        return np.full(len(encoded), -1, dtype=np.int64)
    wanted = np.array(encoded, dtype=keys.dtype)
    found = np.minimum(np.searchsorted(keys, wanted), len(keys) - 1)
    hit = keys[found] == wanted
    width = keys.dtype.itemsize
    if any(len(e) > width for e in encoded):  # longer than every key: cut, it could match
        hit &= np.array([len(e) <= width for e in encoded])
    return np.where(hit, found, -1)


# ── CSV decisions ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionTable:
    """The columns of one CSV decision file, its key and the values some columns allow.

    *optional* columns were added within the format's version: a file without them is
    read with them empty, and they are written only when a row fills one.
    """

    columns: tuple[str, ...]
    key: tuple[str, ...]
    allowed: dict[str, frozenset[str]] = field(default_factory=dict)
    optional: tuple[str, ...] = ()


DECISION_TABLES: dict[str, DecisionTable] = {
    "people": DecisionTable(
        columns=(
            "person_id",
            "role",
            "set",
            "identity",
            "records",
            "merged_into",
            "note",
            "decided_at",
        ),
        key=("person_id",),
        allowed={
            "role": frozenset({"mapped", "context", "projected", "excluded", "undecided"}),
            "identity": frozenset({"confirmed", "auto", "none", "pending"}),
        },
    ),
    "organisations": DecisionTable(
        columns=("org_id", "level", "parents", "name", "merged_into", "note", "decided_at"),
        key=("org_id",),
    ),
    "affiliations": DecisionTable(
        columns=("person_id", "org_id", "start_year", "end_year", "action", "note", "decided_at"),
        key=("person_id", "org_id", "start_year", "action"),
        allowed={"action": frozenset({"add", "remove"})},
    ),
    "keywords": DecisionTable(
        columns=("term", "language", "decision", "target", "reason", "source", "decided_at"),
        key=("term", "language"),
        allowed={
            "decision": frozenset({"keep", "exclude", "merge"}),
            "source": frozenset({"person", "ai-handoff", "ai-copilot", "ai-api"}),
            "category": frozenset(CATEGORIES),
        },
        optional=("category",),
    ),
    "snowball": DecisionTable(
        columns=(
            "round",
            "person_id",
            "seeds",
            "path",
            "joint_texts",
            "last_joint_year",
            "fit",
            "decision",
            "decided_at",
        ),
        key=("round", "person_id"),
        allowed={"decision": frozenset({"mapped", "context", "projected", "no", "later", ""})},
    ),
}


def read_decision_csv(path: Path, name: str) -> list[dict[str, str]]:
    """Read and check a CSV decision file; a missing file is an empty table."""
    spec = DECISION_TABLES[name]
    path = Path(path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = tuple(reader.fieldnames or ())
        missing = [c for c in spec.columns if c not in header]
        if missing:
            raise TableError(f"{path}: missing column(s) {missing}")
        rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
    for row in rows:
        for col in spec.optional:
            row.setdefault(col, "")
    seen: set[tuple[str, ...]] = set()
    for i, row in enumerate(rows, start=2):
        for col, allowed in spec.allowed.items():
            if row[col] not in allowed and not (row[col] == "" and col != "decision"):
                raise TableError(
                    f"{path}, line {i}: {col} {row[col]!r} is not one of {sorted(allowed)}"
                )
        key = tuple(row[k] for k in spec.key)
        if key in seen:
            raise TableError(
                f"{path}, line {i}: key {dict(zip(spec.key, key, strict=True))} repeats"
            )
        seen.add(key)
    return rows


def decision_csv_bytes(name: str, rows: list[dict[str, str]]) -> bytes:
    """The canonical bytes of a CSV decision file: its columns first (an optional one only
    when a row fills it), rows sorted by key."""
    spec = DECISION_TABLES[name]
    optional = [c for c in spec.optional if any(r.get(c) for r in rows)]
    extra = sorted({k for r in rows for k in r} - set(spec.columns) - set(spec.optional))
    columns = list(spec.columns) + optional + extra
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in sorted(rows, key=lambda r: tuple(str(r.get(k, "")) for k in spec.key)):
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue().encode("utf-8")


def write_decision_csv(path: Path, name: str, rows: list[dict[str, str]]) -> None:
    """Write a CSV decision file atomically (unguarded; see :func:`files.write_decision`)."""
    atomic_write_bytes(Path(path), decision_csv_bytes(name, rows))
