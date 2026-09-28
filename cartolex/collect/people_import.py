# SPDX-License-Identifier: MIT
"""Ways people and texts come in without a service: a list, a folder of documents, a corpus.

* :func:`import_people` reads a CSV file or a pasted list (tab, semicolon or
  comma separated, with or without a header) through an :class:`ImportMapping`:
  name columns, identifiers, the role or projected set, organisation columns
  that become levels, and every other column kept as a filter. E-mail
  addresses are refused as identifiers and never stored.
  :func:`propose_mapping` computes a mapping from the header and the values.
* :func:`import_folder` reads a folder of documents (PDF, text), each file on
  its own, and matches each to a person by a name in the file name or a
  sub-folder per person.
* :func:`import_corpus` imports a corpus in the engine's corpus contract
  (an index CSV and text files).

Each stores what it read in the slot's raw folder and rebuilds the tables;
each reports what it did. Duplicates are proposed (:func:`find_duplicates`),
never merged: :func:`confirm_merge` records a merge someone confirmed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cartolex.project import Project
from cartolex.project.models import Level, Slot
from cartolex.project.tables import read_source_table

from .decisions import read_people, update_people
from .names import compatible_first_names, fold, name_key, split_full_name, surname_parts, words
from .tables import RawRun, RawWriter, SourceBuilder, iso, parse_time, rebuild_sources
from .text import clean, detect_language

__all__ = [
    "DuplicateProposal",
    "ImportMapping",
    "ImportReport",
    "confirm_merge",
    "find_duplicates",
    "import_corpus",
    "import_folder",
    "import_people",
    "normalise_openalex_author",
    "normalise_orcid",
    "propose_mapping",
    "read_list",
]

ROLES = ("mapped", "context", "projected", "excluded")
_ROLE_WORDS = {
    "mapped": "mapped",
    "map": "mapped",
    "member": "mapped",
    "cohort": "mapped",
    "membre": "mapped",
    "membro": "mapped",
    "context": "context",
    "contexte": "context",
    "contexto": "context",
    "projected": "projected",
    "project": "projected",
    "projeté": "projected",
    "projete": "projected",
    "projetado": "projected",
    "overlay": "projected",
    "excluded": "excluded",
    "exclude": "excluded",
    "exclu": "excluded",
    "excluido": "excluded",
    "no": "excluded",
}
_ORCID = re.compile(r"(\d{4})-?(\d{4})-?(\d{4})-?(\d{3}[\dXx])")
_OPENALEX_AUTHOR = re.compile(r"(?:^|/|\b)(A\d{4,})\b", re.IGNORECASE)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")

# Header words (folded, letters and digits only) and what they mean.
_FULL = {"name", "fullname", "nomcomplet", "nomecompleto", "person", "author", "member", "membre"}
_LAST = {
    "lastname",
    "surname",
    "familyname",
    "family",
    "last",
    "nomdefamille",
    "sobrenome",
    "apellido",
    "apellidos",
    "nom",
}
_FIRST = {
    "firstname",
    "givenname",
    "givennames",
    "given",
    "first",
    "forename",
    "prenom",
    "prenoms",
    "nombre",
    "primeironome",
}
_ROLE = {"role", "roles", "papel", "papelnoprojeto"}
_SET = {"set", "projectedset", "overlay", "ensemble", "conjunto"}
_SMALL_ORGS = {
    "lab",
    "labs",
    "laboratory",
    "laboratoire",
    "laboratorio",
    "unit",
    "unite",
    "unidade",
    "team",
    "equipe",
    "group",
    "groupe",
    "grupo",
    "department",
    "departement",
    "departamento",
}
_LARGE_ORGS = {
    "institution",
    "institute",
    "institut",
    "instituto",
    "university",
    "universite",
    "universidade",
    "universidad",
    "affiliation",
    "organisation",
    "organization",
    "employer",
    "etablissement",
    "school",
    "faculty",
    "centre",
    "center",
}
_ID_SCHEMES = {"orcid": "orcid", "openalex": "openalex", "idhal": "idhal", "hal": "idhal"}


def _header_key(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", fold(name))


def normalise_orcid(text: str | None) -> str | None:
    """``0000-0000-0000-0000`` from an ORCID or a URL holding one, if its check digit is right."""
    if not text:
        return None
    m = _ORCID.search(text.strip())
    if not m:
        return None
    digits = "".join(m.groups()).upper()
    total = 0
    for d in digits[:15]:
        total = (total + int(d)) * 2
    check = (12 - total % 11) % 11
    if digits[15] != ("X" if check == 10 else str(check)):
        return None
    return "-".join(digits[i : i + 4] for i in range(0, 16, 4))


def normalise_openalex_author(text: str | None) -> str | None:
    """``A…`` from an OpenAlex author id or a URL holding one."""
    if not text:
        return None
    m = _OPENALEX_AUTHOR.search(text.strip())
    return m.group(1).upper() if m else None


# ── reading a list ───────────────────────────────────────────────────────────


def read_list(source: Path | str) -> tuple[list[list[str]], str]:
    """The rows of a CSV file or a pasted list, and the separator found.

    *source* is a path (a :class:`~pathlib.Path`) or the pasted text itself.
    The separator is a tab if the first line has one, else a semicolon if it
    has more of them than commas, else a comma.
    """
    if isinstance(source, Path):
        raw = source.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
    else:
        text = source
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return [], ","
    first = lines[0]
    delimiter = "\t" if "\t" in first else (";" if first.count(";") > first.count(",") else ",")
    rows = [
        [clean(c) for c in row]
        for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    ]
    width = max(len(r) for r in rows)
    return [r + [""] * (width - len(r)) for r in rows], delimiter


@dataclass
class ImportMapping:
    """How the columns of a list are read (see :func:`propose_mapping`).

    Column names are those of the header, or ``column 1``, ``column 2``… for a
    list without one. *organisations* maps a column to a level id, smallest
    level first; *projected_set* makes every row part of that projected set.
    """

    columns: list[str]
    has_header: bool = True
    full_name: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    ids: dict[str, str] = field(default_factory=dict)
    role: str | None = None
    set: str | None = None
    projected_set: str | None = None
    organisations: dict[str, str] = field(default_factory=dict)
    filters: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ImportMapping:
        known = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown mapping key(s): {unknown}; known: {sorted(known)}")
        return cls(**dict(data))

    def check(self) -> None:
        """Refuse a mapping that cannot be read: no name, an unknown column, an e-mail id."""
        if not (self.full_name or self.last_name):
            raise ValueError("the mapping names no name column (full_name or last_name)")
        used = [
            self.full_name,
            self.last_name,
            self.first_name,
            self.role,
            self.set,
            *self.ids.values(),
            *self.organisations,
            *self.filters,
        ]
        missing = sorted({c for c in used if c and c not in self.columns})
        if missing:
            raise ValueError(f"the mapping names column(s) the list does not have: {missing}")
        for scheme, column in self.ids.items():
            if scheme not in ("orcid", "openalex", "idhal"):
                raise ValueError(
                    f"{scheme!r} is not an identifier cartolex keeps (orcid, openalex, idhal); "
                    "an e-mail address is never an identifier"
                )
            if column in self.refused:
                raise ValueError(f"column {column!r} was refused: {self.refused[column]}")

    def describe(self) -> list[str]:
        """The mapping in words, one line per column."""
        lines = []
        roles = {
            self.full_name: "full name",
            self.last_name: "last name",
            self.first_name: "first name",
            self.role: "role",
            self.set: "projected set",
        }
        for col in self.columns:
            if col in roles and col is not None:
                what = roles[col]
            elif col in self.ids.values():
                what = "identifier: " + next(k for k, v in self.ids.items() if v == col)
            elif col in self.organisations:
                what = f"organisation, level {self.organisations[col]}"
            elif col in self.filters:
                what = "filter (kept as a person attribute)"
            elif col in self.refused:
                what = f"refused: {self.refused[col]}"
            else:
                what = "ignored"
            lines.append(f"{col}: {what}")
        if self.projected_set:
            lines.append(f"every row: projected, set {self.projected_set}")
        return lines


def _column_values(rows: Sequence[Sequence[str]], i: int) -> list[str]:
    return [r[i] for r in rows if i < len(r) and r[i]]


def _share(values: Sequence[str], test) -> float:
    return sum(1 for v in values if test(v)) / len(values) if values else 0.0


def propose_mapping(
    rows: Sequence[Sequence[str]], *, levels: Sequence[Level] = ()
) -> ImportMapping:
    """A mapping proposal from a list's first rows.

    The first row is a header when one of its cells names a known column and
    none holds an identifier or an e-mail address. Header words are read in
    English, French, Portuguese and Spanish (``Last name``, ``Nom``,
    ``Prénom``, ``Sobrenome``…); an organisation column is matched to a
    project level by its id or name, else lab-like words go to the smallest
    level and institution-like words to the largest. Values decide the rest:
    a column of ORCIDs, of OpenAlex ids, of e-mail addresses (refused). In a
    list without a header, one text column is a full name, two are the last
    then the first name. Every column left is a filter.
    """
    if not rows:
        raise ValueError("the list is empty")
    first = rows[0]
    keys = [_header_key(c) for c in first]
    level_names = {}
    for lv in levels:
        level_names[_header_key(lv.id)] = lv.id
        for name in lv.names.values():
            level_names[_header_key(name)] = lv.id
    known = _FULL | _LAST | _FIRST | _ROLE | _SET | _SMALL_ORGS | _LARGE_ORGS | set(level_names)
    looks_like_data = any(
        normalise_orcid(c) or _EMAIL.match(c) or normalise_openalex_author(c) for c in first
    )
    has_header = not looks_like_data and any(
        k in known or any(s in k for s in _ID_SCHEMES) or "mail" in k for k in keys
    )
    columns = list(first) if has_header else [f"column {i + 1}" for i in range(len(first))]
    columns = [c or f"column {i + 1}" for i, c in enumerate(columns)]
    data = rows[1:] if has_header else rows
    mapping = ImportMapping(columns=columns, has_header=has_header)
    used: set[int] = set()

    def take(i: int) -> str:
        used.add(i)
        return columns[i]

    for i, key in enumerate(keys if has_header else [""] * len(columns)):
        values = _column_values(data, i)
        if "mail" in key or "courriel" in key or (values and _share(values, _EMAIL.match) > 0.5):
            mapping.refused[take(i)] = "e-mail addresses are not identifiers and are never stored"
        elif any(s in key for s in _ID_SCHEMES):
            scheme = next(v for s, v in _ID_SCHEMES.items() if s in key)
            mapping.ids.setdefault(scheme, take(i))
    for i in range(len(columns)):
        if i in used:
            continue
        values = _column_values(data, i)
        if values and _share(values, _EMAIL.match) > 0.5:
            mapping.refused[take(i)] = "e-mail addresses are not identifiers and are never stored"
        elif values and _share(values, normalise_orcid) > 0.5 and "orcid" not in mapping.ids:
            mapping.ids["orcid"] = take(i)
        elif (
            values
            and _share(values, lambda v: re.fullmatch(r"(https?://openalex\.org/)?A\d{4,}", v))
            > 0.5
            and "openalex" not in mapping.ids
        ):
            mapping.ids["openalex"] = take(i)
    if has_header:
        has_first = any(k in _FIRST for k in keys)
        ordered_levels = [lv.id for lv in levels]
        for i, key in enumerate(keys):
            if i in used:
                continue
            if key in _FIRST and not mapping.first_name:
                mapping.first_name = take(i)
            elif key in _LAST and not mapping.last_name and (key != "nom" or has_first):
                mapping.last_name = take(i)
            elif key in _FULL | {"nom", "nome"} and not mapping.full_name:
                mapping.full_name = take(i)
            elif key in _ROLE and not mapping.role:
                mapping.role = take(i)
            elif key in _SET and not mapping.set:
                mapping.set = take(i)
            elif key in level_names:
                mapping.organisations[take(i)] = level_names[key]
            elif key in _SMALL_ORGS or key in _LARGE_ORGS:
                if ordered_levels:
                    level = ordered_levels[0] if key in _SMALL_ORGS else ordered_levels[-1]
                    if level in mapping.organisations.values():
                        level = next(
                            (
                                lv
                                for lv in ordered_levels
                                if lv not in mapping.organisations.values()
                            ),
                            level,
                        )
                else:
                    level = re.sub(r"[^a-z0-9_-]+", "-", fold(columns[i])).strip("-") or "unit"
                mapping.organisations[take(i)] = level
        if mapping.last_name and mapping.full_name and not mapping.first_name:
            mapping.full_name = None
    else:
        texts = [i for i in range(len(columns)) if i not in used and _column_values(data, i)]
        if len(texts) == 1:
            mapping.full_name = take(texts[0])
        elif len(texts) >= 2:
            mapping.last_name = take(texts[0])
            mapping.first_name = take(texts[1])
    if mapping.full_name and mapping.last_name:
        mapping.full_name = None
    mapping.filters = [c for i, c in enumerate(columns) if i not in used]
    return mapping


# ── importing a list ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DuplicateProposal:
    """Two person rows that may be one person, and why; never applied on its own."""

    person_id: str
    other_id: str
    reason: str


@dataclass
class ImportReport:
    """What an import did."""

    rows_read: int = 0
    people_created: int = 0
    people_known: int = 0
    texts: int = 0
    refused: list[tuple[int | str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duplicates: list[DuplicateProposal] = field(default_factory=list)
    mapping: ImportMapping | None = None
    slot: str = ""
    run_id: str = ""

    def lines(self) -> list[str]:
        """The report in words."""
        out = [
            f"{self.rows_read} row(s) read; {self.people_created} person(s) created, "
            f"{self.people_known} already known" + (f"; {self.texts} text(s)" if self.texts else "")
        ]
        out += [f"refused {where}: {why}" for where, why in self.refused]
        out += self.notes
        out += [
            f"possible duplicate: {d.person_id} and {d.other_id} ({d.reason})"
            for d in self.duplicates
        ]
        return out


def _collection_slot(project: Project, slot: str | None, kind: str) -> str:
    """The slot to use: the one named, else the first of *kind*, else a new one."""
    config = project.config
    if slot:
        found = next((s for s in config.slots if s.id == slot), None)
        if found is None:
            new = Slot(id=slot, kind=kind, fit=True, trajectory=True)
            project.save_config(
                config.model_copy(update={"slots": [*config.slots, new]}),
                action=f"add slot {slot}",
            )
        return slot
    found = next((s for s in config.slots if s.kind == kind), None)
    if found is not None:
        return found.id
    default = {"collection": "collected", "folder": "documents", "corpus": "corpus"}[kind]
    taken = {s.id for s in config.slots} | {o.id for o in config.overlays}
    name, n = default, 2
    while name in taken:
        name, n = f"{default}-{n}", n + 1
    return _collection_slot(project, name, kind)


def _ensure_levels(project: Project, levels: Iterable[str]) -> list[str]:
    """Add the levels a mapping names that the project does not have yet (at the end)."""
    config = project.config
    have = {lv.id for lv in config.levels}
    added = [lv for lv in dict.fromkeys(levels) if lv not in have]
    if added:
        new = [
            *config.levels,
            *(Level(id=lv, names={"en": lv.replace("-", " ").capitalize()}) for lv in added),
        ]
        project.save_config(config.model_copy(update={"levels": new}), action="add levels")
    return added


def _role(value: str) -> str | None:
    return _ROLE_WORDS.get(fold(value).strip())


def _row_key(
    last: str, first: str, ids: Mapping[str, list[str]], orgs: Sequence[Mapping[str, str]]
) -> str:
    material = json.dumps(
        [
            name_key(last, first),
            sorted((k, sorted(v)) for k, v in ids.items()),
            [fold(o["name"]) for o in orgs],
        ],
        separators=(",", ":"),
    )
    return "import:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _org_key(level: str | None, name: str, parent: str | None) -> str:
    return f"import:{level or ''}:{fold(name)}" + (f"<{parent}" if parent else "")


def import_people(
    project: Project,
    source: Path | str,
    *,
    mapping: ImportMapping | None = None,
    slot: str | None = None,
    now: datetime | None = None,
) -> ImportReport:
    """Import a list of people (a CSV file, or the pasted text) into *project*.

    Without *mapping*, :func:`propose_mapping` reads the header. The list's rows
    go to ``sources/<slot>/raw/people/`` (e-mail columns left out), the tables are
    rebuilt, and each new person gets a row in ``decisions/people.csv``: the role
    the list gives (``mapped`` by default), identity ``pending``. People already
    in the project (the same row imported before) keep their decisions.
    """
    now = now or datetime.now(timezone.utc)
    rows, delimiter = read_list(source)
    if not rows:
        raise ValueError("the list is empty")
    mapping = mapping or propose_mapping(rows, levels=project.config.levels)
    mapping.check()
    report = ImportReport(mapping=mapping)
    slot = _collection_slot(project, slot, "collection")
    report.slot = slot
    added = _ensure_levels(project, mapping.organisations.values())
    if added:
        report.notes.append("levels added to the project: " + ", ".join(added))
    level_order = [lv.id for lv in project.config.levels]
    index = {c: i for i, c in enumerate(mapping.columns)}
    data = rows[1:] if mapping.has_header else rows
    for col, why in mapping.refused.items():
        report.notes.append(f"column {col!r} left out: {why}")

    def cell(row: Sequence[str], column: str | None) -> str:
        if column is None:
            return ""
        i = index[column]
        return row[i].strip() if i < len(row) else ""

    records: list[dict[str, Any]] = []
    decisions: dict[str, dict[str, str]] = {}
    for n, row in enumerate(data, start=2 if mapping.has_header else 1):
        report.rows_read += 1
        if mapping.full_name:
            last, first = split_full_name(cell(row, mapping.full_name))
        else:
            last, first = cell(row, mapping.last_name), cell(row, mapping.first_name)
        if not last:
            report.refused.append((f"row {n}", "no name"))
            continue
        ids: dict[str, list[str]] = {}
        orcid = None
        for scheme, column in mapping.ids.items():
            value = cell(row, column)
            if not value:
                continue
            if scheme == "orcid":
                orcid = normalise_orcid(value)
                if orcid is None:
                    report.notes.append(f"row {n}: {value!r} is not a valid ORCID; left out")
            elif scheme == "openalex":
                found = normalise_openalex_author(value)
                if found is None:
                    report.notes.append(
                        f"row {n}: {value!r} is not an OpenAlex author id; left out"
                    )
                else:
                    ids["openalex"] = [found]
            else:
                ids[scheme] = [value]
        orgs: list[dict[str, str]] = []
        for column, level in sorted(
            mapping.organisations.items(),
            key=lambda kv: level_order.index(kv[1]) if kv[1] in level_order else 99,
        ):
            name = cell(row, column)
            if name:
                orgs.append({"level": level, "name": name})
        parent: str | None = None
        for org in reversed(orgs):  # largest first, so each knows its parent
            org["key"] = _org_key(org["level"], org["name"], parent)
            if parent:
                org["parent"] = parent
            parent = org["key"]
        role = None
        if mapping.role and cell(row, mapping.role):
            role = _role(cell(row, mapping.role))
            if role is None:
                report.notes.append(
                    f"row {n}: role {cell(row, mapping.role)!r} is not one of {', '.join(ROLES)}; "
                    "the default applies"
                )
        pset = cell(row, mapping.set) or mapping.projected_set or ""
        if mapping.projected_set or (pset and role is None):
            role = "projected"
        if role == "projected" and not pset:
            report.notes.append(f"row {n}: projected without a set; set 'projected' used")
            pset = "projected"
        columns = {c: cell(row, c) for c in mapping.filters if cell(row, c)}
        key = _row_key(last, first, {**ids, **({"orcid": [orcid]} if orcid else {})}, orgs)
        records.append(
            {
                "row": n,
                "key": key,
                "last_name": last,
                "first_name": first,
                "orcid": orcid,
                "ids": ids,
                "columns": columns,
                "orgs": orgs,
                "role": role or "mapped",
                "set": pset if role == "projected" else "",
                "retrieved_at": iso(now),
            }
        )
    name = source.name if isinstance(source, Path) else "pasted list"
    header = {"source": name, "mapping": mapping.to_json(), "rows_read": report.rows_read}
    known_before = set(read_people(project.layout))
    with RawWriter(project.layout, slot, "people", header, now=now) as writer:
        for rec in records:
            writer.add(rec)
    report.run_id = writer.run_id
    rebuild_sources(project.layout, project.config)
    pid_of = _registry_ids(project, slot, [r["key"] for r in records])
    for rec in records:
        pid = pid_of[rec["key"]]
        if pid in known_before or pid in decisions:
            report.people_known += 1
            continue
        report.people_created += 1
        decisions[pid] = {
            "role": rec["role"],
            "set": rec["set"],
            "identity": "pending",
            "records": "",
        }
    update_people(project.layout, decisions, action="import people", only_new=True, now=now)
    report.duplicates = find_duplicates(project, among=set(decisions))
    _overlays(project, {d["set"] for d in decisions.values() if d["set"]})
    return report


def _registry_ids(project: Project, slot: str, keys: Iterable[str]) -> dict[str, str]:
    from .tables import IdRegistry

    registry = IdRegistry(project.layout, [s.id for s in project.config.slots])
    return {k: registry.lookup("people", [k], slot) or "" for k in keys}


def _overlays(project: Project, sets: set[str]) -> None:
    """Declare the projected sets a list names that the project does not have yet."""
    from cartolex.project.models import Overlay

    config = project.config
    have = {o.id for o in config.overlays} | {s.id for s in config.slots}
    new = [
        s for s in sorted(sets) if s not in have and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", s)
    ]
    if new:
        overlays = [*config.overlays, *(Overlay(id=s) for s in new)]
        project.save_config(config.model_copy(update={"overlays": overlays}), action="add sets")


def read_people_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """The reader of imported lists: people, their organisations, their affiliations."""
    for run in runs:
        for rec in run.records():
            at = parse_time(rec["retrieved_at"])
            ids = dict(rec.get("ids") or {})
            pid = builder.person(
                slot=run.slot,
                keys=[rec["key"]],
                last_name=rec["last_name"],
                first_name=rec.get("first_name") or None,
                orcid=rec.get("orcid"),
                ids=ids,
                source="import",
                columns=rec.get("columns") or {},
                retrieved_at=at,
            )
            for org in rec.get("orgs", []):
                oid = builder.organisation(
                    slot=run.slot,
                    keys=[org["key"]],
                    name=org["name"],
                    level=org.get("level"),
                    parent_keys=[org["parent"]] if org.get("parent") else [],
                    source="import",
                    retrieved_at=at,
                )
                builder.affiliation(pid, oid, None, None, "import")


# ── duplicates ───────────────────────────────────────────────────────────────


def find_duplicates(project: Project, *, among: set[str] | None = None) -> list[DuplicateProposal]:
    """Pairs of people who may be one person, with the reason; none is merged.

    Two rows are proposed when they share an identifier (ORCID, OpenAlex, HAL),
    when their names are the same once case, accents, hyphens and particles are
    set aside, or when their first names are compatible (one is the other's
    initial) and one surname is the other or a part of it. With *among*, only
    pairs involving one of those people. Pairs already decided (one merged into
    the other) are left out.
    """
    path = project.layout.table("people")
    if not path.exists():
        return []
    people = read_source_table(path, "people").to_pylist()
    decided = read_people(project.layout)
    merged = {pid for pid, row in decided.items() if row.get("merged_into")}
    people = [p for p in people if p["person_id"] not in merged]
    out: list[DuplicateProposal] = []
    by_id: dict[tuple[str, str], list[str]] = {}
    for p in people:
        schemes = {("orcid", p["orcid"])} if p["orcid"] else set()
        for scheme, values in dict(p["ids"] or []).items():
            schemes |= {(scheme, v) for v in values}
        for key in schemes:
            by_id.setdefault(key, []).append(p["person_id"])
    seen: set[tuple[str, str]] = set()

    def propose(a: str, b: str, reason: str) -> None:
        pair = (min(a, b), max(a, b))
        if a == b or pair in seen:
            return
        if among is not None and a not in among and b not in among:
            return
        seen.add(pair)
        out.append(DuplicateProposal(pair[0], pair[1], reason))

    for (scheme, _value), pids in sorted(by_id.items()):
        for i, a in enumerate(pids):
            for b in pids[i + 1 :]:
                propose(a, b, f"the same {scheme} identifier")
    by_surname: dict[str, list[dict[str, Any]]] = {}
    for p in people:
        for part in surname_parts(p["last_name"]):
            by_surname.setdefault(part, []).append(p)
    for group in by_surname.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                ka, kb = (
                    name_key(a["last_name"], a["first_name"]),
                    name_key(b["last_name"], b["first_name"]),
                )
                if ka == kb:
                    propose(a["person_id"], b["person_id"], "the same name")
                    continue
                if not compatible_first_names(a["first_name"], b["first_name"]):
                    continue
                sa, sb = surname_parts(a["last_name"]), surname_parts(b["last_name"])
                if sa == sb:
                    propose(
                        a["person_id"],
                        b["person_id"],
                        "the same surname, a first name as an initial",
                    )
                elif set(sa) < set(sb) or set(sb) < set(sa):
                    propose(a["person_id"], b["person_id"], "one surname is part of the other")
    return sorted(out, key=lambda d: (d.person_id, d.other_id))


def confirm_merge(
    project: Project, keep: str, merged: str, *, note: str = "", now: datetime | None = None
) -> None:
    """Record that *merged* is the same person as *keep* (``merged_into``); rebuild the tables so
    *keep* gains the other's name forms as aliases."""
    if keep == merged:
        raise ValueError("a person cannot be merged into themselves")
    rows = read_people(project.layout)
    target = rows.get(keep, {})
    if target.get("merged_into"):
        raise ValueError(f"{keep} is itself merged into {target['merged_into']}")
    update_people(
        project.layout,
        {merged: {"merged_into": keep, "note": note or f"same person as {keep}"}},
        action=f"merge {merged} into {keep}",
        now=now,
    )
    rebuild_sources(project.layout, project.config)


