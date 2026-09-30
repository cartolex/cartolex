# SPDX-License-Identifier: MIT
"""The AI copilot: export a self-sufficient bundle for an assistant that runs code, import its result.

Two tasks, each with a summary (what the bundle holds and never holds, and
its counts), a download, and an import that turns the result into the
proposal the curator already reviews for a handoff:

- **themes** — the saved tree (else the grouping's proposal) with the space's
  vectors; the result's changes become operations to accept one by one in the
  theme editor (``POST /api/themes/ops``), then saved as a version;
- **triage** — the candidates an AI judges (kept, to check and set aside; or
  kept and to check, or to check only) with their evidence; the result's decisions become a keyword proposal, accepted with
  ``POST /api/ai/proposals/{id}/accept``.

The result is kept as it came in ``decisions/history/ai/``; the first
answers freeze the project's identity. The bundle's format and the kit are in
:mod:`cartolex.copilot`, the assembling in :mod:`cartolex.project.copilot`.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import Path as PathParam
from fastapi import Query, Request, Response
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..messages import empty
from ..routing import Routes, runtime_of

routes = Routes(tags=["copilot"])

Language = Annotated[str, Query(pattern=r"^[a-z]{2}$")]
MAX_TERMS = 20_000
#: The tokens one assistant conversation holds comfortably (the kit's ``budget()`` too).
CONVERSATION_TOKENS = 120_000
MAX_PARTS = 12
Parts = Annotated[int, Query(ge=0, le=MAX_PARTS)]
CopilotId = Annotated[str, PathParam(pattern=r"^\d{8}T\d{6}Z-copilot-themes(-\d+)?$")]
#: The verb of each kind of change, as the review names it.
VERBS = {
    "rename": "RENAME",
    "move": "MOVE",
    "merge": "MERGE",
    "split": "SPLIT",
    "create": "CREATE",
    "delete": "DELETE",
    "move_node": "MOVE_NODE",
    "set_aside": "SET_ASIDE",
    "put_back": "PUT_BACK",
    "attribution": "ATTRIBUTION",
    "restructure": "RESTRUCTURE",
}


def _folder(ctx: Any) -> Any:
    return ctx.layout.history / "ai"


def _names(ctx: Any) -> Any:
    from cartolex.project.copilot import NameMask, roster_names

    return NameMask(roster_names(ctx.layout.stage("corpus.assemble")))


def _context(ctx: Any, task: str, **extra: Any) -> dict[str, Any]:
    config = ctx.project.config
    return {
        "domain": config.identity.domain_title,
        "description": config.identity.domain_description,
        "reference_language": config.languages.reference,
        "corpus_languages": list(config.languages.corpus),
        "display_languages": list(getattr(config.languages, "display", []) or []),
        "standing_rules": read_curation(ctx).rules_for(task),
        "curation_notes": read_curation(ctx).notes,
        **extra,
    }


def read_curation(ctx: Any) -> Any:
    """The curator's notes and standing rules (``decisions/curation-notes.md``)."""
    from cartolex.project.curation import parse

    path = ctx.layout.curation_notes_md
    return parse(path.read_text(encoding="utf-8") if path.is_file() else "")


def _keep_rules(ctx: Any, task: str, rules: list[Any], source: str) -> int:
    """Add a result's standing rules to the curator's notes (the new ones only); how many.
    The caller holds the project's mutex."""
    from cartolex.project.curation import render
    from cartolex.project.files import fingerprint, write_decision

    path = ctx.layout.curation_notes_md
    doc, added = read_curation(ctx).with_rules(task, rules)
    if added:
        write_decision(
            ctx.layout,
            path,
            render(doc).encode("utf-8"),
            expected=fingerprint(path),
            action=f"add {added} standing rules from {source}",
        )
    return added


def _store(ctx: Any, kind: str, doc: dict[str, Any]) -> str:
    from cartolex.project.files import atomic_write_bytes, json_bytes, utc_stamp

    folder = _folder(ctx)
    base = f"{utc_stamp()}-copilot-{kind}"
    proposal_id, n = base, 1
    while (folder / f"{proposal_id}.json").exists():
        n += 1
        proposal_id = f"{base}-{n}"
    atomic_write_bytes(folder / f"{proposal_id}.json", json_bytes(doc))
    return proposal_id


