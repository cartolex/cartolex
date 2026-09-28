# SPDX-License-Identifier: MIT
"""The text providers of the demo services: preprint servers, Europe PMC, a file host.

Served from the demo's sources layer (:mod:`cartolex.demo.services.sources`):

* **arXiv** (``arxiv``): ``api/query?id_list=…`` answers an Atom feed (the
  abstract in ``summary``, wrapped as the real feed wraps it; ``arxiv:doi`` for
  a preprint that was published); ``e-print/<id>`` the LaTeX source, one
  gzipped file or a gzipped tar; ``pdf/<id>`` a PDF;
* **bioRxiv and medRxiv** (``biorxiv``):
  ``details/<server>/<DOI>/na/json`` (``{"messages", "collection"}``, the
  ``jatsxml`` link and ``published``); ``content/<DOI>.source.xml`` the JATS;
* **Europe PMC** (``europepmc``): ``search?query=DOI:"…"&resultType=core&format=json``
  (``resultList.result`` with ``abstractText``, ``pmcid``, ``isOpenAccess``);
  ``<PMCID>/fullTextXML`` the JATS of an open-access article (404 otherwise);
* **a file host** (``files``): ``oa/<work>.pdf``, the open-access copies OpenAlex links to.
"""

from __future__ import annotations

import re
import textwrap
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from .biblio import Bibliography
from .http import DEMO_BASE, Reply, Request, json_reply
from .render import Document, render_jats, render_latex, render_pdf
from .sources import ArxivEntry, sources_layer

__all__ = ["ArxivService", "BiorxivService", "EuropePmcService", "FilesService"]

_ATOM = "http://www.w3.org/2005/Atom"


def _not_found(what: str) -> Reply:
    return json_reply(404, {"error": f"no such {what}"})


class ArxivService:
    """The arXiv routes of the demo services."""

    name = "arxiv"

    def __init__(self, bib: Bibliography) -> None:
        self.layer = sources_layer(bib)

    def handle(self, request: Request) -> Reply:
        path = request.path.strip("/")
        if path == "api/query":
            return self._query(request.query)
        m = re.fullmatch(r"(e-print|pdf)/([\d.]+?)(v\d+)?", path)
        if m:
            entry = self.layer.arxiv.get(m.group(2))
            if entry is None:
                return _not_found("e-print")
            if m.group(1) == "pdf":
                blocks = [entry.title, entry.abstract]
                blocks += [b for b in entry.body.split("\n\n") if b.strip()]
                return Reply(200, render_pdf(blocks), "application/pdf")
            return self._source(entry)
        return _not_found("route")

    @staticmethod
    def _document(entry: ArxivEntry) -> Document:
        return Document(
            language=entry.language,
            titles={entry.language: entry.title},
            abstracts={entry.language: entry.abstract},
            body=entry.body,
            authors=entry.authors,
            doi=entry.doi,
            year=entry.year,
        )

    def _source(self, entry: ArxivEntry) -> Reply:
        data = render_latex(self._document(entry), archive=entry.archive, macros=entry.macros)
        kind = "application/x-eprint-tar" if entry.archive else "application/x-eprint"
        return Reply(200, data, kind)

    def _query(self, query: dict[str, str]) -> Reply:
        ids = [i.strip() for i in query.get("id_list", "").split(",") if i.strip()]
        found = [
            self.layer.arxiv[i.split("v")[0]] for i in ids if i.split("v")[0] in self.layer.arxiv
        ]
        out = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<feed xmlns="{_ATOM}" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" '
            'xmlns:arxiv="http://arxiv.org/schemas/atom">',
            "<id>https://arxiv.org/api/demo</id>",
            "<title>arXiv Query: demo</title>",
            "<updated>2026-01-01T00:00:00Z</updated>",
            f"<opensearch:totalResults>{len(found)}</opensearch:totalResults>",
            "<opensearch:startIndex>0</opensearch:startIndex>",
            f"<opensearch:itemsPerPage>{len(found)}</opensearch:itemsPerPage>",
        ]
        for e in found:
            summary = "\n  ".join(textwrap.wrap(e.abstract, 78))
            out += [
                "<entry>",
                f"<id>http://arxiv.org/abs/{e.arxiv_id}v1</id>",
                f"<updated>{e.date}T00:00:00Z</updated>",
                f"<published>{e.date}T00:00:00Z</published>",
                f"<title>{escape(e.title)}</title>",
                f"<summary>  {escape(summary)}\n</summary>",
            ]
            out += [f"<author><name>{escape(f'{g} {s}')}</name></author>" for g, s in e.authors]
            if e.doi:
                out += [
                    f"<arxiv:doi>{escape(e.doi)}</arxiv:doi>",
                    f'<link title="doi" href={quoteattr("https://doi.org/" + e.doi)} rel="related"/>',
                ]
            out += [
                f'<link href="{DEMO_BASE}arxiv/abs/{e.arxiv_id}v1" rel="alternate" type="text/html"/>',
                f'<link title="pdf" href="{DEMO_BASE}arxiv/pdf/{e.arxiv_id}v1" rel="related" '
                'type="application/pdf"/>',
                '<arxiv:primary_category term="demo.coast" scheme="http://arxiv.org/schemas/atom"/>',
                "</entry>",
            ]
        out.append("</feed>")
        return Reply(
            200, ("\n".join(out) + "\n").encode("utf-8"), "application/atom+xml; charset=utf-8"
        )


