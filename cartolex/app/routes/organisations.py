# SPDX-License-Identifier: MIT
"""Organisations as people decide them: rename, change a level or parents, merge and
unmerge, review the pairs that may be one organisation, add or remove affiliations."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from pydantic import BaseModel, Field

from ..deps import ListDep, ProjectDep, page
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty
from ..routing import Routes, runtime_of

routes = Routes(tags=["people"])

OrgId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
Level = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$|^$")]
#: How many people the comparison of two organisations names.
PEOPLE_SHOWN = 20


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_orgs(
    ctx: Any, changes: dict[str, dict[str, str]], expected: str | None, action: str
) -> str:
    """Apply *changes* (organisation id → columns) to ``organisations.csv``, guarded by
    *expected*; a row left with nothing decided is removed. Returns the new version."""
    from cartolex.project.files import write_decision
    from cartolex.project.organisations import org_decisions
    from cartolex.project.tables import decision_csv_bytes

    rows = org_decisions(ctx.layout)
    stamp = _now()
    for oid, values in changes.items():
        row = rows.setdefault(oid, {"org_id": oid, "level": "", "parents": "", "name": "",
                                    "merged_into": "", "note": "", "decided_at": stamp})  # fmt: skip
        row.update({k: "" if v is None else str(v) for k, v in values.items()})
        row["decided_at"] = stamp
        if not any(row.get(k) for k in ("level", "parents", "name", "merged_into", "note")):
            rows.pop(oid)
    data = decision_csv_bytes("organisations", list(rows.values()))
    return write_decision(
        ctx.layout, ctx.layout.organisations_csv, data, expected=expected, action=action
    )


def _known_orgs(ctx: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    from ..corpus_view import _rows

    found = {
        o["org_id"]: o
        for o in _rows(ctx.project, "organisations", None, [("org_id", "in", sorted(set(ids)))])
    }
    unknown = sorted(set(ids) - set(found))
    if unknown:
        raise ApiError.of("organisation_not_found", org=", ".join(unknown[:5]))
    return found


class OrgEdit(BaseModel):
    """What people set on an organisation: its name, level, parents (an empty value gives
    the source's back); ``None`` leaves a field as it is."""

    name: Annotated[str | None, Field(max_length=300)] = None
    level: Level | None = None
    parents: Annotated[list[OrgId] | None, Field(max_length=50)] = None
    note: Annotated[str | None, Field(max_length=500)] = None


@routes.patch("/api/organisations/{org_id}", action="people.write")
def edit_organisation(
    request: Request, response: Response, org_id: str, body: OrgEdit, ctx: ProjectDep
) -> dict[str, Any]:
    """Rename an organisation, change its level or its parents (send ``If-Match`` of
    ``organisations.csv``)."""
    expected = expected_version(request)
    change: dict[str, str] = {}
    if body.name is not None:
        change["name"] = body.name.strip()
    if body.level is not None:
        change["level"] = body.level
    if body.parents is not None:
        if org_id in body.parents:
            raise ApiError.of("invalid", problems=["an organisation is not its own parent"])
        change["parents"] = ";".join(dict.fromkeys(body.parents))
    if body.note is not None:
        change["note"] = body.note
    if not change:
        raise ApiError.of("nothing_to_change")
    with ctx.handle.mutex:
        check_version(ctx.layout.organisations_csv, expected)
        _known_orgs(ctx, [org_id, *(body.parents or [])])
        fp = _write_orgs(ctx, {org_id: change}, expected, f"edit {org_id}")
    response.headers["ETag"] = etag_of(fp)
    return {"changed": org_id, "version": version_of(fp)}


class OrgMerge(BaseModel):
    """Organisations that are one: *sources* are merged into *target*."""

    target: OrgId
    sources: Annotated[list[OrgId], Field(min_length=1, max_length=100)]


def _merge_changes(
    rows: dict[str, dict[str, str]], target: str, sources: list[str]
) -> dict[str, dict[str, str]]:
    from cartolex.project.organisations import org_roots

    if target in sources:
        raise ApiError.of("invalid", problems=["an organisation is not merged into itself"])
    roots = org_roots(rows)
    if target in roots:
        raise ApiError.of("invalid", problems=[f"{target} is itself merged into {roots[target]}"])
    changes = {s: {"merged_into": target} for s in sources}
    for oid, root in roots.items():  # those merged into a source follow it
        if root in sources and oid != target:
            changes[oid] = {"merged_into": target}
    return changes


@routes.post("/api/organisations/merge", action="people.write")
def merge_organisations(
    request: Request, response: Response, body: OrgMerge, ctx: ProjectDep
) -> dict[str, Any]:
    """Merge organisations that are one (``merged_into``; send ``If-Match`` of
    ``organisations.csv``): their affiliations become the target's."""
    from cartolex.project.organisations import org_decisions

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.organisations_csv, expected)
        _known_orgs(ctx, [body.target, *body.sources])
        changes = _merge_changes(org_decisions(ctx.layout), body.target, list(body.sources))
        fp = _write_orgs(ctx, changes, expected, f"merge {len(body.sources)} into {body.target}")
    response.headers["ETag"] = etag_of(fp)
    return {"merged": sorted(changes), "into": body.target, "version": version_of(fp)}


