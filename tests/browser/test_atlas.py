# SPDX-License-Identifier: MIT
"""The atlas (`/map`) on the real app (the S demo world at depth 2), the same atlas from a
page opened from ``file://`` (as the offline site mounts it), and one pan budget.

The main flow: the map drawn with WebGL in at most five API calls; the treemap explored
(a click focuses a theme, a double click opens it down to its keywords, Escape goes back);
a keyword's people; a person found, their co-authors listed and drawn as arcs, the network's
second ring; the layers panel; an organisation compared with another; the filters; the
colour scheme and the layout kept in the person's preferences; the map versions; the world
view; axe. The map's frame in both themes. From ``file://``: the classic script mounts the
atlas, the treemap and a person's rings work without a server.
"""

from __future__ import annotations

import io
import os
from urllib.parse import parse_qs, urlsplit

import pytest
from atlas_file import write_atlas_page
from browser_harness import write_measures
from test_accessibility import blocking, run_axe

MEASURES = os.environ.get("CARTOLEX_UI_MEASURES")

READY = """() => document.querySelector('.cx-atlas__stage')
  && !document.querySelector('.cx-atlas__stage').hidden"""
#: How many points each layer of the drawn scene lights.
LIT = """() => Object.fromEntries(document.querySelector('.cx-atlas-map__box').cxScene()
  .layers.map((l) => [l.id, l.highlightCount || 0]))"""
#: Whether the drawn scene has the network's arcs.
ARCS = """() => document.querySelector('.cx-atlas-map__box').cxScene()
  .lines.some((l) => l.id.startsWith('ring-'))"""


def query(ui) -> dict[str, list[str]]:
    return parse_qs(urlsplit(ui.page.url).query)


def api_calls(ui, since: int) -> list[str]:
    """The API requests since request number *since* (the jobs poller's left out)."""
    return [
        u
        for u in ui.collected.requests[since:]
        if "/api/" in u and "/api/jobs" not in u and "/api/project/state" not in u
    ]


def open_map(ui, path: str = "/map") -> None:
    ui.navigate(path)
    ui.page.locator(".cx-atlas .cx-atlas-map__box").wait_for()
    ui.page.wait_for_function(READY)


def card_heading(page):
    return page.locator(".cx-atlas-card .cx-atlas-card__title")


def card_section(page, text: str):
    return page.locator(".cx-atlas-card h4", has_text=text)


#: The atlas's and the map's heights in the window.
HEIGHTS = """() => ['.cx-atlas', '.cx-atlas-map__box'].map((s) =>
  Math.round(document.querySelector(s).getBoundingClientRect().height))"""


def assert_the_card_keeps_the_height(page) -> list[int]:
    """The atlas's and the map's heights stay as they are while the card shows a focus, hides
    to its rail and comes back: the host gives the atlas its height, never the card's content.
    Answers those heights."""
    seen = [page.evaluate(HEIGHTS)]
    head = page.locator(".cx-atlas-pane--card .cx-atlas-pane__head")
    head.get_by_role("button", name="Hide").click()
    rail = page.locator(".cx-atlas-rail--right")
    rail.wait_for()
    seen.append(page.evaluate(HEIGHTS))
    rail.click()
    head.wait_for()
    seen.append(page.evaluate(HEIGHTS))
    assert all(s == seen[0] for s in seen), seen
    return seen[0]


