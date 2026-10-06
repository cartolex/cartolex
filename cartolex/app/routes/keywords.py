# SPDX-License-Identifier: MIT
"""Keywords: the bands with their reasons and categories, paged on the server; decisions; restore.

The ``rejected`` band holds the candidates the rejection lists banned (cartolex's
list, or an AI's ``never`` answer in an earlier project on this computer): a
person's decision on one of them, a *put back* (keep) above all, removes it from
the machine's cache.
"""

from __future__ import annotations

import csv
from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from pydantic import BaseModel, Field

from ..ai_steps import AI_SOURCES as _AI_SOURCES
from ..deps import ListDep, ProjectDep, page
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty
from ..people_io import decided_now
from ..routing import Routes, runtime_of

routes = Routes(tags=["keywords"])

Band = Literal["kept", "check", "aside", "rejected"]
Category = Literal["concept", "method", "object", "place", "field", "never", "here"]
Term = Annotated[str, Field(min_length=1, max_length=300)]
Lang = Annotated[str, Field(pattern=r"^([a-z]{2})?$")]
#: The most decisions one request carries.
MAX_DECISIONS = 20_000


def extracted(runtime: Any, ctx: Any) -> tuple[list[dict[str, Any]], str | None]:
    """The candidates of the current extraction, every language (cached by its run id)."""
    from cartolex.build.records import read_record

    record = read_record(ctx.layout, "keywords.extract")
    if record is None:
        return [], None
    folder = ctx.layout.stage("keywords.extract")

    def load() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(folder.glob("raw_keywords_*.csv")):
            lang = path.stem.removeprefix("raw_keywords_")
            with open(path, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    rows.append(
                        {
                            "term": r["term"],
                            "language": lang,
                            "score": float(r.get("score") or 0),
                            "score_len": float(r.get("score_len") or 0),
                            "people": int(float(r.get("people") or 0)),
                            "texts": int(float(r.get("texts") or 0)),
                            "forms": [f for f in (r.get("forms") or "").split("|") if f],
                            "band": r.get("band") or "kept",
                            "reason": r.get("reason") or "",
                        }
                    )
        return rows

    rows = runtime.table_cache.get(("keywords", ctx.id, record.run_id), load)
    return rows, record.run_id


def _decisions(ctx: Any) -> tuple[dict[tuple[str, str], dict[str, str]], str | None]:
    from cartolex.project.files import fingerprint
    from cartolex.project.tables import read_decision_csv

    rows = read_decision_csv(ctx.layout.keywords_csv, "keywords")
    return {(r["term"], r["language"]): r for r in rows}, fingerprint(ctx.layout.keywords_csv)


def api_verdicts(runtime: Any, ctx: Any) -> tuple[dict[str, dict[str, str]], Any]:
    """The AI clean-up's verdicts by API (``keywords.triage``), by lower-case term; its record."""
    import json

    from cartolex.build.records import read_record

    record = read_record(ctx.layout, "keywords.triage")
    if record is None:
        return {}, None
    path = ctx.layout.stage("keywords.triage") / "llm_decisions.json"

    def load() -> dict[str, dict[str, str]]:
        try:
            typed = json.loads(path.read_text(encoding="utf-8")).get("typed", {})
        except (OSError, ValueError):
            return {}
        return {str(t).casefold(): d for t, d in typed.items() if isinstance(d, dict)}

    return runtime.table_cache.get(("triage", ctx.id, record.run_id), load), record


#: Which route decided a keyword: you, an AI in a browser (handoff), an AI copilot that
#: runs code, an AI by API, or nobody yet (the extraction's band).
ROUTES = ("person", "ai-handoff", "ai-copilot", "ai-api", "extraction")
#: The sources of ``keywords.csv`` that are an AI's answers the person accepted.
AI_SOURCES = _AI_SOURCES


def machine_rejects(runtime: Any) -> Any:
    """The rejection cache of this computer, or ``None`` (a hosted service)."""
    from cartolex.lexicon.rejects import MachineRejects

    folder = getattr(runtime, "rejects_folder", None)
    return None if folder is None else MachineRejects(folder)


def _effective(
    row: dict[str, Any], decision: dict[str, str] | None, verdict: dict[str, str] | None = None
) -> dict[str, Any]:
    from cartolex.lexicon.categories import category_of
    from cartolex.project.handoff import ACCEPT_CODES, CODES

    out = {**row, "extracted_band": row["band"], "decision": None, "ai": None, "category": None}
    if verdict is not None:
        code = str(verdict.get("verdict", ""))
        out["ai"] = {"route": "api", "code": code, "english": verdict.get("canonical_en") or ""}
        out["category"] = verdict.get("category") or category_of(code) or None
    if decision is not None and decision.get("category"):
        out["category"] = decision["category"]
    if decision is None:
        if verdict is not None and out["ai"]["code"] in CODES:
            code = out["ai"]["code"]
            out["band"] = "kept" if code in ACCEPT_CODES else "aside"
            out["reason"] = f"AI: {CODES[code]}"
            out["route"] = "ai-api"
        else:
            out["route"] = "extraction"
        return out
    out["decision"] = {
        k: decision.get(k, "")
        for k in ("decision", "target", "reason", "source", "decided_at", "category")
    }
    out["route"] = decision["source"] if decision["source"] in AI_SOURCES else "person"
    why = decision["reason"] or "by you"
    if decision["decision"] == "keep":
        out["band"], out["reason"] = "kept", f"kept: {why}"
    elif decision["decision"] == "exclude":
        out["band"], out["reason"] = "aside", f"excluded: {why}"
    else:
        out["band"], out["reason"] = "aside", f"merged into {decision['target']}"
    return out


def languages_split(ctx: Any, decisions: dict, triage: Any) -> dict[str, Any] | None:
    """Several corpus languages and no AI clean-up (by API or by handoff): a warning."""
    from .overview import item

    langs = list(ctx.project.config.languages.corpus)
    if len(langs) < 2 or triage is not None:
        return None
    if any(d.get("source") in AI_SOURCES for d in decisions.values()):
        return None
    return item("health_languages_split", level="warning", languages=langs)


class Where(BaseModel):
    """The list's filters, for a change applied to every keyword they keep."""

    band: Band | None = None
    lang: Annotated[str | None, Field(pattern=r"^[a-z]{2}$")] = None
    category: Category | Literal["none"] | None = None
    decision: Literal["keep", "exclude", "merge", "none"] | None = None
    route: Literal["person", "ai-handoff", "ai-copilot", "ai-api", "extraction"] | None = None
    q: Annotated[str, Field(max_length=300)] = ""


def keyword_view(runtime: Any, ctx: Any, where: Where) -> dict[str, Any]:
    """Every candidate with its effective band and route, the counts, and those *where* keeps."""
    rows, run_id = extracted(runtime, ctx)
    decisions, fp = _decisions(ctx)
    verdicts, triage = api_verdicts(runtime, ctx)
    matched: set[tuple[str, str]] = set()
    view = []
    counts: dict[str, int] = {"kept": 0, "check": 0, "aside": 0, "rejected": 0}
    routes_: dict[str, int] = dict.fromkeys(ROUTES, 0)
    categories: dict[str, int] = {}
    by_lang: dict[str, int] = {}
    for row in rows:
        key = (row["term"], row["language"])
        d = decisions.get(key) or decisions.get((row["term"], ""))
        if d is not None:
            matched.add((d["term"], d["language"]))
        item = _effective(row, d, verdicts.get(row["term"].casefold()))
        counts[item["band"]] = counts.get(item["band"], 0) + 1
        routes_[item["route"]] += 1
        by_lang[item["language"]] = by_lang.get(item["language"], 0) + 1
        cat = item["category"] or "none"
        categories[cat] = categories.get(cat, 0) + 1
        view.append(item)
    orphans = [
        {"term": t, "language": lang_, "decision": d["decision"]}
        for (t, lang_), d in sorted(decisions.items())
        if (t, lang_) not in matched
    ]
    q = where.q.strip().casefold()

    def hit(v: dict[str, Any]) -> bool:
        return q in v["term"].casefold() or any(q in f.casefold() for f in v["forms"])

    items = [
        v
        for v in view
        if (where.band is None or v["band"] == where.band)
        and (where.lang is None or v["language"] == where.lang)
        and (where.route is None or v["route"] == where.route)
        and (where.category is None or (v["category"] or "none") == where.category)
        and (
            where.decision is None
            or (where.decision == "none" and v["decision"] is None)
            or (v["decision"] is not None and v["decision"]["decision"] == where.decision)
        )
        and (not q or hit(v))
    ]
    return {
        "rows": rows,
        "run": run_id,
        "decisions": decisions,
        "fp": fp,
        "triage": triage,
        "counts": counts,
        "routes": routes_,
        "categories": categories,
        "languages": by_lang,
        "orphans": orphans,
        "items": items,
    }


@routes.get("/api/keywords", action="keywords.read")
def list_keywords(
    request: Request,
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    band: Annotated[Band | None, Query()] = None,
    lang: Annotated[str | None, Query(pattern=r"^[a-z]{2}$")] = None,
    decision: Annotated[Literal["keep", "exclude", "merge", "none"] | None, Query()] = None,
    route: Annotated[
        Literal["person", "ai-handoff", "ai-copilot", "ai-api", "extraction"] | None, Query()
    ] = None,
    category: Annotated[Category | Literal["none"] | None, Query()] = None,
) -> dict[str, Any]:
    """The candidates in their bands (kept, to check, set aside, rejected automatically) with
    the reason and the category of each, your decisions and the AI's verdicts by API applied,
    and the route that decided each; paged, sorted and filtered here."""
    runtime = runtime_of(request)
    v = keyword_view(
        runtime,
        ctx,
        Where(band=band, lang=lang, decision=decision, route=route, category=category, q=params.q),
    )
    run_id, decisions, fp, triage = v["run"], v["decisions"], v["fp"], v["triage"]
    counts, routes_, by_lang, orphans, items = (
        v["counts"],
        v["routes"],
        v["languages"],
        v["orphans"],
        v["items"],
    )
    response.headers["ETag"] = etag_of(fp)
    if run_id is None:
        nothing = empty("empty_no_keywords")
    else:
        nothing = empty("empty_no_match")
    record = None
    if run_id is not None:
        from cartolex.build.records import read_record

        record = read_record(ctx.layout, "keywords.extract")
    param = record.parameters.get("counting_unit") if record else None
    unit = str(getattr(param, "value", param) or "person") if record else None
    return page(
        items,
        params,
        sorts={
            "score": lambda v: v["score_len"],
            "term": lambda v: v["term"].casefold(),
            "people": lambda v: v["people"],
            "texts": lambda v: v["texts"],
            "language": lambda v: (v["language"], -v["score_len"]),
        },
        default_sort="-score",
        filters={
            "band": band,
            "lang": lang,
            "decision": decision,
            "route": route,
            "category": category,
            "q": params.q,
        },
        empty=nothing,
        extra={
            "counts": counts,
            "routes": routes_,
            "categories": v["categories"],
            "languages": by_lang,
            "corpus_languages": list(ctx.project.config.languages.corpus),
            "counting_unit": unit,
            "triage": None
            if triage is None
            else {"run": triage.run_id, "at": triage.finished_at, **triage.measures.counts},
            "warning": languages_split(ctx, decisions, triage) if run_id else None,
            "run": run_id,
            "orphans": orphans[:50],
            "orphan_count": len(orphans),
            "version": version_of(fp),
        },
    )


@routes.get("/api/keywords/decisions", action="keywords.read")
def list_decisions(
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    decision: Annotated[Literal["keep", "exclude", "merge"] | None, Query()] = None,
    source: Annotated[str | None, Query(pattern=r"^[a-z-]{1,40}$")] = None,
) -> dict[str, Any]:
    """Your decisions (``keywords.csv``), the latest first: the history of the curation, each
    one restorable (``POST /api/keywords/restore``)."""
    decisions, fp = _decisions(ctx)
    response.headers["ETag"] = etag_of(fp)
    counts: dict[str, int] = {}
    for d in decisions.values():
        counts[d["decision"]] = counts.get(d["decision"], 0) + 1
    items = [
        {
            k: d.get(k, "")
            for k in (
                "term",
                "language",
                "decision",
                "target",
                "reason",
                "source",
                "decided_at",
                "category",
            )
        }
        for d in decisions.values()
        if (decision is None or d["decision"] == decision)
        and (source is None or d["source"] == source)
        and (not params.q or params.q in d["term"].casefold())
    ]
    return page(
        items,
        params,
        sorts={
            "decided_at": lambda v: (v["decided_at"], v["term"]),
            "term": lambda v: v["term"].casefold(),
        },
        default_sort="-decided_at",
        filters={"decision": decision, "source": source, "q": params.q},
        empty=empty("empty_no_decisions"),
        extra={"counts": counts, "version": version_of(fp)},
    )


class KeywordDecision(BaseModel):
    term: Term
    language: Lang = ""
    decision: Literal["keep", "exclude", "merge"]
    target: Annotated[str, Field(max_length=300)] = ""
    reason: Annotated[str, Field(max_length=500)] = ""
    #: The keyword's category; empty: the one it had (an AI's), or none.
    category: Category | Literal[""] = ""


class DecisionsBody(BaseModel):
    decisions: Annotated[list[KeywordDecision], Field(min_length=1, max_length=MAX_DECISIONS)]


def _kept_category(before: dict[str, str], decision: str) -> str:
    """The category a new decision keeps from the row it replaces: an accepted one for a keep
    or a merge, a rejected one for an exclusion."""
    from cartolex.lexicon.categories import ACCEPTED, REJECTED

    category = before.get("category") or ""
    allowed = REJECTED if decision == "exclude" else ACCEPTED
    return category if category in allowed else ""


class KeywordRef(BaseModel):
    term: Term
    language: Lang = ""


class RestoreBody(BaseModel):
    keywords: Annotated[list[KeywordRef], Field(min_length=1, max_length=MAX_DECISIONS)]


def spare(runtime: Any, terms: list[tuple[str, str]]) -> int:
    """A person decided on *terms* (term, language): they leave the machine's rejection cache."""
    machine = machine_rejects(runtime)
    if machine is None:
        return 0
    by_lang: dict[str, list[str]] = {}
    for term, lang in terms:
        for code in [lang] if lang else machine.languages():
            by_lang.setdefault(code, []).append(term)
    return sum(machine.remove(lang, ts) for lang, ts in by_lang.items())


def _write(
    ctx: Any, rows: dict[tuple[str, str], dict[str, str]], expected: str | None, action: str
) -> str:
    from cartolex.project.files import write_decision
    from cartolex.project.tables import decision_csv_bytes

    return write_decision(
        ctx.layout,
        ctx.layout.keywords_csv,
        decision_csv_bytes("keywords", list(rows.values())),
        expected=expected,
        action=action,
    )


@routes.post("/api/keywords/decisions", action="keywords.write")
def decide(
    request: Request, response: Response, body: DecisionsBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Keep, exclude or merge keywords, one or many (send ``If-Match`` of ``keywords.csv``).

    Keeping or merging a keyword takes it out of the machine's rejection cache (a put back
    from the ``rejected`` band); a category, when given, is stored with the decision."""
    expected = expected_version(request)
    languages = set(ctx.project.config.languages.corpus)
    for d in body.decisions:
        if d.language and d.language not in languages:
            raise ApiError.of("not_a_corpus_language", language=d.language)
        if d.decision == "merge" and (not d.target.strip() or d.target == d.term):
            raise ApiError.of("merge_target_missing", term=d.term)
        if d.category and not _kept_category({"category": d.category}, d.decision):
            from cartolex.lexicon.categories import ACCEPTED, REJECTED

            allowed = REJECTED if d.decision == "exclude" else ACCEPTED
            raise ApiError.of(
                "keyword_category_mismatch",
                term=d.term,
                decision=d.decision,
                allowed=", ".join(allowed),
            )
    now = decided_now()
    with ctx.handle.mutex:
        check_version(ctx.layout.keywords_csv, expected)
        rows, _ = _decisions(ctx)
        for d in body.decisions:
            before = rows.get((d.term, d.language)) or {}
            rows[(d.term, d.language)] = {
                "term": d.term,
                "language": d.language,
                "decision": d.decision,
                "target": d.target if d.decision == "merge" else "",
                "reason": d.reason,
                "source": "person",
                "decided_at": now,
                "category": d.category or _kept_category(before, d.decision),
            }
        kinds = sorted({d.decision for d in body.decisions})
        fp = _write(ctx, rows, expected, f"{'/'.join(kinds)} {len(body.decisions)} keywords")
        ctx.project.freeze_identity("first curation decision")
        spare(
            runtime_of(request),
            [(d.term, d.language) for d in body.decisions if d.decision != "exclude"],
        )
    response.headers["ETag"] = etag_of(fp)
    return {"decided": len(body.decisions), "version": version_of(fp)}


@routes.post("/api/keywords/restore", action="keywords.write")
def restore(
    request: Request, response: Response, body: RestoreBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Undo decisions (restore an excluded keyword to its band); send ``If-Match``."""
    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.keywords_csv, expected)
        rows, _ = _decisions(ctx)
        gone = [(k.term, k.language) for k in body.keywords if (k.term, k.language) in rows]
        if not gone:
            raise ApiError.of("no_decision")
        for key in gone:
            del rows[key]
        fp = _write(ctx, rows, expected, f"restore {len(gone)} keywords")
    response.headers["ETag"] = etag_of(fp)
    return {"restored": len(gone), "version": version_of(fp)}