class OrgUnmerge(BaseModel):
    """Organisations merged into another that stand on their own again (an organisation
    others are merged into: every one of them); *remember* records the pairs undone."""

    org_ids: Annotated[list[OrgId], Field(min_length=1, max_length=5000)]
    remember: Literal["distinct", "later"] | None = None


@routes.post("/api/organisations/unmerge", action="people.write")
def unmerge_organisations(
    request: Request, response: Response, body: OrgUnmerge, ctx: ProjectDep
) -> dict[str, Any]:
    """Undo merges of organisations (send ``If-Match`` of ``organisations.csv``)."""
    from cartolex.project.organisations import org_decisions, org_roots
    from cartolex.project.pairs import remember_pairs

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.organisations_csv, expected)
        rows = org_decisions(ctx.layout)
        roots = org_roots(rows)
        undone = {
            oid: roots[oid]
            for oid in rows
            if oid in roots and (oid in body.org_ids or roots[oid] in body.org_ids)
        }
        if not undone:
            raise ApiError.of("nothing_to_change")
        fp = _write_orgs(ctx, {oid: {"merged_into": ""} for oid in undone}, expected,
                         f"unmerge {len(undone)} organisations")  # fmt: skip
        if body.remember:
            remember_pairs(ctx.layout, list(undone.items()), body.remember, kind="organisations")
    response.headers["ETag"] = etag_of(fp)
    return {"unmerged": sorted(undone), "from": undone, "version": version_of(fp)}


# ── pairs that may be one organisation ───────────────────────────────────────


def _norm(name: str) -> str:
    from cartolex.collect.names import words

    return " ".join(words(name or ""))


def org_pairs(ctx: Any, runtime: Any) -> list[dict[str, Any]]:
    """Pairs of organisations that may be one: the same ROR or OpenAlex id (clear: the
    automatic merge takes them), or the same name once case, accents and punctuation are
    set aside with the same parents (proposed); merged organisations are left out."""
    from ..corpus_view import org_stamp, organisations

    def compute() -> list[dict[str, Any]]:
        orgs = organisations(ctx.project, runtime.table_cache)
        by: dict[tuple[str, str], list[str]] = defaultdict(list)
        for o in orgs:
            for scheme in ("ror", "openalex"):
                value = (o["ids"] or {}).get(scheme)
                if value:
                    by[(scheme, str(value).lower())].append(o["org_id"])
            name = _norm(o["name"])
            if name:
                by[("name", f"{name}|{';'.join(sorted(o['parents']))}")].append(o["org_id"])
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for (scheme, value), ids in by.items():
            if not 1 < len(ids) <= 20:
                continue
            for i, a in enumerate(sorted(ids)):
                for b in sorted(ids)[i + 1 :]:
                    entry = found.setdefault(
                        (a, b), {"a": a, "b": b, "evidence": [], "clear": False}
                    )
                    code = {"ror": "org_same_ror", "openalex": "org_same_openalex"}.get(
                        scheme, "org_same_name"
                    )
                    shown = value.split("|")[0]
                    entry["evidence"].append({"code": code, "params": {"value": shown}})
                    entry["clear"] = entry["clear"] or scheme in ("ror", "openalex")
        return sorted(found.values(), key=lambda p: (not p["clear"], p["a"], p["b"]))

    return runtime.table_cache.get(("org-pairs", *org_stamp(ctx.project)), compute)


