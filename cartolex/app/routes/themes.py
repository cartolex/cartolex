# SPDX-License-Identifier: MIT
"""Themes: the tree, every operation of ``cartolex.project.themes``, save, versions, apply."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from fastapi import Path as PathParam
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from cartolex.project.models import ThemesFile

from ..deps import ProjectDep
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..messages import empty
from ..routing import Routes, runtime_of

routes = Routes(tags=["themes"])

NodeId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Names = Annotated[
    dict[Annotated[str, Field(pattern=r"^[a-z]{2}$")], str | None], Field(max_length=8)
]
Keywords = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=300)]],
    Field(min_length=1, max_length=50_000),
]
#: The most operations one request applies.
MAX_OPS = 500


class RenameNode(BaseModel):
    op: Literal["rename_node"]
    node_id: NodeId
    names: Names


class RenameLevel(BaseModel):
    op: Literal["rename_level"]
    level: Annotated[int, Field(ge=1, le=4)]
    names: Names


class MoveKeywords(BaseModel):
    op: Literal["move_keywords"]
    keywords: Keywords
    node_id: NodeId


class MoveNode(BaseModel):
    op: Literal["move_node"]
    node_id: NodeId
    parent: NodeId | None = None
    position: Annotated[int, Field(ge=0)] | None = None


class MergeNodes(BaseModel):
    op: Literal["merge_nodes"]
    source: NodeId
    target: NodeId


class SplitPart(BaseModel):
    members: Annotated[list[str], Field(min_length=1, max_length=50_000)]
    names: Names = {}


class SplitNode(BaseModel):
    op: Literal["split_node"]
    node_id: NodeId
    parts: Annotated[list[SplitPart], Field(min_length=1, max_length=100)]
    ids: list[NodeId] | None = None


class CreateNode(BaseModel):
    op: Literal["create_node"]
    parent: NodeId | None = None
    names: Names = {}
    node_id: NodeId | None = None
    position: Annotated[int, Field(ge=0)] | None = None


class DeleteNode(BaseModel):
    op: Literal["delete_node"]
    node_id: NodeId


class SetAside(BaseModel):
    op: Literal["set_aside"]
    keywords: Keywords
    reason: Annotated[str, Field(max_length=500)] = ""


class PutBack(BaseModel):
    op: Literal["put_back"]
    keywords: Keywords
    node_id: NodeId | None = None


class SetReview(BaseModel):
    op: Literal["set_review"]
    keywords: Keywords
    state: Literal["to_check", "reviewed"] | None = None


class SetAttribution(BaseModel):
    op: Literal["set_attribution"]
    keywords: Keywords
    levels: Annotated[int, Field(ge=0, le=3)] | None = None


class PruneEmpty(BaseModel):
    op: Literal["prune_empty"]


class InsertLevel(BaseModel):
    op: Literal["insert_level"]
    at: Annotated[int, Field(ge=1, le=5)]
    root_names: Names | None = None


class RemoveLevel(BaseModel):
    op: Literal["remove_level"]
    at: Annotated[int, Field(ge=1, le=4)]


Operation = Annotated[
    RenameNode
    | RenameLevel
    | MoveKeywords
    | MoveNode
    | MergeNodes
    | SplitNode
    | CreateNode
    | DeleteNode
    | SetAside
    | PutBack
    | SetReview
    | SetAttribution
    | PruneEmpty
    | InsertLevel
    | RemoveLevel,
    Field(discriminator="op"),
]


def apply_op(tree: ThemesFile, op: Any) -> Any:
    """Apply one operation (a pure function of ``cartolex.project.themes``): an ``Edit``."""
    from cartolex.project import themes as t

    kind = op.op
    if kind == "rename_node":
        return t.rename_node(tree, op.node_id, op.names)
    if kind == "rename_level":
        return t.rename_level(tree, op.level, op.names)
    if kind == "move_keywords":
        return t.move_keywords(tree, op.keywords, op.node_id)
    if kind == "move_node":
        return t.move_node(tree, op.node_id, op.parent, op.position)
    if kind == "merge_nodes":
        return t.merge_nodes(tree, op.source, op.target)
    if kind == "split_node":
        return t.split_node(tree, op.node_id, [(p.members, p.names) for p in op.parts], op.ids)
    if kind == "create_node":
        return t.create_node(tree, op.parent, op.names, node_id=op.node_id, position=op.position)
    if kind == "delete_node":
        return t.delete_node(tree, op.node_id)
    if kind == "set_aside":
        return t.set_aside(tree, op.keywords, op.reason)
    if kind == "put_back":
        return t.put_back(tree, op.keywords, op.node_id)
    if kind == "set_review":
        return t.set_review(tree, op.keywords, op.state)
    if kind == "set_attribution":
        return t.set_attribution(tree, op.keywords, op.levels)
    if kind == "prune_empty":
        return t.prune_empty(tree)
    if kind == "insert_level":
        return t.insert_level(tree, op.at, op.root_names)
    return t.remove_level(tree, op.at)


def _vocabulary(runtime: Any, ctx: Any) -> tuple[list[str], str | None]:
    """The current vocabulary (the space's keywords, in row order) and its run id."""
    from cartolex.build.records import read_record

    record = read_record(ctx.layout, "themes.space")
    path = ctx.layout.stage("themes.space") / "models" / "lexical_data.json"
    if record is None or not path.exists():
        return [], None

    def load() -> list[str]:
        # The terms are in the JSON document itself: no need to load the matrices (and the
        # scientific libraries) to know the vocabulary.
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("terms"), list):
            return [str(t) for t in doc["terms"]]
        from cartolex.atlas.model_files import load_lexical_data

        return [str(t) for t in load_lexical_data(path).terms]

    return runtime.table_cache.get(("vocabulary", ctx.id, record.run_id), load), record.run_id


