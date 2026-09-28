# SPDX-License-Identifier: MIT
"""The loopback server of the demo services, and the faults a test can inject into it.

One :class:`DemoServer` listens on ``127.0.0.1`` on a free port (port 0) in a
thread of its own and serves every service under its own prefix
(``/openalex/…``, ``/orcid/v3.0/…``, ``/hal/…``). A service is any object with
a ``name`` and a ``handle(request) -> Reply`` method.

A :class:`FaultPlan` makes chosen requests fail the way real services do:

========== ==============================================================
``status``  answer with a status (429, 500, 503…), with ``Retry-After`` if given
``hang``    answer only after *delay* seconds (longer than the client's timeout)
``drop``    close the connection without answering
``malformed`` answer 200 with a body cut in the middle (not valid JSON)
``cut_page`` a cursor page with half of its results missing, the cursor kept
``early_end`` a cursor page that says it is the last one too early
========== ==============================================================

A service whose lists are not shaped like OpenAlex's says how a page is cut
with a ``fault_page(kind, reply)`` method. Absolute links in an answer are
written ``demo-base://<service>/…`` (:data:`DEMO_BASE`): the server replaces
the prefix with its own address before answering.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal, Protocol
from urllib.parse import parse_qsl, urlsplit

__all__ = [
    "DEMO_BASE",
    "DemoServer",
    "Fault",
    "FaultPlan",
    "Reply",
    "Request",
    "SeenRequest",
    "Service",
    "json_reply",
]

FaultKind = Literal["status", "hang", "drop", "malformed", "cut_page", "early_end"]
#: Prefix of the absolute links the demo services give (replaced by the server's address).
DEMO_BASE = "demo-base://"
_LINKING_TYPES = ("json", "xml")


@dataclass(frozen=True)
class Request:
    """A request as a service sees it: the path under its prefix, the query, the headers."""

    path: str
    query: dict[str, str]
    headers: dict[str, str]  # names in lower case


@dataclass(frozen=True)
class Reply:
    """What a service answers."""

    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: Mapping[str, str] = field(default_factory=dict)


def json_reply(status: int, data: Any, **headers: str) -> Reply:
    """A JSON answer."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return Reply(status, body, headers=headers)


class Service(Protocol):
    """A demo service: a name (its prefix) and a request handler."""

    name: str

    def handle(self, request: Request) -> Reply: ...


@dataclass(frozen=True)
class SeenRequest:
    """One request the server received (for tests to look at)."""

    service: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]


@dataclass
class Fault:
    """A failure to inject into the requests that match.

    *service* and *path* (a regular expression searched in ``path?query``)
    choose the requests; *skip* lets that many through first; *times* is how
    many it affects (``None``: all of them).
    """

    kind: FaultKind
    service: str | None = None
    path: str | None = None
    status: int = 503
    retry_after: str | None = None
    delay: float = 5.0
    times: int | None = 1
    skip: int = 0
    hits: int = 0

    def matches(self, service: str, target: str) -> bool:
        if self.service is not None and self.service != service:
            return False
        return self.path is None or re.search(self.path, target) is not None


class FaultPlan:
    """The faults to inject, in the order they were added (thread-safe)."""

    def __init__(self) -> None:
        self._faults: list[Fault] = []
        self._lock = threading.Lock()

    def add(self, kind: FaultKind, **options: Any) -> Fault:
        """Add a fault (see :class:`Fault` for the options); returns it."""
        fault = Fault(kind, **options)
        with self._lock:
            self._faults.append(fault)
        return fault

    def clear(self) -> None:
        with self._lock:
            self._faults.clear()

    def take(self, service: str, target: str) -> Fault | None:
        """The fault this request meets, if any (and count it)."""
        with self._lock:
            for fault in self._faults:
                if not fault.matches(service, target):
                    continue
                if fault.skip > 0:
                    fault.skip -= 1
                    continue
                if fault.times is not None and fault.hits >= fault.times:
                    continue
                fault.hits += 1
                return fault
        return None


class _Handler(BaseHTTPRequestHandler):
    server_version = "cartolex-demo-services"
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - base signature
        return

    def do_GET(self) -> None:  # noqa: N802 - the base class names it
        try:
            self.server.app.dispatch(self)  # type: ignore[attr-defined]
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client gave up (a timeout test); nothing to answer to


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        return  # a client that went away is not an error of the demo


class DemoServer:
    """Serves *services* on the loopback interface until :meth:`stop`."""

    def __init__(self, services: Mapping[str, Service], *, port: int = 0) -> None:
        self.services = dict(services)
        self.port = port
        self.faults = FaultPlan()
        self.requests: list[SeenRequest] = []
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("the demo services are not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> DemoServer:
        if self._server is not None:
            return self
        self._stopping.clear()
        server = _Server(("127.0.0.1", self.port), _Handler)
        server.app = self  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, name="cartolex-demo-services", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> DemoServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ── answering ──
    def dispatch(self, h: BaseHTTPRequestHandler) -> None:
        split = urlsplit(h.path)
        name, _, rest = split.path.lstrip("/").partition("/")
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        headers = {k.lower(): v for k, v in h.headers.items()}
        with self._lock:
            self.requests.append(SeenRequest(name, rest, query, headers))
        fault = self.faults.take(name, f"{rest}?{split.query}")
        if fault is not None:
            if fault.kind == "status":
                extra = {"Retry-After": fault.retry_after} if fault.retry_after else {}
                self._send(h, json_reply(fault.status, {"error": "injected failure"}, **extra))
                return
            if fault.kind == "drop":
                h.close_connection = True
                return
            if fault.kind == "hang":
                self._stopping.wait(fault.delay)
                if self._stopping.is_set():
                    return
        service = self.services.get(name)
        if service is None:
            reply = json_reply(404, {"error": "Not found", "message": f"no demo service {name!r}"})
        else:
            reply = service.handle(Request(rest, query, headers))
        if fault is not None and fault.kind == "malformed":
            reply = Reply(200, reply.body[: max(1, len(reply.body) // 2)], reply.content_type)
        elif (
            fault is not None
            and fault.kind in ("cut_page", "early_end")
            and reply.status == 200
            and hasattr(service, "fault_page")
        ):
            reply = service.fault_page(fault.kind, reply)  # type: ignore[union-attr]
        elif fault is not None and fault.kind in ("cut_page", "early_end") and reply.status == 200:
            data = json.loads(reply.body)
            if isinstance(data, dict) and isinstance(data.get("results"), list):
                if fault.kind == "cut_page":
                    data["results"] = data["results"][: len(data["results"]) // 2]
                else:
                    data.setdefault("meta", {})["next_cursor"] = None
                reply = json_reply(200, data)
        marker = DEMO_BASE.encode()
        if any(t in reply.content_type for t in _LINKING_TYPES) and marker in reply.body:
            own = f"{self.base_url}/".encode()
            reply = Reply(
                reply.status, reply.body.replace(marker, own), reply.content_type, reply.headers
            )
        self._send(h, reply)

    @staticmethod
    def _send(h: BaseHTTPRequestHandler, reply: Reply) -> None:
        h.send_response(reply.status)
        h.send_header("Content-Type", reply.content_type)
        h.send_header("Content-Length", str(len(reply.body)))
        for key, value in reply.headers.items():
            h.send_header(key, value)
        h.end_headers()
        h.wfile.write(reply.body)
