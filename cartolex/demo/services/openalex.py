# SPDX-License-Identifier: MIT
"""The subset of the OpenAlex API cartolex uses, served from a :class:`Bibliography`.

Routes (under the service's prefix): ``authors`` (search, filters, paging) and
``authors/<id>``; ``works`` (filters, paging) and ``works/<id>``;
``institutions`` (search, filters, among them ``lineage``: an institution and
every unit below it) and ``institutions/<id>`` or ``institutions/ror:<ror>``. Responses have
the documented shapes: a list is ``{"meta": {...}, "results": [...],
"group_by": []}``, ``per_page`` is at most 100, ``cursor=*`` starts cursor
paging and ``meta.next_cursor`` is ``null`` on the last page.

Search is deliberately literal: every word of the query must be a word of one
of the record's names (a one-letter word matches an initial), with case
ignored but accents kept, so name variants matter as they do in real use.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from ..vocabulary import THEME_BY_ID, THEMES
from .biblio import AuthorRecord, Bibliography, IndexWork, Institution
from .http import Reply, Request, json_reply
from .sources import sources_layer

__all__ = ["OpenAlexService"]

ROOT = "https://openalex.org/"
MAX_PER_PAGE = 100
#: The authors a work names in a list answer, as OpenAlex cuts them (its own record names all).
AUTHORS_SHOWN = 100
_TOPIC_ID = {t.id: f"T999{i + 1:04d}" for i, t in enumerate(THEMES)}
_WORD = re.compile(r"\w+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _short(value: str) -> str:
    """``https://openalex.org/A999…`` or ``A999…`` → ``A999…`` (upper-cased)."""
    return value.rsplit("/", 1)[-1].strip().upper()


def _bare_doi(value: str) -> str:
    value = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value


def _bare_orcid(value: str) -> str:
    return value.strip().rsplit("/", 1)[-1]


class _BadQuery(ValueError):
    pass


def _name_matches(query: list[str], name: str) -> bool:
    words = _words(name)
    return all(any(w == q or (len(q) == 1 and w.startswith(q)) for w in words) for q in query)