def _group_run(ctx: Any) -> str | None:
    """The run id of the grouping whose proposal is on disk (``themes.group``)."""
    from cartolex.build.records import read_record

    record = read_record(ctx.layout, "themes.group")
    return record.run_id if record else None


def _draft(runtime: Any, ctx: Any) -> ThemesFile | None:
    """The grouping's proposal (``themes.group/themes_draft.json``, any depth), or ``None``."""
    run = _group_run(ctx)
    path = ctx.layout.stage("themes.group") / "themes_draft.json"
    if run is None or not path.exists():
        return None

    def load() -> ThemesFile:
        return ThemesFile.model_validate(json.loads(path.read_text(encoding="utf-8")))

    return runtime.table_cache.get(("draft", ctx.id, run), load)


def _usage(runtime: Any, ctx: Any) -> tuple[dict[str, list[float]], int, str | None]:
    """Each keyword's usage in the current vocabulary: ``[people, weight]``; people counted.

    *people* is how many people use the keyword; *weight* is the sum, over
    people, of the keyword's share of their usage (TF counts when the space
    has them): the keywords' weights add up to the number of people with usage.
    """
    from cartolex.build.records import read_record

    record = read_record(ctx.layout, "themes.space")
    path = ctx.layout.stage("themes.space") / "models" / "lexical_data.json"
    if record is None or not path.exists():
        return {}, 0, None

    def load() -> tuple[dict[str, list[float]], int]:
        import numpy as np

        from cartolex.atlas.model_files import load_lexical_data

        data = load_lexical_data(path)
        X = data.X_tf if getattr(data, "X_tf", None) is not None else data.X
        X = X.tocsr() if hasattr(X, "tocsr") else X
        totals = np.asarray(X.sum(axis=1)).ravel()
        scale = np.divide(1.0, totals, out=np.zeros_like(totals, dtype=float), where=totals > 0)
        weights = np.asarray(X.multiply(scale[:, None]).sum(axis=0)).ravel()
        people = np.asarray((X > 0).sum(axis=0)).ravel()
        terms = [str(t) for t in data.terms]
        usage = {
            t: [int(n), round(float(w), 4)] for t, n, w in zip(terms, people, weights, strict=True)
        }
        return usage, int((totals > 0).sum())

    usage, counted = runtime.table_cache.get(("usage", ctx.id, record.run_id), load)
    return usage, counted, record.run_id


