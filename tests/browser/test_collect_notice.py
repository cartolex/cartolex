# SPDX-License-Identifier: MIT
"""The collection notice's levels on the real app and the demo services (world XS): the full
notice the first time with « Don't show this again », then a brief confirmation; an
institution searched by its name starts at once, said in a toast; the reset in Settings ›
Privacy. With ``--ui-screenshots DIR``, the notices are written to ``DIR/collect/`` (English
light and dark, French), with OpenAlex's cost beyond the free budget on a collaborators'
round."""

from __future__ import annotations

from pathlib import Path

import pytest
from browser_harness import prefs_script
from test_corpus import REAL_ROW, _wait_jobs, corpus_app, services  # noqa: F401


def _open_identify(page) -> None:
    page.get_by_role("button", name="Collect", exact=True).click()
    page.get_by_role("menuitem", name="Find identities").click()
    page.get_by_role("button", name="What leaves the computer").click()


def test_the_notice_brief_once_acknowledged_and_none_for_a_search(corpus_app, open_app):  # noqa: F811
    ui = open_app(corpus_app)
    page = ui.page
    ui.navigate("/people")
    page.locator(f".cx-corpus {REAL_ROW}").first.wait_for()

    # ── the first time: the whole notice, the consent and « Don't show this again » ──
    _open_identify(page)
    page.locator(".cx-corpus-notice").wait_for()
    page.get_by_label("I have read what leaves the computer").check()
    page.get_by_label("Don’t show this again for identity searches").check()
    page.get_by_role("button", name="Start").click()
    assert _wait_jobs(ui)["state"] == "succeeded"

    # ── then one sentence, Start ready, the whole notice under « Details » ──
    _open_identify(page)
    brief = page.locator(".cx-corpus-brief")
    brief.wait_for()
    assert "OpenAlex" in brief.inner_text()
    assert page.get_by_role("button", name="Start").is_enabled()
    page.keyboard.press("Escape")
    brief.wait_for(state="detached")

    # ── an institution searched by its name: no dialog, the job starts, a toast says so ──
    page.get_by_role("tab", name="Organisations").click()
    page.get_by_placeholder("Name of an institution").fill("Marine")
    page.get_by_role("button", name="Find", exact=True).click()
    page.locator(".cx-toast", has_text="Nothing personal leaves the computer").wait_for()
    assert page.locator("dialog[open]").count() == 0
    assert _wait_jobs(ui)["state"] == "succeeded"

    # ── Settings › Privacy: the kinds acknowledged, and every notice in full again ──
    ui.navigate("/settings?section=privacy")
    page.get_by_role("button", name="Show every notice in full again").click()
    page.get_by_text("Every notice is shown in full.").wait_for()


@pytest.mark.slow
def test_screenshots_of_the_notices(corpus_app, open_app, pytestconfig):  # noqa: F811
    target = pytestconfig.getoption("--ui-screenshots")
    if not target:
        pytest.skip("pass --ui-screenshots DIR to write the screenshots")
    from cartolex.collect.privacy import openalex_budget

    out = Path(target) / "collect"
    out.mkdir(parents=True, exist_ok=True)
    collection = corpus_app.app.state.cartolex.collection
    plan = collection.plan

    def costly(project, action="identify", options=None):  # a large round, as if online
        found = plan(project, action, options)
        if action == "collaborators":
            found["budget"] = openalex_budget(14.2, False)
            found["estimate"]["cost_usd"] = 14.2
            for h in found["leaves_the_computer"]:
                h["cost_usd"] = 14.2 if h["service"] == "openalex" else None
        return found

    collection.plan = costly
    ui = open_app(corpus_app)  # the launch link works once per app
    page = ui.page
    try:
        for theme, locale in (("light", "en"), ("dark", "en"), ("light", "fr")):
            page.add_init_script(prefs_script(theme=theme, locale=locale))
            page.goto(f"{corpus_app.url}/people")
            page.locator(f".cx-corpus {REAL_ROW}").first.wait_for()
            page.locator(".cx-corpus__actions button").last.click()
            page.get_by_role("menuitem").first.click()
            page.locator(".cx-dialog .cx-button--primary").click()
            page.locator(".cx-corpus-notice").wait_for()
            page.locator(".cx-corpus-consent input").first.check()
            page.locator(".cx-corpus-consent input").nth(1).check()
            page.wait_for_timeout(200)
            page.screenshot(path=str(out / f"notice-full-{theme}-{locale}.png"), full_page=True)
            page.locator(".cx-dialog .cx-button--primary").click()
            _wait_jobs(ui)
            page.locator(".cx-corpus__actions button").last.click()
            page.get_by_role("menuitem").first.click()
            page.locator(".cx-dialog .cx-button--primary").click()
            page.locator(".cx-corpus-brief").wait_for()
            page.wait_for_timeout(200)
            page.screenshot(path=str(out / f"notice-brief-{theme}-{locale}.png"), full_page=True)
            page.keyboard.press("Escape")
            page.locator(".cx-corpus__actions button").last.click()
            page.get_by_role("menuitem").nth(3).click()
            page.locator(".cx-dialog .cx-button--primary").click()
            page.locator(".cx-corpus-budget").wait_for()
            page.wait_for_timeout(200)
            page.screenshot(path=str(out / f"notice-budget-{theme}-{locale}.png"), full_page=True)
            page.keyboard.press("Escape")
            ui.navigate("/settings?section=privacy")
            page.locator(".cx-settings__rows").wait_for()
            page.wait_for_timeout(200)
            page.screenshot(path=str(out / f"privacy-{theme}-{locale}.png"), full_page=True)
            page.locator(".cx-settings__section .cx-button--secondary").click()
            page.locator(".cx-settings__rows").wait_for(state="detached")
    finally:
        collection.plan = plan