class WhereBody(BaseModel):
    """Keep or exclude every keyword the list's filters keep (at most :data:`MAX_DECISIONS`)."""

    where: Where
    decision: Literal["keep", "exclude"]
    reason: Annotated[str, Field(max_length=500)] = ""


@routes.post("/api/keywords/decisions/where", action="keywords.write")
def decide_where(
    request: Request, response: Response, body: WhereBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Keep or exclude every keyword the filters keep (send ``If-Match``)."""
    expected = expected_version(request)
    now = decided_now()
    with ctx.handle.mutex:
        check_version(ctx.layout.keywords_csv, expected)
        items = keyword_view(runtime_of(request), ctx, body.where)["items"]
        if not items:
            raise ApiError.of("nothing_chosen")
        if len(items) > MAX_DECISIONS:
            raise ApiError.of("too_many_decisions", n=len(items), max=MAX_DECISIONS)
        rows, _ = _decisions(ctx)
        for v in items:
            before = rows.get((v["term"], v["language"])) or {}
            rows[(v["term"], v["language"])] = {
                "term": v["term"],
                "language": v["language"],
                "decision": body.decision,
                "target": "",
                "reason": body.reason,
                "source": "person",
                "decided_at": now,
                "category": _kept_category(before, body.decision),
            }
        fp = _write(ctx, rows, expected, f"{body.decision} {len(items)} keywords")
        ctx.project.freeze_identity("first curation decision")
        if body.decision == "keep":
            spare(runtime_of(request), [(v["term"], v["language"]) for v in items])
    response.headers["ETag"] = etag_of(fp)
    return {"decided": len(items), "version": version_of(fp)}
