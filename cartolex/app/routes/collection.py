# SPDX-License-Identifier: MIT
"""Collection: the plan (what leaves the computer), the job, the identity queue, coverage."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import ListDep, ProjectDep, page
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..jobs import JobConflict, JobControl
from ..messages import empty
from ..people_io import read_people, write_people_csv
from ..routing import Routes, principal_of, runtime_of
from .build import busy_error

routes = Routes(tags=["collection"])

PersonId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
#: A record id: ``scheme:value`` (``orcid:0000-0002-…``, ``openalex:A…``, ``hal:<idHAL>``), or
#: a bare ORCID iD.
RECORD = re.compile(
    r"^(?:[a-z][a-z0-9_-]{1,31}:[A-Za-z0-9._/-]{1,128}|\d{4}-\d{4}-\d{4}-\d{3}[\dX])$"
)
Action = Literal["collect", "identify", "harvest", "institutions", "collaborators", "retry"]
#: A threshold for a single clear match when a service gives no flag of its own.
CLEAR_SCORE = 0.8


class CollectOptions(BaseModel):
    """What to collect: the action and its options (each action reads its own)."""

    action: Action = "collect"
    people: Annotated[list[PersonId] | None, Field(max_length=100_000)] = None
    years: Annotated[list[int | None] | None, Field(min_length=2, max_length=2)] = None
    hal: bool = True
    scielo: Annotated[str | None, Field(pattern=r"^[a-z]{2,8}$")] = None
    issns: Annotated[list[str], Field(max_length=200)] = []
    abstracts: bool = False
    search: Annotated[str | None, Field(max_length=200)] = None
    institutions: Annotated[list[str] | None, Field(max_length=100)] = None
    min_works: Annotated[int, Field(ge=1, le=1000)] = 2
    rounds: Annotated[int, Field(ge=1, le=5)] = 1
    seeds: Annotated[list[PersonId] | None, Field(max_length=100_000)] = None
    cap: Annotated[int | None, Field(ge=1, le=100_000)] = None
    max_authors: Annotated[int | None, Field(ge=2, le=10_000)] = None
    #: How OpenAlex is read: its API, or the snapshot folder saved on this computer (by
    #: default, the faster of the two the plan offers).
    openalex: Literal["api", "snapshot"] | None = None
    #: The checkpoint of a paused collection to resume (its options come from it).
    resume: Annotated[str | None, Field(pattern=r"^[a-z_]{1,40}-[0-9a-f]{16}$")] = None
    consent: bool = False
    #: « Don't show this again »: with ``consent``, the notice of this kind of collection is
    #: shown briefly from now on, until its content changes.
    remember: bool = False

    def options(self) -> dict[str, Any]:
        return self.model_dump(
            exclude={"action", "consent", "remember", "resume"}, exclude_none=True
        )


def with_notice(request: Request, summary: dict[str, Any]) -> dict[str, Any]:
    """The plan with the notice it asks for this person (:mod:`cartolex.app.notices`):
    ``notice``, and ``consent_needed`` unless the level is ``none``."""
    from ..notices import notice_level

    runtime = runtime_of(request)
    notice = notice_level(summary, runtime.notices.read(principal_of(request).id))
    if not summary.get("consent_needed", True):  # the service says nothing leaves (a stand-in)
        notice = {**notice, "level": "none", "reasons": ["nothing_sent"]}
    return {**summary, "notice": notice, "consent_needed": notice["level"] != "none"}


@routes.get("/api/collection/plan", action="collection.read")
def plan(
    request: Request, ctx: ProjectDep, action: Annotated[Action, Query()] = "collect"
) -> dict[str, Any]:
    """What *action* would do with its default options, and what leaves the computer."""
    return with_notice(request, runtime_of(request).collection.plan(ctx.project, action, {}))


@routes.post("/api/collection/plan", action="collection.read")
def plan_with(request: Request, body: CollectOptions, ctx: ProjectDep) -> dict[str, Any]:
    """What an action with these options would do, and what leaves the computer (and what
    never does); nothing is sent. ``notice.level`` says how much of it to show."""
    summary = runtime_of(request).collection.plan(ctx.project, body.action, body.options())
    return with_notice(request, summary)


@routes.post("/api/collection/start", action="collection.start")
def start(request: Request, ctx: ProjectDep, body: CollectOptions | None = None) -> JSONResponse:
    """Start an action (a job; one job per project at a time: 409 names the running one).

    When the plan asks consent (its notice is ``brief`` or ``full``: people's names or
    identifiers leave the computer, or the work is beyond the free daily budget), the
    request carries ``consent: true``: the person has read the plan; with
    ``remember: true`` too, this kind's notice is brief from now on.
    """
    runtime = runtime_of(request)
    service = runtime.collection
    if not service.available:
        raise ApiError.of("collection_unavailable")
    body = body or CollectOptions()
    project = ctx.project
    action, options = body.action, body.options()
    if body.resume:
        # A paused collection goes on with the options it was started with.
        found = service.resume_options(project, body.resume)
        if found is None:
            raise ApiError.of("checkpoint_not_found", checkpoint=body.resume)
        options = {**found, "resume": True}
        if body.openalex:  # resumed another way (a snapshot reading keeps its own checkpoint)
            options["openalex"] = body.openalex
    summary = with_notice(request, service.plan(project, action, options))
    if summary["consent_needed"] and not body.consent:
        hosts = ", ".join(h["host"] for h in summary["leaves_the_computer"]) or "nobody"
        raise ApiError.of("consent_needed", hosts=hosts, extra={"plan": summary})
    if body.consent and body.remember and summary["notice"]["personal"]:
        notice = summary["notice"]
        runtime.notices.acknowledge(principal_of(request).id, notice["kind"], notice["digest"])
    if summary.get("openalex"):  # the job reads OpenAlex the way its plan said
        options = {**options, "openalex": summary["openalex"]["chosen"]}

    def work(control: JobControl) -> dict[str, Any]:
        out = dict(service.collect(project, control, action, options))
        if out.get("outcome") in (None, "succeeded") and action in RESULT_LINKS:
            out["link"], out["link_code"] = RESULT_LINKS[action]
        return out

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="collection",
            work=work,
            title=f"collect: {summary.get('action', action)}",
            title_code=f"collect_{summary.get('action', action)}",
        )
    except JobConflict as exc:
        raise busy_error(exc.running) from exc
    return JSONResponse({"job": info.as_dict()}, status_code=202)


#: Where to go once a collection ends: an identification leads to the identities to check,
#: a harvest to the build (an address, and the code of its button's words).
RESULT_LINKS = {
    "identify": ("/people?tab=identities", "identities"),
    "harvest": ("/build", "build"),
}


def latest_job(request: Request, ctx: Any, action: str | None = None) -> Any:
    """The newest collection job (of *action*), or ``None``."""
    jobs = [j for j in runtime_of(request).jobs.list(ctx.id) if j.kind == "collection"]
    if action is not None:
        jobs = [j for j in jobs if (j.result or {}).get("action") == action]
    return jobs[0] if jobs else None


@routes.get("/api/collection", action="collection.read")
def progress(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """The running collection, or the last one: its progress and result."""
    job = latest_job(request, ctx)
    if job is None:
        return {"job": None, "empty": empty("empty_no_collection")}
    return {"job": job.as_dict()}


@routes.post("/api/collection/cancel", action="collection.start")
def cancel(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """Stop the running collection at its next safe point."""
    job = latest_job(request, ctx)
    if job is None or job.state not in ("queued", "running", "cancelling"):
        raise ApiError.of("collection_not_running")
    info = runtime_of(request).jobs.cancel(job.id)
    return {"job": info.as_dict() if info else job.as_dict()}


# ── the identity queue ────────────────────────────────────────────────────────


def _demo_evidence(key: str, value: Any) -> dict[str, Any]:
    """A piece of the demo services' evidence as a code (``corpus.evidence.demo_<key>``)."""
    return {"code": f"demo_{key}", "params": {"value": value if value is not True else ""}}


