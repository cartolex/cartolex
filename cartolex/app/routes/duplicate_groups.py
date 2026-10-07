# SPDX-License-Identifier: MIT
"""Duplicates by groups: people who may all be one person, three or more as well as two,
compared side by side, then merged (all of them or some, into the one kept), set apart
or left for later in one step. The same routes say « these are one person » for people
chosen by hand (the People list, a person's sheet)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from pydantic import BaseModel, Field

from ..deps import ListDep, ProjectDep, page
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty
from ..routing import Routes, runtime_of
from .duplicates import (
    COAUTHORS,
    PersonId,
    _brief,
    _coauthors,
    _key,
    _people,
    _side,
    decided_pairs,
    folded,
    found_pairs,
    standing,
)

routes = Routes(tags=["people"])

#: How many people one comparison or one decision takes at most.
MAX_PEOPLE = 50
Show = Literal["open", "clear", "later", "all"]


def _stamp(path: Any) -> tuple[Any, ...]:
    try:
        st = path.stat()
    except FileNotFoundError:
        return (None,)
    return (st.st_size, st.st_mtime_ns)


def review(ctx: Any, runtime: Any) -> dict[str, Any]:
    """The groups of the review (:func:`cartolex.collect.duplicates.review_groups`) on the
    people of now and the pairs decided, computed once per version of the pairs, the
    merges and the pairs decided: each group's members, its pairs (with their decision)
    and the people in brief."""
    from cartolex.collect.duplicates import review_groups

    layout = ctx.project.layout
    key = ("duplicate-groups", *_key(ctx.project), _stamp(layout.people_csv),
           _stamp(layout.people_pairs_csv))  # fmt: skip

    def compute() -> dict[str, Any]:
        found = found_pairs(ctx, runtime)
        people, _ = _people(ctx, runtime)
        decided = decided_pairs(ctx, people)
        distinct = {k for k, d in decided.items() if d == "distinct"}
        pairs = standing(found["pairs"], people, found["facts"])
        groups = review_groups(pairs, found["facts"], distinct)
        return {
            "groups": groups,
            "decided": decided,
            "distinct": len(distinct),
            "common_names": found["common_names"],
        }

    return runtime.table_cache.get(key, compute)


def _kept(ids: list[str], facts: dict[str, Any], people: dict[str, dict[str, Any]]) -> str:
    """The person a merge of *ids* keeps by default (their roles and identities of now)."""
    from dataclasses import replace

    from cartolex.collect.duplicates import choose_kept

    now = []
    for pid in ids:
        p = people.get(pid) or {}
        now.append(replace(facts[pid], role=p.get("role", ""), identity=p.get("identity", "")))
    return choose_kept(now)


def _clear(ids: list[str], pairs: list[Any], decided: dict[tuple[str, str], str]) -> bool:
    """Whether the clear pairs not decided join every one of *ids*."""
    parent = {x: x for x in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    for p in pairs:
        if p.clear and not p.conflict and not decided.get((p.a, p.b)):
            parent[find(p.a)] = find(p.b)
    return len({find(x) for x in ids}) == 1


@routes.get("/api/people/duplicates/groups", action="people.read")
def groups(
    request: Request,
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    show: Annotated[Show, Query()] = "open",
) -> dict[str, Any]:
    """Groups of people who may all be one person, the most likely first, paged: the pairs
    at least :data:`~cartolex.collect.duplicates.REVIEW_SCORE` likely joined (never across
    a pair said to be two people, nor two different ORCIDs, at most
    :data:`~cartolex.collect.duplicates.MAX_GROUP` people), every other pair a group of
    its own two. Each with ``ids``, ``keep`` (kept by default), ``people`` in brief, its
    ``pairs`` (each with its evidence and ``decision``), ``score`` (its likeliest pair),
    ``clear`` (the clear pairs join them all), ``conflict``, ``decision`` (``later`` when
    every pair is). ``show``: ``open``, ``clear``, ``later``, ``all``; counts per kind,
    ``people`` in open groups, ``distinct`` pairs, ``common_names`` (names too common to
    be proposed pair by pair) and ``last_auto``."""
    from .duplicates import _last_auto

    runtime = runtime_of(request)
    found = found_pairs(ctx, runtime)
    people, fp = _people(ctx, runtime)
    seen = review(ctx, runtime)
    decided = seen["decided"]
    facts = found["facts"]
    counts = {"open": 0, "clear": 0, "later": 0, "people": 0, "distinct": seen["distinct"]}
    waiting: set[str] = set()
    q = folded(params.q) if params.q else ""
    rows: list[dict[str, Any]] = []
    for n, (ids, pairs) in enumerate(seen["groups"]):
        later = all(decided.get((p.a, p.b)) == "later" for p in pairs)
        clear = not later and _clear(ids, pairs, decided)
        if later:
            counts["later"] += 1
        else:
            counts["open"] += 1
            waiting.update(ids)
            counts["clear"] += clear
        wanted = (
            show == "all"
            or (show == "later" and later)
            or (show == "open" and not later)
            or (show == "clear" and clear)
        )
        if not wanted:
            continue
        if q and not any(q in folded(_name(facts[x])) for x in ids):
            continue
        rows.append({"n": n, "score": pairs[0].score, "name": _name(facts[ids[0]]).casefold(),
                     "later": later, "clear": clear})  # fmt: skip
    counts["people"] = len(waiting)
    response.headers["ETag"] = etag_of(fp)
    out = page(
        rows,
        params,
        sorts={
            "score": lambda r: r["score"],
            "name": lambda r: r["name"],
            "size": lambda r: len(seen["groups"][r["n"]][0]),
        },  # fmt: skip
        default_sort="-score",
        filters={"show": show, "q": params.q},
        empty=empty("empty_no_match") if seen["groups"] else empty("empty_no_duplicates"),
        extra={
            "counts": counts,
            "common_names": seen["common_names"][:20],
            "last_auto": _last_auto(people),
            "version": version_of(fp),
        },
    )
    out["items"] = [_group(r, seen, facts, people) for r in out["items"]]
    return out


def _name(f: Any) -> str:
    return " ".join(x for x in (f.first_name, f.last_name) if x) or f.person_id


def _group(
    row: dict[str, Any], seen: dict[str, Any], facts: dict[str, Any], people: dict[str, Any]
) -> dict[str, Any]:
    ids, pairs = seen["groups"][row["n"]]
    keep = _kept(ids, facts, people)
    ordered = [keep, *[x for x in ids if x != keep]]
    return {
        "key": "|".join(ids),
        "ids": ordered,
        "keep": keep,
        "people": [_brief(facts[x], people.get(x)) for x in ordered],
        "pairs": [
            {**p.as_dict(), "decision": seen["decided"].get((p.a, p.b)) or None} for p in pairs
        ],  # fmt: skip
        "score": round(pairs[0].score, 4),
        "clear": row["clear"],
        "conflict": any(p.conflict for p in pairs),
        "decision": "later" if row["later"] else None,
        "size": len(ids),
    }


def _ids(text: str) -> list[str]:
    ids = list(dict.fromkeys(x for x in text.split(",") if x))
    if not 2 <= len(ids) <= MAX_PEOPLE:
        raise ApiError.of("invalid", problems=[f"name 2 to {MAX_PEOPLE} people"])
    return ids


@routes.get("/api/people/duplicates/group", action="people.read")
def compare_group(
    request: Request,
    ctx: ProjectDep,
    ids: Annotated[str, Query(pattern=r"^[A-Za-z0-9_.:-]{1,64}(,[A-Za-z0-9_.:-]{1,64})+$")],
) -> dict[str, Any]:
    """Two or more people side by side, from the project's data only (``ids``, comma
    separated): each one's names and aliases, ORCID, records, identity, role, affiliations
    with their years, texts (count, years, the most recent titles), co-authors in the
    project; what two or more of them share (texts, organisations, co-authors, each with
    who shares it); the pairs proposed among them with their evidence and decision;
    ``keep``, the one a merge keeps by default; ``conflict``, two different ORCIDs among
    them (a merge needs ``override``)."""
    from cartolex.project.identity import (
        effective_orcids,
        merge_roots,
        merged_groups,
        orcid_conflict,
    )

    from ..corpus_view import coverage_inputs

    runtime = runtime_of(request)
    wanted = _ids(ids)
    people, _ = _people(ctx, runtime)
    unknown = sorted(set(wanted) - set(people))
    if unknown:
        raise ApiError.of("unknown_people", ids=unknown)
    decisions, _ = coverage_inputs(ctx.project, runtime.table_cache)
    groups = merged_groups(merge_roots(decisions))
    sides = [_side(ctx, runtime, pid, groups.get(pid, [])) for pid in wanted]
    co = _coauthors(ctx, runtime, {pid: [pid, *groups.get(pid, [])] for pid in wanted})
    names = {pid: f"{p['first_name']} {p['last_name']}".strip() for pid, p in people.items()}
    for side in sides:
        pid = side["person_id"]
        p = people[pid]
        side.update(
            role=p["role"],
            set=p["set"],
            identity=p["identity"],
            records=p["records"],
            unit=p["unit"],
            merged_into=p.get("merged_into") or "",
            coauthors=[
                {"person_id": o, "name": names.get(o, o), "texts": n}
                for o, n in sorted(co[pid].items(), key=lambda kv: (-kv[1], names.get(kv[0], "")))[
                    :COAUTHORS
                ]
            ],
        )
    text_ids = {s["person_id"]: s.pop("text_ids") for s in sides}
    by_text: dict[str, list[str]] = {}
    for pid in wanted:
        for tid in text_ids[pid]:
            by_text.setdefault(tid, []).append(pid)
    shared_ids = {tid: who for tid, who in by_text.items() if len(who) > 1}
    shared_texts = []
    if shared_ids:
        import numpy as np

        from ..texts_view import texts_view

        view = texts_view(ctx.project, runtime.table_cache)
        rows = view.rows(np.flatnonzero(view.where(sorted(shared_ids))), {})
        shared_texts = [{"text_id": t["text_id"], "title": t["title"], "year": t["year"],
                         "people": shared_ids[t["text_id"]]} for t in rows]  # fmt: skip
    orgs: dict[str, dict[str, Any]] = {}
    for side in sides:
        for a in side["affiliations"]:
            o = orgs.setdefault(a["org_id"], {"org_id": a["org_id"], "name": a["name"],
                                              "people": []})  # fmt: skip
            if side["person_id"] not in o["people"]:
                o["people"].append(side["person_id"])
    partners: dict[str, list[str]] = {}
    for pid in wanted:
        for o in co[pid]:
            if o not in wanted:
                partners.setdefault(o, []).append(pid)
    common = sorted((o for o, who in partners.items() if len(who) > 1),
                    key=lambda o: (-len(partners[o]), names.get(o, o)))  # fmt: skip
    found = found_pairs(ctx, runtime)
    seen = review(ctx, runtime)
    inside = set(wanted)
    pairs = [
        {**p.as_dict(), "decision": seen["decided"].get((p.a, p.b)) or None}
        for p in standing(found["pairs"], people, found["facts"])
        if p.a in inside and p.b in inside
    ]
    table_orcids = {pid: p.get("orcid") for pid, p in people.items()}
    orcids = {
        pid: effective_orcids((pid, *groups.get(pid, ())), decisions, table_orcids)
        for pid in wanted
    }
    standing_ids = [x for x in wanted if not people[x].get("merged_into") and x in found["facts"]]
    return {
        "people": sides,
        "shared": {
            "texts": shared_texts,
            "organisations": [o for o in orgs.values() if len(o["people"]) > 1],
            "coauthors": [
                {"person_id": o, "name": names.get(o, o), "people": partners[o]}
                for o in common[:COAUTHORS]
            ],  # fmt: skip
            "coauthors_total": len(common),
        },
        "pairs": pairs,
        "keep": _kept(standing_ids, found["facts"], people) if standing_ids else wanted[0],
        "conflict": any(
            orcid_conflict(orcids[x], orcids[y])
            for i, x in enumerate(wanted)
            for y in wanted[i + 1 :]
        ),
    }


class GroupDecision(BaseModel):
    """People decided together: ``merge`` (every one of *ids* but *keep* into *keep*),
    ``distinct`` (two people each: every two of *ids*, or with *apart* every one of
    *apart* and every other one of *ids*) or ``later`` (the pairs proposed among them).
    A merge of people whose ORCIDs differ needs *override*."""

    ids: Annotated[list[PersonId], Field(min_length=2, max_length=MAX_PEOPLE)]
    decision: Literal["merge", "distinct", "later"]
    keep: PersonId | None = None
    apart: Annotated[list[PersonId], Field(max_length=MAX_PEOPLE)] = []
    override: bool = False


@routes.post("/api/people/duplicates/group", action="people.write")
def decide_group(
    request: Request, response: Response, body: GroupDecision, ctx: ProjectDep
) -> dict[str, Any]:
    """Decide for two or more people at once (send ``If-Match`` of the people): a merge is
    one write of ``people.csv`` (each other one's ``merged_into``; the pairs decided among
    them forgotten), undone in one step by ``POST /api/people/unmerge`` on the ``merged``
    rows it answers; two people or later are written in ``people_pairs.csv``. Answers the
    people's version, for the next decision."""
    from cartolex.collect.decisions import read_people as read_rows
    from cartolex.project.files import fingerprint
    from cartolex.project.identity import MergeRefused, merge_changes
    from cartolex.project.pairs import forget_pairs, pair_key, remember_pairs

    from ..people_io import write_people_csv
    from .people import _merge_refused

    expected = expected_version(request)
    ids = list(dict.fromkeys(body.ids))
    if len(ids) < 2:
        raise ApiError.of("invalid", problems=["name two people or more"])
    runtime = runtime_of(request)
    every = [pair_key(x, y) for i, x in enumerate(ids) for y in ids[i + 1 :]]
    merged: list[str] = []
    with ctx.handle.mutex:
        check_version(ctx.layout.people_csv, expected)
        people, _ = _people(ctx, runtime)
        unknown = sorted(set(ids) - set(people))
        if unknown:
            raise ApiError.of("unknown_people", ids=unknown)
        if body.decision == "merge":
            keep = body.keep or ids[0]
            if keep not in ids:
                raise ApiError.of("invalid", problems=["keep is one of the people named"])
            rows = read_rows(ctx.layout)
            for pid in ids:
                rows.setdefault(pid, {"person_id": pid, "merged_into": "", "records": ""})
            try:
                changes = merge_changes(
                    rows,
                    keep,
                    [x for x in ids if x != keep],
                    {pid: p["orcid"] for pid, p in people.items()},
                    override=body.override,
                )
            except MergeRefused as exc:
                raise _merge_refused(exc) from exc
            fp = write_people_csv(
                ctx.project,
                changes,
                expected=expected,
                action=f"merge {len(ids) - 1} into {keep}",
            )
            merged = sorted(changes)
            forget_pairs(ctx.layout, every)
        else:
            if body.decision == "distinct":
                apart = [x for x in dict.fromkeys(body.apart) if x in ids]
                rest = [x for x in ids if x not in apart]
                chosen = [pair_key(x, y) for x in apart for y in rest] if apart and rest else every
            else:
                inside = set(ids)
                found = found_pairs(ctx, runtime)
                chosen = [
                    (p.a, p.b)
                    for p in standing(found["pairs"], people, found["facts"])
                    if p.a in inside and p.b in inside
                ] or every
            remember_pairs(ctx.layout, chosen, body.decision)
            fp = fingerprint(ctx.layout.people_csv)
    response.headers["ETag"] = etag_of(fp)
    return {"decided": body.decision, "ids": ids, "merged": merged, "version": version_of(fp)}
