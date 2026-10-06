# SPDX-License-Identifier: MIT
"""What the atlas page adds to the map bundle: organisations, filters, texts, regions, bases.

The map bundle (:mod:`cartolex.app.routes.atlas`) holds what the engine placed:
people, keywords, the engine's units and the time windows. The atlas page also
shows, from the project's tables:

- **organisations** at every level, each placed at the mean of the mapped
  people affiliated to it now (directly or through an organisation below it);
  its ``members`` count says exactly that, and ``members_ever`` counts the
  people of the corpus ever affiliated to it (the same way, past affiliations
  included, whether on the map or not);
- the people's **columns** (the extra columns of their lists), their role and
  their current organisations, for the filters;
- **texts**, placed at the mean of the keywords found in their title and
  abstract, else at the mean of their authors on the map;
- the keywords of a person, an organisation or a text (**regions**: the map
  draws the area their keywords span);
- **bases**: another project's map copied into ``sources/bases/<id>/``, on
  which this project's keywords and people are placed by the vocabulary they
  share.

Every function reads the files it is given through the project context and
writes nothing but a base's copy.
"""

from __future__ import annotations

import csv
import functools
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = [
    "BASE_FORMAT",
    "add_base",
    "base_bundle",
    "keyword_sets",
    "map_extras",
    "place_texts",
    "terms_of_people",
    "read_base",
    "remove_base",
]

#: The format of a base's copy (``sources/bases/<id>/base_map.json``).
BASE_FORMAT = "cartolex-base-map/1"
BASE_FILE = "base_map.json"
#: The most keywords a region spans (the heaviest first).
REGION_KEYWORDS = 40
#: The longest keyword looked for in a text, in words.
MAX_TERM_WORDS = 6
XY_DIGITS = 5
_WORD = re.compile(r"\w+(?:['’-]\w+)*", re.UNICODE)


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _mean(points: Iterable[tuple[float, float]]) -> tuple[float, float] | None:
    """The mean of *points*, whatever their order (exact sums: members come from sets)."""
    xs = list(points)
    if not xs:
        return None
    n = len(xs)
    sx = math.fsum(x for x, _ in xs)
    sy = math.fsum(y for _, y in xs)
    return (round(sx / n, XY_DIGITS), round(sy / n, XY_DIGITS))


def _person_ids(ctx: Any) -> dict[tuple[str, str, str], str]:
    """The engine's (last name, first name, unit) → the project's person id."""
    corpus = ctx.layout.stage("corpus.assemble")
    out: dict[tuple[str, str, str], str] = {}
    for slot in ctx.project.config.slots:
        for r in _rows(corpus / slot.id / "people.csv"):
            out[(r["last_name"], r["first_name"], r["unit"])] = r["person_id"]
    return out


# ── organisations and filters ────────────────────────────────────────────────


