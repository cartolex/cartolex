# SPDX-License-Identifier: MIT
"""The atlas (`/map`) on the real app, on the S demo world at depth 2; one pan budget.

The main flow: the map drawn with WebGL, find a person and see them in the
panel and on the map (the hover card), show organisations and texts, filter
the people by a column of theirs and narrow the period, clear every filter
(the period too), the state kept in the address across a reload, the world
view; the API calls of the navigation and axe. The map's frame in both themes, with the « Tune »
panel open and closed and after a resize: its canvas inside its border, nothing over it but its
legend, no block of a foreign colour on it. The budget: a map of 10⁴ points panned in the
component gallery.
"""

from __future__ import annotations

import io
import os
from urllib.parse import parse_qs, urlsplit

import pytest
from browser_harness import write_measures
from test_accessibility import blocking, run_axe

MEASURES = os.environ.get("CARTOLEX_UI_MEASURES")


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
    ui.page.locator(".cx-atlas .cx-map-frame__box").wait_for()


def test_the_main_flow_of_the_atlas(demo_s, app_for, open_app, axe_source):
    ui = open_app(app_for(demo_s), bypass_csp=True)
    page = ui.page
    before = len(ui.collected.requests)
    open_map(ui)
    assert len(api_calls(ui, before)) <= 5, api_calls(ui, before)
    box = page.locator(".cx-atlas .cx-map-frame__box")
    assert box.get_attribute("data-renderer") == "webgl"
    assert blocking(run_axe(ui, axe_source, ".cx-atlas")) == []

    # find a person by name: the panel shows them, the map centres on them, the address keeps it
    atlas = page.evaluate("() => fetch('/api/atlas').then((r) => r.json())")
    person = next(p for p in atlas["people"] if p["person_id"] and p["x"] is not None)
    find = page.get_by_role("combobox", name="Find on the map")
    find.fill(person["name"])
    page.get_by_role("option").first.wait_for()
    find.press("Enter")
    panel = page.locator(".cx-atlas-panel")
    panel.get_by_role("heading", name=person["name"]).wait_for()
    assert query(ui)["sel"] == [f"person:{person['person_id']}"]
    # the hover card of the point at the centre, where the map put the person
    bounds = box.bounding_box()
    page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
    page.locator(".cx-map-frame__card", has_text=person["name"]).wait_for()

    # organisations and texts on the map, each kind with its symbol and count
    page.get_by_role("checkbox", name="Organisations").check()
    page.get_by_role("checkbox", name="Texts").check()
    page.wait_for_function(
        "() => /Texts\\s*\\d/.test(document.querySelector('.cx-atlas-kinds').textContent)"
    )
    assert query(ui)["show"] == ["people,keywords,organisations,texts"]

    # filter the people by a column of theirs, narrow the period, then clear every filter
    column = atlas["columns"][0]
    value = column["values"][0]
    page.get_by_role("button", name=column["column"], exact=True).click()
    page.get_by_role("menuitemcheckbox").first.click()
    page.wait_for_function(
        "(n) => document.querySelector('.cx-atlas-counts, .cx-atlas-kinds').textContent.includes(n)",
        arg=str(value["count"]),
    )
    assert query(ui)["f"] == [f"{column['column']}:{value['value']}"]
    first = page.get_by_role("slider", name="First year")
    first.focus()
    first.press("ArrowRight")
    first.press("ArrowRight")
    assert int(query(ui)["from"][0]) == atlas["years"]["min"] + 2
    page.get_by_role("button", name="Clear the filters").click()
    assert "f" not in query(ui) and "from" not in query(ui) and "to" not in query(ui)

    # the state survives a reload of the address
    page.reload()
    ui.wait_ready(0)
    page.locator(".cx-atlas .cx-map-frame__box").wait_for()
    assert page.get_by_role("checkbox", name="Organisations").is_checked()
    panel.get_by_role("heading", name=person["name"]).wait_for()

    # the map versions: the pinned one, the ways to try another, the base maps
    page.get_by_role("button", name="Map versions").click()
    dialog = page.locator("dialog[open]")
    dialog.get_by_text("pinned", exact=True).wait_for()
    dialog.get_by_role("button", name="Draw it").wait_for()
    dialog.get_by_role("button", name="Add its map").wait_for()
    assert blocking(run_axe(ui, axe_source, "dialog[open]")) == []
    dialog.get_by_role("button", name="Close", exact=True).last.click()

    # the world view: organisations at their address
    page.get_by_role("button", name="World").click()
    assert query(ui)["view"] == ["world"]
    page.get_by_role("group", name="World map of the organisations").wait_for()


