# SPDX-License-Identifier: MIT
"""The AI clean-up by handoff: export the parts of a handoff, import an answer, accept a proposal."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Path as PathParam
from fastapi import Request, Response
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty
from ..people_io import decided_now
from ..routing import Routes, runtime_of
from .keywords import _decisions, _effective, _write, extracted

routes = Routes(tags=["handoff"])

#: What a bundle holds, and what it never holds (shown before the export).
CONTAINS = (
    "keyword strings and their language",
    "for each: how many people and texts use it, its band and why",
    "the field's title and description",
)
NEVER = ("texts", "people's names or identifiers", "your decisions", "keys")
MAX_TERMS = 5_000
ProposalId = Annotated[str, PathParam(pattern=r"^\d{8}T\d{6}Z-handoff(-\d+)?$")]


class TermRef(BaseModel):
    term: Annotated[str, Field(min_length=1, max_length=300)]
    language: Annotated[str, Field(pattern=r"^([a-z]{2})?$")] = ""


class ExportBody(BaseModel):
    """The terms to send: a band (``check`` by default), or a list; parts of at most
    *max_tokens* (a chat assistant reads a limited amount at once)."""

    band: Literal["kept", "check", "aside"] | None = "check"
    terms: Annotated[list[TermRef], Field(max_length=MAX_TERMS)] | None = None
    lang: Annotated[str | None, Field(pattern=r"^[a-z]{2}$")] = None
    limit: Annotated[int, Field(ge=1, le=MAX_TERMS)] = 1000
    max_tokens: Annotated[int, Field(ge=2_000, le=1_000_000)] = 24_000


def _export(request: Request, body: ExportBody, ctx: Any) -> dict[str, Any]:
    from cartolex.build.records import read_record
    from cartolex.project.handoff import (
        BundleItem,
        cautious_tokens,
        part_files,
        part_record,
        split_items,
    )

    runtime = runtime_of(request)
    rows, run_id = extracted(runtime, ctx)
    if run_id is None:
        raise ApiError.of("no_keywords")
    decisions, _ = _decisions(ctx)
    wanted = {(t.term, t.language) for t in body.terms} if body.terms else None
    corpus = read_record(ctx.layout, "corpus.assemble")
    counts = corpus.measures.counts if corpus else {}
    n_people, n_texts = max(1, counts.get("people", 0)), counts.get("texts", 0)
    items = []
    for row in sorted(rows, key=lambda r: -r["score_len"]):
        view = _effective(row, decisions.get((row["term"], row["language"])))
        key = (row["term"], row["language"])
        if wanted is not None:
            if key not in wanted and (row["term"], "") not in wanted:
                continue
        elif body.band is not None and view["band"] != body.band:
            continue
        if body.lang and row["language"] != body.lang:
            continue
        reason = view["reason"]
        inside = [reason.split(":", 1)[1].strip()] if reason.startswith("part-of:") else []
        items.append(
            BundleItem(
                term=row["term"],
                lang=row["language"],
                band=view["band"],
                reason=reason,
                people=row["people"],
                texts=row["texts"],
                specificity=round(1.0 - row["people"] / n_people, 3),
                forms=row["forms"][:4],
                inside=inside,
            )
        )
        if len(items) >= body.limit:
            break
    identity = ctx.project.config.identity
    common = {"domain": identity.domain_title, "description": identity.domain_description}
    chunks = split_items(
        items, max_tokens=body.max_tokens, n_people=n_people, n_texts=n_texts, **common
    )
    parts = []
    for k, chunk in enumerate(chunks, 1):
        name = f"handoff-{k}" if len(chunks) > 1 else "handoff"
        files = part_files(
            chunk, n_people=n_people, n_texts=n_texts, part=k, parts=len(chunks), **common
        )
        parts.append(
            {
                "name": name,
                "part": k,
                "parts": len(chunks),
                "terms": len(chunk),
                "tokens": cautious_tokens(files["prompt.txt"] + files["terms.txt"]),
                "files": files,
                "bundle": part_record(
                    chunk,
                    name=name,
                    part=k,
                    parts=len(chunks),
                    meta={"run": run_id},
                    **common,
                ),
            }
        )
    return {
        "parts": parts,
        "terms": len(items),
        "contains": list(CONTAINS),
        "never": list(NEVER),
        "empty": None if items else empty("empty_handoff"),
    }


@routes.post("/api/handoff/export", action="keywords.read")
def export(request: Request, body: ExportBody, ctx: ProjectDep) -> dict[str, Any]:
    """The parts of a handoff: for each, the prompt to paste, the terms to attach, the answer's
    format, and ``bundle.json`` (to send back with the answer); what they contain and never do."""
    return _export(request, body, ctx)


@routes.post("/api/handoff/export.zip", action="keywords.read")
def export_zip(request: Request, body: ExportBody, ctx: ProjectDep) -> Response:
    """The same parts as a zip: one folder per part with the files a person uploads."""
    from cartolex.project.handoff import part_zip

    out = _export(request, body, ctx)
    if not out["parts"]:
        raise ApiError.of("handoff_empty")
    data = part_zip({p["name"]: p["files"] for p in out["parts"]})
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="handoff.zip"'},
    )


class ImportBody(BaseModel):
    """The part that was sent (its ``bundle.json``), and the answer as it came back (pasted)."""

    bundle: dict[str, Any]
    answer: Annotated[str, Field(min_length=1, max_length=5_000_000)]


def _ai_folder(ctx: Any) -> Path:
    return ctx.layout.history / "ai"


def _proposal(ctx: Any, proposal_id: str) -> dict[str, Any]:
    from cartolex.project.handoff import CODES, items_of, parse_answer

    folder = _ai_folder(ctx)
    answer = folder / f"{proposal_id}.txt"
    sent = folder / f"{proposal_id}.bundle.json"
    if not answer.is_file() or not sent.is_file():
        raise ApiError.of("proposal_not_found", proposal=proposal_id)
    bundle = items_of(json.loads(sent.read_text(encoding="utf-8")))
    parsed = parse_answer(answer.read_text(encoding="utf-8"), bundle)
    decisions, fp = _decisions(ctx)
    items = []
    for index in sorted(parsed.verdicts):
        item, v = bundle[index], parsed.verdicts[index]
        current = decisions.get((item.term, item.lang))
        items.append(
            {
                "number": index + 1,
                "term": item.term,
                "language": item.lang,
                "code": v.code,
                "meaning": CODES.get(v.code, ""),
                "english": v.canonical,
                "proposed": "keep" if v.accept else "exclude",
                "current": current["decision"] if current else None,
            }
        )
    return {
        "id": proposal_id,
        "items": items,
        "answered": len(items),
        "unanswered": parsed.missing(len(bundle)),
        "read": {
            "lines": parsed.lines,
            "ignored": parsed.ignored,
            "unmatched": parsed.unmatched,
            "renumbered": parsed.renumbered,
            "term_mismatch": parsed.term_mismatch,
            "duplicates": parsed.duplicates,
        },
        "keywords_version": version_of(fp),
    }


@routes.post("/api/handoff/import", action="keywords.write")
def import_answers(request: Request, body: ImportBody, ctx: ProjectDep) -> dict[str, Any]:
    """Keep the answer as it came (``decisions/history/ai/``) and propose decisions.

    Nothing reaches ``keywords.csv`` until the proposal is accepted. The first
    AI answers freeze the project's identity.
    """
    from cartolex.project.files import atomic_write_bytes, json_bytes, utc_stamp
    from cartolex.project.handoff import items_of

    try:
        items_of(body.bundle)
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError.of("invalid_bundle", detail=str(exc)) from exc
    folder = _ai_folder(ctx)
    with ctx.handle.mutex:
        base = f"{utc_stamp()}-handoff"
        proposal_id, n = base, 1
        while (folder / f"{proposal_id}.txt").exists():
            n += 1
            proposal_id = f"{base}-{n}"
        atomic_write_bytes(folder / f"{proposal_id}.bundle.json", json_bytes(body.bundle))
        atomic_write_bytes(folder / f"{proposal_id}.txt", body.answer.encode("utf-8"))
        proposal = _proposal(ctx, proposal_id)
        if proposal["answered"]:
            ctx.project.freeze_identity("first AI answers")
    return proposal


@routes.get("/api/handoff/proposals", action="keywords.read")
def proposals(ctx: ProjectDep) -> dict[str, Any]:
    """The proposals imported so far, the newest first."""
    folder = _ai_folder(ctx)
    names = (
        (p.name[: -len(".txt")] for p in folder.glob("*-handoff*.txt")) if folder.is_dir() else ()
    )
    ids = sorted((i for i in names if re.match(r"^\d{8}T\d{6}Z-handoff(-\d+)?$", i)), reverse=True)
    return {
        "items": [{"id": i, "at": i[:16]} for i in ids],
        "total": len(ids),
        "empty": None if ids else empty("empty_no_proposals"),
    }


@routes.get("/api/handoff/proposals/{proposal_id}", action="keywords.read")
def proposal(proposal_id: ProposalId, ctx: ProjectDep) -> dict[str, Any]:
    """One proposal: each answered term, its verdict and the decision it proposes."""
    return _proposal(ctx, proposal_id)


class AcceptBody(BaseModel):
    """The terms whose proposed decision is accepted (``all``: every one)."""

    terms: Annotated[list[TermRef], Field(max_length=MAX_TERMS)] = []
    all: bool = False


@routes.post("/api/handoff/proposals/{proposal_id}/accept", action="keywords.write")
def accept(
    request: Request,
    response: Response,
    proposal_id: ProposalId,
    body: AcceptBody,
    ctx: ProjectDep,
) -> dict[str, Any]:
    """Write the accepted decisions into ``keywords.csv`` (source ``ai-handoff``); ``If-Match``."""
    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.keywords_csv, expected)
        prop = _proposal(ctx, proposal_id)
        chosen = {(t.term, t.language) for t in body.terms}
        items = [i for i in prop["items"] if body.all or (i["term"], i["language"]) in chosen]
        if not items:
            raise ApiError.of("nothing_chosen")
        rows, _ = _decisions(ctx)
        now = decided_now()
        for i in items:
            rows[(i["term"], i["language"])] = {
                "term": i["term"],
                "language": i["language"],
                "decision": i["proposed"],
                "target": "",
                "reason": f"AI: {i['meaning']}"
                + (
                    f"; English form: {i['english']}" if i["english"] not in ("", i["term"]) else ""
                ),
                "source": "ai-handoff",
                "decided_at": now,
            }
        fp = _write(ctx, rows, expected, f"accept {len(items)} AI answers")
        ctx.project.freeze_identity("first AI answers")
    response.headers["ETag"] = etag_of(fp)
    return {"accepted": len(items), "version": version_of(fp)}
