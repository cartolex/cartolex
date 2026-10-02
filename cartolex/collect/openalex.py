# SPDX-License-Identifier: MIT
"""The OpenAlex requests cartolex makes, and how their answers are read.

Every request goes through the job's :class:`~cartolex.collect.http.HttpClient`,
with its kind (for the cache lifetime) and the kinds of data it sends:

============================================= =========================== ==============
request                                       kind                        sends
============================================= =========================== ==============
``authors?search=``                           ``person_search``           a name
``authors?filter=orcid:``                     ``authors_by_orcid``        an identifier
``authors/<id>``                              ``author``                  an identifier
``institutions?search=``                      ``institution_search``      an institution name
``institutions/<id>``, ``institutions/ror:``  ``institution``             an identifier
``institutions?filter=lineage:``              ``institution_units``       identifiers
``works?filter=author.id:``                   ``works_by_author``         identifiers
``works?filter=authorships.institutions.``    ``works_by_institution``    identifiers
``lineage:`` (page by page, three fields)
``works?filter=doi:``                         ``works_by_doi``            DOIs, 50 at a time
============================================= =========================== ==============

Lists use cursor paging at the documented maximum of 100 per page. The works of
an institution, which can number in the millions, are read page by page
(:func:`institution_work_pages`) with only the fields the proposal reads
(:data:`INSTITUTION_WORK_FIELDS`), so that nothing grows with their number.
:class:`OpenAlexApi` gathers these requests behind the methods every finder
uses, so that the snapshot (:class:`cartolex.collect.snapshot.SnapshotSource`)
can stand in for the API.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import replace
from typing import Any, Protocol

from .http import CursorPaging, Fetched, HttpClient, NotFound, Page

__all__ = [
    "AUTHORS_SHOWN",
    "AUTHOR_BATCH",
    "DOI_BATCH",
    "INSTITUTION_WORK_FIELDS",
    "PAGING",
    "PER_PAGE",
    "WORK_FIELDS",
    "OpenAlexApi",
    "OpenAlexSource",
    "author",
    "author_batches",
    "complete_authors",
    "institution",
    "institution_units",
    "institution_work_pages",
    "parse_institution_ref",
    "authors_by_orcid",
    "bare_doi",
    "doc_type",
    "record_dois",
    "search_authors",
    "search_institutions",
    "short_id",
    "work",
    "works_by_authors",
    "works_by_dois",
    "works_by_institutions",
    "works_of_authors",
]

SERVICE = "openalex"
#: The documented maximum of results per page.
PER_PAGE = 100
#: DOIs asked for in one request (the documented limit of one filter is 100 values).
DOI_BATCH = 50
#: Author records asked for in one request, when many people's works are needed at once.
AUTHOR_BATCH = 50
#: The most authors a work names in a list answer: OpenAlex cuts the list there, and the
#: work's own record (free of charge) names them all.
AUTHORS_SHOWN = 100
#: The fields of a work asked for (``select``) by the harvest and the collaborators' rounds:
#: what the tables and the rounds read (the records digested in :mod:`cartolex.collect.digests`),
#: and a few small ones kept for later: the other identifiers (PMID, PMCID), retraction and
#: paratext, the bibliographic details, where an open copy is, the references, the index's
#: own topic (kept to compare with, never read to build anything) and the last update. A
#: record holds half of what a whole one does; the classifications, locations, funding and
#: citation metrics are left out.
WORK_FIELDS = ",".join(
    (
        "id",
        "doi",
        "title",
        "display_name",
        "publication_year",
        "publication_date",
        "type",
        "language",
        "primary_location",
        "authorships",
        "abstract_inverted_index",
        "ids",
        "is_retracted",
        "is_paratext",
        "biblio",
        "open_access",
        "best_oa_location",
        "referenced_works",
        "primary_topic",
        "updated_date",
    )
)
#: The fields of a work an institution's proposal reads (``select``: a quarter of a full
#: record's size or less).
INSTITUTION_WORK_FIELDS = "id,publication_year,authorships"
_ID = re.compile(r"([AWIST]\d+)$", re.IGNORECASE)
_INSTITUTION = re.compile(r"(?:^|/|\b)(I\d{2,})\b", re.IGNORECASE)
#: A ROR id: ``0``, six characters of Crockford's base 32, two check digits.
_ROR = re.compile(r"(?:ror\.org/|ror:|^)\s*(0[0-9a-hjkmnp-tv-z]{6}\d{2})\b", re.IGNORECASE)


def _check_list(data: Any) -> None:
    if not isinstance(data, dict):
        raise ValueError("a list answer is an object")
    if not isinstance(data.get("results"), list):
        raise ValueError("a list answer has a list of results")
    meta = data.get("meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("count"), int):
        raise ValueError("a list answer has a count")


def _check_entity(data: Any) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise ValueError("an entity has an id")


PAGING = CursorPaging(
    items=lambda d: d["results"],
    next_cursor=lambda d: d["meta"].get("next_cursor"),
    total=lambda d: d["meta"]["count"],
)


def short_id(value: str | None) -> str | None:
    """``https://openalex.org/A123`` → ``A123`` (``None`` stays ``None``)."""
    if not value:
        return None
    m = _ID.search(value.strip())
    return m.group(1).upper() if m else None