def map_extras(ctx: Any, people: list[dict[str, Any]], cache: Any = None) -> dict[str, Any]:
    """Organisations placed on the map, the people's filters and the years the map covers.

    *people* are the bundle's people (``person_id``, ``x``, ``y``). Answers
    ``organisations``, ``organisation_levels`` (smallest first, with their names),
    ``columns`` (each extra column's values and counts among the people on the map),
    ``people`` (person id → ``role``, ``columns``, ``orgs``: current organisations)
    and ``years`` (the first and last year of the texts).
    """
    from ..project.tables import read_source_table
    from .corpus_view import effective_affiliation_table, organisations, people_view

    project = ctx.project
    layout = ctx.layout
    on_map = {
        p["person_id"]: (p["x"], p["y"])
        for p in people
        if p.get("person_id") and p.get("x") is not None and p.get("y") is not None
    }
    view = people_view(project, cache)["people"] if layout.table("people").exists() else []
    by_id = {p["person_id"]: p for p in view}

    # Current affiliations, and each organisation's ancestors (an organisation counts the
    # people of the organisations below it).
    orgs = organisations(project, cache) if layout.table("organisations").exists() else []
    parents = {o["org_id"]: list(o["parents"]) for o in orgs}

    @functools.cache
    def ancestors(org_id: str) -> frozenset[str]:  # once per organisation
        seen: set[str] = set()
        todo = [org_id]
        while todo:
            o = todo.pop()
            if o in seen:
                continue
            seen.add(o)
            todo.extend(parents.get(o, []))
        return frozenset(seen)

    now: dict[str, set[str]] = defaultdict(set)
    ever: dict[str, set[str]] = defaultdict(set)
    person_orgs: dict[str, list[str]] = defaultdict(list)
    if layout.table("affiliations").exists():
        rows = effective_affiliation_table(project, ["end_year"]).to_pylist()
        for a in rows:
            for o in ancestors(a["org_id"]):
                ever[o].add(a["person_id"])
            if a["end_year"] is not None:
                continue
            person_orgs[a["person_id"]].append(a["org_id"])
            for o in ancestors(a["org_id"]):
                now[o].add(a["person_id"])

    placed = []
    for o in orgs:
        members = [pid for pid in now.get(o["org_id"], ()) if pid in on_map]
        xy = _mean(on_map[pid] for pid in members)
        placed.append(
            {
                "id": o["org_id"],
                "name": o["name"],
                "acronym": o["acronym"],
                "level": o["level"],
                "parents": o["parents"],
                "x": xy[0] if xy else None,
                "y": xy[1] if xy else None,
                "members": len(members),
                "members_ever": len(ever.get(o["org_id"], ())),
                "location": None,
            }
        )
    if placed:
        table = read_source_table(
            layout.table("organisations"), "organisations", ["org_id", "location"]
        ).to_pylist()
        where = {r["org_id"]: r["location"] for r in table}
        for o in placed:
            loc = where.get(o["id"])
            if loc and loc.get("lat") is not None and loc.get("lon") is not None:
                o["location"] = {"lat": round(loc["lat"], 4), "lon": round(loc["lon"], 4)}

    declared = [lv.id for lv in project.config.levels]
    seen_levels = []
    for o in placed:
        if o["level"] and o["level"] not in declared and o["level"] not in seen_levels:
            seen_levels.append(o["level"])
    names = {lv.id: dict(lv.names) for lv in project.config.levels}
    levels = [
        {"id": lv, "names": names.get(lv, {}), "count": sum(1 for o in placed if o["level"] == lv)}
        for lv in [*declared, *seen_levels]
    ]

    counts: dict[str, dict[str, int]] = {}
    extras: dict[str, dict[str, Any]] = {}
    for pid in on_map:
        p = by_id.get(pid) or {}
        columns = dict(p.get("columns") or {})
        extras[pid] = {
            "role": p.get("role", ""),
            "columns": columns,
            "orgs": sorted(person_orgs.get(pid, [])),
        }
        for key, value in columns.items():
            if value:
                column = counts.setdefault(key, {})
                column[value] = column.get(value, 0) + 1
    columns_out = [
        {
            "column": key,
            "values": [
                {"value": v, "count": n}
                for v, n in sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))[:50]
            ],
        }
        for key, values in sorted(counts.items())
        if 1 < len(values) <= 200  # a column with one value, or one per person, filters nothing
    ]

    years: list[int] = []
    if layout.table("texts").exists():
        texts = read_source_table(layout.table("texts"), "texts", ["year"]).column("year")
        years = [y for y in texts.to_pylist() if y]
    return {
        "organisations": placed,
        "organisation_levels": levels,
        "columns": columns_out,
        "people": extras,
        "years": {"min": min(years, default=None), "max": max(years, default=None)},
    }


# ── keywords of people, organisations and texts ─────────────────────────────


def terms_of_people(ctx: Any) -> dict[str, list[tuple[str, float]]]:
    """Person id → their keywords and scores, the heaviest first (from the keywords stage)."""
    ids = _person_ids(ctx)
    out: dict[str, list[tuple[str, float]]] = defaultdict(list)
    path = ctx.layout.stage("keywords.build") / "keywords_by_researcher_restricted.csv"
    for r in _rows(path):
        pid = ids.get((r["last_name"], r["first_name"], r["unit"]))
        if not pid:
            continue
        try:
            score = float(r.get("score") or 0)
        except ValueError:
            continue
        out[pid].append((r["term"], score))
    for terms in out.values():
        terms.sort(key=lambda ts: -ts[1])
    return dict(out)


