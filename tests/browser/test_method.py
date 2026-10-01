# SPDX-License-Identifier: MIT
"""The method screen (`/method`) on the real app, the S demo world at depth 2.

One scenario over the steps: the API calls of a step, a parameter changed
(its mark, saved, not built yet) and put back, the tiers' folds, the parts by
slot kind, the providers' order and the levels edited and read back, the
diagnostics' figures of each step, t-SNE switched off with its reason when
openTSNE is missing, a layout preview computed as a job and shown beside the map, and
« rebuild from here » opening the pre-flight sheet; axe on the screen. The
review screenshots, with ``--ui-screenshots DIR``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_accessibility import blocking, run_axe

import cartolex.atlas.reducers as reducers


def api_calls(ui, since: int) -> list[str]:
    return [
        u
        for u in ui.collected.requests[since:]
        if "/api/" in u and "/api/jobs" not in u and "/api/project/state" not in u
    ]


def step(ui, name: str) -> None:
    before = ui.token()
    ui.page.get_by_role("navigation", name="Steps of the build").get_by_role(
        "link", name=re.compile(name)
    ).click()
    ui.wait_ready(before)


def saved(ui) -> None:
    """The edits are saved: Save is disabled again (a toast may be an earlier save's)."""
    ui.page.wait_for_function(
        "() => document.querySelector('.cx-method-card .cx-settings__actions button').disabled"
    )


def settled(ui) -> None:
    ui.page.wait_for_function(
        "() => !document.querySelector('[aria-busy=true], .cx-card--loading')"
    )


def test_the_steps_of_the_method(demo_s, app_for, open_app, axe_source, monkeypatch):
    ui = open_app(app_for(demo_s))
    page = ui.page
    before = len(ui.collected.requests)
    ui.navigate("/method?step=keywords")
    settled(ui)
    assert len(api_calls(ui, before)) <= 5, api_calls(ui, before)
    page.get_by_role("img", name="Candidates by score and band").wait_for()
    page.get_by_role("grid", name="Candidates ranked by score").get_by_role("row").nth(2).wait_for()
    assert blocking(run_axe(ui, axe_source, ".cx-method")) == []

    # a parameter changed: marked, saved, not built yet; then back to its default
    row = page.locator("[data-param='keywords.extract.min_people']")
    row.get_by_role("spinbutton").fill("4")
    row.get_by_text("Changed", exact=True).wait_for()
    row.get_by_text("Default: 3").wait_for()
    page.get_by_role("button", name="Save", exact=True).click()
    page.get_by_text("Build options saved").first.wait_for()
    row.get_by_text(re.compile("Not built with this value yet")).wait_for()
    row.get_by_role("button", name="Back to default").click()
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_function(
        "() => document.querySelector('.cx-method-card .cx-settings__actions button').disabled"
    )
    row.get_by_text("Changed", exact=True).wait_for(state="detached")
    # the tiers: the rules of the filters are folded under « Advanced »
    stop_words = page.locator("[data-param='keywords.extract.stop_words']")
    assert not stop_words.is_visible()
    page.get_by_text(re.compile(r"^Advanced \(\d+\)$")).click()
    stop_words.get_by_role("switch").wait_for()

    # real controls: the parts by slot kind, the providers' order, the levels, round-tripped
    step(ui, "Texts")
    settled(ui)
    page.get_by_role("checkbox", name="Collected texts: abstract").uncheck()
    page.get_by_text(re.compile(r"^Advanced \(\d+\)$")).click()
    hal = page.get_by_role("listitem", name=re.compile(r"^HAL, 3 of"))
    hal.focus()
    page.keyboard.press("Alt+ArrowUp")
    page.get_by_role("listitem", name=re.compile(r"^HAL, 2 of")).wait_for()
    page.get_by_role("button", name="Save", exact=True).click()
    saved(ui)
    step(ui, "Grouping")
    settled(ui)
    levels = page.locator("[data-param='themes.group.level_sizes']")
    levels.get_by_role("checkbox", name="Set the size of each level").check()
    levels.get_by_role("spinbutton", name="Level 1").fill("3")
    levels.get_by_role("button", name="Add a level").click()
    levels.get_by_role("spinbutton", name="Level 2").fill("9")
    page.get_by_role("button", name="Save", exact=True).click()
    saved(ui)
    ui.navigate("/method?step=texts")
    settled(ui)
    assert not page.get_by_role("checkbox", name="Collected texts: abstract").is_checked()
    assert page.get_by_role("checkbox", name="Folders of documents: abstract").is_checked()
    # a fold holding a changed parameter opens by itself
    assert (
        page.locator("details.cx-method-tier[open]").filter(has_text="provider_priority").count()
        == 1
    )
    page.get_by_role("listitem", name=re.compile(r"^HAL, 2 of")).wait_for()
    ui.navigate("/method?step=grouping")
    settled(ui)
    levels = page.locator("[data-param='themes.group.level_sizes']")
    assert levels.get_by_role("spinbutton", name="Level 2").input_value() == "9"

    # the space: the rule of its dimensions, the variance and the neighbours kept
    step(ui, "Space")
    settled(ui)
    assert page.get_by_text(re.compile(r"by a rule: 20 up to 2 000 people")).count() >= 1
    page.get_by_role("img", name="Variance explained by each dimension").wait_for()
    page.get_by_role("img", name="Nearest people kept by the first dimensions").wait_for()

    # the grouping: the levels, the comb's θ, the dendrogram of the themes
    step(ui, "Grouping")
    settled(ui)
    page.get_by_role("img", name="Keywords per group at each level, by θ").wait_for()
    page.get_by_role("img", name="How the top-level themes join").wait_for()

    # the layout: t-SNE listed switched off with its reason when openTSNE is missing
    monkeypatch.setattr(reducers, "opentsne_available", lambda: False)
    step(ui, "Layout")
    settled(ui)
    assert page.get_by_role("radio", name="t-SNE").is_disabled()
    page.get_by_text(
        re.compile(r"needs the optional openTSNE package.*cartolex\[tsne\]")
    ).first.wait_for()
    # a preview computed as a job, beside the map on the same people
    page.get_by_role("group", name="Layout to preview").get_by_role("spinbutton").first.fill("10")
    page.get_by_role("button", name="Preview").click()
    page.get_by_text(re.compile(r"UMAP: \d+ ?% of the nearest people kept")).wait_for(
        timeout=120_000
    )
    page.get_by_text(re.compile(r"The map now: \d+ ?% of the nearest people kept")).wait_for()
    page.get_by_role("table", name="Nearest people kept, by method").wait_for()

    # rebuild from here: the pre-flight sheet of this step and those after it
    before = ui.token()
    page.get_by_role("button", name="Rebuild from here").click()
    ui.wait_ready(before)
    assert "force=map.layout" in page.url
    page.get_by_role("heading", name="Build", level=1).wait_for()


@pytest.mark.slow
def test_screenshots_of_the_method(demo_s, app_for, open_app, pytestconfig):
    target = pytestconfig.getoption("--ui-screenshots")
    if not target:
        pytest.skip("pass --ui-screenshots DIR to write the screenshots")
    out = Path(target) / "method"
    out.mkdir(parents=True, exist_ok=True)
    for theme, locale in (("light", "en"), ("dark", "en"), ("light", "fr")):
        ui = open_app(app_for(demo_s), theme=theme, locale=locale)
        for sid in ("texts", "keywords", "space", "grouping", "layout"):
            ui.navigate(f"/method?step={sid}")
            settled(ui)
            if sid == "layout":
                ui.page.locator(".cx-method-form .cx-button--primary").click()
                ui.page.locator(".cx-method-compare").wait_for(timeout=120_000)
                ui.page.wait_for_timeout(300)
            ui.page.wait_for_timeout(200)
            ui.page.screenshot(path=str(out / f"{sid}-{theme}-{locale}.png"), full_page=True)
        ui.page.context.close()
