# SPDX-License-Identifier: MIT
"""People: the list (roles, identity, coverage), edits, merges, and importing a list."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from pydantic import BaseModel, Field

from ..collection import new_import_id
from ..deps import ListDep, ProjectDep
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


#: The people list's sorts.
SORTS = {
    "name": _name,
    "role": lambda p: p["role"],
    "identity": lambda p: p["identity"],
    "texts": lambda p: p["coverage"].get("texts", 0),
    "unit": lambda p: p["unit"].casefold(),
    "state": lambda p: ("failed", "no_data", "thin", "good", "").index(p["state"]),
}


CoverageFilter = Literal["good", "thin", "none", "failed", "no_data"]


class PeopleFilter(BaseModel):
    """The people a list shows, or a bulk change names: each filter narrows the list."""

    role: Role | None = None
    identity: Literal["confirmed", "auto", "none", "pending"] | None = None
    set: Annotated[str | None, Field(max_length=64)] = None
    coverage: CoverageFilter | None = None
    source: Annotated[str | None, Field(max_length=64)] = None
    q: Annotated[str | None, Field(max_length=200)] = None
    columns: Annotated[dict[str, str], Field(max_length=20)] = {}


def _filtered(people: list[dict[str, Any]], f: PeopleFilter) -> list[dict[str, Any]]:
    q = (f.q or "").strip().casefold()
    return [
        p
        for p in people
        if (f.role is None or p["role"] == f.role)
        and (f.identity is None or p["identity"] == f.identity)
        and (f.set is None or p["set"] == f.set)
        and (f.coverage is None or _coverage_is(p, p["state"], f.coverage))
        and (f.source is None or p["source"] == f.source)
        and all(p["columns"].get(k, "") == v for k, v in f.columns.items())
        and (not q or q in _name(p) or q in p["unit"].casefold())
    ]


def _coverage_is(p: dict[str, Any], state: str, wanted: str) -> bool:
    """A class (good, thin, none) or, once the coverage report knows the person, its state."""
    if wanted in ("failed", "no_data"):
        return state == wanted
    if wanted == "none":
        return p["coverage"]["class"] == "none"
    return state == wanted if state else p["coverage"]["class"] == wanted


@routes.get("/api/people", action="people.read")
def list_people(
    request: Request,
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    role: Annotated[Role | None, Query()] = None,
    identity: Annotated[Literal["confirmed", "auto", "none", "pending"] | None, Query()] = None,
    set: Annotated[str | None, Query(max_length=64)] = None,  # noqa: A002 - the column's name
    coverage: Annotated[CoverageFilter | None, Query()] = None,
    source: Annotated[str | None, Query(max_length=64)] = None,
    col: Annotated[list[str] | None, Query(max_length=20)] = None,
) -> dict[str, Any]:
    """People with their role, identity state and coverage; paged, sorted and filtered here.

    ``coverage`` is a class (good, thin, none) or a state of the coverage report
    (failed, no_data); ``col=<column>:<value>`` keeps the people whose extra column has
    that value (repeat it for several). The answer's ``facets`` list each extra column's
    values, for filters built from the people's own columns.
    """
    from ..corpus_view import ordered, people_view

    view = people_view(ctx.project, runtime_of(request).table_cache)
    people, fp = view["people"], view["fp"]
    columns: dict[str, str] = {}
    for item in col or []:
        key, sep, value = item.partition(":")
        if not sep:
            raise ApiError.of("invalid", problems=[f"col={item}: write col=<column>:<value>"])
        columns[key] = value
    wanted = PeopleFilter(
        role=role,
        identity=identity,
        set=set,
        coverage=coverage,
        source=source,
        q=params.q,
        columns=columns,
    )
    sort = params.sort or "name"
    name = sort.lstrip("-")
    if name not in SORTS:
        raise ApiError.of("invalid_sort", sort=name, sorts=sorted(SORTS))
    rows = _filtered(ordered(view, name, SORTS[name], sort.startswith("-")), wanted)
    response.headers["ETag"] = etag_of(fp)
    return {
        "items": rows[params.offset : params.offset + params.limit],
        "total": len(rows),
        "offset": params.offset,
        "limit": params.limit,
        "sort": sort,
        "sorts": sorted(SORTS),
        "filters": {
            k: v
            for k, v in {
                "role": role,
                "identity": identity,
                "set": set,
                "coverage": coverage,
                "source": source,
                "col": col,
                "q": params.q,
            }.items()
            if v not in (None, "", [])
        },
        "empty": None
        if rows
        else (empty("empty_no_people") if not people else empty("empty_no_match")),
        "counts": view["counts"],
        "facets": view["facets"],
        "sets": [o.id for o in ctx.project.config.overlays],
        "version": version_of(fp),
    }


class PeopleEdit(BaseModel):
    """A role, a set (for ``projected``) or a note for some people."""

    person_ids: Annotated[list[PersonId], Field(max_length=MAX_BATCH)] = []
    #: Instead of ids: every person the filter keeps (a list's « all N matching »).
    where: PeopleFilter | None = None
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
        ids = list(body.person_ids)
        if body.where is not None:
            from ..corpus_view import people_view

            view = people_view(ctx.project, runtime_of(request).table_cache)
            ids = [p["person_id"] for p in _filtered(view["people"], body.where)]
        if not ids:
            raise ApiError.of("nothing_to_change")
        _known(request, ctx, ids)
        what = ", ".join(f"{k} {v}" for k, v in changes.items() if k != "note") or "note"
        fp = write_people_csv(
            ctx.project,
            {pid: changes for pid in ids},
            expected=expected,
            action=f"{what} for {len(ids)} people",
        )
    response.headers["ETag"] = etag_of(fp)
    return {"changed": len(ids), "version": version_of(fp)}


class MergeBody(BaseModel):
    """Rows that are one person: *sources* are merged into *target*.

    Two rows with different ORCIDs are refused (``merge_orcid_conflict``) unless
    *override*: two different iDs are two people unless someone who knows says otherwise.
    """

    target: PersonId
    sources: Annotated[list[PersonId], Field(min_length=1, max_length=100)]
    override: bool = False
    note: Annotated[str, Field(max_length=500)] = ""


def _merge_refused(exc: Any) -> ApiError:
    """The API's error for a merge refused (:class:`cartolex.project.identity.MergeRefused`)."""
    p = exc.params
    if exc.code == "self_merge":
        return ApiError.of("self_merge")
    if exc.code == "merged_target":
        return ApiError.of("merged_target", target=p["target"], into=p["into"])
    return ApiError.of(
        "merge_orcid_conflict",
        target=p["target"],
        source=p["source"],
        orcids=", ".join(p["orcids"]),
    )


