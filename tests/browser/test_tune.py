# SPDX-License-Identifier: MIT
"""The « Tune » panels of the steps' pages and the Build page's Recipe, on the real app (the S
demo world at depth 2).

One scenario per panel: a closed panel reads nothing; opening it reads the parameters and the
step's diagnostics; an essential value changed (its mark), saved (the header counts it, the
page and the later pages say they are out of date, with the stage to rebuild from), then put
back to its default. Then the Recipe: a changed value, filtered, exported as Markdown, its row
linking to its panel; and ``/method`` sending to the Recipe or to a step's page. The review
screenshots, with ``--ui-screenshots DIR``.
"""

from __future__ import annotations

import json
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


def settled(ui) -> None:
    ui.page.wait_for_function(
        "() => !document.querySelector('[aria-busy=true], .cx-card--loading')"
    )


def open_panel(ui, title: str):
    """Open the panel named *title*: nothing was read for it before, its parameters after."""
    page = ui.page
    assert not [u for u in api_calls(ui, 0) if "/api/params" in u or "/api/method/" in u]
    toggle = page.get_by_role("button", name=re.compile(title))
    assert toggle.get_attribute("aria-expanded") == "false"
    before = len(ui.collected.requests)
    toggle.click()
    panel = page.locator(".cx-tune")
    panel.locator("[data-param]").first.wait_for()
    settled(ui)
    assert any("/api/params" in u for u in api_calls(ui, before))
    return panel, toggle


def save(panel) -> None:
    """Save the panel's parameters and wait until Save is disabled again (the save ended)."""
    button = panel.locator(".cx-tune__body > .cx-settings__actions").get_by_role(
        "button", name="Save", exact=True
    )
    button.click()
    button.page.wait_for_function(
        "() => document.querySelector('.cx-tune__body > .cx-settings__actions button').disabled"
    )


def stale_note(page, stage: str):
    return page.locator(".cx-tune__stale").filter(has_text=stage)


def test_the_keywords_panel(demo_s, app_for, open_app, axe_source):
    ui = open_app(app_for(demo_s))
    page = ui.page
    ui.collected.requests.clear()
    ui.navigate("/keywords")
    settled(ui)
    panel, toggle = open_panel(ui, "Tune the keywords")
    assert "defaults" in toggle.inner_text()
    page.get_by_role("img", name="Candidates by score and band").wait_for()
    assert blocking(run_axe(ui, axe_source, ".cx-tune")) == []

    row = panel.locator("[data-param='keywords.extract.min_people']")
    row.get_by_text("Fewest people").wait_for()  # its short label, beside its code name
    row.get_by_role("spinbutton").fill("4")
    row.get_by_text("Changed", exact=True).wait_for()
    save(panel)
    page.wait_for_function(
        "() => /1 changed/.test(document.querySelector('.cx-tune__toggle').innerText)"
    )
    stale_note(page, "find keyword candidates").wait_for()
    # the later steps are out of date too: the themes say so, with the stage to rebuild from
    ui.navigate("/themes")
    stale_note(page, "find keyword candidates").get_by_role(
        "button", name=re.compile("Rebuild from")
    ).wait_for()
    # back to the default: the stage is up to date again
    ui.navigate("/keywords?tune=1")
    panel = page.locator(".cx-tune")
    row = panel.locator("[data-param='keywords.extract.min_people']")
    row.get_by_role("button", name="Back to default").click()
    save(panel)
    row.get_by_text("Changed", exact=True).wait_for(state="detached")
    page.locator(".cx-tune__stale").wait_for(state="detached")
    assert "defaults" in page.locator(".cx-tune__toggle").inner_text()
    # the rules of the filters are folded under « Advanced »
    stop_words = panel.locator("[data-param='keywords.extract.stop_words']")
    assert not stop_words.is_visible()
    panel.get_by_text(re.compile(r"^Advanced \(\d+\)$")).click()
    stop_words.get_by_role("switch").wait_for()


def test_the_keywords_thresholds_show_their_counts_as_they_move(demo_s, app_for, open_app):
    ui = open_app(app_for(demo_s))
    page = ui.page
    ui.navigate("/keywords?tune=1")
    settled(ui)
    box = page.locator(".cx-thresholds")
    box.get_by_text("These are the last build's thresholds.").wait_for()
    field = page.locator(".cx-tune [data-param='keywords.extract.min_people']").get_by_role(
        "spinbutton"
    )

    def counts(before: str) -> str:
        """The box's words once they changed from *before* and nothing is being asked."""
        page.wait_for_function(
            "(before) => { const box = document.querySelector('.cx-thresholds');"
            " return Boolean(box) && box.getAttribute('aria-busy') === 'false'"
            " && box.innerText !== before; }",
            arg=before,
        )
        return box.inner_text()

    unchanged = box.inner_text()
    field.fill("5")
    five = counts(unchanged)
    assert re.search(r"Candidates\s+[\d,]+ → [\d,]+ \(−[\d,]+\)", five), five
    assert re.search(r"\d+ candidates? would leave", five)
    leaving = box.locator(".cx-thresholds__list").first.locator("li")
    assert leaving.count() > 0 and "Fewest people" in leaving.first.inner_text()
    field.fill("8")
    eight = counts(five)
    assert "would leave" in eight  # more leave, other words
    # a looser window needs a new extraction: said so, not previewed
    field.fill("2")
    box.get_by_text(re.compile("only a new extraction shows them")).wait_for()
    # nothing saved
    params = page.evaluate("() => fetch('/api/params').then((r) => r.json())")
    stage = next(s for s in params["stages"] if s["id"] == "keywords.extract")
    assert next(p for p in stage["params"] if p["name"] == "min_people")["value"] == 3