#: The frame, its box, its canvas and its legend, and what is on top at points of the canvas.
FRAME_GEOMETRY = """() => {
  const rect = (e) => { const b = e.getBoundingClientRect();
    return {left: b.left, top: b.top, right: b.right, bottom: b.bottom}; };
  const frame = document.querySelector('.cx-atlas__frame');
  const box = frame.querySelector('.cx-map-frame__box');
  const canvas = box.querySelector('canvas');
  const legend = frame.querySelector('.cx-map-frame__legend');
  const c = rect(canvas), l = rect(legend);
  const over = [];
  for (let i = 1; i < 10; i += 1) for (let j = 1; j < 10; j += 1) {
    const x = c.left + (c.right - c.left) * i / 10, y = c.top + (c.bottom - c.top) * j / 10;
    if (x >= l.left && x <= l.right && y >= l.top && y <= l.bottom) continue;
    if (y < 0 || y > innerHeight) continue;
    const top = document.elementFromPoint(x, y);
    if (top && top !== canvas && !legend.contains(top)) over.push(top.className || top.tagName);
  }
  return {frame: rect(frame), box: rect(box), canvas: c, legend: l, over,
    border: parseFloat(getComputedStyle(frame).borderTopWidth)};
}"""


def white_blocks(png: bytes, area: dict, legend: dict) -> int:
    """How many 8×8 blocks of *area* (outside the *legend*) are pure white."""
    Image = pytest.importorskip("PIL.Image")
    img = Image.open(io.BytesIO(png)).convert("RGB")
    found = 0
    for y in range(int(area["top"]) + 2, int(area["bottom"]) - 10, 8):
        for x in range(int(area["left"]) + 2, int(area["right"]) - 10, 8):
            if (
                legend["left"] - 8 <= x <= legend["right"]
                and legend["top"] - 8 <= y <= legend["bottom"]
            ):
                continue
            block = img.crop((x, y, x + 8, y + 8)).getextrema()
            found += all(lo >= 250 for lo, _ in block)
    return found


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_map_stays_inside_its_frame(demo_s, app_for, open_app, theme):
    ui = open_app(app_for(demo_s), theme=theme)
    page = ui.page
    for path, size in (
        ("/map", None),
        ("/map?tune=1", None),
        ("/map?tune=1", {"width": 1200, "height": 700}),
        ("/map", {"width": 1600, "height": 1000}),
    ):
        if size:
            page.set_viewport_size(size)
        ui.open(path)  # a reload: the panel open or closed as the address says
        page.locator(".cx-atlas .cx-map-frame__box").wait_for()
        page.wait_for_timeout(400)  # the resize observed, the frame drawn again
        page.locator(".cx-atlas__frame").scroll_into_view_if_needed()
        g = page.evaluate(FRAME_GEOMETRY)
        inner = {
            k: g["frame"][k] + (g["border"] if k in ("left", "top") else -g["border"])
            for k in g["frame"]
        }
        where = f"{path} {size}"
        for side in ("left", "top"):
            assert g["canvas"][side] >= inner[side] - 0.5, (where, g)
        for side in ("right", "bottom"):
            assert g["canvas"][side] <= inner[side] + 0.5, (where, g)  # no point below the border
        assert g["over"] == [], (where, g["over"])  # nothing over the canvas but the legend
        if theme == "dark":
            shot = page.screenshot()
            assert white_blocks(shot, g["canvas"], g["legend"]) == 0, where


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
