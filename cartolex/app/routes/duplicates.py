# SPDX-License-Identifier: MIT
"""Duplicates: pairs of people who may be one person, compared side by side, decided one
after another (merge, two people, later), and the clear ones merged in one step."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

import numpy as np
from fastapi import Query, Request, Response
from pydantic import BaseModel, Field

from ..deps import ListDep, ProjectDep, page
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty
from ..routing import Routes, runtime_of

routes = Routes(tags=["people"])

PersonId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
#: How many pairs a preview of the automatic merge shows.
PREVIEW = 12
#: How many recent titles and co-authors the comparison shows of each side.
RECENT = 5
COAUTHORS = 8


class _Columns:
    """The texts' view (:mod:`cartolex.app.texts_view`) as the duplicates read texts."""

    def __init__(self, view: Any) -> None:
        self.view = view
        self.n = view.n
        self.year = view.year
        self.has_year = view.has_year
        self.author_text = np.asarray(view.authors["text"])
        self.author_person = np.asarray(view.authors["person"])
        self.person_ids = view.person_ids
        self._ids: list[str] | None = None

    def tid(self, row: int) -> str:
        if self._ids is None:
            self._ids = self.view.table["text_id"].to_pylist()
        return self._ids[row]


def _key(project: Any) -> tuple[Any, ...]:
    from cartolex.project.identity import merges_digest

    from ..corpus_view import org_stamp

    return ("duplicates", *org_stamp(project), merges_digest(project.layout.people_csv))


def found_pairs(ctx: Any, runtime: Any) -> dict[str, Any]:
    """Every pair and the facts of each person, computed once per version of the tables,
    the merges and the decisions on organisations (decided pairs are filtered later)."""

    def compute() -> dict[str, Any]:
        from cartolex.collect.duplicates import duplicate_pairs
        from cartolex.project.organisations import org_decisions, org_roots

        from ..corpus_view import coverage_inputs, organisations
        from ..texts_view import texts_view

        project = ctx.project
        if not project.layout.table("people").exists():
            return {"pairs": [], "facts": {}}
        decisions, _ = coverage_inputs(project, runtime.table_cache)
        view = texts_view(project, runtime.table_cache)
        names = {
            o["org_id"]: o["acronym"] or o["name"]
            for o in organisations(project, runtime.table_cache)
        }
        pairs, facts = duplicate_pairs(
            project,
            decisions=decisions,
            columns=lambda: _Columns(view),
            org_roots=org_roots(org_decisions(project.layout)),
            org_names=names,
        )
        return {"pairs": pairs, "facts": facts}

    return runtime.table_cache.get(_key(ctx.project), compute)


def _brief(f: Any, person: dict[str, Any] | None) -> dict[str, Any]:
    p = person or {}
    return {
        "person_id": f.person_id,
        "name": " ".join(x for x in (f.first_name, f.last_name) if x) or f.person_id,
        "first_name": f.first_name,
        "last_name": f.last_name,
        "unit": p.get("unit", ""),
        "role": p.get("role", f.role),
        "identity": p.get("identity", f.identity),
        "orcids": sorted(f.orcids),
        "texts": f.texts,
        "first_year": f.first_year,
        "last_year": f.last_year,
    }


def _people(ctx: Any, runtime: Any) -> tuple[dict[str, dict[str, Any]], str | None]:
    from ..corpus_view import people_view

    view = people_view(ctx.project, runtime.table_cache)
    return {p["person_id"]: p for p in view["people"]}, view["fp"]


