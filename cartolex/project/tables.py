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
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .files import atomic_write_bytes, replace_path

__all__ = [
    "DECISION_TABLES",
    "PRIVATE_PARTS",
    "SOURCE_KEYS",
    "SOURCE_SCHEMAS",
    "DecisionTable",
    "TableError",
    "empty_table",
    "read_decision_csv",
    "read_source_table",
    "shareable_parts",
    "write_decision_csv",
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


def _check(name: str, table: pa.Table, where: str) -> pa.Table:
    schema = SOURCE_SCHEMAS[name]
    for fld in schema:
        if fld.name not in table.column_names:
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
    for col in _REQUIRED[name]:
        if table[col].null_count:
            raise TableError(f"{where}: column {col!r} has {table[col].null_count} empty value(s)")
    for (tname, col), allowed in _ALLOWED.items():
        if tname == name:
            bad = set(pc.unique(table[col]).to_pylist()) - allowed
            if bad:
                raise TableError(f"{where}: column {col!r} has unknown value(s) {sorted(bad)[:5]}")
    keys = SOURCE_KEYS[name]
    n = table.num_rows
    if n > 1:
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
        pq.write_table(ordered, tmp, compression="zstd", row_group_size=64_000)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        replace_path(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def read_source_table(path: Path, name: str, columns: list[str] | None = None) -> pa.Table:
    """Read and check source table *name* (all columns unless *columns* is given)."""
    table = pq.read_table(path)
    table = _check(name, table, str(path))
    return table.select(columns) if columns else table


# ── CSV decisions ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionTable:
    """The columns of one CSV decision file, its key and the values some columns allow."""

    columns: tuple[str, ...]
    key: tuple[str, ...]
    allowed: dict[str, frozenset[str]] = field(default_factory=dict)


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
        },
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
    """The canonical bytes of a CSV decision file: its columns first, rows sorted by key."""
    spec = DECISION_TABLES[name]
    extra = sorted({k for r in rows for k in r} - set(spec.columns))
    columns = list(spec.columns) + extra
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in sorted(rows, key=lambda r: tuple(str(r.get(k, "")) for k in spec.key)):
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue().encode("utf-8")


def write_decision_csv(path: Path, name: str, rows: list[dict[str, str]]) -> None:
    """Write a CSV decision file atomically (unguarded; see :func:`files.write_decision`)."""
    atomic_write_bytes(Path(path), decision_csv_bytes(name, rows))
