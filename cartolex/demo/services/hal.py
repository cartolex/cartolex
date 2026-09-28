# SPDX-License-Identifier: MIT
"""The subset of the HAL API cartolex uses, served from the demo's sources layer.

Routes (under the service's prefix):

* ``search/`` — the Solr search API: ``q`` one clause ``field:value`` or
  ``field:(a OR b)`` (``*:*`` for everything) on ``authIdHal_s``,
  ``authFullName_t`` (case and accents ignored), ``halId_s``, ``doiId_s``,
  ``arxivId_s`` or ``docid``; one ``fq`` range ``producedDateY_i:[a TO b]``;
  ``fl`` (a comma-separated list, ``*`` for every field); ``rows`` (at most
  10,000); ``cursorMark`` with ``sort=docid asc`` (refused otherwise, as Solr
  does): ``nextCursorMark`` equals the cursor sent once the list is exhausted;
* ``ref/structure/`` — the structure referential: ``q=docid:(a OR b)``, with
  ``parentDocid_i`` for each structure's parents;
* ``files/<halId>/document`` — a deposit's PDF.

Answers are ``{"response": {"numFound", "start", "docs"}, "nextCursorMark"}``.
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from typing import Any

from .biblio import Bibliography
from .http import DEMO_BASE, Reply, Request, json_reply
from .render import render_pdf
from .sources import HalDeposit, HalStructure, sources_layer

__all__ = ["HalService"]

MAX_ROWS = 10_000
_CLAUSE = re.compile(r"^\s*([\w.]+)\s*:\s*(.+?)\s*$", re.S)
_RANGE = re.compile(r"^\s*(\w+)\s*:\s*\[\s*(\d+|\*)\s+TO\s+(\d+|\*)\s*\]\s*$")
_FACET = "_FacetSep_"
_JOIN = "_JoinSep_"


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _values(text: str) -> list[str]:
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        parts = re.split(r"\s+OR\s+", text[1:-1].strip())
    else:
        parts = [text]
    return [p.strip().strip('"') for p in parts if p.strip()]


class _BadQuery(ValueError):
    pass


class HalService:
    """The HAL routes of the demo services."""

    name = "hal"

    def __init__(self, bib: Bibliography) -> None:
        self.layer = sources_layer(bib)
        self._docs = {d.hal_id: self._doc(d) for d in self.layer.deposits.values()}
        self._by_docid = sorted(self.layer.deposits.values(), key=lambda d: d.docid)

    # ── documents ──
    def _doc(self, d: HalDeposit) -> dict[str, Any]:
        structures = self.layer.structures
        doc: dict[str, Any] = {
            "docid": d.docid,
            "halId_s": d.hal_id,
            "uri_s": f"https://hal.science/{d.hal_id}",
            "docType_s": d.doc_type,
            "language_s": [d.language],
            "title_s": list(d.titles.values()),
            "abstract_s": list(d.abstracts.values()),
            "producedDate_s": d.date,
            "producedDateY_i": d.year,
            "publicationDateY_i": d.year,
            "authFullName_s": [a.full_name for a in d.authors],
            "authFirstName_s": [a.first for a in d.authors],
            "authLastName_s": [a.last for a in d.authors],
            "authIdHal_s": [a.idhal for a in d.authors if a.idhal],
            "authIdHalFullName_fs": [
                f"{a.idhal}{_FACET}{a.full_name}" for a in d.authors if a.idhal
            ],
            "authORCIDIdExt_s": [a.orcid for a in d.authors if a.orcid],
            "authIdHasStructure_fs": [
                f"{i}{_FACET}{a.full_name}{_JOIN}{s}{_FACET}{structures[s].name}"
                for i, a in enumerate(d.authors, start=1)
                for s in a.structures
            ],
            "structId_i": sorted({s for a in d.authors for s in a.structures}),
            "openAccess_bool": d.file,
            "submitType_s": "file" if d.file else "notice",
            "keyword_s": ["demo"],
        }
        for lang, title in d.titles.items():
            doc[f"{lang}_title_s"] = [title]
        for lang, abstract in d.abstracts.items():
            doc[f"{lang}_abstract_s"] = [abstract]
        if d.doi:
            doc["doiId_s"] = d.doi
        if d.arxiv:
            doc["arxivId_s"] = d.arxiv
        if d.file:
            doc["fileMain_s"] = f"{DEMO_BASE}hal/files/{d.hal_id}/document"
            doc["files_s"] = [doc["fileMain_s"]]
        return doc

    @staticmethod
    def _structure(s: HalStructure) -> dict[str, Any]:
        out: dict[str, Any] = {
            "docid": s.docid,
            "label_s": s.name,
            "name_s": s.name,
            "type_s": s.type,
            "valid_s": "VALID",
        }
        if s.acronym:
            out["acronym_s"] = s.acronym
        if s.parents:
            out["parentDocid_i"] = list(s.parents)
        if s.country:
            out["country_s"] = s.country
        return out

    # ── requests ──
    def handle(self, request: Request) -> Reply:
        path = request.path.strip("/")
        try:
            if path == "search":
                return self._search(request.query)
            if path == "ref/structure":
                return self._structures(request.query)
            m = re.fullmatch(r"files/([\w.-]+)/document", path)
            if m:
                return self._file(m.group(1))
        except _BadQuery as exc:
            return json_reply(400, {"error": {"msg": str(exc), "code": 400}})
        return json_reply(404, {"error": {"msg": f"no route {path}", "code": 404}})

    def _file(self, hal_id: str) -> Reply:
        d = self.layer.deposits.get(hal_id)
        if d is None or not d.file or d.world_work is None:
            return json_reply(404, {"error": {"msg": "no such file", "code": 404}})
        work = self.layer.work(d.world_work)
        blocks = [d.titles[d.language], d.abstracts[d.language]]
        blocks += [b for b in work.body.split("\n\n") if b.strip()]
        return Reply(200, render_pdf(blocks), "application/pdf")

    def _match(self, q: str) -> list[HalDeposit]:
        q = q.strip()
        if q in ("*:*", "*"):
            return list(self._by_docid)
        m = _CLAUSE.match(q)
        if not m:
            raise _BadQuery(f"cannot read the query {q!r}")
        field, values = m.group(1), _values(m.group(2))
        if field == "authIdHal_s":
            wanted = set(values)
            return [d for d in self._by_docid if any(a.idhal in wanted for a in d.authors)]
        if field == "authFullName_t":
            wanted = {" ".join(_fold(v).split()) for v in values}
            return [
                d
                for d in self._by_docid
                if any(" ".join(_fold(a.full_name).split()) in wanted for a in d.authors)
            ]
        if field == "halId_s":
            return [d for d in self._by_docid if d.hal_id in set(values)]
        if field == "doiId_s":
            wanted = {v.lower() for v in values}
            return [d for d in self._by_docid if d.doi and d.doi.lower() in wanted]
        if field == "arxivId_s":
            return [d for d in self._by_docid if d.arxiv in set(values)]
        if field == "docid":
            return [d for d in self._by_docid if str(d.docid) in set(values)]
        raise _BadQuery(f"undefined field {field}")

    def _search(self, query: dict[str, str]) -> Reply:
        if query.get("wt", "json") != "json":
            raise _BadQuery("the demo answers JSON only")
        rows_text = query.get("rows", "30")
        try:
            rows = int(rows_text)
        except ValueError:
            raise _BadQuery(f"rows must be a number, not {rows_text!r}") from None
        if not 0 <= rows <= MAX_ROWS:
            raise _BadQuery(f"rows must be at most {MAX_ROWS}")
        docs = self._match(query.get("q", "*:*"))
        if query.get("fq"):
            m = _RANGE.match(query["fq"])
            if not m or m.group(1) != "producedDateY_i":
                raise _BadQuery(f"the demo filters on producedDateY_i ranges only: {query['fq']!r}")
            lo = -1 if m.group(2) == "*" else int(m.group(2))
            hi = 10**6 if m.group(3) == "*" else int(m.group(3))
            docs = [d for d in docs if lo <= d.year <= hi]
        total = len(docs)
        body: dict[str, Any] = {}
        if "cursorMark" in query:
            if query.get("sort", "").strip() != "docid asc":
                raise _BadQuery("Cursor functionality requires a sort containing a uniqueKey field")
            mark = query["cursorMark"]
            after = -1
            if mark != "*":
                try:
                    after = int(base64.urlsafe_b64decode(mark.encode()).decode().split(":")[1])
                except (ValueError, IndexError, UnicodeDecodeError):
                    raise _BadQuery(f"Unable to parse 'cursorMark': {mark}") from None
            page = [d for d in docs if d.docid > after][:rows]
            nxt = (
                base64.urlsafe_b64encode(f"docid:{page[-1].docid}".encode()).decode()
                if page
                else mark
            )
            body["nextCursorMark"] = nxt
        else:
            start = int(query.get("start", "0"))
            page = docs[start : start + rows]
        fields = [f.strip() for f in query.get("fl", "*").split(",") if f.strip()]
        out = []
        for d in page:
            doc = self._docs[d.hal_id]
            if "*" in fields:
                out.append(dict(doc))
            else:
                out.append({k: doc[k] for k in fields if k in doc})
        body["response"] = {"numFound": total, "start": 0, "docs": out}
        return json_reply(200, body)

    def _structures(self, query: dict[str, str]) -> Reply:
        m = _CLAUSE.match(query.get("q", ""))
        if not m or m.group(1) != "docid":
            raise _BadQuery("the demo referential answers q=docid:(…) only")
        wanted = {int(v) for v in _values(m.group(2)) if v.isdigit()}
        found = [
            self._structure(s) for k, s in sorted(self.layer.structures.items()) if k in wanted
        ]
        return json_reply(200, {"response": {"numFound": len(found), "start": 0, "docs": found}})

    @staticmethod
    def fault_page(kind: str, reply: Reply) -> Reply:
        """A page cut in half, or a list that ends too early (an empty page)."""
        data = json.loads(reply.body)
        docs = data.get("response", {}).get("docs")
        if not isinstance(docs, list):
            return reply
        data["response"]["docs"] = docs[: len(docs) // 2] if kind == "cut_page" else []
        return json_reply(200, data)
