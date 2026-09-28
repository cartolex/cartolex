# SPDX-License-Identifier: MIT
"""Collection: the plan (what leaves the computer), the job, the identity queue, coverage."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import ListDep, ProjectDep, empty_hint, page
from ..errors import ApiError
from ..etags import etag_of, expected_version, version_of
from ..jobs import JobConflict, JobControl
from ..people_io import read_people, write_people_csv
from ..routing import Routes, runtime_of

routes = Routes(tags=["collection"])

PersonId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
#: A record id: ``scheme:value`` (``orcid:0000-0002-…``, ``openalex:A…``), or a bare ORCID iD.
RECORD = re.compile(
    r"^(?:[a-z][a-z0-9_-]{1,31}:[A-Za-z0-9._/-]{1,128}|\d{4}-\d{4}-\d{4}-\d{3}[\dX])$"
)


@routes.get("/api/collection/plan", action="collection.read")
def plan(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """What a collection would do, and what leaves the computer (and what never does)."""
    return runtime_of(request).collection.plan(ctx.project)


@routes.post("/api/collection/start", action="collection.start")
def start(request: Request, ctx: ProjectDep) -> JSONResponse:
    """Start collecting (a job; one job per project at a time: 409 names the running one)."""
    runtime = runtime_of(request)
    service = runtime.collection
    if not service.available:
        raise ApiError(
            409,
            "collection_unavailable",
            "collecting texts is not available in this version",
            next_action="none",
        )
    project = ctx.project

    def work(control: JobControl) -> dict[str, Any]:
        return dict(service.collect(project, control))

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="collection",
            work=work,
            title="collect texts",
        )
    except JobConflict as exc:
        raise ApiError(409, "busy", str(exc), next_action="wait", job=exc.running.id) from exc
    return JSONResponse({"job": info.as_dict()}, status_code=202)


def _latest(request: Request, ctx: Any) -> Any:
    jobs = [j for j in runtime_of(request).jobs.list(ctx.id) if j.kind == "collection"]
    return jobs[0] if jobs else None


@routes.get("/api/collection", action="collection.read")
def progress(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """The running collection, or the last one: its progress and result."""
    job = _latest(request, ctx)
    if job is None:
        return {
            "job": None,
            "empty": empty_hint("no collection has run", "Plan a collection", "collect"),
        }
    return {"job": job.as_dict()}


@routes.post("/api/collection/cancel", action="collection.start")
def cancel(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """Stop the running collection at its next safe point."""
    job = _latest(request, ctx)
    if job is None or job.state not in ("queued", "running", "cancelling"):
        raise ApiError(409, "not_running", "no collection is running")
    info = runtime_of(request).jobs.cancel(job.id)
    return {"job": info.as_dict() if info else job.as_dict()}


@routes.get("/api/collection/identities", action="collection.read")
def identities(
    request: Request,
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    state: Annotated[Literal["pending", "confirmed", "auto", "none", "all"], Query()] = "pending",
) -> dict[str, Any]:
    """The identity queue: each person to check, with candidate records and their evidence."""
    people, fp = read_people(ctx.project)
    rows = [
        p
        for p in people
        if p["role"] != "excluded"
        and not p["merged_into"]
        and (state == "all" or p["identity"] == state)
        and (not params.q or params.q in f"{p['last_name']} {p['first_name']}".casefold())
    ]
    out = page(
        rows,
        params,
        sorts={
            "name": lambda p: f"{p['last_name']} {p['first_name']}".casefold(),
            "texts": lambda p: p["coverage"].get("texts", 0),
        },
        default_sort="name",
        filters={"state": state, "q": params.q},
        empty=empty_hint(
            "nobody waits for a check" if state == "pending" else "nobody in this state",
            "See everyone",
            "none",
        ),
        extra={"version": version_of(fp)},
    )
    ids = [p["person_id"] for p in out["items"]]
    candidates = runtime_of(request).collection.candidates(ctx.project, ids)
    out["items"] = [
        {
            "person_id": p["person_id"],
            "last_name": p["last_name"],
            "first_name": p["first_name"],
            "orcid": p["orcid"],
            "unit": p["unit"],
            "identity": p["identity"],
            "records": p["records"],
            "candidates": candidates.get(p["person_id"], []),
        }
        for p in out["items"]
    ]
    response.headers["ETag"] = etag_of(fp)
    return out


class IdentityDecision(BaseModel):
    """``accept`` a candidate record, ``none`` (no record exists), or ``id``: a pasted record."""

    decision: Literal["accept", "none", "id"]
    record: Annotated[str | None, Field(max_length=200)] = None


def _record(value: str | None) -> str:
    text = (value or "").strip()
    if not RECORD.match(text):
        raise ApiError(
            422,
            "invalid",
            "a record is scheme:id (orcid:0000-0002-1825-0097, openalex:A123…) or an ORCID iD",
            next_action="fix-input",
        )
    return f"orcid:{text}" if re.match(r"^\d{4}-", text) else text


@routes.post("/api/collection/identities/{person_id}", action="collection.identities")
def decide(
    request: Request,
    response: Response,
    person_id: str,
    body: IdentityDecision,
    ctx: ProjectDep,
) -> dict[str, Any]:
    """Decide one person's identity (send ``If-Match`` of the people's version)."""
    expected = expected_version(request)
    with ctx.handle.mutex:
        people, _ = read_people(ctx.project)
        if person_id not in {p["person_id"] for p in people}:
            raise ApiError(404, "not_found", f"no person {person_id!r}", next_action="reload")
        if body.decision == "none":
            change = {"identity": "none", "records": ""}
        elif body.decision == "id":
            change = {"identity": "confirmed", "records": _record(body.record)}
        else:
            found = runtime_of(request).collection.candidates(ctx.project, [person_id])
            options = [c["record"] for c in found.get(person_id, [])]
            record = body.record or (options[0] if options else None)
            if record is None or record not in options:
                raise ApiError(
                    409,
                    "no_candidate",
                    "this record is not a candidate of this person; paste an id instead",
                    next_action="fix-input",
                )
            change = {"identity": "confirmed", "records": record}
        fp = write_people_csv(
            ctx.project,
            {person_id: change},
            expected=expected,
            action=f"identity {body.decision} for {person_id}",
        )
    response.headers["ETag"] = etag_of(fp)
    return {"person_id": person_id, **change, "version": version_of(fp)}


class BulkAccept(BaseModel):
    person_ids: Annotated[list[PersonId], Field(min_length=1, max_length=5000)]


@routes.post("/api/collection/identities/accept", action="collection.identities")
def accept_many(
    request: Request, response: Response, body: BulkAccept, ctx: ProjectDep
) -> dict[str, Any]:
    """Accept the best candidate of each person listed (those without one are left as they are)."""
    expected = expected_version(request)
    found = runtime_of(request).collection.candidates(ctx.project, body.person_ids)
    changes = {
        pid: {"identity": "confirmed", "records": options[0]["record"]}
        for pid, options in found.items()
        if options
    }
    left = sorted(set(body.person_ids) - set(changes))
    if not changes:
        raise ApiError(
            409, "no_candidate", "none of these people has a candidate record", next_action="none"
        )
    with ctx.handle.mutex:
        fp = write_people_csv(
            ctx.project, changes, expected=expected, action=f"accept {len(changes)} identities"
        )
    response.headers["ETag"] = etag_of(fp)
    return {"accepted": sorted(changes), "left": left, "version": version_of(fp)}


@routes.get("/api/collection/coverage", action="collection.read")
def coverage(ctx: ProjectDep) -> dict[str, Any]:
    """How well the texts cover the people: counts per coverage class and per role."""
    people, _ = read_people(ctx.project)
    classes: dict[str, int] = {"good": 0, "thin": 0, "none": 0}
    by_role: dict[str, dict[str, int]] = {}
    texts = 0
    for p in people:
        cls = p["coverage"]["class"]
        classes[cls] += 1
        by_role.setdefault(p["role"], {"good": 0, "thin": 0, "none": 0})[cls] += 1
        texts += p["coverage"].get("texts", 0)
    return {
        "people": len(people),
        "classes": classes,
        "by_role": by_role,
        "authorships": texts,
        "empty": None
        if people
        else empty_hint("no people yet: import a list of names", "Import people", "import-people"),
    }
