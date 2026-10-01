# SPDX-License-Identifier: MIT
"""The method screen: each step's diagnostic from its outputs, and the layout's preview.

The parameters themselves are read and written through ``/api/params`` and the
map versions (``/api/map/versions``); these routes only read what the stages
produced (cached by their runs) and draw a layout preview on a sample.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..jobs import JobConflict, JobControl
from ..routing import Routes, runtime_of
from .build import busy_error

routes = Routes(tags=["method"])

Step = Literal["texts", "keywords", "space", "grouping", "layout"]


@routes.get("/api/method/{step}", action="params.read")
def get_step(request: Request, ctx: ProjectDep, step: Step) -> dict[str, Any]:
    """One step's diagnostic: ``texts`` (the counts), ``keywords`` (the candidates by band,
    reason and language, the scores' histogram, the vocabulary against its cap, the scoring's
    fixed settings), ``space`` (explained variance by dimension, neighbours kept by the first
    dimensions), ``grouping`` (levels, keywords per group, too broad, outline, dendrogram of
    the top level, the comb's θ calibration), ``layout`` (the pinned version, neighbours kept
    by the map, the previews computed); each with ``empty`` before its stage ran."""
    from ..method import step_view

    return step_view(runtime_of(request), ctx, step)


@routes.get("/api/method/keywords/preview", action="params.read")
def keywords_preview(
    request: Request,
    ctx: ProjectDep,
    min_people: Annotated[int | None, Query(ge=1, le=10**7)] = None,
    min_texts: Annotated[int | None, Query(ge=1, le=10**7)] = None,
    max_share: Annotated[float | None, Query(ge=0.01, le=1.0)] = None,
    max_keywords: Annotated[int | None, Query(ge=10, le=10**7)] = None,
) -> dict[str, Any]:
    """What the keywords' thresholds would keep of the last build's candidates and vocabulary,
    without a new extraction and without saving anything: the counts by band, the vocabulary's
    size, the strongest candidates that would leave and the vocabulary entries that would enter
    or leave. A value only a new extraction can show (a looser window) is named in ``needs``
    (``preview_needs_extraction``) and the last build's value is used in its place."""
    from ..method import keywords_preview as preview_of

    values = {
        "min_people": min_people,
        "min_texts": min_texts,
        "max_share": max_share,
        "max_keywords": max_keywords,
    }
    return preview_of(runtime_of(request), ctx, values)


class PreviewBody(BaseModel):
    """A layout to preview: its method, seed and parameters (see ``GET /api/method/layout``)."""

    method: Literal["umap", "tsne", "tree"]
    seed: Annotated[int, Field(ge=0, lt=2**32)] = 0
    params: Annotated[dict[str, float | int | str | None], Field(max_length=16)] = {}


@routes.post("/api/method/layout/preview", action="map.write")
def preview(request: Request, ctx: ProjectDep, body: PreviewBody) -> JSONResponse:
    """The preview of a layout on a sample of the people, with the map on the same sample: 200
    with ``preview`` when it was computed on the current space, else 202 with the ``job`` that
    computes it (send the same request again once it ends)."""
    from ..method import LAYOUT_DEFAULTS, layout_preview, preview_key, unavailable_methods

    runtime = runtime_of(request)
    missing = unavailable_methods().get(body.method)
    if missing is not None:
        raise ApiError.of("layout_method_unavailable", **missing["params"])
    known = [p["name"] for p in LAYOUT_DEFAULTS[body.method]]
    for key in body.params:
        if key not in known:
            raise ApiError.of(
                "layout_param_unknown",
                method=body.method,
                param=key,
                known=", ".join(known) or "—",
            )
    params = {k: v for k, v in body.params.items() if v is not None}
    models = ctx.layout.stage("themes.space") / "models" / "embeddings.json"
    if not models.exists():
        raise ApiError.of("preview_needs_build", stage="themes.space")
    if (
        body.method == "tree"
        and not (ctx.layout.stage("themes.apply") / "themes_tree.json").exists()
    ):
        raise ApiError.of("preview_needs_build", stage="themes.apply")
    key = preview_key(ctx, body.method, body.seed, params)
    found = runtime.preview_cache.peek(key)
    if found is not None:
        return JSONResponse({"preview": found})

    def work(control: JobControl) -> dict[str, Any]:
        result = layout_preview(ctx, body.method, body.seed, params)
        runtime.preview_cache.put(key, result)
        return {"method": body.method, "overlap": result["overlap"], "sample": result["sample"]}

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="preview",
            work=work,
            title="preview a layout",
            group="preview",
            title_code="preview_layout",
            title_params={"method": body.method},
        )
    except JobConflict as exc:
        raise busy_error(exc.running) from exc
    return JSONResponse({"job": info.as_dict()}, status_code=202)
