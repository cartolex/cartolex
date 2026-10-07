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
    distances,
    duplicate_groups,
    duplicates,
    jobs,
    keywords,
    lexicon,
    machine,
    maps,
    me,
    method,
    notices,
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
        notices,
        projects,
        state,
        overview,
        build,
        jobs,
        params,
        method,
        maps,
        snapshots,
        duplicate_groups,
        duplicates,
        organisations,
        corpus,
        people,
        collection,
        collaborators,
        sources,
        lexicon,
        keywords,
        themes,
        themes_fit,
        playground,
        atlas,
        distances,
        share,
        settings,
        project_tools,
        machine,
        ai_proposals,
        copilot,
    )
]
