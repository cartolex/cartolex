# SPDX-License-Identifier: MIT
"""Several built map layouts, end to end on the real app (the S demo world at depth 2), and
on an offline site opened from ``file://``.

The app: « Map versions » tries a layout in space built beside the pinned flat one (a build
of the map, as a job); the atlas's « Layout » shows it in 3D (`map=` in the address), with
the texts of the focus, the network's second ring and the trajectory in space; back to the
flat map; « Share » offers both layouts to the site, and builds it. The site: built with
both, its atlas switches to the layout in space, read from its own files.
"""

from __future__ import annotations

import shutil
from urllib.parse import parse_qs, urlsplit

import pytest
from site_walk import open_page

READY = """() => document.querySelector('.cx-atlas__stage')
  && !document.querySelector('.cx-atlas__stage').hidden"""
BOX = "document.querySelector('.cx-atlas-map__box')"
SCENE = f"{BOX}.cxScene()"
#: A layer of the drawn scene with z (a map in space) and points: `id` given.
LAYER_3D = f"""(id) => {{ const l = {SCENE}.layers.find((x) => x.id === id);
  return Boolean(l && l.z && l.x.length > 0 && Array.from(l.z).some((v) => v !== 0)); }}"""


def query(page) -> dict[str, list[str]]:
    return parse_qs(urlsplit(page.url).query)


@pytest.mark.slow
def test_a_layout_in_space_built_beside_the_flat_map(demo_s, app_for, open_app):
    server = app_for(demo_s)
    ui = open_app(server)
    page = ui.page
    ui.navigate("/map")
    page.wait_for_function(READY)
    assert page.locator(".cx-atlas-layout-group").is_hidden()

    # « Map versions »: a layout in space, built beside the pinned (flat) one
    page.get_by_role("button", name="Map versions").click()
    dialog = page.get_by_role("dialog", name="Map versions and base maps")
    pinned = dialog.locator("[data-version='v1']")
    assert pinned.get_by_role("switch", name="Build it too").is_checked()
    assert pinned.get_by_role("switch", name="Build it too").is_disabled()
    assert "nearest kept" in pinned.inner_text()
    dialog.get_by_label("In space (3D)").check()
    assert dialog.get_by_label("Build it beside the pinned one").is_checked()
    dialog.get_by_role("button", name="Draw it").click()
    dialog.locator("[data-version='v2']").wait_for()
    page.keyboard.press("Escape")
    # the build ends: the atlas is read again and offers both layouts
    layout = page.get_by_label("Layout", exact=True)
    layout.wait_for(timeout=300_000)
    assert layout.locator("option").all_inner_texts()[1].startswith("v2 · 3D")

    # a person with co-authors, then the layout in space: the focus is kept
    atlas = page.evaluate("() => fetch('/api/atlas').then((r) => r.json())")
    linked = next(
        p
        for p in atlas["people"][:40]
        if page.evaluate(
            "(id) => fetch(`/api/atlas/coauthors?kind=person&id=${id}`).then((r) => r.json())",
            p["person_id"],
        ).get("lines")
    )
    find = page.get_by_role("combobox", name="Find")
    find.fill(linked["name"])
    page.get_by_role("option").first.wait_for()
    find.press("Enter")
    layout.select_option("v2")
    page.wait_for_function(f"() => {BOX}.dataset.dimensions === '3'")
    assert query(page)["map"] == ["v2"]
    assert query(page)["sel"] == [f"person:{linked['person_id']}"]
    page.wait_for_function(LAYER_3D, arg="people")

    # the texts of the focus, the network's second ring and the trajectory, in space
    layers = page.locator(".cx-atlas-layers")
    layers.locator("[data-key='more']").click()
    layers.locator("[data-key='eye-texts']").click()
    layers.locator("[data-key='tx-focus']").click()
    page.wait_for_function(LAYER_3D, arg="texts")
    layers.locator("[data-key='net-2']").click()
    page.wait_for_function(
        f"() => {SCENE}.lines.some((l) => l.id.startsWith('ring-') && l.z && l.z.length > 0)"
    )
    assert query(page)["net"] == ["2"]
    assert not page.evaluate(f"() => {SCENE}.lines.some((l) => l.id === 'path')")
    layers.locator("[data-key='traj']").click()
    page.wait_for_function(f"() => {SCENE}.lines.some((l) => l.id === 'path' && l.z)")
    assert query(page)["traj"] == ["1"]

    # back to the flat map
    layout.select_option("v1")
    page.wait_for_function(f"() => {BOX}.dataset.dimensions === '2'")
    assert "map" not in query(page)

    # « Share »: the site carries both layouts by default; at least one stays ticked
    ui.navigate("/share")
    layouts = page.locator("#cx-share-layouts")
    layouts.wait_for()
    boxes = layouts.get_by_role("checkbox")
    assert boxes.count() == 2 and boxes.nth(0).is_checked() and boxes.nth(1).is_checked()
    boxes.nth(1).uncheck()
    assert boxes.nth(0).is_disabled()
    boxes.nth(1).check()
    page.locator("#cx-share-names").get_by_label("Pseudonyms", exact=False).first.check()
    page.get_by_role("button", name="Build the site").click()
    page.locator(".cx-share__done").wait_for(timeout=300_000)
    builds = page.evaluate("() => fetch('/api/share').then((r) => r.json())")
    assert builds


@pytest.fixture(scope="module")
def site_two_layouts(demo_s, tmp_path_factory):
    """A pseudonymous site of the S world carrying its flat map and a map in space."""
    from cartolex.cli import main as cli
    from cartolex.project.project import Project
    from cartolex.site.builder import SiteOptions, build_site

    root = shutil.copytree(
        demo_s, tmp_path_factory.mktemp("layouts") / "p", ignore=shutil.ignore_patterns(".lock")
    )
    assert cli(["versions", str(root), "--try-another", "--seed", "7", "--dimensions", "3",
                "--built"]) == 0  # fmt: skip
    assert cli(["build", str(root)]) == 0  # the map area only: its versions changed
    project = Project.open(root, write=True)
    try:
        record = build_site(project, SiteOptions(names=False))
        return project.layout.outputs / "sites" / record["id"]
    finally:
        project.close()


@pytest.mark.slow
def test_the_offline_site_switches_to_its_layout_in_space(site_two_layouts, browser):
    assert (site_two_layouts / "data" / "layout-v2.js").is_file()
    url = (site_two_layouts / "index.html").as_uri() + "#/map"
    page, errors, refused, context = open_page(browser, url)
    try:
        page.wait_for_function(READY)
        assert page.evaluate(f"() => {BOX}.dataset.dimensions") == "2"
        layout = page.get_by_label("Layout", exact=True)
        assert [o.split(" · ")[:2] for o in layout.locator("option").all_inner_texts()] == [
            ["v1", "2D"],
            ["v2", "3D"],
        ]
        layout.select_option("v2")
        page.wait_for_function(f"() => {BOX}.dataset.dimensions === '3'")
        page.wait_for_function(LAYER_3D, arg="people")
        assert "map=v2" in page.url
        # a reload opens the layout the address names
        page.reload()
        page.wait_for_function(f"() => {BOX}.dataset.dimensions === '3'")
        layout.select_option("v1")
        page.wait_for_function(f"() => {BOX}.dataset.dimensions === '2'")
        assert errors == [] and refused == []
    finally:
        context.close()
