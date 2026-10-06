# SPDX-License-Identifier: MIT
"""The shell in a browser: start order, routing and its lifecycle, guards, stores, API client."""

from __future__ import annotations

import json
import time

import pytest
from browser_harness import prefs_script


def test_boot_renders_the_navigation_and_the_first_page(ui):
    ui.open("/")
    page = ui.page
    assert page.url.endswith("/overview")  # `/` goes to the first navigation entry
    assert page.locator("h1").inner_text() == "Coastal and marine demo"
    links = page.locator(".cx-nav__link")
    assert links.count() == 7  # six manifest entries and the extension's page
    assert page.locator('[data-nav="overview"]').get_attribute("aria-current") == "page"
    assert page.title() == "Overview · cartolex"
    assert ui.missing_keys() == []


def test_boot_order_manifest_catalogues_extensions_then_page(ui):
    ui.open("/overview")
    paths = [u.split("/", 3)[-1] for u in ui.collected.requests]
    order = [
        paths.index("api/app/manifest"),
        paths.index("static/i18n/en.json"),
        paths.index("static/ext/demo/index.js"),
        paths.index("static/pages/overview.js"),
    ]
    assert order == sorted(order)


def test_status_dots_render_from_the_cache_then_refresh(ui, server):
    cached = json.loads(json.dumps(server.data["state"]))
    for stage in cached["stages"]:
        if stage["area"] == "keywords":
            stage["state"] = "failed"
    ui.page.add_init_script(
        "localStorage.setItem('cartolex.state/1:demo-coast', "
        + json.dumps(json.dumps(cached))
        + ");"
    )
    server.delays["/api/project/state"] = 1.5
    ui.page.goto(server.url + "/gallery")
    dot = ui.page.locator('[data-nav="keywords"] .cx-status')
    dot.wait_for()
    assert dot.get_attribute("data-state") == "failed"  # at once, from the cache
    ui.page.wait_for_function(
        "() => document.querySelector('[data-nav=keywords] .cx-status')"
        ".dataset.state === 'needs_update'",
        timeout=5000,
    )


def test_links_navigate_without_reloading_and_move_the_focus(ui):
    ui.open("/overview")
    ui.page.evaluate("() => { window.cxSamePage = true; }")
    before = ui.token()
    ui.page.locator('[data-nav="keywords"]').click()
    ui.wait_ready(before)
    assert ui.page.url.endswith("/keywords")
    assert ui.page.evaluate("() => window.cxSamePage") is True
    assert ui.active()["tag"] == "h1"
    assert ui.page.locator('[data-nav="keywords"]').get_attribute("aria-current") == "page"
    before = ui.token()
    ui.page.go_back()
    ui.wait_ready(before)
    assert ui.page.url.endswith("/overview")


def test_unknown_address_shows_the_not_found_page(ui):
    ui.open("/no/such/page")
    assert ui.page.locator("h1").inner_text() == "Page not found"
    assert ui.page.locator(".cx-empty a").get_attribute("href") == "/"


def test_extension_page_slot_and_catalogue_override(ui):
    ui.open("/overview")
    assert (
        ui.page.locator('[data-slot="overview.cards"]')
        .inner_text()
        .startswith("A card from an extension")
    )
    assert ui.page.locator('[data-nav="share"]').inner_text() == "Publish"  # override
    ui.navigate("/demo")
    assert ui.page.locator("h1").inner_text() == "Extension"


def test_late_answers_of_a_left_page_are_dropped(ui, server):
    server.delays["/api/ext/demo/slow"] = 1.0
    ui.open("/overview")
    ui.navigate("/demo")
    ui.navigate("/overview")  # leave before the answer
    time.sleep(1.5)
    assert ui.page.evaluate("() => window.cxDemoAnswers || 0") == 0
    server.delays.clear()
    ui.navigate("/demo")
    ui.page.wait_for_function("() => window.cxDemoAnswers === 1", timeout=5000)


