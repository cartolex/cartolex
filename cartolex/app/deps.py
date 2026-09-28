# SPDX-License-Identifier: MIT
"""Dependencies routes share: the project context and the list parameters."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Annotated, Any

from fastapi import Depends, Query, Request

from .errors import ApiError
from .projects import ProjectContext
from .routing import principal_of, runtime_of

__all__ = [
    "MAX_LIMIT",
    "ListDep",
    "ListParams",
    "ProjectDep",
    "empty_hint",
    "list_params",
    "page",
    "project_context",
]

#: The most rows one page of a list holds.
MAX_LIMIT = 500


def project_context(request: Request) -> ProjectContext:
    """The project this request works on (409 when none is open)."""
    runtime = runtime_of(request)
    return ProjectContext(runtime.projects.resolve(request, principal_of(request)))


ProjectDep = Annotated[ProjectContext, Depends(project_context)]


class ListParams:
    """Paging, sorting and a text filter, read from the query."""

    def __init__(self, offset: int, limit: int, sort: str | None, q: str | None) -> None:
        self.offset = offset
        self.limit = limit
        self.sort = sort
        self.q = (q or "").strip().casefold()


def list_params(
    offset: Annotated[int, Query(ge=0, le=10_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    sort: Annotated[str | None, Query(max_length=64, pattern=r"^-?[a-z_]+$")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> ListParams:
    return ListParams(offset, limit, sort, q)


ListDep = Annotated[ListParams, Depends(list_params)]


def empty_hint(message: str, label: str, action: str) -> dict[str, Any]:
    """What an empty list says to do next (V2-013)."""
    return {"message": message, "next": {"label": label, "action": action}}


def page(
    items: Sequence[dict[str, Any]],
    params: ListParams,
    *,
    sorts: dict[str, Callable[[dict[str, Any]], Any]],
    default_sort: str,
    filters: dict[str, Any] | None = None,
    empty: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One page of *items* sorted on the server: ``{"items", "total", "offset", …}``.

    *sorts* maps each sort name to its key; ``-name`` sorts descending. An
    empty result carries *empty*, what to do next.
    """
    sort = params.sort or default_sort
    name = sort.lstrip("-")
    if name not in sorts:
        raise ApiError(
            422,
            "invalid",
            f"cannot sort by {name!r}; sort by one of {sorted(sorts)}",
            next_action="fix-input",
        )
    key = sorts[name]

    def safe(item: dict[str, Any]) -> tuple[int, Any]:
        value = key(item)
        return (1, 0) if value is None else (0, value)

    ordered = sorted(items, key=safe, reverse=sort.startswith("-"))
    chunk = ordered[params.offset : params.offset + params.limit]
    return {
        "items": chunk,
        "total": len(ordered),
        "offset": params.offset,
        "limit": params.limit,
        "sort": sort,
        "sorts": sorted(sorts),
        "filters": {k: v for k, v in (filters or {}).items() if v not in (None, "")},
        "empty": empty if not ordered else None,
        **(extra or {}),
    }