class OpenAlexService:
    """The OpenAlex routes of the demo services."""

    name = "openalex"

    def __init__(self, bib: Bibliography) -> None:
        self.bib = bib
        #: The authors a work names in a list answer (tests lower it to see works cut).
        self.authors_shown = AUTHORS_SHOWN
        #: Open-access copies (world work → link), from the sources layer.
        self._oa = sources_layer(bib).oa_links
        self._works = {w.id: self._work_json(w) for w in bib.works.values()}
        self._record_affiliations = {a.id: self._affiliations(a) for a in bib.authors.values()}
        self._authors = {a.id: self._author_json(a) for a in bib.authors.values()}
        self._inst_works = Counter(
            inst for w in bib.works.values() for a in w.authorships for inst in a.institutions
        )
        self._institutions = {i.id: self._institution_json(i) for i in bib.institutions.values()}

    # ── JSON of each entity ──
    def _dehydrated(self, inst: Institution) -> dict[str, Any]:
        return {
            "id": ROOT + inst.id,
            "display_name": inst.name,
            "ror": f"https://ror.org/{inst.ror}" if inst.ror else None,
            "country_code": None,
            "type": inst.type,
            "lineage": [ROOT + i for i in inst.lineage],
        }

    def _institution_json(self, inst: Institution) -> dict[str, Any]:
        out = self._dehydrated(inst)
        out.update(
            display_name_acronyms=[inst.acronym] if inst.acronym else [],
            display_name_alternatives=[],
            geo=(
                {
                    "city": inst.site,
                    "region": None,
                    "country_code": None,
                    "country": None,
                    "latitude": inst.lat,
                    "longitude": inst.lon,
                }
                if inst.lat is not None
                else None
            ),
            associated_institutions=[
                dict(self._dehydrated(self.bib.institutions[p]), relationship="parent")
                for p in inst.parents
            ]
            + [
                dict(self._dehydrated(child), relationship="child")
                for child in sorted(self.bib.institutions.values(), key=lambda i: i.id)
                if inst.id in child.parents
            ],
            works_count=self._inst_works.get(inst.id, 0),
            cited_by_count=0,
            ids={
                "openalex": ROOT + inst.id,
                **({"ror": f"https://ror.org/{inst.ror}"} if inst.ror else {}),
            },
        )
        return out

    @staticmethod
    def _topic(theme_id: str, count: int | None = None, score: float | None = None) -> dict:
        theme = THEME_BY_ID[theme_id]
        topic: dict[str, Any] = {
            "id": ROOT + _TOPIC_ID[theme_id],
            "display_name": theme.name_en,
        }
        if count is not None:
            topic["count"] = count
        if score is not None:
            topic["score"] = score
        field = "Social sciences of the sea" if theme.kind == "social" else "Marine sciences"
        topic.update(
            subfield={"id": ROOT + "subfields/9991", "display_name": "Coastal and marine systems"},
            field={"id": ROOT + "fields/999", "display_name": field},
            domain={"id": ROOT + "domains/9", "display_name": "Invented domain"},
        )
        return topic

    def _work_json(self, w: IndexWork) -> dict[str, Any]:
        n = len(w.authorships)
        authorships = []
        for rank, a in enumerate(w.authorships):
            insts = [self.bib.institutions[i] for i in a.institutions]
            position = "first" if rank == 0 else ("last" if rank == n - 1 else "middle")
            authorships.append(
                {
                    "author_position": position,
                    "author": {
                        "id": ROOT + a.author_id if a.author_id else None,
                        "display_name": a.name,
                        "orcid": f"https://orcid.org/{a.orcid}" if a.orcid else None,
                    },
                    "institutions": [self._dehydrated(i) for i in insts],
                    "countries": [],
                    "is_corresponding": a.corresponding,
                    "raw_author_name": a.name,
                    "raw_affiliation_strings": [i.name for i in insts],
                    "affiliations": [
                        {"raw_affiliation_string": i.name, "institution_ids": [ROOT + i.id]}
                        for i in insts
                    ],
                }
            )
        index: dict[str, list[int]] = {}
        for pos, word in enumerate(w.abstract.split()):
            index.setdefault(word, []).append(pos)
        doi = f"https://doi.org/{w.doi}" if w.doi else None
        oa_link = self._oa.get(w.world_work) if w.world_work else None
        oa_location = (
            {
                "is_oa": True,
                "landing_page_url": oa_link,
                "pdf_url": oa_link,
                "source": None,
                "license": None,
                "version": "acceptedVersion",
            }
            if oa_link
            else None
        )
        source = {
            "id": ROOT + f"S999{int(hashlib.sha256(w.venue.encode()).hexdigest(), 16) % 10**7:07d}",
            "display_name": w.venue,
            "issn_l": None,
            "issn": None,
            "host_organization": None,
            "type": w.source_type,
        }
        return {
            "id": ROOT + w.id,
            "doi": doi,
            "title": w.title,
            "display_name": w.title,
            "publication_year": w.year,
            "publication_date": w.date,
            "ids": {"openalex": ROOT + w.id, **({"doi": doi} if doi else {})},
            "language": w.language,
            "primary_location": {
                "is_oa": False,
                "landing_page_url": doi,
                "pdf_url": None,
                "source": source,
                "license": None,
                "version": None,
            },
            "open_access": {
                "is_oa": oa_link is not None,
                "oa_status": "green" if oa_link else "closed",
                "oa_url": oa_link,
                "any_repository_has_fulltext": oa_link is not None,
            },
            "best_oa_location": oa_location,
            "locations_count": 2 if oa_location else 1,
            "type": w.type,
            "authorships": authorships,
            "is_authors_truncated": False,
            "corresponding_author_ids": [
                ROOT + a.author_id for a in w.authorships if a.corresponding and a.author_id
            ],
            "abstract_inverted_index": index or None,
            "cited_by_count": 0,
            "is_retracted": False,
            "is_paratext": False,
            "primary_topic": self._topic(w.themes[0], score=0.99) if w.themes else None,
            "topics": [self._topic(t, score=0.99 - 0.1 * i) for i, t in enumerate(w.themes)],
            "updated_date": "2026-01-01T00:00:00",
            "created_date": "2020-01-01",
        }

    def _affiliations(self, a: AuthorRecord) -> dict[str, set[int]]:
        years: dict[str, set[int]] = {}
        for wid in a.works:
            w = self.bib.works[wid]
            for au in w.authorships:
                if au.author_id == a.id:
                    for inst in au.institutions:
                        years.setdefault(inst, set()).add(w.year)
        return years

    def _author_json(self, a: AuthorRecord) -> dict[str, Any]:
        works = [self.bib.works[w] for w in a.works]
        affs = self._record_affiliations[a.id]
        ordered = sorted(affs, key=lambda i: (-max(affs[i]), i))
        latest = max((w.year for w in works), default=None)
        last_known = sorted(
            {
                inst
                for w in works
                if w.year == latest
                for au in w.authorships
                if au.author_id == a.id
                for inst in au.institutions
            }
        )
        themes = Counter(t for w in works for t in w.themes[:1])
        by_year = Counter(w.year for w in works)
        orcid = f"https://orcid.org/{a.orcid}" if a.orcid else None
        return {
            "id": ROOT + a.id,
            "orcid": orcid,
            "display_name": a.display_name,
            "display_name_alternatives": list(a.alternatives),
            "works_count": len(works),
            "cited_by_count": 0,
            "summary_stats": {"2yr_mean_citedness": 0.0, "h_index": 0, "i10_index": 0},
            "ids": {"openalex": ROOT + a.id, **({"orcid": orcid} if orcid else {})},
            "affiliations": [
                {
                    "institution": self._dehydrated(self.bib.institutions[i]),
                    "years": sorted(affs[i], reverse=True),
                }
                for i in ordered
            ],
            "last_known_institutions": [
                self._dehydrated(self.bib.institutions[i]) for i in last_known
            ],
            "topics": [
                self._topic(t, count=n)
                for t, n in sorted(themes.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "counts_by_year": [
                {"year": y, "works_count": n, "cited_by_count": 0}
                for y, n in sorted(by_year.items(), reverse=True)
            ],
            "works_api_url": f"https://api.openalex.org/works?filter=author.id:{a.id}",
            "updated_date": "2026-01-01T00:00:00",
            "created_date": "2020-01-01",
        }

    # ── requests ──
    def handle(self, request: Request) -> Reply:
        parts = [p for p in request.path.split("/") if p]
        try:
            if not parts:
                return json_reply(200, {"msg": "demo OpenAlex", "version": "demo"})
            entity = parts[0]
            if entity not in ("authors", "works", "institutions"):
                return json_reply(404, {"error": "Not found", "message": f"no route {entity}"})
            if len(parts) > 1:
                return self._one(entity, "/".join(parts[1:]), request.query.get("select"))
            return self._list(entity, request.query)
        except _BadQuery as exc:
            return json_reply(
                400, {"error": "Invalid query parameters error.", "message": str(exc)}
            )

    def _one(self, entity: str, ident: str, select: str | None = None) -> Reply:
        found: dict[str, Any] | None = None
        if entity == "authors":
            if ident.lower().startswith("orcid:"):
                orcid = _bare_orcid(ident[6:])
                found = next(
                    (self._authors[a.id] for a in self.bib.authors.values() if a.orcid == orcid),
                    None,
                )
            else:
                found = self._authors.get(_short(ident))
        elif entity == "works":
            if ident.lower().startswith(("doi:", "https://doi.org/", "10.")):
                doi = _bare_doi(ident)
                found = next(
                    (self._works[w.id] for w in self.bib.works.values() if w.doi == doi), None
                )
            else:
                found = self._works.get(_short(ident))
        elif ident.lower().startswith("ror:"):
            ror = ident[4:].strip().rsplit("/", 1)[-1].lower()
            found = next(
                (self._institutions[i.id] for i in self.bib.institutions.values() if i.ror == ror),
                None,
            )
        else:
            found = self._institutions.get(_short(ident))
        if found is None:
            return json_reply(404, {"error": "Not found", "message": f"no such {entity[:-1]}"})
        return json_reply(200, self._project(found, select))

    def _list(self, entity: str, query: dict[str, str]) -> Reply:
        per_page_text = query.get("per_page", query.get("per-page", "25"))
        try:
            per_page = int(per_page_text)
        except ValueError:
            raise _BadQuery(f"per_page must be a number, not {per_page_text!r}") from None
        if not 1 <= per_page <= MAX_PER_PAGE:
            raise _BadQuery(f"per_page is {per_page}; it must be between 1 and {MAX_PER_PAGE}")
        rows = self._select(entity, query.get("search", ""), query.get("filter", ""))
        count = len(rows)
        signature = hashlib.sha256(
            f"{entity}|{query.get('search', '')}|{query.get('filter', '')}".encode()
        ).hexdigest()[:12]
        meta: dict[str, Any] = {
            "count": count,
            "db_response_time_ms": 3,
            "per_page": per_page,
            "groups_count": None,
            "cost_usd": 0.001 if query.get("search") else 0.0001,
        }
        if "cursor" in query:
            cursor = query["cursor"]
            if cursor == "*":
                offset = 0
            else:
                try:
                    decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
                    sig, offset_text = decoded.split(":")
                    offset = int(offset_text)
                except (ValueError, UnicodeDecodeError):
                    raise _BadQuery("the cursor is not valid") from None
                if sig != signature:
                    raise _BadQuery("the cursor belongs to another query")
            page_rows = rows[offset : offset + per_page]
            end = offset + len(page_rows)
            meta["page"] = None
            meta["next_cursor"] = (
                base64.urlsafe_b64encode(f"{signature}:{end}".encode()).decode()
                if page_rows
                else None
            )
        else:
            try:
                page = int(query.get("page", "1"))
            except ValueError:
                raise _BadQuery("page must be a number") from None
            if page < 1 or page * per_page > 10_000 + per_page:
                raise _BadQuery("basic paging only reaches the first 10,000 results")
            page_rows = rows[(page - 1) * per_page : page * per_page]
            meta["page"] = page
        if entity == "works":
            page_rows = [self._cut_authors(row) for row in page_rows]
        results = [self._project(row, query.get("select")) for row in page_rows]
        return json_reply(200, {"meta": meta, "results": results, "group_by": []})

    def _cut_authors(self, row: dict[str, Any]) -> dict[str, Any]:
        """A work as a list shows it: its first :attr:`authors_shown` authors only."""
        if len(row.get("authorships") or []) <= self.authors_shown:
            return row
        return {**row, "authorships": row["authorships"][: self.authors_shown],
                "is_authors_truncated": True}  # fmt: skip

    @staticmethod
    def _project(row: dict[str, Any], select: str | None) -> dict[str, Any]:
        if not select:
            return row
        wanted = [f.strip() for f in select.split(",") if f.strip()]
        return {k: row[k] for k in wanted if k in row}

    # ── filtering ──
    def _select(self, entity: str, search: str, filters: str) -> list[dict[str, Any]]:
        if entity == "authors":
            rows = list(self.bib.authors.values())
            tests = [self._author_filter(k, v) for k, v in _parse_filters(filters)]
            rows = [a for a in rows if all(t(a) for t in tests)]
            if search:
                q = _words(search)
                rows = [
                    a
                    for a in rows
                    if any(_name_matches(q, n) for n in (a.display_name, *a.alternatives))
                ]
            rows.sort(key=lambda a: (-len(a.works), a.id))
            return [self._authors[a.id] for a in rows]
        if entity == "works":
            works = list(self.bib.works.values())
            tests = [self._work_filter(k, v) for k, v in _parse_filters(filters)]
            works = [w for w in works if all(t(w) for t in tests)]
            if search:
                q = _words(search)
                works = [w for w in works if set(q) <= set(_words(w.title + " " + w.abstract))]
            works.sort(key=lambda w: (w.date, w.id))
            return [self._works[w.id] for w in works]
        insts = list(self.bib.institutions.values())
        tests = [self._institution_filter(k, v) for k, v in _parse_filters(filters)]
        insts = [i for i in insts if all(t(i) for t in tests)]
        if search:
            q = _words(search)
            insts = [
                i
                for i in insts
                if set(q) <= set(_words(i.name)) or set(q) <= set(_words(i.acronym or ""))
            ]
        insts.sort(key=lambda i: (-self._inst_works.get(i.id, 0), i.id))
        return [self._institutions[i.id] for i in insts]

    def _author_filter(self, key: str, value: str) -> Callable[[AuthorRecord], bool]:
        values = _alternatives(value)
        if key in ("id", "openalex", "ids.openalex"):
            wanted = {_short(v) for v in values}
            return lambda a: a.id in wanted
        if key in ("orcid", "ids.orcid"):
            wanted = {_bare_orcid(v) for v in values}
            return lambda a: a.orcid in wanted
        if key in ("affiliations.institution.id", "last_known_institutions.id"):
            wanted = {_short(v) for v in values}
            if key == "affiliations.institution.id":
                return lambda a: bool(wanted & set(self._record_affiliations[a.id]))
            return lambda a: bool(
                wanted & {_short(i["id"]) for i in self._authors[a.id]["last_known_institutions"]}
            )
        if key == "display_name.search":
            q = _words(value)
            return lambda a: any(_name_matches(q, n) for n in (a.display_name, *a.alternatives))
        raise _BadQuery(f"{key} is not a valid filter for authors")

    def _work_filter(self, key: str, value: str) -> Callable[[IndexWork], bool]:
        values = _alternatives(value)
        if key in ("author.id", "authorships.author.id"):
            wanted = {_short(v) for v in values}
            return lambda w: any(a.author_id in wanted for a in w.authorships)
        if key == "doi":
            wanted = {_bare_doi(v) for v in values}
            if len(values) > 100:
                raise _BadQuery("at most 100 values may be combined with |")
            return lambda w: w.doi in wanted
        if key in ("openalex", "ids.openalex", "id"):
            wanted = {_short(v) for v in values}
            return lambda w: w.id in wanted
        if key == "publication_year":
            return _year_test(value)
        if key == "from_publication_date":
            return lambda w: w.date >= value
        if key == "to_publication_date":
            return lambda w: w.date <= value
        if key == "type":
            return lambda w: w.type in values
        if key == "authorships.institutions.id":
            wanted = {_short(v) for v in values}
            return lambda w: any(wanted & set(a.institutions) for a in w.authorships)
        if key == "authorships.institutions.lineage":
            wanted = {_short(v) for v in values}
            return lambda w: any(
                wanted & set(self.bib.institutions[i].lineage)
                for a in w.authorships
                for i in a.institutions
            )
        raise _BadQuery(f"{key} is not a valid filter for works")

    def _institution_filter(self, key: str, value: str) -> Callable[[Institution], bool]:
        values = _alternatives(value)
        if key in ("id", "openalex", "ids.openalex"):
            wanted = {_short(v) for v in values}
            return lambda i: i.id in wanted
        if key == "display_name.search":
            q = _words(value)
            return lambda i: set(q) <= set(_words(i.name))
        if key == "lineage":
            wanted = {_short(v) for v in values}
            return lambda i: bool(wanted & set(self._ancestry(i)))
        if key == "ror":
            wanted = {v.rsplit("/", 1)[-1].lower() for v in values}
            return lambda i: i.ror in wanted
        if key == "type":
            return lambda i: i.type in values
        raise _BadQuery(f"{key} is not a valid filter for institutions")

    def _ancestry(self, inst: Institution) -> list[str]:
        """The institution and every institution above it."""
        out, todo = [], [inst.id]
        while todo:
            iid = todo.pop()
            if iid not in out:
                out.append(iid)
                todo += list(self.bib.institutions[iid].parents)
        return out


def _parse_filters(text: str) -> Iterable[tuple[str, str]]:
    for item in (x for x in text.split(",") if x.strip()):
        key, sep, value = item.partition(":")
        if not sep or not value:
            raise _BadQuery(f"the filter {item!r} is not key:value")
        yield key.strip(), value.strip()


def _alternatives(value: str) -> list[str]:
    values = [v for v in value.split("|") if v]
    if len(values) > 100:
        raise _BadQuery("at most 100 values may be combined with |")
    return values


def _year_test(value: str) -> Callable[[IndexWork], bool]:
    try:
        if value.startswith(">"):
            low = int(value[1:])
            return lambda w: w.year > low
        if value.startswith("<"):
            high = int(value[1:])
            return lambda w: w.year < high
        if "-" in value:
            a, b = value.split("-", 1)
            lo, hi = int(a) if a else -1, int(b) if b else 10**6
            return lambda w: lo <= w.year <= hi
        years = {int(v) for v in value.split("|")}
    except ValueError:
        raise _BadQuery(f"publication_year {value!r} is not a year or a range") from None
    return lambda w: w.year in years
