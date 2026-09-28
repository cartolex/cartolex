# SPDX-License-Identifier: MIT
"""Themes: the tree, every operation of ``cartolex.project.themes``, save, versions, apply."""

from __future__ import annotations

import json
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
        from cartolex.atlas.model_files import load_lexical_data

        return [str(t) for t in load_lexical_data(path).terms]

    return runtime.table_cache.get(("vocabulary", ctx.id, record.run_id), load), record.run_id


def _draft(runtime: Any, ctx: Any, terms: list[str], space_run: str | None) -> ThemesFile | None:
    from cartolex.build.records import read_record
    from cartolex.project.themes import vocabulary_fingerprint
    from cartolex.project.themes_curated import from_curated

    record = read_record(ctx.layout, "themes.group")
    path = ctx.layout.stage("themes.group") / "subfields_draft.json"
    if record is None or not path.exists() or not terms:
        return None

    def load() -> ThemesFile:
        doc = json.loads(path.read_text(encoding="utf-8"))
        tree = from_curated(
            doc, terms, reference_language=ctx.project.config.languages.reference
        ).tree
        basis = {
            "run": f"themes.space/{space_run}" if space_run else None,
            "vocabulary": vocabulary_fingerprint(terms),
        }
        return tree.model_copy(update={"based_on": tree.based_on.model_validate(basis)})

    return runtime.table_cache.get(("draft", ctx.id, record.run_id, space_run), load)


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
    """The theme tree: the saved one, else the draft of the last grouping, else none."""
    from cartolex.project.themes_versions import read_themes

    runtime = runtime_of(request)
    terms, space_run = _vocabulary(runtime, ctx)
    tree, fp = read_themes(ctx.project)
    response.headers["ETag"] = etag_of(fp)
    if tree is not None:
        return {"source": "saved", "version": version_of(fp), **_tree_view(tree, terms)}
    draft = _draft(runtime, ctx, terms, space_run)
    if draft is not None:
        return {"source": "draft", "version": version_of(fp), **_tree_view(draft, terms)}
    return {
        "source": "none",
        "version": version_of(fp),
        "tree": None,
        "empty": empty("empty_no_themes"),
    }


class OpsBody(BaseModel):
    """A tree and the operations to apply to it, in order."""

    tree: dict[str, Any]
    ops: Annotated[list[Operation], Field(min_length=1, max_length=MAX_OPS)]


def _parse_tree(raw: dict[str, Any]) -> ThemesFile:
    try:
        return ThemesFile.model_validate(raw)
    except ValueError as exc:
        raise ApiError.of("invalid_tree", detail=str(exc)) from exc


@routes.post("/api/themes/ops", action="themes.read")
def apply_ops(body: OpsBody, ctx: ProjectDep) -> dict[str, Any]:
    """Apply operations to a tree and return the new tree with each step's description.

    Nothing is saved: the interface keeps the tree being edited and its undo
    list (the descriptions name the steps); ``PUT /api/themes`` saves.
    """
    from cartolex.project.themes import ThemeEditError

    tree = _parse_tree(body.tree)
    steps = []
    for i, op in enumerate(body.ops):
        try:
            edit = apply_op(tree, op)
        except ThemeEditError as exc:
            raise ApiError.of(
                "theme_step_refused", step=i + 1, op=op.op, detail=str(exc), extra={"step": i}
            ) from exc
        tree = edit.tree
        steps.append({"op": op.op, "description": edit.description})
    return {"tree": tree.model_dump(mode="json", by_alias=True), "steps": steps}


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
    return {
        "written": saved.written,
        "action": saved.action,
        "removed": list(saved.removed),
        "version": version_of(saved.fingerprint),
        "tree": saved.tree.model_dump(mode="json", by_alias=True),
    }


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
