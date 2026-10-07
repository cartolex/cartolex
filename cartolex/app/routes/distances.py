# SPDX-License-Identifier: MIT
"""What the Map screen's « Distances » tab reads (``docs/dev/atlas.md``, « Distances »): the
same data the offline site carries, so that one piece of code (``static/distances/``)
measures in the browser for both.

- ``GET /api/atlas/vectors?kind=person|organisation``: every person's (organisation's)
  vector in the space of the themes over the atlas bundle's order, int8 rows (each scaled so
  that its largest component is ±127: a cosine does not depend on the scale; zeros for one
  without a place), base64;
- ``GET /api/atlas/links?kind=person|organisation``: who writes with whom over the bundle's
  order, as sparse lists (``ptr``, ``nbr``, ``cnt``: the texts together, the strongest
  first), organisations paired within a level.

Both are cached by lineage with the bundle, and answer 304 to ``If-None-Match``. The
measures a browser cannot compute (``keywords``, ``jaccard``: they need every person's whole
vocabulary) are measured here, within bounds (:mod:`cartolex.app.similarity`):

- ``POST /api/atlas/similarity``: the similarity of each of ``a`` to each of ``b``
  (people, organisations or themes by id; ``b.ids`` null: every person or organisation of the
  bundle), at most :data:`MAX_CELLS` cells, float32 rows as base64;
- ``POST /api/atlas/similar-pairs``: among at most :data:`MAX_SCOPE` people or organisations,
  the most alike pairs that never wrote together (``apart``, every pair measured, at most
  :data:`MAX_PAIRS_SCOPE` of them) or the co-authors the least alike (``together``).
"""

from __future__ import annotations

import base64
from typing import Annotated, Any, Literal

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..routing import Routes, runtime_of
from .atlas import _bundle, _etag, _extras, lineage, space_of

routes = Routes(tags=["atlas"])

Kind = Literal["person", "organisation"]
Measure = Literal["space", "keywords", "jaccard", "themes"]
#: The most cells one similarity answer holds (a field's people against one, a matrix).
MAX_CELLS = 2_000_000
#: The most people or organisations a request names, and whose every pair is measured.
MAX_SCOPE = 250_000
MAX_PAIRS_SCOPE = 5_000
MAX_PAIRS = 500


def _cached(request: Request, ctx: Any, name: str, kind: str, make: Any) -> Response:
    """*make()*'s answer, kept per lineage, tables and kind; 304 when the client has it."""
    from ..corpus_view import stamp
    from ..space_index import space_run

    runtime = runtime_of(request)
    runs = lineage(ctx)
    etag = _etag(runs, [name, kind, str(stamp(ctx.project)), space_run(ctx.layout)])
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    key = ("atlas-distances", name, kind, ctx.id, etag)
    body = runtime.atlas_cache.get(key, make)
    return JSONResponse(body, headers={"ETag": etag})


@routes.get("/api/atlas/vectors", action="atlas.read")
def atlas_vectors(request: Request, ctx: ProjectDep, kind: Kind) -> Response:
    """Every person's or organisation's vector, over the bundle's order: ``{kind, dim,
    count, values}`` (``values``: base64 int8 rows); 409 ``no_space`` before the map."""
    import numpy as np

    from cartolex.site.data import int8_rows

    runtime = runtime_of(request)
    view = space_of(runtime, ctx)  # 409 no_space

    def make() -> dict[str, Any]:
        runs = lineage(ctx)
        bundle = _bundle(runtime, ctx, runs)
        z = np.asarray(view.space.vectors, dtype=np.float32)
        dim = int(z.shape[1]) if z.ndim == 2 else 0
        if kind == "person":
            dense = np.zeros((len(bundle["people"]), dim), np.float32)
            rows = np.flatnonzero(view.at >= 0)
            dense[view.at[rows]] = z[rows]
        else:
            orgs = _extras(runtime, ctx, runs, bundle)["organisations"]
            dense = np.zeros((len(orgs), dim), np.float32)
            for i, o in enumerate(orgs):
                v = view.org_vector(o["id"])
                if v is not None:
                    dense[i] = v
        q = int8_rows(dense) if len(dense) else np.zeros((0, dim), np.int8)
        return {
            "kind": kind,
            "dim": dim,
            "count": len(q),
            "values": base64.b64encode(q.tobytes()).decode("ascii"),
        }

    return _cached(request, ctx, "vectors", kind, make)


