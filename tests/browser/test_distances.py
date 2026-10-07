# SPDX-License-Identifier: MIT
"""Distances (``static/distances/``), one code in the offline site and the app's Map screen.

From ``file://`` with every request refused, on a pseudonymous site of the S world: a
person's ranked list, the most alike first, its first score the cosine of the int8 vectors
the site carries, its co-authors marked with their texts together (the site's links); a
row's « Its list » and « Compare » (the atlas comparing both); the two lists of pairs; a
matrix of organisations ordered by theme, whose cell opens Compare from the keyboard; the
ranked list saved as CSV with the pseudonyms. In the app: the Map screen's Distances pane
over ``GET /api/atlas/vectors`` and ``GET /api/atlas/links``, within the call budget, axe.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import re
from pathlib import Path

import numpy as np
import pytest
from site_walk import open_page
from test_accessibility import blocking, run_axe
from test_atlas import api_calls

from cartolex.project.project import Project
from cartolex.site.builder import SiteOptions, build_site

DONE = """() => { const s = document.querySelector('.cx-dist__status');
  return Boolean(s && s.textContent && !s.getAttribute('aria-busy')
    && !/Measuring/.test(s.textContent)); }"""


@pytest.fixture(scope="module")
def site(demo_s, tmp_path_factory) -> Path:
    """A pseudonymous site of the S world (built on a copy)."""
    from app_harness import copy_project

    project = Project.open(copy_project(demo_s, tmp_path_factory.mktemp("dist") / "p"))
    try:
        record = build_site(project, SiteOptions(names=False))
        return project.layout.outputs / "sites" / record["id"]
    finally:
        project.close()


def _data(site: Path, name: str):
    text = (site / "data" / f"{name}.js").read_text(encoding="utf-8")
    return json.loads(text[text.index("=", text.index("]")) + 1 :].strip().rstrip(";"))


def _vector(site: Path, core: dict, sid: str) -> np.ndarray:
    """A person's vector as the site carries it: part (k − 1) mod n, row (k − 1) // n."""
    n, k = core["shards"]["vectors"], int(sid[1:]) - 1
    part = _data(site, f"vectors/{k % n}")
    rows = np.frombuffer(base64.b64decode(part["v"]), np.int8).reshape(-1, part["dim"])
    v = rows[k // n].astype(float)
    return v / np.linalg.norm(v)


def test_the_sites_distances_from_a_file(site, browser):
    core = _data(site, "core")
    links = _data(site, "links")["people"]
    ptr = links["ptr"]
    # a person with co-authors on the map
    i = next(i for i in range(len(core["people"]["id"])) if ptr[i + 1] > ptr[i])
    me = core["people"]["id"][i]
    page, errors, refused, context = open_page(
        browser, (site / "index.html").as_uri() + f"#/distances?of=person:{me}"
    )
    try:
        page.wait_for_function(DONE)
        rows = page.locator(".cx-dist-table tbody tr")
        assert (
            rows.count() == min(50, len(core["people"]["id"]) - 1)
            and "people ranked" in page.locator(".cx-dist__status").inner_text()
        )
        scores = [
            float(x) for x in page.locator(".cx-dist-table tbody .cx-dist-score").all_inner_texts()
        ]
        assert scores == sorted(scores, reverse=True)
        # the first score is the cosine of the vectors the site carries
        first = rows.first.locator("th").inner_text()
        other = "s" + re.search(r"\d+", first).group(0)
        cosine = float(_vector(site, core, me) @ _vector(site, core, other))
        assert abs(cosine - scores[0]) < 0.006
        # its co-authors say their texts together, as the links do
        partner = core["people"]["id"][links["nbr"][ptr[i]]]
        n = links["cnt"][ptr[i]]
        together = {}
        for _ in range(20):
            page.wait_for_function(DONE)
            for row in page.locator(".cx-dist-table tbody tr").all():
                name = row.locator("th").inner_text()
                together[name] = row.locator("td").nth(2).inner_text()
            if page.locator(".cx-dist-pager [data-key=next]:not([disabled])").count() == 0:
                break
            page.locator(".cx-dist-pager [data-key=next]").click()
        label = f"Person {partner[1:]}"
        assert together[label] == (f"{n} texts together" if n > 1 else "1 text together")
        # saved as CSV, with the pseudonyms
        with page.expect_download() as got:
            page.get_by_role("button", name="↓ Download CSV").click()
        text = Path(got.value.path()).read_text(encoding="utf-8-sig")
        table = list(csv.reader(io.StringIO(text)))
        assert table[0][:2] == ["Rank", "Name"] and len(table) == len(core["people"]["id"])
        assert all(r[1].startswith("Person ") for r in table[1:])
        # « Compare » opens the atlas with both
        page.locator(".cx-dist-table tbody tr").first.get_by_role("link", name="Compare").click()
        page.wait_for_function(
            "() => /#\\/map\\?sel=person/.test(location.hash) && /with=person/.test(location.hash)"
        )
        page.locator(".cx-atlas-card__title").wait_for()

        # the pairs: alike but never together, then together but unlike
        page.goto((site / "index.html").as_uri() + "#/distances?d=pairs")
        page.wait_for_function(DONE)
        assert re.search(
            r"\d+ pairs among \d+ people", page.locator(".cx-dist__status").inner_text()
        )
        assert (
            page.locator(".cx-dist-table tbody tr").first.locator("td").nth(2).inner_text() == "No"
        )
        page.get_by_role("button", name="Work together, talk differently").click()
        page.wait_for_function(DONE)
        assert (
            "together"
            in page.locator(".cx-dist-table tbody tr").first.locator("td").nth(2).inner_text()
        )

        # a matrix of organisations, its cell opened from the keyboard
        page.goto((site / "index.html").as_uri() + "#/distances?d=matrix&mx=orgs")
        page.wait_for_function(DONE)
        page.locator(".cx-dist-heat__canvas").wait_for()
        box = page.locator(".cx-dist-heat__box")
        box.focus()
        box.press("ArrowRight")
        assert "×" in page.locator(".cx-dist-heat__cursor").inner_text()
        box.press("Enter")
        page.wait_for_function("() => /with=organisation/.test(location.hash)")
        assert errors == [] and refused == []
    finally:
        context.close()


def test_the_map_screens_distances_pane(demo_s, app_for, open_app, axe_source):
    ui = open_app(app_for(demo_s))
    page = ui.page
    atlas = page.evaluate("() => fetch('/api/atlas').then((r) => r.json())")
    me = atlas["people"][0]["person_id"]
    before = len(ui.collected.requests)
    ui.navigate(f"/map?pane=distances&of=person:{me}")
    page.wait_for_function(DONE)
    calls = api_calls(ui, before)
    assert len(calls) <= 5, calls
    assert any("/api/atlas/vectors?kind=person" in c for c in calls)
    assert (
        page.get_by_role("button", name="Distances", exact=True).get_attribute("aria-pressed")
        == "true"
    )
    assert page.locator(".cx-dist-table tbody tr").count() > 0
    assert blocking(run_axe(ui, axe_source, ".cx-dist")) == []
    # a name opens its sheet in People; Compare, the atlas pane comparing both
    row = page.locator(".cx-dist-table tbody tr").first
    assert row.locator("th a").get_attribute("href").startswith("/people?person=")
    row.get_by_role("link", name="Compare").click()
    page.locator(".cx-atlas-compare__head, .cx-atlas-card__title").first.wait_for()
    assert "with=person" in page.url and "pane=" not in page.url
    # the vectors and the links the pane read: the bundle's order
    vectors = page.evaluate("() => fetch('/api/atlas/vectors?kind=person').then((r) => r.json())")
    assert vectors["count"] == len(atlas["people"]) and vectors["dim"] > 0
    links = page.evaluate("() => fetch('/api/atlas/links?kind=organisation').then((r) => r.json())")
    assert len(links["ptr"]) == len(atlas["organisations"]) + 1
