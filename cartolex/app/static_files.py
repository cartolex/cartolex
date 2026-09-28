# SPDX-License-Identifier: MIT
"""The interface's files, the extensions' files and the shell of every page.

Files are served from ``cartolex/app/static/`` (or the settings' folder) at
``/static/``, and from each extension's ``static_dir`` at
``/static/ext/<id>/``. The media type comes from a fixed table, never from the
platform (``.js`` is ``text/javascript`` everywhere); a path that leaves its
folder, names a hidden file or a type not in the table is not found. Every
other address that is not under ``/api`` or ``/static`` (``/keywords``,
``/themes/n7``) is answered with the shell, ``index.html``: the interface routes
in the browser (history routes).
"""

from __future__ import annotations

import html
import json
from pathlib import Path, PurePosixPath

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from .errors import ApiError
from .routing import Routes, runtime_of

__all__ = ["MEDIA_TYPES", "PACKAGE_STATIC", "routes", "safe_file"]

PACKAGE_STATIC = Path(__file__).with_name("static")

#: The media type of each file type served; other types are not served.
MEDIA_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}


def safe_file(root: Path, rel: str) -> Path | None:
    """The file *rel* inside *root*, or ``None`` when it leaves it, is hidden or not served."""
    if not rel or "\\" in rel or "\x00" in rel:
        return None
    parts = PurePosixPath(rel).parts
    if not parts or PurePosixPath(rel).is_absolute():
        return None
    if any(p.startswith(".") for p in parts):
        return None
    try:
        base = Path(root).resolve()
        path = (base / rel).resolve()
        path.relative_to(base)
    except (OSError, ValueError):
        return None
    if path.suffix.lower() not in MEDIA_TYPES or not path.is_file():
        return None
    return path


def _file(path: Path) -> FileResponse:
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[path.suffix.lower()],
        headers={"Cache-Control": "no-cache"},
    )


def _missing() -> ApiError:
    return ApiError.of("static_missing")


FALLBACK_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
</head>
<body>
<main>
<h1>{name}</h1>
<p>The interface files are not installed with this copy of cartolex.
The API is running; its description is at /api/openapi.json.</p>
</main>
</body>
</html>
"""

USED_LINK = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>cartolex</title></head>
<body>
<main>
<h1>This link has already been used</h1>
<p>A launch link opens cartolex once. Start cartolex again from its command to get a new one,
or go back to the window where it is already open.</p>
</main>
</body>
</html>
"""

routes = Routes(tags=["interface"])


@routes.get("/launch", action="app.launch", resource="app", include_in_schema=False)
def launch(request: Request, token: str = "", next: str = "/") -> Response:
    """Exchange the launch token for a session, then go to the interface."""
    runtime = runtime_of(request)
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    session = runtime.exchange(token)
    if session is None:
        existing, _ = runtime.session_of(request)
        if existing is not None:
            return RedirectResponse(target, status_code=303)
        return HTMLResponse(USED_LINK, status_code=403)
    response = RedirectResponse(target, status_code=303)
    for value in runtime.cookie_values(session):
        response.headers.append("set-cookie", value)
    return response


@routes.get(
    "/static/ext/{ext_id}/{path:path}",
    action="static.read",
    resource="static",
    include_in_schema=False,
)
def extension_file(request: Request, ext_id: str, path: str) -> Response:
    """A file of an extension's ``static_dir`` (or one of its in-memory catalogues)."""
    ext = runtime_of(request).extensions.by_id(ext_id)
    if ext is None:
        raise _missing()
    if path.startswith("i18n/") and path.endswith(".json"):
        locale = path[len("i18n/") : -len(".json")]
        entry = ext.i18n.get(locale)
        if isinstance(entry, dict) or (entry is not None and not isinstance(entry, str)):
            return Response(
                json.dumps(dict(entry), ensure_ascii=False),
                media_type=MEDIA_TYPES[".json"],
                headers={"Cache-Control": "no-cache"},
            )
    if ext.static_dir is None:
        raise _missing()
    found = safe_file(ext.static_dir, path)
    if found is None:
        raise _missing()
    return _file(found)


def _static_root(request: Request) -> Path:
    return runtime_of(request).settings.static_dir or PACKAGE_STATIC


@routes.get("/static/{path:path}", action="static.read", resource="static", include_in_schema=False)
def static_file(request: Request, path: str) -> Response:
    """A file of the interface."""
    found = safe_file(_static_root(request), path)
    if found is None:
        raise _missing()
    return _file(found)


def _shell(request: Request) -> Response:
    index = safe_file(_static_root(request), "index.html")
    if index is not None:
        return _file(index)
    name = (
        html.escape(runtime_of(request).extensions.branding.name or "cartolex")
        if (runtime_of(request).extensions.branding)
        else "cartolex"
    )
    return HTMLResponse(FALLBACK_SHELL.format(name=name), headers={"Cache-Control": "no-cache"})


@routes.get("/", action="static.read", resource="static", include_in_schema=False)
def shell_root(request: Request) -> Response:
    """The shell of the interface."""
    return _shell(request)


@routes.get("/{path:path}", action="static.read", resource="static", include_in_schema=False)
def shell(request: Request, path: str) -> Response:
    """Any page address: the shell (the interface routes in the browser)."""
    if path.split("/", 1)[0] in ("api", "static"):
        raise ApiError.of("no_route")
    return _shell(request)
