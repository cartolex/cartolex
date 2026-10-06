# SPDX-License-Identifier: MIT
"""The header: the project menu (recent projects, a held one opened anyway, a busy app), the
version at the foot of the settings menu, the logo leading to the About page."""

from __future__ import annotations

import pytest

expect = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed").expect


@pytest.mark.allow_console_errors  # the 409 of a held project, on purpose
def test_the_project_menu_lists_the_recent_projects_and_opens_a_held_one_after_a_warning(
    ui, server
):
    ui.open("/overview")
    page = ui.page
    button = page.locator(".cx-project-menu__button")
    assert button.inner_text().strip() == "Coastal and marine demo"
    button.focus()
    page.keyboard.press("Enter")
    menu = page.locator(".cx-project-menu [role=menu]")
    menu.wait_for()
    names = menu.locator("[role=menuitem]").all_inner_texts()
    assert not any("Coastal" in n for n in names)  # the open project is not offered again
    assert any("Estuary survey" in n for n in names) and any("New project" in n for n in names)
    missing = menu.get_by_role("menuitem", name="Dune morphology")
    assert missing.get_attribute("aria-disabled") == "true"
    menu.get_by_role("menuitem", name="Reef ecology").click()
    dialog = page.get_by_role("alertdialog", name="Open the project anyway?")
    dialog.wait_for()
    assert "another-computer" in dialog.inner_text()
    with page.expect_navigation():
        dialog.get_by_role("button", name="Open anyway").click()
    assert server.opened == ["/projects/reefs-held"]


@pytest.mark.allow_console_errors  # the 409 of a busy app, on purpose
def test_a_busy_app_says_so_and_leads_to_the_activity(ui):
    ui.open("/overview")
    page = ui.page
    page.locator(".cx-project-menu__button").click()
    page.get_by_role("menuitem", name="Tide gauges").click()
    dialog = page.get_by_role("alertdialog", name="The project could not be opened")
    expect(dialog).to_contain_text("A job is running")
    dialog.get_by_role("button", name="See the Activity").click()
    page.get_by_role("dialog", name="Activity").wait_for()


def test_the_logo_leads_to_the_about_page_and_the_settings_menu_names_the_build(ui):
    ui.open("/overview")
    page = ui.page
    page.locator(".cx-header .cx-menubutton button").click()
    about = page.get_by_role("menuitem", name="About cartolex")
    expect(about).to_contain_text("1.0.0.dev0 · 0123456 · 2026-10-06")
    page.keyboard.press("Escape")
    assert page.get_by_role("menuitem", name="Projects").count() == 0  # in the project menu now
    page.locator(".cx-brand").click()
    page.wait_for_url("**/about")
    ui.wait_ready(1)
    assert page.locator("h1").inner_text() == "About cartolex"
    assert page.title() == "About cartolex · Coastal and marine demo · cartolex"
    figure = page.get_by_role("img", name="How a map is made")
    assert figure.locator("text").count() >= 18  # six steps, three lines each
    expect(page.locator(".cx-about__citation")).to_contain_text("Ada Example, Ben Sample (2026)")
    expect(page.locator(".cx-about__version")).to_have_text("1.0.0.dev0 · 0123456 · 2026-10-06")
    assert page.locator(".cx-about__ref").count() >= 10
    assert ui.missing_keys() == []
