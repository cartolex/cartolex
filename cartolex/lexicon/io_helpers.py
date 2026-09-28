# SPDX-License-Identifier: MIT
"""Corpus-contract readers and the person roster."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from .lang_utils import detect_language_text
from .utils import canonicalize_names

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

    Reads the per-document / per-person corpus indexes and collapses them to a
    single row per canonical (last_name, first_name, unit). Every other column
    of an index that does not describe a document (:data:`DOCUMENT_COLUMNS`) is
    a person attribute: the roster keeps the first non-empty value seen for each
    person, in the order the columns first appear, so any attribute can later
    colour or filter the persons by its name. This roster (the full set of
    researchers with any corpus document) is required by the atlas stages, and
    is independent of the keyword recency and document-type windows. Returns
    the number of researchers written; raises :class:`CorpusError` when no
    index yields a person.
    """
    attributes: list[str] = []
    merged: dict[tuple[str, str, str], dict[str, str]] = {}

    for idx in index_csvs:
        if not idx.exists():
            continue
        try:
            df = pd.read_csv(idx, dtype=str)
        except Exception as exc:
            logger.warning("Skipping %s while building researcher index: %s", idx.name, exc)
            continue
        if not set(IDENTITY_COLUMNS).issubset(df.columns):
            logger.warning("Skipping %s: missing identity columns for researcher index", idx.name)
            continue
        own = [c for c in df.columns if c not in IDENTITY_COLUMNS and c not in DOCUMENT_COLUMNS]
        attributes += [c for c in own if c not in attributes]
        for _, row in df.iterrows():
            last = str(row.get("last_name", "") or "").strip()
            first = str(row.get("first_name", "") or "").strip()
            unit = _normalize_unit(row.get("unit", ""))
            key = (
                canonicalize_names(last),
                canonicalize_names(first),
                canonicalize_names(unit),
            )
            person = merged.setdefault(key, {"last_name": last, "first_name": first, "unit": unit})
            for col in own:
                if not person.get(col):
                    person[col] = _attribute_value(row.get(col))

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


def _load_index_as_store(
    index_csv: Path,
    source_tag: str,
    *,
    df: pd.DataFrame | None = None,
    progress_state: dict[str, int] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
    recency_years: int | None = None,
    allowed_doc_types: set[str] | None = None,
    now_year: int | None = None,
) -> dict[tuple[str, str, str], dict[str, object]]:
    if df is None:
        if not index_csv.exists():
            raise FileNotFoundError(f"Index CSV not found (requested): {index_csv}")
        df = pd.read_csv(index_csv)

    required = {"last_name", "first_name", "unit", "txt_path"}
    missing = required.difference(df.columns)
    if missing:
        logger.warning(
            "Skipping %s: missing columns %s in %s", source_tag, sorted(missing), index_csv.name
        )
        return {}

    # Consumer-side filters. Rows whose doc_type / doc_year is absent or
    # blank are never excluded, preserving backward compatibility with legacy
    # per-person indexes and undated (e.g. manual) corpora.
    allowed = (
        {t.strip().lower() for t in allowed_doc_types} if allowed_doc_types is not None else None
    )
    cutoff_year = _filter_window(recency_years, now_year)

    store: dict[tuple[str, str, str], dict[str, object]] = {}
    n_type_filtered = 0

    last_pct = -1
    if progress_state:
        last_pct = progress_state.get("last_pct", -1)
        total = max(progress_state.get("total", 0), 1)
        seen = progress_state.get("seen", 0)
        if progress_callback:
            pct = int((seen / total) * 100)
            if pct != last_pct:
                last_pct = pct
                progress_state["last_pct"] = pct
                progress_callback(pct, f"Loading {source_tag} index ({seen}/{total})")

    for _, row in df.iterrows():
        if progress_state is not None:
            progress_state["seen"] = progress_state.get("seen", 0) + 1
            total = max(progress_state.get("total", 0), 1)
            seen = progress_state["seen"]
            if progress_callback:
                pct = int((seen / total) * 100)
                if pct != last_pct:
                    last_pct = pct
                    progress_state["last_pct"] = pct
                    progress_callback(pct, f"Loading {source_tag} ({seen}/{total})")

        if allowed is not None:
            raw_type = row.get("doc_type", "")
            # A blank cell reads back as NaN: it is a document without a type.
            dtype = "" if pd.isna(raw_type) else str(raw_type).strip().lower()
            if dtype and dtype not in allowed:
                n_type_filtered += 1
                continue
        if cutoff_year is not None:
            dyear = str(row.get("doc_year", "")).strip()
            if dyear:
                try:
                    parsed_year: int | None = int(float(dyear))
                except ValueError:
                    parsed_year = None
                if parsed_year is not None and parsed_year < cutoff_year:
                    continue

        last = str(row.get("last_name", "")).strip()
        first = str(row.get("first_name", "")).strip()
        unit = _normalize_unit(row.get("unit", ""))

        key = (
            canonicalize_names(last),
            canonicalize_names(first),
            canonicalize_names(unit),
        )

        txt_path_raw = str(row["txt_path"])
        # Resolve relative paths against the index CSV's parent directory (project root).
        # Absolute paths (legacy) are used as-is for backward compatibility.
        txt_path = Path(txt_path_raw)
        if not txt_path.is_absolute():
            txt_path = (index_csv.parent / txt_path).resolve()
        if not txt_path.exists():
            logger.warning("(%s) missing text file %s", source_tag, txt_path)
            continue

        text = txt_path.read_text(encoding="utf-8", errors="ignore")

        if key not in store:
            store[key] = {
                "last_name": last,
                "first_name": first,
                "unit": unit,
                "text_parts": [text],
                "txt_paths": [str(txt_path)],
                "sources": {source_tag},
            }
        else:
            store[key]["text_parts"] = list(store[key]["text_parts"]) + [text]
            store[key]["txt_paths"] = list(store[key]["txt_paths"]) + [str(txt_path)]
            store[key]["sources"] = set(store[key]["sources"]) | {source_tag}

    if n_type_filtered:
        # Loud on purpose: a doc-type filter silently swallowing a corpus is
        # very hard to diagnose downstream (empty keyword tables, no error).
        log = logger.error if not store else logger.warning
        log(
            "(%s) doc-type filter excluded %d row(s) (allowed doc_type: %s)%s",
            source_tag,
            n_type_filtered,
            sorted(allowed or ()),
            " — ALL rows were excluded; check the slot's doc_types" if not store else "",
        )

    return store


