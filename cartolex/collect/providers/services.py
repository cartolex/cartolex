# SPDX-License-Identifier: MIT
"""The text providers: arXiv, bioRxiv and medRxiv, Europe PMC, HAL, SciELO, OpenAlex.

Each one improves texts already found: a missing abstract, and on request a
full text. Structured full texts (JATS, LaTeX) come before PDF files; a PDF
gives a ``full`` part, read whole. What each one sends is declared for the
privacy summary.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..hal import HAL_FIELDS, parse_hal_doc
from ..http import HttpClient, NotFound, ServiceError
from ..scielo import parse_scielo_article
from ..text import abstract_from_inverted_index
from .base import (
    Found,
    Provided,
    Provider,
    TextRef,
    UnsafeLink,
    check_link,
    is_local,
    parts_from,
    plain_abstracts,
)
from .formats import check_pdf, check_xml, latex_source, pdf_text, read_jats, read_latex

__all__ = [
    "ArxivProvider",
    "BiorxivProvider",
    "EuropePmcProvider",
    "HalProvider",
    "OpenAlexProvider",
    "ScieloProvider",
]

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def _bare_arxiv(value: str) -> str:
    value = value.strip().rsplit("/abs/", 1)[-1].removeprefix("arXiv:")
    head, sep, tail = value.rpartition("v")
    return head if sep and tail.isdigit() and head else value


def _check_eprint(data: bytes) -> None:
    if data[:4] == b"%PDF":
        check_pdf(data)
        return
    latex_source(data)


def _pdf_found(data: bytes, work_dir: Path, text: TextRef) -> Found:
    content, error = pdf_text(data, work_dir)
    if error or not content:
        return Found(read="pdf", error=error or "the PDF holds no text")
    from ..text import detect_language

    return Found([Provided("full", detect_language(content), "plain", content)], read="pdf")


class ArxivProvider(Provider):
    """arXiv: the abstract from the API (and the DOI of the published version), the LaTeX
    source as full text. One request every three seconds, as arXiv asks."""

    name = "arxiv"
    service = "arxiv"
    sends = ("identifier",)
    describes = "the arXiv identifier of a preprint"
    full_text_format = "latex"
    batch = 50

    def for_abstract(self, text: TextRef) -> bool:
        return bool(text.ids.get("arxiv"))

    for_full_text = for_abstract

    def abstracts(self, client: HttpClient, texts: Sequence[TextRef]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        by_id: dict[str, list[TextRef]] = {}
        for t in texts:
            by_id.setdefault(_bare_arxiv(t.ids["arxiv"]), []).append(t)
        ids = sorted(by_id)
        for i in range(0, len(ids), self.batch):
            chunk = ids[i : i + self.batch]
            try:
                data = client.get_bytes(
                    "arxiv",
                    "query",
                    {"id_list": ",".join(chunk), "max_results": len(chunk)},
                    kind="arxiv_query",
                    sends=self.sends,
                    validate=check_xml,
                ).content
                entries = self._entries(data)
            except (ServiceError, ValueError) as exc:
                for aid in chunk:
                    for t in by_id[aid]:
                        out[t.text_id] = exc
                continue
            for aid in chunk:
                entry = entries.get(aid)
                for t in by_id[aid]:
                    if entry is None:
                        out[t.text_id] = None
                        continue
                    summary, doi = entry
                    links = [("version_of_doi", doi)] if doi and doi != (t.doi or "") else []
                    out[t.text_id] = Found(plain_abstracts([(None, summary)]), links, read="api")
        return out

    def abstract(self, client: HttpClient, text: TextRef) -> Found | None:
        result = self.abstracts(client, [text])[text.text_id]
        if isinstance(result, Exception):
            raise result
        return result

    @staticmethod
    def _entries(data: bytes) -> dict[str, tuple[str, str | None]]:
        root = ET.fromstring(data)
        out: dict[str, tuple[str, str | None]] = {}
        for entry in root.iter(f"{_ATOM}entry"):
            ident = entry.findtext(f"{_ATOM}id") or ""
            title = entry.findtext(f"{_ATOM}title") or ""
            if "api/errors" in ident or title.strip() == "Error":
                continue
            summary = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())
            doi = (entry.findtext(f"{_ARXIV}doi") or "").strip().lower() or None
            out[_bare_arxiv(ident)] = (summary, doi)
        return out

    def full_text(self, client: HttpClient, text: TextRef, work_dir: Path) -> Found | None:
        base = client.service("arxiv").base_url
        root = base[: -len("/api")] if base.endswith("/api") else base
        try:
            data = client.get_bytes(
                "arxiv",
                f"{root}/e-print/{_bare_arxiv(text.ids['arxiv'])}",
                kind="arxiv_eprint",
                sends=self.sends,
                validate=_check_eprint,
            ).content
        except NotFound:
            return None
        if data[:4] == b"%PDF":  # a submission without its source
            return _pdf_found(data, work_dir, text)
        doc = read_latex(latex_source(data))
        return Found(parts_from(doc, fmt="latex"), read="latex")


class BiorxivProvider(Provider):
    """bioRxiv and medRxiv: the abstract and the published DOI from the details endpoint, the
    JATS full text from the link it gives. Tried for preprints whose DOI has one of
    *doi_prefixes* (the servers' own prefix by default)."""

    name = "biorxiv"
    service = "biorxiv"
    sends = ("DOI",)
    describes = "the DOI of a preprint"
    full_text_format = "jats"

    def __init__(self, doi_prefixes: Sequence[str] = ("10.1101/",)) -> None:
        self.doi_prefixes = tuple(p.lower() for p in doi_prefixes)

    def for_abstract(self, text: TextRef) -> bool:
        return (
            text.doc_type == "preprint"
            and bool(text.doi)
            and str(text.doi).startswith(self.doi_prefixes)
        )

    for_full_text = for_abstract

    def _record(self, client: HttpClient, doi: str) -> dict[str, Any] | None:
        for server in ("biorxiv", "medrxiv"):
            data = client.get_json(
                "biorxiv",
                f"details/{server}/{doi}/na/json",
                kind="biorxiv_details",
                sends=self.sends,
                validate=lambda d: d["collection"].__iter__(),
            ).data
            versions = [r for r in data.get("collection") or [] if isinstance(r, dict)]
            if versions:
                return max(versions, key=lambda r: int(str(r.get("version") or "0") or 0))
        return None

    @staticmethod
    def _links(record: dict[str, Any]) -> list[tuple[str, str]]:
        published = str(record.get("published") or "").strip().lower()
        return [("version_of_doi", published)] if published.startswith("10.") else []

    def abstract(self, client: HttpClient, text: TextRef) -> Found | None:
        record = self._record(client, str(text.doi))
        if record is None:
            return None
        parts = plain_abstracts([(None, str(record.get("abstract") or ""))])
        return Found(parts, self._links(record), read="api")

    def full_text(self, client: HttpClient, text: TextRef, work_dir: Path) -> Found | None:
        record = self._record(client, str(text.doi))
        if record is None or not record.get("jatsxml"):
            return None
        url = check_link(str(record["jatsxml"]), local_ok=is_local(client, "biorxiv"))
        data = client.get_bytes(
            "biorxiv", url, kind="biorxiv_jats", sends=self.sends, validate=check_xml
        ).content
        return Found(parts_from(read_jats(data), fmt="jats"), self._links(record), read="jats")


class EuropePmcProvider(Provider):
    """Europe PMC: the abstract from a search by DOI, the JATS full text of open-access
    articles (by the PMCID the search gave)."""

    name = "europepmc"
    service = "europepmc"
    sends = ("DOI", "identifier")
    describes = "the DOI of an article, then the PMCID Europe PMC gave for it"
    full_text_format = "jats"

    def for_abstract(self, text: TextRef) -> bool:
        return bool(text.doi or text.ids.get("pmcid") or text.ids.get("pmid"))

    for_full_text = for_abstract

    def _record(self, client: HttpClient, text: TextRef) -> dict[str, Any] | None:
        if text.doi:
            query = f'DOI:"{text.doi}"'
        elif text.ids.get("pmcid"):
            query = f"PMCID:{text.ids['pmcid']}"
        else:
            query = f"EXT_ID:{text.ids['pmid']} AND SRC:MED"
        data = client.get_json(
            "europepmc",
            "search",
            {"query": query, "resultType": "core", "format": "json", "pageSize": 1},
            kind="europepmc_search",
            sends=["DOI" if text.doi else "identifier"],
            validate=lambda d: d["resultList"]["result"].__iter__(),
        ).data
        results = [r for r in data["resultList"]["result"] if isinstance(r, dict)]
        return results[0] if results else None

    def abstract(self, client: HttpClient, text: TextRef) -> Found | None:
        record = self._record(client, text)
        if record is None or not record.get("abstractText"):
            return None
        return Found(plain_abstracts([(None, str(record["abstractText"]))]), read="api")

    def full_text(self, client: HttpClient, text: TextRef, work_dir: Path) -> Found | None:
        record = self._record(client, text)
        if record is None or record.get("isOpenAccess") != "Y" or not record.get("pmcid"):
            return None
        try:
            data = client.get_bytes(
                "europepmc",
                f"{record['pmcid']}/fullTextXML",
                kind="europepmc_fulltext",
                sends=["identifier"],
                validate=check_xml,
            ).content
        except NotFound:
            return None
        return Found(parts_from(read_jats(data), fmt="jats"), read="jats")


class HalProvider(Provider):
    """HAL: abstracts in every language of a deposit (found by its HAL id, else its DOI),
    and its main file (a PDF) as full text."""

    name = "hal"
    service = "hal"
    sends = ("identifier", "DOI")
    describes = "the HAL identifier, else the DOI, of a text"
    full_text_format = "pdf"

    def for_abstract(self, text: TextRef) -> bool:
        return bool(text.ids.get("hal") or text.doi)

    for_full_text = for_abstract

    def _doc(self, client: HttpClient, text: TextRef) -> dict[str, Any] | None:
        hal_id = text.ids.get("hal")
        q = f'halId_s:"{hal_id}"' if hal_id else f'doiId_s:"{text.doi}"'
        data = client.get_json(
            "hal",
            "search/",
            {"q": q, "fl": ",".join(HAL_FIELDS), "rows": 1, "wt": "json"},
            kind="hal_record",
            sends=["identifier" if hal_id else "DOI"],
            validate=lambda d: d["response"]["docs"].__iter__(),
        ).data
        docs = [d for d in data["response"]["docs"] if isinstance(d, dict)]
        return docs[0] if docs else None

    def abstract(self, client: HttpClient, text: TextRef) -> Found | None:
        doc = self._doc(client, text)
        work = parse_hal_doc(doc) if doc else None
        if work is None or not work.abstracts:
            return None
        return Found(plain_abstracts(work.abstracts), read="api")

    def full_text(self, client: HttpClient, text: TextRef, work_dir: Path) -> Found | None:
        doc = self._doc(client, text)
        if not doc or not doc.get("fileMain_s"):
            return None
        url = check_link(str(doc["fileMain_s"]), local_ok=is_local(client, "hal"))
        data = client.get_bytes(
            "hal", url, kind="hal_file", sends=["identifier"], validate=check_pdf
        ).content
        return _pdf_found(data, work_dir, text)


class ScieloProvider(Provider):
    """SciELO: abstracts in every language of an article (by its PID), and its SciELO PS
    (JATS) full text, each translation's body a part in its language."""

    name = "scielo"
    service = "scielo"
    sends = ("identifier",)
    describes = "the SciELO identifier (PID) of an article"
    full_text_format = "jats"

    def for_abstract(self, text: TextRef) -> bool:
        return ":" in str(text.ids.get("scielo") or "")

    for_full_text = for_abstract

    @staticmethod
    def _params(text: TextRef) -> dict[str, str]:
        collection, _, code = str(text.ids["scielo"]).partition(":")
        return {"code": code, "collection": collection}

    def abstract(self, client: HttpClient, text: TextRef) -> Found | None:
        try:
            data = client.get_json(
                "scielo",
                "api/v1/article/",
                self._params(text),
                kind="scielo_article",
                sends=self.sends,
                validate=lambda d: d["article"].keys(),
            ).data
        except NotFound:
            return None
        work = parse_scielo_article(data)
        if work is None or not work.abstracts:
            return None
        return Found(plain_abstracts(work.abstracts), read="api")

    def full_text(self, client: HttpClient, text: TextRef, work_dir: Path) -> Found | None:
        try:
            data = client.get_bytes(
                "scielo",
                "api/v1/article/",
                {**self._params(text), "format": "xmlrsps"},
                kind="scielo_fulltext",
                sends=self.sends,
                validate=check_xml,
            ).content
        except NotFound:
            return None
        return Found(parts_from(read_jats(data), fmt="jats"), read="jats")


class OpenAlexProvider(Provider):
    """OpenAlex: a missing abstract from the work's inverted index, and the PDF of its
    open-access copy (``best_oa_location``, then the other locations), downloaded from the
    host that holds it."""

    name = "openalex"
    service = "openalex"
    sends = ("identifier", "DOI", "file address")
    describes = (
        "the OpenAlex identifier, else the DOI, of a text; then the address of its "
        "open-access copy, to the host that holds it"
    )
    full_text_format = "pdf"

    def for_abstract(self, text: TextRef) -> bool:
        return bool(text.ids.get("openalex") or text.doi)

    for_full_text = for_abstract

    def _work(self, client: HttpClient, text: TextRef) -> dict[str, Any] | None:
        oid = str(text.ids.get("openalex") or "").rsplit("/", 1)[-1]
        path, kind = (f"works/{oid}", "work") if oid else (f"works/doi:{text.doi}", "works_by_doi")
        try:
            return client.get_json(
                "openalex",
                path,
                kind=kind,
                sends=["identifier" if oid else "DOI"],
                validate=lambda d: d["id"],
            ).data
        except NotFound:
            return None

    def abstract(self, client: HttpClient, text: TextRef) -> Found | None:
        work = self._work(client, text)
        if not work or not work.get("abstract_inverted_index"):
            return None
        abstract = abstract_from_inverted_index(work["abstract_inverted_index"])
        return Found(plain_abstracts([(work.get("language"), abstract)]), read="api")

    def full_text(self, client: HttpClient, text: TextRef, work_dir: Path) -> Found | None:
        work = self._work(client, text)
        if not work:
            return None
        links: list[str] = []
        for loc in [work.get("best_oa_location"), *(work.get("locations") or [])]:
            if isinstance(loc, dict) and loc.get("pdf_url") and loc["pdf_url"] not in links:
                links.append(str(loc["pdf_url"]))
        last: Exception | None = None
        for link in links:
            try:
                url = check_link(link, local_ok=is_local(client, "files"))
                data = client.get_bytes(
                    "files", url, kind="oa_file", sends=["file address"], validate=check_pdf
                ).content
            except (UnsafeLink, ServiceError) as exc:
                last = exc
                continue
            return _pdf_found(data, work_dir, text)
        if last is not None:
            raise last
        return None
