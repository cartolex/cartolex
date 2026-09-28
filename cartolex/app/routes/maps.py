# SPDX-License-Identifier: MIT
"""Map versions: list, pin, try another layout, discard."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Request, Response
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..routing import Routes, runtime_of

routes = Routes(tags=["map"])


class VersionAction(BaseModel):
    """``pin`` a version, ``try`` another layout (a new version beside the pinned one, another
    seed), or ``discard`` a version nobody pinned. ``build`` also starts a build of the map."""

    action: Literal["pin", "try", "discard"]
    version: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")] = None
    seed: Annotated[int | None, Field(ge=0, lt=2**32)] = None
    method: Literal["umap"] | None = None
    note: Annotated[str, Field(max_length=500)] = ""
    build: bool = False


def _view(ctx: Any) -> dict[str, Any]:
    from cartolex.project.maps import read_maps

    maps, fp = read_maps(ctx.layout)
    versions = [{**v.model_dump(mode="json"), "pinned": v.id == maps.pinned} for v in maps.versions]
    return {
        "pinned": maps.pinned,
        "versions": versions[::-1],
        "version": version_of(fp),
        "empty": None
        if versions
        else {
            "message": "no map yet: the first build draws one and pins it",
            "next": {"label": "Build the map", "action": "build"},
        },
    }


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
    """Pin, try or discard (send ``If-Match`` with the version of ``maps.json`` you read)."""
    from cartolex.project.maps import discard, pin, read_maps, save_maps, try_another

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.maps_json, expected)
        maps, _ = read_maps(ctx.layout)
        try:
            if body.action == "pin":
                if not body.version:
                    raise ValueError("name the version to pin")
                maps, action = pin(maps, body.version), f"pin {body.version}"
            elif body.action == "discard":
                if not body.version:
                    raise ValueError("name the version to discard")
                maps, action = discard(maps, body.version), f"discard {body.version}"
            else:
                seed = body.seed
                if seed is None:
                    seed = max((v.layout.seed for v in maps.versions), default=0) + 1
                maps, added = try_another(maps, seed=seed, method=body.method, note=body.note)
                action = f"try {added}"
        except KeyError as exc:
            raise ApiError(404, "not_found", str(exc).strip("'\""), next_action="reload") from exc
        except ValueError as exc:
            raise ApiError(409, "refused", str(exc), next_action="fix-input") from exc
        save_maps(ctx.layout, maps, expected=expected, action=action)
    view = _view(ctx)
    view["done"] = action
    if body.build:
        from .build import start_build_job

        view["job"] = start_build_job(
            runtime_of(request), ctx, ["map.layout"], title="draw the map"
        )["job"]
    response.headers["ETag"] = etag_of(view["version"])
    return view