def test_the_main_flow_of_the_atlas(demo_s, app_for, open_app, axe_source):
    ui = open_app(app_for(demo_s), bypass_csp=True)
    page = ui.page
    before = len(ui.collected.requests)
    open_map(ui)
    assert len(api_calls(ui, before)) <= 5, api_calls(ui, before)
    box = page.locator(".cx-atlas-map__box")
    assert box.get_attribute("data-renderer") == "webgl"
    assert blocking(run_axe(ui, axe_source, ".cx-atlas")) == []
    atlas = page.evaluate("() => fetch('/api/atlas').then((r) => r.json())")

    # the treemap: a click focuses a theme, a double click opens it, then down to its keywords
    blocks = page.locator(".cx-atlas-tree__block")
    blocks.first.click(position={"x": 20, "y": 8})
    assert query(ui)["sel"][0].startswith("theme:")
    blocks.first.dblclick(position={"x": 20, "y": 8})
    page.wait_for_function("() => /[?&]open=/.test(location.search)")
    blocks.first.dblclick(position={"x": 20, "y": 8})
    page.locator(".cx-atlas-pane__title", has_text="Keywords of").wait_for()
    blocks.first.click(position={"x": 10, "y": 8})
    assert query(ui)["sel"][0].startswith("keyword:")
    card_section(page, "Used by").wait_for()
    page.wait_for_function(f"() => ({LIT})().people > 0")  # its people lit on the map
    box.focus()
    page.keyboard.press("Escape")
    # Escape goes back to the theme: the address follows on the next frame
    page.wait_for_function("() => /[?&]sel=theme(:|%3A)/.test(location.search)")

    # a person found by name: their co-authors listed and drawn, then a second ring
    linked = None
    for p in atlas["people"][:20]:
        r = page.evaluate(
            "(id) => fetch(`/api/atlas/coauthors?kind=person&id=${id}`).then((r) => r.json())",
            p["person_id"],
        )
        if r.get("lines"):
            linked = p
            break
    assert linked, "the S world has co-authors"
    find = page.get_by_role("combobox", name="Find")
    find.fill(linked["name"])
    page.get_by_role("option").first.wait_for()
    find.press("Enter")
    card_heading(page).filter(has_text=linked["name"]).wait_for()
    assert query(ui)["sel"] == [f"person:{linked['person_id']}"]
    card_section(page, "Co-authors").wait_for()
    page.wait_for_function(ARCS)
    assert_the_card_keeps_the_height(page)
    page.locator(".cx-atlas-layers").get_by_role("button", name="2", exact=True).click()
    assert query(ui)["net"] == ["2"]
    card_section(page, "Second circle").wait_for()
    # the treemap shows the person's own theme weights
    page.locator(".cx-atlas-pane__title", has_text=f"Themes of {linked['name']}").wait_for()

    # the layers panel: keywords hidden and back, people's names written
    layers = page.locator(".cx-atlas-layers")
    layers.get_by_role("button", name="Keywords", exact=True).click()
    assert "keywords" not in query(ui)["show"][0]
    layers.get_by_role("button", name="Keywords", exact=True).click()
    layers.get_by_role("button", name="Names: People").click()
    assert query(ui)["names"] == ["people"]

    # an organisation from the person's card, compared with another: the people in both
    page.locator(".cx-atlas-card__head .cx-atlas-link").first.click()
    assert query(ui)["sel"][0].startswith("organisation:")
    card_section(page, "Members").wait_for()
    page.get_by_role("button", name="Compare with…").click()
    other = page.get_by_role("combobox", name="The other side of the comparison")
    other.fill("-")  # every acronym of the S world has one
    options = page.locator(".cx-atlas-compare-pick").get_by_role("option")
    options.nth(1).wait_for()
    other.press("ArrowDown")  # the first may be the organisation itself
    other.press("Enter")
    card_section(page, "People in both").wait_for()
    assert "with" in query(ui)
    page.get_by_role("button", name="Stop comparing").click()

    # the filters, folded under one button
    page.locator(".cx-atlas__tools").get_by_role("button", name="Filters").click()
    column = atlas["columns"][0]
    page.locator(".cx-atlas__filters input[type=checkbox]").first.check()
    assert query(ui)["f"][0].startswith(f"{column['column']}:")
    page.get_by_role("button", name="Clear the filters").click()
    assert "f" not in query(ui)

    # the colour scheme (the whole app's) and the layout belong to the person: kept by the app
    page.get_by_role("combobox", name="Colour scheme").select_option("viridis")
    page.wait_for_function(
        "() => document.documentElement.style.getPropertyValue('--cx-hue-1') !== ''"
    )
    page.locator(".cx-atlas-pane--tree").get_by_role("button", name="Hide").click()
    page.wait_for_function(
        """() => fetch('/api/me/preferences').then((r) => r.json())
          .then((d) => d.preferences.other['atlas.tree_on'] === false)"""
    )
    page.reload()
    ui.wait_ready(0)
    page.wait_for_function(READY)
    assert page.locator(".cx-atlas-pane--tree").is_hidden()
    assert page.get_by_role("combobox", name="Colour scheme").input_value() == "viridis"
    page.locator(".cx-atlas-rail").first.click()
    page.get_by_role("combobox", name="Colour scheme").select_option("vivid")

    # the map versions
    page.get_by_role("button", name="Map versions").click()
    dialog = page.locator("dialog[open]")
    dialog.get_by_text("pinned", exact=True).wait_for()
    assert blocking(run_axe(ui, axe_source, "dialog[open]")) == []
    dialog.get_by_role("button", name="Close", exact=True).last.click()

    # the world view: organisations at their address
    page.get_by_role("button", name="World", exact=True).click()
    assert query(ui)["view"] == ["world"]
    page.get_by_role("group", name="World map of the organisations").wait_for()


