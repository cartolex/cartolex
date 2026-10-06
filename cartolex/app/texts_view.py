# SPDX-License-Identifier: MIT
"""The texts of the tables as one view of columns, for lists of millions of texts.

The Texts tab lists, filters, sorts and counts every text of the tables; the coverage
counts them by year and language; a person's sheet lists theirs. A dictionary per text
does not scale (6 M texts: minutes and gigabytes at each new version of the tables), so
the view keeps each field as a column. It is built once per version of the tables and
kept in the project's cache, in ``cache/views/texts-<version>/``: ``texts.arrow`` (the
texts' fields, Arrow's file format), ``authors.npy`` (each authorship as two codes) and
``people.json`` (the people's ids, by code). The app maps the files into memory: the
computer reads the pages a filter or a sort needs, and the app holds a few numbers per
text. Only a page's rows become dictionaries.

What a text shows of its parts (the richest content, the providers, the languages) and
its people are read as columns (:mod:`cartolex.project.text_columns`), without the
parts' contents. The copies of a work are found as
``corpus.assemble`` finds them (:func:`cartolex.project.corpus.work_copies`), from the
titles' keys the view keeps.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from cartolex.project import Project
from cartolex.project.text_columns import read_text_columns

__all__ = ["CONTENT", "FORMAT", "TextsView", "texts_view", "view_stamp"]

#: The view's format: another one is built again.
FORMAT = "cartolex-texts-view/1"
#: A text's richest content, poorest first.
CONTENT = ("title", "abstract", "full")
#: The fields of the texts table a row of the view carries.
FIELDS = (
    "text_id",
    "title",
    "year",
    "doc_type",
    "slot",
    "source",
    "doi",
    "version_of",
    "n_authors",
)
#: The tables the view is built from.
TABLES = ("texts", "text_parts", "authorships")
#: Rows read at a time.
BATCH = 65_536
#: The orders of the texts a list sorts by.
ORDERS = ("year", "title", "source", "people", "content")
#: A title's first bytes (case folded) that sort it; the rest sorts by code point.
_PREFIX = 16


def view_stamp(project: Project) -> tuple[Any, ...]:
    """What the view depends on: the format and the three tables it reads."""
    out: list[Any] = [str(project.layout.root), FORMAT]
    for name in TABLES:
        try:
            st = project.layout.table(name).stat()
            out.append((name, st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            out.append((name, None))
    return tuple(out)


@dataclass
class TextsView:
    """Every text of the tables, in ``text_id`` order: the texts table's fields (a table
    mapped from the cache), and as arrays its richest content, providers, languages,
    people and title keys."""

    table: pa.Table
    providers: list[str]
    #: Each combination of languages a text has, joined by ``+`` (``""``: none).
    languages: list[str]
    year: np.ndarray
    has_year: np.ndarray
    content: np.ndarray
    provider_bits: np.ndarray
    language: np.ndarray
    people: np.ndarray
    #: Every authorship as codes (``text``: a row of the view, ``person``: in *person_ids*).
    authors: np.ndarray
    person_ids: list[str]
    #: The view's folder in the project's cache (``None``: a view kept nowhere), where
    #: what is computed from the same tables may be kept beside it.
    folder: Path | None = None
    _orders: dict[str, np.ndarray] = field(default_factory=dict)
    _person_index: dict[str, int] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def n(self) -> int:
        return self.table.num_rows

    def counts(self) -> dict[str, dict[str, int]]:
        """How many texts have each richest content, and each provider."""
        content = np.bincount(self.content, minlength=len(CONTENT)) if self.n else [0] * 3
        return {
            "content": {c: int(k) for c, k in zip(CONTENT, content, strict=True) if k},
            "provider": {
                p: int(np.count_nonzero(self.provider_bits & np.uint64(1 << i)))
                for i, p in enumerate(self.providers)
            },
        }

    def title_keys(self) -> tuple[np.ndarray, np.ndarray]:
        """Each text's :func:`~cartolex.project.corpus.title_hashes`."""
        return (
            self.table["title_hash"].to_numpy(),
            self.table["title_length"].to_numpy(),
        )

    def of_people(self, person_ids: Iterable[str]) -> np.ndarray:
        """A mask of the texts with one of *person_ids* among their people."""
        with self._lock:
            if self._person_index is None:
                self._person_index = {p: i for i, p in enumerate(self.person_ids)}
            index = self._person_index
        codes = [index[p] for p in person_ids if p in index]
        mask = np.zeros(self.n, dtype=bool)
        if codes:
            chosen = np.zeros(len(self.person_ids), dtype=bool)
            chosen[codes] = True
            mask[self.authors["text"][chosen[self.authors["person"]]]] = True
        return mask

    def versioned(self) -> np.ndarray:
        """A mask of the texts naming a published version (``version_of``)."""
        version_of = self.table["version_of"]
        return _numpy(pc.and_kleene(pc.is_valid(version_of), pc.not_equal(version_of, "")))

    def where(self, text_ids: list[str] | set[str]) -> np.ndarray:
        """A mask of the texts among *text_ids*."""
        if not self.n:
            return np.zeros(0, dtype=bool)
        value_set = pa.array(sorted(text_ids), type=pa.string())
        return _numpy(pc.is_in(self.table["text_id"], value_set=value_set))

    def select(
        self,
        *,
        slot: str | None = None,
        year: int | None = None,
        language: str | None = None,
        content: str | None = None,
        provider: str | None = None,
        among: np.ndarray | None = None,
        q: str = "",
    ) -> np.ndarray | None:
        """A mask of the texts that pass every filter given (``None``: no filter given).
        *q* is found in the title (any case) or is the DOI; *among* is a mask."""
        masks: list[np.ndarray] = []
        if slot is not None:
            masks.append(_numpy(pc.equal(self.table["slot"], slot)))
        if year is not None:
            masks.append(self.has_year & (self.year == year))
        if language is not None:
            codes = [i for i, k in enumerate(self.languages) if language in k.split("+")]
            masks.append(np.isin(self.language, codes))
        if content is not None:
            masks.append(self.content == CONTENT.index(content))
        if provider is not None:
            if provider in self.providers:
                bit = np.uint64(1 << self.providers.index(provider))
                masks.append((self.provider_bits & bit) != 0)
            else:
                masks.append(np.zeros(self.n, dtype=bool))
        if among is not None:
            masks.append(among)
        if q:
            title = pc.match_substring(self.table["title"], q, ignore_case=True)
            masks.append(_numpy(pc.or_kleene(title, pc.equal(self.table["doi"], q))))
        if not masks:
            return None
        out = masks[0].copy()
        for mask in masks[1:]:
            out &= mask
        return out

    def order(self, name: str) -> np.ndarray:
        """The texts sorted by *name* (``year``, ``title``, ``source``, ``people``,
        ``content``), smallest first, a missing value last, ties in ``text_id`` order;
        computed once, and kept in the view's folder (a change of how an order is made
        changes :data:`FORMAT`)."""
        if name not in ORDERS:
            raise KeyError(name)
        with self._lock:
            if name not in self._orders:
                self._orders[name] = self._kept_order(name)
            return self._orders[name]

    def _kept_order(self, name: str) -> np.ndarray:
        kept = self.folder / f"order-{name}.npy" if self.folder is not None else None
        if kept is not None:
            with contextlib.suppress(OSError, ValueError):
                found = np.load(kept, mmap_mode="r", allow_pickle=False)
                if found.shape == (self.n,):
                    return found
        order = np.argsort(self._sort_key(name), kind="stable").astype(
            np.int32 if self.n < 2**31 else np.int64
        )
        if kept is not None:
            with contextlib.suppress(OSError):
                part = kept.with_name(f".{kept.name}.{os.getpid()}")
                with open(part, "wb") as fh:
                    np.save(fh, order, allow_pickle=False)
                os.replace(part, kept)
        return order

    def _sort_key(self, name: str) -> np.ndarray:
        if name == "year":
            return np.where(self.has_year, self.year.astype(np.int64), np.iinfo(np.int64).max)
        if name == "people":
            return self.people
        if name == "content":
            return self.content
        if name == "source":
            return _ranks(self.table["source"])
        if name == "title":
            return _title_ranks(self.table["title"])
        raise KeyError(name)

    def page(
        self, mask: np.ndarray | None, sort: str, offset: int, limit: int
    ) -> tuple[np.ndarray, int]:
        """The rows of one page (indices) and how many rows pass *mask*. *sort* is a sort
        name, ``-name`` for the reverse order."""
        order = self.order(sort.lstrip("-"))
        if sort.startswith("-"):
            order = order[::-1]
        if mask is not None:
            order = order[mask[order]]
        return order[offset : offset + limit], len(order)

    def rows(self, indices: np.ndarray, copies: dict[str, str]) -> list[dict[str, Any]]:
        """The texts at *indices*, as the list shows them."""
        if not len(indices):
            return []
        fields = self.table.select(list(FIELDS))
        # A row at a time: taking from a table of many chunks would join each column first.
        taken = pa.concat_tables([fields.slice(i, 1) for i in indices.tolist()]).to_pylist()
        out = []
        for row, i in zip(taken, indices.tolist(), strict=True):
            bits = int(self.provider_bits[i])
            langs = self.languages[self.language[i]]
            out.append(
                {
                    **row,
                    "doi": row["doi"] or "",
                    "version_of": row["version_of"] or "",
                    "copy_of": copies.get(row["text_id"], ""),
                    "people": int(self.people[i]),
                    "providers": [p for b, p in enumerate(self.providers) if bits >> b & 1],
                    "languages": langs.split("+") if langs else [],
                    "content": CONTENT[self.content[i]],
                }
            )
        return out