# ── folders of documents ─────────────────────────────────────────────────────

FOLDER_SUFFIXES = (".pdf", ".txt", ".md")


def _read_document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        import pypdf

        from cartolex.lexicon.pdf_text import extract_text

        with open(path, "rb") as fh:  # a file that is not a PDF fails here, with its reason
            len(pypdf.PdfReader(fh, strict=True).pages)
        return extract_text(path)
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _match_person(label: str, people: Sequence[Mapping[str, Any]]) -> tuple[str | None, str]:
    """The one person whose name appears in *label* (a file name or a folder name)."""
    got = set(words(label))
    hits = []
    for p in people:
        sur = surname_parts(p["last_name"])
        if not sur or not set(sur) <= got:
            continue
        rest = got - set(sur)
        given = words(p["first_name"] or "")
        strong = bool(given) and set(given) <= rest
        hits.append((strong, p["person_id"]))
    if not hits:
        return None, "no person's name found in it"
    strong = [pid for s, pid in hits if s]
    if len(strong) == 1:
        return strong[0], ""
    if len(hits) == 1:
        return hits[0][1], ""
    return None, "it names several people: " + ", ".join(sorted(pid for _, pid in hits))


def import_folder(
    project: Project,
    folder: Path,
    *,
    slot: str | None = None,
    create_people: bool = False,
    now: datetime | None = None,
) -> ImportReport:
    """Import a folder of documents, each matched to a person.

    A file in a sub-folder belongs to the person the sub-folder names; a file
    at the top belongs to the person its name names. Each file is read on its
    own: an unreadable file is reported with its reason and never stops the
    others. With *create_people*, a sub-folder whose name matches nobody
    creates a person. The text of each file becomes one ``full`` part.
    """
    now = now or datetime.now(timezone.utc)
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} is not a folder")
    slot = _collection_slot(project, slot, "folder")
    report = ImportReport(slot=slot)
    people = []
    if project.layout.table("people").exists():
        people = read_source_table(project.layout.table("people"), "people").to_pylist()
    files = sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in FOLDER_SUFFIXES
    )
    records = []
    for path in files:
        rel = path.relative_to(folder).as_posix()
        report.rows_read += 1
        parts = path.relative_to(folder).parts
        label = parts[0] if len(parts) > 1 else path.stem
        pid, why = _match_person(label, people)
        person = None
        if pid is None and len(parts) > 1 and create_people and why.startswith("no person"):
            last, first = split_full_name(label.replace("_", " "))
            person = {
                "key": f"folder:{name_key(last, first)}",
                "last_name": last,
                "first_name": first,
            }
        elif pid is None:
            report.refused.append((rel, why))
            continue
        try:
            text = clean(_read_document(path))
        except Exception as exc:  # one broken file never stops the others
            report.refused.append(
                (rel, f"could not be read ({type(exc).__name__}: {str(exc)[:120]})")
            )
            continue
        if not text:
            report.refused.append((rel, "no text in it (a scanned document without a text layer?)"))
            continue
        year = _YEAR.search(path.stem)
        records.append(
            {
                "file": rel,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "person_id": pid,
                "person": person,
                "title": clean(path.stem.replace("_", " ")),
                "year": int(year.group(1)) if year else None,
                "language": detect_language(text),
                "text": text,
                "retrieved_at": iso(now),
            }
        )
    # The folder's name only: no absolute path is stored in a project file.
    header = {"folder": folder.resolve().name, "files": report.rows_read}
    with RawWriter(project.layout, slot, "folder", header, now=now) as writer:
        for rec in records:
            writer.add(rec)
    report.run_id = writer.run_id
    report.texts = len(records)
    before = set(read_people(project.layout))
    rebuild_sources(project.layout, project.config)
    created = {rec["person"]["key"] for rec in records if rec["person"] is not None}
    if created:
        pid_of = _registry_ids(project, slot, created)
        new = {
            pid: {"role": "mapped", "identity": "none"}
            for pid in pid_of.values()
            if pid not in before
        }
        report.people_created = len(new)
        update_people(project.layout, new, action="import folder", only_new=True, now=now)
    _full_parts(project, report)
    return report


