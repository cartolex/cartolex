# SPDX-License-Identifier: MIT
"""Keyboard scripts: every component of the gallery reached and operated without a mouse.

Focus starts from the page's top and moves with Tab, Shift+Tab, arrows,
Enter, Space and Escape only; `locator.focus()` is used only to start a
script at a component (the Tab order itself is checked separately).
"""

from __future__ import annotations

import pytest

CONTEXT = {"permissions": ["clipboard-read", "clipboard-write"]}
#: Sections with something to operate (tokens, status, tracker and icons only show).
INTERACTIVE = [
    "button",
    "card",
    "stepper",
    "tabs",
    "table",
    "menu",
    "dialog",
    "toast",
    "form",
    "empty",
    "error",
    "progress",
    "tooltip",
    "handoff",
    "activity",
]


def tab_until(ui, predicate, limit: int = 60, key: str = "Tab") -> dict:
    """Press *key* until the focused element satisfies *predicate*; return its description."""
    for _ in range(limit):
        ui.page.keyboard.press(key)
        active = ui.active()
        if predicate(active):
            return active
    raise AssertionError(f"not reached with {key} in {limit} presses; last: {ui.active()}")


def focused_text(ui) -> str:
    return ui.active()["text"]


@pytest.fixture()
def gallery(ui):
    ui.open("/gallery")
    return ui


def test_tab_reaches_every_interactive_section_in_order(gallery):
    ui = gallery
    ui.page.keyboard.press("Tab")
    assert focused_text(ui) == "Skip to content"
    seen: list[str] = []
    for _ in range(600):
        ui.page.keyboard.press("Tab")
        active = ui.active()
        assert active["tag"] != "body", f"the focus fell to the page after {seen[-1:]}"
        section = active["section"]
        if section and (not seen or seen[-1] != section):
            seen.append(section)
        if section == "activity" and "Activity" in active["text"]:
            break
    assert [s for s in INTERACTIVE if s not in seen] == []
    # Sections come once each, in the page's order: no focus jumps back and forth.
    assert len(seen) == len(set(seen))


def test_skip_link_and_navigation(gallery):
    ui = gallery
    page = ui.page
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    assert ui.active()["id"] == "cx-main"
    page.keyboard.press("Shift+Tab")
    tab_until(ui, lambda a: a["text"] == "Keywords", key="Shift+Tab")
    before = ui.token()
    page.keyboard.press("Enter")
    ui.wait_ready(before)
    assert page.url.endswith("/keywords")
    assert ui.active()["tag"] == "h1"


def test_buttons(gallery):
    page = gallery.page
    page.get_by_role("button", name="Build", exact=True).focus()
    page.keyboard.press("Enter")
    page.keyboard.press("Space")
    assert gallery.section("button").get_by_role("status").inner_text() == "Pressed 2 times."
    loading = gallery.section("button").get_by_role("button", name="Loading").first
    loading.focus()
    page.keyboard.press("Enter")  # a loading button keeps the focus and ignores presses
    assert gallery.active()["text"].startswith("Loading")
    assert gallery.section("button").get_by_role("status").inner_text() == "Pressed 2 times."


def test_tabs_automatic_and_manual(gallery):
    page = gallery.page
    auto = page.get_by_role("tablist", name="Keyword bands", exact=True)
    auto.get_by_role("tab", name="Kept").focus()
    page.keyboard.press("ArrowRight")
    assert auto.get_by_role("tab", selected=True).inner_text().startswith("To check")
    page.keyboard.press("End")  # the disabled tab is skipped
    assert auto.get_by_role("tab", selected=True).inner_text().startswith("Set aside")
    page.keyboard.press("ArrowRight")  # wraps
    assert auto.get_by_role("tab", selected=True).inner_text().startswith("Kept")
    manual = page.get_by_role("tablist", name="Keyword bands, manual selection")
    manual.get_by_role("tab", name="Kept").focus()
    page.keyboard.press("ArrowRight")
    assert focused_text(gallery).startswith("To check")
    assert manual.get_by_role("tab", selected=True).inner_text().startswith("Kept")
    page.keyboard.press("Enter")
    assert manual.get_by_role("tab", selected=True).inner_text().startswith("To check")


