# SPDX-License-Identifier: MIT
"""The corpus screen's reading routes: coverage, organisations, texts, a person's sheet,
duplicates; and importing folders of documents and corpora (a job)."""

from __future__ import annotations

import shutil
from typing import Annotated, Any, Literal

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from ..corpus_view import (
    coverage_states,
    organisation_detail,
    organisations,
    people_view,
    person_detail,
    stamp,
    text_detail,
    work_copies,
)
from ..deps import ListDep, ProjectDep, page
from ..errors import ApiError
from ..jobs import JobConflict, JobControl
from ..messages import empty
from ..people_io import read_people
from ..routing import Routes, runtime_of
from ..texts_view import texts_view
from ..uploads import extract_archive, save_upload
from .build import busy_error

routes = Routes(tags=["corpus"])

ItemId = Annotated[str, Query(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]


# ── coverage ─────────────────────────────────────────────────────────────────


@routes.get("/api/collection/coverage", action="collection.read")
def coverage(
    request: Request,
    ctx: ProjectDep,
    organisations_shown: Annotated[int, Query(alias="organisations", ge=0, le=5000)] = 200,
) -> dict[str, Any]:
    """How well the texts cover the people: coverage classes per role, the four states
    (good, thin, failed, no data) and their first blocking causes, the states by
    organisation (the largest first), the texts by year and by language, the slots;
    computed once per version of what they read."""
    runtime = runtime_of(request)
    fp = people_view(ctx.project, runtime.table_cache)["fp"]
    out = runtime.table_cache.get(
        ("coverage", stamp(ctx.project), fp), lambda: _coverage(ctx, runtime)
    )
    return {**out, "by_organisation": out["by_organisation"][:organisations_shown]}


def _coverage(ctx: Any, runtime: Any) -> dict[str, Any]:
    import numpy as np

    from cartolex.collect.coverage import CAUSES, STATES
    from cartolex.collect.decisions import collect_params
    from cartolex.collect.providers import coverage as slot_coverage

    people = people_view(ctx.project, runtime.table_cache)["people"]
    states = coverage_states(ctx.project, runtime.table_cache)
    classes: dict[str, int] = {"good": 0, "thin": 0, "none": 0}
    by_state: dict[str, int] = dict.fromkeys(STATES, 0)
    by_role: dict[str, dict[str, int]] = {}
    causes: dict[str, int] = {}
    authorships = 0
    counted: set[str] = set()
    for p in people:
        cls = p["coverage"]["class"]
        classes[cls] += 1
        by_role.setdefault(p["role"], {"good": 0, "thin": 0, "none": 0})[cls] += 1
        authorships += p["coverage"].get("texts", 0)
        st = states.get(p["person_id"])
        if st is None or p["role"] == "excluded" or p["merged_into"]:
            continue
        counted.add(p["person_id"])
        by_state[st["state"]] += 1
        if st["cause"]:
            causes[st["cause"]] = causes.get(st["cause"], 0) + 1
    orgs = organisations(ctx.project, runtime.table_cache)
    by_org = _states_by_organisation(ctx, states, counted, {o["org_id"]: o for o in orgs})
    # The texts of the people counted, each once (a preprint naming its published version
    # is left out).
    view = texts_view(ctx.project, runtime.table_cache)
    rows = np.flatnonzero(view.of_people(counted) & ~view.versioned())
    by_year: dict[str, dict[str, int]] = {}
    years = np.where(view.has_year[rows], view.year[rows], -1).astype(np.int64)
    keys, counts = np.unique(years * 2 + (view.content[rows] == 0), return_counts=True)
    for key, n in zip(keys.tolist(), counts.tolist(), strict=True):
        year, titles_only = divmod(key, 2)
        entry = by_year.setdefault(
            str(year) if year >= 0 else "unknown", {"with_abstract": 0, "titles_only": 0}
        )
        entry["titles_only" if titles_only else "with_abstract"] += n
    by_language: dict[str, int] = {}
    for code, n in enumerate(np.bincount(view.language[rows], minlength=len(view.languages))):
        if n:
            name = view.languages[code] or "und"
            by_language[name] = by_language.get(name, 0) + int(n)
    return {
        "people": len(people),
        "counted": len(counted),
        "good": collect_params(ctx.project, "coverage")["good"],
        "classes": classes,
        "states": by_state,
        "causes": {k: {"count": n, "message": CAUSES.get(k, k)} for k, n in sorted(causes.items())},
        "by_role": by_role,
        "by_organisation": by_org,
        "organisations": len(by_org),
        "by_year": dict(sorted(by_year.items())),
        "by_language": dict(sorted(by_language.items(), key=lambda kv: (-kv[1], kv[0]))),
        "slots": slot_coverage(ctx.layout),
        "authorships": authorships,
        "empty": None if people else empty("empty_no_people"),
    }


def _states_by_organisation(
    ctx: Any, states: dict[str, dict[str, Any]], counted: set[str], orgs: dict[str, Any]
) -> list[dict[str, Any]]:
    import pyarrow as pa

    from cartolex.collect.coverage import STATES

    from ..corpus_view import effective_affiliation_table

    if not ctx.layout.table("affiliations").exists() or not counted:
        return []
    table = effective_affiliation_table(ctx.project, [])
    who = sorted(counted)
    persons = pa.table({"person_id": who, "state": [states[pid]["state"] for pid in who]})
    joined = table.join(persons, "person_id", join_type="inner").group_by(["org_id", "state"])
    grouped = joined.aggregate([("person_id", "count_distinct")])
    out: dict[str, dict[str, Any]] = {}
    for oid, state, n in zip(
        grouped["org_id"].to_pylist(),
        grouped["state"].to_pylist(),
        grouped["person_id_count_distinct"].to_pylist(),
        strict=True,
    ):
        if oid not in out:
            org = orgs.get(oid, {})
            out[oid] = {
                "org_id": oid,
                "name": org.get("name", oid),
                "level": org.get("level", ""),
                "people": 0,
                **dict.fromkeys(STATES, 0),
            }
        out[oid][state] += n
        out[oid]["people"] += n
    return sorted(out.values(), key=lambda o: (-o["people"], o["name"].casefold()))


# ── organisations ────────────────────────────────────────────────────────────


@routes.get("/api/organisations", action="people.read")
def list_organisations(
    request: Request,
    ctx: ProjectDep,
    params: ListDep,
    level: Annotated[str | None, Query(max_length=64)] = None,
    parent: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    """Organisations with their level, parents and people; filters ``level``, ``parent``."""
    rows = organisations(ctx.project, runtime_of(request).table_cache)
    levels: dict[str, int] = {}
    for o in rows:
        levels[o["level"]] = levels.get(o["level"], 0) + 1
    shown = [
        o
        for o in rows
        if (level is None or o["level"] == level)
        and (parent is None or parent in o["parents"])
        and (
            not params.q or params.q in o["name"].casefold() or params.q in o["acronym"].casefold()
        )
    ]
    return page(
        shown,
        params,
        sorts={
            "name": lambda o: o["name"].casefold(),
            "level": lambda o: o["level"],
            "people": lambda o: o["people"],
            "people_ever": lambda o: o["people_ever"],
            "children": lambda o: o["children"],
        },
        default_sort="name",
        filters={"level": level, "parent": parent, "q": params.q},
        empty=empty("empty_no_match") if rows else empty("empty_no_people"),
        extra={"counts": {"level": levels}},
    )


@routes.get("/api/organisations/{org_id}", action="people.read")
def get_organisation(org_id: str, ctx: ProjectDep) -> dict[str, Any]:
    """One organisation: its parents, units, people and the years of each affiliation."""
    found = organisation_detail(ctx.project, org_id)
    if found is None:
        raise ApiError.of("organisation_not_found", org=org_id)
    return found


# ── texts ────────────────────────────────────────────────────────────────────


#: How the texts list sorts.
TEXT_SORTS = ("content", "people", "source", "title", "year")


@routes.get("/api/texts", action="people.read")
def list_texts(
    request: Request,
    ctx: ProjectDep,
    params: ListDep,
    slot: Annotated[str | None, Query(max_length=64)] = None,
    year: Annotated[int | None, Query(ge=0, le=3000)] = None,
    language: Annotated[str | None, Query(max_length=16)] = None,
    content: Annotated[Literal["title", "abstract", "full"] | None, Query()] = None,
    provider: Annotated[str | None, Query(max_length=32)] = None,
    person: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    """Texts with their parts per provider; filters ``slot``, ``year``, ``language``,
    ``content`` (what the richest part is), ``provider``, ``person``; counts per content
    and provider, and the ``duplicates``: copies of a work the corpus reads once (each
    copy's ``copy_of`` names the text read). The texts are a view of columns: a page
    turns only its own rows into objects, whatever the number of texts."""
    runtime = runtime_of(request)
    view = texts_view(ctx.project, runtime.table_cache)
    copies = work_copies(ctx.project, runtime.table_cache)
    sort = params.sort or "-year"
    if sort.lstrip("-") not in TEXT_SORTS:
        raise ApiError.of("invalid_sort", sort=sort.lstrip("-"), sorts=list(TEXT_SORTS))
    mask = view.select(
        slot=slot,
        year=year,
        language=language,
        content=content,
        provider=provider,
        among=view.of_people(_with_merged(ctx, runtime, person)) if person is not None else None,
        q=params.q,
    )
    shown, total = view.page(mask, sort, params.offset, params.limit)
    filters = {
        "slot": slot,
        "year": year,
        "language": language,
        "content": content,
        "provider": provider,
        "person": person,
        "q": params.q,
    }
    return {
        "items": view.rows(shown, copies),
        "total": total,
        "offset": params.offset,
        "limit": params.limit,
        "sort": sort,
        "sorts": list(TEXT_SORTS),
        "filters": {k: v for k, v in filters.items() if v not in (None, "")},
        "empty": None if total else empty("empty_no_match" if view.n else "empty_no_collection"),
        "counts": {**view.counts(), "duplicates": len(copies)},
    }


def _with_merged(ctx: Any, runtime: Any, person: str) -> list[str]:
    """A person and the rows merged into them: whose texts are theirs."""
    from cartolex.project.identity import merge_roots, merged_groups

    from ..corpus_view import coverage_inputs

    decisions, _ = coverage_inputs(ctx.project, runtime.table_cache)
    return [person, *merged_groups(merge_roots(decisions)).get(person, [])]


@routes.get("/api/texts/{text_id}", action="people.read")
def get_text(request: Request, text_id: str, ctx: ProjectDep) -> dict[str, Any]:
    """One text: its parts by provider (a preview each), its people, the records merged
    into it, its versions and the conflicts between finders."""
    found = text_detail(ctx.project, text_id, runtime_of(request).table_cache)
    if found is None:
        raise ApiError.of("text_not_found", text=text_id)
    return found


# ── one person, duplicates ───────────────────────────────────────────────────


@routes.get("/api/people/duplicates", action="people.read")
def duplicates(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """Pairs of people who may be one person, with the reason; none is merged."""
    from cartolex.collect.people_import import find_duplicates

    if not ctx.layout.table("people").exists():
        return {"items": [], "total": 0}
    people, _ = read_people(ctx.project, runtime_of(request).table_cache)
    names = {p["person_id"]: f"{p['first_name']} {p['last_name']}".strip() for p in people}
    units = {p["person_id"]: p["unit"] for p in people}
    items = [
        {
            "person_id": d.person_id,
            "other_id": d.other_id,
            "reason": d.reason,
            "names": [names.get(d.person_id, ""), names.get(d.other_id, "")],
            "units": [units.get(d.person_id, ""), units.get(d.other_id, "")],
        }
        for d in find_duplicates(ctx.project)
    ]
    return {"items": items, "total": len(items)}


@routes.get("/api/people/{person_id}/sheet", action="people.read")
def sheet(request: Request, person_id: str, ctx: ProjectDep) -> dict[str, Any]:
    """Why a person's profile is what it is: the coverage and its first blocking cause, the
    sources used and discarded, the attempts, the texts, the affiliations with their years."""
    cache = runtime_of(request).table_cache
    found = person_detail(ctx.project, person_id, cache)
    if found is None:
        raise ApiError.of("person_not_found", person=person_id)
    people = people_view(ctx.project, cache)["people"]
    decision = next((p for p in people if p["person_id"] == person_id), None)
    if decision is not None:
        found["decision"] = {
            k: decision[k] for k in ("role", "set", "identity", "records", "merged_into", "note")
        }
    return found


# ── documents and corpora ────────────────────────────────────────────────────


@routes.post("/api/people/import/documents", action="people.import", status_code=202)
async def import_documents(request: Request, ctx: ProjectDep) -> JSONResponse:
    """Import a folder of documents (a zip, or one document) or a corpus (a zip holding its
    index CSV and files), as a job. Form fields: ``file``, ``kind`` (``folder`` or
    ``corpus``), ``person_id`` (every document is this person's), ``create_people``."""
    from ..collection import new_import_id

    runtime = runtime_of(request)
    settings = runtime.settings
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise ApiError.of("file_missing")
    form = await request.form(max_files=1, max_fields=8)
    staging = runtime.uploads_of(ctx.id) / new_import_id()
    docs = staging / "documents"
    try:
        item = form.get("file")
        if item is None or isinstance(item, str):
            raise ApiError.of("file_missing")
        kind = str(form.get("kind") or "folder")
        person_id = str(form.get("person_id") or "") or None
        create = str(form.get("create_people") or "") in ("1", "true", "on")
        limit = int(settings.max_upload_mb * 1024 * 1024)

        async def chunks() -> Any:
            while block := await item.read(1 << 20):
                yield block

        name = item.filename or "upload"
        if name.lower().endswith(".zip"):
            archive = await save_upload(chunks(), staging, name, max_bytes=limit)
            extract_archive(
                archive,
                docs,
                max_members=settings.max_archive_members,
                max_bytes=int(settings.max_archive_mb * 1024 * 1024),
            )
            archive.unlink(missing_ok=True)
        else:
            await save_upload(chunks(), docs, name, max_bytes=limit)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        await form.close()
    if kind not in ("folder", "corpus"):
        shutil.rmtree(staging, ignore_errors=True)
        raise ApiError.of("import_refused", detail=f"{kind} is neither folder nor corpus")
    index = None
    if kind == "corpus":
        found = sorted(p for p in docs.glob("*.csv"))
        if not found:
            shutil.rmtree(staging, ignore_errors=True)
            raise ApiError.of("corpus_index_missing")
        index = found[0]
    elif not any(
        p.suffix.lower() in (".pdf", ".txt", ".md") for p in docs.rglob("*") if p.is_file()
    ):
        shutil.rmtree(staging, ignore_errors=True)
        raise ApiError.of("documents_missing")
    project = ctx.project

    def work(control: JobControl) -> dict[str, Any]:
        from cartolex.collect.people_import import import_corpus, import_folder

        control.progress({"fraction": 0.05, "message": "reading the documents"})
        try:
            if index is not None:
                report = import_corpus(project, index, root=docs)
            else:
                report = import_folder(project, docs, create_people=create, person_id=person_id)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        control.progress({"fraction": 1.0, "message": "done"})
        return {
            "action": f"import_{kind}",
            "texts": report.texts,
            "people_created": report.people_created,
            "refused": len(report.refused),
            "refusals": [f"{where}: {why}" for where, why in report.refused[:50]],
            "duplicates": len(report.duplicates),
            "summary": f"{report.texts} text(s), {report.people_created} person(s) created",
            "summary_code": "imported",
            "summary_params": {"texts": report.texts, "people": report.people_created},
        }

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="import",
            work=work,
            title=f"import a {kind}",
            title_code=f"import_{kind}",
        )
    except JobConflict as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise busy_error(exc.running) from exc
    return JSONResponse({"job": info.as_dict()}, status_code=202)
