# SPDX-License-Identifier: MIT
"""The atlas bundle: what the map needs, cached by lineage (the runs it is made from).

The bundle (``cartolex-atlas/3``) reads only the theme files of any depth
(``themes_applied.json``, ``theme_keywords.csv``, ``theme_people.parquet``,
``theme_organisations.parquet``, the ``levels`` of ``positions.json``), never the
two-level files that exist at depth 2 only. Every weight it gives on the theme tree
is a **usage share**: for each level, the share of a person's (an organisation's)
usage that counts toward each node of that level, as ``{node id: share}``.

The people's time windows are not in the bundle, which only counts them: a map of a
hundred thousand people has millions. ``GET /api/atlas/windows`` gives them as columns
(``cartolex-atlas-windows/1``), every one or one person's, each with its texts, its
place and its largest top-level node (``trajectory_themes.parquet``).
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse

from cartolex.lexicon.utils import unit_value

from ..deps import ProjectDep
from ..errors import ApiError
from ..messages import empty
from ..routing import Routes, runtime_of

routes = Routes(tags=["atlas"])

#: The stages whose results the bundle reads; their run ids are its lineage.
LINEAGE = ("corpus.assemble", "themes.apply", "map.layout", "map.trajectories", "overlays.position")
FORMAT = "cartolex-atlas/3"
TEXTS_FORMAT = "cartolex-atlas-texts/1"
WINDOWS_FORMAT = "cartolex-atlas-windows/1"
#: The most regions one request asks for.
MAX_REGIONS = 500
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
    identity = _identities(ctx)
    people_rows = _parquet(
        applyf / "theme_people.parquet", ["researcher_id", "level", "node", "share"]
    )
    by_researcher = _grouped(people_rows, lambda r: r[0])
    people = []
    engine_to_person: dict[str, str] = {}
    for r in _rows(mapf / "umap_individuals.csv"):
        rid = r.get("id", "")
        pid = identity.get(_identity_key(r), "")
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
    # Their categories (concept, method, object, place, field), when an AI or a person gave one.
    categories = _json(layout.stage("keywords.build") / "categories.json")
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
                "category": categories.get(r["term"].strip().lower()) or None,
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

    # Time windows: only counted here (``GET /api/atlas/windows`` gives them).
    windows, window_years = _windows_count(
        layout, set(r for r, pid in engine_to_person.items() if pid)
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
        "windows": windows,
        "window_years": window_years,
        "overlays": overlays,
        "bounds": {
            "xmin": min(xs, default=None),
            "xmax": max(xs, default=None),
            "ymin": min(ys, default=None),
            "ymax": max(ys, default=None),
        },
    }


def _identity_key(row: dict[str, str]) -> tuple[str, str, str]:
    """A row's (last name, first name, unit), the unit as the engine names it: a person
    without one is ``NA`` in the engine's tables and empty in the corpus's."""
    return (row["last_name"], row["first_name"], unit_value(row["unit"]))


def _identities(ctx: Any) -> dict[tuple[str, str, str], str]:
    """The engine's identity of each person (:func:`_identity_key`) → their id."""
    corpus = ctx.layout.stage("corpus.assemble")
    identity: dict[tuple[str, str, str], str] = {}
    for slot in ctx.project.config.slots:
        for r in _rows(corpus / slot.id / "people.csv"):
            identity[_identity_key(r)] = r["person_id"]
    return identity


def _trajectory_points(layout: Any) -> Any:
    """``umap_trajectories.csv`` as an Arrow table (its columns the windows need), or ``None``."""
    path = layout.stage("map.trajectories") / "umap_trajectories.csv"
    if not path.is_file():
        return None
    import pyarrow as pa
    import pyarrow.csv as pacsv

    columns = ["researcher_id", "bin_start", "bin_end", "n_docs", "umap_x", "umap_y"]
    return pacsv.read_csv(
        path,
        convert_options=pacsv.ConvertOptions(
            include_columns=columns,
            column_types={"researcher_id": pa.string()},
        ),
    )


