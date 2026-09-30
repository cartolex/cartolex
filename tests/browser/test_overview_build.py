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
    page.locator(".cx-overview-preview .cx-map-frame__canvas").wait_for()
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
        ui.page.locator(".cx-overview-preview .cx-map-frame__canvas").wait_for()
        _shots(request, ui, f"overview-{theme}-{locale}")
        ui.navigate("/build?scope=map")
        ui.page.locator(".cx-build .cx-card").first.wait_for()
        _shots(request, ui, f"build-{theme}-{locale}")


def _copilot_answer(ui, button, folder: Path) -> Path:
    """Download the themes bundle with *button* and answer it with the kit: one rename."""
    import io
    import zipfile

    from cartolex.copilot import open_bundle

    with ui.page.expect_download() as download:
        button.click()
    zipfile.ZipFile(io.BytesIO(Path(download.value.path()).read_bytes())).extractall(folder)
    s = open_bundle(folder)
    top = next(n["id"] for n in s.tree["nodes"] if n["parent"] is None)
    s.rename(top, "Coastal climate records", "its keywords are archives of past climates")
    return s.write_result("One change.", curator_agreed=True)


def _wait_for_the_copilot(ui) -> None:
    """Choose the copilot for the theme curation and start a build that pauses for it."""
    page = ui.page
    ui.navigate("/build?force=themes.group")
    page.locator(".cx-build-pre").wait_for()
    step = page.locator("[data-ai-step='themes.curation']")
    step.get_by_label("With your copilot").check()
    page.locator("[data-pause='themes.curation']").wait_for()
    assert step.get_by_label("By API").is_disabled()
    rows = page.locator(".cx-build-pre__plan .cx-tracker__stage")
    assert rows.filter(has_text="Waits for your copilot").count() >= 1
    page.get_by_role("button", name="Build 1 stage, then pause").click()
    page.locator(".cx-build-wait [data-outcome='waiting']").wait_for(timeout=60_000)


def test_a_build_pauses_for_the_copilot_and_continues(request, demo_s, app_for, open_app, tmp_path):
    ui = open_app(app_for(demo_s))
    page = ui.page
    _wait_for_the_copilot(ui)
    assert api(ui, "GET", "/api/build")["data"]["job"]["state"] == "waiting"
    _shots(request, ui, "build-waiting-light")

    ui.navigate("/overview")
    assert _next_step(ui) == "next_copilot_waiting"
    page.locator("[data-next] button").click()
    page.get_by_role("button", name="Open the theme copilot").click()
    d = page.locator("dialog[open]")  # the editor opens the copilot's dialog at once
    button = d.locator(".cx-handoff__actions button")
    button.wait_for()
    result = _copilot_answer(ui, button, tmp_path / "themes")
    d.locator(".cx-dialog__footer button").last.click()
    d.locator("input[type=file]").set_input_files(str(result))
    d.locator(".cx-themes-ai__item").first.wait_for()
    d.locator(".cx-dialog__footer button").last.click()
    page.locator(".cx-toast", has_text="1 proposed change applied").wait_for()
    page.wait_for_function(
        "() => !document.querySelector('.cx-themes__status .cx-themes-state.is-dirty')"
    )

    ui.navigate("/build")
    page.get_by_role("button", name="Continue the build").click()
    sentence = page.locator(".cx-build-result__sentence[data-outcome='succeeded']")
    sentence.wait_for(timeout=60_000)
    ran = api(ui, "GET", "/api/build")["data"]["job"]["result"]["ran"]
    assert ran[0] == "themes.apply", ran
    assert ui.collected.console_errors == [] and ui.collected.page_errors == []


def test_the_copilot_pause_screenshots(request, demo_s, app_for, open_app):
    """The waiting card in dark and in French, for review (only with --ui-screenshots)."""
    if not request.config.getoption("--ui-screenshots"):
        pytest.skip("screenshots only with --ui-screenshots DIR")
    for theme, locale in (("dark", "en"), ("light", "fr")):
        ui = open_app(app_for(demo_s), theme=theme, locale=locale)
        _next_step(ui)  # the launch link opens the overview
        version = api(ui, "POST", "/api/build", {"dry_run": True})["data"]["ai"]["version"]
        api(ui, "PUT", "/api/build/ai", {"themes.curation": "copilot"}, f'"{version}"')
        ui.navigate("/build?force=themes.group")
        ui.page.locator("[data-pause='themes.curation']").wait_for()
        _shots(request, ui, f"build-ai-{theme}-{locale}")
        ui.page.locator(".cx-build-actions .cx-button--primary").first.click()
        ui.page.locator(".cx-build-wait [data-outcome='waiting']").wait_for(timeout=60_000)
        _shots(request, ui, f"build-waiting-{theme}-{locale}")
