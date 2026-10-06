# SPDX-License-Identifier: MIT
"""Corpus-contract readers and the person roster."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from .lang_utils import detect_language_text

if TYPE_CHECKING:
    from cartolex.context import RunContext

logger = logging.getLogger(__name__)


#: The columns of a corpus index that identify a person.
IDENTITY_COLUMNS = ("last_name", "first_name", "unit")
#: The columns of a corpus index that describe one document, not its person.
DOCUMENT_COLUMNS = ("txt_path", "doc_year", "doc_type", "source")


def _normalize_unit(raw_unit) -> str:
    unit = str(raw_unit)
    if unit.lower() == "nan" or unit.strip() == "":
        return "NA"
    return unit


def _filter_window(
    recency_years: int | None,
    now_year: int | None,
) -> int | None:
    """Return the minimum acceptable doc_year, or None if no recency filter.

    The window is counted back from *now_year* (a run's ``ctx.now_year``); a
    recency window without a year is an error — the engine never reads the
    clock to compute one.
    """
    if recency_years and int(recency_years) > 0:
        if now_year is None:
            raise ValueError("a recency window needs now_year (the run context's current year)")
        return int(now_year) - (int(recency_years) - 1)
    return None


#: One corpus slot as the loaders read it: ``(slot id, index CSV, document types)``;
#: the document types are ``None`` when every document of the slot is read.
SlotIndex = tuple[str, Path, "Collection[str] | None"]


class CorpusError(RuntimeError):
    """The corpus contract gives nothing to read: no usable index, or no person in it."""


def slot_indexes(ctx: RunContext, *, trajectory: bool = False) -> list[SlotIndex]:
    """The corpus slots a stage reads, in the settings' order, with their index files.

    The slots that build the map (``fit``), or with *trajectory* the slots the
    trajectory stage reads; each index is ``ctx.paths.corpus_index_csv(<id>)``.
    """
    settings = ctx.settings
    slots = settings.trajectory_slots if trajectory else settings.fit_slots
    return [(s.id, ctx.paths.corpus_index_csv(s.id), s.doc_types) for s in slots]


def build_researcher_index(ctx: RunContext) -> int:
    """Roster stage: write the person roster of a run (``ctx.paths.roster_csv``).

    Built from the indexes of the slots that build the map (see
    :func:`slot_indexes` and :func:`write_roster`). Returns the number of
    persons written.
    """
    index_csvs = [index_csv for _, index_csv, _ in slot_indexes(ctx)]
    return write_roster(index_csvs=index_csvs, out_csv=ctx.paths.roster_csv)


def _attribute_value(raw: object) -> str:
    """A person attribute cell as text; a blank cell (read as NaN) is empty."""
    if raw is None or (isinstance(raw, float) and raw != raw):
        return ""
    return str(raw).strip()


def write_roster(*, index_csvs: Iterable[Path], out_csv: Path) -> int:
    """Write the deduplicated person roster read from *index_csvs* to *out_csv*.

    Reads the people of each corpus slot (the folder of each index: a packed corpus's
    ``people.csv``, or the rows of a per-document index) and collapses them to a single
    row per canonical (last_name, first_name, unit). Every other column that does not
    describe a document (:data:`DOCUMENT_COLUMNS`) is a person attribute: the roster keeps
    the first non-empty value seen for each person, in the order the columns first
    appear, so any attribute can later colour or filter the persons by its name. This
    roster (the full set of researchers with any corpus document) is required by the
    atlas stages, and is independent of the keyword recency and document-type windows.
    Returns the number of researchers written; raises :class:`CorpusError` when no
    index yields a person.
    """
    from .corpus_store import identity, is_packed, slot_people, unit_value

    attributes: list[str] = []
    merged: dict[tuple[str, str, str], dict[str, str]] = {}

    for idx in index_csvs:
        idx = Path(idx)
        if not (idx.exists() or is_packed(idx.parent)):
            continue
        try:
            columns, rows = slot_people(idx)
        except Exception as exc:
            logger.warning("Skipping %s while building researcher index: %s", idx.name, exc)
            continue
        if not set(IDENTITY_COLUMNS).issubset(columns):
            logger.warning("Skipping %s: missing identity columns for researcher index", idx.name)
            continue
        own = [
            c
            for c in columns
            if c not in IDENTITY_COLUMNS and c not in DOCUMENT_COLUMNS and c != "person_id"
        ]
        attributes += [c for c in own if c not in attributes]
        for row in rows:
            last = (row.get("last_name") or "").strip()
            first = (row.get("first_name") or "").strip()
            unit = unit_value(row.get("unit") or "")
            person = merged.setdefault(
                identity(last, first, unit), {"last_name": last, "first_name": first, "unit": unit}
            )
            for col in own:
                if not person.get(col):
                    person[col] = (row.get(col) or "").strip()

    if not merged:
        raise CorpusError(
            f"No corpus index with persons found — cannot build {out_csv.name}. "
            "Write the corpus slots' index files first."
        )

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_cols = [*IDENTITY_COLUMNS, *attributes]
    pd.DataFrame(list(merged.values()), columns=out_cols).to_csv(out_csv, index=False)
    logger.info("Wrote researcher index (%d researchers) to %s", len(merged), out_csv)
    return len(merged)


def _load_slots(
    indexes: Sequence[SlotIndex],
    *,
    progress_callback: Callable[[int, str], None] | None,
    recency_years: int | None,
    now_year: int | None,
) -> dict[tuple[str, str, str], dict[str, object]]:
    """Read the slots' corpora in order and gather their documents by person.

    A slot without a corpus is skipped with a warning; each slot's document types
    filter its own pairs; the recency window applies to every dated document (see
    :func:`cartolex.lexicon.corpus_store.load_corpus`). Each person's texts come in
    the slots' order, with each text's id (the path of its file, or its id in a
    packed corpus) in ``txt_paths``. Raises :class:`CorpusError` when no slot has a
    corpus with documents.
    """
    from .corpus_store import load_corpus

    if not indexes:
        raise CorpusError("No corpus slot to read: the settings declare none for this stage.")
    corpus = load_corpus(indexes, recency_years=recency_years, now_year=now_year)
    if not corpus.rows_read:
        raise CorpusError(
            "No valid corpus index files found (slots: "
            + ", ".join(f"{tag} → {index_csv.name}" for tag, index_csv, _ in indexes)
            + ")."
        )
    if progress_callback:
        progress_callback(0, "Reading the texts")
    texts = dict(corpus.texts())
    keys = corpus.text_keys()
    merged: dict[tuple[str, str, str], dict[str, object]] = {}
    for i, t in zip(corpus.person.tolist(), corpus.text.tolist(), strict=True):
        person = corpus.people[i]
        entry = merged.get(person.key)
        if entry is None:
            entry = merged[person.key] = {
                "last_name": person.last_name,
                "first_name": person.first_name,
                "unit": person.unit,
                "text_parts": [],
                "txt_paths": [],
                "sources": set(person.sources),
            }
        entry["text_parts"].append(texts[t])  # type: ignore[attr-defined]
        entry["txt_paths"].append(keys[t])  # type: ignore[attr-defined]
    if progress_callback:
        progress_callback(100, "Texts read")
    return merged


def load_documents_selected(
    indexes: Sequence[SlotIndex],
    detect_language: bool = False,
    progress_callback: Callable[[int, str], None] | None = None,
    recency_years: int | None = None,
    now_year: int | None = None,
) -> tuple[list[str], pd.DataFrame]:
    """
    Concatenate the documents of the corpus slots *indexes* by researcher.

    *indexes* lists ``(slot id, index CSV, document types)`` in document order
    (see :func:`slot_indexes`). Optional ``recency_years`` applies
    consumer-side filtering on per-document indexes (rows lacking
    doc_year/doc_type pass through unfiltered).
    """
    merged = _load_slots(
        indexes,
        progress_callback=progress_callback,
        recency_years=recency_years,
        now_year=now_year,
    )

    docs: list[str] = []
    meta_rows: list[dict[str, str]] = []

    for key, info in merged.items():
        text_parts = [t for t in info.get("text_parts", []) if isinstance(t, str) and t.strip()]
        if not text_parts:
            continue

        joined_text = "\n\n".join(text_parts)
        docs.append(joined_text)
        doc_lang = detect_language_text(joined_text) if detect_language else ""

        sources = sorted(set(info.get("sources", set())))
        meta_rows.append(
            {
                "last_name": str(info.get("last_name", "")),
                "first_name": str(info.get("first_name", "")),
                "unit": str(info.get("unit", "")),
                "txt_path": ";".join(list(info.get("txt_paths", []))),
                "source": "+".join(sources) if sources else "",
                "last_name_canon": key[0],
                "first_name_canon": key[1],
                "unit_canon": key[2],
                "doc_language": doc_lang,
            }
        )

    if progress_callback:
        progress_callback(100, "Documents loaded.")
    return docs, pd.DataFrame(meta_rows)


#: Texts a worker counts at a time.
_COUNT_TEXTS = 2000
_COUNTING: dict[str, object] = {}


def _set_counting(params: dict) -> None:
    from sklearn.feature_extraction.text import CountVectorizer

    _COUNTING["vectorizer"] = CountVectorizer(**params)


def _count_texts(items: list[tuple[int, str]]):  # noqa: ANN202
    """In a worker: each text's counts of the fixed vocabulary (sparse rows), with the
    texts' indexes."""
    rows = [t for t, _ in items]
    return rows, _COUNTING["vectorizer"].fit_transform([text for _, text in items])  # type: ignore[attr-defined]


def count_documents_selected(
    indexes: Sequence[SlotIndex],
    count_params: dict,
    *,
    recency_years: int | None = None,
    now_year: int | None = None,
    workers: int = 1,
    progress_callback: Callable[[int, str], None] | None = None,
):  # noqa: ANN201
    """Each person's document (their texts) as counts of a fixed vocabulary, and the
    people's table.

    *count_params* are a ``CountVectorizer``'s parameters with a fixed ``vocabulary``
    (a text's counts do not depend on the others). Each text is counted once, in the
    order the corpus stores them, a block at a time in *workers* worker processes: the
    texts are never held together. A person's counts are the sum of their texts' (a
    phrase across the end of one text and the start of the next is not one). Returns
    ``(counts, meta_df)``: a CSR matrix, one row per person of the table (the people with
    a text that is not blank, in the order the slots first name them).
    """
    from scipy import sparse

    from cartolex.scale import ordered_map

    from .corpus_store import load_corpus

    if not indexes:
        raise CorpusError("No corpus slot to read: the settings declare none for this stage.")
    corpus = load_corpus(indexes, recency_years=recency_years, now_year=now_year)
    if not corpus.rows_read:
        raise CorpusError(
            "No valid corpus index files found (slots: "
            + ", ".join(f"{tag} → {index_csv.name}" for tag, index_csv, _ in indexes)
            + ")."
        )
    if progress_callback:
        progress_callback(0, "Counting the texts")
    width = len(count_params["vocabulary"])
    row_of = np.full(corpus.n_texts, -1, dtype=np.int64)
    found = int(np.unique(corpus.text).size) if len(corpus.text) else 0

    def blocks():  # noqa: ANN202
        block: list[tuple[int, str]] = []
        for t, text in corpus.texts():
            if not text.strip():
                continue
            block.append((t, text))
            if len(block) >= _COUNT_TEXTS:
                yield block
                block = []
        if block:
            yield block

    counted = []
    done = 0
    for rows, matrix in ordered_map(
        _count_texts, blocks(), workers=workers, initializer=_set_counting,
        initargs=(count_params,),
    ):  # fmt: skip
        row_of[rows] = np.arange(done, done + len(rows))
        done += len(rows)
        counted.append(matrix)
        if progress_callback:
            progress_callback(min(95, 95 * done // max(found, 1)), "Counting")
    texts = sparse.vstack(counted, format="csr") if counted else sparse.csr_matrix((0, width))
    del counted
    # Each person: the sum of their texts' counts (a text twice, counted twice).
    meta_rows: list[dict[str, str]] = []
    who: list[np.ndarray] = []
    which: list[np.ndarray] = []
    for i, positions in enumerate(corpus.pairs_of()):
        own = row_of[corpus.text[positions]]
        own = own[own >= 0]
        if not len(own):
            continue
        person = corpus.people[i]
        meta_rows.append(
            {
                "last_name": person.last_name,
                "first_name": person.first_name,
                "unit": person.unit,
                "source": "+".join(sorted(set(person.sources))),
                "last_name_canon": person.key[0],
                "first_name_canon": person.key[1],
                "unit_canon": person.key[2],
                "doc_language": "",
            }
        )
        who.append(np.full(len(own), len(meta_rows) - 1, dtype=np.int64))
        which.append(own)
    if who:
        rows_ = np.concatenate(who)
        cols = np.concatenate(which)
        pairs = sparse.csr_matrix(
            (np.ones(len(rows_), dtype=texts.dtype), (rows_, cols)),
            shape=(len(meta_rows), texts.shape[0]),
        )
        counts = (pairs @ texts).tocsr()
        counts.sort_indices()  # in column order within each row, as a vectorizer gives them
    else:
        counts = sparse.csr_matrix((0, width), dtype=np.float64)
    if progress_callback:
        progress_callback(100, "Documents counted.")
    return counts, pd.DataFrame(meta_rows)


def _split_text_by_language(full_text: str, corpus_languages: tuple[str, ...]) -> dict[str, str]:
    """Route one entity's paragraphs to per-language streams (pure function)."""
    chunks: dict[str, list[str]] = {lang: [] for lang in corpus_languages}
    for para in full_text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        lang = detect_language_text(para, allowed=corpus_languages, default="unknown")
        if lang in chunks:
            chunks[lang].append(para)
    return {lang: "\n\n".join(chunks[lang]) for lang in corpus_languages}


@dataclass(frozen=True)
class PersonTexts:
    """One person's texts, each split into its paragraphs per corpus language.

    ``texts`` holds ``(text id, {language: paragraphs})`` pairs in document
    order; the text id is the resolved path of the text file, so a text two
    people wrote has one id.
    """

    key: tuple[str, str, str]
    unit: str
    texts: tuple[tuple[str, dict[str, tuple[str, ...]]], ...]


def _split_paragraphs(text: str, corpus_languages: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """One text's paragraphs, by detected corpus language (other languages dropped)."""
    chunks: dict[str, list[str]] = {lang: [] for lang in corpus_languages}
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        lang = detect_language_text(para, allowed=corpus_languages, default="unknown")
        if lang in chunks:
            chunks[lang].append(para)
    return {lang: tuple(paras) for lang, paras in chunks.items()}


def load_texts_split_by_language(
    indexes: Sequence[SlotIndex],
    progress_callback: Callable[[int, str], None] | None = None,
    recency_years: int | None = None,
    now_year: int | None = None,
    corpus_languages: tuple[str, ...] = ("fr", "en"),
    n_jobs: int = 1,
) -> tuple[list[PersonTexts], pd.DataFrame]:
    """The texts of the corpus slots *indexes*, by person, each split by language.

    Like :func:`load_documents_split_by_language`, but each person keeps their
    texts apart (the scoring can then count a text, not only a person, and
    know which texts two people share). Rows of the returned table and items
    of the list are aligned; persons without any text are left out.
    """
    merged = _load_slots(
        indexes,
        progress_callback=progress_callback,
        recency_years=recency_years,
        now_year=now_year,
    )
    entities = []
    for key, info in merged.items():
        pairs = [
            (str(path), text)
            for text, path in zip(
                info.get("text_parts", []), info.get("txt_paths", []), strict=True
            )
            if isinstance(text, str) and text.strip()
        ]
        if pairs:
            entities.append((key, info, pairs))

    # Per-paragraph language detection is the throughput wall and embarrassingly
    # parallel; it is seeded, so parallel output is identical to serial.
    texts = [text for _, _, pairs in entities for _, text in pairs]
    if n_jobs and n_jobs > 1 and len(texts) > 1:
        from concurrent.futures import ProcessPoolExecutor
        from itertools import repeat

        chunksize = max(1, len(texts) // (n_jobs * 4))
        with ProcessPoolExecutor(max_workers=n_jobs) as pool:
            splits = list(
                pool.map(_split_paragraphs, texts, repeat(corpus_languages), chunksize=chunksize)
            )
    else:
        splits = [_split_paragraphs(text, corpus_languages) for text in texts]

    people: list[PersonTexts] = []
    meta_rows: list[dict[str, str]] = []
    position = 0
    for key, info, pairs in entities:
        person_texts = []
        for path, _text in pairs:
            person_texts.append((path, splits[position]))
            position += 1
        people.append(PersonTexts(key, str(info.get("unit", "")), tuple(person_texts)))
        sources = sorted(set(info.get("sources", set())))
        meta_rows.append(
            {
                "last_name": str(info.get("last_name", "")),
                "first_name": str(info.get("first_name", "")),
                "unit": str(info.get("unit", "")),
                "txt_path": ";".join(list(info.get("txt_paths", []))),
                "source": "+".join(sources) if sources else "",
                "last_name_canon": key[0],
                "first_name_canon": key[1],
                "unit_canon": key[2],
            }
        )
    if progress_callback:
        progress_callback(100, "Texts split & loaded.")
    return people, pd.DataFrame(meta_rows)


def load_documents_split_by_language(
    indexes: Sequence[SlotIndex],
    progress_callback: Callable[[int, str], None] | None = None,
    recency_years: int | None = None,
    now_year: int | None = None,
    corpus_languages: tuple[str, ...] = ("fr", "en"),
    n_jobs: int = 1,
) -> tuple[dict[str, list[str]], pd.DataFrame]:
    """
    Concatenate the documents of the corpus slots *indexes* by entity, then
    split the content into one stream per configured *corpus language* by
    paragraph-level detection.

    *indexes* lists ``(slot id, index CSV, document types)`` in document order
    (see :func:`slot_indexes`). A paragraph whose detected language is not in
    ``corpus_languages`` is dropped (the set is configurable; the default is
    FR/EN). Optional ``recency_years`` applies consumer-side filtering on
    per-document indexes (rows lacking doc_year/doc_type pass through
    unfiltered).

    Returns: ``(docs_by_lang, meta_df)`` where ``docs_by_lang[lang]`` is a list
    of per-entity concatenated text, aligned row-for-row with ``meta_df``.
    """
    merged = _load_slots(
        indexes,
        progress_callback=progress_callback,
        recency_years=recency_years,
        now_year=now_year,
    )

    docs_by_lang: dict[str, list[str]] = {lang: [] for lang in corpus_languages}
    meta_rows: list[dict[str, str]] = []

    entities = []
    for key, info in merged.items():
        text_parts = [t for t in info.get("text_parts", []) if isinstance(t, str) and t.strip()]
        if not text_parts:
            continue
        entities.append((key, info, "\n\n".join(text_parts)))

    # Per-paragraph language detection is the extraction throughput wall
    # (measured: hours at 1e5+ docs, single-threaded) and embarrassingly
    # parallel per entity. Detection is deterministic (seeded langdetect), so
    # parallel output is identical to serial. n_jobs=1 keeps the historical
    # inline path.
    texts = [full_text for _, _, full_text in entities]
    if n_jobs and n_jobs > 1 and len(texts) > 1:
        from concurrent.futures import ProcessPoolExecutor
        from itertools import repeat

        chunksize = max(1, len(texts) // (n_jobs * 4))
        with ProcessPoolExecutor(max_workers=n_jobs) as pool:
            splits = list(
                pool.map(
                    _split_text_by_language, texts, repeat(corpus_languages), chunksize=chunksize
                )
            )
    else:
        splits = [_split_text_by_language(full_text, corpus_languages) for full_text in texts]

    for (key, info, _full_text), chunks_by_lang in zip(entities, splits, strict=True):
        for lang in corpus_languages:
            docs_by_lang[lang].append(chunks_by_lang[lang])

        sources = sorted(set(info.get("sources", set())))
        meta_rows.append(
            {
                "last_name": str(info.get("last_name", "")),
                "first_name": str(info.get("first_name", "")),
                "unit": str(info.get("unit", "")),
                "txt_path": ";".join(list(info.get("txt_paths", []))),
                "source": "+".join(sources) if sources else "",
                "last_name_canon": key[0],
                "first_name_canon": key[1],
                "unit_canon": key[2],
            }
        )

    if progress_callback:
        progress_callback(100, "Documents split & loaded.")

    return docs_by_lang, pd.DataFrame(meta_rows)
