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
    marked reviewed (« keep here ») are left out unless ``reviewed``. Sorts:
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
        if (body.reviewed or review.get(b.keyword) != "reviewed")
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