def _active_row(page, grid):
    row_id = grid.get_attribute("aria-activedescendant")
    return page.locator(f"#{row_id}") if row_id else None


def test_table_navigation_selection_sort_and_menu(gallery):
    page = gallery.page
    section = gallery.section("table")
    grid = page.get_by_role("grid", name="Keyword candidates", exact=True)
    status = section.locator(".cx-table__status").first
    grid.focus()
    page.keyboard.press("ArrowDown")
    assert _active_row(page, grid).get_attribute("aria-rowindex") == "2"
    page.keyboard.press("Space")
    page.keyboard.press("Shift+ArrowDown")
    page.keyboard.press("Shift+ArrowDown")
    assert status.inner_text() == "100,000 rows · 3 selected"
    assert grid.locator('[role=row][aria-selected="true"]').count() == 3
    page.keyboard.press("Control+a")
    assert status.inner_text() == "100,000 rows · 100,000 selected"
    page.keyboard.press("Escape")
    assert status.inner_text() == "100,000 rows"
    page.keyboard.press("End")
    last = _active_row(page, grid)
    assert last.get_attribute("aria-rowindex") == "100001"
    assert last.is_visible()
    page.keyboard.press("Home")
    page.keyboard.press("PageDown")
    assert int(_active_row(page, grid).get_attribute("aria-rowindex")) > 5
    page.keyboard.press("Enter")
    term = _active_row(page, grid).locator("[role=gridcell]").first.inner_text()
    assert section.get_by_role("status").inner_text() == f"Opened: {term}"
    # Sort from the keyboard: the header's button follows the grid in the Tab order.
    page.keyboard.press("Tab")
    assert focused_text(gallery) == "Term"
    page.keyboard.press("Enter")
    header = grid.get_by_role("columnheader", name="Term")
    assert header.get_attribute("aria-sort") == "ascending"
    page.keyboard.press("Enter")
    assert header.get_attribute("aria-sort") == "descending"
    # The context menu of the selected rows.
    grid.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Shift+F10")
    menu = page.get_by_role("menu", name="Actions on the selected rows")
    menu.wait_for()
    assert focused_text(gallery) == "Keep this term"
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    menu.wait_for(state="hidden")
    assert "Exclude this term (1 row)" in page.locator(".cx-toast__title").last.inner_text()
    assert gallery.active()["role"] == "grid"


def test_table_update_keeps_scroll_focus_and_selection(gallery):
    page = gallery.page
    grid = page.get_by_role("grid", name="Keyword candidates", exact=True)
    grid.focus()
    for _ in range(4):
        page.keyboard.press("PageDown")
    page.keyboard.press("Space")
    row = _active_row(page, grid)
    term = row.locator("[role=gridcell]").first.inner_text()
    y = row.bounding_box()["y"]
    # Updates made elsewhere (by script: a click would move the focus to the button).
    page.evaluate(
        "() => [...document.querySelectorAll('[data-gallery=table] button')]"
        ".find((b) => b.textContent.trim() === 'Insert a row at the top').click()"
    )
    page.evaluate(
        "() => [...document.querySelectorAll('[data-gallery=table] button')]"
        ".find((b) => b.textContent.trim() === 'Update every other row').click()"
    )
    page.wait_for_function(
        "() => document.querySelector('[data-gallery=table] .cx-table__status')"
        ".textContent.includes('100,001')"
    )
    assert gallery.active()["role"] == "grid"  # the focus stayed
    row = _active_row(page, grid)
    assert row.locator("[role=gridcell]").first.inner_text() == term  # the same row is active
    assert row.get_attribute("aria-selected") == "true"  # and still selected
    assert abs(row.bounding_box()["y"] - y) <= 1  # at the same place on the screen