def _agreed_group_run(ctx: Any, tree: ThemesFile) -> str | None:
    """The grouping run a curated tree has agreed with: named by the tree, else last applied.

    A tree saved from a proposal, or whose owner kept it over a new proposal,
    names the grouping in ``based_on.run``. A rebase names the space instead;
    the grouping it was applied with is then the one the last ``themes.apply``
    run read.
    """
    from cartolex.build.records import read_record

    run = tree.based_on.run or ""
    if run.startswith("themes.group/"):
        return run.split("/", 1)[1]
    record = read_record(ctx.layout, "themes.apply")
    for entry in record.inputs if record else ():
        if getattr(entry, "stage", None) == "themes.group":
            return entry.run_id
    return None


def _proposal_state(ctx: Any, tree: ThemesFile, draft: ThemesFile | None) -> dict[str, Any]:
    """Whether a new grouping of the same vocabulary waits to be agreed on (adopted or not)."""
    run = _group_run(ctx)
    if draft is None or run is None:
        return {"run": None, "pending": False, "same_vocabulary": False}
    same = bool(tree.based_on.vocabulary) and draft.based_on.vocabulary == tree.based_on.vocabulary
    agreed = _agreed_group_run(ctx, tree)
    return {
        "run": f"themes.group/{run}",
        "pending": same and agreed is not None and agreed != run,
        "same_vocabulary": same,
    }


def _tree_view(tree: ThemesFile, terms: list[str]) -> dict[str, Any]:
    from cartolex.project.themes import vocabulary_fingerprint, vocabulary_gaps

    missing, extra = vocabulary_gaps(tree, terms) if terms else ([], [])
    return {
        "tree": tree.model_dump(mode="json", by_alias=True),
        "vocabulary": len(terms),
        "based_on_current": bool(terms)
        and tree.based_on.vocabulary == vocabulary_fingerprint(terms),
        "missing": missing[:200],
        "missing_count": len(missing),
        "extra": extra[:200],
        "extra_count": len(extra),
    }


@routes.get("/api/themes", action="themes.read")
def get_themes(request: Request, response: Response, ctx: ProjectDep) -> dict[str, Any]:
    """The theme tree: the saved one, else the grouping's proposal, else none.

    Beside the tree: how it stands against the current vocabulary, how many
    keywords wait in the « to check » queue, and whether a new proposal of the
    same vocabulary waits to be agreed on (``proposal``).
    """
    from cartolex.project.themes_versions import read_themes

    runtime = runtime_of(request)
    terms, space_run = _vocabulary(runtime, ctx)
    tree, fp = read_themes(ctx.project)
    response.headers["ETag"] = etag_of(fp)
    draft = _draft(runtime, ctx)
    common = {"version": version_of(fp), "space_run": space_run}
    if tree is not None:
        return {
            "source": "saved",
            **common,
            **_tree_view(tree, terms),
            "proposal": _proposal_state(ctx, tree, draft),
        }
    if draft is not None:
        return {
            "source": "draft",
            **common,
            **_tree_view(draft, terms),
            "proposal": {"run": draft.based_on.run, "pending": False, "same_vocabulary": True},
        }
    return {
        "source": "none",
        **common,
        "tree": None,
        "empty": empty("empty_no_themes"),
    }


