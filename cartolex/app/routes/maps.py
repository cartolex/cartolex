# SPDX-License-Identifier: MIT
"""Map versions: list, pin, try another layout, discard."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Request, Response
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty
from ..routing import Routes, runtime_of

routes = Routes(tags=["map"])


class VersionAction(BaseModel):
    """``pin`` a version, ``try`` another layout (a new version beside the pinned one, another
    seed; ``dimensions`` 3 for a map in space, umap only; ``built`` to build it too), ``build``
    a version with the pinned one or no longer (``built``), or ``discard`` a version nobody
    pinned. The field ``build`` also starts a build of the map."""

    action: Literal["pin", "try", "discard", "build"]
    version: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")] = None
    seed: Annotated[int | None, Field(ge=0, lt=2**32)] = None
    method: Literal["umap", "tsne", "tree"] | None = None
    #: ``try``: layout parameters of the method (``n_neighbors``, ``min_dist``, ``perplexity``…),
    #: set over the pinned version's (same method) or the method's defaults; ``None`` removes one.
    params: Annotated[dict[str, float | int | str | None], Field(max_length=16)] = {}
    note: Annotated[str, Field(max_length=500)] = ""
    #: ``try``: a flat map (2) or a map in space (3); the pinned version's by default.
    dimensions: Literal[2, 3] | None = None
    #: ``build``: build the version with the pinned one (true) or no longer (false);
    #: ``try``: build the new version too.
    built: bool | None = None
    build: bool = False


def _view(ctx: Any) -> dict[str, Any]:
    from cartolex.project.maps import read_maps

    maps, fp = read_maps(ctx.layout)
    versions = [{**v.model_dump(mode="json"), "pinned": v.id == maps.pinned} for v in maps.versions]
    from cartolex.build.engine import TSNE_FROM_PEOPLE
    from cartolex.project.models import SPACE_METHODS

    from ..method import LAYOUT_METHODS, unavailable_methods

    missing = unavailable_methods()
    return {
        "methods": list(LAYOUT_METHODS),
        #: The dimensions each method draws (3: a map in space).
        "dimensions": {m: [2, 3] if m in SPACE_METHODS else [2] for m in LAYOUT_METHODS},
        "unavailable": missing,
        "default_method": {
            "tsne_from_people": TSNE_FROM_PEOPLE,
            "tsne_available": "tsne" not in missing,
        },
        "pinned": maps.pinned,
        "versions": versions[::-1],
        "version": version_of(fp),
        "empty": None if versions else empty("empty_no_map_versions"),
    }


def _check_layout_params(maps: Any, body: VersionAction) -> None:
    """Refuse a method this installation cannot draw (422 ``layout_method_unavailable``) and a
    layout parameter the method does not take (422 ``layout_param_unknown``)."""
    from cartolex.build.engine import LAYOUT_METHODS
    from cartolex.project.maps import pinned

    from ..method import unavailable_methods

    method = body.method or pinned(maps).layout.method
    missing = unavailable_methods().get(method) if body.method else None
    if missing is not None:
        raise ApiError.of("layout_method_unavailable", **missing["params"])
    known = sorted(LAYOUT_METHODS.get(method, ({}, None))[0])
    for key in body.params:
        if key not in known:
            raise ApiError.of(
                "layout_param_unknown", method=method, param=key, known=", ".join(known) or "—"
            )
    if body.dimensions == 3:
        from cartolex.project.models import FLAT_RECIPES, SPACE_METHODS

        recipe = body.params.get("layout")
        if recipe is None and method == pinned(maps).layout.method:
            recipe = pinned(maps).layout.params.get("layout")
        if method not in SPACE_METHODS or recipe in FLAT_RECIPES:
            raise ApiError.of("layout_dimensions_unsupported", method=recipe or method)


@routes.get("/api/map/versions", action="map.read")
def list_versions(response: Response, ctx: ProjectDep) -> dict[str, Any]:
    """The map versions, the newest first, and the pinned one."""
    view = _view(ctx)
    response.headers["ETag"] = etag_of(view["version"])
    return view


@routes.post("/api/map/versions", action="map.write")
def change_versions(
    request: Request, response: Response, body: VersionAction, ctx: ProjectDep
) -> dict[str, Any]:
    """Pin, try, build or discard (send ``If-Match`` with the version of ``maps.json`` you
    read)."""
    from cartolex.project.maps import discard, pin, read_maps, save_maps, set_built, try_another

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.maps_json, expected)
        maps, _ = read_maps(ctx.layout)
        try:
            if body.action in ("pin", "discard", "build") and not body.version:
                raise ApiError.of("map_version_missing", action=body.action)
            if body.action == "pin":
                maps, action = pin(maps, body.version), f"pin {body.version}"
            elif body.action == "build":
                built = True if body.built is None else body.built
                maps = set_built(maps, body.version, built)
                action = f"{'build' if built else 'unbuild'} {body.version}"
            elif body.action == "discard":
                if body.version == maps.pinned:
                    raise ApiError.of("map_version_pinned", version=body.version)
                maps, action = discard(maps, body.version), f"discard {body.version}"
            else:
                seed = body.seed
                if seed is None:
                    seed = max((v.layout.seed for v in maps.versions), default=0) + 1
                if maps.pinned is None:
                    raise ApiError.of("no_pinned_version")
                _check_layout_params(maps, body)
                maps, added = try_another(
                    maps,
                    seed=seed,
                    method=body.method,
                    note=body.note,
                    params=body.params,
                    dimensions=body.dimensions,
                    built=bool(body.built),
                )
                action = f"try {added}"
        except KeyError as exc:
            raise ApiError.of("map_version_not_found", version=body.version) from exc
        save_maps(ctx.layout, maps, expected=expected, action=action)
    view = _view(ctx)
    view["done"] = action
    if body.build:
        from .build import start_build_job

        view["job"] = start_build_job(
            runtime_of(request), ctx, ["map.layout"], title="draw the map", title_code="draw_map"
        )["job"]
    response.headers["ETag"] = etag_of(view["version"])
    return view


# ── bases: another project's map ─────────────────────────────────────────────


class BaseAdd(BaseModel):
    """Another project's folder (locally): its map is copied into this project."""

    folder: Annotated[str, Field(min_length=1, max_length=4096)]
    id: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")] = None


