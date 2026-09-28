# SPDX-License-Identifier: MIT
"""Keywords: the three bands with their reasons, paged on the server; decisions; restore."""

from __future__ import annotations

import csv
from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response
from pydantic import BaseModel, Field

from ..deps import ListDep, ProjectDep, page
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty
from ..people_io import decided_now
from ..routing import Routes, runtime_of

routes = Routes(tags=["keywords"])

Band = Literal["kept", "check", "aside"]
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


def _effective(row: dict[str, Any], decision: dict[str, str] | None) -> dict[str, Any]:
    out = {**row, "extracted_band": row["band"], "decision": None}
    if decision is None:
        return out
    out["decision"] = {
        k: decision[k] for k in ("decision", "target", "reason", "source", "decided_at")
    }
    why = decision["reason"] or "by you"
    if decision["decision"] == "keep":
        out["band"], out["reason"] = "kept", f"kept: {why}"
    elif decision["decision"] == "exclude":
        out["band"], out["reason"] = "aside", f"excluded: {why}"
    else:
        out["band"], out["reason"] = "aside", f"merged into {decision['target']}"
    return out


@routes.get("/api/keywords", action="keywords.read")
def list_keywords(
    request: Request,
    response: Response,
    ctx: ProjectDep,
    params: ListDep,
    band: Annotated[Band | None, Query()] = None,
    lang: Annotated[str | None, Query(pattern=r"^[a-z]{2}$")] = None,
    decision: Annotated[Literal["keep", "exclude", "merge", "none"] | None, Query()] = None,
) -> dict[str, Any]:
    """The candidates in their three bands (kept, to check, set aside) with the reason of each,
    your decisions applied; paged, sorted and filtered here."""
    runtime = runtime_of(request)
    rows, run_id = extracted(runtime, ctx)
    decisions, fp = _decisions(ctx)
    response.headers["ETag"] = etag_of(fp)
    matched: set[tuple[str, str]] = set()
    view = []
    counts: dict[str, int] = {"kept": 0, "check": 0, "aside": 0}
    for row in rows:
        key = (row["term"], row["language"])
        d = decisions.get(key) or decisions.get((row["term"], ""))
        if d is not None:
            matched.add((d["term"], d["language"]))
        item = _effective(row, d)
        counts[item["band"]] = counts.get(item["band"], 0) + 1
        view.append(item)
    orphans = [
        {"term": t, "language": lang_, "decision": d["decision"]}
        for (t, lang_), d in sorted(decisions.items())
        if (t, lang_) not in matched
    ]
    items = [
        v
        for v in view
        if (band is None or v["band"] == band)
        and (lang is None or v["language"] == lang)
        and (
            decision is None
            or (decision == "none" and v["decision"] is None)
            or (v["decision"] is not None and v["decision"]["decision"] == decision)
        )
        and (not params.q or params.q in v["term"].casefold())
    ]
    if run_id is None:
        nothing = empty("empty_no_keywords")
    else:
        nothing = empty("empty_no_match")
    return page(
        items,
        params,
        sorts={
            "score": lambda v: v["score_len"],
            "term": lambda v: v["term"].casefold(),
            "people": lambda v: v["people"],
            "texts": lambda v: v["texts"],
        },
        default_sort="-score",
        filters={"band": band, "lang": lang, "decision": decision, "q": params.q},
        empty=nothing,
        extra={
            "counts": counts,
            "run": run_id,
            "orphans": orphans[:50],
            "orphan_count": len(orphans),
            "version": version_of(fp),
        },
    )


class KeywordDecision(BaseModel):
    term: Term
    language: Lang = ""
    decision: Literal["keep", "exclude", "merge"]
    target: Annotated[str, Field(max_length=300)] = ""
    reason: Annotated[str, Field(max_length=500)] = ""


class DecisionsBody(BaseModel):
    decisions: Annotated[list[KeywordDecision], Field(min_length=1, max_length=MAX_DECISIONS)]


class KeywordRef(BaseModel):
    term: Term
    language: Lang = ""


class RestoreBody(BaseModel):
    keywords: Annotated[list[KeywordRef], Field(min_length=1, max_length=MAX_DECISIONS)]


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
    """Keep, exclude or merge keywords, one or many (send ``If-Match`` of ``keywords.csv``)."""
    expected = expected_version(request)
    languages = set(ctx.project.config.languages.corpus)
    for d in body.decisions:
        if d.language and d.language not in languages:
            raise ApiError.of("not_a_corpus_language", language=d.language)
        if d.decision == "merge" and (not d.target.strip() or d.target == d.term):
            raise ApiError.of("merge_target_missing", term=d.term)
    now = decided_now()
    with ctx.handle.mutex:
        check_version(ctx.layout.keywords_csv, expected)
        rows, _ = _decisions(ctx)
        for d in body.decisions:
            rows[(d.term, d.language)] = {
                "term": d.term,
                "language": d.language,
                "decision": d.decision,
                "target": d.target if d.decision == "merge" else "",
                "reason": d.reason,
                "source": "person",
                "decided_at": now,
            }
        kinds = sorted({d.decision for d in body.decisions})
        fp = _write(ctx, rows, expected, f"{'/'.join(kinds)} {len(body.decisions)} keywords")
        ctx.project.freeze_identity("first curation decision")
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
