# SPDX-License-Identifier: MIT
"""The atlas's map in three dimensions and several built map versions, from a page opened
from ``file://`` (the offline site's way, synthetic bundles, no server).

A 3D version: drawn by the 3D controller with WebGL, every layer, link and label with its
z; a click on a person's projected place focuses them; the network's second ring as 3D
arcs; the trajectory off by default, then on (kept in the address, its time windows read
only then) and joined in time order; a theme's hull from its members; the keyboard (one tab
stop: arrows turn, Shift and arrows pan, + zooms, 0 fits, space turns); Turn and Front;
the view saved as SVG; axe. Two versions: the « Layout » select, `map=` in the address, the
3D one read through the source's `version`, the World view and back. A budget: 10⁵ people
and 10⁵ texts in 3D turned at the frame rate.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlsplit

import pytest
from atlas_file import write_atlas_page
from browser_harness import write_measures
from test_accessibility import AXE_OPTIONS, blocking

MEASURES = os.environ.get("CARTOLEX_UI_MEASURES")

READY = """() => document.querySelector('.cx-atlas__stage')
  && !document.querySelector('.cx-atlas__stage').hidden"""
BOX = "document.querySelector('.cx-atlas-map__box')"
SCENE = f"{BOX}.cxScene()"


def address(page) -> dict[str, list[str]]:
    """The atlas's state in the page's fragment."""
    return parse_qs(urlsplit(page.url).fragment)


@pytest.fixture()
def file_page(browser, tmp_path):
    """Open a file page of the atlas (`write_atlas_page` options); answers the page. A console
    error or an uncaught exception fails the test."""
    contexts = []
    errors: list[str] = []

    def open_page(fragment: str = "", **options):
        index = write_atlas_page(tmp_path / f"site{len(contexts)}", **options)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800}, reduced_motion="reduce"
        )
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(index.as_uri() + fragment)
        page.wait_for_function(READY)
        return page

    yield open_page
    for context in contexts:
        context.close()
    assert errors == []


def axe_file_page(page, axe_source: str) -> list[dict]:
    """axe on the atlas of a file page: without preloading the style sheets (a request a page
    opened from ``file://`` may not make)."""
    page.evaluate(axe_source + "\n;0")
    return page.evaluate(
        """async (options) => (await window.axe.run({ include: ['.cx-atlas'] }, options)).violations
          .map((v) => ({ id: v.id, impact: v.impact, help: v.help,
            nodes: v.nodes.slice(0, 5).map((n) => n.target.join(' ') + ' :: ' + n.failureSummary) }))""",
        {**AXE_OPTIONS, "preload": False},
    )


def click_person(page, pid: str) -> None:
    """Click where a person is drawn now (their projected place)."""
    at = page.evaluate(
        f"""(pid) => {{
          const sc = {SCENE};
          const layer = sc.layers.find((l) => l.id === 'people');
          const people = window.ATLAS.index().people;
          const k = Array.from(layer.ref).findIndex((i) => people[i].person_id === pid);
          const p = {BOX}.cxMap.project(layer.x[k], layer.y[k], layer.z[k]);
          const r = {BOX}.getBoundingClientRect();
          return [r.left + p[0], r.top + p[1]];
        }}""",
        pid,
    )
    page.mouse.click(at[0], at[1])


def test_a_map_in_three_dimensions(file_page, axe_source):
    page = file_page(dimensions=3, windows=True)
    box = page.locator(".cx-atlas-map__box")
    assert box.get_attribute("data-dimensions") == "3"
    assert box.get_attribute("data-renderer") == "webgl3d"
    scene = page.evaluate(
        f"() => {{ const s = {SCENE}; return {{dims: s.dimensions,"
        " z: s.layers.every((l) => l.z && l.z.length === l.x.length),"
        " labels: s.labels.every((l) => l.z !== undefined)}; }"
    )
    assert scene == {"dims": 3, "z": True, "labels": True}

    # a click on a person's projected place focuses them; the second ring as arcs in space
    page.locator(".cx-atlas-layers [data-key='net-2']").click()
    page.wait_for_timeout(100)
    click_person(page, "p6")
    page.wait_for_function("() => window.location.hash.includes('sel=person%3Ap6')")
    page.wait_for_function(
        f"() => {SCENE}.lines.some((l) => l.id.startsWith('ring-') && l.z && l.z.length === l.x.length)"
    )
    # no trajectory until asked: no windows read, no path drawn
    assert not page.evaluate(f"() => {SCENE}.lines.some((l) => l.id === 'path')")
    assert not any(r[0] == "windows" for r in page.evaluate("() => window.FAKE_READS"))
    page.locator(".cx-atlas-layers [data-key='traj']").click()
    page.wait_for_function(f"() => {SCENE}.lines.some((l) => l.id === 'path' && l.z)")
    assert address(page)["traj"] == ["1"]
    windows = page.evaluate(f"() => {SCENE}.layers.find((l) => l.id === 'windows').x.length")
    assert windows == 4

    # an organisation: its members spanned by a hull of their projection, not a polygon
    page.evaluate("() => window.ATLAS.select({kind: 'organisation', id: 'o2'})")
    page.wait_for_function(
        f"() => {SCENE}.regions.some((r) => r.id === 'hull' && r.members && !r.polygon)"
    )
    on_screen = page.evaluate(
        f"() => {{ const r = {SCENE}.regions.find((x) => x.id === 'hull');"
        f" const p = {BOX}.cxMap.regionOnScreen(r.members); return p ? p.length : 0; }}"
    )
    assert on_screen >= 6

    # the keyboard: one tab stop; arrows turn, Shift and arrows pan, + zooms, 0 fits, space turns
    box.focus()
    view = lambda: page.evaluate(f"() => {BOX}.cxMap.view()")  # noqa: E731
    before = view()
    page.keyboard.press("ArrowRight")
    assert view()["yaw"] != before["yaw"]
    turned = view()
    page.keyboard.press("Shift+ArrowLeft")
    after = view()
    assert after["yaw"] == turned["yaw"] and (after["tx"], after["tz"]) != (
        turned["tx"],
        turned["tz"],
    )
    page.keyboard.press("+")
    assert view()["zoom"] > 1.2
    page.keyboard.press("0")
    assert abs(view()["zoom"] - 1) < 1e-6
    page.keyboard.press(" ")
    turn = page.locator(".cx-atlas-map__tools [data-role='turn']")
    assert turn.get_attribute("aria-pressed") == "true"
    page.wait_for_timeout(150)
    assert view()["yaw"] != after["yaw"]
    page.locator(".cx-atlas-map__tools [data-role='front']").click()
    assert turn.get_attribute("aria-pressed") == "false"
    assert view()["yaw"] == 0 and view()["pitch"] == 0

    # Save view: the current projection as SVG
    page.locator(".cx-atlas-map__tools [aria-label='Save this view']").click()
    with page.expect_download() as download:
        page.get_by_role("menuitem", name="As an SVG image", exact=True).click()
    svg = open(download.value.path(), encoding="utf-8").read()
    assert svg.startswith("<svg") and "<path" in svg

    assert blocking(axe_file_page(page, axe_source)) == []


def test_two_built_versions_and_the_layout_select(file_page):
    page = file_page(versions=True, windows=True)
    box = page.locator(".cx-atlas-map__box")
    assert box.get_attribute("data-dimensions") == "2"
    layout = page.get_by_label("Layout", exact=True)
    assert layout.locator("option").all_inner_texts() == [
        "v1 · 2D · umap · flat",
        "v3d · 3D · umap",
    ]
    page.evaluate("() => window.ATLAS.select({kind: 'person', id: 'p3'})")
    layout.select_option("v3d")
    page.wait_for_function(f"() => {BOX}.dataset.dimensions === '3'")
    assert address(page)["map"] == ["v3d"]
    assert ["bundle", "v3d"] in page.evaluate("() => window.FAKE_READS")
    # the focus outlives the change of layout; the trajectory is read for the version shown
    assert address(page)["sel"] == ["person:p3"]
    page.locator(".cx-atlas-layers [data-key='traj']").click()
    page.wait_for_function(f"() => {SCENE}.lines.some((l) => l.id === 'path' && l.z)")
    assert ["windows", "p3", "v3d"] in page.evaluate("() => window.FAKE_READS")
    # the flat version again: `map` leaves the address
    layout.select_option("v1")
    page.wait_for_function(f"() => {BOX}.dataset.dimensions === '2'")
    assert "map" not in address(page)
    # the trajectory toggle works on the flat map too
    page.wait_for_function(f"() => {SCENE}.lines.some((l) => l.id === 'path' && !l.z)")


def test_the_layout_in_the_address_and_the_world_view(browser, tmp_path):
    """`map=` given in the address opens that version; the World view (2D) from a 3D version."""
    index = write_atlas_page(tmp_path / "site", versions=True)
    data = index.parent / "data.js"
    # give the organisations an address, so the World view is offered
    data.write_text(
        data.read_text(encoding="utf-8")
        + "window.FAKE_BUNDLE.organisations.forEach((o, i) => { o.location = {lon: 2 + i, lat: 45 + i}; });\n",
        encoding="utf-8",
    )
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    try:
        page.goto(index.as_uri() + "#map=v3d")
        page.wait_for_function(READY)
        page.wait_for_function(f"() => {BOX}.dataset.dimensions === '3'")
        page.get_by_role("button", name="World", exact=True).click()
        page.wait_for_function(f"() => {BOX}.dataset.dimensions === '2'")
        assert address(page)["view"] == ["world"]
        page.get_by_role("button", name="World", exact=True).click()
        page.wait_for_function(f"() => {BOX}.dataset.dimensions === '3'")
    finally:
        context.close()


def test_a_large_map_in_three_dimensions_turns_at_the_frame_rate(browser, tmp_path):
    """10⁵ people and 10⁵ texts in 3D, turned for 120 frames: the 2D map's budget."""
    index = write_atlas_page(
        tmp_path / "large",
        generate={"people": 100_000, "texts": 100_000, "keywords": 2000, "orgs": 200},
    )
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    try:
        page.goto(index.as_uri() + "#show=people,keywords,organisations,texts")
        page.wait_for_function(READY, timeout=60_000)
        page.wait_for_function(
            f"() => {SCENE}.layers.some((l) => l.id === 'texts' && l.x.length === 100000)",
            timeout=60_000,
        )
        page.wait_for_timeout(300)
        measure = page.evaluate(
            f"""() => new Promise((resolve) => {{
              const api = {BOX}.cxMap;
              const before = api.stats().frames;
              let n = 0;
              const start = performance.now();
              const step = () => {{
                api.turnBy(0.01, 0);
                n += 1;
                if (n < 120) {{ requestAnimationFrame(step); return; }}
                requestAnimationFrame(() => {{
                  const s = api.stats();
                  const draws = s.drawMs.slice(-120).sort((a, b) => a - b);
                  resolve({{ renderer: api.renderer, frames: s.frames - before,
                    ms: performance.now() - start, draw_mean: draws.reduce((a, b) => a + b, 0) / draws.length,
                    draw_p95: draws[Math.floor(draws.length * 0.95)] }});
                }});
              }};
              requestAnimationFrame(step);
            }})"""
        )
    finally:
        context.close()
    fps = measure["frames"] / (measure["ms"] / 1000)
    measure["fps"] = round(fps, 1)
    write_measures(MEASURES, "map3d_turn_100k", measure)
    assert measure["renderer"] == "webgl3d", measure
    if os.environ.get("CARTOLEX_STRICT_BUDGETS") or os.environ.get("CI"):
        assert fps >= 50 and measure["draw_p95"] < 16, measure
    else:  # a shared machine under load slows every frame: report, do not fail
        assert measure["draw_p95"] < 16, measure