def _org_brief(o: dict[str, Any]) -> dict[str, Any]:
    return {k: o.get(k) for k in ("org_id", "name", "acronym", "level", "parent_names", "country",
                                  "people", "people_ever", "ids", "source")}  # fmt: skip


@routes.get("/api/organisations/pairs", action="people.read")
def organisation_pairs(
    request: Request,
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    show: Annotated[Literal["open", "clear", "later", "all"], Query()] = "open",
) -> dict[str, Any]:
    """Pairs of organisations that may be one, with their evidence (``org_same_ror``,
    ``org_same_openalex``: clear; ``org_same_name``: the same name and parents), filtered
    by ``organisation_pairs.csv`` (``distinct`` pairs never come back); counts."""
    from cartolex.project.files import fingerprint
    from cartolex.project.pairs import pair_key, read_pairs

    from ..corpus_view import organisations

    runtime = runtime_of(request)
    by_id = {o["org_id"]: o for o in organisations(ctx.project, runtime.table_cache)}
    decided = read_pairs(ctx.layout, "organisations")
    counts = {"open": 0, "clear": 0, "later": 0, "distinct": 0}
    rows = []
    for p in org_pairs(ctx, runtime):
        if p["a"] not in by_id or p["b"] not in by_id:
            continue
        decision = (decided.get(pair_key(p["a"], p["b"])) or {}).get("decision") or None
        if decision == "distinct":
            counts["distinct"] += 1
            continue
        counts["later" if decision else "open"] += 1
        counts["clear"] += bool(p["clear"] and not decision)
        if (
            show == "all"
            or (show == "later" and decision == "later")
            or (show == "open" and not decision)
            or (show == "clear" and not decision and p["clear"])
        ):
            rows.append({**p, "decision": decision, "key": f"{p['a']}|{p['b']}",
                         "orgs": [_org_brief(by_id[p["a"]]), _org_brief(by_id[p["b"]])]})  # fmt: skip
    fp = fingerprint(ctx.layout.organisations_csv)
    response.headers["ETag"] = etag_of(fp)
    return page(
        rows,
        params,
        sorts={"name": lambda r: r["orgs"][0]["name"].casefold(), "clear": lambda r: r["clear"]},
        default_sort="-clear",
        filters={"show": show},
        empty=empty("empty_no_match"),
        extra={"counts": counts, "version": version_of(fp)},
    )


