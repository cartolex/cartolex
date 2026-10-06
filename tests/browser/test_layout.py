# SPDX-License-Identifier: MIT
"""Every screen can be scrolled to its end (the S demo world, at two window sizes).

A screen that fills the window under the header (the people's and keywords' lists, the
theme editor) is at least the window's height and grows when it holds more: an open
« Tune » panel, the lexicon's word cloud, banners. On each screen and tab, the page is
scrolled to its end: the screen's box holds all its content (nothing overflows it, so
nothing is cut), its bottom is in the window, and a list that fills the screen keeps a
usable height instead of being squeezed under what is above it.
"""

from __future__ import annotations

import pytest

PAGES = [
    "/",
    "/keywords?band=kept",
    "/keywords?band=kept&tune=1",
    "/keywords?band=lexicon",
    "/people",
    "/people?tab=identities",
    "/people?tab=duplicates",
    "/people?tab=organisations",
    "/people?tab=texts&tune=1",
    "/people?tab=coverage",
    "/themes",
    "/themes?tune=1",
    "/build?tab=recipe",
    "/settings?section=build",
    "/share",
    "/about",
]

#: The least height of a list that fills its screen, in pixels (--cx-fill-min's floor).
MIN_LIST = 200

AT_THE_END = """() => {
  window.scrollTo(0, document.scrollingElement.scrollHeight);
  const root = document.querySelector('.cx-outlet > *');
  const box = root.getBoundingClientRect();
  const lists = [...root.querySelectorAll('.cx-table--fill .cx-table__scroller, .cx-themes__body')];
  return {
    overflow: root.scrollHeight - root.clientHeight,
    bottom: box.bottom,
    window: window.innerHeight,
    lists: lists.map((e) => e.getBoundingClientRect().height),
  };
}"""


@pytest.mark.parametrize(
    "size", [(1280, 720), pytest.param((1920, 1080), marks=pytest.mark.slow)], ids=["720", "1080"]
)
def test_every_screen_scrolls_to_its_end(demo_s, app_for, open_app, size):
    width, height = size
    ui = open_app(app_for(demo_s), viewport={"width": width, "height": height})
    page = ui.page
    for path in PAGES:
        page.goto(ui.base + path)
        ui.wait_ready(0)
        page.wait_for_load_state("networkidle")
        found = page.evaluate(AT_THE_END)
        assert found["overflow"] <= 1, f"{path}: content overflows the screen by {found}"
        assert found["bottom"] <= found["window"] + 1, f"{path}: its end is out of reach {found}"
        assert all(h >= MIN_LIST for h in found["lists"]), f"{path}: a list is squeezed {found}"
