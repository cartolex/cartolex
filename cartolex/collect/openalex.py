# SPDX-License-Identifier: MIT
"""The OpenAlex requests cartolex makes, and how their answers are read.

Every request goes through the job's :class:`~cartolex.collect.http.HttpClient`,
with its kind (for the cache lifetime) and the kinds of data it sends:

============================== ======================= ==============
request                        kind                    sends
============================== ======================= ==============
``authors?search=``            ``person_search``       a name
``authors?filter=orcid:``      ``authors_by_orcid``    an identifier
``authors/<id>``               ``author``              an identifier
``institutions?search=``       ``institution_search``  an institution name
``works?filter=author.id:``    ``works_by_author``     identifiers
``works?filter=doi:``          ``works_by_doi``        DOIs, 50 at a time
============================== ======================= ==============

Lists of works use cursor paging at the documented maximum of 100 per page.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from .http import CursorPaging, Fetched, HttpClient, NotFound

__all__ = [
    "DOI_BATCH",
    "PAGING",
    "PER_PAGE",
    "author",
    "authors_by_orcid",
    "bare_doi",
    "doc_type",
    "record_dois",
    "search_authors",
    "search_institutions",
    "short_id",
    "works_by_authors",
    "works_by_dois",
]

SERVICE = "openalex"
#: The documented maximum of results per page.
PER_PAGE = 100
#: DOIs asked for in one request (the documented limit of one filter is 100 values).
DOI_BATCH = 50
_ID = re.compile(r"([AWIST]\d+)$", re.IGNORECASE)


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
    return client.get_all(
        SERVICE,
        "works",
        {"filter": ",".join(filters), "per_page": PER_PAGE},
        kind="works_by_author",
        sends=["identifier"],
        paging=PAGING,
        validate=_check_list,
    )


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
            {"filter": "doi:" + "|".join(batch), "per_page": PER_PAGE},
            kind="works_by_doi",
            sends=["DOI"],
            paging=PAGING,
            validate=_check_list,
        )
        out += [(w, fetched) for w in fetched.data]
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
