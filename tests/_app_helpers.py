# SPDX-License-Identifier: MIT
"""Helpers of the app tests: a signed-in test client, fake projects, a waiting loop."""

from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import Any

from _build_fakes import Controls, make_project, make_registry

from cartolex.app import AppSettings, create_app
from cartolex.app.security import CSRF_HEADER

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

BASE = "http://127.0.0.1"
TOKEN = "launch-token-for-tests"


class Client:
    """A test client signed in through the launch link, sending the CSRF header on changes."""

    def __init__(self, app: Any, *, sign_in: bool = True) -> None:
        self.app = app
        self.http = TestClient(app, base_url=BASE, raise_server_exceptions=True)
        self.csrf: str | None = None
        if sign_in:
            self.sign_in(TOKEN)

    def sign_in(self, token: str) -> Any:
        response = self.http.get(f"/launch?token={token}", follow_redirects=False)
        runtime = self.app.state.cartolex
        self.csrf = self.http.cookies.get(runtime.csrf_cookie)
        return response

    def _headers(self, method: str, headers: dict[str, str] | None) -> dict[str, str]:
        out = dict(headers or {})
        if method not in ("GET", "HEAD") and self.csrf and CSRF_HEADER not in out:
            out[CSRF_HEADER] = self.csrf
        return out

    def request(self, method: str, url: str, *, headers: dict[str, str] | None = None, **kw: Any):
        return self.http.request(method, url, headers=self._headers(method, headers), **kw)

    def get(self, url: str, **kw: Any):
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any):
        return self.request("POST", url, **kw)

    def put(self, url: str, **kw: Any):
        return self.request("PUT", url, **kw)

    def patch(self, url: str, **kw: Any):
        return self.request("PATCH", url, **kw)

    def delete(self, url: str, **kw: Any):
        return self.request("DELETE", url, **kw)

    def wait_job(self, job_id: str, timeout: float = 120.0) -> dict[str, Any]:
        """Poll a job until it ends; returns its last state."""
        deadline = time.monotonic() + timeout
        while True:
            job = self.get(f"/api/jobs/{job_id}").json()
            if job["state"] not in ("queued", "running", "cancelling"):
                return job
            if time.monotonic() > deadline:
                raise TimeoutError(f"job {job_id} still {job['state']}")
            time.sleep(0.05)


def fake_project(root: Path, **kw: Any) -> Path:
    """A small project with fake texts and people (closed, so an app can open it)."""
    make_project(root, **kw).close()
    return root


def fake_app(root: Path, log: Path, *, extensions=(), **settings: Any) -> tuple[Any, Controls]:
    """An app on a fake project, building with the fake stages of ``_build_fakes``."""
    controls = Controls(log=log)
    base = dict(
        project=root,
        launch_token=TOKEN,
        registry=make_registry(controls),
        build_budget_mb=1e9,
        build_year=2026,
        heartbeat_s=0.5,
    )
    base.update(settings)
    return create_app(AppSettings(**base), extensions), controls


def etag(response: Any) -> str:
    return response.headers["ETag"]
