# SPDX-License-Identifier: MIT
"""Sharing: the offline site's builds, the privacy summary and checks, figures, tables and files.

A site build and the files that take time (the map bundle, the project as one
zip) run as jobs; a figure (the map as PNG or SVG) and the theme table are
answered at once. Nothing is ever written over an earlier build or export.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Path as PathParam
from fastapi import Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..jobs import JobConflict, JobControl
from ..messages import empty
from ..routing import Routes, runtime_of
from ..static_files import MEDIA_TYPES, safe_file
from .build import busy_error

routes = Routes(tags=["share"])

BuildId = Annotated[str, PathParam(pattern=r"^[0-9A-Za-z_-]{1,64}$")]
ExportName = Annotated[str, PathParam(pattern=r"^[\w-][\w.-]{0,127}$")]
Names = Literal["names", "pseudonyms"]
Texts = Literal["none", "titles", "abstracts"]
Language = Literal["en", "fr", "pt-BR"]


class SiteBody(BaseModel):
    """A site's options: ``names`` (asked at each build of a people atlas), ``names_projected``
    (pseudonyms unless chosen), the texts carried,
    the title and the language the site opens in."""

    names: Names | None = None
    names_projected: Names = "pseudonyms"
    texts: Texts = "none"
    title: Annotated[str, Field(max_length=120)] = ""
    language: Language = "en"


class ExportBody(BaseModel):
    """A file to write: the map bundle, the project as one zip, or distances in the space
    of the themes (``neighbours``, ``similarity``, ``vectors``) of the people on the map
    (``of: person``; every one, or those of ``ids``) or of the organisations of one
    ``level``, by the project's measure (``similarity`` of ``params.json``; the plan names
    it), with ``<file>.meta.json`` beside the file. People are named or given pseudonyms as ``names`` says (asked: 422
    ``export_names_question``); a similarity matrix above ten million cells needs
    ``confirm`` (409 ``export_size_confirm``, its size said). ``plan`` answers what would
    be written, without writing it."""

    kind: Literal["map_bundle", "project", "neighbours", "similarity", "vectors"]
    of: Literal["person", "organisation"] = "person"
    level: Annotated[str | None, Field(max_length=64)] = None
    k: Annotated[int, Field(ge=1, le=100)] = 10
    names: Names | None = None
    ids: Annotated[list[Annotated[str, Field(max_length=200)]] | None,
                   Field(max_length=500_000)] = None  # fmt: skip
    confirm: bool = False
    plan: bool = False


DISTANCES = ("neighbours", "similarity", "vectors")


def _stale_stages(request: Request, project: Any) -> list[str]:
    from cartolex.site.builder import READS

    from .state import stage_states

    try:
        states = stage_states(runtime_of(request), project)
    except Exception:  # noqa: BLE001 - a params file that does not fit: no stale stage to name
        return []
    return [
        s["id"] for s in states if s["id"] in READS and s["state"] in ("needs_update", "failed")
    ]


@routes.get("/api/share", action="share.read")
def builds(
    request: Request,
    ctx: ProjectDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    """The site builds in ``outputs/sites/`` (the newest first, the ``latest`` marked, each
    ``stale`` when what it was built from changed since), the exported files, and whether
    building works."""
    from cartolex.site.exports import list_exports

    builder = runtime_of(request).site_builder
    items = builder.builds(ctx.project)
    return {
        "available": builder.available,
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "limit": limit,
        "exports": list_exports(ctx.project),
        "empty": None
        if items
        else empty("empty_no_site" if builder.available else "empty_no_site_unavailable"),
    }


@routes.get("/api/share/plan", action="share.read")
def plan(
    request: Request,
    ctx: ProjectDep,
    names: Names | None = None,
    names_projected: Names = "pseudonyms",
    texts: Texts = "none",
    title: Annotated[str, Query(max_length=120)] = "",
    language: Language = "en",
) -> dict[str, Any]:
    """What a build with these options would carry and never carry (the privacy summary), and
    the checks before publishing (``blocker``, ``question``, ``warning``, ``info``)."""
    from cartolex.site.builder import SiteOptions
    from cartolex.site.checks import plan as site_plan

    options = SiteOptions.of(
        {
            "names": names,
            "names_projected": names_projected,
            "texts": texts,
            "title": title,
            "language": language,
        }
    )
    from .atlas import _bundle, _extras, lineage

    runtime = runtime_of(request)
    runs = lineage(ctx)
    bundle = extras = None
    if runs["map.layout"] is not None:  # the map's, as the atlas keeps them
        bundle = _bundle(runtime, ctx, runs)
        extras = _extras(runtime, ctx, runs, bundle)
    return site_plan(
        ctx.project,
        options,
        stale_stages=_stale_stages(request, ctx.project),
        bundle=bundle,
        extras=extras,
        cache=runtime.table_cache,
    )


@routes.post("/api/share/builds", action="share.build")
def start(request: Request, ctx: ProjectDep, body: SiteBody | None = None) -> JSONResponse:
    """Build the offline site (a job). A people atlas needs ``names`` (names or pseudonyms)."""
    runtime = runtime_of(request)
    builder = runtime.site_builder
    if not builder.available:
        raise ApiError.of("not_available")
    from cartolex.site.builder import SiteOptions
    from cartolex.site.checks import plan as site_plan

    body = body or SiteBody()
    raw = body.model_dump()
    codes = {c["code"] for c in site_plan(ctx.project, SiteOptions.of(raw))["checks"]}
    if "no_map" in codes:
        raise ApiError.of("no_map_to_share")
    if "names_unanswered" in codes:
        raise ApiError.of("names_question")
    project = ctx.project

    def work(control: JobControl) -> dict[str, Any]:
        return dict(builder.build(project, raw, control))

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="site",
            work=work,
            title="build the site",
            title_code="build_site",
        )
    except JobConflict as exc:
        raise busy_error(exc.running) from exc
    return JSONResponse({"job": info.as_dict()}, status_code=202)


def _folder(request: Request, ctx: Any, build_id: str) -> Any:
    folder = runtime_of(request).site_builder.folder(ctx.project, build_id)
    if folder is None:
        raise ApiError.of("site_not_found", build=build_id)
    return folder


@routes.get("/api/share/builds/{build_id}/site/{path:path}", action="share.read",
            include_in_schema=False)  # fmt: skip
def site_file(request: Request, ctx: ProjectDep, build_id: BuildId, path: str) -> Response:
    """A file of a build, to open the site in the browser (``…/site/index.html``)."""
    folder = _folder(request, ctx, build_id)
    found = safe_file(folder, path or "index.html")
    if found is None:
        raise ApiError.of("site_not_found", build=build_id)
    return FileResponse(found, media_type=MEDIA_TYPES[found.suffix.lower()],
                        headers={"Cache-Control": "no-cache"})  # fmt: skip


@routes.get("/api/share/builds/{build_id}/zip", action="share.read")
def site_zip(request: Request, ctx: ProjectDep, build_id: BuildId) -> Response:
    """A build as one zip, its README first (« unzip the whole folder first »), to send."""
    from cartolex.site.builder import site_zip as make_zip

    _folder(request, ctx, build_id)
    made = make_zip(ctx.project, build_id)
    if made is None:
        raise ApiError.of("site_not_found", build=build_id)
    path, name = made
    return FileResponse(path, media_type="application/zip", filename=name)


@routes.get("/api/share/figures/map", action="share.read")
def map_figure(
    ctx: ProjectDep,
    format: Literal["png", "svg"] = "png",
    width: Annotated[int, Query(ge=200, le=6000)] = 1600,
    height: Annotated[int, Query(ge=200, le=6000)] = 1200,
    theme: Literal["light", "dark"] = "light",
    language: Annotated[str, Query(max_length=8)] = "",
) -> Response:
    """The map as a PNG or SVG image of the size asked (in pixels), light or dark."""
    from cartolex.site.exports import map_figure as figure

    try:
        data = figure(ctx.project, fmt=format, width=width, height=height, theme=theme,
                      language=language)  # fmt: skip
    except LookupError as exc:
        raise ApiError.of("no_map_to_share") from exc
    media = "image/png" if format == "png" else "image/svg+xml"
    return Response(data, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="map-{width}x{height}.{format}"'})  # fmt: skip


@routes.get("/api/share/tables/themes.csv", action="share.read")
def themes_csv(ctx: ProjectDep) -> Response:
    """The theme tree as CSV: level, parent, names, keywords, weight, share, top keywords."""
    from cartolex.site.exports import theme_table

    try:
        text = theme_table(ctx.project)
    except LookupError as exc:
        raise ApiError.of("no_map_to_share") from exc
    return Response(text.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="themes.csv"'})  # fmt: skip


@routes.post("/api/share/exports", action="share.build")
def export(request: Request, ctx: ProjectDep, body: ExportBody) -> JSONResponse:
    """Write the map bundle or the project as one zip into ``outputs/exports/`` (a job)."""
    from cartolex.site.exports import write_map_bundle, write_project_zip

    from .atlas import lineage

    runtime = runtime_of(request)
    project = ctx.project
    if body.kind in DISTANCES:
        return _distances(request, ctx, body)
    if body.kind == "map_bundle" and lineage(ctx)["themes.apply"] is None:
        raise ApiError.of("no_map_to_share")

    def work(control: JobControl) -> dict[str, Any]:
        control.progress({"fraction": 0.1, "stage": "share.export", "message": "writing"})
        if body.kind == "map_bundle":
            path = write_map_bundle(project)
        else:
            path = write_project_zip(project, cancelled=lambda: control.cancelled)
        if path is None:
            return {"summary": "nothing changed", "summary_code": "export_cancelled"}
        control.progress({"fraction": 1.0, "stage": "share.export", "message": "done"})
        return {"name": path.name, "summary": f"{path.name} written",
                "summary_code": "export_written", "summary_params": {"name": path.name}}  # fmt: skip

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="export",
            work=work,
            title="write an export",
            title_code=f"export_{body.kind}",
        )
    except JobConflict as exc:
        raise busy_error(exc.running) from exc
    return JSONResponse({"job": info.as_dict()}, status_code=202)


def _distances(request: Request, ctx: Any, body: ExportBody) -> JSONResponse:
    """Plan or write an export of distances (a job)."""
    from ..distance_exports import human_size, plan_export, write_export
    from ..similarity import measure_of
    from .atlas import space_of

    runtime = runtime_of(request)
    project = ctx.project
    view = space_of(runtime, ctx)
    measure = measure_of(project)
    plan = plan_export(view, body.kind, body.of, ids=body.ids, level=body.level, k=body.k,
                       measure=measure)  # fmt: skip
    if body.plan:
        names = {lv.id: dict(lv.names) for lv in project.config.levels}
        levels = [
            {"id": lv, "names": names.get(lv, {}), "count": len(view.org_vectors(lv)[0])}
            for lv in view.levels
        ]
        return JSONResponse(
            {"plan": {**plan.as_dict(), "size": human_size(plan.bytes), "levels": levels}}
        )
    if body.of == "person" and body.names is None:
        raise ApiError.of("export_names_question")
    if plan.confirm and not body.confirm:
        raise ApiError.of("export_size_confirm", cells=plan.cells, size=human_size(plan.bytes))

    def work(control: JobControl) -> dict[str, Any]:
        def say(fraction: float) -> None:
            control.progress({"fraction": round(0.02 + 0.97 * fraction, 4),
                              "stage": "share.export", "message": "writing"})  # fmt: skip

        say(0.0)
        path = write_export(
            project, view, body.kind, body.of, names=body.names == "names", ids=body.ids,
            level=body.level, k=body.k, measure=measure, progress=say,
            cancelled=lambda: control.cancelled,
        )  # fmt: skip
        if path is None:
            return {"summary": "nothing changed", "summary_code": "export_cancelled"}
        control.progress({"fraction": 1.0, "stage": "share.export", "message": "done"})
        return {"name": path.name, "summary": f"{path.name} written",
                "summary_code": "export_written", "summary_params": {"name": path.name}}  # fmt: skip

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="export",
            work=work,
            title="write an export",
            title_code=f"export_{body.kind}",
        )
    except JobConflict as exc:
        raise busy_error(exc.running) from exc
    return JSONResponse({"job": info.as_dict(), "plan": plan.as_dict()}, status_code=202)


#: The media type of an exported file, by its extension.
EXPORT_TYPES = {
    ".zip": "application/zip",
    ".csv": "text/csv; charset=utf-8",
    ".parquet": "application/vnd.apache.parquet",
    ".npz": "application/octet-stream",
    ".json": "application/json",
}


@routes.get("/api/share/exports/{name}", action="share.read")
def export_file(ctx: ProjectDep, name: ExportName) -> Response:
    """An exported file, to download."""
    from cartolex.site.exports import exports_folder

    path = exports_folder(ctx.project) / name
    if not path.is_file():
        raise ApiError.of("export_not_found", name=name)
    media = EXPORT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=name)