@routes.get("/api/atlas/links", action="atlas.read")
def atlas_links(request: Request, ctx: ProjectDep, kind: Kind) -> Response:
    """Who writes with whom over the bundle's order: ``{kind, ptr, nbr, cnt}``; 409
    ``no_space`` before the map."""
    from cartolex.site.data import site_links

    runtime = runtime_of(request)
    space_of(runtime, ctx)  # 409 no_space

    def make() -> dict[str, Any]:
        runs = lineage(ctx)
        bundle = _bundle(runtime, ctx, runs)
        orgs = _extras(runtime, ctx, runs, bundle)["organisations"]
        if kind == "person":
            pids = [p.get("person_id") or "" for p in bundle["people"]]
            links = site_links(ctx.project, pids, [], [], runtime.table_cache)["people"]
        else:
            links = site_links(ctx.project, [], [], orgs, runtime.table_cache)["orgs"]
        return {"kind": kind, **{k: links[k] for k in ("ptr", "nbr", "cnt")}}

    return _cached(request, ctx, "links", kind, make)


def _links(runtime: Any, ctx: Any, kind: str) -> dict[str, Any]:
    """The links over the bundle's order, kept with the vectors' and links' answers."""
    from cartolex.site.data import site_links

    runs = lineage(ctx)
    bundle = _bundle(runtime, ctx, runs)

    def make() -> dict[str, Any]:
        if kind == "person":
            pids = [p.get("person_id") or "" for p in bundle["people"]]
            return site_links(ctx.project, pids, [], [], runtime.table_cache)["people"]
        orgs = _extras(runtime, ctx, runs, bundle)["organisations"]
        return site_links(ctx.project, [], [], orgs, runtime.table_cache)["orgs"]

    key = ("atlas-distances", "links-arrays", kind, ctx.id, tuple(sorted(runs.items())))
    return runtime.atlas_cache.get(key, make)


class Side(BaseModel):
    """One side of a similarity: people, organisations or themes, by id (null: every person
    or organisation of the bundle, in its order)."""

    kind: Literal["person", "organisation", "theme"]
    ids: Annotated[list[str], Field(max_length=MAX_SCOPE)] | None = None


class SimilarityBody(BaseModel):
    measure: Measure
    a: Side
    b: Side


class PairsBody(BaseModel):
    measure: Measure
    kind: Kind
    ids: Annotated[list[str], Field(max_length=MAX_SCOPE)]
    mode: Literal["apart", "together"] = "apart"
    limit: Annotated[int, Field(ge=1, le=MAX_PAIRS)] = 200