def test_leaving_with_unsaved_edits_asks_first(ui):
    ui.open("/overview")
    ui.navigate("/gallery")
    page = ui.page
    page.locator('[data-guard="toggle"]').check()
    page.locator('[data-nav="overview"]').click()
    dialog = page.get_by_role("alertdialog")
    dialog.wait_for()
    assert ui.active()["text"] == "Stay on the page"  # the safe answer has the focus
    page.keyboard.press("Enter")
    dialog.wait_for(state="hidden")
    assert page.url.endswith("/gallery")
    before = ui.token()
    page.go_back()  # the browser's back button asks too, and stays when told to
    dialog.wait_for()
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    assert page.url.endswith("/gallery")
    page.locator('[data-nav="overview"]').click()
    dialog.wait_for()
    dialog.get_by_role("button", name="Leave without saving").click()
    ui.wait_ready(before)
    assert page.url.endswith("/overview")


def test_theme_and_language_switch_and_persist(ui):
    ui.open("/gallery")
    page = ui.page
    page.locator(".cx-header .cx-menubutton button").click()
    page.get_by_role("menuitemradio", name="Dark").click()
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
    page.locator(".cx-header .cx-menubutton button").click()
    page.get_by_role("menuitemradio", name="Français").click()
    page.wait_for_function("() => document.documentElement.lang === 'fr'")
    assert page.locator("h1").inner_text() == "Galerie des composants"
    assert page.title() == "Galerie des composants · cartolex"
    page.reload()
    ui.wait_ready(0)
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
    assert page.locator("h1").inner_text() == "Galerie des composants"
    assert ui.missing_keys() == []


def test_preferences_outlive_the_browser_storage(ui, server):
    ui.open("/gallery")
    page = ui.page
    page.locator(".cx-header .cx-menubutton button").click()
    page.get_by_role("menuitemradio", name="Dark").click()
    page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
    deadline = time.monotonic() + 5
    while (server.prefs or {}).get("theme") != "dark":
        assert time.monotonic() < deadline, server.prefs
        time.sleep(0.05)
    page.evaluate("() => localStorage.clear()")  # a new address, or a cleared cache
    page.reload()
    ui.wait_ready(0)
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"


@pytest.mark.parametrize("locale", ["fr", "pt-BR"])
def test_catalogues_cover_the_gallery_in_every_language(ui, locale):
    ui.page.add_init_script(prefs_script(locale=locale))
    ui.open("/gallery")
    assert ui.page.evaluate("() => document.documentElement.lang") == locale
    assert ui.missing_keys() == []


@pytest.mark.allow_console_errors  # the browser logs every 4xx and 5xx answer
def test_api_client_csrf_etag_stale_and_errors(ui, server):
    ui.open("/overview")
    page = ui.page

    def respond(route):
        request = route.request
        if request.method == "GET":
            route.fulfill(status=200, json={"value": 1}, headers={"ETag": '"v1"'})
        elif request.headers.get("if-match") == '"v1"':
            route.fulfill(
                status=412,
                json={"error": {"code": "stale", "message": "Changed meanwhile.", "next": None}},
                headers={"ETag": '"v2"', "X-Request-Id": "req-1"},
            )
        else:
            route.fulfill(status=200, json={"seen": dict(request.headers)})

    page.route("**/api/test/doc", respond)
    page.route(
        "**/api/test/broken*",
        lambda route: route.fulfill(
            status=500,
            json={
                "error": {
                    "code": "stage_failed",
                    "message": "It broke.",
                    "next": {"label": "Open the parameters", "action": "open:/settings"},
                }
            },
            headers={"X-Request-Id": "req-2"},
        ),
    )
    result = page.evaluate(
        """async () => {
          const { ApiClient, readCookie } = await import('/static/core/api.js');
          const api = new ApiClient({ csrfToken: () => readCookie('cartolex_csrf') });
          const read = await api.get('/api/test/doc');
          const stale = await api.put('/api/test/doc', { value: 2 }, { ifMatch: read.etag });
          const fresh = await api.post('/api/test/doc', {});
          const broken = await api.get('/api/test/broken?term=secret');
          const controller = new AbortController();
          controller.abort();
          const aborted = await api.get('/api/test/doc', { signal: controller.signal });
          return { read, stale, csrf: fresh.data.seen['x-cartolex-csrf'] || null,
                   cookie: readCookie('cartolex_csrf'), broken, aborted };
        }"""
    )
    assert result["read"]["ok"] and result["read"]["etag"] == '"v1"'
    assert result["stale"]["kind"] == "stale" and result["stale"]["etag"] == '"v2"'
    assert result["csrf"] == result["cookie"] == server.token
    broken = result["broken"]
    assert broken["kind"] == "http" and broken["status"] == 500
    assert broken["error"]["code"] == "stage_failed"
    assert broken["error"]["next"] == {"label": "Open the parameters", "action": "open:/settings"}
    assert broken["error"]["requestId"] == "req-2"
    assert "secret" not in broken["error"]["path"]  # no query in a diagnostic
    assert result["aborted"]["kind"] == "aborted"


