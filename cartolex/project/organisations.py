# SPDX-License-Identifier: MIT
"""What people decided about organisations, applied over what the sources say.

``decisions/organisations.csv`` changes an organisation's ``name``, ``level`` and
``parents`` (``;``-separated), or merges it into another (``merged_into``); an
empty cell leaves the source's value. ``decisions/affiliations.csv`` adds an
affiliation the sources lack (``add``) or removes one they give (``remove``:
the person's affiliations to that organisation, those starting in
``start_year`` when it is given).

Everything that shows or counts organisations reads them through here: the
corpus (the unit of each person), the organisations' list and detail, a
person's sheet, the atlas. A merged organisation is read as the one it is
merged into: its affiliations are that organisation's, it is not listed on its
own, and a parent that is merged is replaced by the one that remains. Like a
person's merge, it can be undone: the merged row keeps everything else.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .layout import ProjectLayout
from .tables import read_decision_csv

__all__ = [
    "AFFILIATION_SOURCE",
    "decisions_stamp",
    "effective_affiliations",
    "effective_organisations",
    "org_decisions",
    "org_roots",
    "read_affiliation_decisions",
]

#: The ``source`` of an affiliation someone added.
AFFILIATION_SOURCE = "decision"


def org_decisions(layout: ProjectLayout) -> dict[str, dict[str, str]]:
    """The rows of ``organisations.csv`` by organisation id (none without the file)."""
    return {r["org_id"]: r for r in read_decision_csv(layout.organisations_csv, "organisations")}


def read_affiliation_decisions(layout: ProjectLayout) -> list[dict[str, str]]:
    """The rows of ``affiliations.csv`` (none without the file)."""
    return read_decision_csv(layout.affiliations_csv, "affiliations")


def decisions_stamp(layout: ProjectLayout) -> tuple[Any, ...]:
    """What the effective organisations depend on beyond the tables: both files' versions."""
    out: list[Any] = []
    for path in (layout.organisations_csv, layout.affiliations_csv):
        try:
            st = Path(path).stat()
            out.append((path.name, st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            out.append((path.name, None))
    return tuple(out)


def org_roots(rows: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """Each merged organisation → the one that remains (the end of its chain; a loop, or a
    chain ending on an organisation nobody decided about and absent here, is followed as
    far as it goes)."""
    into = {oid: (r.get("merged_into") or "") for oid, r in rows.items()}
    out: dict[str, str] = {}
    for oid, target in into.items():
        if not target:
            continue
        seen = {oid}
        current = target
        while into.get(current):
            if current in seen:
                current = ""
                break
            seen.add(current)
            current = into[current]
        if current and current not in seen:
            out[oid] = current
    return out


def effective_organisations(
    orgs: Iterable[Mapping[str, Any]], rows: Mapping[str, Mapping[str, str]]
) -> list[dict[str, Any]]:
    """The organisations as people decided them: each one's ``name``, ``level`` and
    ``parents`` replaced where a decision gives one (parents merged elsewhere replaced by
    the one that remains), the merged ones left out. Each kept organisation lists the ones
    merged into it in ``merged_from``, and says what was decided in ``decided`` (the
    fields people set)."""
    roots = org_roots(rows)
    merged_from: dict[str, list[str]] = {}
    for oid, root in roots.items():
        merged_from.setdefault(root, []).append(oid)
    out = []
    for org in orgs:
        oid = org["org_id"]
        if oid in roots:
            continue
        row = rows.get(oid) or {}
        o = dict(org)
        decided = []
        if row.get("name"):
            o["name"] = row["name"]
            decided.append("name")
        if row.get("level"):
            o["level"] = row["level"]
            decided.append("level")
        parents = list(o.get("parents") or [])
        if row.get("parents"):
            parents = [p for p in row["parents"].split(";") if p]
            decided.append("parents")
        o["parents"] = list(
            dict.fromkeys(roots.get(p, p) for p in parents if roots.get(p, p) != oid)
        )
        o["merged_from"] = sorted(merged_from.get(oid, []))
        o["decided"] = decided
        out.append(o)
    return out


def effective_affiliations(
    affiliations: Iterable[Mapping[str, Any]],
    decided: Iterable[Mapping[str, str]],
    roots: Mapping[str, str],
    person_roots: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The affiliations as people decided them: the tables' (each organisation merged
    elsewhere read as the one that remains), less those removed, with those added
    (``source``: :data:`AFFILIATION_SOURCE`). With *person_roots*, a person merged into
    another is read as that person."""
    person_roots = person_roots or {}
    removed: set[tuple[str, str]] = set()
    removed_from: set[tuple[str, str, int]] = set()
    added: list[dict[str, Any]] = []
    for row in decided:
        pid = person_roots.get(row["person_id"], row["person_id"])
        oid = roots.get(row["org_id"], row["org_id"])
        start = int(row["start_year"]) if row.get("start_year") else None
        end = int(row["end_year"]) if row.get("end_year") else None
        if row["action"] == "remove":
            if start is None:
                removed.add((pid, oid))
            else:
                removed_from.add((pid, oid, start))
        elif row["action"] == "add":
            added.append({"person_id": pid, "org_id": oid, "start_year": start,
                          "end_year": end, "source": AFFILIATION_SOURCE})  # fmt: skip
    out = []
    for a in affiliations:
        pid = person_roots.get(a["person_id"], a["person_id"])
        oid = roots.get(a["org_id"], a["org_id"])
        if (pid, oid) in removed or (pid, oid, a.get("start_year")) in removed_from:
            continue
        out.append({**a, "person_id": pid, "org_id": oid})
    return out + added
