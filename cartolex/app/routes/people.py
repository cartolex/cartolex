# SPDX-License-Identifier: MIT
"""People: the list (roles, identity, coverage), edits, merges, and importing a list."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from pydantic import BaseModel, Field

from ..collection import new_import_id
from ..deps import ListDep, ProjectDep, page
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty
from ..people_io import read_people, write_people_csv
from ..routing import Routes, runtime_of

routes = Routes(tags=["people"])

Role = Literal["mapped", "context", "projected", "excluded", "undecided"]
PersonId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
#: The most people one change names.
MAX_BATCH = 20_000


def _name(p: dict[str, Any]) -> str:
    return f"{p['last_name']} {p['first_name']}".casefold()


@routes.get("/api/people", action="people.read")
def list_people(
    request: Request,
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    role: Annotated[Role | None, Query()] = None,
    identity: Annotated[Literal["confirmed", "auto", "none", "pending"] | None, Query()] = None,
    set: Annotated[str | None, Query(max_length=64)] = None,  # noqa: A002 - the column's name
    coverage: Annotated[Literal["good", "thin", "none"] | None, Query()] = None,
) -> dict[str, Any]:
    """People with their role, identity state and coverage; paged, sorted and filtered here."""
    people, fp = read_people(ctx.project, runtime_of(request).table_cache)
    counts: dict[str, dict[str, int]] = {"role": {}, "identity": {}, "coverage": {}}
    for p in people:
        for key, value in (
            ("role", p["role"]),
            ("identity", p["identity"]),
            ("coverage", p["coverage"]["class"]),
        ):
            counts[key][value] = counts[key].get(value, 0) + 1
    rows = [
        p
        for p in people
        if (role is None or p["role"] == role)
        and (identity is None or p["identity"] == identity)
        and (set is None or p["set"] == set)
        and (coverage is None or p["coverage"]["class"] == coverage)
        and (not params.q or params.q in _name(p) or params.q in p["unit"].casefold())
    ]
    response.headers["ETag"] = etag_of(fp)
    nothing = empty("empty_no_people") if not people else empty("empty_no_match")
    return page(
        rows,
        params,
        sorts={
            "name": _name,
            "role": lambda p: p["role"],
            "identity": lambda p: p["identity"],
            "texts": lambda p: p["coverage"].get("texts", 0),
            "unit": lambda p: p["unit"].casefold(),
        },
        default_sort="name",
        filters={
            "role": role,
            "identity": identity,
            "set": set,
            "coverage": coverage,
            "q": params.q,
        },
        empty=nothing,
        extra={"counts": counts, "version": version_of(fp)},
    )


class PeopleEdit(BaseModel):
    """A role, a set (for ``projected``) or a note for some people."""

    person_ids: Annotated[list[PersonId], Field(min_length=1, max_length=MAX_BATCH)]
    role: Role | None = None
    set: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$|^$")] = None
    note: Annotated[str | None, Field(max_length=2000)] = None


def _known(request: Request, ctx: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    people, _ = read_people(ctx.project, runtime_of(request).table_cache)
    by_id = {p["person_id"]: p for p in people}
    unknown = sorted(set(ids) - set(by_id))
    if unknown:
        raise ApiError.of("unknown_people", ids=unknown[:5])
    return by_id


@routes.patch("/api/people", action="people.write")
def edit_people(
    request: Request, response: Response, body: PeopleEdit, ctx: ProjectDep
) -> dict[str, Any]:
    """Set the role, the set or the note of some people (send ``If-Match``)."""
    expected = expected_version(request)
    sets = {o.id for o in ctx.project.config.overlays}
    if body.set and body.set not in sets:
        raise ApiError.of("unknown_set", set=body.set, sets=sorted(sets))
    if body.role == "projected" and not (body.set or sets):
        raise ApiError.of("set_needed")
    changes: dict[str, str] = {}
    if body.role is not None:
        changes["role"] = body.role
        if body.role != "projected":
            changes["set"] = ""
    if body.set is not None:
        changes["set"] = body.set
    if body.note is not None:
        changes["note"] = body.note
    if not changes:
        raise ApiError.of("nothing_to_change")
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        _known(request, ctx, body.person_ids)
        what = ", ".join(f"{k} {v}" for k, v in changes.items() if k != "note") or "note"
        fp = write_people_csv(
            ctx.project,
            {pid: changes for pid in body.person_ids},
            expected=expected,
            action=f"{what} for {len(body.person_ids)} people",
        )
    response.headers["ETag"] = etag_of(fp)
    return {"changed": len(body.person_ids), "version": version_of(fp)}


class MergeBody(BaseModel):
    """Rows that are one person: *sources* are merged into *target*."""

    target: PersonId
    sources: Annotated[list[PersonId], Field(min_length=1, max_length=100)]


@routes.post("/api/people/merge", action="people.write")
def merge_people(
    request: Request, response: Response, body: MergeBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Merge rows that are one person (``merged_into``); send ``If-Match``."""
    expected = expected_version(request)
    if body.target in body.sources:
        raise ApiError.of("self_merge")
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        by_id = _known(request, ctx, [body.target, *body.sources])
        if by_id[body.target]["merged_into"]:
            raise ApiError.of(
                "merged_target", target=body.target, into=by_id[body.target]["merged_into"]
            )
        changes = {pid: {"merged_into": body.target} for pid in body.sources}
        for pid, p in by_id.items():  # rows merged into a source follow it to the target
            if p["merged_into"] in body.sources:
                changes[pid] = {"merged_into": body.target}
        fp = write_people_csv(
            ctx.project,
            changes,
            expected=expected,
            action=f"merge {len(body.sources)} into {body.target}",
        )
    response.headers["ETag"] = etag_of(fp)
    return {"merged": sorted(changes), "into": body.target, "version": version_of(fp)}