def _windows_count(layout: Any, mapped: set[str]) -> tuple[int, dict[str, int] | None]:
    """How many time windows of mapped people are placed, and their first and last years."""
    import pyarrow as pa
    import pyarrow.compute as pc

    points = _trajectory_points(layout)
    if points is None or not mapped:
        return 0, None
    keep = pc.and_(
        pc.is_in(points["researcher_id"], value_set=pa.array(sorted(mapped), pa.string())),
        pc.is_valid(points["umap_x"]),
    )
    points = points.filter(keep)
    if not points.num_rows:
        return 0, None
    return points.num_rows, {
        "min": int(pc.min(points["bin_start"]).as_py()),
        "max": int(pc.max(points["bin_end"]).as_py()),
    }


#: Rows of the trajectories' themes read at a time by the windows layer.
THEME_BATCH = 262_144
_WINDOW_COLUMNS = ("person", "start", "end", "texts", "x", "y", "top")


def build_windows(ctx: Any) -> dict[str, Any]:
    """Every placed time window of the mapped people, as arrays: ``person`` (an index in
    the bundle's people), ``start``, ``end``, ``texts``, ``x``, ``y`` and ``top`` (an
    index in ``tops``, the largest top-level node's id; ``-1``: none). People, windows
    and nodes are codes, never a string per window."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    layout = ctx.layout
    empty = {k: np.zeros(0, dtype=np.int64) for k in _WINDOW_COLUMNS}
    empty["tops"] = []
    points = _trajectory_points(layout)
    if points is None:
        return empty
    identity = _identities(ctx)
    order: dict[str, int] = {}
    for k, r in enumerate(_rows(layout.stage("map.layout") / "umap_individuals.csv")):
        if identity.get(_identity_key(r)):
            order.setdefault(r.get("id", ""), k)
    rid = pc.dictionary_encode(points["researcher_id"].combine_chunks())
    rid_names = rid.dictionary.to_pylist()
    person_of = np.array([order.get(r, -1) for r in rid_names], dtype=np.int64)
    person = (
        person_of[rid.indices.to_numpy(zero_copy_only=False)] if len(rid_names) else empty["person"]
    )
    x = points["umap_x"].to_numpy(zero_copy_only=False).astype(np.float64)
    keep = (person >= 0) & ~np.isnan(x)
    start = points["bin_start"].to_numpy(zero_copy_only=False).astype(np.int64)
    end = points["bin_end"].to_numpy(zero_copy_only=False).astype(np.int64)
    # Each window's largest top-level node: the level-1 rows, by person code and window
    # code, the largest share first.
    top = np.full(len(person), -1, dtype=np.int64)
    tops: list[str] = []
    themes = layout.stage("map.trajectories") / "trajectory_themes.parquet"
    if themes.is_file() and len(person):
        # The points' windows, named as the rows name them ("start_end"), and their keys.
        spans, w_point = np.unique(np.stack([start, end], axis=1), axis=0, return_inverse=True)
        w_names = pa.array([f"{a}_{b}" for a, b in spans.tolist()], pa.string())
        p_key = rid.indices.to_numpy(zero_copy_only=False).astype(np.int64) * len(spans)
        p_key += w_point.reshape(-1)
        # The level-1 rows read in batches (tens of millions at national scale): of each
        # batch, its best row per window; of those, the best.
        nodes: dict[str, int] = {}
        found: list[tuple[Any, Any, Any]] = []
        parquet = pq.ParquetFile(themes)
        for batch in parquet.iter_batches(
            batch_size=THEME_BATCH, columns=["researcher_id", "window", "level", "node", "share"]
        ):
            batch = batch.filter(pc.equal(batch["level"], 1))
            if not batch.num_rows:
                continue
            r = pc.index_in(batch["researcher_id"].cast(pa.string()), value_set=rid.dictionary)
            w = pc.index_in(batch["window"].cast(pa.string()), value_set=w_names)
            r = r.fill_null(-1).to_numpy(zero_copy_only=False).astype(np.int64)
            w = w.fill_null(-1).to_numpy(zero_copy_only=False).astype(np.int64)
            node = pc.dictionary_encode(batch["node"].cast(pa.string()))
            codes = [nodes.setdefault(str(v), len(nodes)) for v in node.dictionary.to_pylist()]
            n = np.asarray(codes, dtype=np.int64)[node.indices.to_numpy(zero_copy_only=False)]
            share = batch["share"].to_numpy(zero_copy_only=False).astype(np.float64)
            ok = (r >= 0) & (w >= 0)
            found.append(_best_per_key(r[ok] * len(spans) + w[ok], share[ok], n[ok]))
        if found:
            key, _share, best = _best_per_key(
                *(np.concatenate(c) for c in zip(*found, strict=True))
            )
            tops = list(nodes)
            if len(key):
                at = np.minimum(np.searchsorted(key, p_key), len(key) - 1)
                hit = key[at] == p_key
                top[hit] = best[at[hit]]
    sort = np.lexsort((start[keep], person[keep]))
    picked = np.flatnonzero(keep)[sort]
    return {
        "person": person[picked],
        "start": start[picked],
        "end": end[picked],
        "texts": points["n_docs"].to_numpy(zero_copy_only=False).astype(np.int64)[picked],
        "x": np.round(x[picked], XY_DIGITS),
        "y": np.round(
            points["umap_y"].to_numpy(zero_copy_only=False).astype(np.float64)[picked], XY_DIGITS
        ),
        "top": top[picked],
        "tops": tops,
    }


def _best_per_key(key: Any, share: Any, node: Any) -> tuple[Any, Any, Any]:
    """Of the rows of each key, the one of the largest share (the first of equal ones), by
    key."""
    import numpy as np

    ranked = np.lexsort((-share, key))
    key, share, node = key[ranked], share[ranked], node[ranked]
    first = np.ones(len(key), dtype=bool)
    first[1:] = key[1:] != key[:-1]
    return key[first], share[first], node[first]


def _windows_json(windows: dict[str, Any], rows: Any = None) -> dict[str, list[Any]]:
    """The windows (those at *rows*, every one by default) as the reply's columns."""
    tops = windows["tops"]
    pick = (lambda a: a) if rows is None else (lambda a: a[rows])
    out = {k: pick(windows[k]).tolist() for k in ("person", "start", "end", "texts", "x", "y")}
    out["top"] = [tops[t] if t >= 0 else None for t in pick(windows["top"]).tolist()]
    return out


