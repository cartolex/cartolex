# SPDX-License-Identifier: MIT
"""The method screen (`/method`) on the real app, the S demo world at depth 2.

One scenario over the steps: the API calls of a step, a parameter changed
(its mark, saved, not built yet) and put back, the diagnostics' figures of
each step, a layout preview computed as a job and shown beside the map, and
« rebuild from here » opening the pre-flight sheet; axe on the screen. The
review screenshots, with ``--ui-screenshots DIR``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_accessibility import blocking, run_axe


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


def settled(ui) -> None:
    ui.page.wait_for_function(
        "() => !document.querySelector('[aria-busy=true], .cx-card--loading')"
    )


def test_the_steps_of_the_method(demo_s, app_for, open_app, axe_source):
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
    row = page.locator("tr[data-param='keywords.extract.min_people']")
    row.get_by_role("spinbutton").fill("4")
    row.get_by_text("Changed: the default is 3").wait_for()
    page.get_by_role("button", name="Save").click()
    page.get_by_text("Build options saved").first.wait_for()
    row.get_by_text(re.compile("Not built with this value yet")).wait_for()
    row.get_by_role("button", name="Back to default").click()
    page.get_by_role("button", name="Save").click()
    page.wait_for_function(
        "() => document.querySelector('.cx-method-card .cx-settings__actions button').disabled"
    )
    row.get_by_text("Changed: the default is 3").wait_for(state="detached")

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

    # the layout: a preview computed as a job, beside the map on the same people
    step(ui, "Layout")
    settled(ui)
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