def white_blocks(png: bytes, area: dict) -> int:
    """How many 8×8 blocks of *area* are pure white."""
    Image = pytest.importorskip("PIL.Image")
    img = Image.open(io.BytesIO(png)).convert("RGB")
    found = 0
    for y in range(int(area["top"]) + 2, int(area["bottom"]) - 10, 8):
        for x in range(int(area["left"]) + 2, int(area["right"]) - 10, 8):
            block = img.crop((x, y, x + 8, y + 8)).getextrema()
            found += all(lo >= 250 for lo, _ in block)
    return found


#: The map's box and its canvas, as seen in the window.
GEOMETRY = """() => { const r = (e) => { const b = e.getBoundingClientRect();
  return {left: b.left, top: Math.max(0, b.top), right: b.right, bottom: Math.min(innerHeight, b.bottom)}; };
  return {map: r(document.querySelector('.cx-atlas-map')),
    canvas: r(document.querySelector('.cx-atlas-map__canvas'))}; }"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_map_stays_inside_its_frame(demo_s, app_for, open_app, theme):
    ui = open_app(app_for(demo_s), theme=theme)
    page = ui.page
    for path, size in (
        ("/map", None),
        ("/map?tune=1", {"width": 1200, "height": 700}),
        ("/map", {"width": 1600, "height": 1000}),
    ):
        if size:
            page.set_viewport_size(size)
        ui.open(path)
        page.wait_for_function(READY)
        page.wait_for_timeout(400)  # the resize observed, the map drawn again
        page.locator(".cx-atlas-map").scroll_into_view_if_needed()
        g = page.evaluate(GEOMETRY)
        for side in ("left", "top"):
            assert g["canvas"][side] >= g["map"][side] - 0.5, (path, g)
        for side in ("right", "bottom"):
            assert g["canvas"][side] <= g["map"][side] + 0.5, (path, g)
        if theme == "dark":
            assert white_blocks(page.screenshot(), g["canvas"]) == 0, path


def test_the_atlas_runs_from_a_file_page(browser, tmp_path):
    """The offline site's way: the classic script, no server, the same atlas."""
    index = write_atlas_page(tmp_path / "site")
    context = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    try:
        page.goto(index.as_uri())
        page.wait_for_function(READY)
        page.locator(".cx-atlas-tree__block").first.click(position={"x": 20, "y": 8})
        assert "sel=theme" in page.url
        page.evaluate("() => window.ATLAS.select({kind: 'person', id: 'p6'})")
        card_heading(page).filter(has_text="Person 6").wait_for()
        card_section(page, "Co-authors").wait_for()
        page.wait_for_function(ARCS)
        # no host links: nothing to open on another screen, nothing broken either
        assert page.locator(".cx-atlas-card a").count() == 0
        assert errors == []
    finally:
        context.close()


def test_a_map_of_ten_thousand_points_pans_at_the_frame_rate(ui):
    ui.open("/gallery")
    box = ui.page.locator(".cx-gallery__map .cx-map-frame__box")
    box.scroll_into_view_if_needed()
    assert ui.page.locator(".cx-gallery__map").get_attribute("data-points") == "10000"
    measure = ui.page.evaluate(
        """() => new Promise((resolve) => {
          const api = document.querySelector('.cx-gallery__map .cx-map-frame__box').cxMap;
          const before = api.stats().frames;
          let n = 0;
          const start = performance.now();
          const step = () => {
            api.panBy(3, 1);
            n += 1;
            if (n < 120) { requestAnimationFrame(step); return; }
            requestAnimationFrame(() => {
              const s = api.stats();
              const draws = s.drawMs.slice(-120).sort((a, b) => a - b);
              resolve({ renderer: api.renderer, frames: s.frames - before,
                ms: performance.now() - start, draw_mean: draws.reduce((a, b) => a + b, 0) / draws.length,
                draw_p95: draws[Math.floor(draws.length * 0.95)] });
            });
          };
          requestAnimationFrame(step);
        })"""
    )
    fps = measure["frames"] / (measure["ms"] / 1000)
    measure["fps"] = round(fps, 1)
    write_measures(MEASURES, "map_pan_10k", measure)
    assert measure["renderer"] == "webgl", measure
    if os.environ.get("CARTOLEX_STRICT_BUDGETS") or os.environ.get("CI"):
        assert fps >= 50 and measure["draw_p95"] < 16, measure
    else:  # a shared machine under load slows every frame: report, do not fail
        assert measure["draw_p95"] < 16, measure
