# SPDX-License-Identifier: MIT
"""The scratch database of a rebuild: rows kept on disk while the source tables are rebuilt.

Rebuilding the tables reads every record of every run: millions for a large project.
Instead of holding them, the :class:`~cartolex.collect.tables.SourceBuilder` keeps
what grows with the records (each record of a text, the texts' parts, authorships and
affiliations, the id registry's keys) in an SQLite database in a scratch folder; memory
holds the people, the organisations and SQLite's page cache, whatever the project's size.
The database lives only for the rebuild: it is created empty, written without a journal
(a rebuild that stops starts over from the raw runs) and removed at the end.

SQLite comes with Python: no server, no dependency. Its rules give the builder's: a
part or an authorship stated twice keeps the first statement (``INSERT OR IGNORE``),
an affiliation's span widens (``ON CONFLICT … DO UPDATE``).
"""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

__all__ = ["CACHE_MB", "WorkStore", "connect"]

#: SQLite's page cache, in megabytes: what the database may hold in memory.
CACHE_MB = 256

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reg_keys (
    tbl TEXT NOT NULL, key TEXT NOT NULL, slot TEXT NOT NULL, id TEXT NOT NULL,
    PRIMARY KEY (tbl, key, slot)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS records (
    seq INTEGER PRIMARY KEY, tid TEXT NOT NULL, n_authors INTEGER, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_tid ON records (tid, seq);
CREATE TABLE IF NOT EXISTS parts (
    tid TEXT NOT NULL, part TEXT NOT NULL, language TEXT NOT NULL, provider TEXT NOT NULL,
    format TEXT NOT NULL, content BLOB NOT NULL, retrieved_at TEXT,
    PRIMARY KEY (tid, part, language, provider)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS authorships (
    tid TEXT NOT NULL, pid TEXT NOT NULL, position INTEGER NOT NULL, orgs TEXT NOT NULL,
    last INTEGER, corresponding INTEGER,
    PRIMARY KEY (tid, pid)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS affiliations (
    pid TEXT NOT NULL, oid TEXT NOT NULL, source TEXT NOT NULL, start_year INTEGER,
    end_year INTEGER, PRIMARY KEY (pid, oid, source)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS old_affiliations (
    pid TEXT NOT NULL, oid TEXT NOT NULL, PRIMARY KEY (pid, oid)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS texts_out (
    tid TEXT PRIMARY KEY, slot TEXT NOT NULL, year INTEGER, date TEXT, data TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS positions (tid TEXT PRIMARY KEY, position INTEGER) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS merge_log (
    kind TEXT NOT NULL, sortkey TEXT NOT NULL, seq INTEGER PRIMARY KEY, doc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS selected (tid TEXT PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kept_texts (tid TEXT PRIMARY KEY) WITHOUT ROWID;
"""


def connect(path: str | Path, *, cache_mb: int = CACHE_MB) -> sqlite3.Connection:
    """A connection tuned for scratch work: no journal, no waiting for the disk, one writer."""
    db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    for pragma in (
        "page_size = 8192",
        "journal_mode = OFF",
        "synchronous = OFF",
        "locking_mode = EXCLUSIVE",
        f"cache_size = -{int(cache_mb) * 1024}",
        "temp_store = FILE",
    ):
        db.execute(f"PRAGMA {pragma}")
    return db


class WorkStore:
    """A rebuild's scratch database, in a folder of its own under *scratch* (removed by
    :meth:`close`); ``None``: in memory, for small rebuilds and tests."""

    def __init__(self, scratch: Path | None = None, *, cache_mb: int = CACHE_MB) -> None:
        self.folder: Path | None = None
        if scratch is None:
            self.db = connect(":memory:", cache_mb=cache_mb)
        else:
            Path(scratch).mkdir(parents=True, exist_ok=True)
            self.folder = Path(tempfile.mkdtemp(prefix="rebuild-", dir=scratch))
            self.db = connect(self.folder / "work.sqlite", cache_mb=cache_mb)
        self.db.executescript(_SCHEMA)
        self.db.execute("BEGIN")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.db.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        return self.db.executemany(sql, rows)

    def rows(self, sql: str, params: Iterable[Any] = (), *, size: int = 10_000) -> Iterator[Any]:
        """The rows of a query, fetched a batch at a time on a cursor of their own."""
        cursor = self.db.cursor()
        cursor.execute(sql, tuple(params))
        while True:
            batch = cursor.fetchmany(size)
            if not batch:
                break
            yield from batch
        cursor.close()

    def close(self) -> None:
        """Close the database and remove its folder."""
        with contextlib.suppress(sqlite3.Error):
            self.db.execute("COMMIT")
        with contextlib.suppress(sqlite3.Error):
            self.db.close()
        if self.folder is not None:
            shutil.rmtree(self.folder, ignore_errors=True)

    def __enter__(self) -> WorkStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
