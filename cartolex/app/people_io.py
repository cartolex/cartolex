# SPDX-License-Identifier: MIT
"""People of a project: reading the tables and decisions, writing roles.

The people of a project are rows of ``sources/tables/people.parquet`` (what a
source says: names, identifiers, extra columns) and rows of
``decisions/people.csv`` (what people decided: role, set, identity, records,
merges). This module reads both into one view and writes decisions with the
guarded writer; :mod:`cartolex.app.importing` imports lists.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from cartolex.project import Project
from cartolex.project.files import fingerprint, write_decision
from cartolex.project.tables import (
    decision_csv_bytes,
    read_decision_csv,
    read_source_table,
)

__all__ = [
    "coverage_of",
    "decided_now",
    "people_rows",
    "read_people",
    "write_people_csv",
]


def decided_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).casefold()
    return "".join(c for c in text if not unicodedata.combining(c)).strip()


# ── reading ──────────────────────────────────────────────────────────────────


def people_rows(project: Project) -> list[dict[str, Any]]:
    """The rows of ``people.parquet`` (none when the table does not exist)."""
    path = project.layout.table("people")
    if not path.exists():
        return []
    return read_source_table(path, "people").to_pylist()


def _ids(value: Any) -> dict[str, list[str]]:
    if not value:
        return {}
    items = value.items() if isinstance(value, Mapping) else value
    return {str(k): list(v or []) for k, v in items}


def _map(value: Any) -> dict[str, str]:
    if not value:
        return {}
    items = value.items() if isinstance(value, Mapping) else value
    return {str(k): ("" if v is None else str(v)) for k, v in items}


def coverage_of(project: Project) -> dict[str, dict[str, Any]]:
    """Per person: texts, texts with an abstract, first and last year."""
    layout = project.layout
    if not layout.table("authorships").exists() or not layout.table("texts").exists():
        return {}
    texts = read_source_table(layout.table("texts"), "texts", ["text_id", "year"]).to_pydict()
    year_of = dict(zip(texts["text_id"], texts["year"], strict=True))
    abstracts: set[str] = set()
    if layout.table("text_parts").exists():
        parts = read_source_table(layout.table("text_parts"), "text_parts", ["text_id", "part"])
        abstracts = {
            t
            for t, p in zip(parts["text_id"].to_pylist(), parts["part"].to_pylist(), strict=True)
            if p in ("abstract", "body", "full")
        }
    auth = read_source_table(layout.table("authorships"), "authorships", ["text_id", "person_id"])
    out: dict[str, dict[str, Any]] = {}
    for tid, pid in zip(auth["text_id"].to_pylist(), auth["person_id"].to_pylist(), strict=True):
        entry = out.setdefault(
            pid, {"texts": 0, "with_abstract": 0, "first_year": None, "last_year": None}
        )
        entry["texts"] += 1
        entry["with_abstract"] += int(tid in abstracts)
        year = year_of.get(tid)
        if year is not None:
            entry["first_year"] = min(year, entry["first_year"] or year)
            entry["last_year"] = max(year, entry["last_year"] or year)
    return out


def coverage_class(entry: Mapping[str, Any] | None) -> str:
    """``none`` (no text), ``thin`` (fewer than 3 texts, or none with an abstract), ``good``."""
    if not entry or not entry.get("texts"):
        return "none"
    if entry["texts"] < 3 or not entry.get("with_abstract"):
        return "thin"
    return "good"


def _units(project: Project) -> dict[str, str]:
    layout = project.layout
    if not layout.table("affiliations").exists() or not layout.table("organisations").exists():
        return {}
    orgs = read_source_table(layout.table("organisations"), "organisations").to_pylist()
    level = project.config.levels[0].id if project.config.levels else None
    label = {
        o["org_id"]: o["acronym"] or o["name"]
        for o in orgs
        if level is None or o["level"] in (level, None)
    }
    out: dict[str, str] = {}
    for aff in read_source_table(layout.table("affiliations"), "affiliations").to_pylist():
        if aff["org_id"] in label and aff["end_year"] is None:
            out.setdefault(aff["person_id"], label[aff["org_id"]])
    return out


def _stamp(project: Project) -> tuple[Any, ...]:
    """What the tables' view depends on: each table's size and modification time."""
    out: list[Any] = [str(project.layout.root)]
    for name in ("people", "texts", "text_parts", "authorships", "affiliations", "organisations"):
        path = project.layout.table(name)
        try:
            st = path.stat()
            out.append((name, st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            out.append((name, None))
    return tuple(out)


def read_people(project: Project, cache: Any = None) -> tuple[list[dict[str, Any]], str | None]:
    """Every person: the table's row joined with the decision, and ``people.csv``'s fingerprint.

    *cache* (an app's :class:`~cartolex.app.runtime.Cache`) keeps the tables'
    part between calls while the tables do not change.

    Without ``people.csv`` everyone is ``mapped`` (as the build reads it); a
    person with no row in it once the file exists is ``undecided``.
    """
    layout = project.layout
    decisions = {r["person_id"]: r for r in read_decision_csv(layout.people_csv, "people")}
    file_exists = layout.people_csv.exists()
    if cache is not None:  # the tables change only when people are imported or collected
        rows_, coverage, units = cache.get(
            ("people", _stamp(project)),
            lambda: (people_rows(project), coverage_of(project), _units(project)),
        )
        rows_ = [dict(r) for r in rows_]
    else:
        rows_, coverage, units = people_rows(project), coverage_of(project), _units(project)
    out = []
    rows = rows_
    known = {r["person_id"] for r in rows}
    # A decision whose person left the sources is listed, never dropped silently.
    for pid in sorted(set(decisions) - known):
        rows.append(
            {
                "person_id": pid,
                "last_name": "",
                "first_name": "",
                "orcid": None,
                "ids": None,
                "columns": None,
                "source": "",
            }
        )
    for row in rows:
        pid = row["person_id"]
        d = decisions.get(pid)
        cov = coverage.get(pid)
        out.append(
            {
                "person_id": pid,
                "last_name": row["last_name"],
                "first_name": row["first_name"] or "",
                "orcid": row["orcid"],
                "ids": _ids(row["ids"]),
                "columns": _map(row["columns"]),
                "unit": units.get(pid, ""),
                "source": row["source"],
                "role": (d["role"] if d else ("undecided" if file_exists else "mapped"))
                or "undecided",
                "set": d["set"] if d else "",
                "identity": (d["identity"] if d else "") or "pending",
                "records": [r for r in (d["records"] if d else "").split(";") if r],
                "merged_into": d["merged_into"] if d else "",
                "note": d["note"] if d else "",
                "decided_at": d["decided_at"] if d else "",
                "coverage": {
                    **(cov or {"texts": 0, "with_abstract": 0}),
                    "class": coverage_class(cov),
                },
                "missing_source": pid not in known,
            }
        )
    return out, fingerprint(layout.people_csv)


# ── writing decisions ────────────────────────────────────────────────────────


def write_people_csv(
    project: Project,
    changes: Mapping[str, Mapping[str, str]],
    *,
    expected: str | None,
    action: str,
) -> str:
    """Apply *changes* (person id → columns) to ``people.csv``, guarded by *expected*.

    When the file does not exist yet, every person of the table gets a row with
    the role they have now (``mapped``), so writing one decision never changes
    the others. Returns the new fingerprint.
    """
    layout = project.layout
    now = decided_now()
    if layout.people_csv.exists():
        rows = {r["person_id"]: r for r in read_decision_csv(layout.people_csv, "people")}
    else:
        rows = {
            p["person_id"]: {
                "person_id": p["person_id"],
                "role": "mapped",
                "set": "",
                "identity": "",
                "records": "",
                "merged_into": "",
                "note": "",
                "decided_at": now,
            }
            for p in people_rows(project)
        }
    for pid, values in changes.items():
        row = rows.setdefault(
            pid,
            {
                "person_id": pid,
                "role": "undecided",
                "set": "",
                "identity": "",
                "records": "",
                "merged_into": "",
                "note": "",
                "decided_at": now,
            },
        )
        row.update({k: ("" if v is None else str(v)) for k, v in values.items()})
        row["decided_at"] = now
    data = decision_csv_bytes("people", list(rows.values()))
    return write_decision(layout, layout.people_csv, data, expected=expected, action=action)
