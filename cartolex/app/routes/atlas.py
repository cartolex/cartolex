# SPDX-License-Identifier: MIT
"""The atlas bundle: what the map needs, cached by lineage (the runs it is made from)."""

from __future__ import annotations

import csv
import hashlib
import json
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
FORMAT = "cartolex-atlas/1"


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN → None


def lineage(ctx: Any) -> dict[str, str | None]:
    from cartolex.build.records import read_record

    out: dict[str, str | None] = {}
    for stage in LINEAGE:
        record = read_record(ctx.layout, stage)
        out[stage] = record.run_id if record else None
    return out


def _labels(entry: dict[str, Any]) -> dict[str, str]:
    return {k[len("label_") :]: v for k, v in entry.items() if k.startswith("label_") and v}


def build_bundle(ctx: Any, runs: dict[str, str | None]) -> dict[str, Any]:
    """The bundle of the current results (read from the stages' folders)."""
    layout = ctx.layout
    config = ctx.project.config
    corpus = layout.stage("corpus.assemble")
    identity: dict[tuple[str, str, str], str] = {}
    for slot in config.slots:
        for r in _rows(corpus / slot.id / "people.csv"):
            identity[(r["last_name"], r["first_name"], r["unit"])] = r["person_id"]
    mapf = layout.stage("map.layout")
    weights: dict[str, dict[str, float]] = {}
    for r in _rows(mapf / "subfield_weights.csv"):
        w = _num(r.get("weight"))
        if w:
            weights.setdefault(r["researcher_id"], {})[r["subfield_id"]] = round(w, 6)
    people = []
    engine_to_person: dict[str, str] = {}
    for r in _rows(mapf / "umap_individuals.csv"):
        pid = identity.get((r["last_name"], r["first_name"], r["unit"]), "")
        engine_to_person[r.get("id", "")] = pid
        people.append(
            {
                "person_id": pid,
                "name": f"{r['first_name']} {r['last_name']}".strip(),
                "unit": r["unit"],
                "x": _num(r["umap_x"]),
                "y": _num(r["umap_y"]),
                "themes": weights.get(r.get("id", ""), {}),
            }
        )
    applied_path = mapf / "subfields.json"
    applied = json.loads(applied_path.read_text(encoding="utf-8")) if applied_path.is_file() else {}
    themes = [
        {
            "id": str(s["id"]),
            "label": s.get("label", ""),
            "labels": _labels(s),
            "color": s.get("color"),
            "weight": s.get("weight"),
            "share": s.get("share"),
            "top_terms": s.get("top_terms", [])[:10],
        }
        for s in applied.get("subfields", [])
    ]
    topics = [
        {
            "id": str(c["id"]),
            "theme": str(c.get("subfield_id")),
            "label": c.get("label", ""),
            "labels": _labels(c),
            "color": c.get("color"),
            "weight": c.get("weight"),
            "top_terms": c.get("top_terms", [])[:10],
        }
        for c in applied.get("concepts", [])
    ]
    placed = {r["term"]: r for r in _rows(mapf / "lexicon_weights.csv")}
    keywords = []
    for r in _rows(mapf / "umap_terms_clustered.csv") or _rows(mapf / "umap_terms.csv"):
        w = placed.get(r["term"], {})
        keywords.append(
            {
                "term": r["term"],
                "x": _num(r["umap_x"]),
                "y": _num(r["umap_y"]),
                "topic": w.get("concept_id") or None,
                "theme": w.get("subfield_id") or None,
                "weight": _num(w.get("weight")),
            }
        )
    units = [
        {
            "unit": r["unit"],
            "x": _num(r["umap_x"]),
            "y": _num(r["umap_y"]),
            "size": int(float(r.get("size") or 0)),
            "ellipse": {
                "sx": _num(r.get("sx")),
                "sy": _num(r.get("sy")),
                "rho": _num(r.get("rho")),
            },
        }
        for r in _rows(mapf / "umap_labs.csv")
    ]
    trajectories = [
        {
            "person_id": engine_to_person.get(r["researcher_id"], ""),
            "start": int(r["bin_start"]),
            "end": int(r["bin_end"]),
            "texts": int(r["n_docs"]),
            "x": _num(r["umap_x"]),
            "y": _num(r["umap_y"]),
        }
        for r in _rows(layout.stage("map.trajectories") / "umap_trajectories.csv")
    ]
    overlays = []
    for overlay in config.overlays:
        path = layout.stage("overlays.position") / overlay.id / "positions.json"
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            for item in doc.get("items", []):
                overlays.append(
                    {
                        "set": overlay.id,
                        "person_id": item.get("person_id"),
                        "x": item.get("x"),
                        "y": item.get("y"),
                        "themes": item.get("themes"),
                    }
                )
    xs = [p["x"] for p in people if p["x"] is not None] + [
        k["x"] for k in keywords if k["x"] is not None
    ]
    ys = [p["y"] for p in people if p["y"] is not None] + [
        k["y"] for k in keywords if k["y"] is not None
    ]
    from cartolex.build.records import read_record

    record = read_record(layout, "map.layout")
    drawn = record.measures.counts.get("version") if record else None
    return {
        "format": FORMAT,
        "lineage": runs,
        "map_version": f"v{drawn}" if drawn else None,  # the version the map was drawn with
        "people": people,
        "keywords": keywords,
        "themes": themes,
        "topics": topics,
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
    digest = hashlib.sha256(json.dumps(runs, sort_keys=True).encode()).hexdigest()[:32]
    return f'"atlas-{digest}"'


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
    key = ("atlas", ctx.id, tuple(sorted(runs.items())))
    bundle = runtime.atlas_cache.get(key, lambda: build_bundle(ctx, runs))
    return JSONResponse({**bundle, "available": True}, headers={"ETag": etag})
