# SPDX-License-Identifier: MIT
"""Running the app: a local server on a loopback port, or a service for hosting.

:func:`serve` binds the socket itself (port 0 picks a free one), starts
uvicorn on it with the app's JSON logs, and, locally, opens the browser at the
launch link once the server listens. The link is also printed: it works once.
With ``settings.idle_stop_s``, a watcher stops the server once no page has been
open that long and no job runs (:mod:`cartolex.app.presence`).

The local app keeps its address from one launch to the next (:func:`bind_remembered`):
the browser keeps what a page stores per address (the theme, the drafts of the
theme editor), and a new port would be a new address. It takes the port it had
last time when that port is free, else :data:`PREFERRED_PORT`, else any free one.
"""

from __future__ import annotations

import contextlib
import json
import logging
import socket
import sys
import threading
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .logs import configure_logging
from .presence import stop_when_unused

if TYPE_CHECKING:
    from .extensions import Extension
    from .settings import AppSettings

__all__ = ["PREFERRED_PORT", "bind_remembered", "default_data_dir", "serve"]

#: The port the local app tries when the one it had last time is taken (or on its first
#: launch), below the range systems give to outgoing connections.
PREFERRED_PORT = 28734
#: The file of the app's folder that remembers its port.
PORT_FILE = "port.json"


def default_data_dir(name: str = "cartolex") -> Path:
    """The app's own folder on this computer (recent projects, uploads waiting)."""
    import os

    home = Path.home()
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif sys.platform == "darwin":  # pragma: no cover - exercised on macOS only
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / name


def _bind(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        # There SO_REUSEADDR would let another program bind the same port and take
        # connections meant for the app; this option forbids it instead.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def _remembered_port(path: Path) -> int | None:
    try:
        port = json.loads(path.read_text(encoding="utf-8")).get("port")
    except (OSError, ValueError, AttributeError):
        return None
    return port if isinstance(port, int) and 0 < port < 65536 else None


def bind_remembered(
    host: str, data_dir: Path | None, preferred: int = PREFERRED_PORT
) -> socket.socket:
    """A listening socket on the port the app had last time when it is free, else on
    *preferred*, else on any free port; the port taken is remembered in *data_dir*
    (``port.json``) for the next launch."""
    path = Path(data_dir) / PORT_FILE if data_dir is not None else None
    last = _remembered_port(path) if path is not None else None
    sock = None
    for candidate in dict.fromkeys(p for p in (last, preferred) if p):
        try:
            sock = _bind(host, candidate)
            break
        except OSError:  # taken by another program (or another cartolex)
            continue
    if sock is None:
        sock = _bind(host, 0)
    port = sock.getsockname()[1]
    if path is not None and port != last:
        from cartolex.project.files import atomic_write_bytes, json_bytes

        with contextlib.suppress(OSError):
            atomic_write_bytes(
                path, json_bytes({"format": "cartolex-port/1", "port": port}), durable=False
            )
    return sock


def serve(
    settings: AppSettings,
    extensions: Sequence[Extension] = (),
    *,
    host: str = "127.0.0.1",
    port: int | None = 0,
    open_browser: bool = True,
    announce: bool = True,
) -> int:
    """Run the app until interrupted; returns the exit status.

    *port* ``None`` takes the port the app had last time (:func:`bind_remembered`,
    remembered in ``settings.data_dir``); ``0`` any free port.
    """
    import uvicorn

    from .app import create_app

    configure_logging()
    app = create_app(settings, extensions)
    runtime = app.state.cartolex
    sock = bind_remembered(host, settings.data_dir) if port is None else _bind(host, port)
    bound = sock.getsockname()[1]
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    shown = f"[{shown}]" if ":" in shown else shown
    url = f"http://{shown}:{bound}/launch?token={runtime.launch_token}"
    config = uvicorn.Config(
        app,
        log_config=None,
        access_log=False,
        lifespan="on",
        server_header=False,
        proxy_headers=settings.hosted,
    )
    server = uvicorn.Server(config)

    def announce_when_ready() -> None:
        while not server.started and not server.should_exit:
            threading.Event().wait(0.05)
        if not server.started:
            return
        logging.getLogger("cartolex.app").info(
            "listening", extra={"event": "listening", "mode": settings.mode, "port": bound}
        )
        if announce:
            print(f"cartolex is running: {url}", flush=True)
            print("this link opens it once; press Ctrl-C to stop", flush=True)
            if settings.idle_stop_s is not None:
                print(
                    _minutes(
                        settings.idle_stop_s, "it stops by itself {} after its last page closes"
                    ),
                    flush=True,
                )
        if open_browser:
            webbrowser.open(url)

    def stop_unused() -> None:
        if announce:
            with contextlib.suppress(OSError, ValueError):  # the terminal may be gone
                print(
                    _minutes(settings.idle_stop_s or 0, "no page open for {}: cartolex stopped"),
                    flush=True,
                )
        server.should_exit = True

    stopped = threading.Event()
    threading.Thread(target=announce_when_ready, name="cartolex-announce", daemon=True).start()
    if settings.idle_stop_s is not None:
        threading.Thread(
            target=stop_when_unused,
            args=(runtime.presence, runtime.jobs, settings.idle_stop_s, stop_unused, stopped),
            name="cartolex-idle-stop",
            daemon=True,
        ).start()
    try:
        server.run(sockets=[sock])
    finally:
        stopped.set()
        sock.close()
    return 0


def _minutes(seconds: float, text: str) -> str:
    minutes = max(1, round(seconds / 60))
    return text.format(f"{minutes} minute" + ("s" if minutes > 1 else ""))