def test_the_texts_panel_on_the_people_page(demo_s, app_for, open_app):
    ui = open_app(app_for(demo_s))
    page = ui.page
    ui.collected.requests.clear()
    ui.navigate("/people?tab=texts")
    settled(ui)
    panel, toggle = open_panel(ui, "Tune the texts")
    assert "1 changed" in toggle.inner_text()  # the pinned year
    panel.get_by_role("group", name="The whole build").wait_for()  # the seed and the year
    panel.get_by_role("checkbox", name="Collected texts: abstract").uncheck()
    save(panel)
    stale_note(page, "gather the texts").wait_for()
    assert "2 changed" in toggle.inner_text()
    row = panel.locator("[data-param='corpus.assemble.parts']")
    row.get_by_role("button", name="Back to default").click()
    save(panel)
    assert panel.get_by_role("checkbox", name="Collected texts: abstract").is_checked()
    page.locator(".cx-tune__stale").wait_for(state="detached")


def test_the_themes_panel(demo_s, app_for, open_app):
    ui = open_app(app_for(demo_s))
    page = ui.page
    ui.collected.requests.clear()
    ui.navigate("/themes")
    settled(ui)
    panel, _ = open_panel(ui, "Tune the space and the grouping")
    # the space's unit is essential: shown without opening a fold
    assert panel.locator("[data-param='themes.space.space_unit']").is_visible()
    page.get_by_role("img", name="Variance explained by each dimension").wait_for()
    page.get_by_role("img", name="How the top-level themes join").wait_for()
    comb = panel.get_by_role("switch", name="The comb")
    comb.focus()
    page.keyboard.press("Space")
    save(panel)
    stale_note(page, "group keywords into topics and themes").wait_for()
    panel.locator("[data-param='themes.group.comb']").get_by_role(
        "button", name="Back to default"
    ).click()
    save(panel)
    page.locator(".cx-tune__stale").wait_for(state="detached")


def test_the_map_panel(demo_s, app_for, open_app, monkeypatch):
    monkeypatch.setattr(reducers, "opentsne_available", lambda: False)
    ui = open_app(app_for(demo_s))
    page = ui.page
    ui.collected.requests.clear()
    ui.navigate("/map")
    settled(ui)
    panel, _ = open_panel(ui, "Tune the map")
    # the map's layout: t-SNE listed switched off with its reason when openTSNE is missing
    assert panel.get_by_role("radio", name="t-SNE").is_disabled()
    row = panel.locator("[data-param='map.trajectories.window_years']")
    row.get_by_role("spinbutton").fill("4")
    save(panel)
    stale_note(page, "change over time").wait_for()
    row.get_by_role("button", name="Back to default").click()
    save(panel)
    page.locator(".cx-tune__stale").wait_for(state="detached")
    # rebuild from here: the pre-flight sheet of this step and those after it
    before = ui.token()
    panel.get_by_role("button", name="Rebuild from here").click()
    ui.wait_ready(before)
    assert "force=map.layout" in page.url


def test_the_recipe_and_the_old_method_address(demo_s, app_for, open_app, tmp_path):
    server = app_for(demo_s)
    params = tmp_path / "project-0" / "decisions" / "params.json"
    doc = json.loads(params.read_text(encoding="utf-8"))
    doc.setdefault("stages", {}).setdefault("keywords.extract", {})["min_people"] = 4
    params.write_text(json.dumps(doc), encoding="utf-8")
    ui = open_app(server)
    page = ui.page

    # /method?step=… opens the step's page with its panel open; /method, the Recipe
    ui.navigate("/method?step=keywords")
    assert re.search(r"/keywords\?tune=1$", page.url)
    page.locator(".cx-tune [data-param='keywords.extract.min_people']").wait_for()
    ui.collected.requests.clear()
    ui.navigate("/method")
    assert page.url.endswith("/build?tab=recipe")
    settled(ui)
    assert [u.split("/api/", 1)[1] for u in api_calls(ui, 0)] == ["recipe"], api_calls(ui, 0)
    page.get_by_role("tab", name="Recipe", selected=True).wait_for()

    page.get_by_role("checkbox", name="Changed only").check()
    row = page.locator("[data-recipe='keywords.extract.min_people']")
    row.get_by_text("Changed").wait_for()
    # the demo's own settings (pinned year, depth, keywords per group) and this one; nothing at its default
    assert page.locator("tr[data-recipe]").count() == 4
    page.locator("[data-recipe='themes.group.keywords_per_group']").wait_for()
    assert page.locator("[data-recipe='keywords.extract.max_share']").count() == 0
    with page.expect_download() as got:
        page.get_by_text("Download as Markdown").click()
    text = Path(got.value.path()).read_text(encoding="utf-8")
    assert "| Fewest people * | `min_people` | 4 | 3 |" in text
    # each row links to the page where it is tuned
    before = ui.token()
    row.get_by_role("link", name="Tune « Fewest people » on Keywords").click()
    ui.wait_ready(before)
    page.locator(".cx-tune [data-param='keywords.extract.min_people']").wait_for()
    # the header's settings menu no longer lists the method
    page.get_by_role("button", name="Settings and display").click()
    page.get_by_role("menuitem", name="Settings", exact=True).wait_for()
    assert page.get_by_role("menuitem", name="Method").count() == 0


