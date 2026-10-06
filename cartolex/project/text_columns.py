# SPDX-License-Identifier: MIT
"""The texts of a project's tables as columns: what lists and counts need of each text.

The coverage of each person, the corpus screen's lists and counts read the same few
facts of every text: its year, whether its published version supersedes it, the
richest part it has, its providers, the languages of its words and of its title, its
source, and its people. A dictionary per text takes gigabytes and minutes for a
project of millions of texts; read here a batch of rows at a time into arrays, a text
costs a few dozen bytes and a person is a code.

:func:`read_text_columns` reads every text, or only the texts of some people (a
person's sheet): then only the row groups that hold them are read.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .layout import ProjectLayout
from .tables import check_source_file, find_ids, id_keys

__all__ = ["RICHNESS", "WORD_PARTS", "TextColumns", "read_text_columns"]

#: The parts that hold a text's words (a title does not).
WORD_PARTS = frozenset({"abstract", "body", "full"})
#: How rich each part makes a text: 0 a title only, 1 an abstract, 2 a full text.
RICHNESS = {"abstract": 1, "body": 2, "full": 2}
#: Rows read at a time.
BATCH = 65_536


@dataclass
class TextColumns:
    """Texts in ``text_id`` order, each a row of every array; authorships as codes."""

    keys: np.ndarray  #: text ids (:func:`~cartolex.project.tables.id_keys`)
    year: np.ndarray  #: int32, 0 when unknown
    has_year: np.ndarray
    #: A preprint whose published version is in the tables (read through it).
    superseded: np.ndarray
    richness: np.ndarray  #: int8, :data:`RICHNESS`
    providers: np.ndarray  #: uint64, bit *i*: ``provider_names[i]``
    provider_names: list[str]
    #: Languages (bits, by ``language_names``) of the parts with words, and of the title.
    worded: np.ndarray
    titled: np.ndarray
    language_names: list[str]
    source: np.ndarray  #: int32, by ``sources``
    sources: list[str]
    author_text: np.ndarray  #: int32: an authorship's text (a row)
    author_person: np.ndarray  #: int32: an authorship's person, by ``person_ids``
    person_ids: list[str]

    @property
    def n(self) -> int:
        return len(self.keys)

    def tid(self, i: int) -> str:
        return bytes(self.keys[i]).decode("utf-8")

    def lookup(self, ids: Collection[str]) -> np.ndarray:
        """Each text id's row (``-1``: not read)."""
        return find_ids(self.keys, list(ids))

    def has_words(self) -> np.ndarray:
        return self.richness > 0

    def language_sets(self) -> tuple[np.ndarray, list[list[str]]]:
        """Each text's languages as a code: of its parts with words, else of its title; and
        each code's languages, sorted (an empty list: none known)."""
        if not self.n:
            return np.zeros(0, dtype=np.int32), []
        chosen = np.where(self.has_words()[:, None], self.worded, self.titled)
        combos, codes = np.unique(chosen, axis=0, return_inverse=True)
        names = [
            sorted(
                {
                    self.language_names[w * 64 + b]
                    for w, word in enumerate(combo)
                    for b in range(64)
                    if int(word) >> b & 1
                }
                - {""}
            )
            for combo in combos
        ]
        return np.asarray(codes, dtype=np.int32).reshape(-1), names

    def texts_by_person(self, *, read: bool = True) -> dict[str, np.ndarray]:
        """Each person's texts (rows, in ``text_id`` order); with *read*, only the texts
        read (a superseded preprint is read through its published version)."""
        keep = ~self.superseded[self.author_text] if read else np.ones(len(self.author_text), bool)
        texts, who = self.author_text[keep], self.author_person[keep]
        order = np.argsort(who, kind="stable")
        bounds = np.searchsorted(who[order], np.arange(len(self.person_ids) + 1))
        return {
            pid: texts[order[bounds[c] : bounds[c + 1]]]
            for c, pid in enumerate(self.person_ids)
            if bounds[c + 1] > bounds[c]
        }