class _Matcher:
    """Finds the map's keywords (and their aliases) in a text, by runs of up to six words."""

    def __init__(self, terms: Iterable[str], aliases: dict[str, str]) -> None:
        self.lookup: dict[tuple[str, ...], str] = {}
        for term in terms:
            self.lookup[tuple(_WORD.findall(term.lower()))] = term
        for alias, canonical in aliases.items():
            key = tuple(_WORD.findall(alias.lower()))
            if canonical in terms and key not in self.lookup:
                self.lookup[key] = canonical
        self.longest = min(MAX_TERM_WORDS, max((len(k) for k in self.lookup), default=1))
        #: Every run of words a keyword starts with: a longer run is looked up only after it.
        self.starts = {key[:n] for key in self.lookup for n in range(1, len(key) + 1)}

    def find(self, text: str) -> set[str]:
        words = _WORD.findall(text.lower())
        found: set[str] = set()
        get, starts = self.lookup.get, self.starts
        for i in range(len(words)):
            for n in range(1, min(self.longest, len(words) - i) + 1):
                run = tuple(words[i : i + n])
                if run not in starts:
                    break
                term = get(run)
                if term is not None:
                    found.add(term)
        return found


#: The most texts the map draws: a larger corpus is drawn by a sample, the same each time
#: while the texts are the same (a uniform draw with a fixed seed).
MAX_TEXTS = 100_000


def place_texts(
    ctx: Any,
    keywords: list[dict[str, Any]],
    people: list[dict[str, Any]],
    *,
    limit: int = MAX_TEXTS,
) -> dict[str, Any]:
    """The texts placed on the map (at most *limit*: a sample of a larger corpus): at the
    mean of the keywords found in its title and abstract (``by: keywords``), else at the
    mean of its authors on the map (``by: authors``).

    Columnar, for large corpora: ``id``, ``title``, ``year``, ``x``, ``y``, ``by`` (0 keywords,
    1 authors), ``terms`` (indexes into *keywords* of the terms found) and ``people`` (the
    authors on the map); ``total`` texts in the tables, ``sampled`` when only some are
    drawn. Texts placed neither way are counted in ``unplaced``. Only the texts drawn
    have their parts read, a row group at a time.
    """
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from ..project.tables import check_source_file, read_source_table

    layout = ctx.layout
    out: dict[str, Any] = {
        "id": [], "title": [], "year": [], "x": [], "y": [], "by": [], "terms": [], "people": [],
        "unplaced": 0, "total": 0, "sampled": False,
    }  # fmt: skip
    if not layout.table("texts").exists():
        return out
    texts = read_source_table(layout.table("texts"), "texts", ["text_id", "title", "year"])
    out["total"] = texts.num_rows
    if texts.num_rows > limit:
        rows = np.sort(np.random.default_rng(0).choice(texts.num_rows, limit, replace=False))
        texts = texts.take(pa.array(rows))
        out["sampled"] = True
    wanted = texts["text_id"].combine_chunks()
    at = {
        k["term"]: (i, k["x"], k["y"])
        for i, k in enumerate(keywords)
        if k.get("x") is not None and k.get("y") is not None
    }
    aliases = {
        r["alias"]: r["canonical"]
        for r in _rows(layout.stage("keywords.build") / "models" / "term_aliases.csv")
    }
    matcher = _Matcher(at.keys(), aliases)
    on_map = {
        p["person_id"]: (p["x"], p["y"])
        for p in people
        if p.get("person_id") and p.get("x") is not None
    }
    authors: dict[str, list[str]] = defaultdict(list)
    if layout.table("authorships").exists():
        mapped = pa.array(sorted(on_map), pa.string())
        for batch in _batches(layout.table("authorships"), "authorships", ["text_id", "person_id"]):
            keep = pc.and_(
                pc.is_in(batch.column(0), value_set=wanted),
                pc.is_in(batch.column(1), value_set=mapped),
            )
            if pc.any(keep).as_py():
                rows_ = batch.filter(keep)
                for tid, pid in zip(
                    rows_.column(0).to_pylist(), rows_.column(1).to_pylist(), strict=True
                ):
                    authors[tid].append(pid)
    content: dict[str, list[str]] = defaultdict(list)
    path = layout.table("text_parts")
    if path.exists():
        check_source_file(path, "text_parts")
        pf = pq.ParquetFile(path)
        for group in range(pf.num_row_groups):
            ids = pf.read_row_group(group, columns=["text_id"]).column(0)
            mine = pc.is_in(ids, value_set=wanted)
            if not pc.any(mine).as_py():
                continue
            part = pf.read_row_group(group, columns=["text_id", "part", "content"]).filter(mine)
            for tid, kind, text in zip(
                part.column(0).to_pylist(),
                part.column(1).to_pylist(),
                part.column(2).to_pylist(),
                strict=True,
            ):
                if kind in ("title", "abstract") and text:
                    content[tid].append(text)
        pf.close()
    for t in texts.to_pylist():
        tid = t["text_id"]
        found = matcher.find(" \n ".join(content.get(tid) or [t["title"] or ""]))
        xy = _mean((at[term][1], at[term][2]) for term in found)
        by = 0
        if xy is None:
            xy = _mean(on_map[pid] for pid in authors.get(tid, []))
            by = 1
        if xy is None:
            out["unplaced"] += 1
            continue
        out["id"].append(tid)
        out["title"].append(t["title"] or "")
        out["year"].append(t["year"])
        out["x"].append(xy[0])
        out["y"].append(xy[1])
        out["by"].append(by)
        out["terms"].append(sorted(at[term][0] for term in found))
        out["people"].append(sorted(set(authors.get(tid, []))))
    return out