def preview_bar(page):
    return page.locator(".cx-atlas-preview")


def wait_preview(page, params: dict) -> None:
    """Wait until the map shows the preview of *params*, computed (no progress any more)."""
    page.wait_for_function(
        "(want) => { const bar = document.querySelector('.cx-atlas-preview');"
        " const p = bar && bar.querySelector('[data-preview-params]');"
        " return Boolean(p) && p.dataset.previewParams === want && !bar.querySelector('.cx-progress'); }",
        arg=json.dumps(params, separators=(",", ":")),
        timeout=120_000,
    )


def test_the_map_previews_a_layout_change_in_place(demo_s, app_for, open_app, tmp_path):
    ui = open_app(app_for(demo_s))
    page = ui.page
    ui.navigate("/map?tune=1")
    settled(ui)
    frame = page.locator(".cx-atlas__frame")
    assert "is-preview" not in frame.get_attribute("class")
    field = page.locator(".cx-tune [data-param='map.n_neighbors']").get_by_role("spinbutton")
    # a change draws a preview on the map; a next change while it is computed supersedes it
    field.fill("10")
    preview_bar(page).get_by_text("Computing the preview").wait_for()
    page.wait_for_timeout(1500)  # its job started
    field.fill("11")
    wait_preview(page, {"n_neighbors": 11})
    jobs = page.evaluate("() => fetch('/api/jobs').then((r) => r.json())")["jobs"]
    states = sorted(j["state"] for j in jobs if j["kind"] == "preview")
    assert states in (["cancelled", "succeeded"], ["succeeded", "succeeded"]), states
    assert "is-preview" in frame.get_attribute("class")
    bar = preview_bar(page)
    assert re.search(
        r"UMAP: \d+ ?% of the nearest people kept \(the map now: \d+ ?%\)", bar.inner_text()
    )
    assert frame.get_by_role("group", name=re.compile("Preview of the layout")).count() == 1
    bar.get_by_role("button", name="Before").click()
    frame.get_by_role("group", name=re.compile("The map now, the same")).wait_for()
    # discard: the map again, the field back at the pinned version's value
    bar.get_by_role("button", name="Discard").click()
    bar.wait_for(state="detached")
    assert "is-preview" not in frame.get_attribute("class")
    assert field.input_value() == "25"
    # keep: a new map version, pinned, then the build of the map
    field.fill("11")
    wait_preview(page, {"n_neighbors": 11})  # computed once: cached
    before = ui.token()
    preview_bar(page).get_by_role("button", name="Keep").click()
    ui.wait_ready(before)
    assert "/build?scope=map" in page.url
    versions = page.evaluate("() => fetch('/api/map/versions').then((r) => r.json())")
    pinned = next(v for v in versions["versions"] if v["id"] == versions["pinned"])
    assert pinned["layout"]["params"] == {"n_neighbors": 11}


@pytest.mark.slow
def test_screenshots_of_the_tune_panels(demo_s, app_for, open_app, pytestconfig):
    target = pytestconfig.getoption("--ui-screenshots")
    if not target:
        pytest.skip("pass --ui-screenshots DIR to write the screenshots")
    out = Path(target) / "tune"
    out.mkdir(parents=True, exist_ok=True)
    pages = {
        "texts": "/people?tab=texts&tune=1",
        "keywords": "/keywords?tune=1",
        "themes": "/themes?tune=1",
        "map": "/map?tune=1",
        "recipe": "/build?tab=recipe",
        "closed": "/keywords",
    }
    for theme, locale in (("light", "en"), ("dark", "en"), ("light", "fr")):
        ui = open_app(app_for(demo_s), theme=theme, locale=locale)
        for name, path in pages.items():
            ui.navigate(path)
            settled(ui)
            ui.page.wait_for_timeout(300)
            ui.page.screenshot(path=str(out / f"{name}-{theme}-{locale}.png"), full_page=True)
        ui.page.context.close()
