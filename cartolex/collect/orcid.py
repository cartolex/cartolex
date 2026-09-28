# SPDX-License-Identifier: MIT
"""The ORCID public API (v3.0) requests cartolex makes, and how their answers are read.

The registry is where a person declares their own works and employments. It
is what separates two people an index merged into one record: the index
copies the ORCID of the merged record onto every work of that record, so
filtering the index by ORCID gives both people's works; the person's declared
works, fetched from the index by DOI, give theirs only. Requests ask for JSON
(``Accept: application/json``); without it the service answers in XML.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .http import Fetched, HttpClient, NotFound
from .openalex import bare_doi

__all__ = [
    "DeclaredWork",
    "Employment",
    "declared_works",
    "employments",
    "registry_names",
    "registry_record",
]

SERVICE = "orcid"


@dataclass(frozen=True)
class DeclaredWork:
    """A work a person declared, with its DOI when it has one."""

    put_code: int | None
    title: str
    year: int | None
    doi: str | None
    type: str | None


@dataclass(frozen=True)
class Employment:
    """An employment a person declared."""

    organisation: str
    department: str | None
    start: int | None
    end: int | None


def _check_works(data: Any) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("group", []), list):
        raise ValueError("a works answer has a list of groups")


def _check_record(data: Any) -> None:
    if not isinstance(data, dict) or "orcid-identifier" not in data:
        raise ValueError("a record answer names its ORCID")


def _value(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _year(date: Any) -> int | None:
    text = _value(date, "year", "value")
    try:
        return int(text) if text else None
    except (TypeError, ValueError):
        return None


def _doi(external_ids: Any) -> str | None:
    for ext in _value(external_ids, "external-id") or []:
        if (ext.get("external-id-type") or "").lower() != "doi":
            continue
        if (ext.get("external-id-relationship") or "self") != "self":
            continue
        value = _value(ext, "external-id-normalized", "value") or ext.get("external-id-value")
        doi = bare_doi(value)
        if doi:
            return doi
    return None


def parse_works(data: dict[str, Any]) -> list[DeclaredWork]:
    """The declared works of a works answer: one per group, the group's DOI first."""
    works = []
    for group in data.get("group") or []:
        summaries = group.get("work-summary") or [{}]
        first = summaries[0]
        doi = _doi(group.get("external-ids")) or next(
            (d for d in (_doi(s.get("external-ids")) for s in summaries) if d), None
        )
        works.append(
            DeclaredWork(
                put_code=first.get("put-code"),
                title=_value(first, "title", "title", "value") or "",
                year=_year(first.get("publication-date")),
                doi=doi,
                type=first.get("type"),
            )
        )
    return works


def declared_works(client: HttpClient, orcid: str) -> tuple[list[DeclaredWork], Fetched] | None:
    """The works *orcid* declared (and the answer), or ``None`` when the registry has no such iD."""
    try:
        fetched = client.get_json(
            SERVICE,
            f"{orcid}/works",
            kind="registry_works",
            sends=["identifier"],
            validate=_check_works,
            headers={"Accept": "application/json"},
        )
    except NotFound:
        return None
    return parse_works(fetched.data), fetched


def registry_record(client: HttpClient, orcid: str) -> Fetched | None:
    """The whole public record of *orcid*, or ``None`` when the registry has no such iD."""
    try:
        return client.get_json(
            SERVICE,
            f"{orcid}/record",
            kind="registry_record",
            sends=["identifier"],
            validate=_check_record,
            headers={"Accept": "application/json"},
        )
    except NotFound:
        return None


def employments(record: dict[str, Any]) -> list[Employment]:
    """The employments of a record."""
    out = []
    groups = _value(record, "activities-summary", "employments", "affiliation-group") or []
    for group in groups:
        for summary in group.get("summaries") or []:
            emp = summary.get("employment-summary") or {}
            name = _value(emp, "organization", "name")
            if not name:
                continue
            out.append(
                Employment(
                    organisation=name,
                    department=emp.get("department-name"),
                    start=_year(emp.get("start-date")),
                    end=_year(emp.get("end-date")),
                )
            )
    return out


def registry_names(record: dict[str, Any]) -> list[tuple[str, str]]:
    """``(last, first)`` of the record's name, then its other names (split at the last word)."""
    from .names import split_full_name

    names = []
    family = _value(record, "person", "name", "family-name", "value")
    given = _value(record, "person", "name", "given-names", "value")
    if family:
        names.append((family, given or ""))
    for other in _value(record, "person", "other-names", "other-name") or []:
        if other.get("content"):
            names.append(split_full_name(other["content"]))
    return names
