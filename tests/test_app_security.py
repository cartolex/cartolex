# SPDX-License-Identifier: MIT
"""The app's security: host check, launch token, CSRF, CORS, CSP, authorisation, uploads, 412."""

from __future__ import annotations

import io
import zipfile

import pytest
from _app_helpers import TOKEN, Client, etag, fake_app, fake_project
from fastapi.routing import APIRoute

from cartolex.app import AppSettings, Decision, create_app
from cartolex.app.routing import Guard
from cartolex.app.security import CSRF_HEADER


@pytest.fixture()
def app(tmp_path):
    app, _ = fake_app(fake_project(tmp_path / "p"), tmp_path / "calls.log")
    yield app
    app.state.cartolex.shutdown()


def test_every_route_asks_authorize(app, tmp_path):
    from _app_extension import make_extension

    ext_app, _ = fake_app(
        fake_project(tmp_path / "q"),
        tmp_path / "c2.log",
        extensions=[make_extension(tmp_path / "s")],
    )
    try:
        for application in (app, ext_app):
            routes = [r for r in application.routes if isinstance(r, APIRoute)]
            assert len(routes) > 50
            missing = [
                f"{sorted(r.methods)} {r.path}"
                for r in routes
                if not any(isinstance(d.call, Guard) for d in r.dependant.dependencies)
            ]
            assert not missing, f"routes without authorize(): {missing}"
            others = [r for r in application.routes if not isinstance(r, APIRoute)]
            assert not others, (
                f"routes outside the guard: {[getattr(r, 'path', r) for r in others]}"
            )
    finally:
        ext_app.state.cartolex.shutdown()


def test_the_host_header_is_checked(app):
    client = Client(app)
    assert client.get("/api/health").status_code == 200
    for host in ("evil.example", "127.0.0.1.evil.example", "localhost.evil.example"):
        r = client.http.get("/api/health", headers={"Host": host})
        assert r.status_code == 400 and r.json()["error"]["code"] == "host_refused", host
    assert client.http.get("/api/health", headers={"Host": "localhost:8123"}).status_code == 200
    assert client.http.get("/api/health", headers={"Host": "[::1]:8123"}).status_code == 200


def test_hosted_names_are_the_configured_ones(tmp_path):
    app = create_app(
        AppSettings(mode="hosted", projects_root=tmp_path, allowed_hosts=("maps.example.org",))
    )
    client = Client(app, sign_in=False)
    assert client.http.get("/api/health", headers={"Host": "maps.example.org"}).status_code == 200
    assert client.http.get("/api/health", headers={"Host": "127.0.0.1"}).status_code == 400
    with pytest.raises(ValueError, match="allowed_hosts"):
        AppSettings(mode="hosted", projects_root=tmp_path)


def test_the_launch_token_is_exchanged_once(app):
    stranger = Client(app, sign_in=False)
    assert stranger.get("/api/app/manifest").status_code == 401
    assert stranger.sign_in("wrong").status_code == 403
    first = Client(app, sign_in=False)
    r = first.sign_in(TOKEN)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookies = r.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("cartolex_session_"))
    assert "HttpOnly" in session and "SameSite=Strict" in session
    assert first.get("/api/app/manifest").status_code == 200
    # the same link again: a second browser gets nothing, the first goes on
    assert Client(app, sign_in=False).sign_in(TOKEN).status_code == 403
    assert first.sign_in(TOKEN).status_code == 303
    assert first.delete("/api/session").status_code == 200
    assert first.get("/api/app/manifest").status_code == 401


def test_changes_need_the_csrf_token_of_the_session(app):
    client = Client(app)
    body = {"seed": 3, "stages": {}}
    version = etag(client.get("/api/params"))
    for headers in ({CSRF_HEADER: ""}, {CSRF_HEADER: "wrong"}):
        r = client.put("/api/params", json=body, headers={**headers, "If-Match": version})
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"
    r = client.http.put("/api/params", json=body, headers={"If-Match": version})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"
    other = Client(app, sign_in=False)
    r = other.http.put("/api/params", json=body, headers={CSRF_HEADER: client.csrf})
    assert r.status_code == 401  # a token without its session is nothing
    ok = client.put("/api/params", json=body, headers={"If-Match": version})
    assert ok.status_code == 200, ok.text


