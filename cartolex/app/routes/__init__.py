# SPDX-License-Identifier: MIT
"""The app's routes, one module per screen or service; ``docs/dev/api.md`` describes them."""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    app_routes,
    atlas,
    build,
    collaborators,
    collection,
    corpus,
    handoff,
    jobs,
    keywords,
    machine,
    maps,
    me,
    overview,
    params,
    people,
    project_tools,
    projects,
    settings,
    share,
    snapshots,
    sources,
    state,
    themes,
    themes_fit,
)

#: Every router of the app, in the order they are mounted (the interface's files come last).
ROUTERS: list[APIRouter] = [
    m.routes.router
    for m in (
        app_routes,
        me,
        projects,
        state,
        overview,
        build,
        jobs,
        params,
        maps,
        snapshots,
        corpus,
        people,
        collection,
        collaborators,
        sources,
        keywords,
        themes,
        themes_fit,
        atlas,
        share,
        settings,
        project_tools,
        machine,
        handoff,
    )
]