@routes.get("/api/organisations/compare", action="people.read")
def compare_organisations(
    request: Request,
    ctx: ProjectDep,
    a: Annotated[str, Query(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")],
    b: Annotated[str, Query(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")],
) -> dict[str, Any]:
    """Two organisations side by side: names and acronym, ROR and OpenAlex ids, level,
    parents, units, country, source, their people (how many, the first names) and the
    people they share."""
    from ..corpus_view import organisation_detail

    sides = {}
    for oid in (a, b):
        found = organisation_detail(ctx.project, oid)
        if found is None:
            raise ApiError.of("organisation_not_found", org=oid)
        people = {x["person_id"]: x["name"] for x in found.get("affiliations") or []}
        sides[oid] = {
            **{k: v for k, v in found.items() if k != "affiliations"},
            "people": len(people),
            "names": sorted(people.values(), key=str.casefold)[:PEOPLE_SHOWN],
            "_ids": set(people),
            "_names": people,
        }
    common = sides[a].pop("_ids") & sides[b].pop("_ids")
    names = {**sides[a].pop("_names"), **sides[b].pop("_names")}
    return {"a": sides[a], "b": sides[b],
            "shared": {"people": sorted((names[p] for p in common), key=str.casefold)[:PEOPLE_SHOWN],
                       "people_total": len(common)}}  # fmt: skip


class OrgPairDecision(BaseModel):
    """One pair of organisations decided: ``merge`` (into *keep*), ``distinct`` or ``later``."""

    a: OrgId
    b: OrgId
    decision: Literal["merge", "distinct", "later"]
    keep: OrgId | None = None


@routes.post("/api/organisations/decide", action="people.write")
def decide_organisations(
    request: Request, response: Response, body: OrgPairDecision, ctx: ProjectDep
) -> dict[str, Any]:
    """Decide one pair of organisations (send ``If-Match`` of ``organisations.csv``)."""
    from cartolex.project.files import fingerprint
    from cartolex.project.organisations import org_decisions
    from cartolex.project.pairs import forget_pairs, remember_pairs

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.organisations_csv, expected)
        _known_orgs(ctx, [body.a, body.b])
        if body.decision == "merge":
            keep = body.keep or body.a
            if keep not in (body.a, body.b):
                raise ApiError.of("invalid", problems=["keep is one of the pair"])
            other = body.b if keep == body.a else body.a
            changes = _merge_changes(org_decisions(ctx.layout), keep, [other])
            fp = _write_orgs(ctx, changes, expected, f"merge {other} into {keep}")
            forget_pairs(ctx.layout, [(body.a, body.b)], kind="organisations")
        else:
            remember_pairs(ctx.layout, [(body.a, body.b)], body.decision, kind="organisations")
            fp = fingerprint(ctx.layout.organisations_csv)
    response.headers["ETag"] = etag_of(fp)
    return {"decided": body.decision, "version": version_of(fp)}


class OrgAuto(BaseModel):
    """The automatic merge of the clear pairs (the same ROR or OpenAlex id)."""

    apply: bool = False


@routes.post("/api/organisations/auto", action="people.write")
def auto_merge_organisations(
    request: Request, response: Response, body: OrgAuto, ctx: ProjectDep
) -> dict[str, Any]:
    """Without ``apply``, the organisations the clear pairs would merge; with it (send
    ``If-Match`` of ``organisations.csv``), one write that merges them, each into the one
    with the most people."""
    from cartolex.project.organisations import org_decisions
    from cartolex.project.pairs import pair_key, read_pairs

    from ..corpus_view import organisations

    runtime = runtime_of(request)
    by_id = {o["org_id"]: o for o in organisations(ctx.project, runtime.table_cache)}
    decided = read_pairs(ctx.layout, "organisations")
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    for p in org_pairs(ctx, runtime):
        if (
            p["clear"]
            and pair_key(p["a"], p["b"]) not in decided
            and p["a"] in by_id
            and p["b"] in by_id
        ):
            ra, rb = find(p["a"]), find(p["b"])
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list[str]] = defaultdict(list)
    for oid in parent:
        groups[find(oid)].append(oid)
    plan = []
    for root, ids in groups.items():
        members = sorted({root, *ids})
        keep = max(members, key=lambda o: (by_id[o]["people_ever"], -members.index(o)))
        plan.append({"keep": keep, "merge": [m for m in members if m != keep],
                     "names": {m: by_id[m]["name"] for m in members}})  # fmt: skip
    plan.sort(key=lambda g: g["names"][g["keep"]].casefold())
    merged = sum(len(g["merge"]) for g in plan)
    if not body.apply:
        return {"groups": len(plan), "merged": merged, "examples": plan[:12], "applied": False}
    expected = expected_version(request)
    if not plan:
        raise ApiError.of("nothing_to_change")
    with ctx.handle.mutex:
        check_version(ctx.layout.organisations_csv, expected)
        rows = org_decisions(ctx.layout)
        changes: dict[str, dict[str, str]] = {}
        for g in plan:
            changes.update(_merge_changes(rows, g["keep"], g["merge"]))
        fp = _write_orgs(
            ctx, changes, expected, f"merge {len(changes)} organisations by identifier"
        )
    response.headers["ETag"] = etag_of(fp)
    return {"groups": len(plan), "merged": len(changes), "org_ids": sorted(changes),
            "examples": plan[:12], "applied": True, "version": version_of(fp)}  # fmt: skip


# ── affiliations ─────────────────────────────────────────────────────────────


class AffiliationChange(BaseModel):
    """An affiliation added (``add``) or removed (``remove``), or a decision forgotten
    (``forget``: the sources' affiliation comes back, an added one goes)."""

    person_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
    org_id: OrgId
    start_year: Annotated[int | None, Field(ge=1000, le=3000)] = None
    end_year: Annotated[int | None, Field(ge=1000, le=3000)] = None
    action: Literal["add", "remove", "forget"]
    note: Annotated[str, Field(max_length=500)] = ""


class AffiliationChanges(BaseModel):
    changes: Annotated[list[AffiliationChange], Field(min_length=1, max_length=5000)]


@routes.post("/api/affiliations", action="people.write")
def change_affiliations(
    request: Request, response: Response, body: AffiliationChanges, ctx: ProjectDep
) -> dict[str, Any]:
    """Add or remove affiliations, or forget such a decision (send ``If-Match`` of
    ``affiliations.csv``)."""
    from cartolex.project.files import write_decision
    from cartolex.project.organisations import read_affiliation_decisions
    from cartolex.project.tables import decision_csv_bytes

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.affiliations_csv, expected)
        _known_orgs(ctx, [c.org_id for c in body.changes])
        rows = {
            (r["person_id"], r["org_id"], r["start_year"], r["action"]): r
            for r in read_affiliation_decisions(ctx.layout)
        }
        stamp = _now()
        for c in body.changes:
            start = "" if c.start_year is None else str(c.start_year)
            if c.action == "forget":
                for action in ("add", "remove"):
                    rows.pop((c.person_id, c.org_id, start, action), None)
                continue
            other = "remove" if c.action == "add" else "add"
            rows.pop((c.person_id, c.org_id, start, other), None)
            rows[(c.person_id, c.org_id, start, c.action)] = {
                "person_id": c.person_id, "org_id": c.org_id, "start_year": start,
                "end_year": "" if c.end_year is None else str(c.end_year), "action": c.action,
                "note": c.note, "decided_at": stamp,
            }  # fmt: skip
        data = decision_csv_bytes("affiliations", list(rows.values()))
        fp = write_decision(ctx.layout, ctx.layout.affiliations_csv, data, expected=expected,
                            action=f"affiliations: {len(body.changes)} changes")  # fmt: skip
    response.headers["ETag"] = etag_of(fp)
    return {"changed": len(body.changes), "version": version_of(fp)}


@routes.get("/api/affiliations/version", action="people.read")
def affiliations_version(response: Response, ctx: ProjectDep) -> dict[str, Any]:
    """The version of ``affiliations.csv`` (for ``If-Match``) and its rows."""
    from cartolex.project.files import fingerprint
    from cartolex.project.organisations import read_affiliation_decisions

    fp = fingerprint(ctx.layout.affiliations_csv)
    response.headers["ETag"] = etag_of(fp)
    return {"version": version_of(fp), "items": read_affiliation_decisions(ctx.layout)}
