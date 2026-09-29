# SPDX-License-Identifier: MIT
"""The AI copilot's dialogs on the real app (the S demo world at depth 2).

The bundle is downloaded from the dialog's link, a short session of the kit
answers it (in this process), and its ``result.json`` goes back through the
dialog: the theme changes are applied and saved as a version, the keyword
decisions accepted. The review screenshots with ``--ui-screenshots``.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from test_theme_editor import api, dialog, open_editor, wait_status


def _answer(ui, href: str, folder: Path, task: str) -> Path:
    """Download the bundle at *href*, answer it with the kit, and return its result file."""
    from cartolex.copilot import open_bundle

    body = ui.page.request.get(ui.server.url.rstrip("/") + href).body()
    zipfile.ZipFile(io.BytesIO(body)).extractall(folder)
    s = open_bundle(folder)
    if task == "themes":
        top = next(n["id"] for n in s.tree["nodes"] if n["parent"] is None)
        s.rename(top, "Coastal climate records", "its keywords are archives of past climates")
        s.set_aside(s.keywords(top)[-1:], "too general to name a theme")
    else:
        s.keep(s.items[0]["term"], s.items[0]["lang"], "a concept of the field")
        s.exclude(s.items[-1]["term"], s.items[-1]["lang"], "too generic", code="G")
    return s.write_result("Two changes to look at first.", curator_agreed=True)


def _themes(ui, folder: Path, shots: Path | None, suffix: str) -> None:
    page = ui.page
    open_editor(ui)
    page.locator(".cx-themes__toolbar .cx-menubutton button").last.click()
    page.get_by_role("menu").get_by_role("menuitem").nth(2).click()
    d = dialog(ui)
    link = d.locator("a[download]")
    link.wait_for()
    if shots:
        page.screenshot(path=str(shots / f"themes-export-{suffix}.png"))
    result = _answer(ui, link.get_attribute("href"), folder, "themes")
    d.locator(".cx-dialog__footer button").last.click()
    d.locator("input[type=file]").set_input_files(str(result))
    d.locator(".cx-themes-ai__item").first.wait_for()
    d.locator(".cx-copilot__outcome summary").click()
    if shots:
        page.screenshot(path=str(shots / f"themes-review-{suffix}.png"))
    d.locator(".cx-dialog__footer button").last.click()


def _keywords(ui, folder: Path, shots: Path | None, suffix: str) -> None:
    page = ui.page
    ui.navigate("/keywords?band=check")
    page.locator(".cx-corpus__actions .cx-menubutton button").click()
    page.get_by_role("menuitem").nth(2).click()
    d = dialog(ui)
    link = d.locator("a[download]")
    link.wait_for()
    if shots:
        page.screenshot(path=str(shots / f"keywords-export-{suffix}.png"))
    result = _answer(ui, link.get_attribute("href"), folder, "triage")
    d.locator(".cx-dialog__footer button").last.click()
    d.locator("input[type=file]").set_input_files(str(result))
    d.locator(".cx-kw-review").wait_for()
    if shots:
        page.screenshot(path=str(shots / f"keywords-review-{suffix}.png"))
    d.locator(".cx-dialog__footer button").last.click()


def test_a_copilot_result_is_reviewed_applied_and_saved(demo_s, app_for, open_app, tmp_path):
    server = app_for(demo_s)
    ui = open_app(server)
    ui.server = server
    before = len(api(ui, "GET", "/api/themes/versions")["data"]["items"])
    _themes(ui, tmp_path / "themes", None, "")
    ui.page.locator(".cx-toast", has_text="2 proposed changes applied").wait_for()
    wait_status(ui, "Saved")
    versions = api(ui, "GET", "/api/themes/versions")["data"]["items"]
    assert len(versions) == before + 1
    _keywords(ui, tmp_path / "triage", None, "")
    ui.page.locator(".cx-toast", has_text="2 AI decisions accepted").wait_for()


@pytest.mark.slow
def test_screenshots_of_the_copilot(demo_s, app_for, open_app, pytestconfig, tmp_path):
    target = pytestconfig.getoption("--ui-screenshots")
    if not target:
        pytest.skip("pass --ui-screenshots DIR to write the screenshots")
    out = Path(target) / "copilot"
    out.mkdir(parents=True, exist_ok=True)
    for theme, locale in (("light", "en"), ("dark", "en"), ("light", "fr")):
        server = app_for(demo_s)
        ui = open_app(server, theme=theme, locale=locale)
        ui.server = server
        suffix = f"{theme}-{locale}"
        _themes(ui, tmp_path / f"themes-{suffix}", out, suffix)
        _keywords(ui, tmp_path / f"triage-{suffix}", out, suffix)
        ui.page.context.close()
