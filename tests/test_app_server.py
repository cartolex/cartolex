# SPDX-License-Identifier: MIT
"""The command line of the app: ``cartolex`` / ``cartolex app`` / ``cartolex api``, and a real server."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from _app_helpers import fake_project

from cartolex.app import CliVerb, Extension
from cartolex.cli import main


def test_no_argument_opens_the_app(monkeypatch, tmp_path):
    seen = {}

    def serve(settings, extensions, **kw):
        seen.update(settings=settings, extensions=extensions, **kw)
        return 0

    monkeypatch.setattr("cartolex.app.server.serve", serve)
    assert main([]) == 0
    assert seen["settings"].mode == "local" and seen["settings"].project is None
    assert seen["open_browser"] is True and seen["host"] == "127.0.0.1" and seen["port"] is None
    root = fake_project(tmp_path / "p")
    assert main(["app", str(root), "--no-browser", "--data-dir", str(tmp_path / "d")]) == 0
    assert seen["settings"].project == root and seen["open_browser"] is False
    assert seen["settings"].data_dir == tmp_path / "d"
    assert (
        main(["api", "--projects-root", str(tmp_path), "--allowed-host", "maps.example.org"]) == 0
    )
    hosted = seen["settings"]
    assert hosted.mode == "hosted" and hosted.allowed_hosts == ("maps.example.org",)
    assert seen["open_browser"] is False and seen["port"] == 8000
    assert main(["api", str(root), "--projects-root", str(tmp_path)]) == 1


def test_the_local_app_keeps_its_port_from_one_launch_to_the_next(tmp_path):
    from cartolex.app.server import bind_remembered

    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    busy = taken.getsockname()[1]
    try:
        first = bind_remembered("127.0.0.1", tmp_path, preferred=busy)  # preferred taken
        port = first.getsockname()[1]
        assert port != busy
        first.close()
        again = bind_remembered("127.0.0.1", tmp_path, preferred=busy)
        assert again.getsockname()[1] == port  # the same address: the browser keeps its data
        # the remembered port taken by another program: another one, remembered in turn
        other = bind_remembered("127.0.0.1", tmp_path, preferred=busy)
        assert other.getsockname()[1] not in (port, busy)
        assert json.loads((tmp_path / "port.json").read_text())["port"] == other.getsockname()[1]
        again.close()
        other.close()
    finally:
        taken.close()


def test_an_extension_adds_its_verbs(capsys):
    def hello(args) -> int:
        print(f"hello {args.who}")
        return 0

    ext = Extension(
        "greeter",
        cli=(CliVerb("hello", "say hello", hello, lambda p: p.add_argument("who")),),
    )
    assert main(["hello", "tide"], extensions=[ext]) == 0
    assert capsys.readouterr().out == "hello tide\n"


def test_a_real_server_on_a_free_loopback_port(tmp_path):
    root = fake_project(tmp_path / "p")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(Path(__file__).parents[1]), *sys.path])}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cartolex.cli",
            "app",
            str(root),
            "--no-browser",
            "--data-dir",
            str(tmp_path / "data"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        line = ""
        deadline = time.monotonic() + 120
        while "cartolex is running:" not in line:
            assert time.monotonic() < deadline, "the server did not start"
            line = proc.stdout.readline()
            assert line or proc.poll() is None, proc.stderr.read()
        url = line.split("cartolex is running:", 1)[1].strip()
        base = url.split("/launch", 1)[0]
        assert base.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(base + "/api/health", timeout=10) as r:
            assert json.loads(r.read()) == {"status": "ok"}
            assert "default-src 'self'" in r.headers["Content-Security-Policy"]

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kw):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(url, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 303 and "cartolex_session_" in exc.headers["Set-Cookie"]
        try:
            opener.open(url, timeout=10)
            raise AssertionError("the link worked twice")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        assert (root / ".lock").exists()
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    logs = [json.loads(x) for x in proc.stderr.read().splitlines() if x.startswith("{")]
    requests = [x for x in logs if x.get("event") == "request"]
    assert {x["route"] for x in requests} >= {"/api/health", "/launch"}
    assert all("token" not in json.dumps(x) for x in requests)
    assert not (root / ".lock").exists()  # stopping closed the project