class BiorxivService:
    """The bioRxiv and medRxiv routes of the demo services."""

    name = "biorxiv"

    def __init__(self, bib: Bibliography) -> None:
        self.layer = sources_layer(bib)

    def handle(self, request: Request) -> Reply:
        path = request.path.strip("/")
        m = re.fullmatch(r"details/(biorxiv|medrxiv)/(.+)/na/json", path)
        if m:
            entry = self.layer.biorxiv.get(m.group(2).lower())
            if entry is None or entry.server != m.group(1):
                return json_reply(
                    200, {"messages": [{"status": "no posts found"}], "collection": []}
                )
            record: dict[str, Any] = {
                "doi": entry.doi,
                "title": entry.title,
                "authors": "; ".join(f"{s}, {g[0]}." for g, s in entry.authors),
                "author_corresponding": " ".join(entry.authors[0]),
                "author_corresponding_institution": "",
                "date": entry.date,
                "version": "1",
                "type": "new results",
                "license": "cc_by",
                "category": "ecology",
                "jatsxml": f"{DEMO_BASE}biorxiv/content/{entry.doi}v1.source.xml",
                "abstract": entry.abstract,
                "published": entry.published_doi or "NA",
                "server": entry.server,
            }
            return json_reply(
                200,
                {"messages": [{"status": "ok", "count": 1, "total": "1"}], "collection": [record]},
            )
        m = re.fullmatch(r"content/(.+)v1\.source\.xml", path)
        if m:
            entry = self.layer.biorxiv.get(m.group(1).lower())
            if entry is None:
                return _not_found("file")
            work = self.layer.work(entry.world_work)
            doc = Document(
                language=work.language,
                titles={work.language: entry.title},
                abstracts={work.language: entry.abstract},
                body=entry.body,
                authors=entry.authors,
                doi=entry.doi,
            )
            return Reply(200, render_jats(doc, journal=entry.server), "application/xml")
        return _not_found("route")


class EuropePmcService:
    """The Europe PMC routes of the demo services."""

    name = "europepmc"

    def __init__(self, bib: Bibliography) -> None:
        self.layer = sources_layer(bib)
        self._by_doi = {e.doi.lower(): e for e in self.layer.pmc.values()}

    def handle(self, request: Request) -> Reply:
        path = request.path.strip("/")
        if path == "search":
            return self._search(request.query)
        m = re.fullmatch(r"(PMC\d+)/fullTextXML", path)
        if m:
            entry = self.layer.pmc.get(m.group(1))
            if entry is None or not entry.open_access:
                return _not_found("full text")
            work = self.layer.work(entry.world_work)
            doc = Document(
                language=work.language,
                titles={work.language: entry.title},
                abstracts={work.language: entry.abstract},
                body=entry.body,
                authors=entry.authors,
                doi=entry.doi,
                year=entry.year,
                ids={"pmid": entry.pmid, "pmcid": entry.pmcid},
            )
            return Reply(200, render_jats(doc, journal="Demo Journal"), "application/xml")
        return _not_found("route")

    def _search(self, query: dict[str, str]) -> Reply:
        q = query.get("query", "")
        m = re.fullmatch(r'\s*DOI:"?([^"]+)"?\s*', q)
        found = []
        if m and m.group(1).lower() in self._by_doi:
            e = self._by_doi[m.group(1).lower()]
            found.append(
                {
                    "id": e.pmid,
                    "source": "MED",
                    "pmid": e.pmid,
                    "pmcid": e.pmcid,
                    "doi": e.doi,
                    "title": e.title,
                    "authorString": ", ".join(f"{s} {g[0]}" for g, s in e.authors) + ".",
                    "pubYear": str(e.year),
                    "abstractText": e.abstract,
                    "isOpenAccess": "Y" if e.open_access else "N",
                    "inEPMC": "Y" if e.open_access else "N",
                    "inPMC": "Y" if e.open_access else "N",
                    "hasPDF": "Y" if e.open_access else "N",
                }
            )
        return json_reply(
            200,
            {
                "version": "demo",
                "hitCount": len(found),
                "request": {"queryString": q, "resultType": query.get("resultType", "lite")},
                "resultList": {"result": found},
            },
        )


class FilesService:
    """The file host of the demo services: open-access copies of works, as PDFs."""

    name = "files"

    def __init__(self, bib: Bibliography) -> None:
        self.layer = sources_layer(bib)

    def handle(self, request: Request) -> Reply:
        m = re.fullmatch(r"oa/(w\d+)\.pdf", request.path.strip("/"))
        if not m or m.group(1) not in self.layer.oa_links:
            return _not_found("file")
        work = self.layer.work(m.group(1))
        return Reply(200, render_pdf(self.layer.pdf_blocks(work)), "application/pdf")