def read_text_columns(
    layout: ProjectLayout, *, people: Collection[str] | None = None
) -> TextColumns:
    """The texts of the project's tables as columns (see the module docstring): every
    text, or with *people* only the texts of those people and their authorships."""
    texts_path, parts_path = layout.table("texts"), layout.table("text_parts")
    authors_path = layout.table("authorships")
    for name, path in (
        ("texts", texts_path),
        ("text_parts", parts_path),
        ("authorships", authors_path),
    ):
        if path.exists():
            check_source_file(path, name)
    # The authorships first when only some people's texts are read: they name the texts.
    person_codes: dict[str, int] = {}
    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    wanted: np.ndarray | None = None
    if people is not None:
        chosen = pa.array(sorted(set(people)), type=pa.string())
        ids: list[str] = []
        if authors_path.exists():
            for batch in _batches(authors_path, ["text_id", "person_id"]):
                mine = pc.is_in(batch.column(1), value_set=chosen)
                if pc.any(mine).as_py():
                    batch = batch.filter(mine)
                    ids += batch.column(0).to_pylist()
        wanted = (
            np.unique(np.array([i.encode("utf-8") for i in ids], dtype=object)) if ids else None
        )
    texts = _texts(texts_path, wanted)
    keys = id_keys(texts["text_id"]) if texts.num_rows else np.zeros(0, dtype="S1")
    n = len(keys)
    year_col = texts["year"]
    has_year = _numpy(pc.is_valid(year_col)) if n else np.zeros(0, dtype=bool)
    year = np.asarray(year_col.fill_null(0).to_numpy(zero_copy_only=False), dtype=np.int32)
    superseded = _superseded(texts, texts_path, people is not None)
    source, sources = _codes(texts["source"])
    del texts
    richness = np.zeros(n, dtype=np.int8)
    providers = np.zeros(n, dtype=np.uint64)
    provider_codes: dict[str, int] = {}
    language_codes: dict[str, int] = {}
    worded = [np.zeros(n, dtype=np.uint64)]
    titled = [np.zeros(n, dtype=np.uint64)]
    if parts_path.exists() and n:
        for batch in _batches(parts_path, ["text_id", "part", "language", "provider"], keys):
            at = find_ids(keys, batch.column(0))
            ok = at >= 0
            part, kinds = _local_codes(batch.column(1))
            rich = np.array([RICHNESS.get(k, 0) for k in kinds], dtype=np.int8)[part]
            np.maximum.at(richness, at[ok], rich[ok])
            codes = _codes_in(batch.column(3), provider_codes).astype(np.uint64)
            if len(provider_codes) > 64:
                raise ValueError(f"{parts_path}: more than 64 providers")
            np.bitwise_or.at(providers, at[ok], np.uint64(1) << codes[ok])
            langs = _codes_in(batch.column(2), language_codes)
            while len(language_codes) > 64 * len(worded):
                worded.append(np.zeros(n, dtype=np.uint64))
                titled.append(np.zeros(n, dtype=np.uint64))
            has_words = np.array([k in WORD_PARTS for k in kinds], dtype=bool)[part]
            is_title = np.array([k == "title" for k in kinds], dtype=bool)[part]
            bit = np.uint64(1) << (langs % 64).astype(np.uint64)
            for word in range(len(worded)):
                inside = ok & (langs // 64 == word)
                np.bitwise_or.at(worded[word], at[inside & has_words], bit[inside & has_words])
                np.bitwise_or.at(titled[word], at[inside & is_title], bit[inside & is_title])
    if authors_path.exists() and n:
        chosen_people = (
            pa.array(sorted(set(people)), type=pa.string()) if people is not None else None
        )
        for batch in _batches(authors_path, ["text_id", "person_id"], keys):
            if chosen_people is not None:
                batch = batch.filter(pc.is_in(batch.column(1), value_set=chosen_people))
            at = find_ids(keys, batch.column(0))
            who = _codes_in(batch.column(1), person_codes).astype(np.int32)
            pairs.append((at[at >= 0].astype(np.int32), who[at >= 0]))
    return TextColumns(
        keys=keys,
        year=year,
        has_year=has_year,
        superseded=superseded,
        richness=richness,
        providers=providers,
        provider_names=sorted(provider_codes, key=provider_codes.__getitem__),
        worded=np.stack(worded, axis=1),
        titled=np.stack(titled, axis=1),
        language_names=sorted(language_codes, key=language_codes.__getitem__),
        source=source,
        sources=sources,
        author_text=np.concatenate([t for t, _ in pairs]) if pairs else np.zeros(0, np.int32),
        author_person=np.concatenate([p for _, p in pairs]) if pairs else np.zeros(0, np.int32),
        person_ids=sorted(person_codes, key=person_codes.__getitem__),
    )


_TEXT_COLUMNS = ["text_id", "year", "version_of", "source"]


def _texts(path: Path, wanted: np.ndarray | None) -> pa.Table:
    """The texts table's columns this module reads: every row, or the rows of *wanted*
    (UTF-8 ids, sorted)."""
    schema = pa.schema(
        [
            ("text_id", pa.string()),
            ("year", pa.int32()),
            ("version_of", pa.string()),
            ("source", pa.string()),
        ]
    )
    if not path.exists():
        return schema.empty_table()
    if wanted is None:
        return pq.read_table(path, columns=_TEXT_COLUMNS).cast(schema)
    if not len(wanted):
        return schema.empty_table()
    keys = np.array(list(wanted), dtype=f"S{max(len(w) for w in wanted)}")
    chosen = pa.array([w.decode("utf-8") for w in wanted], type=pa.string())
    found = [
        b.filter(pc.is_in(b.column(0), value_set=chosen))
        for b in _batches(path, _TEXT_COLUMNS, keys)
    ]
    return pa.Table.from_batches(found, schema=schema) if found else schema.empty_table()


def _superseded(texts: pa.Table, path: Path, partial: bool) -> np.ndarray:
    """Each text: a preprint whose published version is in the tables."""
    if not texts.num_rows:
        return np.zeros(0, dtype=bool)
    version_of = texts["version_of"]
    if not partial:
        return _numpy(pc.is_in(version_of, value_set=texts["text_id"].combine_chunks()))
    targets = pc.unique(version_of.drop_null()).to_pylist()
    if not targets:
        return np.zeros(texts.num_rows, dtype=bool)
    present = _texts(path, np.array(sorted(t.encode("utf-8") for t in targets), dtype=object))
    return _numpy(pc.is_in(version_of, value_set=present["text_id"].combine_chunks()))


def _batches(
    path: Path, columns: list[str], keys: np.ndarray | None = None
) -> Iterator[pa.RecordBatch]:
    """The rows of a table sorted by ``text_id``, a row group at a time; with *keys* (sorted
    ids), only the row groups whose ``text_id`` range holds one of them."""
    pf = pq.ParquetFile(path)
    try:
        position = pf.schema_arrow.get_field_index("text_id")
        for group in range(pf.num_row_groups):
            if keys is not None and len(keys):
                stats = pf.metadata.row_group(group).column(position).statistics
                if stats is not None and stats.has_min_max:
                    low = np.searchsorted(
                        keys, np.array([stats.min.encode("utf-8")], dtype=keys.dtype)
                    )[0]
                    if low >= len(keys) or keys[low] > np.array(
                        stats.max.encode("utf-8"), dtype=keys.dtype
                    ):
                        continue
            table = pf.read_row_group(group, columns=columns)
            yield from table.to_batches(max_chunksize=BATCH)
    finally:
        pf.close()


def _local_codes(column: pa.Array) -> tuple[np.ndarray, list[str]]:
    """Each value's code among the column's distinct values, and those values (a missing
    value reads as empty)."""
    encoded = pc.dictionary_encode(column, null_encoding="encode")
    values = [v if v is not None else "" for v in encoded.dictionary.to_pylist()]
    return np.asarray(encoded.indices.to_numpy(zero_copy_only=False), dtype=np.int64), values


def _codes_in(column: pa.Array, codes: dict[str, int]) -> np.ndarray:
    """Each value's code in *codes*, a new value taking the next code: one Python step
    per distinct value of the column, not per row."""
    local, values = _local_codes(column)
    mapping = np.array([codes.setdefault(v, len(codes)) for v in values], dtype=np.int64)
    return mapping[local] if len(values) else np.zeros(len(column), dtype=np.int64)


def _codes(column: pa.ChunkedArray) -> tuple[np.ndarray, list[str]]:
    if not len(column):
        return np.zeros(0, dtype=np.int32), []
    encoded = pc.dictionary_encode(column.combine_chunks())
    names = [str(v) if v is not None else "" for v in encoded.dictionary.to_pylist()]
    codes = encoded.indices.fill_null(0).to_numpy(zero_copy_only=False)
    return np.asarray(codes, dtype=np.int32), names


def _numpy(mask: pa.ChunkedArray | pa.Array) -> np.ndarray:
    return np.asarray(mask.fill_null(False).to_numpy(zero_copy_only=False), dtype=bool)
