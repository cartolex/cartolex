# SPDX-License-Identifier: MIT
"""Snapshots: every earlier version of the decision files, the derived generations, restore."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Path as PathParam
from fastapi import Query, Request, Response
from pydantic import BaseModel

from ..deps import ProjectDep
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..routing import Routes

routes = Routes(tags=["snapshots"])

#: The files whose versions are kept: name → the layout's attribute.
FILES = {
    "project.json": "project_json",
    "people.csv": "people_csv",
    "organisations.csv": "organisations_csv",
    "affiliations.csv": "affiliations_csv",
    "params.json": "params_json",
    "keywords.csv": "keywords_csv",
    "themes.json": "themes_json",
    "maps.json": "maps_json",
    "snowball.csv": "snowball_csv",
    "stopwords.json": "stopwords_json",
}
_VERSION = re.compile(r"^(\d{8}T\d{6}Z)-([\w-]{1,80})$")
FileName = Annotated[str, PathParam(pattern=r"^[a-z]+\.(json|csv)$")]
VersionId = Annotated[str, PathParam(pattern=r"^[\w-]{1,96}$")]
#: The largest version a read returns whole (bigger ones: the first lines only).
MAX_CONTENT = 2 * 1024 * 1024


def _path(ctx: Any, name: str) -> Path:
    if name not in FILES:
        raise ApiError(
            404, "not_found", f"{name} has no versions; files with versions: {sorted(FILES)}"
        )
    return getattr(ctx.layout, FILES[name])


def _versions(ctx: Any, name: str) -> list[dict[str, Any]]:
    """The versions of a file, the current first."""
    from cartolex.project.files import fingerprint

    path = _path(ctx, name)
    folder = ctx.layout.history_of(path)
    entries = []
    if folder.is_dir():
        for f in folder.iterdir():
            m = _VERSION.match(f.stem) if f.is_file() and f.suffix == path.suffix else None
            if m:
                at = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                entries.append((at, f.stat().st_mtime_ns, f))
    entries.sort(key=lambda e: (e[0], e[1], e[2].name), reverse=True)
    out = []
    if path.exists():
        out.append({"id": "current", "file": name, "version": version_of(fingerprint(path))})
    for at, _, f in entries:
        m = _VERSION.match(f.stem)
        out.append(
            {
                "id": f.stem,
                "file": name,
                "replaced_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "replaced_by": m.group(2).replace("-", " ") if m else "",
                "bytes": f.stat().st_size,
            }
        )
    return out


def _generations(ctx: Any, registry: Any) -> list[dict[str, Any]]:
    from cartolex.build.records import read_record
    from cartolex.project.generations import generation_run_id

    out = []
    for stage in registry.ids:
        record = read_record(ctx.layout, stage)
        previous = ctx.layout.previous(stage)
        out.append(
            {
                "stage": stage,
                "current": record.run_id if record else None,
                "previous": generation_run_id(previous) if previous.is_dir() else None,
            }
        )
    return out


@routes.get("/api/snapshots", action="snapshots.read")
def list_snapshots(
    request: Request,
    ctx: ProjectDep,
    file: Annotated[str | None, Query(pattern=r"^[a-z]+\.(json|csv)$")] = None,
) -> dict[str, Any]:
    """The versions of one decision file (``file``), or a summary of every file and the
    derived generations (each stage's current run and the one it replaced)."""
    from ..routing import runtime_of

    if file is not None:
        items = _versions(ctx, file)
        return {
            "file": file,
            "items": items,
            "total": len(items),
            "empty": None
            if items
            else {
                "message": f"{file} was never written",
                "next": {"label": "Close", "action": "none"},
            },
        }
    files = []
    for name in FILES:
        versions = _versions(ctx, name)
        files.append(
            {
                "file": name,
                "exists": bool(versions and versions[0]["id"] == "current"),
                "versions": len(versions),
                "last_change": next(
                    (v["replaced_at"] for v in versions if "replaced_at" in v), None
                ),
            }
        )
    return {"files": files, "generations": _generations(ctx, runtime_of(request).registry)}


def _version_file(ctx: Any, name: str, version: str) -> Path:
    path = _path(ctx, name)
    if version == "current":
        if not path.exists():
            raise ApiError(404, "not_found", f"{name} does not exist yet")
        return path
    found = ctx.layout.history_of(path) / f"{version}{path.suffix}"
    if not _VERSION.match(version) or not found.is_file():
        raise ApiError(404, "not_found", f"no version {version} of {name}", next_action="reload")
    return found


@routes.get("/api/snapshots/{file}/{version}", action="snapshots.read")
def read_snapshot(file: FileName, version: VersionId, ctx: ProjectDep) -> dict[str, Any]:
    """One version's content: JSON as an object, CSV as its text."""
    path = _version_file(ctx, file, version)
    data = path.read_bytes()
    truncated = len(data) > MAX_CONTENT
    text = data[:MAX_CONTENT].decode("utf-8", errors="replace")
    content: Any = text
    if path.suffix == ".json" and not truncated:
        content = json.loads(text)
    return {"file": file, "id": version, "content": content, "truncated": truncated}


class RestoreBody(BaseModel):
    """``confirm_identity_change``: restoring ``project.json`` may change a frozen identity."""

    confirm_identity_change: bool = False


@routes.post("/api/snapshots/{file}/{version}/restore", action="snapshots.restore")
def restore_snapshot(
    request: Request,
    response: Response,
    file: FileName,
    version: VersionId,
    ctx: ProjectDep,
    body: RestoreBody | None = None,
) -> dict[str, Any]:
    """Make an earlier version current again, as a new version (so it can be undone too).

    Send ``If-Match`` with the current version of the file.
    """
    from cartolex.project.files import fingerprint, write_decision
    from cartolex.project.models import ProjectFile
    from cartolex.project.themes_versions import restore_version

    expected = expected_version(request)
    if version == "current":
        raise ApiError(409, "refused", "this version is already the current one")
    source = _version_file(ctx, file, version)
    project = ctx.project
    action = f"restore {version}"
    with ctx.handle.mutex:
        check_version(_path(ctx, file), expected)
        if file == "themes.json":
            restore_version(project, version, expected=expected)
        elif file == "project.json":
            config = ProjectFile.model_validate_json(source.read_bytes())
            project.save_config(
                config,
                action=action,
                identity_change=bool(body and body.confirm_identity_change),
            )
        else:
            write_decision(
                ctx.layout, _path(ctx, file), source.read_bytes(), expected=expected, action=action
            )
        if file in ("keywords.csv", "themes.json") and project.has_curation():
            project.freeze_identity("curation decisions restored")
    current = version_of(fingerprint(_path(ctx, file)))
    response.headers["ETag"] = etag_of(current)
    return {"file": file, "restored": version, "version": current}