@routes.get("/api/themes/draft", action="themes.read")
def get_draft(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """The grouping's latest proposal, whatever tree is saved (to compare or adopt it)."""
    draft = _draft(runtime_of(request), ctx)
    if draft is None:
        raise ApiError.of("no_proposal")
    return {"run": draft.based_on.run, "tree": draft.model_dump(mode="json", by_alias=True)}


@routes.get("/api/themes/usage", action="themes.read")
def usage(request: Request, response: Response, ctx: ProjectDep) -> Response:
    """Each keyword's usage in the current vocabulary: ``{term: [people, weight]}``.

    Cached by the space's run, with an ``ETag``; ``If-None-Match`` gives 304.
    """
    usage_map, counted, run = _usage(runtime_of(request), ctx)
    etag = f'"usage-{run}"' if run else '"usage-none"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse({"run": run, "people": counted, "terms": usage_map}, headers={"ETag": etag})


class OpsBody(BaseModel):
    """A tree and the operations to apply to it, in order.

    With ``lenient``, a refused operation is skipped and reported instead of
    refusing the whole request (to re-apply a draft on a newer tree, or to try
    proposals one by one).
    """

    tree: dict[str, Any]
    ops: Annotated[list[Operation], Field(min_length=1, max_length=MAX_OPS)]
    lenient: bool = False


def _parse_tree(raw: dict[str, Any]) -> ThemesFile:
    try:
        return ThemesFile.model_validate(raw)
    except ValueError as exc:
        raise ApiError.of("invalid_tree", detail=str(exc)) from exc


@routes.post("/api/themes/ops", action="themes.read")
def apply_ops(body: OpsBody, ctx: ProjectDep) -> dict[str, Any]:
    """Apply operations to a tree and return the new tree with each step's description.

    Nothing is saved: the interface keeps the tree being edited and its undo
    list (the descriptions name the steps); ``PUT /api/themes`` saves. A
    refused step refuses the request (422, ``theme_step_refused``), unless
    ``lenient``: then it is skipped, and its step says why (``refused``).
    """
    from cartolex.project.themes import ThemeEditError

    tree = _parse_tree(body.tree)
    steps: list[dict[str, Any]] = []
    for i, op in enumerate(body.ops):
        try:
            edit = apply_op(tree, op)
        except ThemeEditError as exc:
            if body.lenient:
                steps.append({"op": op.op, "refused": str(exc)})
                continue
            raise ApiError.of(
                "theme_step_refused", step=i + 1, op=op.op, detail=str(exc), extra={"step": i}
            ) from exc
        tree = edit.tree
        steps.append({"op": op.op, "description": edit.description})
    return {"tree": tree.model_dump(mode="json", by_alias=True), "steps": steps}


class CompareBody(BaseModel):
    """Two trees to compare: what changed from ``before`` to ``after``."""

    before: dict[str, Any]
    after: dict[str, Any]
    limit: Annotated[int, Field(ge=1, le=100_000)] = 5_000


@routes.post("/api/themes/compare", action="themes.read")
def compare_trees(body: CompareBody, ctx: ProjectDep) -> dict[str, Any]:
    """Every difference between two trees (``cartolex.project.themes.compare``), and counts by kind."""
    from cartolex.project.themes import compare

    changes = compare(_parse_tree(body.before), _parse_tree(body.after))
    counts: dict[str, int] = {}
    for c in changes:
        counts[c.kind] = counts.get(c.kind, 0) + 1
    return {
        "changes": [c.as_dict() for c in changes[: body.limit]],
        "total": len(changes),
        "counts": counts,
    }


class SaveBody(BaseModel):
    tree: dict[str, Any]
    action: Annotated[str, Field(min_length=1, max_length=200)]


@routes.put("/api/themes", action="themes.write")
def save(request: Request, response: Response, body: SaveBody, ctx: ProjectDep) -> dict[str, Any]:
    """Save the tree as a new version (send ``If-Match``); empty nodes are removed and named."""
    from cartolex.project.themes_versions import save_themes

    expected = expected_version(request)
    tree = _parse_tree(body.tree)
    with ctx.handle.mutex:
        check_version(ctx.layout.themes_json, expected)
        saved = save_themes(ctx.project, tree, expected=expected, action=body.action)
        if saved.written:
            ctx.project.freeze_identity("first curation decision")
    response.headers["ETag"] = etag_of(saved.fingerprint)
    return _saved_json(saved)


def _saved_json(saved: Any) -> dict[str, Any]:
    return {
        "written": saved.written,
        "action": saved.action,
        "removed": list(saved.removed),
        "version": version_of(saved.fingerprint),
        "tree": saved.tree.model_dump(mode="json", by_alias=True),
    }


@routes.post("/api/themes/rebase", action="themes.write")
def rebase_now(request: Request, response: Response, ctx: ProjectDep) -> dict[str, Any]:
    """Rebase the saved tree onto the current vocabulary now (send ``If-Match``).

    The same rebase an « apply » runs first: kept keywords stay, each new one
    goes to the node that holds most of its group in the proposal, marked « to
    check » (set aside when no node has a majority), vanished ones are removed.
    It is saved as a new version; nothing is written when the tree is already
    based on the current vocabulary.
    """
    from cartolex.build.engine import prepare_themes
    from cartolex.project.themes_versions import read_themes

    expected = expected_version(request)
    terms, _ = _vocabulary(runtime_of(request), ctx)
    if not terms:
        raise ApiError.of("no_keywords")
    with ctx.handle.mutex:
        check_version(ctx.layout.themes_json, expected)
        if read_themes(ctx.project)[0] is None:
            raise ApiError.of("file_not_written", file="themes.json")
        notes = prepare_themes(ctx.project)
        tree, fp = read_themes(ctx.project)
    assert tree is not None
    response.headers["ETag"] = etag_of(fp)
    return {
        "written": bool(notes),
        "notes": notes,
        "version": version_of(fp),
        "to_check": sum(1 for v in tree.review.values() if v == "to_check"),
        "tree": tree.model_dump(mode="json", by_alias=True),
    }


class ProposalBody(BaseModel):
    """What to do with a new proposal of the same vocabulary: adopt it, or keep the tree."""

    decision: Literal["adopt", "keep"]
    run: Annotated[str, Field(min_length=1, max_length=120)]


@routes.post("/api/themes/proposal", action="themes.write")
def decide_proposal(
    request: Request, response: Response, body: ProposalBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Agree once on a new grouping of the same vocabulary (send ``If-Match``).

    ``adopt`` saves the proposal as the new tree; ``keep`` keeps the saved tree
    and records that it was kept over this proposal (``based_on.run``). Either
    is a new version, undone like any other; ``run`` names the proposal the
    person saw, and a newer one refuses the decision.
    """
    from cartolex.project.themes_versions import read_themes, save_themes

    expected = expected_version(request)
    draft = _draft(runtime_of(request), ctx)
    if draft is None:
        raise ApiError.of("no_proposal")
    if draft.based_on.run != body.run:
        raise ApiError.of("proposal_changed", run=str(draft.based_on.run))
    with ctx.handle.mutex:
        check_version(ctx.layout.themes_json, expected)
        tree, _ = read_themes(ctx.project)
        if tree is None:
            raise ApiError.of("file_not_written", file="themes.json")
        run_id = body.run.split("/", 1)[-1]
        if body.decision == "adopt":
            chosen, action = draft, f"adopt the grouping proposal {run_id}"
        else:
            basis = tree.based_on.model_copy(update={"run": body.run})
            chosen = tree.model_copy(update={"based_on": basis})
            action = f"keep the curated tree over the proposal {run_id}"
        saved = save_themes(ctx.project, chosen, expected=expected, action=action)
    response.headers["ETag"] = etag_of(saved.fingerprint)
    return _saved_json(saved)


VersionId = Annotated[str, PathParam(pattern=r"^[\w-]{1,96}$")]


def _version_json(v: Any) -> dict[str, Any]:
    def iso(d: Any) -> str | None:
        return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None

    return {
        "id": v.id,
        "made_at": iso(v.made_at),
        "made_by": v.made_by,
        "replaced_at": iso(v.replaced_at),
        "replaced_by": v.replaced_by,
    }


@routes.get("/api/themes/versions", action="themes.read")
def versions(ctx: ProjectDep) -> dict[str, Any]:
    """Every saved version of the tree, the current one first."""
    from cartolex.project.themes_versions import list_versions

    items = [_version_json(v) for v in list_versions(ctx.project)]
    return {
        "items": items,
        "total": len(items),
        "empty": None if items else empty("empty_tree_never_saved"),
    }


@routes.get("/api/themes/versions/{version_id}", action="themes.read")
def read_version(version_id: VersionId, ctx: ProjectDep) -> dict[str, Any]:
    """The tree of one version."""
    from cartolex.project.themes_versions import read_version as read

    try:
        tree = read(ctx.project, version_id)
    except KeyError as exc:
        raise ApiError.of("version_not_found", version=version_id, file="themes.json") from exc
    return {"id": version_id, "tree": tree.model_dump(mode="json", by_alias=True)}


@routes.post("/api/themes/versions/{version_id}/restore", action="themes.write")
def restore(
    request: Request, response: Response, version_id: VersionId, ctx: ProjectDep
) -> dict[str, Any]:
    """Make an earlier version current, as a new version (``restore <id>``); send ``If-Match``."""
    from cartolex.project.themes_versions import restore_version

    expected = expected_version(request)
    with ctx.handle.mutex:
        check_version(ctx.layout.themes_json, expected)
        try:
            saved = restore_version(ctx.project, version_id, expected=expected)
        except KeyError as exc:
            raise ApiError.of("version_not_found", version=version_id, file="themes.json") from exc
        except ValueError as exc:
            raise ApiError.of("already_current") from exc
    response.headers["ETag"] = etag_of(saved.fingerprint)
    return {
        "restored": version_id,
        "action": saved.action,
        "version": version_of(saved.fingerprint),
    }


#: The stages an « apply » builds: the themes, and the map that shows them.
APPLY_TARGETS = ["themes.apply", "map.layout", "map.trajectories", "overlays.position"]


@routes.post("/api/themes/apply", action="build.start")
def apply(request: Request, ctx: ProjectDep) -> JSONResponse:
    """Apply the saved tree: a build job of the themes and the map (``GET /api/build`` tracks it)."""
    from .build import start_build_job

    runtime = runtime_of(request)
    targets = [t for t in APPLY_TARGETS if t in runtime.registry]
    return JSONResponse(
        start_build_job(runtime, ctx, targets, title="apply the themes"), status_code=202
    )


# ── AI curation by handoff ───────────────────────────────────────────────────

#: What a theme bundle holds, and what it never holds (shown before the export).
HANDOFF_CONTAINS = (
    "the tree: node ids, names and levels",
    "each node's most used keywords, with how many people use each",
    "the set-aside keywords and why",
    "the field's title and description, as the assistant's context",
)
HANDOFF_NEVER = ("texts", "people's names or identifiers", "keys")
ThemeProposalId = Annotated[str, PathParam(pattern=r"^\d{8}T\d{6}Z-themes(-\d+)?$")]


class ThemeExportBody(BaseModel):
    """The tree to send (the one being edited; default: the saved tree, else the proposal),
    how many keywords per node, and the size of each part."""

    tree: dict[str, Any] | None = None
    top: Annotated[int, Field(ge=3, le=50)] = 20
    max_tokens: Annotated[int, Field(ge=4_000, le=1_000_000)] = 24_000


def _theme_export(request: Request, body: ThemeExportBody, ctx: Any) -> dict[str, Any]:
    from cartolex.project.themes_handoff import bundle_parts
    from cartolex.project.themes_versions import read_themes

    runtime = runtime_of(request)
    if body.tree is not None:
        tree: ThemesFile | None = _parse_tree(body.tree)
    else:
        tree = read_themes(ctx.project)[0] or _draft(runtime, ctx)
    if tree is None or not (tree.keywords or tree.set_aside):
        raise ApiError.of("theme_handoff_empty")
    usage_map, _, run = _usage(runtime, ctx)
    config = ctx.project.config
    parts = bundle_parts(
        tree,
        usage_map,
        domain=config.identity.domain_title,
        description=config.identity.domain_description,
        language=config.languages.reference,
        max_tokens=body.max_tokens,
        top=body.top,
        meta={"space_run": run},
    )
    return {
        "parts": parts,
        "nodes": len(tree.nodes),
        "keywords": len(tree.keywords),
        "contains": list(HANDOFF_CONTAINS),
        "never": list(HANDOFF_NEVER),
    }


@routes.post("/api/themes/handoff/export", action="themes.read")
def theme_export(request: Request, body: ThemeExportBody, ctx: ProjectDep) -> dict[str, Any]:
    """The parts of a theme handoff: the prompt to paste, the tree to attach, the answer's
    format and ``bundle.json`` for each; what they contain and what they never contain."""
    return _theme_export(request, body, ctx)


@routes.post("/api/themes/handoff/export.zip", action="themes.read")
def theme_export_zip(request: Request, body: ThemeExportBody, ctx: ProjectDep) -> Response:
    """The same parts as a zip: one folder per part, with ``bundle.json`` beside its texts."""
    from cartolex.project.handoff import part_zip
    from cartolex.project.themes_handoff import part_files

    out = _theme_export(request, body, ctx)
    data = part_zip({p["name"]: part_files(p) for p in out["parts"]})
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="themes-handoff.zip"'},
    )


class ThemeImportBody(BaseModel):
    """The part that was sent (its ``bundle.json``), and the answer as it came back."""

    bundle: dict[str, Any]
    answer: Annotated[str, Field(min_length=1, max_length=5_000_000)]


def _theme_proposal(ctx: Any, proposal_id: str) -> dict[str, Any]:
    from cartolex.project.themes_handoff import parse_answer, tree_of

    folder = ctx.layout.history / "ai"
    answer = folder / f"{proposal_id}.txt"
    sent = folder / f"{proposal_id}.bundle.json"
    if not answer.is_file() or not sent.is_file():
        raise ApiError.of("proposal_not_found", proposal=proposal_id)
    record = json.loads(sent.read_text(encoding="utf-8"))
    tree = tree_of(record)
    parsed = parse_answer(
        answer.read_text(encoding="utf-8"), tree, language=record.get("language") or "en"
    )
    return {
        "id": proposal_id,
        "part": record.get("part", 1),
        "parts": record.get("parts", 1),
        **parsed.as_dict(),
        "applicable": sum(1 for i in parsed.items if not i.refused),
    }


@routes.post("/api/themes/handoff/import", action="themes.write")
def theme_import(body: ThemeImportBody, ctx: ProjectDep) -> dict[str, Any]:
    """Keep the answer as it came (``decisions/history/ai/``) and read it into proposed operations.

    Nothing changes in the tree: the editor shows the proposal, and the
    operations someone accepts go through ``POST /api/themes/ops`` like any
    other edit. The first AI answers freeze the project's identity.
    """
    from cartolex.project.files import atomic_write_bytes, json_bytes, utc_stamp
    from cartolex.project.themes_handoff import tree_of

    try:
        tree_of(body.bundle)
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError.of("invalid_theme_bundle", detail=str(exc)[:300]) from exc
    folder = ctx.layout.history / "ai"
    with ctx.handle.mutex:
        base = f"{utc_stamp()}-themes"
        proposal_id, n = base, 1
        while (folder / f"{proposal_id}.txt").exists():
            n += 1
            proposal_id = f"{base}-{n}"
        atomic_write_bytes(folder / f"{proposal_id}.bundle.json", json_bytes(body.bundle))
        atomic_write_bytes(folder / f"{proposal_id}.txt", body.answer.encode("utf-8"))
        proposal = _theme_proposal(ctx, proposal_id)
        if proposal["items"]:
            ctx.project.freeze_identity("first AI answers")
    return proposal


@routes.get("/api/themes/handoff/proposals", action="themes.read")
def theme_proposals(ctx: ProjectDep) -> dict[str, Any]:
    """The theme proposals imported so far, the newest first."""
    folder = ctx.layout.history / "ai"
    names = (
        (p.name[: -len(".txt")] for p in folder.glob("*-themes*.txt")) if folder.is_dir() else ()
    )
    ids = sorted((i for i in names if re.match(r"^\d{8}T\d{6}Z-themes(-\d+)?$", i)), reverse=True)
    return {
        "items": [{"id": i, "at": i[:16]} for i in ids],
        "total": len(ids),
        "empty": None if ids else empty("empty_no_proposals"),
    }


@routes.get("/api/themes/handoff/proposals/{proposal_id}", action="themes.read")
def theme_proposal(proposal_id: ThemeProposalId, ctx: ProjectDep) -> dict[str, Any]:
    """One imported answer: each proposed operation (with why it is refused, if it is),
    and the lines that could not be read."""
    return _theme_proposal(ctx, proposal_id)