def test_cross_origin_changes_are_refused_and_cors_is_never_open(app):
    client = Client(app)
    for origin in ("null", "http://evil.example", "http://127.0.0.1:1"):
        r = client.post("/api/projects/close", headers={"Origin": origin})
        assert r.status_code == 403 and r.json()["error"]["code"] == "cross_origin", origin
    for method, url, headers in (
        ("GET", "/api/health", {"Origin": "http://evil.example"}),
        ("GET", "/api/app/manifest", {"Origin": "null"}),
        (
            "OPTIONS",
            "/api/people",
            {"Origin": "http://evil.example", "Access-Control-Request-Method": "PATCH"},
        ),
    ):
        r = client.request(method, url, headers=headers)
        assert not any(h.lower().startswith("access-control-") for h in r.headers), url
        assert r.headers.get("access-control-allow-origin") not in ("*", "null")


def test_a_strict_csp_on_every_html_and_js_response(app, tmp_path):
    from _app_extension import make_extension

    static = tmp_path / "ui"
    (static / "core").mkdir(parents=True)
    (static / "index.html").write_text(
        '<!doctype html><script type="module" src="/static/core/main.js"></script>\n',
        encoding="utf-8",
    )
    (static / "core" / "main.js").write_text("export const x = 1;\n", encoding="utf-8")
    ext_app, _ = fake_app(
        fake_project(tmp_path / "q"),
        tmp_path / "c.log",
        extensions=[make_extension(tmp_path / "ext")],
        static_dir=static,
    )
    try:
        client = Client(ext_app)
        answers = {
            "/": "text/html",
            "/keywords": "text/html",
            "/themes/n7": "text/html",
            "/static/index.html": "text/html",
            "/static/core/main.js": "text/javascript",
            "/static/ext/reports/index.js": "text/javascript",
            "/static/ext/reports/pages/reports.js": "text/javascript",
            "/static/ext/reports/i18n/fr.json": "application/json",
            "/api/health": "application/json",
        }
        for url, kind in answers.items():
            r = client.get(url)
            assert r.status_code == 200, url
            assert r.headers["content-type"].startswith(kind), (url, r.headers["content-type"])
            csp = r.headers["content-security-policy"]
            assert "default-src 'self'" in csp and "script-src 'self'" in csp
            assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
            assert r.headers["x-content-type-options"] == "nosniff"
        # an address outside /api and /static is a page: the shell, never a file
        page = client.get("/pyproject.toml")
        assert page.status_code == 200 and page.text.startswith("<!doctype html>")
        for url in (
            "/static/%2e%2e/pyproject.toml",
            "/static/%2e%2e/%2e%2e/pyproject.toml",
            "/static/ext/reports/%2e%2e/%2e%2e/index.html",
            "/static/ext/nobody/index.js",
            "/static/.hidden",
            "/static/core/missing.js",
            "/api/nothing",
        ):
            r = client.get(url)
            assert r.status_code == 404, url
            assert "content-security-policy" in r.headers
    finally:
        ext_app.state.cartolex.shutdown()


def test_a_denied_authorize_is_a_403_with_a_plain_message(tmp_path):
    def authorizer(principal, action, resource):
        if action == "keywords.write":
            return Decision(False, "only the project's editors change keywords")
        return principal.trusted or action in ("app.health", "app.launch", "static.read")

    app, _ = fake_app(fake_project(tmp_path / "p"), tmp_path / "c.log", authorizer=authorizer)
    try:
        client = Client(app)
        r = client.post(
            "/api/keywords/decisions",
            json={"decisions": [{"term": "a", "decision": "keep"}]},
            headers={"If-Match": '"none"'},
        )
        assert r.status_code == 403
        assert r.json()["error"] == {
            "code": "forbidden",
            "params": {
                "reason": "only the project's editors change keywords",
                "action": "keywords.write",
            },
            "message": "only the project's editors change keywords",
            "next": {"label": "Close", "action": "none"},
        }
        assert client.get("/api/keywords").status_code == 200
    finally:
        app.state.cartolex.shutdown()


def test_a_stale_write_is_refused_with_412_and_the_current_version(app):
    first, second = Client(app), Client(app, sign_in=False)
    runtime = app.state.cartolex
    second.http.cookies.update(first.http.cookies)  # two tabs of one browser
    second.csrf = first.csrf
    read_a, read_b = first.get("/api/params"), second.get("/api/params")
    assert etag(read_a) == etag(read_b)
    body = {"seed": 7, "stages": {"themes.group": {"top_groups": 9}}}
    ok = first.put("/api/params", json=body, headers={"If-Match": etag(read_a)})
    assert ok.status_code == 200 and etag(ok) != etag(read_a)
    stale = second.put(
        "/api/params", json={"seed": 8, "stages": {}}, headers={"If-Match": etag(read_b)}
    )
    assert stale.status_code == 412
    error = stale.json()["error"]
    assert error["code"] == "stale" and error["next"]["action"] == "reload"
    assert stale.headers["ETag"] == etag(ok) == f'"{error["current"]}"'
    missing = second.put("/api/params", json={"seed": 8, "stages": {}})
    assert missing.status_code == 428
    params, _ = runtime.projects.current().project.read_params()
    assert params.seed == 7  # the second write changed nothing


