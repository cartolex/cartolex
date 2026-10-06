# SPDX-License-Identifier: MIT
"""The app's routes, one module per screen or service; ``docs/dev/api.md`` describes them."""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    ai_proposals,
    app_routes,
    atlas,
    build,
    collaborators,
    collection,
    copilot,
    corpus,
    duplicates,
    jobs,
    keywords,
    machine,
    maps,
    me,
    method,
    organisations,
    overview,
    params,
    people,
    playground,
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
        method,
        maps,
        snapshots,
        duplicates,
        organisations,
        corpus,
        people,
        collection,
        collaborators,
        sources,
        keywords,
        themes,
        themes_fit,
        playground,
        atlas,
        share,
        settings,
        project_tools,
        machine,
        ai_proposals,
        copilot,
    )
]