def bare_doi(value: str | None) -> str | None:
    """A DOI in lower case without its resolver prefix."""
    if not value:
        return None
    value = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value or None


def doc_type(work: dict[str, Any]) -> str:
    """The text's document type from a work's type and where it was published."""
    typ = (work.get("type") or "article").lower()
    source = ((work.get("primary_location") or {}).get("source") or {}).get("type")
    if typ == "article" and source == "conference":
        return "communication"
    return {
        "article": "article",
        "review": "article",
        "letter": "article",
        "preprint": "preprint",
        "posted-content": "preprint",
        "report": "report",
        "dissertation": "thesis",
        "book": "book",
        "book-chapter": "chapter",
        "proceedings-article": "communication",
    }.get(typ, typ)


def search_authors(
    client: HttpClient, text: str, *, institution_ids: Sequence[str] = (), per_page: int = 25
) -> list[dict[str, Any]]:
    """Author records whose names match *text* (restricted to institutions when given)."""
    params: dict[str, Any] = {"search": text, "per_page": per_page}
    if institution_ids:
        params["filter"] = "affiliations.institution.id:" + "|".join(institution_ids)
    fetched = client.get_json(
        SERVICE, "authors", params, kind="person_search", sends=["name"], validate=_check_list
    )
    return list(fetched.data["results"])


def authors_by_orcid(client: HttpClient, orcid: str) -> list[dict[str, Any]]:
    """Author records that carry this ORCID."""
    fetched = client.get_json(
        SERVICE,
        "authors",
        {"filter": f"orcid:{orcid}", "per_page": 25},
        kind="authors_by_orcid",
        sends=["identifier"],
        validate=_check_list,
    )
    return list(fetched.data["results"])


def author(client: HttpClient, author_id: str) -> Fetched | None:
    """One author record (free of charge), or ``None`` when there is no such record."""
    try:
        return client.get_json(
            SERVICE,
            f"authors/{author_id}",
            kind="author",
            sends=["identifier"],
            validate=_check_entity,
        )
    except NotFound:
        return None


def work(client: HttpClient, work_id: str) -> Fetched | None:
    """One work's own record (free of charge), every author named; ``None`` when it is gone."""
    try:
        return client.get_json(
            SERVICE,
            f"works/{work_id}",
            {"select": WORK_FIELDS},
            kind="work",
            sends=["identifier"],
            validate=_check_entity,
        )
    except NotFound:
        return None


def _authors_cut(record: dict[str, Any]) -> bool:
    return bool(record.get("is_authors_truncated")) or (
        len(record.get("authorships") or []) >= AUTHORS_SHOWN
    )