def test_uploads_stay_in_their_folder(tmp_path):
    from cartolex.app.uploads import MEMBER_PROBLEMS
    from cartolex.project import Project
    from cartolex.project.models import Slot

    root = tmp_path / "p"
    Project.init(root, name="n", domain_title="t", slots=(Slot(id="docs", kind="folder"),)).close()
    app = create_app(AppSettings(project=root, launch_token=TOKEN, max_upload_mb=1))
    try:
        client = Client(app)

        def zipped(*names: str) -> bytes:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for name in names:
                    zf.writestr(name, "text")
            return buf.getvalue()

        for bad in ("../escape.txt", "/abs.txt", "a/../../escape.txt", ".hidden/x.txt"):
            r = client.post(
                "/api/sources/docs/files", files={"file": ("docs.zip", zipped("ok.txt", bad))}
            )
            error = r.json()["error"]
            assert r.status_code == 422 and error["code"] == "unsafe_archive_member", bad
            assert error["params"]["problem"] in MEMBER_PROBLEMS
        assert not (tmp_path / "escape.txt").exists()
        assert not (root / "sources" / "docs").exists() or not any(
            (root / "sources" / "docs").rglob("*")
        )
        r = client.post(
            "/api/sources/docs/files", files={"file": ("../../name.txt", b"plain text")}
        )
        assert r.status_code == 201 and r.json()["files"] == ["name.txt"]
        assert (root / "sources" / "docs" / "name.txt").read_bytes() == b"plain text"
        r = client.post("/api/sources/docs/files", files={"file": ("in.zip", zipped("a/b.txt"))})
        assert r.status_code == 201 and r.json()["files"] == ["a/b.txt"]
        for name, data, code in (
            ("name.txt", b"other", "file_exists"),
            ("again.zip", zipped("a/b.txt"), "archive_replaces"),
        ):
            r = client.post("/api/sources/docs/files", files={"file": (name, data)})
            assert r.status_code == 409 and r.json()["error"]["code"] == code, name
        assert (root / "sources" / "docs" / "name.txt").read_bytes() == b"plain text"
        big = client.post(
            "/api/sources/docs/files", files={"file": ("big.txt", b"x" * (2 * 1024 * 1024))}
        )
        assert big.status_code == 413
    finally:
        app.state.cartolex.shutdown()


def test_an_archive_that_unpacks_too_big_is_refused(tmp_path):
    from cartolex.app.errors import ApiError
    from cartolex.app.uploads import extract_archive

    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge.txt", b"0" * (3 * 1024 * 1024))  # 3 MB that compress to a few KB
        link = zipfile.ZipInfo("link")
        link.external_attr = (0o120777) << 16  # a symbolic link
    assert archive.stat().st_size < 64 * 1024
    target = tmp_path / "out"
    with pytest.raises(ApiError, match="unpacks to more"):
        extract_archive(archive, target, max_members=10, max_bytes=1024 * 1024)
    with pytest.raises(ApiError, match="more than 0 files"):
        extract_archive(archive, target, max_members=0, max_bytes=10**9)
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr(link, "elsewhere")
    with pytest.raises(ApiError, match="link"):
        extract_archive(archive, target, max_members=10, max_bytes=10**9)
    assert not target.exists() or not any(target.iterdir())


def test_a_request_too_large_is_refused_before_it_is_read(app):
    client = Client(app)
    r = client.put(
        "/api/params",
        content=b"{" + b" " * (17 * 1024 * 1024) + b"}",
        headers={"If-Match": '"none"', "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_errors_say_what_to_do_next(app):
    client = Client(app)
    for r in (
        client.get("/api/nothing"),
        client.get("/api/jobs/unknown-job"),
        client.put("/api/params", json={"seed": -1}),
        client.post("/api/build", json={"scope": ["nowhere"]}),
    ):
        error = r.json()["error"]
        assert set(error) >= {"code", "params", "message", "next"}, r.text
        assert set(error["next"]) == {"label", "action"}
