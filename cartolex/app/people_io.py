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


def coverage_of(
    project: Project, columns: Any = None, merged: Mapping[str, str] | None = None
) -> dict[str, dict[str, Any]]:
    """Per person: texts, texts with an abstract, first and last year (from the texts as
    columns: a few numbers per text, a code per person; *columns*: a function giving
    them, when the caller shares them). With *merged* (each merged row → the person it is
    merged into), a person counts the texts of the rows merged into them too, each text
    once, and a merged row counts none of its own."""
    import numpy as np

    from cartolex.project.text_columns import read_text_columns

    layout = project.layout
    if not layout.table("authorships").exists() or not layout.table("texts").exists():
        return {}
    cols = columns() if columns is not None else read_text_columns(layout)
    n = len(cols.person_ids)
    if not n:
        return {}
    who, rows = cols.author_person.astype(np.int64), cols.author_text
    if merged:
        code = {pid: i for i, pid in enumerate(cols.person_ids)}
        remap = np.arange(n, dtype=np.int64)
        for pid, root in merged.items():
            if pid in code and root in code:
                remap[code[pid]] = code[root]
        width = max(int(len(cols.year)), 1)
        pairs = np.unique(remap[who] * width + rows.astype(np.int64))
        who, rows = pairs // width, pairs % width
    texts = np.bincount(who, minlength=n)
    abstracts = np.bincount(who, weights=cols.has_words()[rows], minlength=n).astype(np.int64)
    dated = cols.has_year[rows]
    years = cols.year[rows][dated].astype(np.int64)
    none_first, none_last = np.iinfo(np.int64).max, np.iinfo(np.int64).min
    first = np.full(n, none_first, dtype=np.int64)
    last = np.full(n, none_last, dtype=np.int64)
    np.minimum.at(first, who[dated], years)
    np.maximum.at(last, who[dated], years)
    return {
        pid: {
            "texts": int(texts[i]),
            "with_abstract": int(abstracts[i]),
            "first_year": int(first[i]) if first[i] != none_first else None,
            "last_year": int(last[i]) if last[i] != none_last else None,
        }
        for i, pid in enumerate(cols.person_ids)
        if not merged or pid not in merged
    }


def coverage_class(entry: Mapping[str, Any] | None) -> str:
    """``none`` (no text), ``thin`` (fewer than 3 texts, or none with an abstract), ``good``."""
    if not entry or not entry.get("texts"):
        return "none"
    if entry["texts"] < 3 or not entry.get("with_abstract"):
        return "thin"
    return "good"


def _units(project: Project) -> dict[str, str]:
    """Each person's current organisation at the project's first level, as people decided
    the organisations and affiliations."""
    from cartolex.project.organisations import effective_organisations, org_decisions

    from .corpus_view import effective_affiliation_table

    layout = project.layout
    if not layout.table("affiliations").exists() or not layout.table("organisations").exists():
        return {}
    orgs = effective_organisations(
        read_source_table(layout.table("organisations"), "organisations").to_pylist(),
        org_decisions(layout),
    )
    level = project.config.levels[0].id if project.config.levels else None
    label = {
        o["org_id"]: o["acronym"] or o["name"]
        for o in orgs
        if level is None or o["level"] in (level, None)
    }
    import pyarrow as pa
    import pyarrow.compute as pc

    aff = effective_affiliation_table(project, ["end_year"])
    aff = aff.filter(
        pc.and_(
            pc.is_null(aff["end_year"]),
            pc.is_in(aff["org_id"], value_set=pa.array(sorted(label), pa.string())),
        )
    )
    out: dict[str, str] = {}
    for pid, oid in zip(aff["person_id"].to_pylist(), aff["org_id"].to_pylist(), strict=True):
        out.setdefault(pid, label[oid])
    return out


def _stamp(project: Project) -> tuple[Any, ...]:
    """What the tables' view depends on: each table's size and modification time, and the
    decisions on organisations and affiliations (the people's units)."""
    from cartolex.project.organisations import decisions_stamp

    out: list[Any] = [str(project.layout.root), *decisions_stamp(project.layout)]
    for name in ("people", "texts", "text_parts", "authorships", "affiliations", "organisations"):
        path = project.layout.table(name)
        try:
            st = path.stat()
            out.append((name, st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            out.append((name, None))
    return tuple(out)


def read_people(
    project: Project, cache: Any = None, columns: Any = None
) -> tuple[list[dict[str, Any]], str | None]:
    """Every person: the table's row joined with the decision, and ``people.csv``'s fingerprint.

    *cache* (an app's :class:`~cartolex.app.runtime.Cache`) keeps the tables'
    part between calls while the tables do not change; *columns*: as
    :func:`coverage_of` takes them.

    Without ``people.csv`` everyone is ``mapped`` (as the build reads it); a
    person with no row in it once the file exists is ``undecided``.
    """
    from cartolex.project.identity import merge_roots, merged_groups, merges_digest

    layout = project.layout
    decisions = {r["person_id"]: r for r in read_decision_csv(layout.people_csv, "people")}
    file_exists = layout.people_csv.exists()
    roots = merge_roots(decisions)
    groups = merged_groups(roots)
    if cache is not None:  # the tables change only when people are imported or collected
        rows_, coverage, units = cache.get(
            ("people", _stamp(project), merges_digest(layout.people_csv)),
            lambda: (
                people_rows(project),
                coverage_of(project, columns, roots),
                _units(project),
            ),
        )
        rows_ = [dict(r) for r in rows_]
    else:
        rows_, coverage, units = (
            people_rows(project),
            coverage_of(project, columns, roots),
            _units(project),
        )
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
                "unit": units.get(pid, "")
                or next((units[m] for m in groups.get(pid, ()) if units.get(m)), ""),
                "source": row["source"],
                "role": (d["role"] if d else ("undecided" if file_exists else "mapped"))
                or "undecided",
                "set": d["set"] if d else "",
                "identity": (d["identity"] if d else "") or "pending",
                "records": [r for r in (d["records"] if d else "").split(";") if r],
                "merged_into": d["merged_into"] if d else "",
                "merged_from": groups.get(pid, []),
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
