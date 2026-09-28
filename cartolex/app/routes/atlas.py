# SPDX-License-Identifier: MIT
"""The atlas bundle: what the map needs, cached by lineage (the runs it is made from).

The bundle (``cartolex-atlas/2``) reads only the theme files of any depth
(``themes_applied.json``, ``theme_keywords.csv``, ``theme_people.parquet``,
``theme_organisations.parquet``, ``trajectory_themes.parquet``, the ``levels``
of ``positions.json``), never the two-level files that exist at depth 2 only.
Every weight it gives on the theme tree is a **usage share**: for each level,
the share of a person's (an organisation's, a time window's) usage that counts
toward each node of that level, as ``{node id: share}``.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from ..deps import ProjectDep
from ..messages import empty
from ..routing import Routes, runtime_of

routes = Routes(tags=["atlas"])

#: The stages whose results the bundle reads; their run ids are its lineage.
LINEAGE = ("corpus.assemble", "themes.apply", "map.layout", "map.trajectories", "overlays.position")
FORMAT = "cartolex-atlas/2"
#: The top keywords each node lists.
TOP_KEYWORDS = 10
#: Decimals kept for weights and shares, and for map coordinates.
SHARE_DIGITS = 6
XY_DIGITS = 5


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _num(value: Any, digits: int | None = None) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, digits) if digits is not None else f


def _parquet(path: Path, columns: list[str]) -> list[tuple[Any, ...]]:
    """The rows of a Parquet table, as tuples of *columns* (none when the file is missing)."""
    if not path.is_file():
        return []
    import pyarrow.parquet as pq

    table = pq.read_table(path, columns=columns)
    data = [table.column(c).to_pylist() for c in columns]
    return list(zip(*data, strict=True))


def lineage(ctx: Any) -> dict[str, str | None]:
    from cartolex.build.records import read_record

    out: dict[str, str | None] = {}
    for stage in LINEAGE:
        record = read_record(ctx.layout, stage)
        out[stage] = record.run_id if record else None
    return out


def _shares(depth: int, rows: Iterable[tuple[Any, Any, Any]]) -> list[dict[str, float]]:
    """``[{node: share}]`` per level (1 to *depth*) from ``(level, node, share)`` rows."""
    out: list[dict[str, float]] = [{} for _ in range(depth)]
    for level, node, share in rows:
        s = _num(share, SHARE_DIGITS)
        if s and 1 <= int(level) <= depth:
            out[int(level) - 1][str(node)] = s
    return out


def _grouped(rows: list[tuple[Any, ...]], key_of: Any) -> dict[Any, list[tuple[Any, Any, Any]]]:
    """Rows ``(…, level, node, share)`` grouped by ``key_of(row)``."""
    out: dict[Any, list[tuple[Any, Any, Any]]] = {}
    for row in rows:
        out.setdefault(key_of(row), []).append((row[-3], row[-2], row[-1]))
    return out


def _node(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(entry["id"]),
        "parent": entry.get("parent"),
        "level": int(entry.get("level", 1)),
        "order": int(entry.get("order", 0)),
        "names": dict(entry.get("names") or {}),
        "color": entry.get("color"),
        "weight": _num(entry.get("weight"), SHARE_DIGITS),
        "share": _num(entry.get("share"), SHARE_DIGITS),
        "keywords": int(entry.get("keywords", 0)),
        "keywords_counted": int(entry.get("keywords_counted", 0)),
        "top_keywords": list(entry.get("top_keywords") or [])[:TOP_KEYWORDS],
        "x": _num(entry.get("x"), XY_DIGITS),
        "y": _num(entry.get("y"), XY_DIGITS),
    }


def build_bundle(ctx: Any, runs: dict[str, str | None]) -> dict[str, Any]:
    """The bundle of the current results (read from the stages' folders)."""
    from cartolex.build.records import read_record

    layout = ctx.layout
    config = ctx.project.config
    mapf = layout.stage("map.layout")
    applyf = layout.stage("themes.apply")
    applied = _json(mapf / "themes_applied.json") or _json(applyf / "themes_applied.json")
    depth = int(applied.get("depth") or 0)
    levels = [
        {"level": int(lv.get("level", i)), "names": dict(lv.get("names") or {})}
        for i, lv in enumerate(applied.get("levels") or [], start=1)
    ]
    nodes = [_node(n) for n in applied.get("nodes") or []]

    # People: the map's positions, their project ids, their usage shares per level.
    corpus = layout.stage("corpus.assemble")
    identity: dict[tuple[str, str, str], str] = {}
    for slot in config.slots:
        for r in _rows(corpus / slot.id / "people.csv"):
            identity[(r["last_name"], r["first_name"], r["unit"])] = r["person_id"]
    people_rows = _parquet(
        applyf / "theme_people.parquet", ["researcher_id", "level", "node", "share"]
    )
    by_researcher = _grouped(people_rows, lambda r: r[0])
    people = []
    engine_to_person: dict[str, str] = {}
    for r in _rows(mapf / "umap_individuals.csv"):
        rid = r.get("id", "")
        pid = identity.get((r["last_name"], r["first_name"], r["unit"]), "")
        engine_to_person[rid] = pid
        people.append(
            {
                "person_id": pid,
                "name": f"{r['first_name']} {r['last_name']}".strip(),
                "unit": r["unit"],
                "x": _num(r["umap_x"], XY_DIGITS),
                "y": _num(r["umap_y"], XY_DIGITS),
                "shares": _shares(depth, by_researcher.get(rid, [])),
            }
        )

    # Keywords: their place on the map and on the tree.
    placed = {r["term"]: r for r in _rows(applyf / "theme_keywords.csv")}
    keywords = []
    for r in _rows(mapf / "umap_terms_clustered.csv") or _rows(mapf / "umap_terms.csv"):
        w = placed.get(r["term"]) or {}
        keywords.append(
            {
                "term": r["term"],
                "x": _num(r["umap_x"], XY_DIGITS),
                "y": _num(r["umap_y"], XY_DIGITS),
                "node": w.get("node") or None,
                "level": int(w["level"]) if w.get("level") else None,
                "counts_to": int(w["counts_to"]) if w.get("counts_to") else 0,
                "weight": _num(w.get("weight"), SHARE_DIGITS),
                "share": _num(w.get("share"), SHARE_DIGITS),
            }
        )

    # Organisations (the engine's units): their place and their shares per level.
    unit_rows = _parquet(applyf / "theme_organisations.parquet", ["unit", "level", "node", "share"])
    by_unit = _grouped(unit_rows, lambda r: r[0])
    units = [
        {
            "unit": r["unit"],
            "x": _num(r["umap_x"], XY_DIGITS),
            "y": _num(r["umap_y"], XY_DIGITS),
            "size": int(float(r.get("size") or 0)),
            "ellipse": {
                "sx": _num(r.get("sx")),
                "sy": _num(r.get("sy")),
                "rho": _num(r.get("rho")),
            },
            "shares": _shares(depth, by_unit.get(r["unit"], [])),
        }
        for r in _rows(mapf / "umap_labs.csv")
    ]

    # Trajectories: each person's time windows, placed, with their shares per level.
    trajf = layout.stage("map.trajectories")
    window_rows = _parquet(
        trajf / "trajectory_themes.parquet", ["researcher_id", "window", "level", "node", "share"]
    )
    by_window = _grouped(window_rows, lambda r: (r[0], r[1]))
    trajectories = []
    for r in _rows(trajf / "umap_trajectories.csv"):
        window = f"{r['bin_start']}_{r['bin_end']}"
        trajectories.append(
            {
                "person_id": engine_to_person.get(r["researcher_id"], ""),
                "start": int(r["bin_start"]),
                "end": int(r["bin_end"]),
                "texts": int(r["n_docs"]),
                "x": _num(r["umap_x"], XY_DIGITS),
                "y": _num(r["umap_y"], XY_DIGITS),
                "shares": _shares(depth, by_window.get((r["researcher_id"], window), [])),
            }
        )

    # Projected people: their place and their shares per level.
    overlays = []
    for overlay in config.overlays:
        doc = _json(layout.stage("overlays.position") / overlay.id / "positions.json")
        for item in doc.get("items", []):
            rows = [
                (lv.get("level", 0), n.get("id"), n.get("share"))
                for lv in item.get("levels") or []
                for n in lv.get("nodes") or []
            ]
            overlays.append(
                {
                    "set": overlay.id,
                    "person_id": item.get("person_id"),
                    "x": _num(item.get("x"), XY_DIGITS),
                    "y": _num(item.get("y"), XY_DIGITS),
                    "shares": _shares(depth, rows),
                }
            )

    xs = [p["x"] for p in people if p["x"] is not None] + [
        k["x"] for k in keywords if k["x"] is not None
    ]
    ys = [p["y"] for p in people if p["y"] is not None] + [
        k["y"] for k in keywords if k["y"] is not None
    ]
    record = read_record(layout, "map.layout")
    drawn = record.measures.counts.get("version") if record else None
    return {
        "format": FORMAT,
        "lineage": runs,
        "map_version": f"v{drawn}" if drawn else None,  # the version the map was drawn with
        "depth": depth,
        "levels": levels,
        "source": applied.get("source") or None,
        "weights_basis": applied.get("weights_basis") or None,
        "people_counted": int(applied.get("people_counted") or 0),
        "nodes": nodes,
        "people": people,
        "keywords": keywords,
        "units": units,
        "trajectories": trajectories,
        "overlays": overlays,
        "bounds": {
            "xmin": min(xs, default=None),
            "xmax": max(xs, default=None),
            "ymin": min(ys, default=None),
            "ymax": max(ys, default=None),
        },
    }


def _etag(runs: dict[str, str | None]) -> str:
    key = json.dumps({"format": FORMAT, "runs": runs}, sort_keys=True)
    return f'"atlas-{hashlib.sha256(key.encode()).hexdigest()[:32]}"'


@routes.get("/api/atlas", action="atlas.read")
def atlas(request: Request, ctx: ProjectDep) -> Response:
    """The data the map draws, cached by its lineage; ``If-None-Match`` gives 304 when unchanged."""
    runtime = runtime_of(request)
    runs = lineage(ctx)
    if runs["map.layout"] is None:
        return JSONResponse(
            {
                "format": FORMAT,
                "available": False,
                "empty": empty("empty_no_map"),
            }
        )
    etag = _etag(runs)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    key = ("atlas", FORMAT, ctx.id, tuple(sorted(runs.items())))
    bundle = runtime.atlas_cache.get(key, lambda: build_bundle(ctx, runs))
    return JSONResponse({**bundle, "available": True}, headers={"ETag": etag})
