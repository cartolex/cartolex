from __future__ import annotations

import csv
import logging
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path

from cartolex.lexicon.pdf_text import extract_text as _extract_text

logger = logging.getLogger(__name__)


def _extract_one(path_str: str) -> tuple[str, str]:
    """Extract one PDF; return ``(raw_text, error)``.

    Module-level (picklable) so it can run in spawn-context worker processes;
    errors are returned rather than raised so a corrupt PDF never kills a
    worker batch.
    """
    try:
        return _extract_text(Path(path_str)), ""
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


def clean_text(text: str) -> str:
    """
    Light cleanup for PDF extraction artifacts.
    """
    text = re.sub(r"\(cid:\d+\)", " ", text)
    text = " ".join(text.split())
    return text


#: Columns of the corpus index written for every document, in this order after the
#: identity columns and the person attributes.
_DOCUMENT_COLUMNS = ("doc_year", "doc_type", "source", "txt_path")


def load_pdf_index(index_csv: Path) -> list[dict[str, str]]:
    """Load a PDF index CSV and return rows as dicts.

    Required columns: ``last_name``, ``first_name``, ``unit`` and
    ``pdf_filename``; optional ``doc_year`` and ``doc_type``. Other columns
    named ``pdf_*`` describe the file; any other column is a person attribute
    (see :func:`build_pdf_corpus`).
    """
    if not index_csv.exists():
        raise FileNotFoundError(f"PDF index CSV not found: {index_csv}")

    rows: list[dict[str, str]] = []
    with index_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def build_pdf_corpus(
    *,
    pdf_dir: Path,
    pdf_index_csv: Path,
    out_dir: Path,
    index_csv: Path,
    label: str | None = None,
    per_doc_hook: Callable[[str, dict[str, str]], None] | None = None,
    n_jobs: int | None = 1,
) -> None:
    """Extract text from a set of PDFs and write the per-document corpus.

    ``per_doc_hook``, when given, is called once per successfully extracted
    document with ``(raw_text, index_row)`` — the pre-cleanup text and the
    input index row (``pdf_filename``, ``doc_type``, … keys). Callers use it
    for one-pass structured extraction without re-reading the PDFs.

    ``n_jobs`` fans the CPU-bound text extraction out to worker processes
    (``None`` = all cores but one). Only the pypdf/pdfminer call runs in the
    workers; cleanup, txt writes, the hook and the index are done in the
    parent in index order, so output is identical to the serial path.
    ``n_jobs=1`` keeps the historical inline behaviour.

    *label* names the corpus in messages and fills the index's ``source``
    column (empty when not given).

    The corpus index written has the identity columns (``last_name``,
    ``first_name``, ``unit``), then every other column of the PDF index that
    is not a file column (``pdf_*``) nor a document column — person attributes
    such as a rank or a start year, carried unchanged in their input order —
    then ``doc_year``, ``doc_type``, ``source`` and ``txt_path``.
    """
    if not pdf_dir.exists():
        raise FileNotFoundError(f"PDF directory not found: {pdf_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_index_rows = load_pdf_index(pdf_index_csv)
    identity = ("last_name", "first_name", "unit")
    attributes: list[str] = []
    for row in pdf_index_rows:
        for col in row:
            if (
                col not in identity
                and col not in _DOCUMENT_COLUMNS
                and not col.startswith("pdf_")
                and col not in attributes
            ):
                attributes.append(col)
    label_msg = f" ({label})" if label else ""
    logger.info("Loaded %d index rows from %s%s", len(pdf_index_rows), pdf_index_csv, label_msg)

    # Idempotent rebuild: clear stale corpus text so re-runs do not leave behind
    # files from a previous (e.g. per-person) layout that the new index no longer
    # references.
    for stale in out_dir.glob("*.txt"):
        stale.unlink()

    rows_index: list[dict[str, str]] = []
    n_skipped = 0
    used_stems: set[str] = set()

    n_rows = len(pdf_index_rows)
    present: list[tuple[int, dict[str, str], Path]] = []
    for i, row in enumerate(pdf_index_rows):
        pdf_path = pdf_dir / row["pdf_filename"].strip()
        if not pdf_path.exists():
            # Log a positional index only — file names embed researcher names.
            logger.warning("PDF row %d listed in index but file not found on disk", i)
            continue
        present.append((i, row, pdf_path))

    if n_jobs is None:
        n_jobs = max(1, (os.cpu_count() or 2) - 1)

    def _extractions() -> Iterator[tuple[str, str]]:
        paths = [str(p) for _, _, p in present]
        if n_jobs > 1 and len(paths) > 1:
            # Same recipe as io_helpers' language split: process pool (the
            # extractors are GIL-bound pure Python), ordered map, chunked.
            # Explicit spawn context: an application may run this in a worker
            # thread of a multi-threaded process, where fork() is unsafe — and
            # spawn behaves the same on every platform, frozen or not.
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor

            ctx = multiprocessing.get_context("spawn")
            chunksize = max(1, len(paths) // (n_jobs * 4))
            logger.info("Extracting on %d worker processes", n_jobs)
            with ProcessPoolExecutor(max_workers=n_jobs, mp_context=ctx) as pool:
                yield from pool.map(_extract_one, paths, chunksize=chunksize)
        else:
            for path in paths:
                yield _extract_one(path)

    for (i, row, pdf_path), (raw_text, error) in zip(present, _extractions(), strict=True):
        last = row["last_name"].strip()
        first = row["first_name"].strip()
        unit = row["unit"].strip()
        doc_year = (row.get("doc_year") or "").strip()
        doc_type = (row.get("doc_type") or "").strip()

        # The [N%] marker lets a calling application show progress.
        pct = min(99, int(100 * i / n_rows)) if n_rows else 0
        logger.info(
            "[%d%%] Extracting text from corpus row %d/%d (doc_type=%s)",
            pct,
            i + 1,
            n_rows,
            doc_type or "?",
        )

        if error:
            logger.error("Error extracting corpus row %d: %s", i, error)
            n_skipped += 1
            continue

        if not raw_text or raw_text.strip() == "":
            logger.warning("Empty text from corpus row %d", i)
            continue

        # One .txt per document, named after the PDF stem (unique per document).
        stem = pdf_path.stem.replace("/", "-")
        if stem in used_stems:
            stem = f"{stem}_{i}"
        used_stems.add(stem)
        txt_path = out_dir / f"{stem}.txt"

        try:
            cleaned = clean_text(raw_text)
            # extract_text already repairs surrogates; this is the last belt
            # before the write, so one odd document never stops the corpus.
            safe_text = cleaned.encode("utf-8", errors="replace").decode("utf-8")
            txt_path.write_text(safe_text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — isolate the document
            logger.error("Corpus row %d skipped (%s): %s", i, type(exc).__name__, exc)
            n_skipped += 1
            continue

        rows_index.append(
            {
                "last_name": last,
                "first_name": first,
                "unit": unit,
                **{col: (row.get(col) or "").strip() for col in attributes},
                "doc_year": doc_year,
                "doc_type": doc_type,
                "source": label or "",
                # Store path relative to the index CSV's parent (project root) so that
                # the index is portable and does not embed machine-specific absolute paths.
                "txt_path": txt_path.relative_to(index_csv.parent).as_posix(),
            }
        )

        # Optional one-pass hook (e.g. structured-form extraction) on the
        # already extracted text, avoiding a second pass over the files.
        if per_doc_hook is not None:
            try:
                per_doc_hook(raw_text, row)
            except Exception as exc:  # noqa: BLE001 — the text is written; the hook is a bonus
                logger.warning("Per-document hook failed on corpus row %d: %s", i, exc)

    if n_skipped:
        logger.warning(
            "%d document(s) skipped (extraction or write error) — the corpus was built "
            "from the %d other(s).",
            n_skipped,
            len(rows_index),
        )
    logger.info("Writing per-document index (%d rows) to %s", len(rows_index), index_csv)
    with index_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[*identity, *attributes, *_DOCUMENT_COLUMNS],
        )
        writer.writeheader()
        writer.writerows(rows_index)

    logger.info("Done. %d corpus files written to %s", len(rows_index), out_dir)