def _etag(runs: dict[str, str | None], more: list[str | None] | None = None) -> str:
    key = json.dumps({"format": FORMAT, "runs": runs, "more": more or []}, sort_keys=True)
    return f'"atlas-{hashlib.sha256(key.encode()).hexdigest()[:32]}"'


def _bundle(runtime: Any, ctx: Any, runs: dict[str, str | None]) -> dict[str, Any]:
    key = ("atlas", FORMAT, ctx.id, tuple(sorted(runs.items())))
    return runtime.atlas_cache.get(key, lambda: build_bundle(ctx, runs))


def _extras(runtime: Any, ctx: Any, runs: dict[str, str | None], bundle: dict[str, Any]) -> Any:
    """The organisations, filters and years of the map (from the tables: their stamp keys it)."""
    from ..atlas_layers import map_extras
    from ..corpus_view import stamp

    key = ("atlas-extras", ctx.id, tuple(sorted(runs.items())), stamp(ctx.project))
    return runtime.atlas_cache.get(
        key, lambda: map_extras(ctx, bundle["people"], runtime.table_cache)
    )


def _with_base(ctx: Any, bundle: dict[str, Any], base: str | None) -> dict[str, Any]:
    if not base:
        return bundle
    from ..atlas_layers import base_bundle, read_base

    doc = read_base(ctx, base)
    if doc is None:
        raise ApiError.of("base_not_found", base=base)
    return base_bundle(ctx, bundle, doc)


def _base_fp(ctx: Any, base: str | None) -> str | None:
    if not base:
        return None
    entry = next((b for b in ctx.project.config.bases if b.id == base), None)
    path = ctx.layout.root / entry.bundle if entry else None
    if path is None or not path.is_file():
        return "missing"
    st = path.stat()
    return f"{base}:{st.st_size}:{st.st_mtime_ns}"


@routes.get("/api/atlas", action="atlas.read")
def atlas(
    request: Request, ctx: ProjectDep, base: Annotated[str | None, Query(max_length=64)] = None
) -> Response:
    """The data the map draws, cached by its lineage; ``If-None-Match`` gives 304 when unchanged.

    The bundle carries what the atlas page adds (``organisations``, ``organisation_levels``,
    ``columns``, ``people_extra``, ``years``); ``base`` places it on a base's map."""
    from ..corpus_view import stamp

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
    etag = _etag(runs, [str(stamp(ctx.project)), _base_fp(ctx, base)])
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    bundle = _bundle(runtime, ctx, runs)
    extras = _extras(runtime, ctx, runs, bundle)
    placed = _with_base(ctx, bundle, base)
    if base:
        from ..atlas_layers import map_extras

        extras = map_extras(ctx, placed["people"], runtime.table_cache)
    body = {
        **placed,
        "organisations": extras["organisations"],
        "organisation_levels": extras["organisation_levels"],
        "columns": extras["columns"],
        "people_extra": extras["people"],
        "years": extras["years"],
        "bases": [{"id": b.id, "map_version": b.map_version} for b in ctx.project.config.bases],
        "available": True,
    }
    return JSONResponse(body, headers={"ETag": etag})