def _read(ctx: Any, proposal_id: str) -> dict[str, Any]:
    path = _folder(ctx) / f"{proposal_id}.json"
    if not path.is_file():
        raise ApiError.of("proposal_not_found", proposal=proposal_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _checked(result: Any, task: str) -> dict[str, Any]:
    from cartolex.copilot.bundle import check_result

    problems = check_result(result, task=task)
    if problems:
        raise ApiError.of("invalid_copilot_result", detail="; ".join(problems[:5])[:500])
    return dict(result)


def _zip(data: bytes, name: str) -> Response:
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ── themes ───────────────────────────────────────────────────────────────────


def _theme_source(
    request: Request, ctx: Any, edited: dict[str, Any] | None = None
) -> tuple[Any, str, Any]:
    """The tree a bundle sends (the one being edited when given, else the saved one, else
    the proposal), its source and the proposal."""
    from cartolex.project.themes_versions import read_themes

    from .themes import _draft, _parse_tree

    draft = _draft(runtime_of(request), ctx)
    if edited is not None:
        tree, source = _parse_tree(edited), "edited"
    else:
        tree = read_themes(ctx.project)[0]
        source = "saved" if tree is not None else "draft"
        tree = tree if tree is not None else draft
    if tree is None or not (tree.keywords or tree.set_aside):
        raise ApiError.of("themes_empty")
    return tree, source, draft


def _space_models(request: Request, ctx: Any) -> tuple[Any, Any]:
    """The space's lexical data and embeddings (cached by the space's run)."""
    from cartolex.build.records import read_record

    record = read_record(ctx.layout, "themes.space")
    folder = ctx.layout.stage("themes.space") / "models"
    if record is None or not (folder / "embeddings.json").exists():
        raise ApiError.of("no_space")

    def load() -> tuple[Any, Any]:
        from cartolex.atlas.model_files import load_embeddings, load_lexical_data

        return load_lexical_data(folder / "lexical_data.json"), load_embeddings(
            folder / "embeddings.json"
        )

    return runtime_of(request).table_cache.get(("copilot-space", ctx.id, record.run_id), load)


class ThemesExportBody(BaseModel):
    """The tree to send (the one being edited, unsaved edits included; default: the saved
    tree, else the proposal) and the curator's language."""

    tree: dict[str, Any] | None = None
    language: Annotated[str, Field(pattern=r"^[a-z]{2}$")] = "en"


@routes.post("/api/themes/copilot/summary", action="themes.read")
def themes_summary(request: Request, body: ThemesExportBody, ctx: ProjectDep) -> dict[str, Any]:
    """What a themes bundle would hold and never holds, and its counts (nothing is made)."""
    from cartolex.project.copilot import THEMES_CONTAINS, THEMES_NEVER

    from .themes import _usage

    tree, source, _ = _theme_source(request, ctx, body.tree)
    _, people, _ = _usage(runtime_of(request), ctx)
    return {
        "task": "themes",
        "source": source,
        "counts": {
            "nodes": len(tree.nodes),
            "keywords": len(tree.keywords),
            "set_aside": len(tree.set_aside),
            "people": people,
        },
        "contains": list(THEMES_CONTAINS),
        "never": list(THEMES_NEVER),
    }


@routes.post("/api/themes/copilot/export", action="themes.read")
def themes_export(request: Request, body: ThemesExportBody, ctx: ProjectDep) -> Response:
    """The themes bundle (a zip) of the tree sent (default: the saved one, else the proposal)."""
    from cartolex.project.copilot import themes_bundle

    tree, _, draft = _theme_source(request, ctx, body.tree)
    data, emb = _space_models(request, ctx)
    X = data.X
    U = data.X_tf if getattr(data, "X_tf", None) is not None else data.X
    context = _context(
        ctx,
        "themes",
        depth=tree.depth,
        dimensions=int(emb.Z_terms.shape[1]),
        cluster_components=50,
        levels=_levels(tree, ctx.project.config.languages.reference),
    )
    from cartolex.lexicon.theme_comb import tree_levels

    from .themes_fit import _texts

    terms = [str(t) for t in data.terms]
    doc = tree.model_dump(mode="json", by_alias=True)
    D = _texts(request, ctx, terms)
    zipped, _ = themes_bundle(
        tree=doc,
        draft=draft.model_dump(mode="json", by_alias=True) if draft is not None else None,
        terms=terms,
        X=X,
        U=U,
        Z_terms=emb.Z_terms,
        Z_people=emb.Z_ind,
        context=context,
        curator_language=body.language,
        mask=_names(ctx),
        levels=tree_levels(doc, terms, D) if D is not None else None,
        texts=D,
    )
    return _zip(zipped, "copilot-themes.zip")


def _levels(tree: Any, language: str) -> list[dict[str, Any]]:
    """The tree's levels for the guide: name, nodes, keywords under them and on them."""
    from cartolex.copilot import measures

    doc = tree.model_dump(mode="json", by_alias=True)
    sizes = measures.sizes(doc)["levels"]
    own = measures.balance(doc)["levels"]
    placed = len(doc.get("keywords") or {})
    on = [round(x["share"] * placed) for x in own]
    out = []
    for i, level in enumerate(doc["levels"], 1):
        names = level.get("names") or {}
        size = sizes[i - 1] if i <= len(sizes) else {}
        out.append(
            {
                "level": i,
                "name": names.get(language) or next(iter(names.values()), f"level {i}"),
                "nodes": size.get("nodes", 0),
                "keywords_under": sum(on[i - 1 :]),
                "keywords_on": on[i - 1] if i <= len(on) else 0,
            }
        )
    return out


class ImportBody(BaseModel):
    """A copilot result (``result/result.json`` of the bundle), as it came back; for triage,
    several (``results``: parts, or a result taken up again), merged."""

    result: dict[str, Any] | None = None
    results: Annotated[list[dict[str, Any]], Field(max_length=MAX_PARTS * 4)] | None = None

    def all(self) -> list[dict[str, Any]]:
        found = ([self.result] if self.result is not None else []) + list(self.results or [])
        if not found:
            raise ApiError.of("invalid_copilot_result", detail="no result given")
        return found


def _theme_items(result: dict[str, Any], tree: Any) -> tuple[list[dict[str, Any]], Any]:
    """Each change as a proposal item, tried in order on *tree*: why it is refused, if it is.

    A restructuring (or any change of more operations than ``POST /api/themes/ops`` takes
    at once) also carries ``tree``, the tree it gives: the editor puts it in place as one
    step, and applies the other changes on top of it in bounded requests.
    """
    from pydantic import TypeAdapter, ValidationError

    from cartolex.project.themes import ThemeEditError

    from .themes import MAX_OPS, Operation, apply_op

    adapter = TypeAdapter(Operation)
    items = []
    current = tree
    for number, change in enumerate(result.get("changes") or [], start=1):
        refused = ""
        trial = current
        try:
            for raw in change["ops"]:
                trial = apply_op(trial, adapter.validate_python(raw)).tree
        except ValidationError as exc:
            refused = f"an operation is not valid: {exc.errors()[0].get('msg', '')}"[:300]
        except ThemeEditError as exc:
            refused = str(exc)[:300]
        if not refused:
            current = trial
        item = {
            "number": number,
            "verb": VERBS.get(change.get("kind"), "RESTRUCTURE"),
            "kind": change.get("kind"),
            "op": change["ops"][0],
            "ops": change["ops"],
            "reason": str(change.get("reason") or "")[:2000],
            "text": "",
            "line": number,
            "refused": refused,
        }
        if not refused and (change.get("kind") == "restructure" or len(change["ops"]) > MAX_OPS):
            item["tree"] = trial.model_dump(mode="json", by_alias=True)
        items.append(item)
    return items, current


def _theme_proposal(request: Request, ctx: Any, proposal_id: str) -> dict[str, Any]:
    tree, source, _ = _theme_source(request, ctx)
    result = _read(ctx, proposal_id)
    items, after = _theme_items(result, tree)
    # Whether the project's own operations give the tree the kit computed (same places).
    claimed = (result.get("tree") or {}).get("keywords") or {}
    placed = {k: v for k, v in after.keywords.items() if k in claimed}
    return {
        "id": proposal_id,
        "task": "themes",
        "source": source,
        "items": items,
        "unreadable": [],
        "lines": len(items),
        "ignored": 0,
        "applicable": sum(1 for i in items if not i["refused"]),
        "matches": bool(claimed) and placed == claimed,
        "measures": result.get("measures") or {},
        "notes": str(result.get("notes") or "")[:20_000],
        "rules": list(result.get("rules") or []),
        "made_at": result.get("made_at"),
    }


@routes.post("/api/themes/copilot/import", action="themes.write")
def themes_import(request: Request, body: ImportBody, ctx: ProjectDep) -> dict[str, Any]:
    """Keep the result (``decisions/history/ai/``) and read it into proposed changes.

    Nothing changes in the tree: the editor reviews them like a handoff's; the
    accepted ones go through ``POST /api/themes/ops`` and are saved as a version.
    """
    found = body.all()
    if len(found) != 1:
        raise ApiError.of("invalid_copilot_result", detail="import one themes result at a time")
    result = _checked(found[0], "themes")
    with ctx.handle.mutex:
        proposal_id = _store(ctx, "themes", result)
        _keep_rules(ctx, "themes", list(result.get("rules") or []), proposal_id)
        proposal = _theme_proposal(request, ctx, proposal_id)
        if proposal["items"]:
            ctx.project.freeze_identity("first AI answers")
    return proposal


@routes.get("/api/themes/copilot/proposals/{proposal_id}", action="themes.read")
def themes_proposal(request: Request, proposal_id: CopilotId, ctx: ProjectDep) -> dict[str, Any]:
    """One imported themes result: its changes (with why one is refused), measures and notes."""
    return _theme_proposal(request, ctx, proposal_id)


# ── triage ───────────────────────────────────────────────────────────────────

Scope = Literal["all", "both", "check"]
SCOPES = {"all": ["kept", "check", "aside"], "both": ["kept", "check"], "check": ["check"]}


def _triage_items(request: Request, ctx: Any, scope: str) -> tuple[list[dict[str, Any]], int, int]:
    """The candidates a triage bundle sends, with their evidence and the decision so far:
    ``(items, people, already rejected)``. Those an AI already answered stay out, and so
    do those on cartolex's rejection list or this computer's cache (the extraction rejects
    them at its next run)."""
    from cartolex.build.records import read_record
    from cartolex.lexicon.rejects import shipped_terms
    from cartolex.lexicon.scoring import AI_BANDS

    from .keywords import AI_SOURCES, _decisions, _effective, extracted, machine_rejects

    rows, run_id = extracted(runtime_of(request), ctx)
    if run_id is None:
        raise ApiError.of("no_keywords")
    decisions, _ = _decisions(ctx)
    corpus = read_record(ctx.layout, "corpus.assemble")
    n_people = max(1, (corpus.measures.counts if corpus else {}).get("people", 0))
    bands = SCOPES[scope]
    machine = machine_rejects(runtime_of(request))
    rejected: dict[str, frozenset[str]] = {}

    def listed(term: str, lang: str) -> bool:
        if lang not in rejected:
            known = set(shipped_terms(lang)) if lang else set()
            if machine is not None and lang:
                known |= {str(t) for t in machine.terms(lang)}
            rejected[lang] = frozenset(known)
        return " ".join(term.split()).casefold() in rejected[lang]

    items, dropped = [], 0
    for row in sorted(rows, key=lambda r: -r["score_len"]):
        decision = decisions.get((row["term"], row["language"]))
        view = _effective(row, decision)
        if row["band"] not in AI_BANDS or view["band"] not in bands:
            continue
        if decision is not None and decision["source"] in AI_SOURCES:
            continue
        if listed(row["term"], row["language"]):
            dropped += 1
            continue
        reason = view["reason"]
        items.append(
            {
                "term": row["term"],
                "lang": row["language"],
                "band": view["band"],
                "reason": reason,
                "people": row["people"],
                "texts": row["texts"],
                "specificity": round(1.0 - row["people"] / n_people, 3),
                "forms": row["forms"][:4],
                "inside": [reason.split(":", 1)[1].strip()]
                if reason.startswith("part-of:")
                else [],
                "current": decision["decision"] if decision else None,
            }
        )
        if len(items) >= MAX_TERMS:
            break
    return items, n_people, dropped


def estimated_tokens(terms: int, usage_lines: bool = False) -> int:
    """About how many tokens an assistant spends on *terms* candidates: reading them by group,
    writing its decisions, the guide and the talk (the kit's ``budget()`` says it exactly)."""
    return int(terms) * (20 if usage_lines else 12) + 12_000


def suggested_parts(terms: int, usage_lines: bool = False) -> int:
    """How many parts a triage bundle of *terms* candidates is best cut into: as many as
    :data:`CONVERSATION_TOKENS` go into :func:`estimated_tokens`."""
    need = -(-estimated_tokens(terms, usage_lines) // CONVERSATION_TOKENS)
    return max(1, min(MAX_PARTS, need))


@routes.get("/api/keywords/copilot/summary", action="keywords.read")
def triage_summary(
    request: Request,
    ctx: ProjectDep,
    scope: Annotated[Scope, Query()] = "all",
    usage_lines: bool = False,
) -> dict[str, Any]:
    """What a triage bundle would hold and never holds, and its counts (nothing is made);
    ``parts``: how many parts it is best cut into (one assistant conversation each)."""
    from cartolex.project.copilot import TRIAGE_CONTAINS, TRIAGE_NEVER, TRIAGE_USAGE

    items, n_people, dropped = _triage_items(request, ctx, scope)
    contains = list(TRIAGE_CONTAINS) + ([TRIAGE_USAGE] if usage_lines else [])
    return {
        "task": "triage",
        "counts": {
            "terms": len(items),
            "check": sum(1 for i in items if i["band"] == "check"),
            "kept": sum(1 for i in items if i["band"] == "kept"),
            "aside": sum(1 for i in items if i["band"] == "aside"),
            "people": n_people,
            "already_rejected": dropped,
        },
        "parts": suggested_parts(len(items), usage_lines),
        "tokens": estimated_tokens(len(items), usage_lines),
        "contains": contains,
        "never": list(TRIAGE_NEVER),
        "usage_lines": usage_lines,
        "empty": None if items else empty("empty_triage"),
    }


@routes.get("/api/keywords/copilot/export", action="keywords.read")
def triage_export(
    request: Request,
    ctx: ProjectDep,
    scope: Annotated[Scope, Query()] = "all",
    usage_lines: bool = False,
    language: Language = "en",
    parts: Parts = 0,
) -> Response:
    """The triage bundle (a zip); with *usage_lines*, a few lines of text around each
    candidate, every name and identifier masked; *parts*: how many parts the kit cuts the
    candidates into, by theme (0: as many as :func:`suggested_parts` says)."""
    from cartolex.lexicon.extract_raw import read_term_people
    from cartolex.project.copilot import corpus_texts, triage_bundle
    from cartolex.project.copilot import usage_lines as lines_of

    items, _, _ = _triage_items(request, ctx, scope)
    if not items:
        raise ApiError.of("triage_empty")
    users, n_people = read_term_people(ctx.layout.stage("keywords.extract") / "term_people.npz")
    mask = _names(ctx)
    usage = None
    if usage_lines:
        found = lines_of(
            corpus_texts(ctx.layout.stage("corpus.assemble")),
            sorted({it["term"] for it in items}),
            mask,
        )
        usage = {(it["term"], it["lang"]): found.get(it["term"], []) for it in items}
    zipped, _ = triage_bundle(
        items=items,
        users=users,
        n_people=n_people,
        context=_context(ctx, "triage"),
        curator_language=language,
        mask=mask,
        usage=usage,
        parts=parts or suggested_parts(len(items), usage_lines),
    )
    return _zip(zipped, "copilot-triage.zip")


def triage_proposal(ctx: Any, proposal_id: str) -> dict[str, Any]:
    """An imported triage result in the shape of a keyword proposal (``GET /api/ai/proposals/{id}``)."""
    from cartolex.copilot.bundle import CODES
    from cartolex.lexicon.categories import category_of

    from ..etags import version_of
    from .handoff import _decisions

    result = _read(ctx, proposal_id)
    decisions, fp = _decisions(ctx)
    items = []
    for number, d in enumerate(result.get("decisions") or [], start=1):
        term, lang = str(d["term"]), str(d.get("language") or "")
        current = decisions.get((term, lang))
        target = str(d.get("target") or "") if d["decision"] == "merge" else ""
        items.append(
            {
                "number": number,
                "term": term,
                "language": lang,
                "code": d.get("code") or "",
                "category": str(d.get("category") or category_of(d.get("code") or "")),
                "confidence": "sure" if d.get("confidence") == "sure" else "unsure",
                "meaning": CODES.get(d.get("code") or "", ""),
                "english": target,
                "proposed": d["decision"],
                "target": target,
                "reason": str(d.get("reason") or "")[:2000],
                "group": str(d.get("group") or ""),
                "by": "group" if d.get("by") == "group" else "term",
                "current": current["decision"] if current else None,
            }
        )
    return {
        "id": proposal_id,
        "task": "triage",
        "items": items,
        "answered": len(items),
        "unanswered": 0,
        "read": {
            "lines": len(items),
            "ignored": 0,
            "unmatched": 0,
            "renumbered": 0,
            "term_mismatch": 0,
            "duplicates": 0,
        },
        "keywords_version": version_of(fp),
        "measures": result.get("measures") or {},
        "notes": str(result.get("notes") or "")[:20_000],
        "coverage": result.get("coverage") or {},
        "caveats": result.get("caveats") or {},
        "rules": list(result.get("rules") or []),
        "partial": bool(result.get("partial")),
        "merged": int(result.get("merged") or 1),
    }


def merge_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Several triage results as one: the decisions in the order the results were made (a
    later decision on a term wins), the rules together; the counts and caveats of the latest
    result of each bundle and part (a result taken up again holds the earlier one's work)."""
    ordered = sorted(results, key=lambda r: str(r.get("made_at") or ""))
    if len(ordered) == 1:
        return dict(ordered[0])
    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    rules: list[str] = []
    latest: dict[tuple[str, Any], dict[str, Any]] = {}
    for r in ordered:
        for d in r.get("decisions") or []:
            decisions[(str(d.get("term")), str(d.get("language") or ""))] = d
        rules += [x for x in r.get("rules") or [] if x not in rules]
        latest[(str(r.get("bundle")), r.get("part"))] = r
    coverage: dict[str, dict[str, int]] = {}
    caveats: dict[str, Any] = {"read_lightly": ""}
    for r in latest.values():
        for band, row in (r.get("coverage") or {}).items():
            into = coverage.setdefault(band, {})
            for k, v in row.items():
                into[k] = into.get(k, 0) + int(v or 0)
        for key, value in (r.get("caveats") or {}).items():
            if key == "read_lightly":
                caveats["read_lightly"] = "\n".join(
                    x for x in (caveats["read_lightly"], str(value or "")) if x
                )[:2000]
            elif isinstance(value, dict):
                into = caveats.setdefault(key, {})
                for band, n in value.items():
                    into[band] = into.get(band, 0) + int(n or 0)
    last = ordered[-1]
    return {
        **{k: last[k] for k in ("format", "task", "bundle", "made_at", "kit") if k in last},
        "decisions": list(decisions.values()),
        "measures": last.get("measures") or {},
        "coverage": coverage,
        "caveats": caveats,
        "rules": rules[:50],
        "partial": any(bool(r.get("partial")) for r in latest.values()),
        "merged": len(ordered),
        "notes": "\n\n".join(str(r.get("notes") or "") for r in ordered if r.get("notes"))[:20_000],
    }


@routes.post("/api/keywords/copilot/import", action="keywords.write")
def triage_import(body: ImportBody, ctx: ProjectDep) -> dict[str, Any]:
    """Keep the result (``decisions/history/ai/``) and read it into a keyword proposal;
    several results (the parts of a bundle, a result taken up again) are merged into one.
    Their standing rules join the curator's notes (``decisions/curation-notes.md``).

    Nothing reaches ``keywords.csv`` until it is accepted
    (``POST /api/ai/proposals/{id}/accept``).
    """
    result = _checked(merge_results([_checked(r, "triage") for r in body.all()]), "triage")
    with ctx.handle.mutex:
        proposal_id = _store(ctx, "triage", result)
        _keep_rules(ctx, "triage", list(result.get("rules") or []), proposal_id)
        proposal = triage_proposal(ctx, proposal_id)
        if proposal["items"]:
            ctx.project.freeze_identity("first AI answers")
    return proposal
