# SPDX-License-Identifier: MIT
"""The real app for the browser tests of a screen: a demo project, built, and its server.

The fixture server (tools/ui_fixture_server.py) stands in for the app for the
shell and the gallery; a screen that edits a project (the theme editor) is
tested on the app itself, on a demo world built as a project: `build_demo`
writes and builds one, `AppServer` serves a copy on the loopback interface, on
a free port, until it is stopped. The launch link signs the browser in.
"""

from __future__ import annotations

import shutil
import socket
import threading
import time
from pathlib import Path

#: The launch token of the test apps (the launch link works once per app).
TOKEN = "browser-tests-launch-token"


def build_demo(root: Path, size: str = "S", *, depth: int | None = None) -> Path:
    """Write the demo world *size* (seed 0) as a project in *root* and build it."""
    from cartolex.cli import main as cli
    from cartolex.demo import generate
    from cartolex.demo.project import write_project

    write_project(generate(size, 0), root).close()
    settings = ["pinned_year=2026"]
    if depth is not None:
        settings.append(f"themes.group.depth={depth}")
    assert cli(["params", str(root), "--set", *settings]) == 0
    assert cli(["build", str(root)]) == 0
    return root


def copy_project(built: Path, target: Path) -> Path:
    """A copy of a built project, for a test that changes it."""
    shutil.copytree(built, target, ignore=shutil.ignore_patterns(".lock"))
    return target


class AppServer:
    """The app on a project, served by uvicorn in a thread on a free loopback port."""

    def __init__(self, project: Path, data_dir: Path) -> None:
        import uvicorn

        from cartolex.app import AppSettings, create_app

        self.app = create_app(
            AppSettings(
                project=project,
                launch_token=TOKEN,
                data_dir=data_dir,
                build_budget_mb=1e9,
                build_year=2026,
            )
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(64)
        self.sock = sock
        self.port = sock.getsockname()[1]
        config = uvicorn.Config(
            self.app, log_config=None, access_log=False, lifespan="on", server_header=False
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [sock]}, name="app-server", daemon=True
        )
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the app did not start")
            time.sleep(0.02)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def launch(self) -> str:
        """The launch link: it signs the browser in, then shows the app."""
        return f"{self.url}/launch?token={TOKEN}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()
        self.app.state.cartolex.shutdown()