def _numpy(mask: pa.ChunkedArray | pa.Array) -> np.ndarray:
    return np.asarray(mask.fill_null(False).to_numpy(zero_copy_only=False), dtype=bool)


def _ranks(column: pa.ChunkedArray) -> np.ndarray:
    """Each value's place among the column's distinct values (a missing one last)."""
    values = pc.unique(column).drop_null()
    values = values.take(pc.sort_indices(values))
    found = pc.index_in(column, value_set=values).fill_null(len(values))
    return np.asarray(found.to_numpy(zero_copy_only=False), dtype=np.int64)


def _title_ranks(column: pa.ChunkedArray) -> np.ndarray:
    """A key sorting titles by their first bytes case folded, then by code point (a
    missing title last), without a second copy of the titles."""
    n = len(column)
    head = np.zeros((n, _PREFIX // 8), dtype=">u8")
    missing = np.zeros(n, dtype=bool)
    start = 0
    for chunk in column.chunks:
        for offset in range(0, len(chunk), BATCH):
            titles = chunk.slice(offset, BATCH).to_pylist()
            raw = b"".join(
                (t or "").casefold().encode("utf-8")[:_PREFIX].ljust(_PREFIX, b"\0") for t in titles
            )
            head[start : start + len(titles)] = np.frombuffer(raw, dtype=">u8").reshape(
                -1, _PREFIX // 8
            )
            missing[start : start + len(titles)] = [t is None for t in titles]
            start += len(titles)
    exact = np.empty(n, dtype=np.int64)
    exact[np.asarray(pc.sort_indices(column))] = np.arange(n)
    keys = [exact, *(head[:, j].astype(np.uint64) for j in reversed(range(_PREFIX // 8)))]
    order = np.lexsort([*keys, missing])
    out = np.empty(n, dtype=np.int64)
    out[order] = np.arange(n)
    return out


# ── the view on disk ─────────────────────────────────────────────────────────


def texts_view(project: Project, cache: Any = None) -> TextsView:
    """The view of the project's texts, built when its tables changed (see the module
    docstring), else mapped from the cache; kept in the app's *cache*."""
    stamp = view_stamp(project)

    def compute() -> TextsView:
        views = project.layout.cache / "views"
        digest = hashlib.blake2b(repr(stamp).encode("utf-8"), digest_size=8).hexdigest()
        folder = views / f"texts-{digest}"
        if not (folder / TEXTS_FILE).exists():
            _build(project, folder)
            for old in views.glob("texts-*"):
                if old != folder:  # an earlier version (mapped by another view: next time)
                    shutil.rmtree(old, ignore_errors=True)
        return _open(folder)

    return cache.get(("texts-view", stamp), compute) if cache is not None else compute()


#: A view's files: the texts, the authorships (text, person) as codes, the people's ids.
TEXTS_FILE = "texts.arrow"
AUTHORS_FILE = "authors.npy"
PEOPLE_FILE = "people.json"
_AUTHOR = np.dtype([("text", np.int32), ("person", np.int32)])


def _open(folder: Path) -> TextsView:
    table = pa.ipc.open_file(pa.memory_map(str(folder / TEXTS_FILE), "r")).read_all()
    meta = json.loads(table.schema.metadata[b"cartolex"])

    def column(name: str, dtype: Any) -> np.ndarray:
        return np.asarray(table[name].to_numpy(), dtype=dtype)

    return TextsView(
        table=table,
        providers=meta["providers"],
        languages=meta["languages"],
        year=np.asarray(table["year"].fill_null(0).to_numpy(), dtype=np.int32),
        has_year=_numpy(pc.is_valid(table["year"])),
        content=column("content", np.int8),
        provider_bits=column("providers", np.uint64),
        language=column("language", np.int32),
        people=column("people", np.int32),
        authors=np.load(folder / AUTHORS_FILE, mmap_mode="r", allow_pickle=False),
        person_ids=json.loads((folder / PEOPLE_FILE).read_text(encoding="utf-8")),
        folder=folder,
    )


def _build(project: Project, folder: Path) -> None:
    """Write the view of the project's tables into *folder*, which appears whole."""
    columns = read_text_columns(project.layout)
    codes, sets = columns.language_sets()
    meta = {
        "format": FORMAT,
        "providers": columns.provider_names,
        "languages": ["+".join(langs) for langs in sets],
    }
    people = np.bincount(columns.author_text, minlength=columns.n).astype(np.int32)
    authors = np.empty(len(columns.author_text), dtype=_AUTHOR)
    authors["text"], authors["person"] = columns.author_text, columns.author_person
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{folder.name}.", dir=folder.parent))
    try:
        _write_texts(
            project, work / TEXTS_FILE, meta, columns.richness, columns.providers, codes, people
        )
        np.save(work / AUTHORS_FILE, authors, allow_pickle=False)
        (work / PEOPLE_FILE).write_text(json.dumps(columns.person_ids), encoding="utf-8")
        try:
            os.rename(work, folder)
        except OSError:  # built meanwhile by another app on this project: theirs is the same
            if not (folder / TEXTS_FILE).exists():
                raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _write_texts(
    project: Project,
    path: Path,
    meta: dict[str, Any],
    content: np.ndarray,
    bits: np.ndarray,
    language: np.ndarray,
    people: np.ndarray,
) -> None:
    from cartolex.project.corpus import title_hashes
    from cartolex.project.tables import SOURCE_SCHEMAS

    texts = SOURCE_SCHEMAS["texts"]
    schema = pa.schema(
        [
            *(texts.field(name) for name in FIELDS),
            pa.field("content", pa.int8()),
            pa.field("providers", pa.uint64()),
            pa.field("language", pa.int32()),
            pa.field("people", pa.int32()),
            pa.field("title_hash", pa.uint64()),
            pa.field("title_length", pa.int32()),
        ],
        metadata={"cartolex": json.dumps(meta)},
    )
    with pa.OSFile(str(path), "wb") as sink, pa.ipc.new_file(sink, schema) as writer:
        if not project.layout.table("texts").exists():
            return
        pf = pq.ParquetFile(project.layout.table("texts"))
        there = [name for name in FIELDS if name in pf.schema_arrow.names]
        start = 0
        for batch in pf.iter_batches(BATCH, columns=there):
            end = start + batch.num_rows
            hashes, lengths = title_hashes(batch.column("title").to_pylist())
            columns = [
                batch.column(name).cast(schema.field(name).type)
                if name in there  # an optional column an older file lacks: empty
                else pa.nulls(batch.num_rows, type=schema.field(name).type)
                for name in FIELDS
            ]
            columns += [
                pa.array(content[start:end]),
                pa.array(bits[start:end]),
                pa.array(language[start:end]),
                pa.array(people[start:end]),
                pa.array(hashes),
                pa.array(lengths),
            ]
            writer.write_batch(pa.record_batch(columns, schema=schema))
            start = end
        pf.close()
