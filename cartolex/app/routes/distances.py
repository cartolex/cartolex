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

Both are cached by lineage with the bundle, and answer 304 to ``If-None-Match``.
"""

from __future__ import annotations

import base64
from typing import Any, Literal

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from ..deps import ProjectDep
from ..routing import Routes, runtime_of
from .atlas import _bundle, _etag, _extras, lineage, space_of

routes = Routes(tags=["atlas"])

Kind = Literal["person", "organisation"]


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
