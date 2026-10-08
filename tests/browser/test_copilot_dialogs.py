# SPDX-License-Identifier: MIT
"""The AI copilot's dialogs on the real app (the S demo world at depth 2).

The bundle is downloaded from the dialog, a short session of the kit
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


def _answer(ui, button, folder: Path, task: str) -> Path:
    """Download the bundle with *button*, answer it with the kit, and return its result file."""
    from cartolex.copilot import open_bundle

    with ui.page.expect_download() as download:
        button.click()
    body = Path(download.value.path()).read_bytes()
    zipfile.ZipFile(io.BytesIO(body)).extractall(folder)
    s = open_bundle(folder)
    if task == "themes":
        # A restructuring of more operations than one request takes (500): it comes back as
        # its tree, put in place as one step.
        s.adopt(s.regroup(s.level_sizes()), "the grouping again", curator_agreed=True)
        ops = s.changes[0]["ops"]
        while len(ops) <= 500:
            ops += [
                {"op": "set_attribution", "keywords": [k], "levels": None}
                for k in s.tree["keywords"]
            ]
        top = next(n["id"] for n in s.tree["nodes"] if n["parent"] is None)
        s.rename(top, "Coastal climate records", "its keywords are archives of past climates")
        s.set_aside(s.keywords(top)[-1:], "too general to name a theme")
    else:
        g = s.next_batch(1).split(" ")[0]
        first = s.group(g).members[0]
        s.apply(f"{g}.1 C ; a concept of the field")
        s.exclude(s.items[-1]["term"], s.items[-1]["lang"], "too generic", code="G")
        assert (s.items[first]["term"], s.items[first]["lang"]) in s.decisions
    return s.write_result("Two changes to look at first.", curator_agreed=True)


def _themes(ui, folder: Path, shots: Path | None, suffix: str) -> None:
    page = ui.page
    open_editor(ui)
    page.locator(".cx-themes__ai").click()
    d = dialog(ui)
    button = d.locator(".cx-handoff__actions button")
    button.wait_for()
    if shots:
        page.screenshot(path=str(shots / f"themes-export-{suffix}.png"))
    result = _answer(ui, button, folder, "themes")
    d.locator(".cx-dialog__footer button").last.click()
    d.locator("input[type=file]").set_input_files(str(result))
    d.locator(".cx-themes-ai__item").first.wait_for()
    d.locator(".cx-copilot__outcome summary").click()
    if shots:
        page.screenshot(path=str(shots / f"themes-review-{suffix}.png"))
    d.locator(".cx-dialog__footer button").last.click()
    # Applied and saved: nothing unsaved is left to hold the next navigation.
    page.locator(".cx-toast").first.wait_for()
    page.wait_for_function(
        "() => !document.querySelector('.cx-themes__status .cx-themes-state.is-dirty')"
    )


def _keywords(ui, folder: Path, shots: Path | None, suffix: str) -> None:
    page = ui.page
    ui.navigate("/keywords?band=check")
    page.locator(".cx-corpus__actions .cx-menubutton button").click()
    page.get_by_role("menuitem").nth(0).click()
    d = dialog(ui)
    link = d.get_by_role("button", name="Download the bundle")
    link.wait_for()
    if shots:
        page.screenshot(path=str(shots / f"keywords-export-{suffix}.png"))
    result = _answer(ui, link, folder, "triage")
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
    ui.page.locator(".cx-toast", has_text="3 proposed changes applied").wait_for()
    wait_status(ui, "Saved")
    versions = api(ui, "GET", "/api/themes/versions")["data"]["items"]
    assert len(versions) == before + 1
    _keywords(ui, tmp_path / "triage", None, "")
    ui.page.locator(".cx-toast", has_text="2 AI decisions accepted").wait_for()
    assert api(ui, "GET", "/api/keywords?route=ai-copilot")["data"]["total"] == 2
    made_by = [v["made_by"] or "" for v in api(ui, "GET", "/api/themes/versions")["data"]["items"]]
    assert any(m.startswith("ai-copilot: apply 3 changes") for m in made_by), made_by


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
