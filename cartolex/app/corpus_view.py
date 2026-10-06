# SPDX-License-Identifier: MIT
"""What the corpus screen reads: coverage states, organisations, texts, one person's sheet.

Everything here reads the project's tables and raw records, never a service.
Lists are computed once per version of what they read and kept in the app's
cache (:class:`~cartolex.app.runtime.Cache`). The texts are a view of columns
(:mod:`cartolex.app.texts_view`), so that a project of millions of texts lists,
filters and pages them without a dictionary per text; one text, one person or
one organisation is read from the row groups that hold it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

import numpy as np

from cartolex.project import Project
from cartolex.project.layout import SOURCE_TABLES
from cartolex.project.tables import read_source_table

from .texts_view import texts_view, view_stamp

__all__ = [
    "ordered",
    "people_view",
    "coverage_states",
    "organisation_detail",
    "organisations",
    "person_detail",
    "stamp",
    "text_detail",
    "work_copies",
]

#: How much of a part's content a text's detail shows.
PREVIEW_CHARS = 1200
#: The most values a column's facet lists.
MAX_FACET_VALUES = 50


def stamp(project: Project) -> tuple[Any, ...]:
    """What the views depend on: the tables, the raw records, ``people.csv`` and the parameters."""
    from .collect_service import raw_stamp

    layout = project.layout
    out: list[Any] = [raw_stamp(project)]
    for path in (
        *(layout.table(n) for n in ("people", "texts", "text_parts", "authorships")),
        layout.table("affiliations"),
        layout.table("organisations"),
        layout.params_json,
    ):
        try:
            st = path.stat()
            out.append((path.name, st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            out.append((path.name, None))
    return tuple(out)


def tables_stamp(project: Project) -> tuple[Any, ...]:
    """What the views of texts and organisations depend on: the tables and the parameters
    (not ``people.csv``: editing a person leaves them as they are)."""
    layout = project.layout
    out: list[Any] = [str(layout.root)]
    for path in (*(layout.table(n) for n in SOURCE_TABLES), layout.params_json):
        try:
            st = path.stat()
            out.append((path.name, st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            out.append((path.name, None))
    return tuple(out)


def _rows(
    project: Project, name: str, columns: list[str] | None = None, filters: list | None = None
) -> list[dict[str, Any]]:
    path = project.layout.table(name)
    if not path.exists():
        return []
    return read_source_table(path, name, columns, filters=filters).to_pylist()


def _cached(cache: Any, key: Any, compute: Any) -> Any:
    return cache.get(key, compute) if cache is not None else compute()


def coverage_states(
    project: Project, cache: Any = None, columns: Any = None
) -> dict[str, dict[str, Any]]:
    """Person id → state (good, thin, failed, no_data), first blocking cause and counts
    (*columns*: a function giving every text's columns, when the caller shares them)."""
    from cartolex.collect.coverage import person_coverage

    def compute() -> dict[str, dict[str, Any]]:
        if not project.layout.table("people").exists():
            return {}
        return {
            p.person_id: {
                "state": p.state,
                "cause": p.cause,
                "cause_text": p.cause_text,
                "texts": p.texts,
                "with_abstract": p.with_abstract,
                "titles_only": p.titles_only,
            }
            for p in person_coverage(
                project, detail=False, columns=columns() if columns is not None else None
            )
        }

    return _cached(cache, ("coverage-states", stamp(project)), compute)


def _facets(people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each extra column of the people (from their lists): its values and how many have each."""
    values: dict[str, dict[str, int]] = {}
    for p in people:
        for key, value in p["columns"].items():
            column = values.setdefault(key, {})
            column[value] = column.get(value, 0) + 1
    out = []
    for key in sorted(values):
        counted = sorted(values[key].items(), key=lambda kv: (-kv[1], kv[0]))
        out.append(
            {
                "column": key,
                "values": [{"value": v, "count": n} for v, n in counted[:MAX_FACET_VALUES]],
                "distinct": len(counted),
            }
        )
    return out


def people_view(project: Project, cache: Any = None) -> dict[str, Any]:
    """Every person (the tables joined with the decisions, their coverage state and first
    blocking cause), ``people.csv``'s fingerprint, the counts per role, identity, class and
    state, and the facets of the extra columns; computed once per version of what it reads.
    The rows are shared: read them, never change them."""
    from functools import cache as once

    from cartolex.project.text_columns import read_text_columns

    from .people_io import read_people

    def compute() -> dict[str, Any]:
        @once
        def columns() -> Any:  # every text's, read at most once for both below
            return read_text_columns(project.layout)

        people, fp = read_people(project, cache, columns)
        states = coverage_states(project, cache, columns) if people else {}
        counts: dict[str, dict[str, int]] = {
            "role": {}, "identity": {}, "coverage": {}, "state": {}
        }  # fmt: skip
        for p in people:
            st = states.get(p["person_id"])
            p["state"] = st["state"] if st else ""
            p["cause"] = (
                {"code": st["cause"], "message": st["cause_text"]} if st and st["cause"] else None
            )
            for key, value in (
                ("role", p["role"]),
                ("identity", p["identity"]),
                ("coverage", p["coverage"]["class"]),
                ("state", p["state"]),
            ):
                if value:
                    counts[key][value] = counts[key].get(value, 0) + 1
        return {
            "people": people,
            "fp": fp,
            "counts": counts,
            "facets": _facets(people),
            "orders": {},
        }

    return _cached(cache, ("people-view", stamp(project)), compute)


def ordered(view: dict[str, Any], name: str, key: Any, descending: bool) -> list[dict[str, Any]]:
    """The view's people sorted by *key* (kept with the view, so a list is sorted once)."""
    orders = view["orders"]
    if name not in orders:

        def safe(item: dict[str, Any]) -> tuple[int, Any]:
            value = key(item)
            return (1, 0) if value is None else (0, value)

        orders[name] = sorted(view["people"], key=safe)
    rows = orders[name]
    return rows[::-1] if descending else rows


def _names(project: Project, person_ids: set[str]) -> dict[str, str]:
    if not person_ids:
        return {}
    return {
        p["person_id"]: " ".join(x for x in (p["first_name"], p["last_name"]) if x)
        for p in _rows(
            project,
            "people",
            ["person_id", "last_name", "first_name"],
            [("person_id", "in", sorted(person_ids))],
        )
    }


def organisations(project: Project, cache: Any = None) -> list[dict[str, Any]]:
    """Every organisation: its level, parents (ids and names), people now and ever affiliated."""

    def compute() -> list[dict[str, Any]]:
        import pyarrow.compute as pc

        orgs = _rows(project, "organisations")
        names = {o["org_id"]: o["acronym"] or o["name"] for o in orgs}
        children: dict[str, int] = defaultdict(int)
        for o in orgs:
            for parent in o["parents"] or []:
                children[parent] += 1
        ever: dict[str, int] = {}
        now: dict[str, int] = {}
        path = project.layout.table("affiliations")
        if path.exists():
            aff = read_source_table(path, "affiliations", ["person_id", "org_id", "end_year"])
            for counts, rows in ((ever, aff), (now, aff.filter(pc.is_null(aff["end_year"])))):
                grouped = rows.group_by("org_id").aggregate([("person_id", "count_distinct")])
                counts.update(
                    zip(
                        grouped["org_id"].to_pylist(),
                        grouped["person_id_count_distinct"].to_pylist(),
                        strict=True,
                    )
                )
        return [
            {
                "org_id": o["org_id"],
                "name": o["name"],
                "acronym": o["acronym"] or "",
                "level": o["level"] or "",
                "parents": list(o["parents"] or []),
                "parent_names": [names.get(p, p) for p in o["parents"] or []],
                "children": children.get(o["org_id"], 0),
                "people": now.get(o["org_id"], 0),
                "people_ever": ever.get(o["org_id"], 0),
                "country": o["country"] or "",
                "source": o["source"],
                "ids": dict(o["ids"] or []),
            }
            for o in orgs
        ]

    return _cached(cache, ("organisations", tables_stamp(project)), compute)


def organisation_detail(project: Project, org_id: str) -> dict[str, Any] | None:
    """One organisation with its people and the years of each affiliation, and its units."""
    orgs = {o["org_id"]: o for o in _rows(project, "organisations")}
    org = orgs.get(org_id)
    if org is None:
        return None
    rows = _rows(project, "affiliations", None, [("org_id", "==", org_id)])
    names = _names(project, {a["person_id"] for a in rows})
    affiliations = [
        {
            "person_id": a["person_id"],
            "name": names.get(a["person_id"], a["person_id"]),
            "start_year": a["start_year"],
            "end_year": a["end_year"],
            "source": a["source"],
        }
        for a in rows
    ]
    affiliations.sort(key=lambda a: (a["name"].casefold(), a["start_year"] or 0))
    return {
        "org_id": org_id,
        "name": org["name"],
        "acronym": org["acronym"] or "",
        "level": org["level"] or "",
        "parents": [
            {"org_id": p, "name": orgs[p]["name"] if p in orgs else p} for p in org["parents"] or []
        ],
        "units": [
            {"org_id": o["org_id"], "name": o["name"], "level": o["level"] or ""}
            for o in orgs.values()
            if org_id in (o["parents"] or [])
        ],
        "ids": dict(org["ids"] or []),
        "country": org["country"] or "",
        "location": org["location"],
        "source": org["source"],
        "affiliations": affiliations,
    }


def work_copies(project: Project, cache: Any = None) -> dict[str, str]:
    """Each copy of a work among the texts → the text the corpus reads instead, as
    ``corpus.assemble`` finds them with its parameters
    (:func:`cartolex.project.corpus.work_copies`), once per version of the tables and of
    those parameters."""
    from cartolex.project.corpus import work_copies as find

    view = texts_view(project, cache)
    same = _same_work(project)

    def compute() -> dict[str, str]:
        if not view.n:
            return {}
        return find(project.layout.tables, keys=view.title_keys(), **same)

    return _cached(cache, ("work-copies", view_stamp(project), *same.values()), compute)


def _same_work(project: Project) -> dict[str, int]:
    """How ``corpus.assemble`` finds two texts to be one work: its parameters in
    ``params.json``, else their defaults."""
    from cartolex.project.corpus import DUPLICATE_MIN_TITLE, DUPLICATE_YEAR_GAP

    try:
        params, _ = project.read_params()
        given = params.stages.get("corpus.assemble") or {}
    except Exception:  # an unreadable params.json: the build refuses it with its reasons
        given = {}
    out = {"min_title": DUPLICATE_MIN_TITLE, "year_gap": DUPLICATE_YEAR_GAP}
    for key, name in (("min_title", "duplicate_min_title"), ("year_gap", "duplicate_year_gap")):
        value = given.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            out[key] = value
    return out


def _merge_log(project: Project, cache: Any = None) -> dict[str, dict[str, list[Any]]]:
    """The merges by the text kept and the conflicts by text, from ``sources/merges.json``
    (read once per version of the file)."""
    path = project.layout.sources / "merges.json"
    try:
        st = path.stat()
    except FileNotFoundError:
        return {"merges": {}, "conflicts": {}}

    def compute() -> dict[str, dict[str, list[Any]]]:
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"merges": {}, "conflicts": {}}
        out: dict[str, dict[str, list[Any]]] = {"merges": {}, "conflicts": {}}
        for kind, key in (("merges", "kept"), ("conflicts", "text_id")):
            for entry in log.get(kind, []):
                out[kind].setdefault(str(entry.get(key)), []).append(entry)
        return out

    return _cached(cache, ("merge-log", str(path), st.st_size, st.st_mtime_ns), compute)


def text_detail(project: Project, text_id: str, cache: Any = None) -> dict[str, Any] | None:
    """One text: its fields, each part (a preview of its content) by provider, its people,
    the records merged into it, its versions and the conflicts between finders."""
    one = [("text_id", "==", text_id)]
    found = next(iter(_rows(project, "texts", None, one)), None)
    if found is None:
        return None
    parts = [
        {
            "part": p["part"],
            "language": p["language"],
            "provider": p["provider"],
            "format": p["format"],
            "chars": len(p["content"] or ""),
            "preview": (p["content"] or "")[:PREVIEW_CHARS],
        }
        for p in _rows(project, "text_parts", None, one)
    ]
    rows = _rows(project, "authorships", None, one)
    names = _names(project, {a["person_id"] for a in rows})
    authors = sorted(
        (
            {
                "person_id": a["person_id"],
                "name": names.get(a["person_id"], a["person_id"]),
                "position": a["position"],
                "orgs": list(a["orgs"] or []),
            }
            for a in rows
        ),
        key=lambda a: (a["position"] or 0, a["person_id"]),
    )
    log = _merge_log(project, cache)
    versions = _rows(
        project, "texts", ["text_id", "title", "year"], [("version_of", "==", text_id)]
    )
    return {
        "text_id": text_id,
        "title": found["title"],
        "year": found["year"],
        "date": found["date"],
        "doc_type": found["doc_type"],
        "slot": found["slot"],
        "source": found["source"],
        "doi": found["doi"] or "",
        "ids": dict(found["ids"] or []),
        "n_authors": found["n_authors"],
        "version_of": found["version_of"] or "",
        "versions": [
            {"text_id": t["text_id"], "title": t["title"], "year": t["year"]} for t in versions
        ],
        "parts": parts,
        "people": authors,
        "merges": log["merges"].get(text_id, []),
        "conflicts": log["conflicts"].get(text_id, []),
    }


def person_detail(project: Project, person_id: str, cache: Any = None) -> dict[str, Any] | None:
    """One person's sheet: the coverage and its first blocking cause, the sources used and
    discarded, the attempts, the texts, the affiliations with their years."""
    from cartolex.collect.coverage import person_sheet

    one = [("person_id", "==", person_id)]
    row = next(iter(_rows(project, "people", None, one)), None)
    if row is None:
        return None
    try:
        sheet = person_sheet(project, person_id)
    except ValueError:
        sheet = None  # merged into another person: the sheet is theirs
    rows = _rows(project, "affiliations", None, one)
    org_ids = sorted({a["org_id"] for a in rows})
    orgs = {
        o["org_id"]: o
        for o in (
            _rows(project, "organisations", None, [("org_id", "in", org_ids)]) if org_ids else []
        )
    }
    affiliations = sorted(
        (
            {
                "org_id": a["org_id"],
                "name": orgs[a["org_id"]]["name"] if a["org_id"] in orgs else a["org_id"],
                "level": (orgs.get(a["org_id"]) or {}).get("level") or "",
                "start_year": a["start_year"],
                "end_year": a["end_year"],
                "source": a["source"],
            }
            for a in rows
        ),
        key=lambda a: (-(a["start_year"] or 0), a["name"]),
    )
    view = texts_view(project, cache)
    own = [
        {
            k: t[k]
            for k in ("text_id", "title", "year", "doc_type", "source", "content", "providers")
        }
        for t in view.rows(np.flatnonzero(view.of_people([person_id])), {})
    ]
    own.sort(key=lambda t: (-(t["year"] or 0), t["title"]))
    return {
        "person_id": person_id,
        "last_name": row["last_name"],
        "first_name": row["first_name"] or "",
        "orcid": row["orcid"],
        "ids": {k: list(v) for k, v in dict(row["ids"] or []).items()},
        "columns": dict(row["columns"] or []),
        "aliases": [
            " ".join(x for x in (a["first_name"], a["last_name"]) if x)
            for a in row["aliases"] or []
        ],
        "source": row["source"],
        "sheet": _plain(sheet),
        "affiliations": affiliations,
        "texts": own,
    }


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value
