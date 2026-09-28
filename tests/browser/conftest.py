# SPDX-License-Identifier: MIT
"""The browser harness: the fixture server, one headless Chromium, isolated and offline.

* The fixture server (tools/ui_fixture_server.py) answers on the loopback
  interface, on a free port, with the app's shapes and Content-Security-Policy.
* Chromium runs headless with its own empty home folder and profile; every
  request to anything but the loopback interface is refused (a route handler
  aborts it, and the resolver maps every other host to nothing), and a test
  that caused one fails.
* A test fails on any console error, uncaught exception or CSP violation,
  unless it is marked ``allow_console_errors``.
* Without Playwright or without a Chromium build for it, every browser test
  is skipped with the reason; the rest of the suite runs.

Options: ``--ui-screenshots DIR`` writes the gallery's screenshots there;
``$CARTOLEX_UI_MEASURES`` names a JSON file that receives the budgets and leak
measures.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

from browser_harness import UI, Collected  # noqa: E402

LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}
AXE = HERE / "vendor" / "axe-core" / "axe.min.js"


def _load_fixture_server():
    name = "ui_fixture_server"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--ui-screenshots",
        default=os.environ.get("CARTOLEX_UI_SCREENSHOTS"),
        help="write the gallery's screenshots (light, dark; en, fr, pt-BR) to this folder",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "browser: runs in a headless browser (tests/browser)")
    config.addinivalue_line("markers", "slow: a browser test that takes several seconds")
    config.addinivalue_line("markers", "allow_console_errors: the test provokes errors on purpose")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if HERE in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.browser)


@pytest.fixture(scope="session")
def ui_server():
    """The fixture server, for the whole session."""
    server, _ = _load_fixture_server().serve()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def browser(tmp_path_factory: pytest.TempPathFactory):
    """One headless Chromium for the session, with an empty home folder."""
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("Playwright is not installed (pip install playwright)")
    home = tmp_path_factory.mktemp("browser-home")
    env = {
        **{k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TZ", "DISPLAY")},
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "TZ": "UTC",
    }
    manager = sync_playwright().start()
    try:
        instance = manager.chromium.launch(
            headless=True,
            env=env,
            args=[
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--no-first-run",
                "--disable-features=Translate,OptimizationHints,MediaRouter",
            ],
        )
    except Error as exc:
        manager.stop()
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else "cannot launch"
        pytest.skip(f"no Chromium for this Playwright ({first}); run: playwright install chromium")
    yield instance
    instance.close()
    manager.stop()


@pytest.fixture()
def server(ui_server):
    """The fixture server, back to its fixtures before each test."""
    ui_server.reset()
    return ui_server


@pytest.fixture()
def ui(request: pytest.FixtureRequest, browser, server):
    """A fresh isolated browser context and page on the fixture server (see UI)."""
    options = getattr(request, "param", None) or {}
    context = browser.new_context(
        viewport=options.get("viewport", {"width": 1280, "height": 900}),
        locale=options.get("locale", "en-US"),
        timezone_id="UTC",
        color_scheme=options.get("color_scheme", "light"),
        reduced_motion=options.get("reduced_motion", "reduce"),
        bypass_csp=options.get("bypass_csp", False),
        service_workers="block",
    )
    collected = Collected()

    def gate(route):
        url = route.request.url
        parts = urlsplit(url)
        if parts.scheme in ("data", "blob") or parts.hostname in LOOPBACK:
            collected.requests.append(url)
            route.continue_()
        else:
            collected.blocked.append(url)
            route.abort("blockedbyclient")

    context.route("**/*", gate)
    page = context.new_page()
    page.on(
        "console",
        lambda m: collected.console_errors.append(m.text) if m.type == "error" else None,
    )
    page.on("pageerror", lambda e: collected.page_errors.append(str(e)))
    # Report every CSP violation to the console, where the collector above sees it.
    page.add_init_script(
        "document.addEventListener('securitypolicyviolation', (e) => "
        "console.error('CSP violation: ' + e.violatedDirective + ' ' + e.blockedURI));"
    )
    view = UI(page, server.url, collected)
    yield view
    context.close()
    assert collected.blocked == [], f"requests left the machine: {collected.blocked}"
    if request.node.get_closest_marker("allow_console_errors") is None:
        assert collected.page_errors == [], collected.page_errors
        assert collected.console_errors == [], collected.console_errors


@pytest.fixture(scope="session")
def axe_source() -> str:
    """The vendored axe-core."""
    return AXE.read_text(encoding="utf-8")