@routes.get("/api/atlas/texts", action="atlas.read")
def atlas_texts(
    request: Request, ctx: ProjectDep, base: Annotated[str | None, Query(max_length=64)] = None
) -> Response:
    """Every text placed on the map (columnar: ``id``, ``title``, ``year``, ``x``, ``y``, ``by``,
    ``terms``, ``people``), cached like the bundle; ``base`` places them on a base's map."""
    from ..atlas_layers import place_texts
    from ..corpus_view import stamp

    runtime = runtime_of(request)
    runs = lineage(ctx)
    if runs["map.layout"] is None:
        return JSONResponse({"format": TEXTS_FORMAT, "available": False,
                             "empty": empty("empty_no_map")})  # fmt: skip
    etag = _etag(runs, ["texts", str(stamp(ctx.project)), _base_fp(ctx, base)])
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    def make() -> bytes:
        bundle = _with_base(ctx, _bundle(runtime, ctx, runs), base)
        texts = place_texts(ctx, bundle["keywords"], bundle["people"])
        return json.dumps(
            {"format": TEXTS_FORMAT, "available": True, **texts},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

    reply = runtime.atlas_cache.get(
        ("atlas-texts", ctx.id, etag),
        lambda: _kept(ctx.layout.cache / "atlas", "texts", etag, make),
    )
    return Response(reply, media_type="application/json", headers={"ETag": etag})


#: The replies of one kind kept in the project's cache (the latest ones).
KEPT_REPLIES = 4


def _kept(folder: Path, kind: str, etag: str, make: Any) -> bytes:
    """A reply kept in *folder* for *etag* (what it is made from), else made and kept: a
    reply that takes long to make is made once per version of what it reads, not once per
    session of the app."""
    import contextlib
    import os

    name = hashlib.blake2b(etag.encode("utf-8"), digest_size=8).hexdigest()
    path = folder / f"{kind}-{name}.json"
    with contextlib.suppress(OSError):
        body = path.read_bytes()
        if body.startswith(b'{"format"') and body.endswith(b"}"):
            return body
    body = make()
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f".{path.name}.{os.getpid()}")
        part.write_bytes(body)
        os.replace(part, path)
        older = sorted(folder.glob(f"{kind}-*.json"), key=lambda p: p.stat().st_mtime_ns)
        for stale in older[:-KEPT_REPLIES]:
            stale.unlink(missing_ok=True)
    return body


@routes.get("/api/atlas/windows", action="atlas.read")
def atlas_windows(
    request: Request,
    ctx: ProjectDep,
    person: Annotated[str | None, Query(max_length=64)] = None,
    base: Annotated[str | None, Query(max_length=64)] = None,
) -> Response:
    """The people's time windows, as columns (``person``: an index in the bundle's people,
    ``start``, ``end``, ``texts``, ``x``, ``y``, ``top``): every one, or one ``person``'s;
    none on a base's map (they are not placed there)."""
    runtime = runtime_of(request)
    runs = lineage(ctx)
    if runs["map.layout"] is None:
        return JSONResponse({"format": WINDOWS_FORMAT, "available": False,
                             "empty": empty("empty_no_map")})  # fmt: skip
    from ..corpus_view import stamp

    # The people identified on the map come from the tables: their stamp keys the windows.
    etag = _etag(runs, ["windows", person, str(stamp(ctx.project)), _base_fp(ctx, base)])
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    if base:
        body: Any = {k: [] for k in _WINDOW_COLUMNS}
    else:

        def windows() -> dict[str, Any]:
            return runtime.atlas_cache.get(
                ("atlas-windows", ctx.id, tuple(sorted(runs.items()))), lambda: build_windows(ctx)
            )

        if person is None:
            # Every window: the reply made once and kept (megabytes, not lists of objects).
            def make() -> bytes:
                return json.dumps(
                    {"format": WINDOWS_FORMAT, "available": True, **_windows_json(windows())},
                    separators=(",", ":"),
                ).encode()

            reply = runtime.atlas_cache.get(
                ("atlas-windows-reply", ctx.id, etag),
                lambda: _kept(ctx.layout.cache / "atlas", "windows", etag, make),
            )
            return Response(reply, media_type="application/json", headers={"ETag": etag})
        bundle = _bundle(runtime, ctx, runs)
        at = next((i for i, p in enumerate(bundle["people"]) if p["person_id"] == person), None)
        import numpy as np

        every = windows()
        rows = np.flatnonzero(every["person"] == at) if at is not None else np.zeros(0, int)
        body = _windows_json(every, rows)
    return JSONResponse(
        {"format": WINDOWS_FORMAT, "available": True, **body}, headers={"ETag": etag}
    )