def normalised(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidates in one shape, best first, the single clear match flagged."""
    out = []
    for c in candidates:
        evidence = c.get("evidence") or []
        codes = list(c.get("evidence_codes") or [])
        if isinstance(evidence, dict):
            shown = [(k, v) for k, v in evidence.items() if v not in (None, "", False)]
            evidence = [f"{k}: {v}" for k, v in shown]
            codes = [_demo_evidence(k, v) for k, v in shown]
        evidence = [
            {"text": str(e[0]), "points": e[1]}
            if isinstance(e, list | tuple) and len(e) == 2
            else {"text": str(e), "points": None}
            for e in evidence
        ]
        if len(codes) == len(evidence):  # older records have the words only
            for e, code in zip(evidence, codes, strict=True):
                e["code"] = code.get("code") or ""
                e["params"] = dict(code.get("params") or {})
        out.append(
            {
                "finder": c.get("finder") or "demo",
                "record": c.get("record"),
                "name": c.get("name") or "",
                "score": c.get("score"),
                "evidence": evidence,
                "detail": c.get("detail") or "",
                "detail_code": c.get("detail_code") or "",
                "detail_params": dict(c.get("detail_params") or {}),
                "clear": c.get("clear"),
            }
        )
    usable = [c for c in out if c["record"]]
    if all(c["clear"] is None for c in out):
        single = (
            len(usable) == 1
            and isinstance(usable[0]["score"], int | float)
            and usable[0]["score"] >= CLEAR_SCORE
        )
        for c in out:
            c["clear"] = bool(single and c is usable[0])
    return out


@routes.get("/api/collection/identities", action="collection.read")
def identities(
    request: Request,
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    state: Annotated[Literal["pending", "confirmed", "auto", "none", "all"], Query()] = "pending",
    clear: Annotated[bool | None, Query()] = None,
    finder: Annotated[str | None, Query(max_length=32)] = None,
) -> dict[str, Any]:
    """The identity queue: each person to check, with every finder's candidate records and
    their evidence; ``clear`` keeps the people with (or without) a single clear match."""
    from ..corpus_view import people_view

    runtime = runtime_of(request)
    view = people_view(ctx.project, runtime.table_cache)
    people, fp = view["people"], view["fp"]
    rows = [
        p
        for p in people
        if p["role"] != "excluded"
        and not p["merged_into"]
        and (state == "all" or p["identity"] == state)
        and (not params.q or params.q in f"{p['last_name']} {p['first_name']}".casefold())
    ]
    counts = {"clear": 0, "unclear": 0, "no_candidate": 0}
    found = {
        pid: normalised(c)
        for pid, c in runtime.collection.candidates(
            ctx.project, [p["person_id"] for p in rows]
        ).items()
    }
    for p in rows:
        cands = found.get(p["person_id"], [])
        key = "clear" if any(c["clear"] for c in cands) else "unclear" if cands else "no_candidate"
        counts[key] += 1
    if clear is not None:
        rows = [p for p in rows if any(c["clear"] for c in found.get(p["person_id"], [])) is clear]
    if finder:
        rows = [
            p for p in rows if any(c["finder"] == finder for c in found.get(p["person_id"], []))
        ]
    out = page(
        rows,
        params,
        sorts={
            "name": lambda p: f"{p['last_name']} {p['first_name']}".casefold(),
            "texts": lambda p: p["coverage"].get("texts", 0),
            "candidates": lambda p: len(found.get(p["person_id"], [])),
        },
        default_sort="name",
        filters={"state": state, "q": params.q, "clear": clear, "finder": finder},
        empty=empty(
            "empty_no_identity_to_check" if state == "pending" else "empty_no_identity_in_state"
        ),
        extra={"version": version_of(fp), "counts": counts},
    )
    out["items"] = [
        {
            "person_id": p["person_id"],
            "last_name": p["last_name"],
            "first_name": p["first_name"],
            "orcid": p["orcid"],
            "unit": p["unit"],
            "identity": p["identity"],
            "records": p["records"],
            "columns": p["columns"],
            "candidates": found.get(p["person_id"], []),
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
        raise ApiError.of("invalid_record")
    return f"orcid:{text}" if re.match(r"^\d{4}-", text) else text


class BulkAccept(BaseModel):
    person_ids: Annotated[list[PersonId], Field(min_length=1, max_length=5000)]


@routes.post("/api/collection/identities/accept", action="collection.identities")
def accept_many(
    request: Request, response: Response, body: BulkAccept, ctx: ProjectDep
) -> dict[str, Any]:
    """Accept the single clear match of each person listed; the others are left as they are
    (``left``), to decide one by one."""
    expected = expected_version(request)
    found = runtime_of(request).collection.candidates(ctx.project, body.person_ids)
    changes = {}
    for pid, options in found.items():
        clear = [c for c in normalised(options) if c["clear"]]
        if clear:
            changes[pid] = {"identity": "confirmed", "records": clear[0]["record"]}
    left = sorted(set(body.person_ids) - set(changes))
    if not changes:
        raise ApiError.of("no_clear_match")
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        fp = write_people_csv(
            ctx.project, changes, expected=expected, action=f"accept {len(changes)} identities"
        )
    response.headers["ETag"] = etag_of(fp)
    return {"accepted": sorted(changes), "left": left, "version": version_of(fp)}


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
        check_version(ctx.layout.people_csv, expected)
        people, _ = read_people(ctx.project, runtime_of(request).table_cache)
        if person_id not in {p["person_id"] for p in people}:
            raise ApiError.of("person_not_found", person=person_id)
        if body.decision == "none":
            change = {"identity": "none", "records": ""}
        elif body.decision == "id":
            change = {"identity": "confirmed", "records": _record(body.record)}
        else:
            found = runtime_of(request).collection.candidates(ctx.project, [person_id])
            options = [c["record"] for c in found.get(person_id, []) if c.get("record")]
            record = body.record or (options[0] if options else None)
            if record is None or record not in options:
                raise ApiError.of("not_a_candidate")
            change = {"identity": "confirmed", "records": record}
        fp = write_people_csv(
            ctx.project,
            {person_id: change},
            expected=expected,
            action=f"identity {body.decision} for {person_id}",
        )
    response.headers["ETag"] = etag_of(fp)
    return {"person_id": person_id, **change, "version": version_of(fp)}
