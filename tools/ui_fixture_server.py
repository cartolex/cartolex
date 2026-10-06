# SPDX-License-Identifier: MIT
"""A stand-in for the app's server, for the web interface's tests and development.

It serves what the real app serves to the interface, with the same shapes,
from fixtures and with the standard library only:

* ``/static/…`` — ``cartolex/app/static/``, and ``/static/ext/demo/…`` — the
  generic test extension (``tests/fixtures/ui-extension/``);
* ``GET /api/app/manifest`` — ``tests/fixtures/ui/manifest.json`` (the shape of
  ``tests/fixtures/manifest.example.json``, with the core pages and the test extension);
* ``GET /api/project/state`` — ``tests/fixtures/ui/project-state.example.json``;
* ``GET /api/jobs`` and ``POST /api/jobs/<id>/cancel`` — ``tests/fixtures/ui/jobs.example.json``;
* ``GET`` and ``PUT /api/me/preferences`` — kept in memory until :meth:`FixtureServer.reset`;
* ``GET /api/overview``, ``GET /api/build`` and the dry run ``POST /api/build`` —
  ``tests/fixtures/ui/overview.example.json``;
* ``GET /api/themes`` and ``GET /api/themes/usage`` — ``tests/fixtures/ui/themes.example.json``
  (a small theme tree), ``GET /api/keywords`` — ``tests/fixtures/ui/keywords.example.json``
  (a few keywords), and ``GET /api/atlas`` — no map yet;
* any other ``/api/…`` path — 404 with the error shape;
* every other ``GET`` — the shell document (history routing: the interface's
  routes are real paths).

Every response carries the Content-Security-Policy the app sends
(:data:`CSP`), ``X-Content-Type-Options: nosniff`` and ``text/javascript`` for
JavaScript. State-changing calls must carry the CSRF header with the token of
the cookie the manifest names (``security.csrf_cookie``), set on the shell
document. The server listens on the loopback interface only, on a free port by
default.

Tests run it in a thread (:func:`serve`) and change :attr:`FixtureServer.data`
between steps; ``python tools/ui_fixture_server.py`` serves it for a person
(the component gallery is at ``/gallery``).
"""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "cartolex" / "app" / "static"
FIXTURES = ROOT / "tests" / "fixtures"
EXTENSION = FIXTURES / "ui-extension"

#: The Content-Security-Policy of the app: nothing inline, nothing evaluated,
#: nothing from elsewhere.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)

#: MIME types by extension, the same on every platform (the OS registry is not used).
MIME = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

CSRF_COOKIE = "cartolex_csrf"


def load_fixtures() -> dict:
    """The fixture API's data: manifest, project state and jobs."""
    return {
        "manifest": json.loads((FIXTURES / "ui" / "manifest.json").read_text(encoding="utf-8")),
        "state": json.loads(
            (FIXTURES / "ui" / "project-state.example.json").read_text(encoding="utf-8")
        ),
        "jobs": json.loads((FIXTURES / "ui" / "jobs.example.json").read_text(encoding="utf-8")),
        **json.loads((FIXTURES / "ui" / "themes.example.json").read_text(encoding="utf-8")),
        **json.loads((FIXTURES / "ui" / "overview.example.json").read_text(encoding="utf-8")),
        **json.loads((FIXTURES / "ui" / "keywords.example.json").read_text(encoding="utf-8")),
    }


