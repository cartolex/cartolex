# SPDX-License-Identifier: MIT
"""The corpus a stage reads: its people, their texts, and the texts themselves.

A corpus slot's folder comes in two forms, read alike:

- **packed** (written by a cartolex project): ``pairs.parquet``, one row per (person,
  text) pair (``person_id``, ``text``: the text's row in ``texts.parquet``, ``doc_year``,
  ``doc_type``); ``people.csv``, one row per person (``person_id``, ``last_name``,
  ``first_name``, ``unit``, then the person's attributes); ``texts.parquet``, one row per
  text (``text_id``, ``text``);
- **one file per text** (the corpus contract other applications write): ``index.csv``
  with one row per pair (``last_name``, ``first_name``, ``unit``, ``txt_path``,
  ``doc_year``, ``doc_type``, then attributes) and the text files it names.

:func:`load_corpus` reads the pairs of the slots a stage reads, with its filters (the
slots' document types, a window of years), as compact arrays: people merged by identity
across slots in order of first appearance, each text once. A text's contents are read
only when asked, a block at a time, in the order they are stored
(:meth:`CorpusIndex.texts`): a corpus of millions of texts is never held in memory.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .utils import canonicalize_names

__all__ = [
    "PAIRS_FILE",
    "PEOPLE_FILE",
    "TEXTS_FILE",
    "CorpusIndex",
    "Person",
    "index_rows",
    "is_packed",
    "load_corpus",
    "slot_people",
]

logger = logging.getLogger(__name__)

PAIRS_FILE = "pairs.parquet"
PEOPLE_FILE = "people.csv"
TEXTS_FILE = "texts.parquet"
#: The columns of a corpus row that are not a person's attributes.
_NOT_ATTRIBUTES = {"person_id", "last_name", "first_name", "unit", "txt_path", "doc_year",
                   "doc_type", "source"}  # fmt: skip


def is_packed(folder: Path) -> bool:
    """Whether a slot's folder holds a packed corpus (else the one-file-per-text contract)."""
    return (Path(folder) / PAIRS_FILE).exists()


def unit_value(raw: str) -> str:
    """A unit as the engine names it: ``NA`` when there is none."""
    unit = (raw or "").strip()
    return "NA" if not unit or unit.lower() == "nan" else unit


def identity(last: str, first: str, unit: str) -> tuple[str, str, str]:
    """The engine's identity of a person: canonical last name, first name and unit."""
    return (canonicalize_names(last), canonicalize_names(first), canonicalize_names(unit))


@dataclass
class Person:
    """One engine person: their identity, names and unit as read (``NA`` without one; the
    unit as written in *raw_unit*), and the slots they are in."""

    key: tuple[str, str, str]
    last_name: str
    first_name: str
    unit: str
    raw_unit: str = ""
    sources: list[str] = field(default_factory=list)


class _Store:
    """Where a slot's texts are read from."""

    def __init__(self, offset: int, count: int) -> None:
        self.offset, self.count = offset, count

    def read(self, rows: np.ndarray) -> Iterator[tuple[int, str]]:  # pragma: no cover
        raise NotImplementedError


class _Packed(_Store):
    def __init__(self, path: Path, offset: int, count: int) -> None:
        super().__init__(offset, count)
        self.path = path

    def read(self, rows: np.ndarray) -> Iterator[tuple[int, str]]:
        import pyarrow.parquet as pq

        wanted = np.zeros(self.count, dtype=bool)
        wanted[rows] = True
        pf = pq.ParquetFile(self.path)
        try:
            start = 0
            for i in range(pf.num_row_groups):
                n = pf.metadata.row_group(i).num_rows
                if wanted[start : start + n].any():
                    texts = pf.read_row_group(i, columns=["text"])["text"].to_pylist()
                    for j in np.flatnonzero(wanted[start : start + n]):
                        yield start + int(j), texts[j] or ""
                start += n
        finally:
            pf.close()

    def keys(self) -> list[str]:
        import pyarrow.parquet as pq

        ids = pq.read_table(self.path, columns=["text_id"])["text_id"].to_pylist()
        return [f"{self.path.parent}/{t}" for t in ids]


class _Files(_Store):
    def __init__(self, paths: list[Path], offset: int) -> None:
        super().__init__(offset, len(paths))
        self.paths = paths

    def read(self, rows: np.ndarray) -> Iterator[tuple[int, str]]:
        for row in np.sort(rows):
            try:
                text = self.paths[row].read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            yield int(row), text

    def keys(self) -> list[str]:
        return [str(p) for p in self.paths]