@routes.get("/api/atlas/regions", action="atlas.read")
def atlas_regions(
    request: Request,
    ctx: ProjectDep,
    kind: Literal["person", "organisation"],
    ids: Annotated[str, Query(max_length=20_000)],
) -> dict[str, Any]:
    """The keywords a region spans, by id (``ids``: comma-separated, at most 500): a person's
    most used keywords, or those of an organisation's current members."""
    from ..atlas_layers import keyword_sets

    wanted = [i for i in dict.fromkeys(ids.split(",")) if i][:MAX_REGIONS]
    runtime = runtime_of(request)
    runs = lineage(ctx)
    extras = None
    if kind == "organisation" and runs["map.layout"] is not None:
        extras = _extras(runtime, ctx, runs, _bundle(runtime, ctx, runs))
    return {
        "kind": kind,
        "keywords": keyword_sets(ctx, kind, wanted, extras, _terms(runtime, ctx)),
    }


def _terms(runtime: Any, ctx: Any) -> dict[str, list[tuple[str, float]]]:
    """Every person's keywords, the heaviest first, kept per run of the keywords and of the
    corpus (the people's ids), so a selection does not read the keywords' table again."""
    from cartolex.build.records import read_record

    from ..atlas_layers import terms_of_people

    runs = tuple(
        (r.run_id if r else None)
        for r in (read_record(ctx.layout, s) for s in ("keywords.build", "corpus.assemble"))
    )
    return runtime.atlas_cache.get(("atlas-terms", ctx.id, runs), lambda: terms_of_people(ctx))


# ── distances in the space of the themes, and who uses a keyword ─────────────


#: The most nearest one request asks for, and the most people a keyword's answer lists.
MAX_NEAREST = 100
MAX_USERS = 500
Kind = Literal["person", "organisation", "projected"]


def space_of(runtime: Any, ctx: Any) -> Any:
    """The space of the themes with the map's people (:mod:`cartolex.app.space_index`), kept
    per lineage, run of the space and tables; 409 ``no_space`` before the map is built."""
    from ..corpus_view import stamp
    from ..space_index import space_run, space_view

    runs = lineage(ctx)
    run = space_run(ctx.layout)
    if runs["map.layout"] is None or run is None:
        raise ApiError.of("no_space")
    key = ("atlas-space", ctx.id, tuple(sorted(runs.items())), run, stamp(ctx.project))

    def make() -> Any:
        bundle = _bundle(runtime, ctx, runs)
        return space_view(ctx, run, bundle, _extras(runtime, ctx, runs, bundle))

    return runtime.atlas_cache.get(key, make)


def _found(kind: str, id_: str) -> ApiError:
    return ApiError.of("atlas_item_not_found", kind=kind, id=id_)


@routes.get("/api/atlas/neighbours", action="atlas.read")
def atlas_neighbours(
    request: Request,
    ctx: ProjectDep,
    kind: Kind,
    id: Annotated[str, Query(min_length=1, max_length=200)],
    k: Annotated[int, Query(ge=1, le=MAX_NEAREST)] = 10,
) -> dict[str, Any]:
    """The *k* nearest of a person (people), a projected person (people) or an organisation
    (organisations of its level) by the cosine of their vectors in the space of the themes:
    ``items`` of ``{id, name, similarity}``, the nearest first."""
    from ..space_index import nearest

    view = space_of(runtime_of(request), ctx)
    items = nearest(view, ctx, kind, id, k)
    if items is None:
        raise _found(kind, id)
    return {"kind": kind, "id": id, "metric": "cosine", "k": k, "items": items}


