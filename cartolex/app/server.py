# SPDX-License-Identifier: MIT
"""Running the app: a local server on a free loopback port, or a service for hosting.

:func:`serve` binds the socket itself (port 0 picks a free one), starts
uvicorn on it with the app's JSON logs, and, locally, opens the browser at the
launch link once the server listens. The link is also printed: it works once.
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .logs import configure_logging

if TYPE_CHECKING:
    from .extensions import Extension
    from .settings import AppSettings

__all__ = ["default_data_dir", "serve"]


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


def serve(
    settings: AppSettings,
    extensions: Sequence[Extension] = (),
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    announce: bool = True,
) -> int:
    """Run the app until interrupted; returns the exit status."""
    import uvicorn

    from .app import create_app

    configure_logging()
    app = create_app(settings, extensions)
    runtime = app.state.cartolex
    sock = _bind(host, port)
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
        if open_browser:
            webbrowser.open(url)

    threading.Thread(target=announce_when_ready, name="cartolex-announce", daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
    return 0
