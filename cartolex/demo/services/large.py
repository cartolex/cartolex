# SPDX-License-Identifier: MIT
"""A fake OpenAlex holding one very large institution, its works generated on demand.

:class:`LargeInstitutionService` answers the requests a collection of people
« from institutions » makes, for an invented institution whose works number in
the hundreds of thousands or millions, without storing them: work *i* is built
from a generator seeded with *i* whenever a page holds it, so the service's
memory does not grow with the number of works. It serves

* ``institutions/<id>`` and ``institutions?filter=lineage:…`` (the root and its units);
* ``works?filter=authorships.institutions.lineage:…`` with cursor paging, at
  most 100 a page, ``select`` (root-level fields), and the documented answer
  shape; the year window is accepted and every work lies inside it.

Records have the size and shape of the index's full work records (authorships
with their institutions, an abstract index, referenced and related works,
topics, concepts, locations), so that a measure of memory per work is
meaningful. Authors are drawn from a pool (``works // 8`` by default) with a
skew, so that most of them sign several works; about 60 % have an ORCID in the
block no real record uses.

Cursors carry the offset and when they were issued: with *cursor_ttl*, a
cursor older than that is refused (400), as a service whose cursors expire
would. Faults are injected by the :class:`~cartolex.demo.services.http.DemoServer`
that serves it, like for every demo service::

    service = LargeInstitutionService(works=100_000)
    with DemoServer({"openalex": service}) as server:
        server.faults.add("status", service="openalex", path="cursor=", skip=40, status=503)
"""

from __future__ import annotations

import base64
import hashlib
import random
import time
from collections.abc import Callable
from typing import Any

from ..identifiers import orcid_check_character
from ..names import FIRST_NAMES, invented_surname
from ..vocabulary import THEMES
from .http import Reply, Request, json_reply

__all__ = ["ROOT_ID", "LargeInstitutionService"]

ROOT = "https://openalex.org/"
#: The invented institution at the top of the tree.
ROOT_ID = "I9990000001"
MAX_PER_PAGE = 100
_WORDS = tuple(
    sorted({w.lower() for t in THEMES for term in t.terms for w in term.en.split() if w.isalpha()})
)


def _author_id(k: int) -> str:
    return f"A999{k:07d}"


def _orcid(k: int) -> str:
    body = f"00000000{k:07d}"
    full = body + orcid_check_character(body)
    return "-".join(full[i : i + 4] for i in range(0, 16, 4))