def complete_authors(client: HttpClient, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The works of a list answer, each whose authors the list cut replaced by its own record.

    A list names a work's first :data:`AUTHORS_SHOWN` authors only: without the others,
    the people further down the list would not be found on the work.
    """
    out = list(records)
    for i, record in enumerate(out):
        wid = short_id(record.get("id")) if _authors_cut(record) else None
        if wid:
            found = work(client, wid)
            if found is not None:
                out[i] = found.data
    return out


def parse_institution_ref(text: str) -> str | None:
    """``I…`` (an OpenAlex institution) or ``ror:0…`` (a ROR id) from an id or a URL holding one."""
    text = (text or "").strip()
    ror = _ROR.search(text)
    if ror and ("ror" in text.lower() or len(text) == 9):
        return f"ror:{ror.group(1).lower()}"
    found = _INSTITUTION.search(text)
    return found.group(1).upper() if found else None


def institution(client: HttpClient, ref: str) -> Fetched | None:
    """One institution record, by its OpenAlex id or ``ror:<id>``; ``None`` when there is none."""
    try:
        return client.get_json(
            SERVICE,
            f"institutions/{ref}",
            kind="institution",
            sends=["identifier"],
            validate=_check_entity,
        )
    except NotFound:
        return None


def institution_units(client: HttpClient, roots: Sequence[str]) -> Fetched:
    """The institutions *roots* and every unit below them (their ``lineage`` holds a root)."""
    ids = sorted({r for r in roots if r})
    if not ids:
        raise ValueError("no institution to ask for")
    return client.get_all(
        SERVICE,
        "institutions",
        {"filter": "lineage:" + "|".join(ids), "per_page": PER_PAGE},
        kind="institution_units",
        sends=["identifier"],
        paging=PAGING,
        validate=_check_list,
    )


def works_by_institutions(
    client: HttpClient, roots: Sequence[str], *, years: tuple[int, int] | None = None
) -> Fetched:
    """Every work an author signed at one of *roots* or a unit below it, within *years*."""
    ids = sorted({r for r in roots if r})
    if not ids:
        raise ValueError("no institution to ask for")
    filters = ["authorships.institutions.lineage:" + "|".join(ids), *_window(years)]
    return client.get_all(
        SERVICE,
        "works",
        {"filter": ",".join(filters), "per_page": PER_PAGE},
        kind="works_by_institution",
        sends=["identifier"],
        paging=PAGING,
        validate=_check_list,
    )


def institution_work_pages(
    client: HttpClient,
    roots: Sequence[str],
    *,
    years: tuple[int, int] | None = None,
    cursor: str | None = None,
    read: int = 0,
) -> Iterator[Page]:
    """The works signed at *roots* or below, within *years*, one page at a time (from *cursor*
    when the list is resumed, *read* works later), with :data:`INSTITUTION_WORK_FIELDS` only."""
    ids = sorted({r for r in roots if r})
    if not ids:
        raise ValueError("no institution to ask for")
    filters = ["authorships.institutions.lineage:" + "|".join(ids), *_window(years)]
    return client.pages(
        SERVICE,
        "works",
        {"filter": ",".join(filters), "select": INSTITUTION_WORK_FIELDS, "per_page": PER_PAGE},
        kind="works_by_institution",
        sends=["identifier"],
        paging=PAGING,
        validate=_check_list,
        cursor=cursor,
        read=read,
        per_page=PER_PAGE,
    )


def works_of_authors(
    client: HttpClient, author_ids: Sequence[str], *, years: tuple[int, int] | None = None
) -> tuple[dict[str, list[dict[str, Any]]], list[Fetched]]:
    """The works of each author record, asked :data:`AUTHOR_BATCH` records at a time.

    Returns author id → its works (a work of two of them is under both), and the answers.
    """
    ids = sorted({a for a in author_ids if a})
    out: dict[str, list[dict[str, Any]]] = {a: [] for a in ids}
    answers = []
    for i in range(0, len(ids), AUTHOR_BATCH):
        batch = set(ids[i : i + AUTHOR_BATCH])
        fetched = works_by_authors(client, sorted(batch), years=years)
        answers.append(fetched)
        for work in fetched.data:
            for aid in _work_authors(work) & batch:
                out[aid].append(work)
    return out, answers


def author_batches(groups: Sequence[Sequence[str]], size: int = AUTHOR_BATCH) -> list[list[int]]:
    """Groups of author records (one per person), packed in order into batches of at most
    *size* records; each batch lists the indices of its groups.

    A group that would take a batch past *size* starts the next one, a group larger than
    *size* is a batch of its own, and a group without records joins the batch it falls in.
    """
    batches: list[list[int]] = []
    current: list[int] = []
    ids: set[str] = set()
    for i, group in enumerate(groups):
        new = set(group) - ids
        if current and len(ids) + len(new) > size:
            batches.append(current)
            current, ids, new = [], set(), set(group)
        current.append(i)
        ids |= new
    if current:
        batches.append(current)
    return batches


def _work_authors(work: dict[str, Any]) -> set[str]:
    return {
        a
        for a in (
            short_id((x.get("author") or {}).get("id")) for x in work.get("authorships") or []
        )
        if a
    }


def search_institutions(client: HttpClient, name: str, *, per_page: int = 5) -> list[dict]:
    """Institution records whose names match *name*."""
    fetched = client.get_json(
        SERVICE,
        "institutions",
        {"search": name, "per_page": per_page},
        kind="institution_search",
        sends=["institution name"],
        validate=_check_list,
    )
    return list(fetched.data["results"])


def _window(years: tuple[int, int] | None) -> list[str]:
    if years is None:
        return []
    first, last = years
    out = []
    if first:
        out.append(f"from_publication_date:{first}-01-01")
    if last:
        out.append(f"to_publication_date:{last}-12-31")
    return out


def works_by_authors(
    client: HttpClient, author_ids: Sequence[str], *, years: tuple[int, int] | None = None
) -> Fetched:
    """Every work of these author records within *years*, all pages, checked complete."""
    ids = sorted({a for a in author_ids if a})
    if not ids:
        raise ValueError("no author record to ask for")
    filters = ["author.id:" + "|".join(ids), *_window(years)]
    fetched = client.get_all(
        SERVICE,
        "works",
        {"filter": ",".join(filters), "select": WORK_FIELDS, "per_page": PER_PAGE},
        kind="works_by_author",
        sends=["identifier"],
        paging=PAGING,
        validate=_check_list,
    )
    return replace(fetched, data=complete_authors(client, fetched.data))


def record_dois(client: HttpClient, author_id: str) -> set[str]:
    """The DOIs of every work of one author record (only the DOI of each work is asked for)."""
    fetched = client.get_all(
        SERVICE,
        "works",
        {"filter": f"author.id:{author_id}", "select": "id,doi", "per_page": PER_PAGE},
        kind="works_by_author",
        sends=["identifier"],
        paging=PAGING,
        validate=_check_list,
    )
    return {d for d in (bare_doi(w.get("doi")) for w in fetched.data) if d}


def works_by_dois(client: HttpClient, dois: Iterable[str]) -> list[tuple[dict[str, Any], Fetched]]:
    """The works of these DOIs, asked for :data:`DOI_BATCH` at a time; each with its answer."""
    wanted = sorted({d for d in (bare_doi(x) for x in dois) if d})
    out: list[tuple[dict[str, Any], Fetched]] = []
    for i in range(0, len(wanted), DOI_BATCH):
        batch = wanted[i : i + DOI_BATCH]
        fetched = client.get_all(
            SERVICE,
            "works",
            {"filter": "doi:" + "|".join(batch), "select": WORK_FIELDS, "per_page": PER_PAGE},
            kind="works_by_doi",
            sends=["DOI"],
            paging=PAGING,
            validate=_check_list,
        )
        out += [(w, fetched) for w in complete_authors(client, fetched.data)]
    return out


def record_summary(record: dict[str, Any]) -> dict[str, Any]:
    """The evidence a person needs to recognise an author record."""
    years = [c["year"] for c in record.get("counts_by_year") or [] if c.get("works_count")]
    institutions = []
    for aff in record.get("affiliations") or []:
        inst = aff.get("institution") or {}
        ys = [y for y in aff.get("years") or [] if isinstance(y, int)]
        institutions.append(
            {
                "id": short_id(inst.get("id")),
                "name": inst.get("display_name") or "",
                "lineage": [short_id(x) for x in inst.get("lineage") or [] if short_id(x)],
                "first_year": min(ys) if ys else None,
                "last_year": max(ys) if ys else None,
            }
        )
    orcid = record.get("orcid")
    return {
        "record": f"openalex:{short_id(record.get('id'))}",
        "name": record.get("display_name") or "",
        "alternatives": list(record.get("display_name_alternatives") or []),
        "orcid": orcid.rsplit("/", 1)[-1] if orcid else None,
        "institutions": institutions,
        "works": int(record.get("works_count") or 0),
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "topics": [t.get("display_name") for t in (record.get("topics") or [])[:3]],
    }


# ── sources: the API, or the snapshot ────────────────────────────────────────

Years = tuple[int | None, int | None] | None


def api_window(years: Years) -> tuple[int, int] | None:
    """A year window as the request builders take it (0 for an open end)."""
    if years is None or (years[0] is None and years[1] is None):
        return None
    return (years[0] or 0, years[1] or 0)


class OpenAlexSource(Protocol):
    """What the finders ask of OpenAlex; :class:`OpenAlexApi` and the snapshot answer it."""

    #: Where the records come from, as a job's record and the privacy summary name it.
    label: str

    def author(self, author_id: str) -> Fetched | None: ...

    def works_by_authors(self, author_ids: Sequence[str], years: Years) -> Fetched: ...

    def works_by_dois(self, dois: Iterable[str]) -> list[tuple[dict[str, Any], Fetched]]: ...

    def institution(self, ref: str) -> Fetched | None: ...

    def search_institutions(self, name: str) -> list[dict[str, Any]]: ...

    def institution_units(self, roots: Sequence[str]) -> Fetched: ...

    def works_by_institutions(self, roots: Sequence[str], years: Years) -> Fetched: ...

    def institution_work_pages(
        self, roots: Sequence[str], years: Years, *, cursor: str | None = None, read: int = 0
    ) -> Iterator[Page]: ...

    def works_of_authors(
        self, author_ids: Sequence[str], years: Years
    ) -> dict[str, list[dict[str, Any]]]: ...


class OpenAlexApi:
    """OpenAlex through its API, request by request, through the job's client."""

    label = "api"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def author(self, author_id: str) -> Fetched | None:
        return author(self.client, author_id)

    def works_by_authors(self, author_ids: Sequence[str], years: Years) -> Fetched:
        return works_by_authors(self.client, author_ids, years=api_window(years))

    def works_by_dois(self, dois: Iterable[str]) -> list[tuple[dict[str, Any], Fetched]]:
        return works_by_dois(self.client, dois)

    def institution(self, ref: str) -> Fetched | None:
        return institution(self.client, ref)

    def search_institutions(self, name: str) -> list[dict[str, Any]]:
        return search_institutions(self.client, name, per_page=10)

    def institution_units(self, roots: Sequence[str]) -> Fetched:
        return institution_units(self.client, roots)

    def works_by_institutions(self, roots: Sequence[str], years: Years) -> Fetched:
        return works_by_institutions(self.client, roots, years=api_window(years))

    def institution_work_pages(
        self, roots: Sequence[str], years: Years, *, cursor: str | None = None, read: int = 0
    ) -> Iterator[Page]:
        return institution_work_pages(
            self.client, roots, years=api_window(years), cursor=cursor, read=read
        )

    def works_of_authors(
        self, author_ids: Sequence[str], years: Years
    ) -> dict[str, list[dict[str, Any]]]:
        return works_of_authors(self.client, author_ids, years=api_window(years))[0]
