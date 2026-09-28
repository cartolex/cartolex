# SPDX-License-Identifier: MIT
"""The app factory: :func:`create_app` builds the ASGI app of one local user or a service."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute

from .errors import install_handlers
from .extensions import Extension, ExtensionError, combine
from .routing import Guard
from .runtime import Runtime
from .security import SecurityMiddleware
from .settings import AppSettings

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = ["create_app"]

_PROJECT_PREFIX = re.compile(r"^/api/projects/([a-z0-9][a-z0-9_-]{0,63})(/.+)$")
#: Words after ``/api/projects/`` that are routes, not project ids.
_RESERVED = frozenset({"open", "close", "recent", "current"})


class ProjectPrefix:
    """Hosted: ``/api/projects/<id>/<rest>`` works on project ``<id>`` as ``/api/<rest>``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            m = _PROJECT_PREFIX.match(scope.get("path", ""))
            if m and m.group(1) not in _RESERVED:
                rest = "/api" + m.group(2)
                scope = {**scope, "path": rest, "raw_path": rest.encode("utf-8")}
                scope.setdefault("state", {})["cartolex.project"] = m.group(1)
        await self.app(scope, receive, send)


def _add_extension_route(app: FastAPI, route: Any, prefix: str, guard: Any, ext_id: str) -> None:
    """Add an extension's route under *prefix*, guarded; only API routes are accepted."""
    if not isinstance(route, APIRoute):
        raise ExtensionError(
            f"extension {ext_id!r}: {getattr(route, 'path', route)!r} is not an API route "
            "(serve files through static_dir)"
        )
    app.add_api_route(
        prefix + route.path,
        route.endpoint,
        methods=sorted(route.methods or ()),
        dependencies=[guard, *route.dependencies],
        response_model=route.response_model,
        status_code=route.status_code,
        tags=[*route.tags, f"ext:{ext_id}"],
        summary=route.summary,
        description=route.description,
        name=f"ext.{ext_id}.{route.name}",
        include_in_schema=route.include_in_schema,
        response_class=route.response_class,
    )


def create_app(
    settings: AppSettings | None = None, extensions: Sequence[Extension] = ()
) -> FastAPI:
    """The ASGI app for *settings*, with the host application's *extensions*.

    Without extensions it is cartolex's own app. Locally, the project of
    ``settings.project`` is opened for writing now (its lock is taken; a held
    lock raises :class:`~cartolex.project.LockHeld`) and closed when the app
    stops. Extensions are checked (:class:`~cartolex.app.ExtensionError`).
    """
    from cartolex.project.project import cartolex_version

    from .routes import ROUTERS
    from .static_files import routes as static_routes

    settings = settings or AppSettings()
    combined = combine(extensions)
    runtime = Runtime(settings, combined)
    if settings.project is not None and not settings.hosted:
        runtime.projects.open(settings.project)  # type: ignore[attr-defined]

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            runtime.shutdown()

    app = FastAPI(
        title=(combined.branding.name if combined.branding and combined.branding.name else None)
        or "cartolex",
        version=cartolex_version(),
        description="The API of cartolex's app: the contracts its screens use.",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.cartolex = runtime
    install_handlers(app)
    # Every route is added to the app itself, so ``app.routes`` lists each one with its guard
    # (the tests check that no route skips ``authorize``).
    for router in ROUTERS:
        app.router.routes.extend(router.routes)
    for ext in combined.extensions:
        guard = Depends(Guard(f"ext.{ext.id}", "extension"))
        for router in ext.routers:
            for route in router.routes:
                _add_extension_route(app, route, f"/api/ext/{ext.id}", guard, ext.id)
    app.router.routes.extend(static_routes.router.routes)
    for ext in reversed(combined.extensions):
        for middleware in reversed(ext.middlewares):
            app.add_middleware(middleware.cls, *middleware.args, **middleware.kwargs)
    if settings.hosted:
        app.add_middleware(ProjectPrefix)
    app.add_middleware(SecurityMiddleware, settings=settings)
    return app