@dataclass
class CorpusIndex:
    """The pairs a stage reads, as arrays aligned pair by pair, in the slots' order.

    ``person[k]`` indexes :attr:`people`, ``text[k]`` the corpus's texts (a text two people
    signed has one index), ``year[k]`` is the text's year (``-1``: unknown). Read a text's
    contents with :meth:`texts`.
    """

    people: list[Person]
    person: np.ndarray
    text: np.ndarray
    year: np.ndarray
    n_texts: int
    #: The pairs read before the filters (none: the slots hold no corpus).
    rows_read: int = 0
    #: Each pair's document type, as an index into :attr:`types`.
    type_code: np.ndarray | None = None
    types: list[str] = field(default_factory=list)
    _stores: list[_Store] = field(default_factory=list, repr=False)

    def pair_types(self) -> list[str]:
        """Each pair's document type (``""`` when it has none)."""
        if self.type_code is None:
            return [""] * len(self.text)
        names = self.types
        return [names[c] for c in self.type_code.tolist()]

    def texts(self, which: Iterable[int] | np.ndarray | None = None) -> Iterator[tuple[int, str]]:
        """``(text index, text)`` for the texts *which* (every text a pair names when
        ``None``), in the order they are stored, each once."""
        wanted = np.unique(self.text if which is None else np.asarray(list(which), dtype=np.int64))
        for store in self._stores:
            mine = wanted[(wanted >= store.offset) & (wanted < store.offset + store.count)]
            if len(mine):
                for row, text in store.read(mine - store.offset):
                    yield store.offset + row, text

    def text_keys(self) -> list[str]:
        """Each text's id: the path of its file, or ``<folder>/<text id>`` when packed."""
        out: list[str] = []
        for store in self._stores:
            out += store.keys()  # type: ignore[attr-defined]
        return out

    def first_texts(self) -> np.ndarray:
        """The texts the pairs name, each once, in the order the pairs first name them."""
        _values, first = np.unique(self.text, return_index=True)
        return self.text[np.sort(first)]

    def pairs_of(self) -> list[np.ndarray]:
        """For each person, the positions of their pairs, in order."""
        order = np.argsort(self.person, kind="stable")
        bounds = np.searchsorted(self.person[order], np.arange(len(self.people) + 1))
        return [order[bounds[i] : bounds[i + 1]] for i in range(len(self.people))]


def _cutoff(recency_years: int | None, now_year: int | None) -> int | None:
    if recency_years and int(recency_years) > 0:
        if now_year is None:
            raise ValueError("a recency window needs now_year (the run context's current year)")
        return int(now_year) - (int(recency_years) - 1)
    return None


def _year(raw: object) -> int | None:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), [dict(r) for r in reader]


