# SPDX-License-Identifier: MIT
"""Importing a list of people through the collection's importer, one column at a time.

The interface edits a mapping as one field per column (``last_name``, ``org:lab``,
``column``…); :mod:`cartolex.collect.people_import` reads a list with an
:class:`~cartolex.collect.people_import.ImportMapping`. This module turns one
into the other, keeps an uploaded list until it is confirmed, and imports it:
the list's rows become raw records, the tables are rebuilt, new people get a
row in ``decisions/people.csv``, and possible duplicates are proposed (never
merged).

A pasted list with one person per line (``Last, First`` or ``First Last``)
is read as one column of full names.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import ApiError
from .uploads import clean_name

if TYPE_CHECKING:
    from cartolex.project import Project

__all__ = [
    "BASE_FIELDS",
    "confirm_list",
    "fields_of",
    "mapping_from_fields",
    "propose_list",
]

#: The fields a column can be, besides ``org:<level>`` (an organisation at that level).
BASE_FIELDS = (
    "last_name",
    "first_name",
    "name",
    "orcid",
    "openalex",
    "idhal",
    "role",
    "set",
    "column",
    "ignore",
)
#: Older names still read: ``unit`` is an organisation at the smallest level; ``email`` is
#: never stored.
_ALIASES = {"email": "ignore"}
#: Why a column is refused, as a code the interface words.
_REFUSED = {"e-mail": "refused_email"}
_ROLES = ("mapped", "context", "projected", "excluded", "undecided")


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _names_per_line(text: str) -> list[str] | None:
    """The names of a pasted list with one person per line, or ``None`` for a table.

    A line holds no tab or semicolon and at most one comma (``Last, First``); a
    first line that is a header (``Name``) is left out, unless it has a comma: then
    the list is a CSV table with a header.
    """
    from cartolex.collect.people_import import propose_mapping, read_list

    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    if not lines or any("\t" in line or ";" in line or line.count(",") > 1 for line in lines):
        return None
    rows, _ = read_list(lines[0])
    if propose_mapping(rows).has_header:
        return None if "," in lines[0] else (lines[1:] or None)
    return lines


def _as_name_column(names: Sequence[str]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["name"])
    for name in names:
        writer.writerow([name])
    return out.getvalue()


def fields_of(mapping: Any) -> dict[str, str]:
    """One field per column of an :class:`ImportMapping`."""
    out = {c: "ignore" for c in mapping.columns}
    for column in mapping.filters:
        out[column] = "column"
    for column, level in mapping.organisations.items():
        out[column] = f"org:{level}"
    for scheme, column in mapping.ids.items():
        out[column] = scheme
    for key, fld in (
        ("full_name", "name"),
        ("last_name", "last_name"),
        ("first_name", "first_name"),
        ("role", "role"),
        ("set", "set"),
    ):
        column = getattr(mapping, key)
        if column:
            out[column] = fld
    for column in mapping.refused:
        out[column] = "ignore"
    return out


def mapping_from_fields(
    columns: Sequence[str],
    has_header: bool,
    fields: Mapping[str, str],
    levels: Sequence[str],
    *,
    projected_set: str | None = None,
) -> Any:
    """An :class:`ImportMapping` from one field per column; refused with an :class:`ApiError`."""
    from cartolex.collect.people_import import ImportMapping

    fields = {c: _ALIASES.get(f, f) for c, f in fields.items()}
    known = set(BASE_FIELDS) | {"unit"}
    bad = sorted({f for f in fields.values() if f not in known and not f.startswith("org:")})
    if bad:
        raise ApiError.of("mapping_unknown_fields", fields=bad, known=[*BASE_FIELDS, "org:<level>"])
    unknown = sorted(set(fields) - set(columns))
    if unknown:
        raise ApiError.of("mapping_unknown_columns", columns=unknown)
    mapping = ImportMapping(columns=list(columns), has_header=has_header)
    for column in columns:
        fld = fields.get(column, "ignore")
        if fld in ("last_name", "first_name", "role", "set"):
            if getattr(mapping, fld) is None:
                setattr(mapping, fld, column)
            else:
                mapping.filters.append(column)
        elif fld == "name":
            mapping.full_name = mapping.full_name or column
        elif fld in ("orcid", "openalex", "idhal"):
            mapping.ids.setdefault(fld, column)
        elif fld == "unit":
            mapping.organisations[column] = levels[0] if levels else "unit"
        elif fld.startswith("org:"):
            level = fld.split(":", 1)[1].strip()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", level):
                raise ApiError.of("mapping_unknown_fields", fields=[fld], known=list(BASE_FIELDS))
            mapping.organisations[column] = level
        elif fld == "column":
            mapping.filters.append(column)
        else:
            mapping.ignored.append(column)
    if mapping.full_name and mapping.last_name:
        mapping.full_name = None
    if not (mapping.full_name or mapping.last_name):
        raise ApiError.of("mapping_no_name")
    mapping.projected_set = projected_set or None
    return mapping


def propose_list(project: Project, folder: Path, filename: str, data: bytes) -> dict[str, Any]:
    """Keep an uploaded list in *folder* and propose one field per column."""
    from cartolex.collect.people_import import propose_mapping, read_list

    text = _decode(data)
    kind = "table"
    names = _names_per_line(text)
    if names is not None:
        text, kind = _as_name_column(names), "lines"
    rows, delimiter = read_list(text)
    if not rows or not any(any(c for c in r) for r in rows):
        raise ApiError.of("empty_list")
    levels = project.config.levels
    mapping = propose_mapping(rows, levels=levels)
    body = rows[1:] if mapping.has_header else rows
    if not body:
        raise ApiError.of("empty_list")
    folder.mkdir(parents=True, exist_ok=True)
    name = clean_name(filename, default="list.csv")
    (folder / "raw").mkdir(exist_ok=True)
    (folder / "raw" / name).write_text(text, encoding="utf-8")
    level_ids = [lv.id for lv in levels]
    new_levels = [lv for lv in mapping.organisations.values() if lv not in level_ids]
    proposal = {
        "import_id": folder.name,
        "file": name,
        "kind": kind,
        "separator": {"\t": "tab", ",": "comma", ";": "semicolon"}[delimiter],
        "has_header": mapping.has_header,
        "columns": mapping.columns,
        "rows": len(body),
        "preview": body[:10],
        "mapping": fields_of(mapping),
        "fields": [
            *BASE_FIELDS[:8],
            *(f"org:{lv}" for lv in level_ids + new_levels),
            *BASE_FIELDS[8:],
        ],  # fmt: skip
        "levels": [{"id": lv.id, "names": dict(lv.names)} for lv in levels]
        + [{"id": lv, "names": {}, "new": True} for lv in dict.fromkeys(new_levels)],
        "refused": {
            c: {"code": "refused_email", "message": why} for c, why in mapping.refused.items()
        },
        "warnings": [],
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (folder / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
    return proposal


def confirm_list(
    project: Project,
    folder: Path,
    fields: Mapping[str, str],
    *,
    role: str,
    set_id: str,
) -> dict[str, Any]:
    """Import the list kept in *folder* with one field per column; returns what was done."""
    from cartolex.collect.decisions import read_people, update_people
    from cartolex.collect.people_import import import_people

    if role not in _ROLES:
        raise ApiError.of("unknown_role", role=role)
    raw = folder / "raw"
    files = sorted(raw.iterdir()) if raw.is_dir() else []
    meta = folder / "proposal.json"
    if not files or not meta.exists():
        raise ApiError.of("import_not_found")
    proposal = json.loads(meta.read_text(encoding="utf-8"))
    levels = [lv.id for lv in project.config.levels]
    mapping = mapping_from_fields(
        proposal["columns"],
        proposal["has_header"],
        fields,
        levels,
        projected_set=set_id if role == "projected" and set_id else None,
    )
    if role == "projected" and not set_id and not mapping.set:
        raise ApiError.of("set_needed")
    before = set(read_people(project.layout))
    report = import_people(project, files[0].read_text(encoding="utf-8"), mapping=mapping)
    decisions = read_people(project.layout)
    added = sorted(set(decisions) - before)
    if role not in ("mapped", "projected") and added and not mapping.role:
        update_people(
            project.layout,
            {pid: {"role": role} for pid in added},
            action=f"role {role} for {len(added)} imported people",
        )
    from cartolex.project.files import fingerprint

    names = _names(project)
    return {
        "added": added,
        "already_known": report.people_known,
        "skipped": [f"{where}: {why}" for where, why in report.refused],
        "notes": report.notes,
        "duplicates": [
            {
                "person_id": d.person_id,
                "other_id": d.other_id,
                "reason": d.reason,
                "names": [names.get(d.person_id, ""), names.get(d.other_id, "")],
            }
            for d in report.duplicates
        ],
        "people_version": fingerprint(project.layout.people_csv),
    }


def _names(project: Project) -> dict[str, str]:
    from cartolex.project.tables import read_source_table

    path = project.layout.table("people")
    if not path.exists():
        return {}
    table = read_source_table(path, "people", ["person_id", "last_name", "first_name"])
    return {
        pid: " ".join(x for x in (first, last) if x)
        for pid, last, first in zip(
            table["person_id"].to_pylist(),
            table["last_name"].to_pylist(),
            table["first_name"].to_pylist(),
            strict=True,
        )
    }
