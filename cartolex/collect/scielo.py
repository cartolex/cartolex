# SPDX-License-Identifier: MIT
"""The SciELO finder: articles of SciELO journals, with their abstracts in every language.

**Access route.** SciELO documents two machine routes: the ArticleMeta API
and an OAI-PMH server. cartolex uses **ArticleMeta**: one request gives an
article with its titles and abstracts in every language it has (``v12``,
``v83``, each with its language), its authors with their ORCID when the
journal gave it (``v10``), its DOI and publication date, as JSON; the same
endpoint gives the SciELO PS (JATS) full text on request, and its identifiers
listing pages a collection or a journal by processing date, 1,000 at a time.
OAI-PMH's Dublin Core has no author identifiers and tags languages
unevenly, and would need a second route for the full text. Neither route
searches by author: the finder lists the journals it is given (ISSNs) or a
whole collection, reads each article of the window, and keeps those that have
a person's ORCID; an author who only matches by **name** is **proposed**
(a ``scielo_candidates`` run), never accepted.

Author keywords (``v85``) are never read: the lexicon is emergent.

What SciELO returned is stored as received in ``sources/<slot>/raw/scielo/``;
:func:`read_scielo_runs` builds the table rows from it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cartolex.project.layout import ProjectLayout

from .finders import (
    FinderReport,
    PersonRef,
    Window,
    add_parts,
    check_window,
    name_matches,
    normalise_doi,
)
from .http import Cancelled, CursorPaging, HttpClient, NotFound, ServiceError, ServiceUnavailable
from .tables import RawRun, RawWriter, SourceBuilder, iso, parse_time

__all__ = [
    "SCIELO_DOC_TYPES",
    "ScieloWork",
    "collect_scielo",
    "parse_scielo_article",
    "read_scielo_runs",
]

#: ArticleMeta document types → cartolex's; others (corrections, retractions…) are skipped.
SCIELO_DOC_TYPES: Mapping[str, str] = {
    "research-article": "article",
    "review-article": "article",
    "rapid-communication": "article",
    "brief-report": "article",
    "case-report": "article",
    "article-commentary": "other",
    "editorial": "other",
    "letter": "other",
    "book-review": "other",
    "discussion": "other",
}
PAGE_LIMIT = 1000
#: Without journals, a collection is listed whole: never more articles than this.
MAX_ARTICLES = 5000
MAX_FAILURES_IN_A_ROW = 3


def _offset_paging() -> CursorPaging:
    return CursorPaging(
        items=lambda d: d["objects"],
        next_cursor=lambda d: str(int(d["meta"]["offset"]) + len(d["objects"])),
        total=lambda d: None,
        cursor_param="offset",
        first="0",
        confirm_empty=True,
    )


def _validate_listing(data: Any) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("objects"), list):
        raise ValueError("not an ArticleMeta listing")
    int(data["meta"]["offset"])


def _validate_article(data: Any) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("article"), dict):
        raise ValueError("not an ArticleMeta article")


def _values(field: Any) -> list[dict[str, Any]]:
    return [x for x in field or [] if isinstance(x, dict)]


@dataclass(frozen=True)
class ScieloWork:
    """An article, read: what a text is built from."""

    collection: str
    code: str
    doc_type: str
    year: int | None
    date: str | None
    doi: str | None
    title: str
    language: str | None
    titles: list[tuple[str | None, str]]
    abstracts: list[tuple[str | None, str]]
    authors: tuple[tuple[str, str, str | None], ...]  # given, surname, ORCID

    @property
    def key(self) -> str:
        return f"scielo:{self.collection}:{self.code}"


def parse_scielo_article(data: Mapping[str, Any]) -> ScieloWork | None:
    """An ArticleMeta article → :class:`ScieloWork`; ``None`` for a type that is not a text.

    Raises ``ValueError`` when the record lacks its PID or any title.
    """
    art = data.get("article") or {}
    code = data.get("code") or next((x.get("_") for x in _values(art.get("v2"))), None)
    titles = [(x.get("l"), str(x.get("_") or "")) for x in _values(art.get("v12")) if x.get("_")]
    if not code or not titles:
        raise ValueError("an article without its PID or a title")
    doc_type = SCIELO_DOC_TYPES.get(str(data.get("document_type") or "research-article"))
    if doc_type is None:
        return None
    language = next((str(x["_"]) for x in _values(art.get("v40")) if x.get("_")), None)
    abstracts = [
        (x.get("l"), str(x.get("a") or x.get("_") or ""))
        for x in _values(art.get("v83"))
        if x.get("a") or x.get("_")
    ]
    authors = tuple(
        (str(x.get("n") or ""), str(x.get("s") or ""), str(x["k"]) if x.get("k") else None)
        for x in _values(art.get("v10"))
    )
    year_text = str(data.get("publication_year") or "")[:4]
    date = data.get("publication_date")
    title = next((t for lang, t in titles if lang == language), titles[0][1])
    doi = data.get("doi") or next((x.get("_") for x in _values(art.get("v237"))), None)
    return ScieloWork(
        collection=str(data.get("collection") or ""),
        code=str(code),
        doc_type=doc_type,
        year=int(year_text) if year_text.isdigit() else None,
        date=str(date)[:10] if date else None,
        doi=normalise_doi(doi),
        title=title,
        language=language,
        titles=titles,
        abstracts=abstracts,
        authors=authors,
    )


def _bare_orcid(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).strip().rsplit("/", 1)[-1].upper() or None


def collect_scielo(
    client: HttpClient,
    layout: ProjectLayout,
    slot: str,
    people: Sequence[PersonRef],
    *,
    window: Window,
    collection: str,
    issns: Sequence[str] = (),
    codes: Sequence[str] = (),
    max_articles: int = MAX_ARTICLES,
    now: Callable[[], datetime] | None = None,
) -> FinderReport:
    """Collect the SciELO articles of *people* in *window* into *slot*'s raw folder.

    The articles read are *codes* (PIDs) when given, else those listed for each
    journal of *issns* in *collection* (or the whole collection, at most
    *max_articles*). An article is kept for every person whose ORCID it shows;
    authors who only match a person's name are proposed. Progress, cancel and
    the egress record go through *client*.
    """
    window = check_window(window)
    clock = now or (lambda: datetime.now(timezone.utc))
    report = FinderReport("scielo", window, people=len(people))
    by_orcid = {p.orcid.upper(): p for p in people if p.orcid}
    header = {
        "service": "scielo",
        "window": list(window),
        "collection": collection,
        "issns": list(issns),
        "people": len(people),
    }
    targets = list(dict.fromkeys(codes)) or _list_codes(
        client, collection, issns, window, max_articles
    )
    proposals: list[dict[str, Any]] = []
    failures_in_a_row = 0
    with RawWriter(layout, slot, "scielo", header) as run:
        for n, code in enumerate(targets):
            client.progress(n / max(1, len(targets)), f"SciELO: article {n + 1} of {len(targets)}")
            if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                report.stopped = "SciELO stopped answering; the other articles were not read"
                report.skip("not read", len(targets) - n)
                break
            try:
                data = client.get_json(
                    "scielo",
                    "api/v1/article/",
                    {"code": code, "collection": collection},
                    kind="scielo_article",
                    sends=["identifier"],
                    validate=_validate_article,
                ).data
                failures_in_a_row = 0
            except NotFound:
                report.skip("unknown article")
                continue
            except Cancelled:
                raise
            except ServiceError as exc:
                if isinstance(exc, ServiceUnavailable) and (exc.retry_after or 0) > (
                    client.settings.retry.max_retry_after
                ):
                    raise
                failures_in_a_row += 1
                report.failures.append({"article": code, "error": str(exc)})
                continue
            try:
                work = parse_scielo_article(data)
            except ValueError:
                report.skip("malformed record")
                continue
            if work is None:
                report.skip("type without text")
                continue
            if work.year is None or not window[0] <= work.year <= window[1]:
                report.skip("outside the window")
                continue
            matched = []
            for rank, (given, surname, orcid) in enumerate(work.authors, start=1):
                person = by_orcid.get(_bare_orcid(orcid) or "")
                if person is not None:
                    matched.append({"person_id": person.person_id, "rank": rank, "how": "orcid"})
                    continue
                for p in people:
                    if name_matches(p, given, surname):
                        candidate = {
                            "type": "candidate",
                            "person_id": p.person_id,
                            "article": work.key,
                            "name": f"{given} {surname}".strip(),
                            "rank": rank,
                            "title": work.title,
                            "year": work.year,
                        }
                        proposals.append(candidate)
                        report.candidates.append(candidate)
            if not matched:
                report.skip("nobody's ORCID")
                continue
            run.add(
                {
                    "type": "article",
                    "collection": collection,
                    "code": code,
                    "matched": matched,
                    "retrieved_at": iso(clock()),
                    "article": data,
                }
            )
            report.works += 1
            for m in matched:
                report.found[m["person_id"]] = report.found.get(m["person_id"], 0) + 1
    report.runs.append(run.path)
    if proposals:
        with RawWriter(layout, slot, "scielo_candidates", header) as cands:
            for candidate in proposals:
                cands.add(candidate)
        report.runs.append(cands.path)
    return report


def _list_codes(
    client: HttpClient,
    collection: str,
    issns: Sequence[str],
    window: Window,
    max_articles: int,
) -> list[str]:
    """The PIDs listed for each journal (or the collection), processed since the window's
    first year minus one (an article is processed when it is published, or later)."""
    codes: list[str] = []
    for issn in list(issns) or [None]:
        params = {
            "collection": collection,
            "issn": issn,
            "from": f"{window[0] - 1}-01-01",
            "limit": PAGE_LIMIT,
        }
        items = client.get_all(
            "scielo",
            "api/v1/article/identifiers/",
            params,
            kind="scielo_identifiers",
            sends=["identifier"],
            paging=_offset_paging(),
            validate=_validate_listing,
        ).data
        for item in items:
            if isinstance(item, dict) and item.get("code") and item["code"] not in codes:
                codes.append(str(item["code"]))
        if not issns and len(codes) > max_articles:
            raise ValueError(
                f"the collection {collection!r} lists {len(codes)} articles since {window[0] - 1}; "
                "name the journals (ISSNs) to read, or raise max_articles"
            )
    return codes


def read_scielo_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """Rows from the SciELO runs of one slot: a text per article with its titles and
    abstracts in every language, and the authorships of the people it was kept for."""
    for run in runs:
        for rec in run.records():
            if rec.get("type") != "article":
                continue
            at = parse_time(rec["retrieved_at"])
            try:
                work = parse_scielo_article(rec["article"])
            except ValueError:
                builder.count("scielo: malformed record")
                continue
            if work is None:
                continue
            collection = work.collection or str(rec.get("collection") or "")
            key = f"scielo:{collection}:{work.code}"
            tid = builder.text(
                slot=run.slot,
                keys=[key],
                title=work.title,
                doc_type=work.doc_type,
                source="scielo",
                retrieved_at=at,
                year=work.year,
                date=work.date,
                doi=work.doi,
                ids={"scielo": f"{collection}:{work.code}"},
                n_authors=len(work.authors),
            )
            add_parts(builder, tid, "title", work.titles, provider="scielo", retrieved_at=at)
            add_parts(builder, tid, "abstract", work.abstracts, provider="scielo", retrieved_at=at)
            for m in rec.get("matched", []):
                pid = m.get("person_id")
                if pid and builder.known_person(pid):
                    builder.authorship(
                        tid, pid, position=int(m["rank"]), last=int(m["rank"]) == len(work.authors)
                    )
                    builder.count("scielo: authorships")