def test_menu_button_and_context_menu(gallery):
    page = gallery.page
    section = gallery.section("menu")
    button = section.get_by_role("button", name="Actions", exact=True)
    button.focus()
    page.keyboard.press("Enter")
    assert focused_text(gallery) == "Open"
    page.keyboard.press("ArrowDown")
    assert focused_text(gallery) == "Rename"
    page.keyboard.press("ArrowDown")  # the disabled item is skipped
    assert focused_text(gallery) == "By name"
    page.keyboard.press("w")  # type-ahead
    assert focused_text(gallery) == "Wrap long lines"
    page.keyboard.press("Enter")
    assert gallery.active()["text"].startswith("Actions")
    assert section.get_by_role("status").inner_text() == "Chosen: Wrap long lines"
    page.keyboard.press("ArrowUp")
    assert focused_text(gallery) == "Delete"
    page.keyboard.press("Escape")
    assert gallery.active()["text"].startswith("Actions")
    assert button.get_attribute("aria-expanded") == "false"
    page.keyboard.press("Space")
    page.keyboard.press("Tab")  # Tab closes the menu
    page.get_by_role("menu", name="Actions", exact=True).wait_for(state="hidden")
    target = section.locator(".cx-gallery__target")
    target.focus()
    page.keyboard.press("Shift+F10")
    page.get_by_role("menu", name="Context menu").wait_for()
    page.keyboard.press("Escape")
    assert gallery.active()["classes"] == "cx-gallery__target"


def test_display_menu_changes_the_theme(gallery):
    page = gallery.page
    page.locator(".cx-header .cx-menubutton button").focus()
    page.keyboard.press("Enter")
    tab_until(gallery, lambda a: a["text"] == "Dark", key="ArrowDown", limit=10)
    page.keyboard.press("Enter")
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
    assert gallery.active()["label"] == "Display settings"


def test_dialog_traps_focus_closes_on_escape_and_gives_focus_back(gallery):
    page = gallery.page
    opener = page.get_by_role("button", name="Open a dialog")
    opener.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="Rename the version")
    dialog.wait_for()
    assert gallery.active()["tag"] == "input"
    for key in ["Tab"] * 5 + ["Shift+Tab"] * 5:
        page.keyboard.press(key)
        assert page.evaluate("() => document.activeElement.closest('dialog[open]') !== null")
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    assert gallery.active()["text"] == "Open a dialog"
    page.keyboard.press("Enter")
    page.keyboard.type("Delta")
    tab_until(gallery, lambda a: a["text"] == "Save")
    page.keyboard.press("Enter")
    assert gallery.section("dialog").get_by_role("status").inner_text() == "Saved: Delta"
    assert gallery.active()["text"] == "Open a dialog"


def test_confirm_dialog_and_drawer(gallery):
    page = gallery.page
    page.get_by_role("button", name="Ask before leaving").focus()
    page.keyboard.press("Enter")
    assert focused_text(gallery) == "Stay on the page"
    page.keyboard.press("Tab")
    assert focused_text(gallery) == "Leave without saving"
    page.keyboard.press("Enter")
    assert gallery.section("dialog").get_by_role("status").inner_text() == "You chose to leave."
    page.get_by_role("button", name="Open a drawer").focus()
    page.keyboard.press("Enter")
    drawer = page.get_by_role("dialog", name="Details")
    drawer.wait_for()
    assert page.evaluate("() => document.activeElement.closest('dialog[open]') !== null")
    page.keyboard.press("Escape")
    drawer.wait_for(state="hidden")
    assert gallery.active()["text"] == "Open a drawer"


def test_toasts_are_reached_and_dismissed(gallery):
    page = gallery.page
    page.get_by_role("button", name="Show: Error").focus()
    page.keyboard.press("Enter")
    toast = page.locator(".cx-toaster [role=alert] .cx-toast--error")
    toast.wait_for()
    tab_until(gallery, lambda a: a["label"] == "Dismiss" and a["section"] is None, limit=400)
    page.keyboard.press("Enter")
    toast.wait_for(state="detached")


def test_form_fields_select_and_checkbox(gallery):
    page = gallery.page
    section = gallery.section("form")
    name = section.get_by_label("Name", exact=True)
    name.focus()
    page.keyboard.type("Gamma")
    assert name.input_value() == "Gamma"
    year = section.get_by_label("First year")
    assert year.get_attribute("aria-invalid") == "true"
    assert (
        "Write a year" in section.locator(f"#{year.get_attribute('aria-describedby')}").inner_text()
    )
    select = section.get_by_label("Language")
    select.focus()
    page.keyboard.press("ArrowDown")
    assert select.input_value() == "fr"
    box = section.get_by_label("Keep the pinned map version")
    box.focus()
    page.keyboard.press("Space")
    assert not box.is_checked()


