# SPDX-License-Identifier: MIT
"""The web interface's static checks (tools/ui_check.py), and proof that they catch mistakes."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ui_check = _load("ui_check")
fixture_server = _load("ui_fixture_server")


# ── the interface passes every check ────────────────────────────────────────


def test_every_module_parses():
    node = ui_check.find_node()
    if node is None:
        pytest.skip("no Node to parse with (Playwright's bundled Node, $CARTOLEX_NODE or node)")
    problems, _ = ui_check.check_parse(node)
    assert problems == []


def test_imports_resolve_without_bare_specifiers_or_core_cycles():
    assert ui_check.check_imports() == []


def test_no_literal_text_in_templates():
    assert ui_check.check_literals() == []


def test_no_eval_html_strings_or_inline_handlers():
    assert ui_check.check_bans() == []


def test_vendored_files_match_their_hashes():
    assert ui_check.check_vendor() == []


def test_catalogues_are_complete_and_consistent():
    assert ui_check.check_catalogues() == []


def test_token_contrast_in_both_themes():
    assert ui_check.check_contrast() == []
    rows = ui_check.contrast_table()
    assert {theme for theme, *_ in rows} == {"light", "dark"}
    assert all(ratio >= minimum for *_, ratio, minimum in rows)


def test_interface_languages_are_english_french_and_portuguese():
    manifest = json.loads((ROOT / "tests" / "fixtures" / "manifest.example.json").read_text())
    assert manifest["locales"]["available"] == ["en", "fr", "pt-BR"]
    assert sorted(p.stem for p in (ui_check.STATIC / "i18n").glob("*.json")) == [
        "en",
        "fr",
        "pt-BR",
    ]


# ── the checks catch what they are for ──────────────────────────────────────


@pytest.fixture()
def bad_tree(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "i18n").mkdir()
    (tmp_path / "core" / "a.js").write_text(
        "import { b } from './b.js';\n"
        "import x from 'preact';\n"
        "import { gone } from './missing.js';\n"
        "// html`<p>Commented text</p>`\n"
        "const re = /['\"]\\/`/g;\n"
        'export const a = html`<p class="x">Hello ${b}</p><span title="Tip here">${1}</span>\n'
        '  <code>token</code><button onclick="go()" style="color: red">·</button>`;\n'
        "el.textContent = 'Plain text';\n"
        "el.innerHTML = value;\n"
        "el.innerHTML = '';\n"
        "eval('1');\n"
        "const f = new Function('a', 'return a');\n",
        encoding="utf-8",
    )
    (tmp_path / "core" / "b.js").write_text(
        "import { a } from './a.js';\nexport const b = `x ${html`<i>Nested words</i>`}`;\n",
        encoding="utf-8",
    )
    return tmp_path


def test_import_check_catches_bare_missing_and_cycles(bad_tree: Path):
    problems = "\n".join(ui_check.check_imports(bad_tree, node=None))
    assert "bare specifier 'preact'" in problems
    assert "'./missing.js' does not resolve" in problems
    assert "import cycle in core/" in problems


def test_literal_check_catches_text_attributes_and_dom_text(bad_tree: Path):
    problems = "\n".join(ui_check.check_literals(bad_tree))
    assert "'Hello'" in problems
    assert "title 'Tip here'" in problems
    assert "'Plain text'" in problems
    assert "'Nested words'" in problems
    assert "Commented" not in problems  # comments are not the page
    assert "token" not in problems  # <code> is allowed
    assert "·" not in problems  # no letter, nothing to translate


def test_ban_check_catches_eval_html_and_inline_handlers(bad_tree: Path):
    problems = ui_check.check_bans(bad_tree)
    text = "\n".join(problems)
    assert "eval()" in text and "new Function()" in text
    assert "inline event handler" in text and "style attribute" in text
    assert sum("innerHTML" in p for p in problems) == 1  # the constant '' is fine


def test_icu_subset_parses_plurals_selects_and_quotes():
    nodes = ui_check.parse_message(
        "{n, plural, =0 {none} one {# row} other {# rows}} in {place} — l'arbre '{x}'"
    )
    assert ui_check.message_arguments(nodes) == {("n", "plural"), ("place", "arg")}
    with pytest.raises(ui_check.MessageError):
        ui_check.parse_message("{n, plural, one {# row}}")
    with pytest.raises(ui_check.MessageError):
        ui_check.parse_message("{n, currency}")


def test_contrast_formula():
    black, white = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    assert ui_check.contrast(black, white) == pytest.approx(21.0)
    assert ui_check.contrast(white, white) == pytest.approx(1.0)


# ── the fixture server speaks like the app ──────────────────────────────────


@pytest.fixture(scope="module")
def server():
    srv, _ = fixture_server.serve()
    yield srv
    srv.shutdown()
    srv.server_close()


def _get(url: str, **headers):
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_fixture_server_serves_modules_with_the_csp(server):
    status, headers, body = _get(server.url + "/static/core/main.js")
    assert status == 200
    assert headers["Content-Type"].startswith("text/javascript")
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert "unsafe" not in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert b"boot(" in body


def test_fixture_server_answers_app_routes_with_the_shell(server):
    for path in ("/", "/gallery", "/keywords/anything"):
        status, headers, body = _get(server.url + path)
        assert status == 200 and b'id="cx-app"' in body
        assert "cartolex_csrf=" in headers["Set-Cookie"]


def test_fixture_server_api_shapes(server):
    status, _, body = _get(server.url + "/api/app/manifest")
    manifest = json.loads(body)
    assert status == 200 and manifest["format"] == "cartolex-manifest/1"
    assert set(manifest) >= {
        "app",
        "branding",
        "locales",
        "nav",
        "modules",
        "capabilities",
        "project",
        "security",
    }
    state = json.loads(_get(server.url + "/api/project/state")[2])
    assert {s["state"] for s in state["stages"]} <= {
        "never built",
        "up to date",
        "needs update",
        "running",
        "failed",
        "skipped",
    }
    status, _, body = _get(server.url + "/api/nothing")
    error = json.loads(body)["error"]
    assert status == 404 and set(error) == {"code", "message", "next"}


def test_fixture_server_refuses_a_write_without_csrf(server):
    request = urllib.request.Request(
        server.url + "/api/jobs/job-0002/cancel", data=b"{}", method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(request, timeout=10)
    assert info.value.code == 403
