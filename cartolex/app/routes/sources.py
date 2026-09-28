# SPDX-License-Identifier: MIT
"""A slot's raw material: files and archives uploaded into ``sources/<slot>/``."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Path as PathParam
from fastapi import Request

from ..deps import ProjectDep
from ..errors import ApiError
from ..routing import Routes, runtime_of
from ..uploads import clean_name, extract_archive, save_upload

routes = Routes(tags=["sources"])

SlotId = Annotated[str, PathParam(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
#: How many files a slot listing shows.
MAX_LISTED = 1000


def _slot(ctx: Any, slot_id: str) -> Any:
    slot = next((s for s in ctx.project.config.slots if s.id == slot_id), None)
    if slot is None:
        raise ApiError.of("slot_not_found", slot=slot_id)
    return slot


@routes.get("/api/sources", action="sources.read")
def list_slots(ctx: ProjectDep) -> dict[str, Any]:
    """The project's slots, in order, with the number of files each holds."""
    out = []
    for slot in ctx.project.config.slots:
        folder = ctx.layout.slot(slot.id)
        files = [p for p in folder.rglob("*") if p.is_file()] if folder.is_dir() else []
        out.append({**slot.model_dump(mode="json"), "files": len(files)})
    return {"slots": out}


@routes.get("/api/sources/{slot_id}/files", action="sources.read")
def list_files(slot_id: SlotId, ctx: ProjectDep) -> dict[str, Any]:
    """The files of a slot's folder (paths relative to it)."""
    _slot(ctx, slot_id)
    folder = ctx.layout.slot(slot_id)
    files = sorted(p for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []
    return {
        "slot": slot_id,
        "items": [
            {"path": p.relative_to(folder).as_posix(), "bytes": p.stat().st_size}
            for p in files[:MAX_LISTED]
        ],
        "total": len(files),
    }


@routes.post("/api/sources/{slot_id}/files", action="sources.write", status_code=201)
async def upload(request: Request, slot_id: SlotId, ctx: ProjectDep) -> dict[str, Any]:
    """Upload a document, or a zip archive of documents, into a folder or corpus slot.

    Files are written only inside ``sources/<slot>/``; archive members are
    checked (no absolute path, no ``..``, no link) and sizes capped.
    """
    settings = runtime_of(request).settings
    slot = _slot(ctx, slot_id)
    if slot.kind not in ("folder", "corpus"):
        raise ApiError.of("slot_collected", slot=slot_id)
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise ApiError.of("file_missing")
    target = ctx.layout.slot(slot_id)
    limit = int(settings.max_upload_mb * 1024 * 1024)
    form = await request.form(max_files=1, max_fields=4)
    try:
        item = form.get("file")
        if item is None or isinstance(item, str):
            raise ApiError.of("file_missing")
        name = clean_name(item.filename or "upload")

        async def chunks() -> Any:
            while block := await item.read(1 << 20):
                yield block

        if name.lower().endswith(".zip"):
            staging = runtime_of(request).uploads_of(ctx.id) / f"zip-{slot_id}"
            archive = await save_upload(chunks(), staging, name, max_bytes=limit)
            try:
                written = extract_archive(
                    archive,
                    target,
                    max_members=settings.max_archive_members,
                    max_bytes=int(settings.max_archive_mb * 1024 * 1024),
                )
            finally:
                archive.unlink(missing_ok=True)
            files = [p.relative_to(target).as_posix() for p in written]
        else:
            path = await save_upload(chunks(), target, name, max_bytes=limit)
            files = [path.relative_to(target).as_posix()]
    finally:
        await form.close()
    return {"slot": slot_id, "files": files}