def read_folder_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """The reader of folders: one text per file, the newest run's reading of it first."""
    seen: set[str] = set()
    doc_type = _doc_type(builder, runs[0].slot if runs else "", "report")
    for run in reversed(runs):
        for rec in run.records():
            if rec["file"] in seen:
                continue
            seen.add(rec["file"])
            at = parse_time(rec["retrieved_at"])
            pid = rec.get("person_id")
            if rec.get("person"):
                p = rec["person"]
                pid = builder.person(
                    slot=run.slot,
                    keys=[p["key"]],
                    last_name=p["last_name"],
                    first_name=p.get("first_name") or None,
                    source="folder",
                    retrieved_at=at,
                )
            if not pid or not builder.known_person(pid):
                builder.warnings.append(f"{rec['file']}: its person is no longer in the tables")
                continue
            tid = builder.text(
                slot=run.slot,
                keys=[f"file:{rec['file']}"],
                title=rec["title"],
                doc_type=doc_type,
                year=rec.get("year"),
                source="folder",
                retrieved_at=at,
                n_authors=1,
            )
            builder.part(
                tid,
                part="full",
                language=rec["language"],
                provider="folder",
                content=rec["text"],
                retrieved_at=at,
            )
            builder.authorship(tid, pid, position=1)


def _doc_type(builder: SourceBuilder, slot: str, default: str) -> str:
    found = next((s for s in builder.config.slots if s.id == slot), None)
    return found.doc_types[0] if found is not None and found.doc_types else default


