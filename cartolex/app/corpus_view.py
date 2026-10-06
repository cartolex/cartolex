# SPDX-License-Identifier: MIT
"""What the corpus screen reads: coverage states, organisations, texts, one person's sheet.

Everything here reads the project's tables and raw records, never a service.
Lists are computed once per version of what they read and kept in the app's
cache (:class:`~cartolex.app.runtime.Cache`), so paging through 10⁵ people or
texts sorts and slices rows already in memory.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from cartolex.project import Project
from cartolex.project.layout import SOURCE_TABLES
from cartolex.project.tables import read_source_table

__all__ = [
    "ordered",
    "people_view",
    "coverage_states",
    "organisation_detail",
    "organisations",
    "person_detail",
    "stamp",
    "text_detail",
    "texts",
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


def _rows(project: Project, name: str, columns: list[str] | None = None) -> list[dict[str, Any]]:
    path = project.layout.table(name)
    if not path.exists():
        return []
    return read_source_table(path, name, columns).to_pylist()


def _cached(cache: Any, key: Any, compute: Any) -> Any:
    return cache.get(key, compute) if cache is not None else compute()


def coverage_states(project: Project, cache: Any = None) -> dict[str, dict[str, Any]]:
    """Person id → state (good, thin, failed, no_data), first blocking cause and counts."""
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
            for p in person_coverage(project)
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
    from .people_io import read_people

    def compute() -> dict[str, Any]:
        people, fp = read_people(project, cache)
        states = coverage_states(project, cache) if people else {}
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


def _names(project: Project) -> dict[str, str]:
    return {
        p["person_id"]: " ".join(x for x in (p["first_name"], p["last_name"]) if x)
        for p in _rows(project, "people", ["person_id", "last_name", "first_name"])
    }


def organisations(project: Project, cache: Any = None) -> list[dict[str, Any]]:
    """Every organisation: its level, parents (ids and names), people now and ever affiliated."""

    def compute() -> list[dict[str, Any]]:
        orgs = _rows(project, "organisations")
        names = {o["org_id"]: o["acronym"] or o["name"] for o in orgs}
        now: dict[str, set[str]] = defaultdict(set)
        ever: dict[str, set[str]] = defaultdict(set)
        children: dict[str, int] = defaultdict(int)
        for o in orgs:
            for parent in o["parents"] or []:
                children[parent] += 1
        for a in _rows(project, "affiliations"):
            ever[a["org_id"]].add(a["person_id"])
            if a["end_year"] is None:
                now[a["org_id"]].add(a["person_id"])
        return [
            {
                "org_id": o["org_id"],
                "name": o["name"],
                "acronym": o["acronym"] or "",
                "level": o["level"] or "",
                "parents": list(o["parents"] or []),
                "parent_names": [names.get(p, p) for p in o["parents"] or []],
                "children": children.get(o["org_id"], 0),
                "people": len(now.get(o["org_id"], ())),
                "people_ever": len(ever.get(o["org_id"], ())),
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
    names = _names(project)
    affiliations = [
        {
            "person_id": a["person_id"],
            "name": names.get(a["person_id"], a["person_id"]),
            "start_year": a["start_year"],
            "end_year": a["end_year"],
            "source": a["source"],
        }
        for a in _rows(project, "affiliations")
        if a["org_id"] == org_id
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


def texts(project: Project, cache: Any = None) -> list[dict[str, Any]]:
    """Every text with its parts (part, language, provider), its people and its languages."""

    def compute() -> list[dict[str, Any]]:
        parts: dict[str, list[dict[str, str]]] = defaultdict(list)
        for p in _rows(
            project, "text_parts", ["text_id", "part", "language", "provider", "format"]
        ):
            parts[p["text_id"]].append(
                {k: p[k] for k in ("part", "language", "provider", "format")}
            )
        people: dict[str, list[str]] = defaultdict(list)
        for a in _rows(project, "authorships", ["text_id", "person_id"]):
            people[a["text_id"]].append(a["person_id"])
        rows = _rows(project, "texts")
        copy_of = duplicate_copies(rows, people, **same_work)
        out = []
        for t in rows:
            tp = parts.get(t["text_id"], [])
            kinds = {p["part"] for p in tp}
            out.append(
                {
                    "text_id": t["text_id"],
                    "title": t["title"],
                    "year": t["year"],
                    "doc_type": t["doc_type"],
                    "slot": t["slot"],
                    "source": t["source"],
                    "doi": t["doi"] or "",
                    "version_of": t["version_of"] or "",
                    "n_authors": t["n_authors"],
                    "copy_of": copy_of.get(t["text_id"], ""),
                    "people": people.get(t["text_id"], []),
                    "parts": tp,
                    "providers": sorted({p["provider"] for p in tp}),
                    "languages": sorted(
                        {p["language"] for p in tp if p["part"] != "title"}
                        or {p["language"] for p in tp}
                    ),  # fmt: skip
                    "content": "full"
                    if kinds & {"body", "full"}
                    else "abstract"
                    if "abstract" in kinds
                    else "title",
                }
            )
        return out

    same_work = _same_work(project)
    return _cached(cache, ("texts", tables_stamp(project), *same_work.values()), compute)


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


def duplicate_copies(
    rows: list[dict[str, Any]],
    people: Mapping[str, list[str]],
    *,
    min_title: int | None = None,
    year_gap: int | None = None,
) -> dict[str, str]:
    """The texts the corpus reads once with another (copy → the text read), as
    ``corpus.assemble`` groups them (:func:`cartolex.project.corpus.duplicate_groups`, with its
    *min_title* and *year_gap*): a preprint whose published version is in the tables is left
    aside first."""
    from cartolex.project.corpus import duplicate_groups, version_rank

    ids = {t["text_id"] for t in rows}
    meta = {t["text_id"]: t for t in rows if not (t["version_of"] and t["version_of"] in ids)}
    rules = {k: v for k, v in (("min_title", min_title), ("year_gap", year_gap)) if v is not None}
    out: dict[str, str] = {}
    for group in duplicate_groups(meta, people, **rules):
        keep = min(group, key=lambda t: (version_rank(meta[t]["doc_type"]), t))
        out.update({t: keep for t in group if t != keep})
    return out


def _merge_log(project: Project) -> dict[str, Any]:
    path = project.layout.sources / "merges.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def text_detail(project: Project, text_id: str) -> dict[str, Any] | None:
    """One text: its fields, each part (a preview of its content) by provider, its people,
    the records merged into it, its versions and the conflicts between finders."""
    found = next((t for t in _rows(project, "texts") if t["text_id"] == text_id), None)
    if found is None:
        return None
    names = _names(project)
    parts = [
        {
            "part": p["part"],
            "language": p["language"],
            "provider": p["provider"],
            "format": p["format"],
            "chars": len(p["content"] or ""),
            "preview": (p["content"] or "")[:PREVIEW_CHARS],
        }
        for p in _rows(project, "text_parts")
        if p["text_id"] == text_id
    ]
    authors = sorted(
        (
            {
                "person_id": a["person_id"],
                "name": names.get(a["person_id"], a["person_id"]),
                "position": a["position"],
                "orgs": list(a["orgs"] or []),
            }
            for a in _rows(project, "authorships")
            if a["text_id"] == text_id
        ),
        key=lambda a: (a["position"] or 0, a["person_id"]),
    )
    log = _merge_log(project)
    all_texts = {
        t["text_id"]: t for t in _rows(project, "texts", ["text_id", "title", "year", "version_of"])
    }
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
            {"text_id": t["text_id"], "title": t["title"], "year": t["year"]}
            for t in all_texts.values()
            if t["version_of"] == text_id
        ],
        "parts": parts,
        "people": authors,
        "merges": [m for m in log.get("merges", []) if m.get("kept") == text_id],
        "conflicts": [c for c in log.get("conflicts", []) if c.get("text_id") == text_id],
    }


def person_detail(project: Project, person_id: str, cache: Any = None) -> dict[str, Any] | None:
    """One person's sheet: the coverage and its first blocking cause, the sources used and
    discarded, the attempts, the texts, the affiliations with their years."""
    from cartolex.collect.coverage import person_sheet

    people = {p["person_id"]: p for p in _rows(project, "people")}
    row = people.get(person_id)
    if row is None:
        return None
    try:
        sheet = person_sheet(project, person_id)
    except ValueError:
        sheet = None  # merged into another person: the sheet is theirs
    orgs = {o["org_id"]: o for o in _rows(project, "organisations")}
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
            for a in _rows(project, "affiliations")
            if a["person_id"] == person_id
        ),
        key=lambda a: (-(a["start_year"] or 0), a["name"]),
    )
    mine = {
        a["text_id"]
        for a in _rows(project, "authorships", ["text_id", "person_id"])
        if a["person_id"] == person_id
    }
    own = [
        {
            k: t[k]
            for k in ("text_id", "title", "year", "doc_type", "source", "content", "providers")
        }
        for t in texts(project, cache)
        if t["text_id"] in mine
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
