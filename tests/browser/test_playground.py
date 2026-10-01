# SPDX-License-Identifier: MIT
"""The theme editor's playground on the real app (the S demo world at depth 2): the levels
and the keywords per topic changed, the columns and the balance following; the preview
adopted as the editor's draft, then undone; two quick changes give the latest."""

from __future__ import annotations

import pytest
from test_theme_editor import api, open_editor


@pytest.fixture()
def playground(demo_s, app_for, open_app):
    server = app_for(demo_s)
    ui = open_app(server)
    open_editor(ui)
    ui.page.get_by_role("tab", name="Playground").click()
    ui.page.locator(".cx-pg-block").first.wait_for(timeout=30000)
    return ui


def control(ui, label: str):
    return ui.page.locator(".cx-pg-control").filter(
        has=ui.page.locator(".cx-pg-control__label", has_text=label)
    )


def wait_ready(ui) -> None:
    """Wait until the preview of the controls' values is shown."""
    ui.page.wait_for_function(
        "() => /Regrouped/.test(document.querySelector('.cx-pg-controls__state').textContent)",
        timeout=30000,
    )


def columns(ui) -> list[int]:
    """The nodes of each column (from their titles)."""
    return [
        int(t.split()[-1].replace(",", ""))
        for t in ui.page.locator(".cx-pg-col__title").all_inner_texts()
    ]


def per_topic(ui, n: int) -> None:
    field = control(ui, "Keywords per topic").locator("input")
    field.fill(str(n))


def test_change_the_levels_and_the_keywords_per_topic_adopt_then_undo(playground):
    ui = playground
    page = ui.page
    wait_ready(ui)
    assert len(columns(ui)) == 2
    assert page.locator(".cx-pg-balance tbody tr").count() == 2

    control(ui, "Levels").locator(".cx-segmented__option", has_text="3").click()
    page.wait_for_function("() => document.querySelectorAll('.cx-pg-col__title').length === 3")
    wait_ready(ui)
    assert page.locator(".cx-pg-balance tbody tr").count() == 3
    # each child block lies within its parent's vertical span, in the next column
    outside = page.evaluate(
        """() => [...document.querySelectorAll('.cx-pg-block[data-parent]')]
          .filter((b) => b.dataset.parent).map((b) => {
            const p = document.querySelector(`.cx-pg-block[data-node="${b.dataset.parent}"]`);
            const r = b.getBoundingClientRect();
            const q = p.getBoundingClientRect();
            return r.top >= q.top - 0.5 && r.bottom <= q.bottom + 0.5 && r.left > q.right ? null
              : b.dataset.node;
          }).filter(Boolean)"""
    )
    assert outside == []
    topics = columns(ui)[-1]
    per_topic(ui, 10)
    page.wait_for_function(
        "(n) => Number([...document.querySelectorAll('.cx-pg-col__title span')].pop().textContent) > n",
        arg=topics,
        timeout=30000,
    )
    wait_ready(ui)

    # a node's top keywords in a popover, and back with Escape
    page.locator(".cx-pg-block__button").first.click()
    pop = page.get_by_role("dialog", name=page.locator(".cx-pg-block__name").first.inner_text())
    assert pop.locator("li").count() > 0
    page.keyboard.press("Escape")
    pop.wait_for(state="detached")

    page.get_by_role("button", name="Adopt as my draft").click()
    page.wait_for_function(
        "() => document.querySelector('.cx-themes-centre [role=tab][aria-selected=true]')"
        ".textContent.includes('Treemap')",
        timeout=60000,
    )
    levels = page.locator(".cx-themes__levels")
    assert levels.inner_text().count("›") == 2  # three levels in the editor's draft
    assert "unsaved" in page.locator(".cx-themes__status").inner_text()
    group = next(
        s for s in api(ui, "GET", "/api/params")["data"]["stages"] if s["id"] == "themes.group"
    )
    values = {p["name"]: p["value"] for p in group["params"]}
    assert values["depth"] == 3 and values["keywords_per_group"] == 10
    # no question about the new grouping: it is the draft
    assert page.locator(".cx-themes-banner", has_text="new grouping").count() == 0

    page.locator(".cx-themes__undo").click()
    page.wait_for_function(
        "() => (document.querySelector('.cx-themes__levels').textContent.match(/›/g) || []).length === 1"
    )


def test_two_quick_changes_give_the_latest(playground, demo_s):
    from cartolex.app.playground import group_preview
    from cartolex.build.stages import STAGES
    from cartolex.project import Project

    ui = playground
    page = ui.page
    wait_ready(ui)
    per_topic(ui, 10)
    page.wait_for_timeout(450)  # the first change is asked for (a job)
    per_topic(ui, 12)
    # what a preview of the latest settings shows (computed here, nothing written)
    settings = {"themes.group": {"keywords_per_group": 12}}
    tree = group_preview(Project.open(demo_s), STAGES, settings, year=2026)["tree"]
    leaves = sum(1 for n in tree["nodes"] if n.get("parent") is not None)
    assert leaves != sum(  # the two settings give different trees
        1
        for n in group_preview(
            Project.open(demo_s), STAGES, {"themes.group": {"keywords_per_group": 10}}, year=2026
        )["tree"]["nodes"]
        if n.get("parent") is not None
    )
    page.wait_for_function(
        "(n) => Number([...document.querySelectorAll('.cx-pg-col__title span')].pop().textContent) === n"
        " && /Regrouped/.test(document.querySelector('.cx-pg-controls__state').textContent)",
        arg=leaves,
        timeout=30000,
    )
