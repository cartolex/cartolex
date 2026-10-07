# SPDX-License-Identifier: MIT
"""A walk through an offline site opened from `file://` (shared by the Chromium test and the
Firefox run, which needs a Playwright of its own: `python site_walk.py <index.html>`).

The run exits 0 when the walk passes, 77 when no Firefox build matches this Playwright,
and 1 with the reason otherwise."""

from __future__ import annotations

import sys
from pathlib import Path


def open_page(browser, url: str):
    """A page on *url* that refuses every request but files: (page, errors, refused, context)."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    refused: list[str] = []
    errors: list[str] = []

    def route(r):
        if r.request.url.startswith("file://"):
            r.continue_()
        else:
            refused.append(r.request.url)
            r.abort()

    context.route("**/*", route)
    page = context.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(url)
    return page, errors, refused, context


def walk(page) -> None:
    """Home → search → a person's page → the atlas, mounted over the site's files → an old
    themes address → not found."""
    page.locator("#cx-site:not([hidden])").wait_for(timeout=5000)
    assert page.locator("#cx-missing").count() == 0
    # the lexicon's word cloud, drawn at the build in the page's look, opens the atlas
    page.wait_for_function(
        "() => { const img = [...document.querySelectorAll('.cx-cloud__img')]"
        ".find((e) => e.offsetParent); return img && img.complete && img.naturalWidth > 0; }"
    )
    assert page.locator(".cx-cloud__link").get_attribute("href") == "#/map"
    search = page.get_by_role("searchbox", name="Find a person")
    search.fill("Person 1")
    person = page.locator(".cx-result[href^='#/person/']").first
    person.wait_for()
    search.press("ArrowDown")  # the results take the keyboard
    person.click()
    page.locator(".cx-share").first.wait_for()
    # the texts, when the site carries them, come from the person's own part
    if page.get_by_role("heading", name="Texts").count():
        page.wait_for_function(
            "() => document.querySelector('.cx-texts li') || /None\\./.test("
            "document.querySelector('.cx-card--wide:last-child').textContent)"
        )
    # the person in the atlas: the app's atlas, its data from the site's files
    page.get_by_role("link", name="Show on the atlas").click()
    page.wait_for_function(
        "() => { const r = document.querySelector('.cx-atlas-host');"
        " return r && r.children.length > 0; }"
    )
    assert page.locator(".cx-main--atlas .cx-note--warning").count() == 0
    assert "sel=person" in page.evaluate("location.hash")
    # the network's rings, found in the browser from the site's links
    rings = page.evaluate(
        """async () => {
          const S = window.CxSite;
          const src = S.atlasSource();
          await S.load('links');
          const ptr = S.data.links.people.ptr;
          const busy = S.ix.core.people.id.find((id, i) => ptr[i + 1] > ptr[i]);
          const a = await src.coauthors({ kind: 'person', id: busy, circle: 2 });
          return { count: a.count, lines: a.lines.length, second: Boolean(a.second) };
        }"""
    )
    assert rings["count"] > 0 and rings["lines"] > 0 and rings["second"], rings
    page.evaluate("location.hash = '#/themes'")
    page.wait_for_function("() => location.hash.startsWith('#/map')")
    page.evaluate("location.hash = '#/nowhere/at/all'")
    page.get_by_role("heading", name="Not found").wait_for()


def main(index: str) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if not Path(p.firefox.executable_path).exists():
            return 77
        browser = p.firefox.launch(headless=True)
        try:
            page, errors, refused, _ = open_page(browser, Path(index).as_uri())
            walk(page)
            if errors or refused:
                print(errors, refused)
                return 1
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
