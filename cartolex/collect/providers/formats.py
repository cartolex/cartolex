# SPDX-License-Identifier: MIT
"""Structured full texts and files, read into parts: JATS XML, LaTeX sources, PDF files.

* :func:`read_jats` — a JATS article (Europe PMC, bioRxiv/medRxiv, SciELO PS):
  its abstract and translated abstracts, its body with the sections kept as
  paragraphs (a section's title, its paragraphs, figure and table captions),
  and each translation a ``sub-article`` holds; references, formulas and
  citation markers are dropped;
* :func:`latex_source` and :func:`read_latex` — an e-print source (one gzipped
  file or a gzipped tar, ``\\input`` files inlined) and its title, abstract and
  body, sections kept as paragraphs, accent macros and escapes decoded, maths,
  labels, citations and comments dropped;
* :func:`pdf_text` — a PDF's text through cartolex's PDF extraction, the file
  isolated: written to its own temporary file, any failure reported, never raised.

Every reader refuses what it cannot trust (an XML file that declares
entities, an archive member that is too large) with ``ValueError``.
"""

from __future__ import annotations

import gzip
import html
import io
import re
import tarfile
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..text import clean

__all__ = [
    "Structured",
    "check_pdf",
    "check_xml",
    "latex_source",
    "pdf_text",
    "read_jats",
    "read_latex",
]

_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
#: Parts of a JATS body that carry no running text.
_JATS_DROPPED = {
    "xref",
    "disp-formula",
    "inline-formula",
    "tex-math",
    "graphic",
    "inline-graphic",
    "supplementary-material",
    "object-id",
    "label",
    "ref-list",
    "fn-group",
}
_SKIPPED_ABSTRACTS = {"graphical", "teaser", "toc", "editor-summary"}
MAX_MEMBER_BYTES = 5_000_000
MAX_MEMBERS = 500
MAX_INPUT_DEPTH = 8


@dataclass
class Structured:
    """What a structured document says: title, and abstracts and bodies per language.

    A language is ``None`` when the document does not declare it.
    """

    title: str = ""
    language: str | None = None
    abstracts: list[tuple[str | None, str]] = field(default_factory=list)
    bodies: list[tuple[str | None, str]] = field(default_factory=list)


