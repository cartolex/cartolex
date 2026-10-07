# SPDX-License-Identifier: MIT
"""The interface's files, the extensions' files and the shell of every page.

Files are served from ``cartolex/app/static/`` (or the settings' folder) at
``/static/``, and from each extension's ``static_dir`` at
``/static/ext/<id>/``. The media type comes from a fixed table, never from the
platform (``.js`` is ``text/javascript`` everywhere); a path that leaves its
folder, names a hidden file or a type not in the table is not found. Every
other address that is not under ``/api`` or ``/static`` (``/keywords``,
``/themes/n7``) is answered with the shell, ``index.html``: the interface routes
in the browser (history routes). The documentation, built into the package
(``tools/build_docs.py``), is at ``/static/docs/``.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path, PurePosixPath

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from .errors import ApiError
from .routing import Routes, runtime_of

__all__ = [
    "ATLAS_MODULES",
    "MAP_MODULES",
    "MEDIA_TYPES",
    "PACKAGE_STATIC",
    "classic_script",
    "routes",
    "safe_file",
]

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


#: The map's modules without a library, in the order a classic script needs them.
MAP_MODULES = (
    "components/map/space.js",
    "components/map/core.js",
    "components/map/canvas2d.js",
    "components/map/webgl.js",
    "components/map/controller.js",
    "components/map/canvas3d.js",
    "components/map/webgl3d.js",
    "components/map/controller3d.js",
)

#: The atlas (``docs/dev/atlas.md``): the map's modules, the treemap's layout, the messages and
#: the atlas's own, in the order a classic script needs them; the app and the offline site both
#: mount it.
ATLAS_MODULES = (
    *MAP_MODULES,
    "components/treemap-layout.js",
    "core/messages.js",
    "atlas/dom.js",
    "atlas/schemes.js",
    "atlas/data.js",
    "atlas/state.js",
    "atlas/rings.js",
    "atlas/texts.js",
    "atlas/scene.js",
    "atlas/save.js",
    "atlas/panes.js",
    "atlas/treemap.js",
    "atlas/find.js",
    "atlas/layers.js",
    "atlas/filters.js",
    "atlas/parts.js",
    "atlas/compare.js",
    "atlas/card.js",
    "atlas/mapview.js",
    "atlas/atlas.js",
)

_IMPORT = re.compile(
    r"^import\s[^;]*?\sfrom\s+'(?:\./|(?:\.\./)+)[\w/-]+\.js';[ \t]*\n", re.MULTILINE
)
_EXPORT = re.compile(r"^export (function|const) ([A-Za-z_$][\w$]*)", re.MULTILINE)
_TOP = re.compile(
    r"^(?:export )?(?:async )?(?:function\*?|const|let|class) ([A-Za-z_$][\w$]*)", re.MULTILINE
)


def classic_script(sources: list[Path], global_name: str) -> str:
    """ES modules that import only each other (``import {…} from './x.js'`` or
    ``'../dir/x.js'``) and export only declarations (``export function``, ``export const``),
    as one classic script that sets ``window[global_name]`` to everything they export: what a
    page opened from ``file://`` loads, since browsers refuse ES modules there. *sources*
    come in dependency order. They share one scope: a top-level name declared by two modules
    is refused (``ValueError``)."""
    names: list[str] = []
    parts: list[str] = []
    seen: dict[str, str] = {}
    for path in sources:
        text = _IMPORT.sub("", path.read_text(encoding="utf-8"))
        for m in _TOP.finditer(text):
            if m.group(1) in seen:
                raise ValueError(
                    f"{path.name}: {m.group(1)} is also declared by {seen[m.group(1)]}"
                )
            seen[m.group(1)] = path.name
        names += [m.group(2) for m in _EXPORT.finditer(text)]
        text = _EXPORT.sub(r"\1 \2", text)
        if re.search(r"^\s*(import|export)\b", text, re.MULTILINE):
            raise ValueError(f"{path.name}: an import or export a classic script cannot take")
        parts.append(f"// {path.name}\n{text}")
    body = "\n".join(parts)
    exported = ", ".join(names)
    return (
        "(function () {\n'use strict';\n"
        f"{body}\nwindow[{json.dumps(global_name)}] = {{ {exported} }};\n}})();\n"
    )


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


#: At ``/static/docs/`` when the documentation was not built into this copy.
NO_DOCS = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>cartolex documentation</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem">
<h1>The documentation is not in this copy of cartolex</h1>
<p>An installed version carries it. In a copy of the source, build it with
<code>python tools/build_docs.py</code> (the development extras), or read the
<code>docs/</code> folder.</p>
</body></html>
"""


def _static_root(request: Request) -> Path:
    return runtime_of(request).settings.static_dir or PACKAGE_STATIC


@routes.get("/static/{path:path}", action="static.read", resource="static", include_in_schema=False)
def static_file(request: Request, path: str) -> Response:
    """A file of the interface."""
    found = safe_file(_static_root(request), path)
    if found is None:
        if path.split("/", 1)[0] == "docs" and not (_static_root(request) / "docs").is_dir():
            return HTMLResponse(NO_DOCS, status_code=404)  # a checkout: how to build them
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