def _full_parts(project: Project, report: ImportReport) -> None:
    """Whole documents are read by the build only when its parts include ``full``."""
    params, fp = project.read_params()
    stage = dict(params.stages.get("corpus.assemble", {}))
    if "parts" in stage:
        if "full" not in stage["parts"]:
            report.notes.append(
                "the build reads only " + ", ".join(stage["parts"]) + " of each text: add 'full' "
                "to corpus.assemble.parts to read these documents"
            )
        return
    stage["parts"] = ["title", "abstract", "full"]
    stages = {**params.stages, "corpus.assemble": stage}
    project.save_params(
        params.model_copy(update={"stages": stages}), expected=fp, action="read whole documents"
    )
    report.notes.append(
        "corpus.assemble.parts set to title, abstract, full: the build reads whole documents"
    )


# ── a corpus in the engine's contract ────────────────────────────────────────


def import_corpus(
    project: Project,
    index_csv: Path,
    *,
    root: Path | None = None,
    slot: str | None = None,
    unit_level: str | None = None,
    now: datetime | None = None,
) -> ImportReport:
    """Import a corpus in the engine's contract: ``last_name``, ``first_name``, ``unit``,
    ``txt_path`` and optionally ``doc_year``, ``doc_type``; other columns are person attributes.

    ``txt_path`` is relative to *root* (default: the index's folder). The unit
    becomes an organisation of *unit_level* (default: the project's first
    level). A text file listed for several people is one text with several
    authors. Each row is read on its own; a missing or unreadable file is
    reported.
    """
    now = now or datetime.now(timezone.utc)
    index_csv = Path(index_csv)
    root = Path(root) if root is not None else index_csv.parent
    slot = _collection_slot(project, slot, "corpus")
    report = ImportReport(slot=slot)
    with open(index_csv, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = list(reader.fieldnames or [])
        missing = [c for c in ("last_name", "first_name", "txt_path") if c not in columns]
        if missing:
            raise ValueError(f"{index_csv.name}: missing column(s) {missing}")
        rows = list(reader)
    level = unit_level or (project.config.levels[0].id if project.config.levels else None)
    document = {"last_name", "first_name", "unit", "txt_path", "doc_year", "doc_type", "source"}
    attributes = [c for c in columns if c not in document]
    records = []
    for n, row in enumerate(rows, start=2):
        report.rows_read += 1
        rel = (row.get("txt_path") or "").strip()
        if not (row.get("last_name") or "").strip():
            report.refused.append((f"row {n}", "no name"))
            continue
        path = root / rel
        try:
            text = clean(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            report.refused.append((f"row {n}", f"{rel} could not be read ({type(exc).__name__})"))
            continue
        if not text:
            report.refused.append((f"row {n}", f"{rel} is empty"))
            continue
        year_text = (row.get("doc_year") or "").strip()
        records.append(
            {
                "row": n,
                "last_name": row["last_name"].strip(),
                "first_name": (row.get("first_name") or "").strip(),
                "unit": (row.get("unit") or "").strip(),
                "attributes": {c: row[c] for c in attributes if (row.get(c) or "").strip()},
                "file": rel,
                "year": int(float(year_text)) if re.fullmatch(r"\d{4}(\.0)?", year_text) else None,
                "doc_type": (row.get("doc_type") or "").strip() or None,
                "language": detect_language(text),
                "text": text,
                "retrieved_at": iso(now),
            }
        )
    header = {"index": index_csv.name, "level": level}
    with RawWriter(project.layout, slot, "corpus", header, now=now) as writer:
        for rec in records:
            writer.add(rec)
    report.run_id = writer.run_id
    report.texts = len({r["file"] for r in records})
    before = set(read_people(project.layout))
    if level:
        _ensure_levels(project, [level])
    rebuild_sources(project.layout, project.config)
    keys = {_corpus_person_key(r) for r in records}
    pid_of = _registry_ids(project, slot, keys)
    new = {
        pid: {"role": "mapped", "identity": "none"}
        for pid in pid_of.values()
        if pid and pid not in before
    }
    report.people_created = len(new)
    report.people_known = len(set(pid_of.values())) - len(new)
    update_people(project.layout, new, action="import corpus", only_new=True, now=now)
    _full_parts(project, report)
    return report


def _corpus_person_key(rec: Mapping[str, Any]) -> str:
    return f"corpus:{name_key(rec['last_name'], rec['first_name'])}|{fold(rec['unit'])}"


def read_corpus_runs(runs: list[RawRun], builder: SourceBuilder) -> None:
    """The reader of imported corpora: people and units, one text per file, the newest run first."""
    seen_rows: set[tuple[str, str]] = set()
    rank: dict[str, int] = {}
    doc_type = _doc_type(builder, runs[0].slot if runs else "", "article")
    for run in reversed(runs):
        level = run.header.get("level")
        for rec in run.records():
            key = _corpus_person_key(rec)
            if (key, rec["file"]) in seen_rows:
                continue
            seen_rows.add((key, rec["file"]))
            at = parse_time(rec["retrieved_at"])
            pid = builder.person(
                slot=run.slot,
                keys=[key],
                last_name=rec["last_name"],
                first_name=rec["first_name"] or None,
                columns=rec.get("attributes") or {},
                source="import",
                retrieved_at=at,
            )
            if rec["unit"]:
                oid = builder.organisation(
                    slot=run.slot,
                    keys=[_org_key(level, rec["unit"], None)],
                    name=rec["unit"],
                    level=level,
                    source="import",
                    retrieved_at=at,
                )
                builder.affiliation(pid, oid, None, None, "import")
            tid = builder.text(
                slot=run.slot,
                keys=[f"file:{rec['file']}"],
                title=rec["text"].split("\n", 1)[0][:300],
                doc_type=rec.get("doc_type") or doc_type,
                year=rec.get("year"),
                source="import",
                retrieved_at=at,
            )
            builder.part(
                tid,
                part="full",
                language=rec["language"],
                provider="import",
                content=rec["text"],
                retrieved_at=at,
            )
            rank[tid] = rank.get(tid, 0) + 1
            builder.authorship(tid, pid, position=rank[tid])
    for tid, n in rank.items():
        builder.texts[tid]["n_authors"] = n