# ── JATS ─────────────────────────────────────────────────────────────────────


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _entities(text: str) -> str:
    def one(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in _XML_ENTITIES or name.startswith("#"):
            return m.group(0)
        decoded = html.unescape(m.group(0))
        return decoded if decoded != m.group(0) else " "

    return re.sub(r"&([#\w]+);", one, text)


def _parse_xml(data: bytes) -> ET.Element:
    """Parse XML without its DOCTYPE; refuse a document that declares entities."""
    text = data.decode("utf-8", errors="replace")
    if re.search(r"<!ENTITY", text, re.IGNORECASE):
        raise ValueError("the document declares entities; it is not read")
    text = re.sub(r"<!DOCTYPE[^>\[]*(\[[^\]]*\])?\s*>", "", text, count=1)
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    try:
        return ET.fromstring(_entities(text))
    except ET.ParseError as exc:
        raise ValueError(f"not well-formed XML ({exc})") from None


def check_xml(data: bytes) -> None:
    """Raise ``ValueError`` unless *data* is a well-formed XML document (a cut answer is not)."""
    _parse_xml(data)


def _text(el: ET.Element) -> str:
    """The running text of *el*, without the dropped elements, spaces collapsed."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if _local(node.tag) in _JATS_DROPPED:
            if node.tail:
                parts.append(node.tail)
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
        if node.tail and node is not el:
            parts.append(node.tail)

    walk(el)
    return " ".join("".join(parts).split())


def _blocks(el: ET.Element, *, titles: bool) -> list[str]:
    """Paragraph-level blocks of *el*: section titles (with *titles*), paragraphs, captions."""
    out: list[str] = []
    for child in el:
        tag = _local(child.tag)
        if tag in _JATS_DROPPED:
            continue
        if tag == "title":
            if titles:
                text = _text(child)
                if text:
                    out.append(text)
        elif tag == "p":
            # A paragraph may hold a figure or a list: its own words, then theirs.
            own = ET.Element("p")
            own.text = child.text
            for sub in child:
                if _local(sub.tag) in ("fig", "table-wrap", "list", "boxed-text", "disp-quote"):
                    continue
                own.append(sub)
            text = _text(own)
            if text:
                out.append(text)
            for sub in child:
                if _local(sub.tag) in ("fig", "table-wrap", "list", "boxed-text", "disp-quote"):
                    out += _blocks(sub, titles=True)
        elif tag in ("fig", "table-wrap"):
            caption = next((c for c in child if _local(c.tag) == "caption"), None)
            if caption is not None:
                out += _blocks(caption, titles=True)
        elif tag in ("sec", "list", "list-item", "boxed-text", "disp-quote", "caption", "abstract"):
            out += _blocks(child, titles=titles)
        elif tag in ("def-list", "statement", "app"):
            out += _blocks(child, titles=titles)
    return out


def _lang(el: ET.Element, default: str | None) -> str | None:
    value = el.get(_XML_LANG)
    return value.lower()[:2] if value else default


def read_jats(data: bytes) -> Structured:
    """Title, abstracts and bodies (per language) of a JATS article.

    Raises ``ValueError`` when *data* is not a readable JATS article.
    """
    root = _parse_xml(data)
    if _local(root.tag) != "article":
        raise ValueError(f"not a JATS article (root element {_local(root.tag)!r})")
    language = _lang(root, None)
    out = Structured(language=language)
    front = next((c for c in root if _local(c.tag) == "front"), None)
    meta = None
    if front is not None:
        meta = next((c for c in front if _local(c.tag) == "article-meta"), None)
    if meta is not None:
        title = meta.find(".//{*}article-title")
        if title is None:
            title = meta.find(".//article-title")
        out.title = _text(title) if title is not None else ""
        for el in meta:
            tag = _local(el.tag)
            if tag == "abstract" and el.get("abstract-type") not in _SKIPPED_ABSTRACTS:
                text = "\n\n".join(_blocks(el, titles=False)) or _text(el)
                if text:
                    out.abstracts.append((_lang(el, language), text))
            elif tag == "trans-abstract":
                text = "\n\n".join(_blocks(el, titles=False)) or _text(el)
                if text:
                    out.abstracts.append((_lang(el, None), text))
    body = next((c for c in root if _local(c.tag) == "body"), None)
    if body is not None:
        text = "\n\n".join(_blocks(body, titles=True))
        if text:
            out.bodies.append((language, text))
    for sub in root:
        if _local(sub.tag) != "sub-article" or sub.get("article-type") != "translation":
            continue
        lang = _lang(sub, None)
        for el in sub.iter():
            if _local(el.tag) == "abstract" and el.get("abstract-type") not in _SKIPPED_ABSTRACTS:
                text = "\n\n".join(_blocks(el, titles=False)) or _text(el)
                if text:
                    out.abstracts.append((lang, text))
                break
        sub_body = next((c for c in sub if _local(c.tag) == "body"), None)
        if sub_body is not None:
            text = "\n\n".join(_blocks(sub_body, titles=True))
            if text:
                out.bodies.append((lang, text))
    return out


# ── LaTeX ────────────────────────────────────────────────────────────────────


def latex_source(data: bytes) -> str:
    """The LaTeX text of an e-print: one gzipped file, or a gzipped tar whose main file
    (the one with ``\\documentclass``) gets its ``\\input`` and ``\\include`` files inlined.

    Raises ``ValueError`` when *data* is neither (a PDF-only submission, a cut file).
    """
    try:
        raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    except (OSError, EOFError) as exc:
        raise ValueError(f"the archive cannot be read ({exc})") from None
    if raw[:4] == b"%PDF":
        raise ValueError("the e-print is a PDF, not a LaTeX source")
    files: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
            for n, member in enumerate(tar):
                if n >= MAX_MEMBERS:
                    raise ValueError("the archive holds too many files")
                if not member.isfile() or not member.name.endswith(".tex"):
                    continue
                if member.size > MAX_MEMBER_BYTES:
                    raise ValueError(f"{member.name} is too large to read")
                handle = tar.extractfile(member)
                if handle is not None:
                    name = str(PurePosixPath(member.name))
                    files[name] = handle.read().decode("utf-8", errors="replace")
    except tarfile.TarError:
        files = {}
    if not files:
        if len(raw) > MAX_MEMBER_BYTES:
            raise ValueError("the source is too large to read")
        text = raw.decode("utf-8", errors="replace")
        if "\\" not in text:
            raise ValueError("not a LaTeX source")
        return text
    main = next((n for n in sorted(files) if "\\documentclass" in files[n]), sorted(files)[0])

    def inline(text: str, depth: int) -> str:
        if depth > MAX_INPUT_DEPTH:
            return text

        def one(m: re.Match[str]) -> str:
            name = m.group(2).strip()
            for candidate in (name, f"{name}.tex"):
                if candidate in files:
                    return inline(files[candidate], depth + 1)
            return ""

        return re.sub(r"\\(input|include)\s*\{([^}]*)\}", one, text)

    return inline(files[main], 0)


_ACCENTS = {
    "'": "\u0301",
    "`": "\u0300",
    "^": "\u0302",
    '"': "\u0308",
    "~": "\u0303",
    "c": "\u0327",
    "=": "\u0304",
    "u": "\u0306",
    "v": "\u030c",
    ".": "\u0307",
    "H": "\u030b",
    "k": "\u0328",
}
#: Escaped characters; braces and the tilde go through placeholders (restored last), since
#: bare braces and tildes are LaTeX syntax.
_ESCAPES = {
    "\\%": "%",
    "\\&": "&",
    "\\$": "$",
    "\\#": "#",
    "\\_": "_",
    "\\{": "\x02",
    "\\}": "\x03",
}
_DROPPED_ENVS = ("equation", "equation*", "align", "align*", "eqnarray", "eqnarray*",
                 "displaymath", "tabular", "thebibliography", "verbatim", "lstlisting")  # fmt: skip
_HEADINGS = r"(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?"
_DROPPED_ARGS = r"(?:label|ref|eqref|cite\w*|citep|citet|includegraphics|bibliography|bibliographystyle|vspace|hspace|url|footnote|thanks|pagestyle|thispagestyle|setlength|newcommand|renewcommand|usepackage|documentclass)"


def _braced(text: str, start: int) -> tuple[str, int] | None:
    """The content of the brace group opening at *start*, and the index after it."""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "\\":
            continue
        if c == "{" and (i == 0 or text[i - 1] != "\\"):
            depth += 1
        elif c == "}" and text[i - 1] != "\\":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
    return None


def _command_arg(text: str, name: str) -> str | None:
    m = re.search(r"\\" + name + r"\s*(\[[^\]]*\])?\s*\{", text)
    if not m:
        return None
    found = _braced(text, m.end() - 1)
    return found[0] if found else None


def _decode(text: str) -> str:
    """Accent macros, escapes and the last commands of a LaTeX fragment → plain text."""

    def accent(m: re.Match[str]) -> str:
        mark, letter = m.group(1), m.group(2) or m.group(3)
        if letter.strip() in ("\\i", "i") and mark != "c":
            letter = "i"
        return letter + _ACCENTS.get(mark, "")

    text = re.sub(r"\\" + _DROPPED_ARGS + r"\s*(\[[^\]]*\])?\s*\{[^{}]*\}", "", text)
    # \'e, \'{e}, {\'e}; a letter-named accent (\c, \v…) needs braces or a space: \c{c}, \c c.
    text = re.sub(
        r"\{?\\([\'`^\"~=.])\s*(?:\{\s*(\\?[A-Za-z])\s*\}|(\\i(?![A-Za-z])\s?|[A-Za-z]))\}?",
        accent,
        text,
    )
    text = re.sub(r"\{?\\([Hckuv])(?:\s*\{\s*(\\?[A-Za-z])\s*\}|\s+([A-Za-z]))\}?", accent, text)
    text = text.replace("\\textbackslash{}", "\x00").replace("\\textasciitilde{}", "\x01")
    text = text.replace("\\textasciicircum{}", "^")
    for escaped, plain in _ESCAPES.items():
        text = text.replace(escaped, plain)
    text = re.sub(r"\\(ldots|dots)\b\s*", "…", text)
    text = re.sub(r"\\[,;:!]", " ", text)  # small spaces
    text = re.sub(r"\\i\b\s?", "i", text)  # a dotless i left alone
    text = text.replace("---", "—").replace("--", "–").replace("``", "“").replace("''", "”")
    text = re.sub(r"(?<!\\)~", " ", text)
    for _ in range(5):  # nested styling: \emph{\textbf{x}}
        text = re.sub(r"\\[A-Za-z]+\*?\s*(\[[^\]]*\])?\s*\{([^{}]*)\}", r"\2", text)
    text = re.sub(r"\\[A-Za-z]+\*?", "", text)
    text = text.replace("{", "").replace("}", "").replace("\\\\", " ").replace("\x00", "\\")
    text = text.replace("\x01", "~").replace("\x02", "{").replace("\x03", "}")
    return unicodedata.normalize("NFC", text)


def read_latex(source: str) -> Structured:
    """Title, abstract and body of a LaTeX source (sections and captions kept as paragraphs)."""
    # Comments go first (an escaped \\% stays).
    text = re.sub(r"(?<!\\)%[^\n]*", "", source)
    out = Structured()
    title = _command_arg(text, "title")
    out.title = " ".join(_decode(title).split()) if title else ""
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", text, re.S)
    if m:
        abstract = _paragraphs(m.group(1))
        if abstract:
            out.abstracts.append((None, abstract))
    begin = text.find("\\begin{document}")
    body = text[begin + len("\\begin{document}") :] if begin >= 0 else text
    body = body.split("\\end{document}", 1)[0]
    if m:
        body = body[body.find("\\end{abstract}") + len("\\end{abstract}") :]
    body = re.sub(r"\\maketitle", "", body)
    for env in _DROPPED_ENVS:
        body = re.sub(
            r"\\begin\{" + re.escape(env) + r"\}.*?\\end\{" + re.escape(env) + r"\}",
            "\n\n",
            body,
            flags=re.S,
        )
    body = re.sub(r"\$\$.*?\$\$|\\\[.*?\\\]|(?<!\\)\$[^$]*(?<!\\)\$", " ", body, flags=re.S)
    # Figures and tables: their captions, as paragraphs, where they stand.

    def figure(fm: re.Match[str]) -> str:
        caption = _command_arg(fm.group(0), "caption")
        return f"\n\n{caption}\n\n" if caption else "\n\n"

    body = re.sub(r"\\begin\{(figure|table)\*?\}.*?\\end\{\1\*?\}", figure, body, flags=re.S)

    body_text = body
    pieces: list[str] = []
    pos = 0
    for hm in re.finditer(r"\\" + _HEADINGS + r"\s*(\[[^\]]*\])?\s*\{", body_text):
        if hm.start() < pos:
            continue
        found = _braced(body_text, hm.end() - 1)
        if not found:
            continue
        pieces.append(body_text[pos : hm.start()])
        pieces.append(f"\n\n{found[0]}\n\n")
        pos = found[1]
    pieces.append(body_text[pos:])
    body = "".join(pieces)
    body = re.sub(r"\\item\s*(\[[^\]]*\])?", "\n\n", body)
    body = re.sub(r"\\(begin|end)\{[^}]*\}(\[[^\]]*\])?", "\n\n", body)
    text_body = _paragraphs(body)
    if text_body:
        out.bodies.append((None, text_body))
    return out


def _paragraphs(fragment: str) -> str:
    paragraphs = []
    for para in re.split(r"\n\s*\n", fragment):
        text = " ".join(_decode(para).split())
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


# ── PDF ──────────────────────────────────────────────────────────────────────


def check_pdf(data: bytes) -> None:
    """Raise ``ValueError`` unless *data* looks like a whole PDF (a cut file does not)."""
    if not data.startswith(b"%PDF") or b"%%EOF" not in data[-2048:]:
        raise ValueError("not a whole PDF file")


def pdf_text(data: bytes, work_dir: Path) -> tuple[str, str | None]:
    """The text of a PDF as ``(text, error)``; the file is extracted on its own, from a
    temporary file in *work_dir* removed afterwards, in the job's worker process (a file
    that takes too long is left out, :mod:`cartolex.collect.pdfworker`), and a failure is
    returned, never raised.

    Lines are joined into paragraphs (a blank line separates them), spaces tidied, and a
    word cut at a line-end hyphen is joined again.
    """
    from ..pdfworker import PdfError, extract_pdf

    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_dir, prefix="pdf-") as tmp:
        path = Path(tmp) / "document.pdf"
        path.write_bytes(data)
        try:
            raw = extract_pdf(path)
        except PdfError as exc:  # one broken file never stops a job
            return "", str(exc)
    text = clean(re.sub(r"\(cid:\d+\)", " ", raw))
    # A word cut at a hyphen at the end of a line is joined again, hyphen kept (it may belong
    # to a compound word): "ground-\nwater" gives "ground-water", never two words.
    text = re.sub(r"(?<=\w)-\n(?=[^\W\d_])", "-", text)
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paragraphs if p), None
