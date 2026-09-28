# SPDX-License-Identifier: MIT
"""The app's routes, one module per screen or service; ``docs/dev/api.md`` describes them."""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    app_routes,
    atlas,
    build,
    collection,
    handoff,
    jobs,
    keywords,
    maps,
    params,
    people,
    projects,
    settings,
    share,
    snapshots,
    sources,
    state,
    themes,
)

#: Every router of the app, in the order they are mounted (the interface's files come last).
ROUTERS: list[APIRouter] = [
    m.routes.router
    for m in (
        app_routes,
        projects,
        state,
        build,
        jobs,
        params,
        maps,
        snapshots,
        people,
        collection,
        sources,
        keywords,
        themes,
        atlas,
        share,
        settings,
        handoff,
    )
]
