# SPDX-License-Identifier: MIT
"""The subset of the ORCID public API (v3.0) cartolex uses, served from a :class:`Bibliography`.

Routes: ``v3.0/<orcid>/works`` (the works summary, grouped by identifier) and
``v3.0/<orcid>/record`` (name, other names, employments and works). As the
real service does, it answers in XML unless the request asks for JSON with its
``Accept`` header, and answers 404 for an identifier it does not hold.
"""

from __future__ import annotations

import re
from typing import Any

from .biblio import Bibliography, Employment, RegistryRecord, RegistryWork
from .http import Reply, Request, json_reply

__all__ = ["OrcidService"]

_ORCID = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
_STAMP = 1767225600000  # 2026-01-01, in milliseconds, as the registry dates changes


def _date(year: int | None) -> dict[str, Any] | None:
    if year is None:
        return None
    return {"year": {"value": str(year)}, "month": None, "day": None}


def _external_ids(doi: str) -> dict[str, Any]:
    return {
        "external-id": [
            {
                "external-id-type": "doi",
                "external-id-value": doi,
                "external-id-normalized": {"value": doi.lower(), "transient": True},
                "external-id-url": {"value": f"https://doi.org/{doi}"},
                "external-id-relationship": "self",
            }
        ]
    }


class OrcidService:
    """The ORCID routes of the demo services."""

    name = "orcid"

    def __init__(self, bib: Bibliography) -> None:
        self.bib = bib

    def _work(self, orcid: str, w: RegistryWork) -> dict[str, Any]:
        return {
            "put-code": w.put_code,
            "created-date": {"value": _STAMP},
            "last-modified-date": {"value": _STAMP},
            "source": {"source-name": {"value": "demo services"}},
            "title": {"title": {"value": w.title}, "subtitle": None, "translated-title": None},
            "external-ids": _external_ids(w.doi),
            "url": None,
            "type": w.type,
            "publication-date": _date(w.year),
            "journal-title": {"value": w.venue},
            "visibility": "public",
            "path": f"/{orcid}/work/{w.put_code}",
            "display-index": "0",
        }

    def _works(self, rec: RegistryRecord) -> dict[str, Any]:
        return {
            "last-modified-date": {"value": _STAMP},
            "group": [
                {
                    "last-modified-date": {"value": _STAMP},
                    "external-ids": _external_ids(w.doi),
                    "work-summary": [self._work(rec.orcid, w)],
                }
                for w in rec.works
            ],
            "path": f"/{rec.orcid}/works",
        }

    def _employment(self, rec: RegistryRecord, n: int, e: Employment) -> dict[str, Any]:
        return {
            "summaries": [
                {
                    "employment-summary": {
                        "put-code": 500 + n,
                        "department-name": e.department,
                        "role-title": None,
                        "start-date": _date(e.start),
                        "end-date": _date(e.end),
                        "organization": {
                            "name": e.organisation,
                            "address": {"city": e.site, "region": None, "country": None},
                            "disambiguated-organization": None,
                        },
                        "visibility": "public",
                        "path": f"/{rec.orcid}/employment/{500 + n}",
                    }
                }
            ]
        }

    def _record(self, rec: RegistryRecord) -> dict[str, Any]:
        return {
            "orcid-identifier": {
                "uri": f"https://orcid.org/{rec.orcid}",
                "path": rec.orcid,
                "host": "orcid.org",
            },
            "person": {
                "name": {
                    "given-names": {"value": rec.given},
                    "family-name": {"value": rec.family},
                    "credit-name": None,
                    "path": rec.orcid,
                },
                "other-names": {
                    "other-name": [{"content": n} for n in rec.other_names],
                    "path": f"/{rec.orcid}/other-names",
                },
                "path": f"/{rec.orcid}/person",
            },
            "activities-summary": {
                "employments": {
                    "affiliation-group": [
                        self._employment(rec, n, e) for n, e in enumerate(rec.employments)
                    ],
                    "path": f"/{rec.orcid}/employments",
                },
                "works": self._works(rec),
                "path": f"/{rec.orcid}/activities",
            },
            "path": f"/{rec.orcid}",
        }

    def handle(self, request: Request) -> Reply:
        parts = [p for p in request.path.split("/") if p]
        if len(parts) != 3 or parts[0] != "v3.0" or parts[2] not in ("works", "record"):
            return json_reply(404, {"response-code": 404, "developer-message": "no such route"})
        orcid = parts[1]
        if not _ORCID.match(orcid):
            return json_reply(
                400,
                {"response-code": 400, "developer-message": f"{orcid} is not an ORCID iD"},
            )
        rec = self.bib.registry.get(orcid)
        if rec is None:
            return json_reply(
                404,
                {
                    "response-code": 404,
                    "developer-message": f"ORCID iD {orcid} not found",
                    "user-message": "The resource was not found.",
                    "error-code": 9016,
                },
            )
        if "json" not in request.headers.get("accept", ""):
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<record:record path="/{orcid}"></record:record>\n'
            )
            return Reply(200, body.encode("utf-8"), "application/vnd.orcid+xml; charset=UTF-8")
        data = self._works(rec) if parts[2] == "works" else self._record(rec)
        return json_reply(200, data)
