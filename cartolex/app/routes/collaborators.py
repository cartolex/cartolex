# SPDX-License-Identifier: MIT
"""People found by collection: collaborators round by round, and the people of institutions."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from pydantic import BaseModel, Field

from ..deps import ListDep, ProjectDep, page
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty
from ..people_io import read_people
from ..routing import Routes, runtime_of
from .collection import latest_job

routes = Routes(tags=["collection"])

PersonId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
Decision = Literal["mapped", "context", "projected", "no", "later"]


# ── collaborators ────────────────────────────────────────────────────────────


def _snowball_header(project: Any) -> dict[str, Any]:
    from cartolex.collect.decisions import collect_params
    from cartolex.collect.tables import read_runs

    runs = [r for s in project.config.slots for r in read_runs(project.layout, s.id, "snowball")]
    runs.sort(key=lambda r: r.run_id)
    defaults = collect_params(project, "snowball")
    if not runs:
        return {"run": None, "cap": defaults["cap"], "max_authors": defaults["max_authors"],
                "rounds": [], "cut": None, "seeds": 0, "fit": ""}  # fmt: skip
    header = runs[-1].header
    return {
        "run": runs[-1].run_id,
        "cap": header.get("cap") or defaults["cap"],
        "max_authors": header.get("max_authors") or defaults["max_authors"],
        "rounds": header.get("rounds") or [],
        "cut": header.get("cut"),
        "seeds": len(header.get("seeds") or {}),
        "fit": header.get("fit") or "",
        "years": header.get("years"),
    }


@routes.get("/api/collection/collaborators", action="collection.read")
def collaborators(
    request: Request,
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    round: Annotated[int | None, Query(ge=1, le=10)] = None,  # noqa: A002 - the column's name
    decision: Annotated[Decision | None, Query()] = None,
) -> dict[str, Any]:
    """The collaborators proposed, round by round, with their evidence (joint texts, fit,
    path) and the decision on each; the cap, the rounds and the cut of the latest run."""
    from cartolex.collect.snowball import read_snowball

    people, fp = read_people(ctx.project, runtime_of(request).table_cache)
    by_id = {p["person_id"]: p for p in people}
    name = {pid: f"{p['first_name']} {p['last_name']}".strip() for pid, p in by_id.items()}
    rows = []
    counts: dict[str, dict[str, int]] = {"round": {}, "decision": {}}
    for r in read_snowball(ctx.project):
        pid = r["person_id"]
        person = by_id.get(pid, {})
        item = {
            "person_id": pid,
            "name": name.get(pid, pid),
            "round": int(r["round"] or 0),
            "seeds": [s for s in (r["seeds"] or "").split(";") if s],
            "seed_names": [name.get(s, s) for s in (r["seeds"] or "").split(";") if s],
            "path": [name.get(s, s) for s in (r["path"] or "").split(">") if s],
            "joint_texts": int(r["joint_texts"] or 0),
            "last_joint_year": int(r["last_joint_year"]) if r["last_joint_year"] else None,
            "fit": float(r["fit"]) if r["fit"] else None,
            "decision": r["decision"] or "context",
            "role": person.get("role", ""),
            "unit": person.get("unit", ""),
            "texts": (person.get("coverage") or {}).get("texts", 0),
        }
        counts["round"][str(item["round"])] = counts["round"].get(str(item["round"]), 0) + 1
        counts["decision"][item["decision"]] = counts["decision"].get(item["decision"], 0) + 1
        rows.append(item)
    shown = [
        r
        for r in rows
        if (round is None or r["round"] == round)
        and (decision is None or r["decision"] == decision)
        and (not params.q or params.q in r["name"].casefold())
    ]
    response.headers["ETag"] = etag_of(fp)
    return page(
        shown,
        params,
        sorts={
            "name": lambda r: r["name"].casefold(),
            "round": lambda r: r["round"],
            "joint_texts": lambda r: r["joint_texts"],
            "fit": lambda r: r["fit"],
            "decision": lambda r: r["decision"],
        },
        default_sort="-joint_texts",
        filters={"round": round, "decision": decision, "q": params.q},
        empty=empty("empty_no_match") if rows else empty("empty_no_collection"),
        extra={
            "counts": counts,
            "latest": _snowball_header(ctx.project),
            "version": version_of(fp),
        },
    )


class CollaboratorDecisions(BaseModel):
    """Person id → ``mapped``, ``context``, ``projected``, ``no`` (excluded) or ``later``."""

    decisions: Annotated[dict[str, Decision], Field(min_length=1, max_length=20_000)]


@routes.post("/api/collection/collaborators/decide", action="people.write")
def decide_collaborators(
    request: Request, response: Response, body: CollaboratorDecisions, ctx: ProjectDep
) -> dict[str, Any]:
    """Decide on collaborators: their role follows (``no`` → excluded, ``later`` → undecided,
    ``projected`` → the set ``collaborators``); send ``If-Match`` of the people."""
    from cartolex.collect.snowball import decide_collaborators as decide
    from cartolex.project.files import fingerprint

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        try:
            changed = decide(ctx.project, dict(body.decisions))
        except ValueError as exc:
            raise ApiError.of("import_refused", detail=str(exc)) from exc
        fp = fingerprint(ctx.layout.people_csv)
    response.headers["ETag"] = etag_of(fp)
    return {"decided": len(body.decisions), "changed": changed, "version": version_of(fp)}


# ── institutions ─────────────────────────────────────────────────────────────


@routes.get("/api/collection/institutions", action="collection.read")
def institutions(request: Request, ctx: ProjectDep, params: ListDep) -> dict[str, Any]:
    """The latest search of institutions (from its job), and the latest proposal of their
    people (paged, the most works first), with the suggested merges and the levels."""
    from cartolex.collect.institutions import read_proposal

    search = latest_job(request, ctx, "institutions")
    found = None
    for job in runtime_of(request).jobs.list(ctx.id):
        result = job.result or {}
        if job.kind == "collection" and "institutions" in result and result.get("search"):
            found = {"search": result["search"], "institutions": result["institutions"]}
            break
    try:
        proposal = read_proposal(ctx.project)
    except FileNotFoundError:
        proposal = None
    out: dict[str, Any] = {
        "search": found,
        "proposal": None,
        "job": search.as_dict() if search else None,
    }
    if proposal is None:
        return out
    known = {p.record: p.person_id for p in proposal.people if p.person_id}
    people = [
        {
            "record": p.record,
            "name": p.name,
            "orcid": p.orcid,
            "works": p.works,
            "first_year": p.first_year,
            "last_year": p.last_year,
            "units": p.units,
            "person_id": p.person_id,
        }
        for p in proposal.people
        if not params.q or params.q in p.name.casefold()
    ]
    listed = page(
        people,
        params,
        sorts={
            "works": lambda p: p["works"],
            "name": lambda p: p["name"].casefold(),
            "last_year": lambda p: p["last_year"],
        },
        default_sort="-works",
        filters={"q": params.q},
        empty=empty("empty_no_match"),
    )
    out["proposal"] = {
        **listed,
        "run": proposal.run_id,
        "roots": [
            {"id": r, "name": proposal.units[r].name if r in proposal.units else r}
            for r in proposal.roots
        ],
        "window": list(proposal.window) if proposal.window else None,
        "min_works": proposal.min_works,
        "below": proposal.below,
        "works_read": proposal.works,
        "units": len(proposal.units),
        "levels": proposal.levels,
        "merges": [
            {
                "records": m.records,
                "reason": m.reason,
                "code": m.code,
                "works": m.works,
                "clear": m.clear,
                "people": m.people,
                "taken": [known.get(r) for r in m.records],
            }
            for m in proposal.merges
        ],
        "already": sum(1 for p in proposal.people if p.person_id),
    }
    return out


@routes.get("/api/collection/institutions/people", action="collection.read")
def institution_people(request: Request, ctx: ProjectDep, params: ListDep) -> dict[str, Any]:
    """The people of the latest proposal of institutions, paged and searched (``q``: a
    name, an ORCID or a record) on the server, whatever their number."""
    from cartolex.collect.institutions import read_proposal

    try:
        proposal = read_proposal(ctx.project)
    except FileNotFoundError:
        return page([], params, sorts={"works": lambda p: p["works"]}, default_sort="-works",
                    empty=empty("empty_no_collection"))  # fmt: skip
    q = params.q
    people = [
        {
            "record": p.record,
            "name": p.name,
            "orcid": p.orcid,
            "works": p.works,
            "first_year": p.first_year,
            "last_year": p.last_year,
            "units": p.units,
            "person_id": p.person_id,
        }
        for p in proposal.people
        if not q or q in p.name.casefold() or q in (p.orcid or "") or q in p.record.casefold()
    ]
    return page(
        people,
        params,
        sorts={
            "works": lambda p: p["works"],
            "name": lambda p: p["name"].casefold(),
            "last_year": lambda p: p["last_year"],
        },
        default_sort="-works",
        filters={"q": q},
        empty=empty("empty_no_match"),
        extra={"run": proposal.run_id},
    )


class TakeBody(BaseModel):
    """``all``, or records (``A1``, ``A1+A2`` for two records of one person); their role.
    With ``all``, *join* lists the groups of records taken as one person each (the clear
    suggested merges, and those someone confirmed); *levels* sets the level of each type
    of institution (``education`` → ``institution``), over the proposal's."""

    take: Annotated[list[str], Field(min_length=1, max_length=20_000)]
    role: Literal["mapped", "context", "projected", "excluded"] = "mapped"
    join: Annotated[list[list[str]] | None, Field(max_length=20_000)] = None
    levels: Annotated[
        dict[str, Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]],
        Field(max_length=50),
    ] = {}


@routes.post("/api/collection/institutions/take", action="people.write")
def take(request: Request, response: Response, body: TakeBody, ctx: ProjectDep) -> dict[str, Any]:
    """Take people from the latest proposal (``identity`` confirmed with their records);
    send ``If-Match`` of the people."""
    from cartolex.collect.institutions import take_people
    from cartolex.project.files import fingerprint

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        try:
            report = take_people(
                ctx.project,
                "all" if body.take == ["all"] else body.take,
                role=body.role,
                join=body.join,
                levels=body.levels or None,
            )
        except FileNotFoundError as exc:
            raise ApiError.of("no_institution_proposal") from exc
        except ValueError as exc:
            raise ApiError.of("import_refused", detail=str(exc)) from exc
        fp = fingerprint(ctx.layout.people_csv)
    response.headers["ETag"] = etag_of(fp)
    return {
        "taken": sorted(report.taken),
        "known": report.known,
        "refused": [{"what": w, "why": y} for w, y in report.refused],
        "version": version_of(fp),
    }
