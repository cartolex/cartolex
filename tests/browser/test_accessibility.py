# SPDX-License-Identifier: MIT
"""axe-core on the gallery and the shell, in both themes: no serious or critical violation.

axe is injected through the browser's debugging protocol into a context that
does not enforce the page's Content-Security-Policy (axe builds its messages
with `new Function`); every other browser test runs with the policy on.
"""

from __future__ import annotations

import pytest
from browser_harness import prefs_script

AXE_OPTIONS = {
    "resultTypes": ["violations"],
    "runOnly": {
        "type": "tag",
        "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"],
    },
}
BLOCKING = {"serious", "critical"}


def run_axe(ui, axe_source: str, include: str | None = None) -> list[dict]:
    """The violations axe finds in the page (or inside *include*)."""
    page = ui.page
    if not page.evaluate("() => typeof window.axe === 'object'"):
        page.evaluate(axe_source + "\n;0")
    return page.evaluate(
        """async ([include, options]) => {
          const context = include ? { include: [include] } : document;
          const result = await window.axe.run(context, options);
          return result.violations.map((v) => ({
            id: v.id, impact: v.impact, help: v.help,
            nodes: v.nodes.slice(0, 5).map((n) => n.target.join(' ') + ' :: ' + n.failureSummary),
          }));
        }""",
        [include, AXE_OPTIONS],
    )


def blocking(violations: list[dict]) -> list[str]:
    return [
        f"{v['id']} ({v['impact']}): {v['help']}\n    " + "\n    ".join(v["nodes"])
        for v in violations
        if v["impact"] in BLOCKING
    ]


AXE_CONTEXT = {"bypass_csp": True}


@pytest.mark.parametrize("ui", [AXE_CONTEXT], indirect=True)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_gallery_has_no_serious_violation(ui, axe_source, theme):
    ui.page.add_init_script(prefs_script(theme=theme))
    ui.open("/gallery")
    assert ui.page.evaluate("() => document.documentElement.dataset.theme") == theme
    problems = blocking(run_axe(ui, axe_source))
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("ui", [AXE_CONTEXT], indirect=True)
@pytest.mark.parametrize("locale", ["fr", "pt-BR"])
def test_gallery_in_french_and_portuguese_has_no_serious_violation(ui, axe_source, locale):
    ui.page.add_init_script(prefs_script(locale=locale))
    ui.open("/gallery")
    problems = blocking(run_axe(ui, axe_source))
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("ui", [AXE_CONTEXT], indirect=True)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_shell_pages_have_no_serious_violation(ui, axe_source, theme):
    ui.page.add_init_script(prefs_script(theme=theme))
    for path in ("/overview", "/keywords", "/about", "/no/such/page"):
        ui.open(path)
        problems = blocking(run_axe(ui, axe_source))
        assert problems == [], f"{path}:\n" + "\n".join(problems)


@pytest.mark.parametrize("ui", [AXE_CONTEXT], indirect=True)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_open_dialogs_drawers_and_menus_have_no_serious_violation(ui, axe_source, theme):
    ui.page.add_init_script(prefs_script(theme=theme))
    ui.open("/gallery")
    page = ui.page
    page.locator(".cx-header .cx-activity-indicator").click()
    page.locator("dialog[open]").wait_for()
    problems = blocking(run_axe(ui, axe_source, "dialog[open]"))
    assert problems == [], "activity drawer:\n" + "\n".join(problems)
    page.keyboard.press("Escape")
    page.locator(".cx-header .cx-menubutton button").click()
    page.get_by_role("menu", name="Settings and display").wait_for()
    problems = blocking(run_axe(ui, axe_source, ".cx-header"))
    assert problems == [], "display menu:\n" + "\n".join(problems)
    page.keyboard.press("Escape")
    page.locator(".cx-project-menu__button").click()
    page.locator(".cx-project-menu [role=menu]").wait_for()
    problems = blocking(run_axe(ui, axe_source, ".cx-header"))
    assert problems == [], "project menu:\n" + "\n".join(problems)
    page.keyboard.press("Escape")
    grid = page.get_by_role("grid", name="Keyword candidates", exact=True)
    grid.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Shift+F10")
    page.get_by_role("menu", name="Actions on the selected rows").wait_for()
    problems = blocking(run_axe(ui, axe_source, '[data-gallery="table"]'))
    assert problems == [], "table menu:\n" + "\n".join(problems)