def _batches(path: Any, name: str, columns: list[str]) -> Any:
    """A source table's rows, a batch at a time (its file checked first)."""
    import pyarrow.parquet as pq

    from ..project.tables import check_source_file

    check_source_file(path, name)
    pf = pq.ParquetFile(path)
    try:
        yield from pf.iter_batches(batch_size=262_144, columns=columns)
    finally:
        pf.close()


def keyword_sets(
    ctx: Any,
    kind: str,
    ids: list[str],
    extras: dict[str, Any] | None = None,
    by_person: dict[str, list[tuple[str, float]]] | None = None,
) -> dict[str, list[str]]:
    """The keywords a person or an organisation's current members use most (at most
    ``REGION_KEYWORDS``, the heaviest first), by id; an unknown id gets none. *by_person*:
    every person's keywords (:func:`terms_of_people`), when the caller keeps them."""
    from .space_index import members_of

    if by_person is None:
        by_person = terms_of_people(ctx)
    if kind == "person":
        return {i: [t for t, _ in by_person.get(i, [])[:REGION_KEYWORDS]] for i in ids}
    members = members_of(extras)
    out: dict[str, list[str]] = {}
    for org in ids:
        total: dict[str, float] = defaultdict(float)
        for pid in members.get(org, ()):
            for term, score in by_person.get(pid, []):
                total[term] += score
        ranked = sorted(total.items(), key=lambda ts: (-ts[1], ts[0]))
        out[org] = [t for t, _ in ranked[:REGION_KEYWORDS]]
    return out


# ── bases: another project's map ─────────────────────────────────────────────


