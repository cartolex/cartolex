# SPDX-License-Identifier: MIT
"""The theme editor's playground: the grouping previewed with other settings, and a curated
tree carried onto a new grouping.

``POST /api/themes/playground`` regroups the stored space (refitting it when the
space's unit changes) with the settings sent, in a job of the ``preview`` group,
cached by what it was computed from; nothing is saved
(:mod:`cartolex.app.playground`). ``POST /api/themes/carry`` puts a proposal in
place of a curated tree, keeping the curator's names, set-asides, attributions
and reviews wherever a node continues
(:mod:`cartolex.project.themes_carry`).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..jobs import JobConflict, JobControl
from ..routing import Routes, runtime_of
from .build import busy_error
from .themes import _parse_tree

routes = Routes(tags=["themes"])

Settings = Annotated[dict[str, Annotated[dict[str, Any], Field(max_length=8)]], Field(max_length=2)]


class PlaygroundBody(BaseModel):
    """The settings to preview, per stage (``themes.space``: ``space_unit``; ``themes.group``:
    ``depth``, ``top_groups``, ``keywords_per_group``, ``level_sizes``, ``comb``,
    ``comb_theta``), laid over ``params.json`` (``null``: back to the default or the rule);
    with ``tree``, the tree to compare the preview with."""

    settings: Settings = {}
    tree: dict[str, Any] | None = None


def _refused(exc: Exception) -> ApiError:
    from ..playground import PreviewNeedsBuild

    if isinstance(exc, PreviewNeedsBuild):
        return ApiError.of("preview_needs_build", stage=exc.stage)
    return ApiError.of("invalid_parameters", problems="; ".join(getattr(exc, "problems", [])))


def _against(tree: dict[str, Any] | None, preview: dict[str, Any]) -> dict[str, int] | None:
    from cartolex.project.themes_carry import against

    if tree is None:
        return None
    return against(_parse_tree(tree), preview["tree"])


@routes.post("/api/themes/playground", action="themes.write")
def playground(request: Request, ctx: ProjectDep, body: PlaygroundBody) -> JSONResponse:
    """The grouping's proposal with these settings, on the stored space (refitted first when
    the space's unit changes): 200 ``{preview, against}`` when it was computed from the
    current results, else 202 ``{job}`` (send the same request once the job ends). Nothing is
    saved. ``preview``: ``tree``, ``settings`` (each stage's effective values),
    ``space_refit``, ``theta`` and ``seconds``; ``against``: how it differs from ``tree``."""
    from ..playground import PreviewNeedsBuild, PreviewRefused, cache_key, group_preview
    from ..playground import preview_settings as effective_of

    runtime = runtime_of(request)
    year = runtime.settings.build_year
    try:
        effective = effective_of(ctx.project, runtime.registry, body.settings, year)
    except (PreviewRefused, PreviewNeedsBuild) as exc:
        raise _refused(exc) from exc
    key = cache_key(ctx.id, effective)
    found = runtime.preview_cache.peek(key)
    if found is not None:
        return JSONResponse({"preview": found, "against": _against(body.tree, found)})
    settings = body.settings

    def work(control: JobControl) -> dict[str, Any]:
        def progress(fraction: float, message: str) -> None:
            control.progress({"fraction": round(float(fraction), 3), "message": message})

        result = group_preview(
            ctx.project,
            runtime.registry,
            settings,
            year=year,
            progress=progress,
            cancel=control.cancel,
        )
        if control.cancelled:
            return {"outcome": "cancelled"}
        runtime.preview_cache.put(key, result)
        return {"seconds": result["seconds"], "space_refit": result["space_refit"]}

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="preview",
            work=work,
            title="preview a grouping",
            group="preview",
            title_code="preview_grouping",
            title_params={"refit": int(effective["space_refit"])},
        )
    except JobConflict as exc:
        raise busy_error(exc.running) from exc
    return JSONResponse({"job": info.as_dict()}, status_code=202)


class CarryBody(BaseModel):
    """A curated tree and a proposal to put in its place."""

    tree: dict[str, Any]
    proposal: dict[str, Any]


@routes.post("/api/themes/carry", action="themes.read")
def carry(body: CarryBody, ctx: ProjectDep) -> dict[str, Any]:
    """The proposal with the curator's names, set-asides, attributions and reviews of the tree
    carried wherever a node continues (``tree``), what was carried (``carried``: ``names``,
    ``set_aside``, ``attributions``, ``reviews``, and ``dropped``, the attributions of
    keywords whose node does not continue) and how the proposal differs from the tree
    (``against``). Nothing is saved."""
    from cartolex.project.themes_carry import against, carry_curation

    tree, proposal = _parse_tree(body.tree), _parse_tree(body.proposal)
    carried = carry_curation(tree, proposal)
    return {**carried.as_json(), "against": against(tree, proposal)}
