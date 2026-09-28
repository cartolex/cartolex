# SPDX-License-Identifier: MIT
"""Errors of the API: every one says what happened and what to do next.

Every error response has one shape (V2-016)::

    {"error": {"code": "stale", "message": "…", "next": {"label": "Reload", "action": "reload"}}}

``code`` is a stable key the interface can translate; ``message`` says the
cause in plain words; ``next`` names the action that gets the person out of it:
``label`` in words, ``action`` as a key the interface maps to a route or a
command (:data:`NEXT_ACTIONS`). An error may carry more fields beside those
(``current`` for a stale write, ``problems`` for a refused value).

:func:`install_handlers` turns the exceptions of cartolex's packages
(:class:`~cartolex.project.StaleWrite`, :class:`~cartolex.project.LockHeld`,
:class:`~cartolex.build.BuildBusy`…) into these responses, so a route can
let them rise.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = [
    "NEXT_ACTIONS",
    "ApiError",
    "error_body",
    "error_response",
    "install_handlers",
    "not_found",
]

#: The actions an error's ``next`` may name, and what each means to the interface.
NEXT_ACTIONS: dict[str, str] = {
    "reload": "read the resource again, then apply the change again (reload and merge)",
    "retry": "try the same request again",
    "confirm": "ask the person to confirm, then send the request again with the confirmation",
    "fix-input": "correct the values the message names",
    "open-project": "open or create a project",
    "sign-in": "open the app again from the cartolex command (a new launch link)",
    "wait": "wait for the running job, or cancel it",
    "build": "build the stages the message names",
    "unlock": "remove a stale lock (cartolex project unlock FOLDER)",
    "settings": "change the setting the message names",
    "report": "copy a diagnostic and report the problem",
    "none": "nothing to do",
}


def error_body(
    code: str,
    message: str,
    *,
    next_label: str = "",
    next_action: str = "none",
    **extra: Any,
) -> dict[str, Any]:
    """The body of an error response."""
    if next_action not in NEXT_ACTIONS:
        raise ValueError(f"unknown next action {next_action!r}")
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "next": {"label": next_label or _DEFAULT_LABELS[next_action], "action": next_action},
    }
    error.update(extra)
    return {"error": error}


_DEFAULT_LABELS = {
    "reload": "Reload",
    "retry": "Try again",
    "confirm": "Confirm",
    "fix-input": "Correct the values",
    "open-project": "Open a project",
    "sign-in": "Open the app again",
    "wait": "See the running job",
    "build": "Build",
    "unlock": "Remove the stale lock",
    "settings": "Open the settings",
    "report": "Copy a diagnostic",
    "none": "Close",
}


class ApiError(Exception):
    """An error to answer with: its HTTP status, code, message and next action."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        next_action: str = "none",
        next_label: str = "",
        headers: Mapping[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.next_action = next_action
        self.next_label = next_label
        self.headers = dict(headers or {})
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return error_body(
            self.code,
            self.message,
            next_label=self.next_label,
            next_action=self.next_action,
            **self.extra,
        )


def error_response(error: ApiError) -> JSONResponse:
    return JSONResponse(error.body(), status_code=error.status, headers=error.headers)


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} does not exist", next_action="reload")


def _translate(exc: Exception) -> ApiError | None:
    """The API error for an exception of cartolex's packages, or ``None``."""
    from cartolex.build import BuildBusy
    from cartolex.build.params import ParamsError
    from cartolex.project import LockHeld, NotAProject, StaleLock, StaleWrite, UnsupportedFormat
    from cartolex.project.project import IdentityFrozen
    from cartolex.project.tables import TableError
    from cartolex.project.themes import ThemeEditError

    from .etags import etag_of, version_of

    if isinstance(exc, StaleWrite):
        return ApiError(
            412,
            "stale",
            f"{exc.path.name} changed since it was read; reload it and apply the change again",
            next_action="reload",
            headers={"ETag": etag_of(exc.found)},
            current=version_of(exc.found),
        )
    if isinstance(exc, StaleLock):
        return ApiError(409, "stale_lock", str(exc), next_action="unlock")
    if isinstance(exc, LockHeld):
        return ApiError(
            409,
            "locked",
            str(exc),
            next_action="open-project",
            next_label="Close it there, or open another project",
        )
    if isinstance(exc, NotAProject):
        return ApiError(404, "not_a_project", str(exc), next_action="open-project")
    if isinstance(exc, UnsupportedFormat):
        return ApiError(409, "unsupported_format", str(exc), next_action="open-project")
    if isinstance(exc, IdentityFrozen):
        return ApiError(
            409,
            "identity_frozen",
            str(exc).replace("pass identity_change=True", "confirm the change"),
            next_action="confirm",
            next_label="Change it anyway",
            changed=list(exc.changed),
        )
    if isinstance(exc, ParamsError):
        return ApiError(
            422,
            "invalid_parameters",
            "; ".join(exc.problems),
            next_action="fix-input",
            problems=list(exc.problems),
        )
    if isinstance(exc, ThemeEditError):
        return ApiError(422, "refused", str(exc), next_action="fix-input")
    if isinstance(exc, BuildBusy):
        return ApiError(409, "busy", str(exc), next_action="wait")
    if isinstance(exc, TableError):
        return ApiError(422, "invalid_file", str(exc), next_action="report")
    return None


def install_handlers(app: FastAPI) -> None:
    """Answer every error of *app* with the error shape."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = []
        for e in exc.errors():
            where = ".".join(str(p) for p in e.get("loc", ()) if p not in ("body", "query"))
            problems.append(f"{where}: {e.get('msg', 'invalid')}" if where else e.get("msg", ""))
        return error_response(
            ApiError(
                422,
                "invalid",
                "the request is not valid: " + "; ".join(problems),
                next_action="fix-input",
                problems=problems,
            )
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed", 413: "too_large"}
        code = codes.get(exc.status_code, "http_error")
        message = str(exc.detail) if exc.detail else code.replace("_", " ")
        if exc.status_code == 404:
            message = "no such address in this app"
        return error_response(ApiError(exc.status_code, code, message, headers=exc.headers))

    from cartolex.build import BuildBusy
    from cartolex.build.params import ParamsError
    from cartolex.project import LockHeld, NotAProject, StaleWrite, UnsupportedFormat
    from cartolex.project.project import IdentityFrozen
    from cartolex.project.tables import TableError
    from cartolex.project.themes import ThemeEditError

    async def _known(_: Request, exc: Exception) -> JSONResponse:
        translated = _translate(exc)
        assert translated is not None
        return error_response(translated)

    for cls in (
        StaleWrite,
        LockHeld,
        NotAProject,
        UnsupportedFormat,
        IdentityFrozen,
        ParamsError,
        ThemeEditError,
        BuildBusy,
        TableError,
    ):
        app.add_exception_handler(cls, _known)

    @app.exception_handler(Exception)
    async def _any(request: Request, exc: Exception) -> JSONResponse:
        # Reached through the server-error layer, which logs the exception again after
        # this response is sent; the message never holds the exception's text.
        logging.getLogger("cartolex.app").error(
            "unexpected error",
            extra={
                "event": "error",
                "request_id": getattr(request.state, "request_id", None),
                "error": type(exc).__name__,
            },
        )
        return error_response(
            ApiError(
                500,
                "internal",
                f"something went wrong inside cartolex ({type(exc).__name__})",
                next_action="report",
            )
        )