def _last_auto(people: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The latest automatic merge still in place: its time and the rows it merged."""
    from cartolex.project.identity import AUTO_MERGE_NOTE

    batches: dict[str, list[str]] = defaultdict(list)
    for pid, p in people.items():
        note = p.get("note") or ""
        if p.get("merged_into") and note.startswith(AUTO_MERGE_NOTE):
            batches[note[len(AUTO_MERGE_NOTE) :].strip()].append(pid)
    if not batches:
        return None
    at = max(batches)
    return {"at": at, "person_ids": sorted(batches[at])}


Show = Literal["open", "clear", "later", "all"]


@routes.get("/api/people/duplicates", action="people.read")
def duplicates(
    request: Request,
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    show: Annotated[Show, Query()] = "open",
) -> dict[str, Any]:
    """Pairs of people who may be one person, the most likely first, with each line of
    evidence (``code``, ``params``, English ``text``, ``points``), the ``score``, whether
    the pair is ``clear`` (the automatic merge takes it) and whether two ORCIDs conflict.
    ``show``: ``open`` (not decided), ``clear``, ``later`` or ``all`` (``distinct`` pairs
    never come back). Counts per kind; ``last_auto``, the latest automatic merge."""
    from cartolex.project.pairs import pair_key, read_pairs

    runtime = runtime_of(request)
    found = found_pairs(ctx, runtime)
    people, fp = _people(ctx, runtime)
    decided = read_pairs(ctx.layout)
    facts = found["facts"]
    counts = {"open": 0, "clear": 0, "later": 0, "distinct": 0}
    rows = []
    for pair in found["pairs"]:
        decision = (decided.get(pair_key(pair.a, pair.b)) or {}).get("decision") or None
        if decision == "distinct":
            counts["distinct"] += 1
            continue
        if decision == "later":
            counts["later"] += 1
        else:
            counts["open"] += 1
            counts["clear"] += pair.clear
        wanted = (
            show == "all"
            or (show == "later" and decision == "later")
            or (show == "open" and decision is None)
            or (show == "clear" and decision is None and pair.clear)
        )
        if not wanted:
            continue
        a, b = facts[pair.a], facts[pair.b]
        item = {
            **pair.as_dict(),
            "key": f"{pair.a}|{pair.b}",
            "decision": decision,
            "people": [_brief(a, people.get(pair.a)), _brief(b, people.get(pair.b))],
        }
        if params.q and not any(params.q in x["name"].casefold() for x in item["people"]):
            continue
        rows.append(item)
    response.headers["ETag"] = etag_of(fp)
    return page(
        rows,
        params,
        sorts={"score": lambda r: r["score"], "name": lambda r: r["people"][0]["name"].casefold()},
        default_sort="-score",
        filters={"show": show, "q": params.q},
        empty=empty("empty_no_match") if found["pairs"] else empty("empty_no_duplicates"),
        extra={"counts": counts, "last_auto": _last_auto(people), "version": version_of(fp)},
    )


def _side(ctx: Any, runtime: Any, pid: str, merged: list[str]) -> dict[str, Any]:
    """One side of a comparison, from the project's tables and decisions only."""
    from ..corpus_view import person_detail

    detail = person_detail(ctx.project, pid, runtime.table_cache) or {}
    texts = detail.get("texts") or []
    years = [t["year"] for t in texts if t.get("year")]
    return {
        "person_id": pid,
        "name": " ".join(x for x in (detail.get("first_name"), detail.get("last_name")) if x),
        "aliases": detail.get("aliases") or [],
        "orcid": detail.get("orcid"),
        "ids": detail.get("ids") or {},
        "source": detail.get("source"),
        "columns": detail.get("columns") or {},
        "affiliations": detail.get("affiliations") or [],
        "texts": len(texts),
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "recent": [
            {"text_id": t["text_id"], "title": t["title"], "year": t["year"]}
            for t in texts[:RECENT]
        ],  # fmt: skip
        "merged_from": detail.get("merged_from") or [],
        "text_ids": {t["text_id"] for t in texts},
    }


def _coauthors(ctx: Any, runtime: Any, everyone: dict[str, list[str]]) -> dict[str, dict[str, int]]:
    """Each side → the people they wrote with in the project (person id → texts)."""
    from ..texts_view import texts_view

    view = texts_view(ctx.project, runtime.table_cache)
    index = {p: i for i, p in enumerate(view.person_ids)}
    text, person = np.asarray(view.authors["text"]), np.asarray(view.authors["person"])
    out: dict[str, dict[str, int]] = {}
    for side, ids in everyone.items():
        codes = [index[p] for p in ids if p in index]
        mine = np.isin(person, codes)
        texts = np.unique(text[mine])
        others = person[np.isin(text, texts) & ~np.isin(person, codes)]
        values, counts = np.unique(others, return_counts=True)
        out[side] = {view.person_ids[int(v)]: int(c) for v, c in zip(values, counts, strict=True)}
    return out


@routes.get("/api/people/duplicates/compare", action="people.read")
def compare(
    request: Request,
    ctx: ProjectDep,
    a: Annotated[str, Query(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")],
    b: Annotated[str, Query(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")],
) -> dict[str, Any]:
    """Two people side by side, from the project's own data: names and aliases, ORCID and
    records, identity and role, affiliations with their years, texts (how many, their
    years, the most recent titles), co-authors in the project, and what they share: texts,
    organisations, co-authors; with the pair's evidence."""
    from cartolex.project.identity import merge_roots, merged_groups

    from ..corpus_view import coverage_inputs, people_view

    runtime = runtime_of(request)
    people = {p["person_id"]: p for p in people_view(ctx.project, runtime.table_cache)["people"]}
    unknown = sorted({a, b} - set(people))
    if unknown:
        raise ApiError.of("unknown_people", ids=unknown)
    decisions, _ = coverage_inputs(ctx.project, runtime.table_cache)
    groups = merged_groups(merge_roots(decisions))
    sides = {pid: _side(ctx, runtime, pid, groups.get(pid, [])) for pid in (a, b)}
    co = _coauthors(ctx, runtime, {pid: [pid, *groups.get(pid, [])] for pid in (a, b)})
    names = {pid: f"{p['first_name']} {p['last_name']}".strip() for pid, p in people.items()}
    for pid, side in sides.items():
        p = people[pid]
        side.update(
            role=p["role"],
            set=p["set"],
            identity=p["identity"],
            records=p["records"],
            unit=p["unit"],
            coauthors=[
                {"person_id": o, "name": names.get(o, o), "texts": n}
                for o, n in sorted(co[pid].items(), key=lambda kv: (-kv[1], names.get(kv[0], "")))[
                    :COAUTHORS
                ]
            ],
        )
    shared_ids = sides[a].pop("text_ids") & sides[b].pop("text_ids")
    found = found_pairs(ctx, runtime)
    pair = next(
        (p for p in found["pairs"] if {p.a, p.b} == {a, b}),
        None,
    )
    orgs_a = {x["org_id"]: x["name"] for x in sides[a]["affiliations"]}
    orgs_b = {x["org_id"] for x in sides[b]["affiliations"]}
    common_co = sorted(set(co[a]) & set(co[b]) - {a, b}, key=lambda o: names.get(o, o))
    shared_texts = []
    if shared_ids:
        from ..texts_view import texts_view

        view = texts_view(ctx.project, runtime.table_cache)
        rows = view.rows(np.flatnonzero(view.where(sorted(shared_ids))), {})
        shared_texts = [
            {"text_id": t["text_id"], "title": t["title"], "year": t["year"]} for t in rows
        ]
    return {
        "a": sides[a],
        "b": sides[b],
        "shared": {
            "texts": shared_texts,
            "organisations": [
                {"org_id": o, "name": orgs_a[o]} for o in sorted(set(orgs_a) & orgs_b)
            ],
            "coauthors": [{"person_id": o, "name": names.get(o, o)} for o in common_co[:COAUTHORS]],
            "coauthors_total": len(common_co),
        },
        "pair": pair.as_dict() if pair is not None else None,
    }


class PairDecision(BaseModel):
    """One pair decided: ``merge`` (into *keep*), ``distinct`` (two people) or ``later``.
    A merge of two people whose ORCIDs differ needs *override*."""

    a: PersonId
    b: PersonId
    decision: Literal["merge", "distinct", "later"]
    keep: PersonId | None = None
    override: bool = False


@routes.post("/api/people/duplicates/decide", action="people.write")
def decide(
    request: Request, response: Response, body: PairDecision, ctx: ProjectDep
) -> dict[str, Any]:
    """Decide one pair (send ``If-Match`` of the people): a merge is written in
    ``people.csv`` (the other side's ``merged_into``), two people or later in
    ``people_pairs.csv``. Answers the people's version, for the next decision."""
    from cartolex.collect.decisions import read_people as read_rows
    from cartolex.project.files import fingerprint
    from cartolex.project.identity import MergeRefused, merge_changes
    from cartolex.project.pairs import forget_pairs, remember_pairs

    from ..people_io import write_people_csv
    from .people import _merge_refused

    expected = expected_version(request)
    if body.a == body.b:
        raise ApiError.of("self_merge")
    runtime = runtime_of(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        people, _ = _people(ctx, runtime)
        unknown = sorted({body.a, body.b} - set(people))
        if unknown:
            raise ApiError.of("unknown_people", ids=unknown)
        if body.decision == "merge":
            keep = body.keep or body.a
            if keep not in (body.a, body.b):
                raise ApiError.of("invalid", problems=["keep is one of the pair"])
            other = body.b if keep == body.a else body.a
            rows = read_rows(ctx.layout)
            for pid in (keep, other):
                rows.setdefault(pid, {"person_id": pid, "merged_into": "", "records": ""})
            try:
                changes = merge_changes(
                    rows,
                    keep,
                    [other],
                    {pid: p["orcid"] for pid, p in people.items()},
                    override=body.override,
                )
            except MergeRefused as exc:
                raise _merge_refused(exc) from exc
            fp = write_people_csv(
                ctx.project, changes, expected=expected, action=f"merge {other} into {keep}"
            )
            forget_pairs(ctx.layout, [(body.a, body.b)])
        else:
            remember_pairs(ctx.layout, [(body.a, body.b)], body.decision)
            fp = fingerprint(ctx.layout.people_csv)
    response.headers["ETag"] = etag_of(fp)
    return {"decided": body.decision, "a": body.a, "b": body.b, "version": version_of(fp)}


class AutoMerge(BaseModel):
    """The automatic merge of the clear pairs: a preview, or (``apply``) the merge itself."""

    apply: bool = False


def _clear_groups(ctx: Any, runtime: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The groups the automatic merge would make
    (:func:`cartolex.collect.duplicates.clear_groups`), on the roles of now."""
    from cartolex.collect.duplicates import clear_groups
    from cartolex.project.pairs import read_pairs

    found = found_pairs(ctx, runtime)
    people, _ = _people(ctx, runtime)
    now = {pid: (p["role"], p["identity"]) for pid, p in people.items()}
    groups = clear_groups(found["pairs"], found["facts"], set(read_pairs(ctx.layout)), now)
    return groups, people


@routes.post("/api/people/duplicates/auto", action="people.write")
def auto_merge(
    request: Request, response: Response, body: AutoMerge, ctx: ProjectDep
) -> dict[str, Any]:
    """Merge the clear pairs: without ``apply``, what it would do (the groups, the rows
    merged, examples); with it (send ``If-Match`` of the people), one write of
    ``people.csv`` whose rows carry the same note and time, undone as one step with
    ``POST /api/people/unmerge`` on the rows it answers."""
    from cartolex.collect.decisions import read_people as read_rows
    from cartolex.project.identity import AUTO_MERGE_NOTE, MergeRefused, merge_changes

    from ..people_io import write_people_csv

    runtime = runtime_of(request)
    groups, people = _clear_groups(ctx, runtime)
    merged = sum(len(g["merge"]) for g in groups)
    examples = [
        {
            "keep": {"person_id": g["keep"], "name": g["names"][g["keep"]]},
            "merge": [{"person_id": m, "name": g["names"][m]} for m in g["merge"]],
            "evidence": [e for p in g["pairs"][:1] for e in p.evidence],
        }
        for g in groups[:PREVIEW]
    ]
    if not body.apply:
        return {"groups": len(groups), "merged": merged, "examples": examples, "applied": False}
    expected = expected_version(request)
    if not groups:
        raise ApiError.of("nothing_to_change")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    note = f"{AUTO_MERGE_NOTE} {stamp}"
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        rows = read_rows(ctx.layout)
        changes: dict[str, dict[str, str]] = {}
        for g in groups:
            for pid in (g["keep"], *g["merge"]):
                rows.setdefault(pid, {"person_id": pid, "merged_into": "", "records": ""})
            try:
                found = merge_changes(
                    rows,
                    g["keep"],
                    g["merge"],
                    {p: (people.get(p) or {}).get("orcid") for p in rows},
                    note=note,
                )
            except MergeRefused:
                continue
            for pid, change in found.items():
                changes[pid] = {**change, "note": note}
        if not changes:
            raise ApiError.of("nothing_to_change")
        fp = write_people_csv(
            ctx.project, changes, expected=expected, action=f"merge {len(changes)} clear duplicates"
        )
    response.headers["ETag"] = etag_of(fp)
    return {
        "groups": len(groups),
        "merged": len(changes),
        "person_ids": sorted(changes),
        "at": stamp,
        "examples": examples,
        "applied": True,
        "version": version_of(fp),
    }
