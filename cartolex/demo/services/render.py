# SPDX-License-Identifier: MIT
"""Documents the demo services hand out: JATS XML, LaTeX sources and PDF files.

Each is rendered from a :class:`Document` — a title, an abstract (per
language), a body made of headings, paragraphs and captions — as a real
service would give it: a JATS article with its sections and translated
abstracts (Europe PMC, bioRxiv, SciELO), a LaTeX source as one gzipped file or
a gzipped tar with an ``\\input`` file and a figure (arXiv), a PDF whose text a
PDF extractor can read (HAL files, open-access copies). Rendering is
deterministic: the same document gives the same bytes.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

__all__ = [
    "Document",
    "body_blocks",
    "render_jats",
    "render_latex",
    "render_pdf",
]

#: Headings of body sections, per language (as :mod:`cartolex.demo.bodies` writes them).
_HEADINGS = {
    "en": ("Introduction", "Material and methods", "Results", "Discussion"),
    "fr": ("Introduction", "Matériel et méthodes", "Résultats", "Discussion"),
    "pt": ("Introdução", "Material e métodos", "Resultados", "Discussão"),
}
_CAPTION_WORDS = ("Figure", "Figura", "Table", "Tableau", "Tabela")


@dataclass(frozen=True)
class Document:
    """What a rendered document says.

    *titles* and *abstracts* map a language to a text, the document's own
    language first; *body* is the body in the document's language, as the demo
    world writes it (headings, paragraphs and captions separated by blank
    lines); *translations* holds the body in other languages (SciELO articles).
    """

    language: str
    titles: dict[str, str]
    abstracts: dict[str, str]
    body: str = ""
    authors: tuple[tuple[str, str], ...] = ()  # (given names, surname)
    doi: str | None = None
    year: int | None = None
    translations: dict[str, str] = field(default_factory=dict)
    ids: dict[str, str] = field(default_factory=dict)  # pub-id types: pmid, pmcid, publisher-id

    @property
    def title(self) -> str:
        return self.titles[self.language]

    @property
    def abstract(self) -> str:
        return self.abstracts.get(self.language, "")


def body_blocks(body: str, language: str) -> list[tuple[str, list[str]]]:
    """A body as sections: ``(heading, blocks)``, the blocks being paragraphs and captions.

    Text before the first heading goes in a section with an empty heading.
    """
    headings = set(_HEADINGS.get(language, ())) | {h for hs in _HEADINGS.values() for h in hs}
    sections: list[tuple[str, list[str]]] = []
    for block in (b for b in body.split("\n\n") if b.strip()):
        if block in headings:
            sections.append((block, []))
        else:
            if not sections:
                sections.append(("", []))
            sections[-1][1].append(block)
    return sections


def _is_caption(block: str) -> bool:
    first = block.split(" ", 1)[0]
    return first in _CAPTION_WORDS


# ── JATS ─────────────────────────────────────────────────────────────────────


def _jats_body(body: str, language: str) -> list[str]:
    out = ["<body>"]
    for heading, blocks in body_blocks(body, language):
        out.append("<sec>")
        if heading:
            out.append(f"<title>{escape(heading)}</title>")
        for n, block in enumerate(blocks, start=1):
            if _is_caption(block):
                out.append(
                    f'<fig id="f{n}"><caption><p>{escape(block)}</p></caption>'
                    '<graphic xlink:href="figure.tif"/></fig>'
                )
            else:
                out.append(f"<p>{escape(block)}</p>")
        out.append("</sec>")
    out.append("</body>")
    return out


def render_jats(doc: Document, *, journal: str = "", sub_articles: bool = False) -> bytes:
    """A JATS article: front matter, the abstract and translated abstracts, the body.

    With *sub_articles* (SciELO), each translation of the body becomes a
    ``sub-article`` of type ``translation`` with its own title and abstract.
    """
    lang = doc.language
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Archiving and '
        'Interchange DTD v1.2 20190208//EN" "JATS-archivearticle1.dtd">',
        '<article xmlns:xlink="http://www.w3.org/1999/xlink" '
        'xmlns:mml="http://www.w3.org/1998/Math/MathML" '
        f'article-type="research-article" dtd-version="1.2" xml:lang="{lang}">',
        "<front>",
        f"<journal-meta><journal-title-group><journal-title>{escape(journal)}"
        "</journal-title></journal-title-group></journal-meta>",
        "<article-meta>",
    ]
    if doc.doi:
        out.append(f'<article-id pub-id-type="doi">{escape(doc.doi)}</article-id>')
    for kind, value in sorted(doc.ids.items()):
        out.append(f'<article-id pub-id-type="{escape(kind)}">{escape(value)}</article-id>')
    out.append(f"<title-group><article-title>{escape(doc.title)}</article-title>")
    if not sub_articles:
        for other, title in doc.titles.items():
            if other != lang:
                out.append(
                    f'<trans-title-group xml:lang="{other}"><trans-title>{escape(title)}'
                    "</trans-title></trans-title-group>"
                )
    out.append("</title-group>")
    out.append('<contrib-group content-type="author">')
    for given, surname in doc.authors:
        out.append(
            '<contrib contrib-type="author"><name>'
            f"<surname>{escape(surname)}</surname><given-names>{escape(given)}</given-names>"
            "</name></contrib>"
        )
    out.append("</contrib-group>")
    if doc.year:
        out.append(f'<pub-date pub-type="epub"><year>{doc.year}</year></pub-date>')
    if doc.abstract:
        out.append(f"<abstract><p>{escape(doc.abstract)}</p></abstract>")
    if not sub_articles:
        for other, abstract in doc.abstracts.items():
            if other != lang:
                out.append(f'<trans-abstract xml:lang="{other}"><p>{escape(abstract)}</p>')
                out.append("</trans-abstract>")
    out += ["</article-meta>", "</front>"]
    if doc.body:
        out += _jats_body(doc.body, lang)
    out.append('<back><ref-list><title>References</title><ref id="r1"><mixed-citation>')
    out.append("A cited work, invented for the demo.</mixed-citation></ref></ref-list></back>")
    if sub_articles:
        for n, (other, title) in enumerate(doc.titles.items(), start=1):
            if other == lang:
                continue
            out.append(
                f'<sub-article article-type="translation" id="s{n}" xml:lang="{other}">'
                f"<front-stub><title-group><article-title>{escape(title)}</article-title>"
                "</title-group>"
            )
            if doc.abstracts.get(other):
                out.append(f"<abstract><p>{escape(doc.abstracts[other])}</p></abstract>")
            out.append("</front-stub>")
            if doc.translations.get(other):
                out += _jats_body(doc.translations[other], other)
            out.append("</sub-article>")
    out.append("</article>")
    return ("\n".join(out) + "\n").encode("utf-8")


# ── LaTeX ────────────────────────────────────────────────────────────────────

_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "%": r"\%",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
#: Accented letters written as macros, the way older sources do.
_ACCENT_MACROS = {
    "é": r"\'e",
    "è": r"\`e",
    "ê": r"\^e",
    "à": r"\`a",
    "á": r"\'a",
    "â": r"\^a",
    "ã": r"\~a",
    "ç": r"\c{c}",
    "í": r"\'i",
    "ó": r"\'o",
    "ô": r"\^o",
    "õ": r"\~o",
    "ú": r"\'u",
    "ü": r"\"u",
    "ë": r"\"e",
    "ï": r"\"i",
    "î": r"\^i",
    "û": r"\^u",
    "É": r"\'E",
}


def _latex(text: str, *, macros: bool) -> str:
    out = "".join(_LATEX_ESCAPES.get(c, c) for c in text)
    if macros:
        out = "".join(_ACCENT_MACROS.get(c, c) for c in out)
    return out


def render_latex(doc: Document, *, archive: bool, macros: bool = False) -> bytes:
    """A LaTeX source as an e-print server gives it: one gzipped ``.tex`` file, or (with
    *archive*) a gzipped tar holding ``main.tex``, the body in ``sections/body.tex`` pulled
    in with ``\\input`` and a figure file. With *macros*, accented letters are written as
    accent macros (``\\'e``)."""
    main = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{graphicx}",
        "% A demo source: every word is invented.",
        rf"\title{{{_latex(doc.title, macros=macros)}}}",
        r"\author{"
        + r" \and ".join(_latex(f"{g} {s}", macros=macros) for g, s in doc.authors)
        + "}",
        r"\begin{document}",
        r"\maketitle",
    ]
    if doc.abstract:
        main += [r"\begin{abstract}", _latex(doc.abstract, macros=macros), r"\end{abstract}"]
    body: list[str] = []
    for heading, blocks in body_blocks(doc.body, doc.language) if doc.body else []:
        if heading:
            body.append(rf"\section{{{_latex(heading, macros=macros)}}}")
        for block in blocks:
            text = _latex(block, macros=macros)
            if _is_caption(block):
                body += [
                    r"\begin{figure}[h]",
                    r"\centering",
                    r"\includegraphics[width=0.8\linewidth]{figure1}",
                    rf"\caption{{{text}}}",
                    r"\end{figure}",
                ]
            else:
                body += ["", text, ""]
    tail = [r"\bibliographystyle{plain}", r"\end{document}", ""]
    if not archive:
        text = "\n".join(main + body + tail)
        return gzip.compress(text.encode("utf-8"), mtime=0)
    files = {
        "main.tex": "\n".join(main + [r"\input{sections/body}"] + tail).encode("utf-8"),
        "sections/body.tex": ("\n".join(body) + "\n").encode("utf-8"),
        "figure1.png": b"\x89PNG\r\n\x1a\n" + bytes(range(64)),
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buf.getvalue(), mtime=0)


# ── PDF ──────────────────────────────────────────────────────────────────────

_LINE_CHARS = 88
_LINES_PER_PAGE = 50


def _pdf_string(line: str) -> bytes:
    data = line.encode("cp1252", errors="replace")
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def render_pdf(blocks: Sequence[str]) -> bytes:
    """A PDF of *blocks* (title, abstract, paragraphs…), each wrapped into lines, a blank line
    between blocks, fifty lines a page, in Helvetica with the Windows-1252 encoding."""
    lines: list[str] = []
    for block in blocks:
        for para in block.split("\n"):
            lines += textwrap.wrap(para, _LINE_CHARS) or [""]
        lines.append("")
    pages = [lines[i : i + _LINES_PER_PAGE] for i in range(0, len(lines), _LINES_PER_PAGE)] or [[]]
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, written once the pages are known
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for page in pages:
        ops = [b"BT /F1 10 Tf 12 TL 56 790 Td"]
        for line in page:
            ops.append(b"(" + _pdf_string(line) + b") Tj T*")
        ops.append(b"ET")
        stream = b"\n".join(ops)
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        content = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % content
        )
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        len(kids),
    )
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)
