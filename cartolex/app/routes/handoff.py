# SPDX-License-Identifier: MIT
"""The AI clean-up by handoff: export a bundle of terms, import the answers, accept a proposal."""

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
from ..etags import etag_of, expected_version, version_of
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
    """The terms to send: a band (``check`` by default), or a list."""

    band: Literal["kept", "check", "aside"] | None = "check"
    terms: Annotated[list[TermRef], Field(max_length=MAX_TERMS)] | None = None
    lang: Annotated[str | None, Field(pattern=r"^[a-z]{2}$")] = None
    limit: Annotated[int, Field(ge=1, le=MAX_TERMS)] = 1000


@routes.post("/api/handoff/export", action="keywords.read")
def export(request: Request, body: ExportBody, ctx: ProjectDep) -> dict[str, Any]:
    """A bundle of terms with their evidence, and its text with the instructions to paste."""
    from cartolex.build.records import read_record
    from cartolex.project.handoff import Bundle, BundleItem

    runtime = runtime_of(request)
    rows, run_id = extracted(runtime, ctx)
    if run_id is None:
        raise ApiError(409, "no_keywords", "build the keywords first", next_action="build")
    decisions, _ = _decisions(ctx)
    wanted = {(t.term, t.language) for t in body.terms} if body.terms else None
    corpus = read_record(ctx.layout, "corpus.assemble")
    n_people = max(1, (corpus.measures.counts.get("people", 0) if corpus else 0) or 1)
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
        inside = (
            [view["reason"].split(":", 1)[1].strip()]
            if view["reason"].startswith("part-of:")
            else []
        )
        items.append(
            BundleItem(
                term=row["term"],
                lang=row["language"],
                band=view["band"],
                reason=view["reason"],
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
    bundle = Bundle(identity.domain_title, identity.domain_description, items)
    return {
        "bundle": bundle.as_dict(),
        "text": bundle.to_text(),
        "terms": len(items),
        "contains": list(CONTAINS),
        "never": list(NEVER),
        "empty": None
        if items
        else {
            "message": "no term to send in this band",
            "next": {"label": "Close", "action": "none"},
        },
    }


class ImportBody(BaseModel):
    """The bundle that was sent, and the answers as they came back (pasted)."""

    bundle: dict[str, Any]
    answer: Annotated[str, Field(min_length=1, max_length=5_000_000)]


def _ai_folder(ctx: Any) -> Path:
    return ctx.layout.history / "ai"


def _proposal(ctx: Any, proposal_id: str) -> dict[str, Any]:
    from cartolex.project.handoff import CODES, Bundle, parse_answers

    folder = _ai_folder(ctx)
    answer = folder / f"{proposal_id}.txt"
    sent = folder / f"{proposal_id}.bundle.json"
    if not answer.is_file() or not sent.is_file():
        raise ApiError(404, "not_found", f"no proposal {proposal_id}", next_action="reload")
    bundle = Bundle.from_dict(json.loads(sent.read_text(encoding="utf-8")))
    verdicts = parse_answers(answer.read_text(encoding="utf-8"), [i.term for i in bundle.items])
    decisions, fp = _decisions(ctx)
    items = []
    for item in bundle.items:
        v = verdicts.get(item.term)
        if v is None:
            continue
        current = decisions.get((item.term, item.lang))
        items.append(
            {
                "term": item.term,
                "language": item.lang,
                "code": v.code,
                "meaning": CODES.get(v.code, ""),
                "canonical": v.canonical,
                "proposed": "keep" if v.accept else "exclude",
                "current": current["decision"] if current else None,
            }
        )
    return {
        "id": proposal_id,
        "items": items,
        "answered": len(items),
        "unanswered": len(bundle.items) - len(items),
        "keywords_version": version_of(fp),
    }


@routes.post("/api/handoff/import", action="keywords.write")
def import_answers(request: Request, body: ImportBody, ctx: ProjectDep) -> dict[str, Any]:
    """Keep the answers as they came (``decisions/history/ai/``) and propose decisions.

    Nothing reaches ``keywords.csv`` until the proposal is accepted. The first
    AI answers freeze the project's identity.
    """
    from cartolex.project.files import atomic_write_bytes, utc_stamp
    from cartolex.project.handoff import Bundle

    try:
        bundle = Bundle.from_dict(body.bundle)
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError(
            422, "invalid", f"the bundle is not valid: {exc}", next_action="fix-input"
        ) from exc
    folder = _ai_folder(ctx)
    with ctx.handle.mutex:
        base = f"{utc_stamp()}-handoff"
        proposal_id, n = base, 1
        while (folder / f"{proposal_id}.txt").exists():
            n += 1
            proposal_id = f"{base}-{n}"
        atomic_write_bytes(folder / f"{proposal_id}.bundle.json", bundle.to_json().encode("utf-8"))
        atomic_write_bytes(folder / f"{proposal_id}.txt", body.answer.encode("utf-8"))
        proposal = _proposal(ctx, proposal_id)
        if proposal["answered"]:
            ctx.project.freeze_identity("first AI answers")
    return proposal


@routes.get("/api/handoff/proposals", action="keywords.read")
def proposals(ctx: ProjectDep) -> dict[str, Any]:
    """The proposals imported so far, the newest first."""
    folder = _ai_folder(ctx)
    ids = sorted(
        (p.name[: -len(".txt")] for p in folder.glob("*-handoff*.txt")) if folder.is_dir() else (),
        reverse=True,
    )
    ids = [i for i in ids if re.match(r"^\d{8}T\d{6}Z-handoff(-\d+)?$", i)]
    return {
        "items": [{"id": i, "at": i[:16]} for i in ids],
        "total": len(ids),
        "empty": None
        if ids
        else {
            "message": "no AI answers imported yet",
            "next": {"label": "Export terms", "action": "none"},
        },
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
        prop = _proposal(ctx, proposal_id)
        chosen = {(t.term, t.language) for t in body.terms}
        items = [i for i in prop["items"] if body.all or (i["term"], i["language"]) in chosen]
        if not items:
            raise ApiError(422, "invalid", "choose the terms to accept", next_action="fix-input")
        rows, _ = _decisions(ctx)
        now = decided_now()
        for i in items:
            rows[(i["term"], i["language"])] = {
                "term": i["term"],
                "language": i["language"],
                "decision": i["proposed"],
                "target": "",
                "reason": f"AI: {i['meaning']}",
                "source": "ai-handoff",
                "decided_at": now,
            }
        fp = _write(ctx, rows, expected, f"accept {len(items)} AI answers")
        ctx.project.freeze_identity("first AI answers")
    response.headers["ETag"] = etag_of(fp)
    return {"accepted": len(items), "version": version_of(fp)}
