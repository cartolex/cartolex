# SPDX-License-Identifier: MIT
"""How well keywords fit the tree being edited: borderline keywords and suggested places.

Both read the tree the interface is editing (sent in the body, like
``POST /api/themes/ops``) and the keywords' vectors of the current space
(``themes.space``), cached by the space's run: nothing is written, and the
answer follows every edit. The measure is :mod:`cartolex.lexicon.theme_fit`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Request
from pydantic import BaseModel, Field

from ..deps import MAX_LIMIT, ListParams, ProjectDep, page
from ..errors import ApiError
from ..messages import empty
from ..routing import Routes, runtime_of
from .themes import _parse_tree, _vocabulary

routes = Routes(tags=["themes"])

Term = Annotated[str, Field(min_length=1, max_length=300)]


def _space(request: Request, ctx: Any) -> tuple[list[str], Any]:
    """The space's keywords (row order) and their vectors; 409 ``no_space`` before it is built."""
    runtime = runtime_of(request)
    terms, run = _vocabulary(runtime, ctx)
    path = ctx.layout.stage("themes.space") / "models" / "embeddings.json"
    if run is None or not terms or not path.exists():
        raise ApiError.of("no_space")

    def load() -> Any:
        from cartolex.atlas.model_files import load_embeddings

        return load_embeddings(path).Z_terms

    Z = runtime.table_cache.get(("term-vectors", ctx.id, run), load)
    if len(Z) != len(terms):
        raise ApiError.of("no_space")
    return terms, Z


class BorderlineBody(BaseModel):
    """The tree being edited, the level to compare at (its own node's when absent), a page."""

    tree: dict[str, Any]
    level: Annotated[int, Field(ge=1, le=4)] | None = None
    reviewed: bool = False
    offset: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = 100
    sort: Annotated[str, Field(max_length=64, pattern=r"^-?[a-z_]+$")] | None = None
    q: Annotated[str, Field(max_length=200)] | None = None


@routes.post("/api/themes/borderline", action="themes.read")
def borderline(request: Request, body: BorderlineBody, ctx: ProjectDep) -> dict[str, Any]:
    """The placed keywords nearest the border between their node and another, smallest margin first.

    Each item: ``keyword``, ``node`` (its node at the level compared),
    ``level``, ``other`` (the nearest other node there), ``own`` and ``near``
    (cosines to the two centroids, the keyword left out of its own) and
    ``margin`` (``own − near``; negative: nearer the other node). Keywords
    kept here from this list (review state ``kept``) are left out unless
    ``reviewed``; a review made elsewhere (the queue of a rebase) does not hide one. Sorts:
    ``margin`` (default), ``keyword``, ``own``, ``near``; ``q`` filters on the
    keyword's text.
    """
    from cartolex.lexicon.theme_fit import borderline as measure

    tree = _parse_tree(body.tree)
    terms, Z = _space(request, ctx)
    doc = tree.model_dump(mode="json", by_alias=True)
    review = doc.get("review") or {}
    params = ListParams(body.offset, body.limit, body.sort, body.q)
    items = [
        {
            "keyword": b.keyword,
            "node": b.node,
            "level": b.level,
            "other": b.other,
            "own": b.own,
            "near": b.near,
            "margin": b.margin,
            "review": review.get(b.keyword),
        }
        for b in measure(doc, terms, Z, level=body.level)
        if (body.reviewed or review.get(b.keyword) != "kept")
        and (not params.q or params.q in b.keyword.casefold())
    ]
    return page(
        items,
        params,
        sorts={
            "margin": lambda i: (i["margin"], i["keyword"]),
            "keyword": lambda i: i["keyword"],
            "own": lambda i: (i["own"], i["keyword"]),
            "near": lambda i: (i["near"], i["keyword"]),
        },
        default_sort="margin",
        filters={"level": body.level, "q": body.q, "reviewed": body.reviewed or None},
        empty=empty("empty_no_borderline"),
        extra={
            "measure": "cosine margin to the nearest other node of the same level",
            "negative": sum(1 for i in items if i["margin"] < 0),
        },
    )


def _comb_options(ctx: Any) -> Any:
    """The comb's settings the grouping ran with (its record's parameters, else the defaults)."""
    from cartolex.build.engine import comb_options
    from cartolex.build.records import read_record

    record = read_record(ctx.layout, "themes.group")
    values = {} if record is None else {k: v.value for k, v in record.parameters.items()}
    return comb_options(values)


def _texts(request: Request, ctx: Any, terms: list[str]) -> Any:
    """The texts × keywords the grouping's comb read (``None``: not read, the comb was off)."""
    from cartolex.build.records import read_record
    from cartolex.lexicon.theme_comb import load_text_keywords
    from cartolex.lexicon.theme_tree import TEXT_KEYWORDS

    record = read_record(ctx.layout, "themes.group")
    path = ctx.layout.stage("themes.group") / TEXT_KEYWORDS
    if record is None or not path.exists():
        return None
    D = runtime_of(request).table_cache.get(
        ("text-keywords", ctx.id, record.run_id), lambda: load_text_keywords(path)
    )
    return D if D.shape[1] == len(terms) else None


