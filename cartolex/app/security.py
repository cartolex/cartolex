# SPDX-License-Identifier: MIT
"""Sessions, the launch link, the host check, CSRF and the response headers (4e).

- **Launch link.** Each app has a random launch token. ``cartolex app`` opens
  the browser at ``/launch?token=…``; the first visit exchanges it for a
  session (an ``HttpOnly`` cookie) and the address loses the token. The token
  works once.
- **Host check.** A request whose ``Host`` is not one the app answers to is
  refused before anything else runs: locally the loopback names only, hosted
  the configured names. This stops a web page that renames a server of its own
  to the loopback address (DNS rebinding) from reading the app.
- **CSRF.** Every state-changing request (``POST``, ``PUT``, ``PATCH``,
  ``DELETE``) must send the session's CSRF token in ``X-Cartolex-CSRF``. The
  token is also in a cookie the interface reads (``SameSite=Strict``); it is
  bound to the session. A cross-origin ``Origin`` (``null`` included) is
  refused on those requests.
- **CORS.** None: no ``Access-Control-Allow-*`` header is ever sent.
- **Headers.** Every response has a strict Content-Security-Policy
  (``default-src 'self'``, no inline script, no ``eval``), ``nosniff``, no
  framing and no referrer.

Cookie names carry the app instance's id, so two apps on two loopback ports
(cookies ignore ports) never overwrite each other's session.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .errors import body_of

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from .auth import Principal
    from .settings import AppSettings

__all__ = [
    "CSP",
    "CSRF_HEADER",
    "SAFE_METHODS",
    "SECURITY_HEADERS",
    "SecurityMiddleware",
    "Session",
    "SessionStore",
    "cookie_header",
]

CSRF_HEADER = "X-Cartolex-CSRF"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: The Content-Security-Policy of every response.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "font-src 'self'; connect-src 'self'; worker-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
#: Headers every response carries.
SECURITY_HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
}

_log = logging.getLogger("cartolex.app.access")


@dataclass(frozen=True)
class Session:
    """A signed-in browser: its principal and the CSRF token bound to it."""

    id: str
    csrf: str
    principal: Principal
    created: float


class SessionStore:
    """The sessions of one app, in memory (a restart signs everyone out)."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def new(self, principal: Principal) -> Session:
        session = Session(
            id=secrets.token_urlsafe(32),
            csrf=secrets.token_urlsafe(32),
            principal=principal,
            created=time.time(),
        )
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


def same_secret(given: str | None, expected: str) -> bool:
    """Compare a secret in constant time."""
    return given is not None and hmac.compare_digest(given.encode(), expected.encode())


def cookie_header(name: str, value: str, *, http_only: bool, secure: bool) -> str:
    """A ``Set-Cookie`` value: path ``/``, ``SameSite=Strict``, a session cookie."""
    parts = [f"{name}={value}", "Path=/", "SameSite=Strict"]
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _hostname(host: str) -> str:
    host = host.strip()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    if host.count(":") > 1:  # a bare IPv6 address
        return host
    return host.rsplit(":", 1)[0] if ":" in host else host


class SecurityMiddleware:
    """The outermost layer: host check, request id, body size, origin, headers, access log."""

    def __init__(self, app: ASGIApp, settings: AppSettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":  # the app has no websocket
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        request_id = _request_id(headers.get("x-request-id"))
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        status_box = {"status": 0}

        async def send_wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_box["status"] = message["status"]
                raw = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if not k.lower().startswith(b"access-control-")
                ]
                present = {k.lower() for k, _ in raw}
                for name, value in SECURITY_HEADERS.items():
                    if name.encode() not in present:
                        raw.append((name.encode(), value.encode()))
                raw.append((b"x-request-id", request_id.encode()))
                path = scope.get("path", "")
                if path.startswith("/api/") and b"cache-control" not in present:
                    raw.append((b"cache-control", b"no-store"))
                for cookie in scope.get("state", {}).get("cartolex.cookies", []):
                    raw.append((b"set-cookie", cookie.encode("latin-1")))
                message = {**message, "headers": raw}
            await send(message)

        refusal = self._refusal(scope, headers)
        try:
            if refusal is not None:
                await _json(send_wrapped, *refusal)
            else:
                await self.app(scope, receive, send_wrapped)
        finally:
            _log.info(
                "request",
                extra={
                    "event": "request",
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "route": scope["state"].get("cartolex.route")
                    or ("(refused)" if refusal else "(unrouted)"),
                    "status": status_box["status"],
                    "ms": round((time.monotonic() - started) * 1000, 1),
                },
            )

    def _refusal(self, scope: Scope, headers: dict[str, str]) -> tuple[int, dict[str, Any]] | None:
        host = headers.get("host", "")
        if not host or not self.settings.host_allowed(_hostname(host)):
            return 400, body_of("host_refused")
        method = scope.get("method", "GET")
        if method not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin is not None:
                scheme = "https" if scope.get("scheme") == "https" else "http"
                allowed = {f"{scheme}://{host}"}
                if self.settings.hosted:  # behind a proxy that ends TLS
                    allowed |= {f"https://{host}", f"http://{host}"}
                if origin == "null" or origin not in allowed:
                    return 403, body_of("cross_origin")
            length = headers.get("content-length")
            if length is not None:
                try:
                    size = int(length)
                except ValueError:
                    return 400, body_of("invalid_length")
                limit = self._limit(scope.get("path", ""))
                if size > limit:
                    return 413, body_of("request_too_large", limit_kb=limit // 1024)
        return None

    def _limit(self, path: str) -> int:
        if "/copilot/import" in path:
            return int(self.settings.max_result_mb * 1024 * 1024)
        if path.endswith(("/upload", "/files")) or "/import" in path:
            return int(self.settings.max_upload_mb * 1024 * 1024)
        return int(self.settings.max_request_kb * 1024)


def _request_id(given: str | None) -> str:
    """The client's request id when it is short and plain, else a new one."""
    if given and 8 <= len(given) <= 64 and all(c.isalnum() or c in "-_" for c in given):
        return given
    return secrets.token_hex(8)


async def _json(send: Send, status: int, body: dict[str, Any]) -> None:
    data = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(data)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": data})
