# SPDX-License-Identifier: MIT
"""Screenshots of the gallery and the overview for a person's review (not compared).

Only with ``--ui-screenshots DIR`` (or ``$CARTOLEX_UI_SCREENSHOTS``): the
gallery in light and dark, in English, French and Portuguese (Brazil), and
the overview, as full-page PNG files in DIR.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from browser_harness import prefs_script


@pytest.fixture()
def shots(request: pytest.FixtureRequest) -> Path:
    folder = request.config.getoption("--ui-screenshots")
    if not folder:
        pytest.skip("screenshots only with --ui-screenshots DIR")
    path = Path(folder).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.slow
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("locale", ["en", "fr", "pt-BR"])
def test_gallery_screenshots(ui, shots, theme, locale):
    ui.page.add_init_script(prefs_script(theme=theme, locale=locale))
    for path, name in (("/gallery", "gallery"), ("/overview", "overview")):
        ui.open(path)
        ui.page.mouse.move(0, 0)
        ui.page.screenshot(path=str(shots / f"{name}-{theme}-{locale}.png"), full_page=True)
    assert ui.missing_keys() == []


@pytest.mark.slow
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_open_state_screenshots(ui, shots, theme):
    """Dialogs, the drawer and menus are open only on demand: one picture each."""
    ui.page.add_init_script(prefs_script(theme=theme, locale="en"))
    ui.open("/gallery")
    page = ui.page
    for label, name in (
        ("Export for an assistant", "handoff-export"),
        ("Open at the review", "handoff-review"),
        ("Open a dialog", "dialog"),
    ):
        page.get_by_role("button", name=label).click()
        page.locator("dialog[open]").wait_for()
        page.screenshot(path=str(shots / f"{name}-{theme}.png"))
        page.keyboard.press("Escape")
        page.locator("dialog[open]").wait_for(state="hidden")
    page.locator(".cx-header .cx-activity-indicator").click()
    page.locator("dialog[open]").wait_for()
    page.screenshot(path=str(shots / f"activity-drawer-{theme}.png"))
    page.keyboard.press("Escape")
    grid = page.get_by_role("grid", name="Keyword candidates", exact=True)
    grid.focus()
    for key in ("ArrowDown", "Shift+ArrowDown", "Shift+ArrowDown", "Shift+F10"):
        page.keyboard.press(key)
    page.get_by_role("menu", name="Actions on the selected rows").wait_for()
    page.screenshot(path=str(shots / f"table-menu-{theme}.png"))