def _item(value: str) -> tuple[str, str]:
    kind, _, id_ = value.partition(":")
    if kind not in ("person", "organisation") or not id_:
        raise ApiError.of(
            "invalid_parameters", problems=[f"{value}: not person:<id> or organisation:<id>"]
        )
    return kind, id_


@routes.get("/api/atlas/compare", action="atlas.read")
def atlas_compare(
    request: Request,
    ctx: ProjectDep,
    a: Annotated[str, Query(min_length=3, max_length=220)],
    b: Annotated[str, Query(min_length=3, max_length=220)],
) -> dict[str, Any]:
    """Two people or organisations (``person:<id>``, ``organisation:<id>``) side by side: the
    cosine of their vectors in the space, the cosine and the Jaccard index of their keyword
    use with the keywords they share, the overlap of their top-level themes (Σ min of the
    shares) and the texts with an author on each side."""
    from ..space_index import compare, query_vector

    view = space_of(runtime_of(request), ctx)
    one, two = _item(a), _item(b)
    for kind, id_ in (one, two):
        if query_vector(view, ctx, kind, id_) is None:
            raise _found(kind, id_)
    out = compare(view, ctx, one, two)
    out["texts"]["items"] = _titles(ctx, out["texts"]["items"])
    return {"a": _named(view, *one), "b": _named(view, *two), "metric": "cosine", **out}


def _named(view: Any, kind: str, id_: str) -> dict[str, str]:
    if kind == "person":
        row = view.row_of.get(id_)
        return {"kind": kind, "id": id_, "name": view.name[row] if row is not None else id_}
    return {"kind": kind, "id": id_, "name": (view.orgs.get(id_) or {}).get("name") or id_}


def _titles(ctx: Any, ids: list[str]) -> list[dict[str, Any]]:
    """The title and year of the texts of *ids*, in their order."""
    if not ids or not ctx.layout.table("texts").exists():
        return [{"id": i, "title": "", "year": None} for i in ids]
    import pyarrow as pa
    import pyarrow.compute as pc

    from ..atlas_layers import _batches

    wanted = pa.array(ids, pa.string())
    found: dict[str, dict[str, Any]] = {}
    for batch in _batches(ctx.layout.table("texts"), "texts", ["text_id", "title", "year"]):
        keep = pc.is_in(batch.column(0), value_set=wanted)
        if pc.any(keep).as_py():
            for r in batch.filter(keep).to_pylist():
                found[r["text_id"]] = {
                    "id": r["text_id"],
                    "title": r["title"] or "",
                    "year": r["year"],
                }
        if len(found) == len(ids):
            break
    return [found.get(i) or {"id": i, "title": "", "year": None} for i in ids]


@routes.get("/api/atlas/keyword-people", action="atlas.read")
def atlas_keyword_people(
    request: Request,
    ctx: ProjectDep,
    term: Annotated[str, Query(min_length=1, max_length=300)],
    limit: Annotated[int, Query(ge=1, le=MAX_USERS)] = 50,
) -> dict[str, Any]:
    """The people who use a keyword (or a form merged into it), ranked by the share of
    their keyword use it holds: ``count``, the first ``limit`` (``items``: ``id``, ``name``,
    ``share``) and ``at``, their indexes in the bundle's people (those on the map); ``known``
    is false when the space has no such keyword."""
    from ..space_index import keyword_users

    view = space_of(runtime_of(request), ctx)
    found = keyword_users(view, term, limit)
    if found is None:  # not a keyword of the space: nobody, said plainly (not an error)
        nobody = {"term": term, "count": 0, "items": [], "at": [], "at_capped": False}
        return {"limit": limit, "known": False, **nobody}
    return {"limit": limit, "known": True, **found}


# ── who writes with whom ──────────────────────────────────────────────────────


#: The most partners a page of an answer lists.
MAX_COAUTHORS = 500