def _bases(ctx: Any) -> dict[str, Any]:
    from cartolex.app.atlas_layers import read_base
    from cartolex.project.files import fingerprint

    items = []
    for b in ctx.project.config.bases:
        doc = read_base(ctx, b.id)
        items.append(
            {
                "id": b.id,
                "map_version": b.map_version,
                "name": doc["name"] if doc else None,
                "keywords": len(doc["terms"]) if doc else 0,
                "people": len(doc["people"]) if doc else 0,
                "missing": doc is None,
            }
        )
    return {"bases": items, "version": version_of(fingerprint(ctx.layout.project_json))}


@routes.get("/api/map/bases", action="map.read")
def list_bases(response: Response, ctx: ProjectDep) -> dict[str, Any]:
    """The other projects' maps this project can be placed on."""
    view = _bases(ctx)
    response.headers["ETag"] = etag_of(view["version"])
    return view


@routes.post("/api/map/bases", action="map.write")
def add_base(
    request: Request, response: Response, body: BaseAdd, ctx: ProjectDep
) -> dict[str, Any]:
    """Copy another project's map (its keywords' places, its people's places without names, its
    top-level themes) and add it to ``project.json``'s bases (send ``If-Match``)."""
    from pathlib import Path

    from cartolex.app.atlas_layers import add_base as copy_base
    from cartolex.project import StaleWrite
    from cartolex.project.files import fingerprint
    from cartolex.project.models import Base

    if runtime_of(request).settings.hosted:
        raise ApiError.of("bases_hosted")
    folder = Path(body.folder).expanduser()
    if not folder.is_absolute():
        raise ApiError.of("project_folder_relative", path=str(folder))
    if folder.resolve() == ctx.layout.root.resolve():
        raise ApiError.of("base_same_project")
    expected = expected_version(request)
    with ctx.handle.mutex:
        found = fingerprint(ctx.layout.project_json)
        if found != expected:
            raise StaleWrite(ctx.layout.project_json, expected, found)
        config = ctx.project.config
        taken = {b.id for b in config.bases}
        base_id = body.id or _slug(folder.name)
        n = 2
        while base_id in taken:
            base_id = f"{(body.id or _slug(folder.name))[:28]}-{n}"
            n += 1
        try:
            entry = copy_base(ctx, folder, base_id)
        except (FileNotFoundError, ValueError, KeyError) as exc:
            raise ApiError.of("base_no_map", path=str(folder)) from exc
        new = config.model_copy(update={"bases": [*config.bases, Base(**entry)]})
        ctx.project.save_config(new, action=f"add base map {base_id}")
    view = _bases(ctx)
    view["added"] = base_id
    response.headers["ETag"] = etag_of(view["version"])
    return view


@routes.delete("/api/map/bases/{base_id}", action="map.write")
def remove_base(
    request: Request, response: Response, base_id: str, ctx: ProjectDep
) -> dict[str, Any]:
    """Remove a base and its copy (send ``If-Match``); map versions that name it keep the name."""
    from cartolex.app.atlas_layers import remove_base as drop_copy
    from cartolex.project import StaleWrite
    from cartolex.project.files import fingerprint

    expected = expected_version(request)
    with ctx.handle.mutex:
        found = fingerprint(ctx.layout.project_json)
        if found != expected:
            raise StaleWrite(ctx.layout.project_json, expected, found)
        config = ctx.project.config
        if base_id not in {b.id for b in config.bases}:
            raise ApiError.of("base_not_found", base=base_id)
        in_use = any(v.base == base_id for v in read_maps_versions(ctx))
        if in_use:
            raise ApiError.of("base_in_use", base=base_id)
        new = config.model_copy(update={"bases": [b for b in config.bases if b.id != base_id]})
        ctx.project.save_config(new, action=f"remove base map {base_id}")
        drop_copy(ctx, base_id)
    view = _bases(ctx)
    response.headers["ETag"] = etag_of(view["version"])
    return view


def read_maps_versions(ctx: Any) -> list[Any]:
    from cartolex.project.maps import read_maps

    return list(read_maps(ctx.layout)[0].versions)


def _slug(name: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")[:32]
    return slug or "base"
