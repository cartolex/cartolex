# SPDX-License-Identifier: MIT
"""Text hygiene at the boundary: what a service or a file gives becomes clean plain text.

Every title, abstract and document that enters the source tables passes here:

* :func:`clean` — Unicode NFC, lone surrogates and control characters removed,
  line ends unified, runs of spaces and blank lines collapsed;
* :func:`strip_markup` — JATS and HTML tags removed (block tags become line
  breaks), entities decoded, a leading « Abstract » label dropped; says which
  format the text was in (``plain``, ``jats`` or ``latex``);
* :func:`abstract_from_inverted_index` — an abstract given as an inverted index
  (word → positions) rebuilt in word order;
* :func:`detect_language` — the language of a part, ``und`` when unsure.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Mapping, Sequence

from cartolex.lexicon.lang_utils import detect_language_text

__all__ = [
    "DETECTED_LANGUAGES",
    "abstract_from_inverted_index",
    "clean",
    "detect_language",
    "strip_markup",
]

#: The languages a part can be detected in; anything else is ``und``.
DETECTED_LANGUAGES = ("en", "fr", "pt", "es", "de", "it", "nl", "ca")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b\u2028\u2029\ufeff\ufffd]")
_SPACES = re.compile(r"[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+")
_BLANKS = re.compile(r"\n{3,}")
#: Tags are removed only when they are markup: a JATS or MathML tag, or a known HTML tag
#: (so that « a<b and c>d » in a formula survives).
_INLINE_TAGS = frozenset(
    {"i", "b", "u", "em", "strong", "sub", "sup", "span", "a", "italic", "bold", "sc", "small"}
)
_TAG = re.compile(
    r"</?((?:jats|mml|xlink):[\w.-]+|[A-Za-z][\w-]*)"
    r"((?:\s+[\w:.-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'<>]+))*)\s*/?>"
)
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "ul",
        "ol",
        "sec",
        "title",
        "list",
        "list-item",
        "abstract",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "table",
        "tr",
    }
)
_LABEL = re.compile(r"^\s*(abstract|summary|résumé|resumo|resumen)\s*[:.\-–—]?\s*\n", re.IGNORECASE)
_LATEX = re.compile(r"\\(?:[A-Za-z]+)\s*\{|\$[^$\n]{1,200}\$")
_LATEX_CMD = re.compile(r"\\(?:emph|textit|textbf|mathrm|mathit|text|mathbf)\s*\{([^{}]*)\}")


def clean(text: str | None) -> str:
    """NFC, no lone surrogate or control character, tidy spaces and line breaks."""
    if not text:
        return ""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # Recombine a surrogate pair split in two; a lone half becomes U+FFFD, removed below.
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub(lambda m: " " if m.group(0) in "\u2028\u2029" else "", text)
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANKS.sub("\n\n", text)
    return text.strip()


def strip_markup(text: str | None) -> tuple[str, str]:
    """Remove JATS or HTML markup; returns ``(text, format)`` with format ``plain``, ``jats`` or
    ``latex`` (what it was in before cleaning). LaTeX is kept, with its simplest styling
    commands unwrapped."""
    if not text:
        return "", "plain"
    fmt = "plain"

    def replace(match: re.Match[str]) -> str:
        nonlocal fmt
        name = match.group(1).lower()
        local = name.split(":")[-1]
        if ":" not in name and local not in _BLOCK_TAGS and local not in _INLINE_TAGS:
            return match.group(0)
        fmt = "jats"  # markup of the JATS or HTML family, stored as plain text once cleaned
        return "\n\n" if local in _BLOCK_TAGS else ""

    text = _TAG.sub(replace, text)
    text = html.unescape(text)
    if fmt == "plain" and _LATEX.search(text):
        fmt = "latex"
        text = _LATEX_CMD.sub(r"\1", text)
    text = clean(text)
    if _LABEL.match(text + "\n"):
        text = _LABEL.sub("", text + "\n", count=1).strip()
    return text, fmt


def abstract_from_inverted_index(index: Mapping[str, Sequence[int]] | None) -> str:
    """The abstract an inverted index (word → positions) encodes, words in position order.

    Raises ``ValueError`` when the index is not a mapping of words to integer positions.
    """
    if not index:
        return ""
    if not isinstance(index, Mapping):
        raise ValueError("an inverted index is a mapping of words to positions")
    placed: dict[int, str] = {}
    for word, positions in index.items():
        if not isinstance(positions, list | tuple):
            raise ValueError(f"positions of {word!r} are not a list")
        for pos in positions:
            if not isinstance(pos, int) or isinstance(pos, bool) or pos < 0:
                raise ValueError(f"position {pos!r} of {word!r} is not a non-negative integer")
            placed[pos] = str(word)
    return " ".join(placed[pos] for pos in sorted(placed))


def detect_language(text: str, *, languages: Sequence[str] = DETECTED_LANGUAGES) -> str:
    """The language of *text* among *languages*, or ``und`` when it cannot be told."""
    if len(text.split()) < 4:
        return "und"
    found = detect_language_text(text, allowed=tuple(languages), default="und")
    return found if found in languages else "und"