def _places(runtime: Any, ctx: Any) -> dict[str, dict[str, str]]:
    """What the map draws, by id: people (``map``, or ``projected`` for a projected
    person placed on the finished map) and organisations (``map``); nothing before the map
    is built."""
    from ..corpus_view import stamp

    runs = lineage(ctx)
    if runs["map.layout"] is None:
        return {"person": {}, "organisation": {}}

    def make() -> dict[str, dict[str, str]]:
        bundle = _bundle(runtime, ctx, runs)
        extras = _extras(runtime, ctx, runs, bundle)
        people: dict[str, str] = {}
        for o in bundle.get("overlays") or []:
            if o.get("person_id") and o.get("x") is not None:
                people[o["person_id"]] = "projected"
        for p in bundle.get("people") or []:
            if p.get("person_id") and p.get("x") is not None:
                people[p["person_id"]] = "map"
        orgs = {o["id"]: "map" for o in extras["organisations"] if o.get("x") is not None}
        return {"person": people, "organisation": orgs}

    key = ("atlas-places", ctx.id, tuple(sorted(runs.items())), stamp(ctx.project))
    return runtime.atlas_cache.get(key, make)


def _people_named(runtime: Any, ctx: Any) -> dict[str, tuple[str, str]]:
    """Every person of the project → (name, role), from the people's view."""
    from ..corpus_view import people_view, stamp

    def make() -> dict[str, tuple[str, str]]:
        view = people_view(ctx.project, runtime.table_cache)
        return {
            p["person_id"]: (
                " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x),
                p.get("role") or "",
            )
            for p in view["people"]
        }

    return runtime.atlas_cache.get(("atlas-people-named", ctx.id, stamp(ctx.project)), make)


@routes.get("/api/atlas/coauthors", action="atlas.read")
def atlas_coauthors(
    request: Request,
    ctx: ProjectDep,
    kind: Literal["person", "organisation"],
    id: Annotated[str, Query(min_length=1, max_length=200)],
    circle: Annotated[int, Query(ge=1, le=3)] = 1,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_COAUTHORS)] = 50,
    offset2: Annotated[int, Query(ge=0)] = 0,
    limit2: Annotated[int, Query(ge=1, le=MAX_COAUTHORS)] = 50,
    offset3: Annotated[int, Query(ge=0)] = 0,
    limit3: Annotated[int, Query(ge=1, le=MAX_COAUTHORS)] = 50,
) -> dict[str, Any]:
    """Who writes with a person (the people of the project who signed a work with them,
    with the works together) or an organisation (the organisations of its level whose
    people signed a work with its people); ``circle=2`` adds the partners of the partners
    (``second``: their ``paths``, through how many partners, and ``via``), ``circle=3`` one
    more ring (``third``), each ring paged on the server; the authors outside the project
    are counted, never listed (see :mod:`cartolex.app.coauthors`)."""
    from ..coauthors import answer, org_graph, person_graph
    from ..corpus_view import organisations

    runtime = runtime_of(request)
    project = ctx.project
    places = _places(runtime, ctx)[kind]

    def drawn(ids: list[str]) -> list[bool]:
        return [i in places for i in ids]

    extra: dict[str, Any] = {}
    if kind == "person":
        people = _people_named(runtime, ctx)
        if id not in people:
            raise ApiError.of("unknown_people", ids=[id])
        graph = person_graph(project, runtime.table_cache)

        def describe(ids: list[str]) -> list[dict[str, Any]]:
            out = []
            for i in ids:
                name, role = people.get(i, ("", ""))
                # A projected person is never named on the map: by their id only.
                shown = None if role == "projected" or places.get(i) == "projected" else name
                out.append({"id": i, "name": shown, "role": role, "mapped": role == "mapped",
                            "place": places.get(i)})  # fmt: skip
            return out

    else:
        orgs = {o["org_id"]: o for o in organisations(project, runtime.table_cache)}
        if id not in orgs:
            raise ApiError.of("organisation_not_found", org=id)
        level = orgs[id]["level"] or ""
        graph = org_graph(project, level, runtime.table_cache)
        extra["level"] = level

        def describe(ids: list[str]) -> list[dict[str, Any]]:
            return [
                {"id": i, "name": (orgs.get(i) or {}).get("name") or i,
                 "acronym": (orgs.get(i) or {}).get("acronym") or "", "place": places.get(i)}
                for i in ids
            ]  # fmt: skip

    pages = [(offset, limit), (offset2, limit2), (offset3, limit3)]
    found = answer(graph, id, describe, drawn, depth=circle, pages=pages)
    return {"kind": kind, **extra, **found}
