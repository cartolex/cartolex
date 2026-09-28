# SPDX-License-Identifier: MIT
"""Routes that always ask: every route of the app goes through :class:`Guard`.

``Guard(action, resource)`` is a FastAPI dependency. For each request it finds
the principal (the session's, a host's sign-in, or anonymous), checks the CSRF
token of a state-changing request, and calls
:func:`cartolex.app.auth.authorize` with the route's action and resource. A
refusal is **401** without a session and **403** with one, with a plain
message. :class:`Routes` declares routes with their action, so no route can be
added without one; ``tests/test_app_security.py`` checks every route of the
app has a guard.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request

from .auth import ANONYMOUS, PUBLIC_ACTIONS, Principal, Resource, authorize
from .errors import ApiError
from .security import CSRF_HEADER, SAFE_METHODS, same_secret

if TYPE_CHECKING:
    from .runtime import Runtime

__all__ = ["Guard", "Routes", "principal_of", "runtime_of"]


def runtime_of(request: Request) -> Runtime:
    """The app's runtime (its settings, projects, jobs, sessions…)."""
    return request.app.state.cartolex


def principal_of(request: Request) -> Principal:
    """The principal the guard found for this request."""
    return getattr(request.state, "principal", ANONYMOUS)


class Guard:
    """Authorise a request: principal, CSRF on changes, then ``authorize(action, resource)``.

    *resource* is the kind of thing the route acts on: ``app``, ``static``,
    ``project``, ``job`` or ``extension``; *id_param* names the path parameter
    holding the resource's id.
    """

    def __init__(self, action: str, resource: str = "project", id_param: str | None = None):
        self.action = action
        self.resource = resource
        self.id_param = id_param

    def __repr__(self) -> str:
        return f"Guard({self.action!r}, {self.resource!r})"

    async def __call__(self, request: Request) -> Principal:
        runtime = runtime_of(request)
        route = request.scope.get("route")
        state = request.scope.setdefault("state", {})
        state["cartolex.route"] = getattr(route, "path", None) or "(unrouted)"
        session, principal = runtime.session_of(request)
        public = self.action in PUBLIC_ACTIONS
        if request.method not in SAFE_METHODS and not public:
            given = request.headers.get(CSRF_HEADER)
            if session is None:
                raise ApiError(
                    401,
                    "sign_in",
                    "this browser has no session with cartolex: open the app from the cartolex "
                    "command (its launch link)",
                    next_action="sign-in",
                )
            if not same_secret(given, session.csrf):
                raise ApiError(
                    403,
                    "csrf",
                    f"the change was refused: the {CSRF_HEADER} header is missing or wrong "
                    "(reload the page)",
                    next_action="reload",
                )
        project_id = None
        if self.resource == "project":
            project_id = runtime.projects.project_id(request, principal)
        resource_id = request.path_params.get(self.id_param) if self.id_param else None
        resource = Resource(self.resource, resource_id, project_id)
        decision = authorize(runtime.authorizer, principal, self.action, resource)
        if not decision.allowed:
            if principal.anonymous:
                raise ApiError(
                    401,
                    "sign_in",
                    "sign in first: open the app from the cartolex command (its launch link)",
                    next_action="sign-in",
                )
            raise ApiError(403, "forbidden", decision.reason, next_action="none")
        request.state.principal = principal
        return principal


class Routes:
    """An :class:`~fastapi.APIRouter` whose every route names its action.

    ``routes.get("/keywords", action="keywords.read")`` declares a route
    guarded by ``Guard("keywords.read", "project")``.
    """

    def __init__(self, prefix: str = "", tags: list[str] | None = None) -> None:
        self.router = APIRouter(prefix=prefix, tags=tags or [])

    def _route(self, method: str, path: str, *, action: str, resource: str, **kw: Any) -> Callable:
        id_param = kw.pop("id_param", None)
        deps = [Depends(Guard(action, resource, id_param)), *kw.pop("dependencies", [])]
        return self.router.api_route(path, methods=[method], dependencies=deps, **kw)

    def get(self, path: str, *, action: str, resource: str = "project", **kw: Any) -> Callable:
        return self._route("GET", path, action=action, resource=resource, **kw)

    def post(self, path: str, *, action: str, resource: str = "project", **kw: Any) -> Callable:
        return self._route("POST", path, action=action, resource=resource, **kw)

    def put(self, path: str, *, action: str, resource: str = "project", **kw: Any) -> Callable:
        return self._route("PUT", path, action=action, resource=resource, **kw)

    def patch(self, path: str, *, action: str, resource: str = "project", **kw: Any) -> Callable:
        return self._route("PATCH", path, action=action, resource=resource, **kw)

    def delete(self, path: str, *, action: str, resource: str = "project", **kw: Any) -> Callable:
        return self._route("DELETE", path, action=action, resource=resource, **kw)