def _side_rows(
    runtime: Any, ctx: Any, view: Any, measure: str, side: Side
) -> tuple[Any, Any, list[str]]:
    """The rows of *side* under *measure*, whether each has a place, and its ids."""
    import numpy as np

    from ..similarity import people_rows, rows_of_orgs, theme_rows

    runs = lineage(ctx)
    bundle = _bundle(runtime, ctx, runs)
    if side.kind == "person":
        ids = (
            side.ids
            if side.ids is not None
            else [p.get("person_id") or "" for p in bundle["people"]]
        )
        rows = np.asarray([view.row_of.get(i, -1) for i in ids], dtype=np.int64)
        ok = rows >= 0
        return people_rows(view, measure).take(np.where(ok, rows, 0)), ok, ids
    if side.kind == "organisation":
        orgs = _extras(runtime, ctx, runs, bundle)["organisations"]
        ids = side.ids if side.ids is not None else [o["id"] for o in orgs]
        ok = np.asarray([len(view.member_rows(i)) > 0 for i in ids], dtype=bool)
        return rows_of_orgs(view, measure, ids), ok, ids
    if side.ids is None:
        raise ApiError.of("invalid_parameters", problems=["themes: name them"])
    from scipy import sparse

    nodes = {n["id"]: n for n in bundle["nodes"]}
    wanted: dict[int, dict[str, int]] = {}  # level → node → its row
    for t, node in enumerate(side.ids):
        wanted.setdefault(int((nodes.get(node) or {}).get("level") or 1), {})[node] = t
    r, c, v = [], [], []
    for row in np.flatnonzero(view.at >= 0).tolist():
        shares = bundle["people"][int(view.at[row])].get("shares") or []
        for level, of_level in wanted.items():
            for node, share in (shares[level - 1] if len(shares) >= level else {}).items():
                t = of_level.get(node)
                if t is not None and share:
                    r.append(t)
                    c.append(row)
                    v.append(float(share))
    weights = sparse.csr_matrix((v, (r, c)), shape=(len(side.ids), len(view.space.rids)))
    ok = np.asarray(weights.getnnz(axis=1) > 0)
    return theme_rows(view, measure, weights), ok, side.ids


def _cross(a: Any, b: Any) -> Any:
    """The similarity of each row of *a* to each of *b*, the smaller side measured by blocks."""
    import numpy as np

    if a.measure != b.measure:
        raise ApiError.of(
            "invalid_parameters", problems=["a, b: people or organisations with themes"]
        )
    out = np.zeros((len(a), len(b)), dtype=np.float32)
    if len(a) <= len(b):
        for s in range(0, len(a), 256):
            block = a.take(np.arange(s, min(len(a), s + 256)))
            out[s : s + len(block)] = b.cross(block).T
    else:
        for s in range(0, len(b), 256):
            block = b.take(np.arange(s, min(len(b), s + 256)))
            out[:, s : s + len(block)] = a.cross(block)
    return out


@routes.post("/api/atlas/similarity", action="atlas.read")
def atlas_similarity(request: Request, ctx: ProjectDep, body: SimilarityBody) -> dict[str, Any]:
    """The similarity of each of ``a`` to each of ``b`` by ``measure``: ``{measure, rows,
    cols, values}`` (``values``: float32 rows as base64, NaN where one has no place); 422
    ``invalid_parameters`` past :data:`MAX_CELLS` cells or for themes against people."""
    import numpy as np

    runtime = runtime_of(request)
    view = space_of(runtime, ctx)  # 409 no_space
    if (body.a.kind == "theme") != (body.b.kind == "theme"):
        raise ApiError.of("invalid_parameters", problems=["a, b: themes are compared with themes"])
    ra, oka, ida = _side_rows(runtime, ctx, view, body.measure, body.a)
    if len(ida) * (len(body.b.ids) if body.b.ids is not None else 1) > MAX_CELLS:
        raise ApiError.of("invalid_parameters", problems=[f"a × b: more than {MAX_CELLS} cells"])
    rb, okb, idb = _side_rows(runtime, ctx, view, body.measure, body.b)
    if len(ida) * len(idb) > MAX_CELLS:
        raise ApiError.of("invalid_parameters", problems=[f"a × b: more than {MAX_CELLS} cells"])
    values = _cross(ra, rb) if len(ida) and len(idb) else np.zeros((len(ida), len(idb)), np.float32)
    values[~oka, :] = np.nan
    values[:, ~okb] = np.nan
    return {
        "measure": body.measure,
        "rows": len(ida),
        "cols": len(idb),
        "values": base64.b64encode(np.ascontiguousarray(values, dtype="<f4").tobytes()).decode(
            "ascii"
        ),
    }


