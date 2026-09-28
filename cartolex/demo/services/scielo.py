# SPDX-License-Identifier: MIT
"""The subset of SciELO's ArticleMeta API cartolex uses, served from the demo's sources layer.

Routes (under the service's prefix):

* ``api/v1/article/identifiers/`` — ``collection``, ``issn``, ``from`` and
  ``until`` (processing dates, ISO), ``offset`` and ``limit`` (at most 1,000):
  ``{"meta": {"filter", "limit", "offset"}, "objects": [{"code", "collection",
  "doi", "processing_date"}]}``; an empty ``objects`` list ends the listing;
* ``api/v1/article/`` — ``code`` (the PID, required) and ``collection``: the
  article as ArticleMeta's JSON (the ISIS fields: ``v12`` titles, ``v83``
  abstracts, ``v10`` authors with ``k`` their ORCID, ``v70`` affiliations,
  ``v85`` author keywords…), or with ``format=xmlrsps`` its SciELO PS XML
  (JATS, one ``sub-article`` per translation, with the full text when the
  world has bodies). An unknown PID answers 404.
"""

from __future__ import annotations

import json
from typing import Any

from ..vocabulary import THEME_BY_ID
from .biblio import Bibliography
from .http import DEMO_BASE, Reply, Request, json_reply
from .render import Document, render_jats
from .sources import ScieloArticle, sources_layer

__all__ = ["ScieloService"]

MAX_LIMIT = 1000


class _BadQuery(ValueError):
    pass


def _term(term: Any, language: str) -> str:
    return {"en": term.en, "fr": term.fr, "pt": term.pt or term.en}.get(language, term.en)


class ScieloService:
    """The ArticleMeta routes of the demo services."""

    name = "scielo"

    def __init__(self, bib: Bibliography) -> None:
        self.layer = sources_layer(bib)
        self._articles = dict(sorted(self.layer.scielo.items()))

    def _json(self, a: ScieloArticle) -> dict[str, Any]:
        work = self.layer.work(a.world_work)
        theme = THEME_BY_ID[work.themes[0]]
        keywords = [
            {"l": lang, "k": _term(t, lang), "_": ""}
            for lang in a.abstracts
            for t in theme.terms[:3]
        ]
        compact = a.date.replace("-", "")
        article: dict[str, Any] = {
            "v2": [{"_": a.pid}],
            "v10": [
                {
                    "n": given,
                    "s": surname,
                    "1": f"aff{aff}",
                    "_": "",
                    **({"k": orcid} if orcid else {}),
                }
                for given, surname, orcid, aff in a.authors
            ],
            "v12": [{"l": lang, "_": title} for lang, title in a.titles.items()],
            "v40": [{"_": a.language}],
            "v65": [{"_": compact}],
            "v70": [
                {"i": f"aff{i}", "_": label, "p": ""} for i, label in enumerate(a.affiliations, 1)
            ],
            "v71": [{"_": "oa"}],
            "v83": [{"l": lang, "a": text, "_": ""} for lang, text in a.abstracts.items()],
            "v85": keywords,
        }
        if a.doi:
            article["v237"] = [{"_": a.doi}]
        return {
            "code": a.pid,
            "collection": a.collection,
            "doi": a.doi,
            "publication_year": str(a.year),
            "publication_date": a.date,
            "processing_date": a.date,
            "document_type": "research-article",
            "fulltexts": {
                "html": {lang: f"{DEMO_BASE}scielo/html/{a.pid}/{lang}" for lang in a.abstracts},
            },
            "article": article,
            "title": {"v100": [{"_": a.journal}], "v400": [{"_": a.issn}]},
            "citations": [],
        }

    def _xml(self, a: ScieloArticle) -> bytes:
        doc = Document(
            language=a.language,
            titles=a.titles,
            abstracts=a.abstracts,
            body=a.bodies.get(a.language, ""),
            authors=tuple((given, surname) for given, surname, _orcid, _aff in a.authors),
            doi=a.doi,
            year=a.year,
            translations={k: v for k, v in a.bodies.items() if k != a.language},
            ids={"publisher-id": a.pid},
        )
        return render_jats(doc, journal=a.journal, sub_articles=True)

    def handle(self, request: Request) -> Reply:
        path = request.path.strip("/")
        try:
            if path == "api/v1/article/identifiers":
                return self._identifiers(request.query)
            if path == "api/v1/article":
                return self._article(request.query)
        except _BadQuery as exc:
            return json_reply(400, {"error": str(exc)})
        return json_reply(404, {"error": f"no route {path}"})

    def _identifiers(self, query: dict[str, str]) -> Reply:
        try:
            limit = int(query.get("limit", "100"))
            offset = int(query.get("offset", "0"))
        except ValueError:
            raise _BadQuery("limit and offset are numbers") from None
        if not 1 <= limit <= MAX_LIMIT or offset < 0:
            raise _BadQuery(f"limit must be between 1 and {MAX_LIMIT}")
        start, end = query.get("from", "1500-01-01"), query.get("until", "9999-12-31")
        rows = [
            a
            for a in self._articles.values()
            if (not query.get("collection") or a.collection == query["collection"])
            and (not query.get("issn") or a.issn == query["issn"])
            and start <= a.date <= end
        ]
        objects = [
            {"code": a.pid, "collection": a.collection, "doi": a.doi, "processing_date": a.date}
            for a in rows[offset : offset + limit]
        ]
        meta = {
            "filter": {"processing_date": {"$gte": start, "$lte": end}},
            "limit": limit,
            "offset": offset,
        }
        return json_reply(200, {"meta": meta, "objects": objects})

    def _article(self, query: dict[str, str]) -> Reply:
        code = query.get("code")
        if not code:
            raise _BadQuery("code is required")
        a = self._articles.get(code)
        if a is None or (query.get("collection") and query["collection"] != a.collection):
            return json_reply(404, {"error": "article not found"})
        if query.get("format", "json") == "xmlrsps":
            return Reply(200, self._xml(a), "application/xml; charset=utf-8")
        return json_reply(200, self._json(a))

    @staticmethod
    def fault_page(kind: str, reply: Reply) -> Reply:
        """A listing page cut in half, or one that comes back empty too early."""
        data = json.loads(reply.body)
        objects = data.get("objects")
        if not isinstance(objects, list):
            return reply
        data["objects"] = objects[: len(objects) // 2] if kind == "cut_page" else []
        return json_reply(200, data)
