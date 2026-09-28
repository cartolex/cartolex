# SPDX-License-Identifier: MIT
"""People of a project: reading the tables and decisions, importing a list, writing roles.

The people of a project are rows of ``sources/tables/people.parquet`` (what a
source says: names, identifiers, extra columns) and rows of
``decisions/people.csv`` (what people decided: role, set, identity, records,
merges). This module reads both into one view, turns an uploaded list into a
mapping proposal and then into people, and writes decisions with the guarded
writer.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa

from cartolex.project import Project
from cartolex.project.files import fingerprint, write_decision
from cartolex.project.tables import (
    SOURCE_SCHEMAS,
    decision_csv_bytes,
    read_decision_csv,
    read_source_table,
    write_source_table,
)

__all__ = [
    "FIELDS",
    "ParsedList",
    "coverage_of",
    "decided_now",
    "import_people",
    "parse_list",
    "people_rows",
    "propose_mapping",
    "read_people",
    "write_people_csv",
]

#: What a column of an imported list can be: a person's field, an extra column kept as a
#: filter (``column``), or nothing (``ignore``).
FIELDS = (
    "last_name",
    "first_name",
    "name",
    "orcid",
    "email",
    "unit",
    "column",
    "ignore",
)
MAX_ROWS = 100_000

_HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "last_name": (
        "last name",
        "lastname",
        "surname",
        "family name",
        "nom",
        "sobrenome",
        "apellido",
    ),
    "first_name": ("first name", "firstname", "given name", "forename", "prénom", "prenom", "nome"),
    "name": ("name", "full name", "nom complet", "nome completo", "person", "personne"),
    "orcid": ("orcid", "orcid id", "orcid ids"),
    "email": ("email", "e-mail", "mail", "courriel"),
    "unit": (
        "unit",
        "lab",
        "laboratory",
        "laboratoire",
        "team",
        "équipe",
        "equipe",
        "affiliation",
        "organisation",
        "organization",
        "department",
        "unité",
        "unite",
    ),
}
_ORCID = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])")


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


def read_people(project: Project) -> tuple[list[dict[str, Any]], str | None]:
    """Every person: the table's row joined with the decision, and ``people.csv``'s fingerprint.

    Without ``people.csv`` everyone is ``mapped`` (as the build reads it); a
    person with no row in it once the file exists is ``undecided``.
    """
    layout = project.layout
    decisions = {r["person_id"]: r for r in read_decision_csv(layout.people_csv, "people")}
    file_exists = layout.people_csv.exists()
    coverage = coverage_of(project)
    units = _units(project)
    out = []
    rows = people_rows(project)
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


# ── importing a list ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedList:
    """An uploaded or pasted list: its columns and rows (text), and how it was read."""

    columns: list[str]
    rows: list[list[str]]
    kind: str  # "table" (a CSV with a header) or "lines" (one person per line)
    warnings: list[str] = field(default_factory=list)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_list(data: bytes | str) -> ParsedList:
    """Read a list of people: a CSV (or tab/semicolon separated) with a header, or lines."""
    text = _decode(data) if isinstance(data, bytes) else data
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ParsedList([], [], "lines", ["the list is empty"])
    sample = "\n".join(lines[:20])
    header = lines[0]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t")
        has_header = csv.Sniffer().has_header(sample) or any(
            _guess(h) not in ("column", "ignore") for h in next(csv.reader([header], dialect))
        )
    except csv.Error:
        dialect, has_header = None, False
    warnings: list[str] = []
    if dialect is not None and has_header:
        reader = csv.reader(io.StringIO(text), dialect)
        rows = [r for r in reader if any(c.strip() for c in r)]
        columns = [c.strip() or f"column {i + 1}" for i, c in enumerate(rows[0])]
        body = [[c.strip() for c in r] + [""] * (len(columns) - len(r)) for r in rows[1:]]
        if len(body) > MAX_ROWS:
            warnings.append(f"only the first {MAX_ROWS} rows are read")
        return ParsedList(columns, [r[: len(columns)] for r in body[:MAX_ROWS]], "table", warnings)
    if len(lines) > MAX_ROWS:
        warnings.append(f"only the first {MAX_ROWS} lines are read")
    return ParsedList(["name"], [[line.strip()] for line in lines[:MAX_ROWS]], "lines", warnings)


def _guess(header: str) -> str:
    h = _fold(header).replace("_", " ").replace("-", " ").strip()
    for fld, hints in _HEADER_HINTS.items():
        if h in {_fold(x).replace("-", " ") for x in hints}:
            return fld
    return "column"


def propose_mapping(parsed: ParsedList) -> dict[str, str]:
    """Column → field (:data:`FIELDS`), guessed from the header names."""
    if parsed.kind == "lines":
        return {"name": "name"}
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for column in parsed.columns:
        guess = _guess(column)
        if guess not in ("column", "ignore") and guess in taken:
            guess = "column"
        taken.add(guess)
        mapping[column] = guess
    return mapping


def _split_name(value: str) -> tuple[str, str]:
    """``Last, First`` or ``First Last`` → (last, first)."""
    value = " ".join(value.split())
    if "," in value:
        last, _, first = value.partition(",")
        return last.strip(), first.strip()
    parts = value.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def _people_of(parsed: ParsedList, mapping: Mapping[str, str]) -> tuple[list[dict], list[str]]:
    bad = sorted({v for v in mapping.values() if v not in FIELDS})
    if bad:
        raise ValueError(f"unknown field(s) {bad}; known: {list(FIELDS)}")
    unknown = sorted(set(mapping) - set(parsed.columns))
    if unknown:
        raise ValueError(f"the list has no column(s) {unknown}")
    fields = set(mapping.values())
    if not ({"last_name", "name"} & fields):
        raise ValueError("map a column to last_name, or to name (a full name)")
    people, skipped = [], []
    index = {c: i for i, c in enumerate(parsed.columns)}
    for n, row in enumerate(parsed.rows, start=1):
        person: dict[str, Any] = {"columns": {}}
        for column, fld in mapping.items():
            value = row[index[column]].strip() if index[column] < len(row) else ""
            if fld == "ignore" or not value:
                continue
            if fld == "column":
                person["columns"][column] = value
            elif fld == "name":
                person["last_name"], person["first_name"] = _split_name(value)
            elif fld == "orcid":
                m = _ORCID.search(value.upper())
                if m:
                    person["orcid"] = m.group(1)
                else:
                    person["columns"][column] = value
            else:
                person[fld] = value
        if not person.get("last_name"):
            skipped.append(f"row {n}: no name")
            continue
        people.append(person)
    return people, skipped


def import_people(
    project: Project,
    parsed: ParsedList,
    mapping: Mapping[str, str],
    *,
    role: str = "mapped",
    set_id: str = "",
    expected_people: str | None,
) -> dict[str, Any]:
    """Add the people of *parsed* to the project, with *role*; returns what was done.

    A person already in the project (the same ORCID iD, or the same names) is
    not added twice. A ``unit`` becomes an organisation (one per distinct
    name) and a current affiliation. Every added person gets a row in
    ``people.csv`` (identity ``pending``), guarded by *expected_people*.
    """
    if role not in ("mapped", "context", "projected", "excluded", "undecided"):
        raise ValueError(f"unknown role {role!r}")
    people, skipped = _people_of(parsed, mapping)
    layout = project.layout
    existing = people_rows(project)
    by_orcid = {r["orcid"]: r["person_id"] for r in existing if r["orcid"]}
    by_name = {
        (_fold(r["last_name"]), _fold(r["first_name"] or "")): r["person_id"] for r in existing
    }
    numbers = [int(m.group(1)) for r in existing if (m := re.fullmatch(r"p(\d+)", r["person_id"]))]
    next_n = max(numbers, default=0) + 1
    now = datetime.now(timezone.utc)
    added, known = [], []
    new_rows: list[dict[str, Any]] = []
    units: dict[str, list[str]] = {}
    for person in people:
        key = (_fold(person["last_name"]), _fold(person.get("first_name", "")))
        pid = by_orcid.get(person.get("orcid") or "") or by_name.get(key)
        if pid is not None:
            known.append(pid)
            continue
        pid = f"p{next_n:04d}"
        next_n += 1
        by_name[key] = pid
        if person.get("orcid"):
            by_orcid[person["orcid"]] = pid
        ids = [("email", [person["email"]])] if person.get("email") else []
        new_rows.append(
            {
                "person_id": pid,
                "last_name": person["last_name"],
                "first_name": person.get("first_name") or None,
                "orcid": person.get("orcid"),
                "ids": ids,
                "source": "import",
                "columns": sorted(person["columns"].items()),
                "aliases": [],
                "retrieved_at": now,
            }
        )
        if person.get("unit"):
            units.setdefault(person["unit"], []).append(pid)
        added.append(pid)
    if new_rows:
        table = pa.Table.from_pylist(
            _normalise(existing) + new_rows, schema=SOURCE_SCHEMAS["people"]
        )
        write_source_table(layout.table("people"), "people", table)
    if units:
        _add_units(project, units, now)
    fp = expected_people
    if added:
        fp = write_people_csv(
            project,
            {
                pid: {
                    "role": role,
                    "set": set_id if role == "projected" else "",
                    "identity": "pending",
                }
                for pid in added
            },
            expected=expected_people,
            action=f"import {len(added)} people",
        )
    return {"added": added, "already_known": known, "skipped": skipped, "people_version": fp}


def _normalise(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        r = dict(r)
        for key in ("ids", "columns"):
            value = r.get(key)
            if isinstance(value, Mapping):
                r[key] = list(value.items())
        out.append(r)
    return out


def _add_units(project: Project, units: Mapping[str, Sequence[str]], now: datetime) -> None:
    layout = project.layout
    orgs = (
        read_source_table(layout.table("organisations"), "organisations").to_pylist()
        if layout.table("organisations").exists()
        else []
    )
    affs = (
        read_source_table(layout.table("affiliations"), "affiliations").to_pylist()
        if layout.table("affiliations").exists()
        else []
    )
    by_name = {_fold(o["name"]): o["org_id"] for o in orgs}
    numbers = [int(m.group(1)) for o in orgs if (m := re.fullmatch(r"o(\d+)", o["org_id"]))]
    next_n = max(numbers, default=0) + 1
    level = project.config.levels[0].id if project.config.levels else None
    for name, pids in units.items():
        org_id = by_name.get(_fold(name))
        if org_id is None:
            org_id = f"o{next_n:04d}"
            next_n += 1
            by_name[_fold(name)] = org_id
            orgs.append(
                {
                    "org_id": org_id,
                    "name": name,
                    "level": level,
                    "parents": [],
                    "ids": [],
                    "source": "import",
                    "retrieved_at": now,
                }
            )
        for pid in pids:
            affs.append({"person_id": pid, "org_id": org_id, "source": "import"})
    write_source_table(
        layout.table("organisations"),
        "organisations",
        pa.Table.from_pylist(_normalise(orgs), schema=SOURCE_SCHEMAS["organisations"]),
    )
    write_source_table(
        layout.table("affiliations"),
        "affiliations",
        pa.Table.from_pylist(affs, schema=SOURCE_SCHEMAS["affiliations"]),
    )
