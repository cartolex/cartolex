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
    person_detail,
    text_detail,
    texts,
)
from ..deps import ListDep, ProjectDep, page
from ..errors import ApiError
from ..jobs import JobConflict, JobControl
from ..messages import empty
from ..people_io import read_people
from ..routing import Routes, runtime_of
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
    organisation (the largest first), the texts by year and by language, the slots."""
    from cartolex.collect.coverage import CAUSES, STATES
    from cartolex.collect.decisions import collect_params
    from cartolex.collect.providers import coverage as slot_coverage

    from ..corpus_view import people_view

    runtime = runtime_of(request)
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
    by_year: dict[str, dict[str, int]] = {}
    by_language: dict[str, int] = {}
    for t in texts(ctx.project, runtime.table_cache):
        if t["version_of"] or not any(pid in counted for pid in t["people"]):
            continue
        year = str(t["year"]) if t["year"] is not None else "unknown"
        entry = by_year.setdefault(year, {"with_abstract": 0, "titles_only": 0})
        entry["titles_only" if t["content"] == "title" else "with_abstract"] += 1
        lang = "+".join(t["languages"]) or "und"
        by_language[lang] = by_language.get(lang, 0) + 1
    return {
        "people": len(people),
        "counted": len(counted),
        "good": collect_params(ctx.project, "coverage")["good"],
        "classes": classes,
        "states": by_state,
        "causes": {k: {"count": n, "message": CAUSES.get(k, k)} for k, n in sorted(causes.items())},
        "by_role": by_role,
        "by_organisation": by_org[:organisations_shown],
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
    from cartolex.collect.coverage import STATES
    from cartolex.project.tables import read_source_table

    if not ctx.layout.table("affiliations").exists():
        return []
    table = read_source_table(
        ctx.layout.table("affiliations"), "affiliations", ["person_id", "org_id"]
    )
    members: dict[str, set[str]] = {}
    for pid, oid in zip(table["person_id"].to_pylist(), table["org_id"].to_pylist(), strict=True):
        if pid in counted:
            members.setdefault(oid, set()).add(pid)
    out = []
    for oid, pids in members.items():
        entry = dict.fromkeys(STATES, 0)
        for pid in pids:
            entry[states[pid]["state"]] += 1
        org = orgs.get(oid, {})
        out.append(
            {
                "org_id": oid,
                "name": org.get("name", oid),
                "level": org.get("level", ""),
                "people": len(pids),
                **entry,
            }
        )
    out.sort(key=lambda o: (-o["people"], o["name"].casefold()))
    return out


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
    ``content`` (what the richest part is), ``provider``, ``person``; counts per content."""
    rows = texts(ctx.project, runtime_of(request).table_cache)
    counts: dict[str, dict[str, int]] = {"content": {}, "provider": {}}
    for t in rows:
        counts["content"][t["content"]] = counts["content"].get(t["content"], 0) + 1
        for p in t["providers"]:
            counts["provider"][p] = counts["provider"].get(p, 0) + 1
    shown = [
        t
        for t in rows
        if (slot is None or t["slot"] == slot)
        and (year is None or t["year"] == year)
        and (language is None or language in t["languages"])
        and (content is None or t["content"] == content)
        and (provider is None or provider in t["providers"])
        and (person is None or person in t["people"])
        and (not params.q or params.q in (t["title"] or "").casefold() or params.q == t["doi"])
    ]
    out = page(
        shown,
        params,
        sorts={
            "year": lambda t: t["year"],
            "title": lambda t: (t["title"] or "").casefold(),
            "source": lambda t: t["source"],
            "people": lambda t: len(t["people"]),
            "content": lambda t: ("title", "abstract", "full").index(t["content"]),
        },
        default_sort="-year",
        filters={
            "slot": slot,
            "year": year,
            "language": language,
            "content": content,
            "provider": provider,
            "person": person,
            "q": params.q,
        },
        empty=empty("empty_no_match") if rows else empty("empty_no_collection"),
        extra={"counts": counts},
    )
    out["items"] = [{**t, "people": len(t["people"])} for t in out["items"]]
    return out


@routes.get("/api/texts/{text_id}", action="people.read")
def get_text(text_id: str, ctx: ProjectDep) -> dict[str, Any]:
    """One text: its parts by provider (a preview each), its people, the records merged
    into it, its versions and the conflicts between finders."""
    found = text_detail(ctx.project, text_id)
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
    found = person_detail(ctx.project, person_id, runtime_of(request).table_cache)
    if found is None:
        raise ApiError.of("person_not_found", person=person_id)
    people, _ = read_people(ctx.project, runtime_of(request).table_cache)
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
        }

    try:
        info = runtime.jobs.submit(
            project=ctx.id,
            jobs_dir=ctx.layout.jobs,
            kind="import",
            work=work,
            title=f"import a {kind}",
        )
    except JobConflict as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise busy_error(exc.running) from exc
    return JSONResponse({"job": info.as_dict()}, status_code=202)
