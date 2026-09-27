# SPDX-License-Identifier: MIT
"""Pure-Python PDF text extraction.

Replaces the ``pdftotext`` (poppler) system dependency.  Uses ``pypdf`` as
primary extractor and falls back to ``pdfminer.six`` when pypdf returns empty
or garbage text (common for heavily formatted, multi-column report PDFs).

No subprocess calls; no external binaries required.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Minimum character count after stripping to consider extraction successful.
_MIN_TEXT_LEN = 50


def repair_surrogates(text: str) -> str:
    """Return *text* with no UTF-16 surrogate code points left in it.

    ``pypdf`` (and, more rarely, ``pdfminer``) can hand back a lone surrogate
    such as ``"\\ud835"`` when a font's ToUnicode map is broken on a
    supplementary-plane character (mathematical italics, emoji, some CJK).
    Such a string is not encodable: any later ``write(..., encoding="utf-8")``
    dies with ``UnicodeEncodeError: surrogates not allowed`` — one such
    document is enough to stop a whole corpus build.

    A high+low pair that pypdf split into two code units is recombined into
    the real character; a lone half becomes U+FFFD, which the tokenizers
    already drop.
    """
    if not text:
        return text
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        pass
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def _extract_with_pypdf(path: Path) -> str:
    """Extract text via pypdf (pure Python, no system deps)."""
    try:
        import pypdf  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "pypdf is required for PDF text extraction.  Install with: pip install pypdf"
        ) from exc

    parts: list[str] = []
    with path.open("rb") as fh:
        reader = pypdf.PdfReader(fh, strict=False)
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
    return "\n".join(parts)


def _extract_with_pdfminer(path: Path) -> str:
    """Extract text via pdfminer.six (better layout analysis than pypdf)."""
    try:
        from pdfminer.high_level import extract_text as _extract  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "pdfminer.six is required as a PDF extraction fallback.  "
            "Install with: pip install pdfminer.six"
        ) from exc

    with path.open("rb") as fh:
        return _extract(fh)


def extract_first_page(path: Path) -> str:
    """Return text from only the first page of a PDF (fast pre-screen)."""
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    try:
        import pypdf  # type: ignore[import]

        with path.open("rb") as fh:
            reader = pypdf.PdfReader(fh, strict=False)
            if reader.pages:
                return repair_surrogates(reader.pages[0].extract_text() or "")
    except Exception as exc:
        logger.debug("First-page extraction failed for %s: %s", path.name, exc)
    return ""


def extract_text(path: Path) -> str:
    """Extract plain text from a PDF file.

    Tries ``pypdf`` first; falls back to ``pdfminer.six`` if the result is
    too short (likely a scanned or complex-layout document).

    Args:
        path: Absolute path to the PDF file.

    Returns:
        Extracted text as a unicode string.  May be empty for scanned-only
        documents without OCR layers.
    """
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    text = ""
    try:
        text = _extract_with_pypdf(path)
    except Exception as exc:
        logger.warning("pypdf extraction failed for %s: %s — trying pdfminer", path.name, exc)

    if len(text.strip()) < _MIN_TEXT_LEN:
        logger.debug(
            "pypdf returned short text (%d chars) for %s; trying pdfminer.six",
            len(text.strip()),
            path.name,
        )
        try:
            fallback = _extract_with_pdfminer(path)
        except Exception as exc:
            logger.warning("pdfminer fallback also failed for %s: %s", path.name, exc)
            fallback = ""

        if len(fallback.strip()) > len(text.strip()):
            text = fallback

    return repair_surrogates(text)
