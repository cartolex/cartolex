# SPDX-License-Identifier: MIT
"""The keywords screen on the real app (the S demo world, English and French): the main flow.

Open the screen (its budget of API calls), see the warning of the languages,
search, select a range and exclude it, find it in the history and undo it,
then triage with an AI copilot: the bundle offered, a result (two parts)
imported, reviewed, some accepted, and the route of the accepted decisions. Axe on the screen and its
dialog; the review screenshots with ``--ui-screenshots``.
"""

from __future__ import annotations

import json

import pytest
from test_accessibility import blocking, run_axe
from test_theme_editor import api


@pytest.fixture()
def keywords(demo_s, app_for, open_app, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    server = app_for(demo_s)
    ui = open_app(server)
    ui.server = server
    return ui


def api_calls(ui, start: int) -> list[str]:
    urls = ui.collected.requests[start:]
    return [u for u in urls if "/api/" in u and "/api/jobs" not in u]


def test_bands_bulk_history_and_the_ai_copilot(keywords, axe_source, tmp_path):
    ui = keywords
    page = ui.page
    start = len(ui.collected.requests)
    ui.navigate("/keywords")
    grid = page.get_by_role("grid", name="To check")
    grid.locator("[role=row][aria-selected]").first.wait_for()
    assert len(api_calls(ui, start)) <= 5
    # the head: the counting unit and the languages; the warning of the languages
    assert "counted by person" in page.locator(".cx-corpus__summary").inner_text()
    warning = page.locator(".cx-kw-warning")
    assert "themes of their own" in warning.inner_text()
    # the kept band, a search, a range of three rows excluded
    page.get_by_role("tab", name="Kept").click()
    grid = page.get_by_role("grid", name="Kept")
    grid.locator("[role=row][aria-selected]").first.wait_for()
    rows = grid.locator("[role=row][aria-selected]")
    rows.nth(0).click()
    rows.nth(2).click(modifiers=["Shift"])
    page.get_by_text("3 keywords selected").wait_for()
    page.locator(".cx-corpus-bulk").get_by_role("button", name="Exclude").click()
    page.locator(".cx-toast", has_text="3 keywords excluded").wait_for()
    excluded = api(ui, "GET", "/api/keywords?decision=exclude")["data"]
    assert excluded["total"] == 3
    assert {i["route"] for i in excluded["items"]} == {"person"}
    # the history: the three exclusions, one undone
    page.get_by_role("button", name="History").click()
    drawer = page.locator("dialog[open]")
    history = drawer.get_by_role("grid", name="Decisions")
    history.locator("[role=row][aria-selected]").first.wait_for()
    assert history.locator("[role=row][aria-selected]").count() == 3
    history.locator("[role=row][aria-selected]").first.click()
    drawer.get_by_role("button", name="Undo 1 decision").click()
    page.locator(".cx-toast", has_text="1 decision undone").wait_for()
    assert api(ui, "GET", "/api/keywords?decision=exclude")["data"]["total"] == 2
    page.keyboard.press("Escape")
    drawer.wait_for(state="detached")
    # the AI copilot: the bundle is offered; two results (two parts) come back, merged
    page.get_by_role("button", name="Triage with AI").first.click()
    page.get_by_role("menuitem", name="With an AI copilot (runs code, no key)…").click()
    d = page.locator("dialog[open]")
    d.locator("a[download]").wait_for()
    blocking_ = blocking(run_axe(ui, axe_source, "dialog[open]"))
    assert blocking_ == [], blocking_
    rows = api(ui, "GET", "/api/keywords?band=aside&lang=en&limit=4")["data"]["items"]
    fr = next(
        r for r in api(ui, "GET", "/api/keywords?band=aside&lang=fr&limit=1")["data"]["items"]
    )
    decisions = [
        {"term": rows[0]["term"], "language": rows[0]["language"], "decision": "keep",
         "code": "C", "reason": "family: a concept", "group": "g1", "by": "group"},
        {"term": rows[1]["term"], "language": rows[1]["language"], "decision": "exclude",
         "code": "G", "reason": "family: too generic", "group": "g1", "by": "group"},
        {"term": rows[2]["term"], "language": rows[2]["language"], "decision": "exclude",
         "code": "F", "reason": "a broken piece", "group": "g2", "by": "term"},
        {"term": fr["term"], "language": "fr", "decision": "merge", "target": rows[0]["term"],
         "code": "C", "reason": "its translation", "group": "g3", "by": "term"},
    ]  # fmt: skip
    files = []
    for k, part in enumerate((decisions[:2], decisions[2:]), 1):
        doc = {"format": "cartolex-copilot-result/1", "task": "triage", "bundle": "b1",
               "made_at": f"2026-01-01T00:00:0{k}Z", "part": k, "parts": 2, "partial": False,
               "decisions": part, "rules": ["research discourse: always excluded, sure"],
               "coverage": {"all": {"candidates": 2, "decided_by_group": 2 - k + 1}},
               "caveats": {"not_read": {"all": 5}, "read_lightly": ""}, "notes": ""}  # fmt: skip
        files.append(tmp_path / f"result-part-{k}.json")
        files[-1].write_text(json.dumps(doc), encoding="utf-8")
    d.get_by_role("button", name="I have the result").click()
    d.locator("input[type=file]").set_input_files([str(f) for f in files])
    review = d.get_by_role("grid", name="Proposed decisions")
    review.locator("[role=row][aria-selected]").first.wait_for()
    assert "4 terms have an answer" in d.inner_text()
    assert f"merged into “{rows[0]['term']}”" in d.inner_text()
    assert "10 candidates were never shown" in d.inner_text()  # the caveats of both parts
    assert "research discourse: always excluded, sure" in d.inner_text()
    # leave one out: accept three
    review.locator("[role=row][aria-selected]").nth(2).click(modifiers=["Control"])
    d.get_by_role("button", name="Accept 3 decisions").click()
    page.locator(".cx-toast", has_text="3 AI decisions accepted").wait_for()
    decided = api(ui, "GET", "/api/keywords?route=ai-copilot")["data"]
    assert decided["total"] == 3
    # the route shows in the list, and the warning of the languages is gone; the copilot's
    # acceptance gate counts the candidates nobody judged
    page.get_by_role("tab", name="Set aside").click()
    page.get_by_role("combobox", name="Decided by").select_option("ai-copilot")
    page.get_by_role("grid", name="Set aside").get_by_text("AI · copilot").first.wait_for()
    assert page.locator(".cx-kw-warning:not(.cx-kw-gate)").count() == 0
    assert "not judged" in page.locator(".cx-kw-gate").inner_text()
    assert blocking(run_axe(ui, axe_source)) == []


@pytest.mark.slow
def test_screenshots_of_the_keywords(demo_s, app_for, open_app, pytestconfig):
    target = pytestconfig.getoption("--ui-screenshots")
    if not target:
        pytest.skip("pass --ui-screenshots DIR to write the screenshots")
    from pathlib import Path

    out = Path(target) / "keywords"
    out.mkdir(parents=True, exist_ok=True)
    for theme, locale in (("light", "en"), ("dark", "en"), ("light", "fr")):
        ui = open_app(app_for(demo_s), theme=theme, locale=locale)
        page = ui.page
        ui.navigate("/keywords?band=kept")
        page.locator(".cx-kw-table [role=row][aria-selected]").first.wait_for()
        page.wait_for_timeout(200)
        page.screenshot(path=str(out / f"list-{theme}-{locale}.png"))
        page.locator(".cx-corpus__actions .cx-menubutton button").click()
        page.get_by_role("menuitem").first.click()
        page.locator("dialog[open] a[download]").wait_for()
        page.screenshot(path=str(out / f"copilot-{theme}-{locale}.png"))
        page.keyboard.press("Escape")
        page.locator(".cx-corpus__actions .cx-menubutton button").click()
        page.get_by_role("menuitem").nth(1).click()
        page.locator("dialog[open] .cx-kw-api").wait_for()
        page.screenshot(path=str(out / f"api-{theme}-{locale}.png"))
        page.context.close()