def _load_slots(
    indexes: Sequence[SlotIndex],
    *,
    progress_callback: Callable[[int, str], None] | None,
    recency_years: int | None,
    now_year: int | None,
) -> dict[tuple[str, str, str], dict[str, object]]:
    """Read the slot indexes in order and merge their documents by person.

    A slot whose index is missing, unreadable or empty is skipped with a
    warning; each slot's document types filter its own rows; the recency
    window applies to every dated document. Raises :class:`CorpusError` when
    no slot has a usable index.
    """
    if not indexes:
        raise CorpusError("No corpus slot to read: the settings declare none for this stage.")

    merged: dict[tuple[str, str, str], dict[str, object]] = {}
    progress_state = None
    total_rows = 0
    index_frames: list[tuple[Path, str, Collection[str] | None, pd.DataFrame]] = []
    for tag, index_csv, doc_types in indexes:
        if not index_csv.exists():
            logger.warning("Skipping %s: index file %s not found", tag, index_csv.name)
            continue
        try:
            df = pd.read_csv(index_csv)
        except Exception as exc:
            logger.warning("Skipping %s: cannot read %s: %s", tag, index_csv.name, exc)
            continue
        if df.empty:
            logger.warning("Skipping %s: index file %s is empty", tag, index_csv.name)
            continue
        index_frames.append((index_csv, tag, doc_types, df))
        total_rows += len(df)

    if not index_frames:
        raise CorpusError(
            "No valid corpus index files found (slots: "
            + ", ".join(f"{tag} → {index_csv.name}" for tag, index_csv, _ in indexes)
            + ")."
        )

    if progress_callback:
        progress_state = {"seen": 0, "total": max(total_rows, 1), "last_pct": -1}

    for index_csv, tag, doc_types, df in index_frames:
        store = _load_index_as_store(
            index_csv,
            tag,
            df=df,
            progress_state=progress_state,
            progress_callback=progress_callback,
            recency_years=recency_years,
            allowed_doc_types=set(doc_types) if doc_types is not None else None,
            now_year=now_year,
        )
        for key, info in store.items():
            if key not in merged:
                merged[key] = info
            else:
                merged[key]["text_parts"] = list(merged[key]["text_parts"]) + list(
                    info["text_parts"]
                )
                merged[key]["txt_paths"] = list(merged[key]["txt_paths"]) + list(info["txt_paths"])
                merged[key]["sources"] = set(merged[key]["sources"]) | set(info["sources"])
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