class PastedList(BaseModel):
    """A pasted list: one person per line (``Last, First`` or ``First Last``), or a CSV."""

    text: Annotated[str, Field(min_length=1, max_length=5_000_000)]
    name: Annotated[str, Field(max_length=200)] = "pasted.txt"


@routes.post("/api/people/import", action="people.import")
async def import_list(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """Receive a list (a CSV file in a form, or ``{"text": …}``) and propose a column mapping.

    Nothing changes in the project until the proposal is confirmed.
    """
    runtime = runtime_of(request)
    limit = int(runtime.settings.max_upload_mb * 1024 * 1024)
    kind = request.headers.get("content-type", "")
    if kind.startswith("multipart/form-data"):
        form = await request.form(max_files=1, max_fields=4)
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise ApiError.of("file_missing")
        data = await upload.read(limit + 1)
        name = upload.filename or "list.csv"
        await form.close()
    else:
        try:
            body = PastedList.model_validate_json(await request.body())
        except ValueError as exc:
            raise ApiError.of("list_body") from exc
        data, name = body.text.encode("utf-8"), body.name
    if len(data) > limit:
        raise ApiError.of("file_too_large", limit_mb=runtime.settings.max_upload_mb)
    import_id = new_import_id()
    folder = runtime.uploads_of(ctx.id) / import_id
    return runtime.collection.propose_import(ctx.project, folder, name, data)


class ConfirmImport(BaseModel):
    """The confirmed mapping (column → field), and the role of the people added."""

    mapping: Annotated[dict[str, str], Field(max_length=200)]
    role: Role = "mapped"
    set: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$|^$")] = ""


def _import_folder(request: Request, ctx: Any, import_id: str) -> Any:
    from pathlib import PurePosixPath

    if not re.match(r"^imp-[0-9a-f]{12}$", import_id) or PurePosixPath(import_id).name != import_id:
        raise ApiError.of("import_not_found")
    folder = runtime_of(request).uploads_of(ctx.id) / import_id
    if not folder.is_dir():
        raise ApiError.of("import_not_found")
    return folder


@routes.post("/api/people/import/{import_id}/confirm", action="people.import")
def confirm_import(
    request: Request, response: Response, import_id: str, body: ConfirmImport, ctx: ProjectDep
) -> dict[str, Any]:
    """Add the people of an import with the confirmed mapping (send ``If-Match`` of people)."""
    runtime = runtime_of(request)
    expected = expected_version(request)
    folder = _import_folder(request, ctx, import_id)
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        result = runtime.collection.confirm_import(
            ctx.project,
            folder,
            body.mapping,
            role=body.role,
            set_id=body.set,
            expected_people=expected,
        )
    runtime.drop_uploads(ctx.id, import_id)
    response.headers["ETag"] = etag_of(result.get("people_version"))
    return {
        "added": len(result["added"]),
        "already_known": len(result["already_known"]),
        "skipped": result["skipped"],
        "person_ids": result["added"],
        "version": version_of(result.get("people_version")),
    }


@routes.delete("/api/people/import/{import_id}", action="people.import")
def drop_import(request: Request, import_id: str, ctx: ProjectDep) -> dict[str, Any]:
    """Forget an import that was not confirmed."""
    _import_folder(request, ctx, import_id)
    runtime_of(request).drop_uploads(ctx.id, import_id)
    return {"dropped": import_id}