class FixtureServer(ThreadingHTTPServer):
    """The HTTP server; its :attr:`data`, :attr:`delays` and :attr:`log` are the tests' levers."""

    daemon_threads = True

    def __init__(self, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.data = load_fixtures()
        self.token = secrets.token_urlsafe(16)
        #: Seconds to wait before answering a path (to test late answers).
        self.delays: dict[str, float] = {}
        #: Every request: (method, path), in order.
        self.log: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        #: The preferences the app keeps (``PUT /api/me/preferences``), none at first.
        self.prefs: dict | None = None

    @property
    def url(self) -> str:
        """The server's base URL."""
        return f"http://127.0.0.1:{self.server_address[1]}"

    def reset(self) -> None:
        """Back to the fixtures as written, no delay, an empty log."""
        with self.lock:
            self.data = load_fixtures()
            self.delays = {}
            self.log = []
            self.prefs = None


class Handler(BaseHTTPRequestHandler):
    """Answers one request from the fixtures."""

    server: FixtureServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep the test output quiet."""

    # ── helpers ────────────────────────────────────────────────────────────
    def _headers(self, status: int, content_type: str, length: int, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-cache")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _json(self, status: int, payload: object, extra: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._headers(status, MIME[".json"], len(body), extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, code: str, message: str, next_: dict | None = None) -> None:
        self._json(status, {"error": {"code": code, "message": message, "next": next_}})

    def _file(self, path: Path) -> None:
        data = path.read_bytes()
        kind = MIME.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
        self._headers(HTTPStatus.OK, kind or "application/octet-stream", len(data))
        if self.command != "HEAD":
            self.wfile.write(data)

    def _cookie(self, name: str) -> str | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return None

    def _begin(self) -> str:
        path = unquote(urlsplit(self.path).path)
        with self.server.lock:
            self.server.log.append((self.command, path))
            delay = self.server.delays.get(path, 0.0)
        if delay:
            time.sleep(delay)
        return path

    # ── routes ─────────────────────────────────────────────────────────────
    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = self._begin()
        if path.startswith("/static/"):
            return self._static(path)
        if path.startswith("/api/"):
            return self._api_get(path)
        return self._shell()

    def do_POST(self) -> None:  # noqa: N802
        path = self._begin()
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if not path.startswith("/api/"):
            return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "Not here.")
        if not self._same_app():
            return self._error(HTTPStatus.FORBIDDEN, "csrf", "The request is not from this app.")
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
            with self.server.lock:
                for job in self.server.data["jobs"]["jobs"]:
                    if job["id"] == parts[2] and job["state"] in ("queued", "running"):
                        job["state"] = "cancelling"
                        return self._json(HTTPStatus.ACCEPTED, {"job": job})
            return self._error(HTTPStatus.NOT_FOUND, "job_not_found", "No such running job.")
        if path == "/api/build":
            # The dry run only: the fixture never starts a build.
            with self.server.lock:
                return self._json(HTTPStatus.OK, self.server.data["plan"])
        return self._error(HTTPStatus.NOT_FOUND, "not_found", "No such route.")

    def do_PUT(self) -> None:  # noqa: N802
        path = self._begin()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        if not self._same_app():
            return self._error(HTTPStatus.FORBIDDEN, "csrf", "The request is not from this app.")
        if path == "/api/me/preferences":
            with self.server.lock:
                self.server.prefs = json.loads(body or b"{}")
            return self._json(HTTPStatus.OK, self._prefs_view())
        return self._error(HTTPStatus.NOT_FOUND, "not_found", "No such route.")

    def _same_app(self) -> bool:
        security = self.server.data["manifest"]["security"]
        return self.headers.get(security["csrf_header"]) == (
            self._cookie(security.get("csrf_cookie") or CSRF_COOKIE) or "\0"
        )

    def _prefs_view(self) -> dict:
        with self.server.lock:
            kept = copy.deepcopy(self.server.prefs)
        empty = {"locale": None, "theme": None, "dismissed_jobs": [], "other": {}}
        return {
            "preferences": {**empty, **(kept or {})},
            "stored": kept is not None,
            "locales": self.server.data["manifest"]["locales"]["available"],
        }

    def _static(self, path: str) -> None:
        rel = path.removeprefix("/static/")
        base = STATIC
        if rel.startswith("ext/demo/"):
            base, rel = EXTENSION, rel.removeprefix("ext/demo/")
        target = (base / rel).resolve()
        if not target.is_relative_to(base.resolve()) or not target.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not_found", "No such file.")
        self._file(target)

    def _api_get(self, path: str) -> None:
        with self.server.lock:
            data = copy.deepcopy(self.server.data)
        if path == "/api/app/manifest":
            return self._json(HTTPStatus.OK, data["manifest"])
        if path == "/api/project/state":
            return self._json(HTTPStatus.OK, data["state"])
        if path == "/api/jobs":
            return self._json(HTTPStatus.OK, data["jobs"])
        if path == "/api/overview":
            return self._json(HTTPStatus.OK, data["overview"])
        if path == "/api/build":
            return self._json(HTTPStatus.OK, data["build"])
        if path == "/api/themes":
            return self._json(HTTPStatus.OK, data["themes"], {"ETag": '"sha256:fixture"'})
        if path == "/api/keywords":
            return self._json(HTTPStatus.OK, data["keywords"], {"ETag": '"sha256:fixture"'})
        if path == "/api/themes/usage":
            return self._json(HTTPStatus.OK, data["usage"])
        if path == "/api/atlas":
            empty = {
                "code": "empty_no_map",
                "params": {},
                "message": "no map yet: build the map",
                "next": {"label": "Build the map", "action": "build"},
            }
            return self._json(
                HTTPStatus.OK, {"format": "cartolex-atlas/2", "available": False, "empty": empty}
            )
        if path == "/api/me/preferences":
            return self._json(HTTPStatus.OK, self._prefs_view())
        if path == "/api/ext/demo/slow":
            # The test extension's own route: the tests delay it to answer late.
            return self._json(HTTPStatus.OK, {"answer": 42})
        return self._error(
            HTTPStatus.NOT_FOUND,
            "not_found",
            "No such route.",
            {"label": "Go to the overview", "action": "open:/overview"},
        )

    def _shell(self) -> None:
        data = (STATIC / "index.html").read_bytes()
        name = self.server.data["manifest"]["security"].get("csrf_cookie") or CSRF_COOKIE
        cookie = f"{name}={self.server.token}; Path=/; SameSite=Strict"
        self._headers(HTTPStatus.OK, MIME[".html"], len(data), {"Set-Cookie": cookie})
        if self.command != "HEAD":
            self.wfile.write(data)


def serve(port: int = 0) -> tuple[FixtureServer, threading.Thread]:
    """Start the server in a daemon thread; stop it with ``server.shutdown()``."""
    server = FixtureServer(port)
    thread = threading.Thread(target=server.serve_forever, name="ui-fixture-server", daemon=True)
    thread.start()
    return server, thread


def main(argv: list[str] | None = None) -> int:
    """Serve the interface on the loopback interface until interrupted."""
    parser = argparse.ArgumentParser(description="Serve the web interface with fixture data.")
    parser.add_argument("--port", type=int, default=0, help="port (default: a free one)")
    args = parser.parse_args(argv)
    server = FixtureServer(args.port)
    print(f"ui fixture server: {server.url}/gallery  (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