class LargeInstitutionService:
    """The OpenAlex routes of one large invented institution (see the module's text)."""

    name = "openalex"

    def __init__(
        self,
        works: int,
        *,
        authors: int | None = None,
        units: int = 40,
        seed: int = 0,
        years: tuple[int, int] = (2015, 2024),
        cursor_ttl: float | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if works < 1:
            raise ValueError("the institution needs at least one work")
        self.works = works
        self.authors = authors or max(20, works // 8)
        self.n_units = units
        self.seed = seed
        self.years = years
        self.cursor_ttl = cursor_ttl
        self.clock = clock
        self._names: dict[int, tuple[str, str]] = {}
        self.units = [f"I999{1000000 + u:07d}" for u in range(units)]
        self._external = [f"I999{5000000 + e:07d}" for e in range(200)]

    # ── the records ──
    def _dehydrated(self, iid: str) -> dict[str, Any]:
        if iid == ROOT_ID:
            name, typ, lineage = "Invented National Institute", "government", [iid]
        elif iid in self.units:
            n = self.units.index(iid)
            name, typ, lineage = f"Invented Unit {n + 1}", "facility", [iid, ROOT_ID]
        else:
            name, typ, lineage = f"Invented Partner {iid[-3:]}", "education", [iid]
        return {
            "id": ROOT + iid,
            "display_name": name,
            "ror": None,
            "country_code": None,
            "type": typ,
            "lineage": [ROOT + x for x in lineage],
        }

    def _institution(self, iid: str) -> dict[str, Any] | None:
        if iid != ROOT_ID and iid not in self.units and iid not in self._external:
            return None
        out = self._dehydrated(iid)
        parents = [ROOT_ID] if iid in self.units else []
        children = self.units if iid == ROOT_ID else []
        out.update(
            display_name_acronyms=[],
            display_name_alternatives=[],
            associated_institutions=[
                dict(self._dehydrated(p), relationship="parent") for p in parents
            ]
            + [dict(self._dehydrated(c), relationship="child") for c in children],
            works_count=self.works if iid == ROOT_ID else self.works // max(1, self.n_units),
            cited_by_count=0,
            ids={"openalex": ROOT + iid},
        )
        return out

    def _name(self, k: int) -> tuple[str, str]:
        found = self._names.get(k)
        if found is None:
            rng = random.Random(f"{self.seed}:author:{k}")
            found = (rng.choice(FIRST_NAMES), invented_surname(rng))
            if len(self._names) < 200_000:
                self._names[k] = found
        return found

    def _authorship(self, rng: random.Random, k: int | None, position: str) -> dict[str, Any]:
        if k is not None:  # an author of the institution, at one of its units
            first, last = self._name(k)
            unit = self.units[k % self.n_units]
            insts = [unit] + ([ROOT_ID] if k % 5 == 0 else [])
            author = {
                "id": ROOT + _author_id(k),
                "display_name": f"{first} {last}",
                "orcid": f"https://orcid.org/{_orcid(k)}" if k % 5 < 3 else None,
            }
        else:
            outside = random.Random(rng.random())
            first, last = outside.choice(FIRST_NAMES), invented_surname(outside)
            insts = [outside.choice(self._external)]
            author = {
                "id": ROOT + f"A998{outside.randrange(10**7):07d}",
                "display_name": f"{first} {last}",
                "orcid": None,
            }
        names = [self._dehydrated(i)["display_name"] for i in insts]
        return {
            "author_position": position,
            "author": author,
            "institutions": [self._dehydrated(i) for i in insts],
            "countries": [],
            "is_corresponding": position == "first",
            "raw_author_name": author["display_name"],
            "raw_affiliation_strings": names,
            "affiliations": [
                {"raw_affiliation_string": n, "institution_ids": [ROOT + i]}
                for n, i in zip(names, insts, strict=True)
            ],
        }

    def work(self, i: int) -> dict[str, Any]:
        """Work *i* (0-based), the same at every call."""
        rng = random.Random(f"{self.seed}:work:{i}")
        inside = 1 + (rng.random() < 0.4) + (rng.random() < 0.15)
        # A skewed draw: a few authors sign many works, most sign a few.
        ks = sorted({int(self.authors * rng.random() ** 2) for _ in range(inside)})
        n_out = rng.choice((0, 1, 2, 3, 4, 5, 6, 8))
        slots: list[int | None] = [*ks, *([None] * n_out)]
        rng.shuffle(slots)
        n = len(slots)
        authorships = [
            self._authorship(
                rng, k, "first" if r == 0 else ("last" if r == n - 1 else "middle")
            )
            for r, k in enumerate(slots)
        ]
        year = rng.randint(*self.years)
        words = [rng.choice(_WORDS) for _ in range(rng.randint(120, 220))]
        index: dict[str, list[int]] = {}
        for pos, word in enumerate(words):
            index.setdefault(word, []).append(pos)
        title = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(6, 14))).capitalize()
        wid = f"W9990{i:08d}"
        doi = f"https://doi.org/10.5555/cartolex-demo.large.{i}"
        source = {
            "id": ROOT + f"S999{rng.randrange(10**7):07d}",
            "display_name": f"Invented Journal {rng.randrange(400)}",
            "issn_l": None,
            "issn": None,
            "host_organization": None,
            "type": "journal",
        }
        location = {
            "is_oa": False,
            "landing_page_url": doi,
            "pdf_url": None,
            "source": source,
            "license": None,
            "version": "publishedVersion",
        }
        topics = []
        for theme in rng.sample(THEMES, 3):
            topics.append(
                {
                    "id": ROOT + f"T999{rng.randrange(10**4):04d}",
                    "display_name": theme.name_en,
                    "score": round(rng.random(), 4),
                    "subfield": {"id": ROOT + "subfields/9991", "display_name": "Invented"},
                    "field": {"id": ROOT + "fields/999", "display_name": "Invented field"},
                    "domain": {"id": ROOT + "domains/9", "display_name": "Invented domain"},
                }
            )
        return {
            "id": ROOT + wid,
            "doi": doi,
            "title": title,
            "display_name": title,
            "publication_year": year,
            "publication_date": f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "ids": {"openalex": ROOT + wid, "doi": doi},
            "language": "en",
            "primary_location": location,
            "type": "article",
            "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None},
            "authorships": authorships,
            "countries_distinct_count": 1,
            "institutions_distinct_count": len({i["id"] for a in authorships for i in a["institutions"]}),
            "cited_by_count": rng.randrange(200),
            "biblio": {"volume": str(rng.randrange(90)), "issue": "1", "first_page": "1",
                       "last_page": str(rng.randrange(2, 40))},  # fmt: skip
            "is_retracted": False,
            "is_paratext": False,
            "primary_topic": topics[0],
            "topics": topics,
            "keywords": [
                {"id": ROOT + f"keywords/{w}", "display_name": w, "score": 0.5}
                for w in rng.sample(_WORDS, 5)
            ],
            "concepts": [
                {
                    "id": ROOT + f"C999{rng.randrange(10**6):06d}",
                    "wikidata": None,
                    "display_name": " ".join(rng.sample(_WORDS, 2)),
                    "level": rng.randrange(4),
                    "score": round(rng.random(), 4),
                }
                for _ in range(rng.randint(6, 14))
            ],
            "mesh": [],
            "locations_count": 1,
            "locations": [location],
            "best_oa_location": None,
            "sustainable_development_goals": [],
            "grants": [],
            "referenced_works_count": 0,
            "referenced_works": [
                ROOT + f"W9991{rng.randrange(10**8):08d}" for _ in range(rng.randint(10, 60))
            ],
            "related_works": [ROOT + f"W9991{rng.randrange(10**8):08d}" for _ in range(20)],
            "abstract_inverted_index": index,
            "counts_by_year": [{"year": year, "cited_by_count": rng.randrange(20)}],
            "updated_date": "2026-01-01T00:00:00",
            "created_date": f"{year}-01-01",
        }

    # ── requests ──
    def handle(self, request: Request) -> Reply:
        parts = [p for p in request.path.split("/") if p]
        if not parts or parts[0] not in ("works", "institutions"):
            return json_reply(404, {"error": "Not found", "message": "no such route"})
        if len(parts) > 1:
            if parts[0] == "institutions":
                found = self._institution(parts[1].upper())
            else:
                ident = parts[1].upper()
                ok = ident.startswith("W9990") and ident[5:].isdigit()
                found = self.work(int(ident[5:])) if ok and int(ident[5:]) < self.works else None
            if found is None:
                return json_reply(404, {"error": "Not found", "message": "no such record"})
            return json_reply(200, found)
        query = request.query
        try:
            per_page = int(query.get("per_page", query.get("per-page", "25")))
        except ValueError:
            return _bad("per_page must be a number")
        if not 1 <= per_page <= MAX_PER_PAGE:
            return _bad(f"per_page is {per_page}; it must be between 1 and {MAX_PER_PAGE}")
        filters = query.get("filter", "")
        if parts[0] == "institutions":
            if f"lineage:{ROOT_ID}" not in filters:
                return self._page([], 0, query, per_page, filters)
            rows = [self._institution(i) for i in [ROOT_ID, *self.units]]
            return self._page(rows, len(rows), query, per_page, filters)
        if ROOT_ID not in filters and not any(u in filters for u in self.units):
            return self._page([], 0, query, per_page, filters)
        return self._page(None, self.works, query, per_page, filters)

    def _page(
        self,
        rows: list[Any] | None,
        count: int,
        query: dict[str, str],
        per_page: int,
        filters: str,
    ) -> Reply:
        signature = hashlib.sha256(filters.encode()).hexdigest()[:12]
        cursor = query.get("cursor")
        if cursor is None:
            page = int(query.get("page", "1") or 1)
            if page * per_page > 10_000 + per_page:
                return _bad("basic paging only reaches the first 10,000 results")
            offset = (page - 1) * per_page
        elif cursor == "*":
            offset = 0
        else:
            try:
                sig, offset_text, issued = (
                    base64.urlsafe_b64decode(cursor.encode()).decode().split(":")
                )
                offset = int(offset_text)
            except (ValueError, UnicodeDecodeError):
                return _bad("the cursor is not valid")
            if sig != signature:
                return _bad("the cursor belongs to another query")
            if self.cursor_ttl is not None and self.clock() - float(issued) > self.cursor_ttl:
                return _bad("the cursor has expired")
        end = min(count, offset + per_page)
        if rows is None:
            page_rows = [self.work(i) for i in range(offset, end)]
        else:
            page_rows = rows[offset:end]
        select = [f.strip() for f in (query.get("select") or "").split(",") if f.strip()]
        if select:
            page_rows = [{k: r[k] for k in select if k in r} for r in page_rows]
        meta: dict[str, Any] = {
            "count": count,
            "db_response_time_ms": 12,
            "page": None if cursor is not None else int(query.get("page", "1") or 1),
            "per_page": per_page,
            "groups_count": None,
            "cost_usd": 0.0001,
        }
        if cursor is not None:
            meta["next_cursor"] = (
                base64.urlsafe_b64encode(f"{signature}:{end}:{self.clock():.0f}".encode()).decode()
                if page_rows
                else None
            )
        return json_reply(200, {"meta": meta, "results": page_rows, "group_by": []})


def _bad(message: str) -> Reply:
    return json_reply(400, {"error": "Invalid query parameters error.", "message": message})
