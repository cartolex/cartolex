# SPDX-License-Identifier: MIT
"""The lexicon: the keywords the last vocabulary build made (paged), their CSV and word cloud.

The rows come from :mod:`cartolex.app.lexicon_view`; a change to them is a keyword
decision (``POST /api/keywords/decisions`` on the candidates a row gathers), applied by
the next build.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Query, Request, Response

from ..deps import ListDep, ProjectDep, page
from ..messages import empty
from ..routing import Routes, runtime_of

routes = Routes(tags=["keywords"])

Language = Annotated[str, Query(pattern=r"^[a-z]{2}$")]
Category = Literal["concept", "method", "object", "place", "field", "none"]
#: Twelve colours, ``rrggbb`` separated by commas (the hue families of a colour scheme).
_HUES = r"^[0-9a-fA-F]{6}(,[0-9a-fA-F]{6}){11}$"


def _data(request: Request, ctx: Any) -> dict[str, Any] | None:
    from ..lexicon_view import lexicon

    return lexicon(runtime_of(request), ctx)


@routes.get("/api/keywords/lexicon", action="keywords.read")
def lexicon_list(
    request: Request,
    ctx: ProjectDep,
    params: ListDep,
    language: Language = "en",
    category: Annotated[Category | None, Query()] = None,
    theme: Annotated[str | None, Query(max_length=80)] = None,
) -> dict[str, Any]:
    """The keywords of the last vocabulary build, each with its term per display language,
    rank and score, people, texts, category, theme (in *language*), forms and candidates;
    paged, sorted and filtered (``q``: a term or a form; ``category``; ``theme``: a
    top-level node) here."""
    from ..lexicon_view import node_label

    data = _data(request, ctx)
    if data is None:
        return page(
            [],
            params,
            sorts={"rank": lambda v: v},
            default_sort="rank",
            empty=empty("empty_no_keywords"),
            extra={"run": None},
        )
    nodes = data["nodes"]
    q = params.q.strip().casefold()

    def keep(item: dict[str, Any]) -> bool:
        if category is not None and (item["category"] or "none") != category:
            return False
        if theme is not None and (nodes.get(item["node"] or "") or {}).get("top") != theme:
            return False
        if q:
            words = [*item["terms"].values(), item["concept"], *item["forms"]]
            return any(q in w.casefold() for w in words)
        return True

    items = [
        {
            **item,
            "theme": node_label(nodes, item["node"], language),
            "hue": (nodes.get(item["node"] or "") or {}).get("hue"),
        }
        for item in data["items"]
        if keep(item)
    ]
    shown = language if language in data["languages"] else data["languages"][0]
    tops = sorted({n["top"] for n in nodes.values()}, key=lambda n: list(nodes).index(n))
    return page(
        items,
        params,
        sorts={
            "rank": lambda v: v["rank"],
            "term": lambda v: v["terms"].get(shown, v["concept"]).casefold(),
            "people": lambda v: (v["people"], -v["rank"]),
            "texts": lambda v: (v["texts"], -v["rank"]),
            "category": lambda v: (v["category"] or "~", v["rank"]),
            "theme": lambda v: (v["theme"] or "~", v["rank"]),
        },
        default_sort="rank",
        filters={"category": category, "theme": theme, "q": params.q},
        empty=empty("empty_no_match"),
        extra={
            "run": data["run"],
            "languages": data["languages"],
            "keywords": len(data["items"]),
            "themes": [{"id": t, "label": node_label(nodes, t, language)} for t in tops],
        },
    )


@routes.get("/api/keywords/lexicon/export", action="keywords.read")
def lexicon_export(request: Request, ctx: ProjectDep, language: Language = "en") -> Response:
    """The whole lexicon as CSV (``lexicon.csv``)."""
    from ..errors import ApiError
    from ..lexicon_view import lexicon_csv

    data = _data(request, ctx)
    if data is None:
        raise ApiError.of("no_keywords")
    return Response(
        lexicon_csv(data, language).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="lexicon.csv"'},
    )


@routes.get("/api/keywords/lexicon/cloud", action="keywords.read")
def lexicon_cloud(
    request: Request,
    ctx: ProjectDep,
    by: Literal["score", "people"] = "score",
    theme: Literal["light", "dark"] = "light",
    colour: Literal["theme", "category"] = "theme",
    language: Language = "en",
    hues: Annotated[str | None, Query(max_length=83, pattern=_HUES)] = None,
) -> Response:
    """The word cloud of the lexicon's most important keywords (by score or by people), as
    SVG for a light or a dark page, coloured by theme or by category (*hues*: the twelve
    colours of the person's colour scheme, ``rrggbb`` separated by commas); cached by the
    build."""
    from ..errors import ApiError
    from ..lexicon_view import word_cloud

    palette = tuple(f"#{h.lower()}" for h in hues.split(",")) if hues else ()
    svg = word_cloud(
        runtime_of(request), ctx, by=by, theme=theme, colour=colour, language=language,
        hues=palette,
    )  # fmt: skip
    if svg is None:
        raise ApiError.of("no_keywords")
    return Response(
        svg.encode("utf-8"),
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=3600"},
    )