LEVEL_SORTS = {
    "suggested": lambda i: (i["to"] is None, -i["share"] if i["to"] else i["share"], i["keyword"]),
    "keyword": lambda i: i["keyword"],
    "share": lambda i: (i["share"], i["keyword"]),
}


class LevelsBody(BaseModel):
    """The tree being edited and a page."""

    tree: dict[str, Any]
    reviewed: bool = False
    offset: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = 100
    sort: Annotated[str, Field(max_length=64, pattern=r"^-?[a-z_]+$")] | None = None
    q: Annotated[str, Field(max_length=200)] | None = None


@routes.post("/api/themes/levels", action="themes.read")
def levels(request: Request, body: LevelsBody, ctx: ProjectDep) -> dict[str, Any]:
    """The placed keywords whose texts support a higher node: move up, or too broad for any theme.

    The comb (:func:`cartolex.lexicon.theme_comb.tree_levels`) read on the tree
    sent, from the texts the grouping read. Each item: ``keyword``, ``node``
    (where the tree puts it), ``to`` (the ancestor to move it up to; ``null``:
    too broad for any theme), ``share`` (the share of its use that node holds,
    above what any keyword gives it; too broad: the best a top-level node
    holds), ``texts`` and ``reason`` (the set-aside reason of a keyword too
    broad). Moves up first, largest share first. Keywords kept
    here (review ``kept``) are left out unless ``reviewed``. Empty with
    ``empty_no_texts`` when the grouping did not read the texts.
    """
    from cartolex.lexicon.theme_comb import tree_levels
    from cartolex.lexicon.theme_tree import TOO_BROAD

    tree = _parse_tree(body.tree)
    terms, _ = _space(request, ctx)
    params = ListParams(body.offset, body.limit, body.sort, body.q)
    D = _texts(request, ctx, terms)
    if D is None:
        return page(
            [],
            params,
            sorts=LEVEL_SORTS,
            default_sort="suggested",
            empty=empty("empty_no_texts"),
            extra={"theta": None, "too_broad": 0},
        )
    doc = tree.model_dump(mode="json", by_alias=True)
    review = doc.get("review") or {}
    theta, found = tree_levels(doc, terms, D, options=_comb_options(ctx))
    items = [
        {
            "keyword": s.keyword,
            "node": s.node,
            "to": s.to,
            "share": s.share,
            "texts": s.texts,
            "reason": None if s.to else TOO_BROAD,
            "review": review.get(s.keyword),
        }
        for s in found
        if (body.reviewed or review.get(s.keyword) != "kept")
        and (not params.q or params.q in s.keyword.casefold())
    ]
    return page(
        items,
        params,
        sorts=LEVEL_SORTS,
        default_sort="suggested",
        filters={"q": body.q, "reviewed": body.reviewed or None},
        empty=empty("empty_no_levels"),
        extra={
            "measure": "share of the keyword's texts on the node, above what any keyword gives it",
            "theta": theta,
            "too_broad": sum(1 for i in items if i["to"] is None),
        },
    )


class SuggestBody(BaseModel):
    """The tree being edited and the keywords to place (every set-aside and « to check » one
    when absent)."""

    tree: dict[str, Any]
    keywords: Annotated[list[Term], Field(max_length=5_000)] | None = None
    top: Annotated[int, Field(ge=1, le=10)] = 3
    scope: Literal["aside", "check", "both"] = "both"


@routes.post("/api/themes/suggestions", action="themes.read")
def suggest(request: Request, body: SuggestBody, ctx: ProjectDep) -> dict[str, Any]:
    """The nodes nearest each keyword: ``{"suggestions": {keyword: [{node, score}]}}``.

    Candidates are the nodes holding keywords on them; *score* is the cosine
    to their centroid. Without ``keywords``: the set-aside keywords, the
    « to check » ones or both (``scope``), at most 5 000.
    """
    from cartolex.lexicon.theme_fit import suggestions

    tree = _parse_tree(body.tree)
    terms, Z = _space(request, ctx)
    doc = tree.model_dump(mode="json", by_alias=True)
    wanted = body.keywords
    if wanted is None:
        aside = list(doc.get("set_aside") or {}) if body.scope != "check" else []
        check = (
            [k for k, v in (doc.get("review") or {}).items() if v == "to_check"]
            if body.scope != "aside"
            else []
        )
        wanted = list(dict.fromkeys(aside + check))[:5_000]
    found = suggestions(doc, terms, Z, wanted, top=body.top)
    return {
        "suggestions": {
            k: [{"node": s.node, "score": s.score} for s in v] for k, v in found.items()
        },
        "measure": "cosine to the centroid of the keywords on each node",
    }
