# SPDX-License-Identifier: MIT
"""Links between the map and the other screens, co-authors and who uses a keyword, full
screen; on the S demo world at depth 2.

A person given in the address is shown and centred, with their co-authors and the works
together, joined to them by lines on the map (an API call budget kept); a keyword's panel names the people who use it and leads to
the keywords screen, which keeps that keyword whatever its band; the themes screen opens a
keyword from its address; the map's body goes full screen and comes back; the view is saved
as SVG (with its legend) and as PNG.
"""

from __future__ import annotations

from urllib.parse import quote

from test_atlas import api_calls, open_map


def test_the_map_links_to_the_other_screens_and_back(demo_s, app_for, open_app):
    ui = open_app(app_for(demo_s))
    page = ui.page
    atlas = page.evaluate("() => fetch('/api/atlas').then((r) => r.json())")
    person = atlas["people"][0]

    # a person from the address: shown, their co-authors with the works together
    co = page.evaluate(
        "(id) => fetch(`/api/atlas/coauthors?kind=person&id=${id}`).then((r) => r.json())",
        person["person_id"],
    )
    assert co["count"] > 0
    before = len(ui.collected.requests)
    open_map(ui, f"/map?sel=person:{person['person_id']}")
    panel = page.locator(".cx-atlas-panel")
    panel.get_by_role("heading", name=person["name"]).wait_for()
    page.locator(".cx-atlas-coauthors__list li").first.wait_for()
    assert page.locator(".cx-atlas-coauthors__list li").count() == min(co["count"], 30)
    assert page.locator(".cx-atlas-near").count() == 0  # no similarity list in the panel
    assert len(api_calls(ui, before)) <= 5, api_calls(ui, before)
    assert panel.get_by_role("link", name="Open in People").get_attribute("href") == (
        f"/people?person={person['person_id']}"
    )

    # a keyword: the people who use it, then the keywords screen on it
    term = max(atlas["keywords"], key=lambda k: k["weight"] or 0)["term"]
    ui.navigate(f"/map?sel=keyword:{quote(term)}")
    panel.get_by_text("Used by").first.wait_for()
    panel.get_by_role("link", name="Open in Keywords").click()
    page.locator(".cx-kw-term-filter").wait_for()
    page.wait_for_function(
        "(t) => [...document.querySelectorAll('.cx-kw-term')].some((e) => e.textContent === t)",
        arg=term,
    )
    # its row opens the people who use it
    page.locator(".cx-kw-term", has_text=term).first.dblclick()
    page.locator("dialog[open] .cx-kw-people li").first.wait_for()
    page.keyboard.press("Escape")

    # the themes screen opens the keyword from its address
    ui.navigate(f"/themes?keyword={quote(term)}")
    page.locator(".cx-themes-panel__title", has_text=term).wait_for()
    page.locator(".cx-themes-panel__people a").first.wait_for()

    # full screen and back (the fallback when the browser refuses its own)
    open_map(ui)
    body = page.locator(".cx-atlas__body")
    page.get_by_role("button", name="Full screen").click()
    page.wait_for_function("() => document.querySelector('.cx-atlas__body.is-fullscreen')")
    page.get_by_role("button", name="Leave full screen (Esc)").click()
    page.wait_for_function("() => !document.querySelector('.cx-atlas__body.is-fullscreen')")
    assert "is-fullscreen" not in (body.get_attribute("class") or "")

    # the view saved as it is on screen: SVG with the legend, PNG
    menu = page.get_by_role("button", name="Save the view")
    with page.expect_download() as svg:
        menu.click()
        page.get_by_role("menuitem", name="As an SVG image, with the legend").click()
    text = open(svg.value.path(), encoding="utf-8").read()
    assert text.startswith("<svg") and text.count("<path") > 3 and 'class="legend"' in text
    top = next(n for n in atlas["nodes"] if n["parent"] is None)
    assert (top["names"].get("en") or next(iter(top["names"].values()))) in text
    with page.expect_download() as png:
        menu.click()
        page.get_by_role("menuitem", name="As a PNG image", exact=True).click()
    assert open(png.value.path(), "rb").read(8) == b"\x89PNG\r\n\x1a\n"