def test_activity_indicator_drawer_and_cancel(ui, server):
    ui.open("/overview")
    page = ui.page
    indicator = page.locator(".cx-activity-indicator")
    assert indicator.inner_text() == "Building · keywords 45%"
    indicator.click()
    drawer = page.locator("dialog.cx-dialog--drawer[open]")
    drawer.wait_for()
    assert drawer.locator(".cx-job").count() == 2
    drawer.get_by_role("button", name="Stop").click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('.cx-job__state')]"
        ".some((el) => el.textContent.trim() === 'Stopping')",
        timeout=5000,
    )
    assert ("POST", "/api/jobs/job-0002/cancel") in server.log
    page.keyboard.press("Escape")
    drawer.wait_for(state="hidden")
    assert ui.active()["classes"].startswith("cx-activity-indicator")  # focus returns


@pytest.mark.slow
def test_information_toasts_go_and_error_toasts_stay_until_dismissed(ui, server):
    ui.open("/gallery")
    page = ui.page
    job = server.data["jobs"]["jobs"][0]
    job.update(
        state="failed",
        progress=None,
        error={"code": "stage_failed", "message": "It stopped.", "next": None},
    )
    alert = page.locator(".cx-toaster [role=alert] .cx-toast--error")
    alert.wait_for(timeout=5000)
    page.get_by_role("button", name="Show: Information").click()
    info = page.locator(".cx-toaster [aria-live=polite] .cx-toast--info")
    info.wait_for()
    page.mouse.move(0, 0)
    time.sleep(7)  # longer than an information toast lives
    assert info.count() == 0
    assert alert.count() == 1
    assert page.locator(".cx-header .cx-activity-indicator").inner_text() == "Build failed"
    alert.get_by_role("button", name="Dismiss").click()
    assert alert.count() == 0


def test_a_failing_slot_contribution_is_contained(ui):
    ui.open("/keywords")
    card = ui.page.locator('[data-slot="keywords.cards"] .cx-error-card')
    expect_text = card.inner_text()
    assert "A part of this page failed" in expect_text
    assert ui.page.locator("h1").inner_text() == "Keywords"  # the page works on


@pytest.mark.allow_console_errors  # the browser logs the module it could not load
def test_an_extension_that_fails_to_load_is_reported(ui, server):
    server.data["manifest"]["modules"].append("/static/ext/demo/missing.js")
    ui.open("/overview")
    toast = ui.page.locator(".cx-toaster [role=alert] .cx-toast--error")
    toast.wait_for()
    assert "An extension could not be loaded" in toast.inner_text()
    assert ui.page.locator("h1").inner_text() == "Coastal and marine demo"


def test_the_header_links_to_the_documentation(ui):
    ui.open("/overview")
    link = ui.page.locator('[data-nav="docs"]')
    assert link.get_attribute("href") == "/static/docs/index.html"
    assert link.get_attribute("target") == "_blank" and link.get_attribute("rel") == "noopener"
    assert link.get_attribute("aria-label") == "Documentation"