def slot_people(index_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """A slot's people as the roster reads them: the columns, and one row per person (a
    packed corpus's ``people.csv``) or per pair (the index), each with the person's names,
    unit and attributes. *index_path* is the slot's index, or a path in its folder."""
    index_path = Path(index_path)
    if is_packed(index_path.parent):
        return _read_csv(index_path.parent / PEOPLE_FILE)
    return _read_csv(index_path)


def index_rows(index_path: Path) -> list[dict[str, str]]:
    """Every (person, text) pair of a slot as an index row holds it, in order, with the
    person's attributes, the text's id (``text_id``: its file's name without ``.txt``
    when a file holds it) and the text itself (``text``). For inspection and tests: the
    whole slot is read into memory."""
    import pyarrow.parquet as pq

    index_path = Path(index_path)
    folder = index_path.parent
    if not is_packed(folder):
        _columns, rows = _read_csv(index_path)
        out = []
        for r in rows:
            path = Path(r.get("txt_path") or "")
            path = path if path.is_absolute() else folder / path
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            out.append({**r, "text_id": path.stem, "text": text})
        return out
    _columns, people = _read_csv(folder / PEOPLE_FILE)
    who = {r["person_id"]: {k: v for k, v in r.items() if k != "person_id"} for r in people}
    texts = pq.read_table(folder / TEXTS_FILE)
    ids, contents = texts["text_id"].to_pylist(), texts["text"].to_pylist()
    pairs = pq.read_table(folder / PAIRS_FILE).to_pylist()
    return [
        {
            **who[p["person_id"]],
            "person_id": p["person_id"],
            "doc_year": "" if p["doc_year"] is None else str(p["doc_year"]),
            "doc_type": p["doc_type"] or "",
            "text_id": ids[p["text"]],
            "text": contents[p["text"]] or "",
        }
        for p in pairs
    ]


def load_corpus(
    indexes: Sequence[tuple[str, Path, Collection[str] | None]],
    *,
    recency_years: int | None = None,
    now_year: int | None = None,
    doc_types: bool = True,
) -> CorpusIndex:
    """The pairs of the slots *indexes* (``(slot id, index path, document types)``, the index
    path naming the slot's folder), with their filters.

    A slot's document types keep the pairs of those types (a pair without a type is kept;
    *doc_types* ``False`` keeps every pair); a window of *recency_years* counted back from
    *now_year* keeps the pairs of those years (a pair without a year is kept). A slot whose
    folder holds no corpus is skipped with a warning; a text file the index names but that
    does not exist is skipped with a warning.
    """
    cutoff = _cutoff(recency_years, now_year)
    people: list[Person] = []
    index_of: dict[tuple[str, str, str], int] = {}
    persons: list[np.ndarray] = []
    texts: list[np.ndarray] = []
    years: list[np.ndarray] = []
    codes: list[np.ndarray] = []
    type_names: list[str] = []
    code_of: dict[str, int] = {}

    def type_code(kind: str) -> int:
        code = code_of.get(kind)
        if code is None:
            code = code_of[kind] = len(type_names)
            type_names.append(kind)
        return code

    stores: list[_Store] = []
    offset = 0
    rows_read = 0

    def person_of(tag: str, last: str, first: str, raw_unit: str) -> int:
        unit = unit_value(raw_unit)
        key = identity(last, first, unit)
        i = index_of.get(key)
        if i is None:
            i = index_of[key] = len(people)
            people.append(Person(key, last, first, unit, raw_unit.strip()))
        if tag not in people[i].sources:
            people[i].sources.append(tag)
        return i

    for tag, index_path, slot_types in indexes:
        folder = Path(index_path).parent
        allowed = None
        if doc_types and slot_types is not None:
            allowed = {t.strip().lower() for t in slot_types}
        if is_packed(folder):
            rows_read += _count_rows(folder / PAIRS_FILE)
            rows = _packed_pairs(folder, tag, allowed, cutoff, person_of, type_code, offset)
            count = _count_rows(folder / TEXTS_FILE)
            stores.append(_Packed(folder / TEXTS_FILE, offset, count))
        elif Path(index_path).exists():
            rows, paths, n_read = _file_pairs(
                Path(index_path), tag, allowed, cutoff, person_of, type_code, offset
            )
            rows_read += n_read
            count = len(paths)
            stores.append(_Files(paths, offset))
        else:
            logger.warning("Skipping %s: no corpus in %s", tag, folder)
            continue
        persons.append(rows[0])
        texts.append(rows[1])
        years.append(rows[2])
        codes.append(rows[3])
        offset += count

    def cat(parts: list[np.ndarray], dtype: type) -> np.ndarray:
        return np.concatenate(parts).astype(dtype) if parts else np.zeros(0, dtype=dtype)

    return CorpusIndex(
        people=people,
        person=cat(persons, np.int32),
        text=cat(texts, np.int64),
        year=cat(years, np.int32),
        n_texts=offset,
        rows_read=rows_read,
        type_code=cat(codes, np.int32),
        types=type_names,
        _stores=stores,
    )


def _count_rows(path: Path) -> int:
    import pyarrow.parquet as pq

    return pq.ParquetFile(path).metadata.num_rows


def _packed_pairs(folder, tag, allowed, cutoff, person_of, type_code, offset):
    import pyarrow.parquet as pq

    _columns, rows = _read_csv(folder / PEOPLE_FILE)
    names = {r["person_id"]: (r["last_name"], r["first_name"], r["unit"]) for r in rows}
    table = pq.read_table(
        folder / PAIRS_FILE, columns=["person_id", "text", "doc_year", "doc_type"]
    )
    pids = table["person_id"].to_pylist()
    rows_text = table["text"].to_numpy(zero_copy_only=False)
    doc_years = table["doc_year"].to_pylist()
    kinds = table["doc_type"].to_pylist()
    person, text, year, code = [], [], [], []
    seen: dict[str, int] = {}
    for pid, row, y, kind in zip(pids, rows_text, doc_years, kinds, strict=True):
        if allowed is not None and kind and kind.strip().lower() not in allowed:
            continue
        if cutoff is not None and y is not None and y < cutoff:
            continue
        i = seen.get(pid)
        if i is None:
            last, first, unit = names[pid]
            i = seen[pid] = person_of(tag, last.strip(), first.strip(), unit or "")
        person.append(i)
        text.append(offset + int(row))
        year.append(-1 if y is None else y)
        code.append(type_code(kind or ""))
    return np.array(person), np.array(text), np.array(year), np.array(code)


def _file_pairs(index_csv, tag, allowed, cutoff, person_of, type_code, offset):
    _columns, rows = _read_csv(index_csv)
    paths: list[Path] = []
    path_index: dict[Path, int] = {}
    person, text, year, code = [], [], [], []
    n_type_filtered = 0
    for r in rows:
        kind = (r.get("doc_type") or "").strip().lower()
        if allowed is not None and kind and kind not in allowed:
            n_type_filtered += 1
            continue
        y = _year(r.get("doc_year"))
        if cutoff is not None and y is not None and y < cutoff:
            continue
        raw = Path(r.get("txt_path") or "")
        path = raw if raw.is_absolute() else (index_csv.parent / raw).resolve()
        j = path_index.get(path)
        if j is None:
            if not path.exists():
                logger.warning("(%s) missing text file %s", tag, path)
                continue
            j = path_index[path] = len(paths)
            paths.append(path)
        person.append(
            person_of(
                tag,
                (r.get("last_name") or "").strip(),
                (r.get("first_name") or "").strip(),
                r.get("unit") or "",
            )
        )
        text.append(offset + j)
        year.append(-1 if y is None else y)
        code.append(type_code((r.get("doc_type") or "").strip()))
    if n_type_filtered:
        logger.warning(
            "(%s) doc-type filter excluded %d row(s) (allowed doc_type: %s)",
            tag,
            n_type_filtered,
            sorted(allowed or ()),
        )
    return (np.array(person), np.array(text), np.array(year), np.array(code)), paths, len(rows)