def add_base(ctx: Any, folder: Path, base_id: str) -> dict[str, Any]:
    """Copy the map of the project in *folder* (its keywords, its people's places without
    their names, its top-level themes) into ``sources/bases/<base_id>/``; the base's entry
    for ``project.json``. Raises ``FileNotFoundError`` when *folder* has no map."""
    from ..build.records import read_record
    from ..project.files import atomic_write_bytes, json_bytes
    from ..project.layout import ProjectLayout

    other = ProjectLayout(folder)
    mapf = other.stage("map.layout")
    terms = _rows(mapf / "umap_terms.csv")
    if not other.project_json.is_file() or not terms:
        raise FileNotFoundError(f"{folder} has no map: build it there first")
    config = json.loads(other.project_json.read_text(encoding="utf-8"))
    record = read_record(other, "map.layout")
    drawn = record.measures.counts.get("version") if record else None
    applied = {}
    if (mapf / "themes_applied.json").is_file():
        applied = json.loads((mapf / "themes_applied.json").read_text(encoding="utf-8"))
    doc = {
        "format": BASE_FORMAT,
        "name": str(config.get("name") or folder.name),
        "map_version": f"v{drawn}" if drawn else "v1",
        "terms": [
            [r["term"], round(float(r["umap_x"]), XY_DIGITS), round(float(r["umap_y"]), XY_DIGITS)]
            for r in terms
        ],
        "people": [
            [round(float(r["umap_x"]), XY_DIGITS), round(float(r["umap_y"]), XY_DIGITS)]
            for r in _rows(mapf / "umap_individuals.csv")
        ],
        "themes": [
            {"names": dict(n.get("names") or {}), "x": n.get("x"), "y": n.get("y")}
            for n in applied.get("nodes") or []
            if int(n.get("level", 1)) == 1 and n.get("x") is not None
        ],
    }
    target = ctx.layout.base(base_id)
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(target / BASE_FILE, json_bytes(doc))
    return {
        "id": base_id,
        "bundle": f"sources/bases/{base_id}/{BASE_FILE}",
        "map_version": doc["map_version"],
    }


def read_base(ctx: Any, base_id: str) -> dict[str, Any] | None:
    """A base's copy, or ``None`` when the project has no such base (or its copy is gone)."""
    base = next((b for b in ctx.project.config.bases if b.id == base_id), None)
    if base is None:
        return None
    path = (ctx.layout.root / base.bundle).resolve()
    if not path.is_relative_to(ctx.layout.root.resolve()) or not path.is_file():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc if doc.get("format") == BASE_FORMAT else None


def remove_base(ctx: Any, base_id: str) -> None:
    """Remove a base's copy (its entry in ``project.json`` is removed by the caller)."""
    import shutil

    target = ctx.layout.base(base_id)
    if target.is_dir():
        shutil.rmtree(target)


def base_bundle(ctx: Any, bundle: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """The bundle placed on a base's map: each keyword the base has at its place there, each
    person at the mean of their keywords' places there (weighted by use), organisations and
    texts follow; keywords the base lacks, people without a shared keyword and the time
    windows are not placed. The base's own people come as ``base_people`` (places only)."""
    at = {t: (x, y) for t, x, y in base["terms"]}
    keywords = [
        {**k, "x": at[k["term"]][0] if k["term"] in at else None,
         "y": at[k["term"]][1] if k["term"] in at else None}
        for k in bundle["keywords"]
    ]  # fmt: skip
    by_person = terms_of_people(ctx)
    people = []
    for p in bundle["people"]:
        sx = sy = w = 0.0
        for term, score in by_person.get(p["person_id"], []):
            if term in at and score > 0:
                sx += at[term][0] * score
                sy += at[term][1] * score
                w += score
        xy = (round(sx / w, XY_DIGITS), round(sy / w, XY_DIGITS)) if w else (None, None)
        people.append({**p, "x": xy[0], "y": xy[1]})
    xs = [x for x, _ in base["people"]] + [t[1] for t in base["terms"]]
    ys = [y for _, y in base["people"]] + [t[2] for t in base["terms"]]
    return {
        **bundle,
        "keywords": keywords,
        "people": people,
        "units": [],
        "windows": 0,
        "window_years": None,
        "overlays": [],
        "nodes": [{**n, "x": None, "y": None} for n in bundle["nodes"]],
        "base": {
            "name": base["name"],
            "map_version": base["map_version"],
            "shared_keywords": sum(1 for k in keywords if k["x"] is not None),
            "people": [p for p in base["people"]],
            "themes": base["themes"],
        },
        "bounds": {
            "xmin": min(xs, default=None),
            "xmax": max(xs, default=None),
            "ymin": min(ys, default=None),
            "ymax": max(ys, default=None),
        },
    }
