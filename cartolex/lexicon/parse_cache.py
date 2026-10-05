# SPDX-License-Identifier: MIT
"""The parse cache: the noun-phrase analysis of each text, kept between runs.

Parsing is the slow part of the keyword extraction, and a corpus mostly grows
by adding documents, so each analysed text (a :class:`TextAnalysis`) is kept,
keyed by the sha256 of the text, the identity of the model that parsed it
(``name@version``) and :data:`~cartolex.lexicon.noun_phrases.PATTERN_VERSION`.
A later run parses only the texts it has not seen with the same model and
patterns.

Layout (the caller chooses *folder*)::

    <folder>/<model name>-<model version>/<pattern version>/analyses.sqlite

One SQLite table, ``analyses (key TEXT PRIMARY KEY, data BLOB)``: a text's
sha256 and its analysis as compressed JSON (``{"runs": …, "lemmas": …}``). One
process writes at a time, any number read: worker processes look texts up while
the run stores new ones (a cache opened with ``readonly``). An analysis is
stored once and never changed. Another model version or pattern version lives
in another folder: it is never read, and the folder can be deleted.

The parts an earlier version wrote, ``part-<digest>.jsonl`` (a header line
``{"format": "cartolex-parse/1", "model": …, "patterns": …}`` then one line per
text, ``{"sha256": …, "runs": …, "lemmas": …}``), are read into the database
the first time it is opened for writing, then left as they are; a part whose
header does not match, or that cannot be read, is skipped with a warning.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import zlib
from collections.abc import Collection, Iterator, Mapping
from pathlib import Path

from .noun_phrases import PATTERN_VERSION, TextAnalysis

__all__ = ["DB_NAME", "FORMAT", "ParseCache", "text_key"]

logger = logging.getLogger(__name__)

#: Format tag of the header line of a part written by an earlier version.
FORMAT = "cartolex-parse/1"
#: The database of one model and pattern version.
DB_NAME = "analyses.sqlite"
#: Keys looked up in one query.
_LOOKUP = 500


def text_key(text: str) -> str:
    """The cache key of a text: the sha256 of its UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pack(analysis: TextAnalysis) -> bytes:
    data = json.dumps(analysis.to_json(), ensure_ascii=False, separators=(",", ":"))
    return zlib.compress(data.encode("utf-8"), 1)


def _unpack(blob: bytes, share: dict | None) -> TextAnalysis:
    analysis = TextAnalysis.from_json(json.loads(zlib.decompress(blob)))
    return analysis if share is None else analysis.shared(share)


class ParseCache:
    """The cached analyses of one model and one pattern version, under *folder*.

    With *readonly* the cache only reads (a worker process; the database may not
    exist yet, and then nothing is found).
    """

    def __init__(
        self,
        folder: Path | str,
        model: str,
        *,
        patterns: str = PATTERN_VERSION,
        readonly: bool = False,
    ) -> None:
        name, sep, version = model.partition("@")
        if not sep or not name or not version:
            raise ValueError(f"model identity must be 'name@version', not {model!r}")
        self.model = model
        self.patterns = patterns
        self.readonly = readonly
        self.dir = Path(folder) / f"{name}-{version}" / patterns
        self.path = self.dir / DB_NAME
        self._db: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection | None:
        if self._db is not None:
            return self._db
        if self.readonly:
            if not self.path.exists():
                return None
            self._db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=60)
            return self._db
        self.dir.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        db = sqlite3.connect(str(self.path), timeout=60)
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = NORMAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS analyses (key TEXT PRIMARY KEY, data BLOB NOT NULL)"
            " WITHOUT ROWID"
        )
        db.commit()
        self._db = db
        if fresh:
            self._import_parts()
        return db

    def _header(self) -> dict[str, str]:
        return {"format": FORMAT, "model": self.model, "patterns": self.patterns}

    def _legacy(self) -> Iterator[tuple[str, TextAnalysis]]:
        header = self._header()
        for part in sorted(self.dir.glob("part-*.jsonl")):
            try:
                with part.open(encoding="utf-8") as handle:
                    if json.loads(handle.readline() or "null") != header:
                        logger.warning("Parse cache: skipping %s (another format or model).", part)
                        continue
                    found = []
                    for line in handle:
                        if line.strip():
                            entry = json.loads(line)
                            found.append((entry["sha256"], TextAnalysis.from_json(entry)))
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                logger.warning("Parse cache: skipping unreadable %s (%s).", part, exc)
                continue
            yield from found

    def _import_parts(self) -> None:
        """The parts an earlier version wrote, stored in the database (once)."""
        batch: dict[str, TextAnalysis] = {}
        for key, analysis in self._legacy():
            batch.setdefault(key, analysis)
            if len(batch) >= 10_000:
                self.write(batch)
                batch = {}
        self.write(batch)

    def read(
        self, wanted: Collection[str] | None = None, *, share: dict | None = None
    ) -> dict[str, TextAnalysis]:
        """The cached analyses, by text key (only those in *wanted* when given).

        With *share*, each analysis is read in its shared form
        (:meth:`TextAnalysis.shared`), through that table.
        """
        db = self._connect()
        if db is None:
            return {}
        out: dict[str, TextAnalysis] = {}
        if wanted is None:
            for key, blob in db.execute("SELECT key, data FROM analyses ORDER BY key"):
                out[key] = _unpack(blob, share)
            return out
        keys = sorted(set(wanted))
        for start in range(0, len(keys), _LOOKUP):
            some = keys[start : start + _LOOKUP]
            marks = ",".join("?" * len(some))
            for key, blob in db.execute(
                f"SELECT key, data FROM analyses WHERE key IN ({marks})", some
            ):
                out[key] = _unpack(blob, share)
        return out

    def write(self, analyses: Mapping[str, TextAnalysis]) -> int:
        """Store *analyses* (by text key); returns how many were new."""
        if not analyses or self.readonly:
            return 0
        db = self._connect()
        assert db is not None
        before = db.total_changes
        db.executemany(
            "INSERT OR IGNORE INTO analyses VALUES (?, ?)",
            ((key, _pack(analysis)) for key, analysis in sorted(analyses.items())),
        )
        db.commit()
        return db.total_changes - before

    def close(self) -> None:
        """Close the database (it opens again when needed)."""
        if self._db is not None:
            self._db.close()
            self._db = None

    def __enter__(self) -> ParseCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