def test_stepper_goes_back_to_a_done_step(gallery):
    page = gallery.page
    section = gallery.section("stepper")
    section.get_by_role("button", name="Choose the source").focus()
    page.keyboard.press("Enter")
    current = section.locator('[aria-current="step"]').first
    assert current.inner_text().startswith("1")


def test_tooltip_and_help(gallery):
    page = gallery.page
    section = gallery.section("tooltip")
    trigger = section.get_by_role("button", name="Hover or focus me")
    page.get_by_role("button", name="Go back 30 %").focus()
    tab_until(gallery, lambda a: a["text"] == "Hover or focus me")
    tip = section.locator(f"#{trigger.get_attribute('aria-describedby')}")
    assert tip.is_visible() and tip.inner_text() == "Rebuild only what changed"
    page.keyboard.press("Escape")
    tip.wait_for(state="hidden")
    help_button = section.get_by_role("button", name="Help: specificity")
    help_button.focus()
    page.keyboard.press("Enter")
    panel = section.locator(f"#{help_button.get_attribute('aria-controls')}")
    panel.wait_for()
    page.keyboard.press("Escape")
    panel.wait_for(state="hidden")
    assert gallery.active()["label"] == "Help: specificity"


@pytest.mark.parametrize("ui", [CONTEXT], indirect=True)
def test_error_card_details_and_diagnostic(gallery):
    page = gallery.page
    card = gallery.section("error").locator(".cx-error-card").nth(1)
    card.locator("summary").focus()
    page.keyboard.press("Enter")
    assert card.locator("details").get_attribute("open") is not None
    tab_until(gallery, lambda a: a["text"] == "Copy a diagnostic")
    page.keyboard.press("Enter")
    card.get_by_role("status").filter(has_text="Diagnostic copied").wait_for()
    copied = page.evaluate("() => navigator.clipboard.readText()")
    assert "code: network" in copied and "request: GET /api/project/state" in copied


def test_progress_bar_never_goes_back(gallery):
    page = gallery.page
    section = gallery.section("progress")
    bar = section.get_by_role("progressbar", name="Example progress 2")
    page.get_by_role("button", name="Add 10 %").focus()
    page.keyboard.press("Enter")
    assert bar.get_attribute("aria-valuenow") == "55"
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")  # « Go back 30 % »
    assert bar.get_attribute("aria-valuenow") == "55"


def test_ai_handoff_from_export_to_import(gallery):
    page = gallery.page
    opener = page.get_by_role("button", name="Export for an assistant")
    opener.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="Ask an AI assistant")
    dialog.wait_for()
    assert "never contains" in dialog.inner_text()
    tab_until(gallery, lambda a: a["text"] == "I have the answer")
    page.keyboard.press("Enter")
    assert gallery.active()["tag"] == "textarea"
    page.keyboard.type("C en sediment transport=sediment transport\nG dune erosion")
    tab_until(gallery, lambda a: a["text"] == "Check the answer")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.activeElement.classList.contains('cx-handoff')")
    assert "2 of the 12 terms have an answer." in dialog.inner_text()
    tab_until(gallery, lambda a: a["text"] == "Import these decisions")
    page.keyboard.press("Enter")
    dialog.wait_for(state="hidden")
    assert gallery.active()["text"] == "Export for an assistant"
    assert "The decisions are imported" in page.locator(".cx-toast__title").last.inner_text()


def test_activity_indicator_and_drawer(gallery):
    page = gallery.page
    page.locator(".cx-header .cx-activity-indicator").focus()
    page.keyboard.press("Enter")
    drawer = page.get_by_role("dialog", name="Activity")
    drawer.wait_for()
    tab_until(gallery, lambda a: a["text"] == "Stop")
    page.keyboard.press("Escape")
    drawer.wait_for(state="hidden")
    assert gallery.active()["classes"].startswith("cx-activity-indicator")
