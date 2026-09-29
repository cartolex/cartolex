# SPDX-License-Identifier: MIT
"""The overview and the build on the real app (the S demo world, built at depth 2).

One scenario: the overview shows the next step, the stages and the map's
preview; a parameter changes, the next step becomes « Build »; it opens the
pre-flight sheet, which starts the build; the tracker follows it and it ends
in one sentence. With ``--ui-screenshots DIR``, the pages are also written
there in both themes and in French.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from browser_harness import prefs_script
from test_theme_editor import api


def _shots(request, ui, name: str) -> None:
    folder = request.config.getoption("--ui-screenshots")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        ui.page.mouse.move(0, 0)
        ui.page.screenshot(path=str(Path(folder) / f"{name}.png"), full_page=True)


def _next_step(ui) -> str:
    ui.page.locator("[data-next]").wait_for()
    return ui.page.locator("[data-next]").get_attribute("data-next")


@pytest.mark.parametrize("theme", ["light"])
def test_the_next_step_leads_to_a_build_that_ends_in_one_sentence(
    request, demo_s, app_for, open_app, theme
):
    server = app_for(demo_s)
    ui = open_app(server, theme=theme)
    page = ui.page  # the launch link opens the overview
    assert _next_step(ui) in ("next_curate_themes", "next_open_map")
    page.locator(".cx-overview-preview canvas").wait_for()
    assert page.locator(".cx-overview-stages .cx-tracker__stage").count() >= 5
    _shots(request, ui, "overview-built-light")

    # A new width of the time windows: the stage « change over time » needs an update.
    view = api(ui, "GET", "/api/params")
    data = view["data"]
    stages = {
        s["id"]: {p["name"]: p["value"] for p in s["params"] if p["set_in_file"]}
        for s in data["stages"]
    }
    stages["map.trajectories"] = {**stages.get("map.trajectories", {}), "window_years": 4}
    body = {
        "seed": data["global"]["seed"]["value"],
        "pinned_year": data["global"]["pinned_year"]["value"],
        "stages": stages,
    }
    assert api(ui, "PUT", "/api/params", body, view["etag"])["status"] == 200

    page.reload()
    page.wait_for_function(
        "() => document.querySelector('[data-next]')?.dataset.next === 'next_update'"
    )
    page.locator("[data-next] button").click()
    page.locator(".cx-build-pre").wait_for()
    assert "/build" in page.url
    rows = page.locator(".cx-build-pre__plan .cx-tracker__stage")
    assert rows.filter(has_text="Will run").count() == 1
    _shots(request, ui, "build-preflight-light")
    page.get_by_role("button", name="Build 1 stage").click()
    sentence = page.locator(".cx-build-result__sentence")
    sentence.wait_for(timeout=60_000)
    assert sentence.get_attribute("data-outcome") == "succeeded"
    assert sentence.inner_text().startswith("Built 1 stage")
    _shots(request, ui, "build-result-light")
    assert ui.collected.console_errors == [] and ui.collected.page_errors == []


def test_overview_and_build_screenshots(request, demo_s, app_for, open_app):
    """The pages in dark and in French, for review (only with --ui-screenshots)."""
    if not request.config.getoption("--ui-screenshots"):
        pytest.skip("screenshots only with --ui-screenshots DIR")
    for theme, locale in (("dark", "en"), ("light", "fr")):
        ui = open_app(app_for(demo_s), theme=theme)  # the launch link works once per app
        ui.page.add_init_script(prefs_script(theme=theme, locale=locale))
        ui.page.reload()
        _next_step(ui)
        ui.page.locator(".cx-overview-preview canvas").wait_for()
        _shots(request, ui, f"overview-{theme}-{locale}")
        ui.navigate("/build?scope=map")
        ui.page.locator(".cx-build .cx-card").first.wait_for()
        _shots(request, ui, f"build-{theme}-{locale}")