@routes.post("/api/atlas/similar-pairs", action="atlas.read")
def atlas_similar_pairs(request: Request, ctx: ProjectDep, body: PairsBody) -> dict[str, Any]:
    """Among ``ids``: ``apart``, the most alike pairs that never wrote together (every pair
    measured: at most :data:`MAX_PAIRS_SCOPE` ids, else 422 ``invalid_parameters``), or
    ``together``, the co-authors the least alike; ``items`` of ``{a, b, similarity, texts}``
    (ids)."""
    import numpy as np

    runtime = runtime_of(request)
    view = space_of(runtime, ctx)  # 409 no_space
    if body.mode == "apart" and len(body.ids) > MAX_PAIRS_SCOPE:
        raise ApiError.of(
            "invalid_parameters", problems=[f"ids: more than {MAX_PAIRS_SCOPE} for every pair"]
        )
    rows, ok, ids = _side_rows(runtime, ctx, view, body.measure, Side(kind=body.kind, ids=body.ids))
    runs = lineage(ctx)
    bundle = _bundle(runtime, ctx, runs)
    if body.kind == "person":
        order = [p.get("person_id") or "" for p in bundle["people"]]
    else:
        order = [o["id"] for o in _extras(runtime, ctx, runs, bundle)["organisations"]]
    at = {i: k for k, i in enumerate(order)}
    links = _links(runtime, ctx, body.kind)
    ptr, nbr, cnt = (np.asarray(links[k], dtype=np.int64) for k in ("ptr", "nbr", "cnt"))
    # the scope's position of each bundle item (-1: outside)
    pos = np.full(len(order), -1, dtype=np.int64)
    for p, i in enumerate(ids):
        if i in at and ok[p]:
            pos[at[i]] = p
    src = np.repeat(np.arange(len(ptr) - 1, dtype=np.int64), np.diff(ptr))
    pa, pb = pos[src], pos[nbr] if len(nbr) else nbr
    linked = (pa >= 0) & (pb >= 0) & (pa < pb)
    pa, pb, texts = pa[linked], pb[linked], cnt[linked]
    found: list[tuple[float, int, int, int]] = []
    if body.mode == "together":
        sims = rows.pairs(pa, pb) if len(pa) else np.zeros(0, np.float32)
        best = np.lexsort((-texts, sims))[: body.limit]
        found = [(float(sims[k]), int(pa[k]), int(pb[k]), int(texts[k])) for k in best]
    else:
        n = len(ids)
        near = set(zip(pa.tolist(), pb.tolist(), strict=True))
        top_s = np.zeros(0, np.float32)
        top_a = np.zeros(0, np.int64)
        top_b = np.zeros(0, np.int64)
        for s in range(0, n, 256):
            cols = np.arange(s, min(n, s + 256))
            block = rows.cross(rows.take(cols))  # n × block: rows × the block's columns
            r, c = np.nonzero(np.ones_like(block, dtype=bool))
            keep = (r < cols[c]) & ok[r] & ok[cols[c]]
            r, c = r[keep], c[keep]
            vals = block[r, c]
            k = min(len(vals), body.limit + len(near))
            if k:
                pick = np.argpartition(-vals, k - 1)[:k]
                top_s = np.r_[top_s, vals[pick]]
                top_a = np.r_[top_a, r[pick]]
                top_b = np.r_[top_b, cols[c[pick]]]
                if len(top_s) > 4 * (body.limit + len(near)):
                    keepk = np.argsort(-top_s, kind="stable")[: body.limit + len(near)]
                    top_s, top_a, top_b = top_s[keepk], top_a[keepk], top_b[keepk]
        for k in np.argsort(-top_s, kind="stable"):
            pair = (int(top_a[k]), int(top_b[k]))
            if pair in near:
                continue
            found.append((float(top_s[k]), pair[0], pair[1], 0))
            if len(found) >= body.limit:
                break
    return {
        "measure": body.measure,
        "mode": body.mode,
        "count": len(ids),
        "items": [
            {"a": ids[a], "b": ids[b], "similarity": round(s, 4), "texts": t}
            for s, a, b, t in found
        ],
    }