@routes.post("/api/people/merge", action="people.write")
def merge_people(
    request: Request, response: Response, body: MergeBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Merge rows that are one person (``merged_into``); send ``If-Match``. A merged row
    keeps its records, identity and role: ``POST /api/people/unmerge`` gives it back."""
    from cartolex.collect.decisions import read_people as read_rows
    from cartolex.project.identity import MergeRefused, merge_changes

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        by_id = _known(request, ctx, [body.target, *body.sources])
        rows = read_rows(ctx.layout)
        for pid in by_id:  # a person without a row yet: as the list shows them
            rows.setdefault(pid, {"person_id": pid, "merged_into": "", "records": ""})
        try:
            changes = merge_changes(
                rows,
                body.target,
                body.sources,
                {pid: p["orcid"] for pid, p in by_id.items()},
                override=body.override,
                note=body.note,
            )
        except MergeRefused as exc:
            raise _merge_refused(exc) from exc
        fp = write_people_csv(
            ctx.project,
            changes,
            expected=expected,
            action=f"merge {len(body.sources)} into {body.target}",
        )
    response.headers["ETag"] = etag_of(fp)
    return {"merged": sorted(changes), "into": body.target, "version": version_of(fp)}


class UnmergeBody(BaseModel):
    """Rows merged into another person that stand on their own again (a person others are
    merged into: every one of them). *remember* records each pair undone in
    ``people_pairs.csv`` (``distinct``: never proposed again; ``later``: kept out of the
    automatic merge)."""

    person_ids: Annotated[list[PersonId], Field(min_length=1, max_length=MAX_BATCH)]
    remember: Literal["distinct", "later"] | None = None


@routes.post("/api/people/unmerge", action="people.write")
def unmerge_people(
    request: Request, response: Response, body: UnmergeBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Undo merges (send ``If-Match`` of the people): each row named, or merged into a
    person named, gets back what it had before the merge."""
    from cartolex.collect.decisions import read_people as read_rows
    from cartolex.project.identity import unmerge_changes

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        rows = read_rows(ctx.layout)
        unknown = sorted(set(body.person_ids) - set(rows))
        if unknown:
            raise ApiError.of("unknown_people", ids=unknown[:5])
        before = {pid: rows[pid]["merged_into"] for pid in rows if rows[pid]["merged_into"]}
        changes = unmerge_changes(rows, body.person_ids)
        if not changes:
            raise ApiError.of("nothing_to_change")
        fp = write_people_csv(
            ctx.project, changes, expected=expected, action=f"unmerge {len(changes)} people"
        )
        if body.remember:
            from cartolex.project.pairs import remember_pairs

            remember_pairs(
                ctx.layout,
                [(pid, before[pid]) for pid in sorted(changes)],
                body.remember,
                note="merge undone",
            )
    response.headers["ETag"] = etag_of(fp)
    return {
        "unmerged": sorted(changes),
        "from": {pid: before[pid] for pid in sorted(changes)},
        "version": version_of(fp),
    }


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
        "already_known": result["already_known"],
        "skipped": result["skipped"],
        "notes": result.get("notes", []),
        "duplicates": result.get("duplicates", []),
        "person_ids": result["added"],
        "version": version_of(result.get("people_version")),
    }


@routes.delete("/api/people/import/{import_id}", action="people.import")
def drop_import(request: Request, import_id: str, ctx: ProjectDep) -> dict[str, Any]:
    """Forget an import that was not confirmed."""
    _import_folder(request, ctx, import_id)
    runtime_of(request).drop_uploads(ctx.id, import_id)
    return {"dropped": import_id}
