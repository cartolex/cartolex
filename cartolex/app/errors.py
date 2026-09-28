# SPDX-License-Identifier: MIT
"""Errors of the API: every one says what happened and what to do next, in any language.

Every error response has one shape (V2-016)::

    {"error": {"code": "stale", "params": {"file": "themes.json"},
               "message": "themes.json changed since it was read; reload it …",
               "next": {"label": "Reload", "action": "reload"}}}

``code`` is a stable key and ``params`` the values its message names: the
interface shows the text of the code from its own catalogues (English, French,
Portuguese), filled with the params; ``message`` is the same text in English,
the fallback. ``next`` names the action that gets the person out of it:
``label`` in English, ``action`` a key the interface maps to a route or a
command (:data:`NEXT_ACTIONS`). An error may carry more fields beside those
(``current`` for a stale write, ``job`` for a busy project).

Every code is declared once in :data:`ERRORS`, with its status, its English
template and its next action; :meth:`ApiError.of` builds an error from it, so
a code always means the same message. ``docs/dev/api.md`` lists them.

:func:`install_handlers` turns the exceptions of cartolex's packages
(:class:`~cartolex.project.StaleWrite`, :class:`~cartolex.project.LockHeld`,
:class:`~cartolex.build.BuildBusy`…) into these responses, so a route can
let them rise.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = [
    "ERRORS",
    "NEXT_ACTIONS",
    "ApiError",
    "ErrorKind",
    "body_of",
    "error_body",
    "error_response",
    "install_handlers",
    "render",
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


@dataclass(frozen=True)
class ErrorKind:
    """One error code: its HTTP status, its English template (``{param}``), its next action."""

    status: int
    template: str
    next_action: str = "none"
    next_label: str = ""


#: Every error code of the API. The template's ``{names}`` are the error's params.
ERRORS: dict[str, ErrorKind] = {
    # the app, sessions and security
    "host_refused": ErrorKind(
        400,
        "this address is not one cartolex answers to; open it from the address the cartolex "
        "command gives",
        "sign-in",
    ),
    "cross_origin": ErrorKind(
        403, "a change was asked from another site; cartolex takes changes from its own pages only"
    ),
    "request_too_large": ErrorKind(
        413, "the request is larger than the limit ({limit_kb} KB)", "fix-input"
    ),
    "invalid_length": ErrorKind(400, "the request's length is not a number"),
    "sign_in": ErrorKind(
        401, "sign in first: open the app from the cartolex command (its launch link)", "sign-in"
    ),
    "csrf": ErrorKind(
        403,
        "the change was refused: the {header} header is missing or wrong (reload the page)",
        "reload",
    ),
    "forbidden": ErrorKind(403, "{reason}"),
    "version_required": ErrorKind(
        428,
        "this change needs the version you read: send it in If-Match (the ETag of the read)",
        "reload",
    ),
    "version_ambiguous": ErrorKind(400, "If-Match names one version", "reload"),
    "stale": ErrorKind(
        412, "{file} changed since it was read; reload it and apply the change again", "reload"
    ),
    "invalid": ErrorKind(422, "the request is not valid: {problems}", "fix-input"),
    "no_route": ErrorKind(404, "no such address in this app"),
    "method_not_allowed": ErrorKind(405, "this address does not take this method"),
    "http_error": ErrorKind(400, "the request was refused ({status})"),
    "internal": ErrorKind(500, "something went wrong inside cartolex ({error_type})", "report"),
    "static_missing": ErrorKind(404, "no such file in the interface", "reload"),
    "unknown_locale": ErrorKind(
        422, "{locale} is not an interface language; choose among {locales}", "fix-input"
    ),
    "invalid_sort": ErrorKind(422, "cannot sort by {sort}; sort by one of {sorts}", "fix-input"),
    # projects
    "no_project": ErrorKind(409, "no project is open: open one, or create one", "open-project"),
    "project_not_named": ErrorKind(
        400, "name the project in the address: /api/projects/<id>/…", "open-project"
    ),
    "invalid_project_id": ErrorKind(400, "{id} is not a project id", "fix-input"),
    "project_not_found": ErrorKind(404, "there is no project {id}", "open-project"),
    "hosted_projects": ErrorKind(404, "a hosted app names its project in the address"),
    "not_a_project": ErrorKind(404, "{path} holds no cartolex project", "open-project"),
    "unsupported_format": ErrorKind(
        409, "the project is in format {found}; this cartolex reads {expected}", "open-project"
    ),
    "locked": ErrorKind(
        409,
        "the project is open in {app} (process {pid} on {host}, since {since})",
        "open-project",
        "Close it there, or open another project",
    ),
    "stale_lock": ErrorKind(
        409,
        "the project's lock is stale: {app} (process {pid}, since {since}) no longer runs on "
        "this computer; remove it if no other window has the project open",
        "unlock",
    ),
    "project_exists": ErrorKind(
        409, "the folder already holds a project, or is not empty: {path}", "fix-input"
    ),
    "project_folder_missing": ErrorKind(422, "choose the folder of the new project", "fix-input"),
    "project_folder_relative": ErrorKind(
        422, "the project's folder is a full path: {path}", "fix-input"
    ),
    "project_id_missing": ErrorKind(422, "a hosted project needs an id", "fix-input"),
    "field_title_missing": ErrorKind(
        422,
        "name the field the map covers (its title): the AI receives it with the terms",
        "fix-input",
    ),
    "no_language_pack": ErrorKind(
        422, "cartolex has no language pack for {languages}; choose among {available}", "fix-input"
    ),
    "identity_frozen": ErrorKind(
        409,
        "the project's identity is frozen: changing its {changed} means cached AI answers are "
        "not reused (they are paid for again) or texts are parsed again; confirm the change to "
        "make it anyway",
        "confirm",
        "Change it anyway",
    ),
    "invalid_file": ErrorKind(422, "a file of the project is not valid: {detail}", "report"),
    "nothing_to_change": ErrorKind(422, "nothing to change", "fix-input"),
    # jobs and building
    "busy": ErrorKind(
        409,
        "a {kind} job ({job}) is already running on this project; wait for it or cancel it",
        "wait",
    ),
    "stages_running": ErrorKind(409, "a job is running {stages}; wait for it or cancel it", "wait"),
    "unknown_scope": ErrorKind(
        422, "{item} is neither a stage nor an area; stages: {stages}", "fix-input"
    ),
    "invalid_parameters": ErrorKind(422, "the parameters were refused: {problems}", "fix-input"),
    "job_not_found": ErrorKind(404, "there is no job {job}", "reload"),
    "job_ended": ErrorKind(409, "the job has already ended ({state})"),
    "job_elsewhere": ErrorKind(409, "this job runs in another process; stop it there"),
    # map versions and snapshots
    "map_version_not_found": ErrorKind(404, "there is no map version {version}", "reload"),
    "map_version_missing": ErrorKind(422, "name the version to {action}", "fix-input"),
    "map_version_pinned": ErrorKind(
        409, "{version} is pinned: pin another version before discarding it", "fix-input"
    ),
    "no_pinned_version": ErrorKind(
        409, "there is no pinned map version to start from: build the map first", "build"
    ),
    "no_versions": ErrorKind(404, "{file} has no versions; files with versions: {files}"),
    "file_not_written": ErrorKind(404, "{file} does not exist yet"),
    "version_not_found": ErrorKind(404, "there is no version {version} of {file}", "reload"),
    "already_current": ErrorKind(409, "this version is already the current one"),
    # people and collection
    "unknown_people": ErrorKind(404, "unknown person id(s): {ids}", "reload"),
    "unknown_set": ErrorKind(422, "there is no projected set {set}; sets: {sets}", "fix-input"),
    "set_needed": ErrorKind(
        422,
        "a projected person belongs to a projected set: add one in the settings first",
        "settings",
    ),
    "self_merge": ErrorKind(422, "a person cannot be merged into themselves", "fix-input"),
    "merged_target": ErrorKind(
        409, "{target} is itself merged into {into}: merge into that person", "fix-input"
    ),
    "file_missing": ErrorKind(422, "send the file in a form, as 'file'", "fix-input"),
    "list_body": ErrorKind(
        422, "send the list in a form (as 'file'), or as {{\"text\": …}}", "fix-input"
    ),
    "empty_list": ErrorKind(422, "the list holds nobody", "fix-input"),
    "import_not_found": ErrorKind(404, "this import is not waiting any more", "reload"),
    "mapping_unknown_fields": ErrorKind(
        422, "unknown field(s) {fields}; the fields are {known}", "fix-input"
    ),
    "mapping_unknown_columns": ErrorKind(422, "the list has no column(s) {columns}", "fix-input"),
    "mapping_no_name": ErrorKind(
        422, "map a column to last_name, or to name (a full name)", "fix-input"
    ),
    "unknown_role": ErrorKind(422, "{role} is not a role", "fix-input"),
    "collection_unavailable": ErrorKind(409, "collecting texts is not available in this version"),
    "no_slot": ErrorKind(
        409, "the project has no slot to collect into: add one in the settings", "settings"
    ),
    "collection_not_running": ErrorKind(409, "no collection is running"),
    "person_not_found": ErrorKind(404, "there is no person {person}", "reload"),
    "invalid_record": ErrorKind(
        422,
        "a record is scheme:id (orcid:0000-0002-1825-0097, openalex:A123…) or an ORCID iD",
        "fix-input",
    ),
    "not_a_candidate": ErrorKind(
        409, "this record is not a candidate of this person; paste an id instead", "fix-input"
    ),
    "no_candidates": ErrorKind(409, "none of these people has a candidate record"),
    # sources and uploads
    "slot_not_found": ErrorKind(404, "the project has no slot {slot}"),
    "slot_collected": ErrorKind(409, "slot {slot} is filled by collection, not by uploads"),
    "file_too_large": ErrorKind(
        413, "the file is larger than the limit ({limit_mb} MB)", "fix-input"
    ),
    "unsafe_name": ErrorKind(422, "the name {name} leaves its folder", "fix-input"),
    "file_exists": ErrorKind(
        409,
        "{name} is already there; nothing is replaced (rename the file to add it)",
        "fix-input",
    ),
    "not_an_archive": ErrorKind(422, "the file is not a zip archive", "fix-input"),
    "archive_too_many_files": ErrorKind(
        413, "the archive holds more than {max_members} files", "fix-input"
    ),
    "archive_too_large": ErrorKind(
        413, "the archive unpacks to more than {limit_mb} MB", "fix-input"
    ),
    "unsafe_archive_member": ErrorKind(
        422,
        "the archive was refused: a member is not allowed ({problem}); nothing was written",
        "fix-input",
    ),
    "archive_replaces": ErrorKind(
        409, "the archive would replace {name}; nothing was written", "fix-input"
    ),
    "archive_corrupt": ErrorKind(
        422, "a member of the archive is larger than it says", "fix-input"
    ),
    # keywords, themes, the AI handoff
    "not_a_corpus_language": ErrorKind(422, "{language} is not a corpus language", "fix-input"),
    "merge_target_missing": ErrorKind(422, "merge {term} into another keyword", "fix-input"),
    "no_decision": ErrorKind(404, "none of these keywords has a decision", "reload"),
    "invalid_tree": ErrorKind(422, "the tree is not valid: {detail}", "reload"),
    "theme_refused": ErrorKind(422, "the change was refused: {detail}", "fix-input"),
    "theme_step_refused": ErrorKind(422, "step {step} ({op}) was refused: {detail}", "fix-input"),
    "no_keywords": ErrorKind(409, "build the keywords first", "build"),
    "handoff_empty": ErrorKind(404, "no term to send in this band"),
    "invalid_bundle": ErrorKind(422, "the bundle is not valid: {detail}", "fix-input"),
    "proposal_not_found": ErrorKind(404, "there is no proposal {proposal}", "reload"),
    "nothing_chosen": ErrorKind(422, "choose the terms to accept", "fix-input"),
    # sharing
    "not_available": ErrorKind(501, "building the offline site is not available in this version"),
}


def _shown(value: Any) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(v) for v in value)
    return str(value)


def render(code: str, params: Mapping[str, Any]) -> str:
    """The English text of *code* filled with *params*."""
    return ERRORS[code].template.format(**{k: _shown(v) for k, v in params.items()})


def error_body(
    code: str,
    message: str,
    *,
    params: Mapping[str, Any] | None = None,
    next_label: str = "",
    next_action: str = "none",
    **extra: Any,
) -> dict[str, Any]:
    """The body of an error response."""
    if next_action not in NEXT_ACTIONS:
        raise ValueError(f"unknown next action {next_action!r}")
    error: dict[str, Any] = {
        "code": code,
        "params": dict(params or {}),
        "message": message,
        "next": {"label": next_label or _DEFAULT_LABELS[next_action], "action": next_action},
    }
    error.update(extra)
    return {"error": error}


def body_of(code: str, **params: Any) -> dict[str, Any]:
    """The error body of catalogued *code* (for the layers below the routes)."""
    kind = ERRORS[code]
    return error_body(
        code,
        render(code, params),
        params=params,
        next_label=kind.next_label,
        next_action=kind.next_action,
    )


class ApiError(Exception):
    """An error to answer with: its status, code, params, message and next action.

    Build it with :meth:`of` from a code of :data:`ERRORS`.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        params: Mapping[str, Any] | None = None,
        next_action: str = "none",
        next_label: str = "",
        headers: Mapping[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.params = dict(params or {})
        self.message = message
        self.next_action = next_action
        self.next_label = next_label
        self.headers = dict(headers or {})
        self.extra = extra

    @classmethod
    def of(
        cls,
        code: str,
        *,
        headers: Mapping[str, str] | None = None,
        extra: Mapping[str, Any] | None = None,
        **params: Any,
    ) -> ApiError:
        """The error of catalogued *code*, its template filled with *params*."""
        kind = ERRORS[code]
        return cls(
            kind.status,
            code,
            render(code, params),
            params=params,
            next_action=kind.next_action,
            next_label=kind.next_label,
            headers=headers,
            **dict(extra or {}),
        )

    def body(self) -> dict[str, Any]:
        return error_body(
            self.code,
            self.message,
            params=self.params,
            next_label=self.next_label,
            next_action=self.next_action,
            **self.extra,
        )


def error_response(error: ApiError) -> JSONResponse:
    return JSONResponse(error.body(), status_code=error.status, headers=error.headers)


def _translate(exc: Exception) -> ApiError | None:
    """The API error for an exception of cartolex's packages, or ``None``."""
    from cartolex.build import BuildBusy
    from cartolex.build.params import ParamsError
    from cartolex.project import LockHeld, NotAProject, StaleLock, StaleWrite, UnsupportedFormat
    from cartolex.project.project import FORMAT, IdentityFrozen
    from cartolex.project.tables import TableError
    from cartolex.project.themes import ThemeEditError

    from .etags import etag_of, version_of

    if isinstance(exc, StaleWrite):
        return ApiError.of(
            "stale",
            file=exc.path.name,
            headers={"ETag": etag_of(exc.found)},
            extra={"current": version_of(exc.found)},
        )
    if isinstance(exc, StaleLock):
        info = exc.info
        return ApiError.of(
            "stale_lock",
            app=info.app if info else "",
            pid=info.pid if info else "",
            since=info.since if info else "",
        )
    if isinstance(exc, LockHeld):
        info = exc.info
        return ApiError.of(
            "locked",
            app=info.app if info else "another application",
            pid=info.pid if info else "?",
            host=info.host if info else "?",
            since=info.since if info else "?",
        )
    if isinstance(exc, NotAProject):
        return ApiError.of("not_a_project", path=str(exc).split(" holds ", 1)[0])
    if isinstance(exc, UnsupportedFormat):
        return ApiError.of("unsupported_format", found=exc.found, expected=FORMAT)
    if isinstance(exc, IdentityFrozen):
        return ApiError.of("identity_frozen", changed=list(exc.changed))
    if isinstance(exc, ParamsError):
        return ApiError.of("invalid_parameters", problems=list(exc.problems))
    if isinstance(exc, ThemeEditError):
        return ApiError.of("theme_refused", detail=str(exc))
    if isinstance(exc, BuildBusy):
        running = str(exc).removeprefix("a job is running ").split(";", 1)[0]
        return ApiError.of("stages_running", stages=running.split(", "))
    if isinstance(exc, TableError):
        return ApiError.of("invalid_file", detail=str(exc))
    return None


def install_handlers(app: FastAPI) -> None:
    """Answer every error of *app* with the error shape."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems, fields = [], []
        for e in exc.errors():
            where = ".".join(str(p) for p in e.get("loc", ()) if p not in ("body", "query"))
            fields.append(where)
            problems.append(f"{where}: {e.get('msg', 'invalid')}" if where else e.get("msg", ""))
        return error_response(ApiError.of("invalid", problems=problems, fields=fields))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            error = ApiError.of("no_route")
        elif exc.status_code == 405:
            error = ApiError.of("method_not_allowed", headers=exc.headers)
        else:
            error = ApiError.of("http_error", status=exc.status_code, headers=exc.headers)
            error.status = exc.status_code
        return error_response(error)

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
        return error_response(ApiError.of("internal", error_type=type(exc).__name__))
