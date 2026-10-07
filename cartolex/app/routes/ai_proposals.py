# SPDX-License-Identifier: MIT
"""The AI clean-up's proposals (read, accept) and its route by API.

A proposal is a copilot's triage result (:mod:`.copilot`) or, imported by an
earlier version, an answer to a handoff (a prompt and a list pasted in a
chat): both stay readable and are accepted the same way."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import Path as PathParam
from fastapi import Request, Response
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty, message
from ..people_io import decided_now
from ..routing import Routes, runtime_of
from .keywords import _decisions, _write, extracted

routes = Routes(tags=["ai"])

#: What the triage by API sends, and what it never sends (shown before a run).
API_SENDS = ("keyword strings and their language", "the field's title and description")
API_NEVER = ("texts", "people's names or identifiers", "your decisions", "keys")
MAX_TERMS = 200_000
#: A proposal: a copilot's triage result, or an answer to a handoff imported by an
#: earlier version (still readable, and accepted the same way).
ProposalId = Annotated[str, PathParam(pattern=r"^\d{8}T\d{6}Z-(handoff|copilot-triage)(-\d+)?$")]
_PROPOSAL = re.compile(r"^\d{8}T\d{6}Z-(handoff|copilot-triage)(-\d+)?$")


class TermRef(BaseModel):
    term: Annotated[str, Field(min_length=1, max_length=300)]
    language: Annotated[str, Field(pattern=r"^([a-z]{2})?$")] = ""


def _ai_folder(ctx: Any) -> Path:
    return ctx.layout.history / "ai"


def _proposal(ctx: Any, proposal_id: str) -> dict[str, Any]:
    from cartolex.lexicon.categories import category_of
    from cartolex.project.handoff import CODES, items_of, parse_answer

    if "-copilot-" in proposal_id:
        from .copilot import triage_proposal

        return triage_proposal(ctx, proposal_id)
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
        joins = bool(v.canonical) and v.canonical.casefold() != item.term.casefold()
        items.append(
            {
                "number": index + 1,
                "term": item.term,
                "language": item.lang,
                "code": v.code,
                "category": category_of(v.code),
                "confidence": v.confidence,
                "meaning": CODES.get(v.code, ""),
                "english": v.canonical,
                # An accepted term whose English form is another term joins it (its
                # translation, or its usual English spelling): a merge.
                "proposed": ("merge" if joins else "keep") if v.accept else "exclude",
                "target": v.canonical if v.accept and joins else "",
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


def _proposal_ids(ctx: Any) -> list[str]:
    folder = _ai_folder(ctx)
    if not folder.is_dir():
        return []
    names = {p.name.split(".", 1)[0] for p in folder.iterdir() if p.suffix in (".txt", ".json")}
    return sorted((i for i in names if _PROPOSAL.match(i)), reverse=True)


@routes.get("/api/ai/proposals", action="keywords.read")
def proposals(ctx: ProjectDep) -> dict[str, Any]:
    """The proposals imported so far (a copilot's results, and the answers to a handoff an
    earlier version imported), the newest first."""
    ids = _proposal_ids(ctx)
    return {
        "items": [{"id": i, "at": i[:16]} for i in ids],
        "total": len(ids),
        "empty": None if ids else empty("empty_no_proposals"),
    }


@routes.get("/api/ai/proposals/{proposal_id}", action="keywords.read")
def proposal(proposal_id: ProposalId, ctx: ProjectDep) -> dict[str, Any]:
    """One proposal: each answered term, its verdict and the decision it proposes."""
    return _proposal(ctx, proposal_id)


class AcceptBody(BaseModel):
    """The terms whose proposed decision is accepted (``all``: every one)."""

    terms: Annotated[list[TermRef], Field(max_length=MAX_TERMS)] = []
    all: bool = False


@routes.post("/api/ai/proposals/{proposal_id}/accept", action="keywords.write")
def accept(
    request: Request,
    response: Response,
    proposal_id: ProposalId,
    body: AcceptBody,
    ctx: ProjectDep,
) -> dict[str, Any]:
    """Write the accepted decisions into ``keywords.csv`` (source ``ai-handoff``, or
    ``ai-copilot`` for a copilot's result); ``If-Match``. A copilot's result also sets the
    build's route for the clean-up to the copilot when it was « No AI »
    (:func:`cartolex.app.ai_steps.route_copilot_triage`): ``route``, the route after it,
    and ``note``, ``accepted_route_copilot`` when it changed (else ``null``)."""
    from ..ai_steps import route_copilot_triage, routes_of

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
        source = "ai-copilot" if "-copilot-" in proposal_id else "ai-handoff"
        for i in items:
            rows[(i["term"], i["language"])] = {
                "term": i["term"],
                "language": i["language"],
                "decision": i["proposed"],
                "target": i["target"],
                "reason": f"AI: {i.get('reason') or i['meaning']}"[:500]
                + (
                    f"; English form: {i['english']}" if i["english"] not in ("", i["term"]) else ""
                ),
                "source": source,
                "decided_at": now,
                "category": i.get("category") or "",
            }
        fp = _write(ctx, rows, expected, f"accept {len(items)} AI answers")
        ctx.project.freeze_identity("first AI answers")
        feed_rejects(request, ctx, items, source)
        switched = source == "ai-copilot" and route_copilot_triage(ctx.project)
        route = routes_of(ctx.project.read_params()[0])["keywords.triage"]
    response.headers["ETag"] = etag_of(fp)
    return {
        "accepted": len(items),
        "version": version_of(fp),
        "route": route,
        "note": message("accepted_route_copilot") if switched else None,
    }


def feed_rejects(request: Request, ctx: Any, items: list[dict[str, Any]], route: str) -> int:
    """Put the accepted ``never`` exclusions the judge was sure of into the machine's
    rejection cache."""
    from cartolex.build.engine import project_fingerprint

    from .keywords import machine_rejects

    machine = machine_rejects(runtime_of(request))
    rows = [
        {"term": i["term"], "language": i["language"]}
        for i in items
        if i.get("category") == "never"
        and i.get("confidence") == "sure"
        and i["proposed"] == "exclude"
        and i["language"]
    ]
    if machine is None or not rows:
        return 0
    return machine.add(rows, route=route, project=project_fingerprint(ctx.project))


@routes.get("/api/keywords/ai", action="keywords.read")
def ai_routes(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """The two routes of the AI filtering: with a copilot (no key; the proposals imported so
    far) and by API (whether a key and a provider are set, what it sends, and an estimate of
    the calls and tokens of a run), with what the last run by API decided."""

    from ..ai_usage import recorded_usage, triage_estimate
    from .keywords import api_verdicts

    runtime = runtime_of(request)
    _, run_id = extracted(runtime, ctx)
    # The API route judges every candidate but those rejected automatically, one per
    # distinct term; the answers already paid for (cache/ai/) cost nothing.
    estimate = triage_estimate(runtime, ctx) or {
        "terms": 0, "new": 0, "answered": 0, "rejected": 0, "calls": 0,
        "tokens_in": 0, "tokens_out": 0, "upper_bound": True,
    }  # fmt: skip
    identity = ctx.project.config.identity.ai
    ai = runtime.ai_access()
    key = bool(ai is not None and (ai.api_key or ai.client_factory))
    _, triage = api_verdicts(runtime, ctx)
    return {
        "run": run_id,
        "copilot": {"proposals": len(_proposal_ids(ctx))},
        "api": {
            "provider": identity.provider if identity else None,
            "model": identity.model if identity else None,
            "key": key,
            "ready": key and identity is not None,
            "stage": "keywords.triage",
            "sends": list(API_SENDS),
            "never": list(API_NEVER),
            "estimate": estimate,
            "last": None
            if triage is None
            else {"run": triage.run_id, "at": triage.finished_at, **triage.measures.counts},
            # the tokens the provider reported: the last run and every run of the project
            "usage": recorded_usage(ctx.layout),
        },
    }


class RunBody(BaseModel):
    """``consent``: the person read what leaves the computer and what it costs."""

    consent: bool = False


@routes.post("/api/keywords/ai/run", action="build.start")
def run_api(request: Request, body: RunBody, ctx: ProjectDep) -> Any:
    """Filter the keywords by API: switch the AI clean-up on (``params.json``) and start it
    as a build job of ``keywords.triage`` (202); refused without a key, a provider or
    consent."""
    from fastapi.responses import JSONResponse

    from .build import start_build_job

    runtime = runtime_of(request)
    identity = ctx.project.config.identity.ai
    ai = runtime.ai_access()
    if identity is None or ai is None or not (ai.api_key or ai.client_factory):
        raise ApiError.of("ai_api_not_ready")
    if not body.consent:
        raise ApiError.of("ai_consent_needed", provider=identity.provider)
    with ctx.handle.mutex:
        params, fp = ctx.project.read_params()
        stage = params.stages.get("keywords.triage", {})
        if stage.get("enabled") is not True:
            stages = {**params.stages, "keywords.triage": {**stage, "enabled": True}}
            ctx.project.save_params(
                params.model_copy(update={"stages": stages}),
                expected=fp,
                action="switch the AI clean-up on",
            )
    started = start_build_job(
        runtime, ctx, ["keywords.triage"], consent=["keywords.triage"], title="AI filtering"
    )
    return JSONResponse(started, status_code=202)
