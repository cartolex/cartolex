# SPDX-License-Identifier: MIT
"""Who is who, on the real app: groups of people decided with the keyboard, the clear pairs
merged in one step and undone in one, people said to be one by hand (« Same person… » from
the list and from a sheet) and undone, a merge undone from a sheet, and the addresses other
pages link to (a person, an organisation); axe on the duplicates' review and the dialog."""

from __future__ import annotations

import time

import pytest
from test_accessibility import blocking, run_axe

REAL_ROW = ".cx-table__row:not(.cx-table__row--head):not(.cx-table__row--skeleton)"


@pytest.fixture()
def doubled_app(tmp_path):
    """The app on the S demo world written as a project, with duplicate people added."""
    from app_harness import AppServer

    from cartolex.demo import generate
    from cartolex.demo.duplicates import add_duplicates
    from cartolex.demo.project import write_project

    world = generate("S", 0)
    project = write_project(world, tmp_path / "project")
    truth = add_duplicates(project, world, seed=0, share=0.25, homonyms=0.1)
    project.close()
    server = AppServer(tmp_path / "project", tmp_path / "app")
    server.truth = truth
    yield server
    server.stop()


def _get(ui, path: str) -> dict:
    return ui.page.evaluate("async (p) => (await fetch(p)).json()", path)


def _until(test, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not test():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def _merged(ui) -> int:
    people = _get(ui, "/api/people?limit=500")["items"]
    return sum(1 for p in people if p["merged_into"])


def _counts(ui) -> dict:
    return _get(ui, "/api/people/duplicates/groups?limit=1")["counts"]


def test_groups_by_keyboard_the_clear_ones_in_one_step_and_back(doubled_app, open_app, axe_source):
    ui = open_app(doubled_app, bypass_csp=True)
    page = ui.page
    ui.navigate("/people?tab=duplicates")
    review = page.locator(".cx-corpus-queue")
    review.locator(REAL_ROW).first.wait_for()
    counts = _counts(ui)

    # ↓ a group, compared side by side; D: two people, never proposed again
    review.locator(".cx-table__scroller").focus()
    page.keyboard.press("ArrowDown")
    review.locator(".cx-dup-shared").wait_for()
    violations = blocking(run_axe(ui, axe_source))
    assert violations == [], "\n".join(violations)
    page.keyboard.press("d")
    _until(lambda: _counts(ui)["distinct"] >= 1)
    # the next group comes up; 2: one person, keeping the second one
    review.locator(".cx-dup-shared").wait_for()
    page.keyboard.press("2")
    _until(lambda: _merged(ui) >= 1)
    merged = _merged(ui)

    # the clear pairs: a preview, then one step, then one « Undo »
    left = _counts(ui)["clear"]
    assert 0 < left <= counts["clear"]
    page.get_by_role("button", name="Merge the clear pairs").click()
    page.locator(".cx-dup-auto").wait_for()
    page.get_by_role("button", name="Merge them").click()
    _until(lambda: _merged(ui) > merged)
    banner = page.locator(".cx-dup-last")
    banner.wait_for()
    banner.get_by_role("button", name="Undo").click()
    _until(lambda: _merged(ui) == merged)  # the group decided by hand stays merged
    assert _get(ui, "/api/people/duplicates/groups?limit=1")["last_auto"] is None


def test_same_person_by_hand_from_the_list_and_from_a_sheet(doubled_app, open_app, axe_source):
    ui = open_app(doubled_app, bypass_csp=True)
    page = ui.page
    keep, other, _ = doubled_app.truth.same[0]  # two rows of one name and ORCID
    name = _get(ui, f"/api/people/{other}/sheet")["last_name"]
    ui.navigate("/people")
    rows = page.locator(REAL_ROW)
    rows.first.wait_for()
    page.locator(".cx-corpus-filters__search input").fill(name)
    _until(lambda: rows.count() == 2)
    # two rows selected: « Same person… »
    rows.nth(0).click()
    rows.nth(1).click(modifiers=["Control"])
    page.get_by_role("button", name="Same person…").click()
    dialog = page.locator("dialog[open] .cx-same")
    dialog.locator(".cx-dup-table").wait_for()
    violations = blocking(run_axe(ui, axe_source, include="dialog[open]"))
    assert violations == [], "\n".join(violations)
    page.locator("dialog[open]").get_by_role("button", name="Merge into").click()
    _until(lambda: _merged(ui) == 1)
    page.locator(".cx-toast").get_by_role("button", name="Undo").click()
    _until(lambda: _merged(ui) == 0)

    # « Same person as… » from a sheet: the other found by a search
    ui.navigate(f"/people?person={keep}")
    page.get_by_role("button", name="Same person as…").click()
    page.locator(".cx-same-search input").fill(name)
    page.locator(".cx-same-found button").first.click()
    page.locator("dialog[open] .cx-same .cx-dup-table").wait_for()
    page.locator("dialog[open]").last.get_by_role("button", name="Merge into").click()
    _until(lambda: _merged(ui) == 1)


def test_a_merge_undone_from_the_sheet_and_the_addresses_other_pages_use(doubled_app, open_app):
    ui = open_app(doubled_app)
    page = ui.page
    keep, other, _ = doubled_app.truth.same[0]
    etag = page.evaluate("async () => (await fetch('/api/people?limit=1')).headers.get('ETag')")
    page.evaluate(
        """async ([etag, keep, other]) => {
          const m = (await (await fetch('/api/app/manifest')).json()).security;
          const cookie = document.cookie.split('; ').find((c) => c.startsWith(`${m.csrf_cookie}=`));
          const token = m.csrf_token || (cookie || '').split('=')[1];
          await fetch('/api/people/merge', {method: 'POST', headers: {'Content-Type':
            'application/json', 'If-Match': etag, [m.csrf_header]: token},
            body: JSON.stringify({target: keep, sources: [other]})});
        }""",
        [etag, keep, other],
    )
    assert _merged(ui) == 1
    # /people?person=<id> opens the sheet: the row says whom it is merged into
    ui.navigate(f"/people?person={other}")
    drawer = page.locator(".cx-drawer, dialog[open]").last
    drawer.locator(".cx-dup-merged").wait_for()
    drawer.get_by_role("button", name="Unmerge").click()
    page.get_by_role("menuitem", name="They are two people").click()
    _until(lambda: _merged(ui) == 0)
    pairs = _get(ui, "/api/people/duplicates?show=all&limit=500")["items"]
    assert not any({p["a"], p["b"]} == {keep, other} for p in pairs)
    # /people?tab=organisations&org=<id> opens the organisation
    org = _get(ui, "/api/organisations?limit=1")["items"][0]
    ui.navigate(f"/people?tab=organisations&org={org['org_id']}")
    page.locator(".cx-org-edit").wait_for()
