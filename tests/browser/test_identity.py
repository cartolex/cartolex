# SPDX-License-Identifier: MIT
"""Who is who, on the real app: pairs of people decided with the keyboard, the clear pairs
merged in one step and undone in one, a merge undone from a sheet, and the addresses other
pages link to (a person, an organisation); axe on the duplicates' review."""

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


def test_pairs_by_keyboard_the_clear_ones_in_one_step_and_back(doubled_app, open_app, axe_source):
    ui = open_app(doubled_app, bypass_csp=True)
    page = ui.page
    ui.navigate("/people?tab=duplicates")
    review = page.locator(".cx-corpus-queue")
    review.locator(REAL_ROW).first.wait_for()
    counts = _get(ui, "/api/people/duplicates?limit=1")["counts"]

    # ↓ a pair, compared side by side; D: two people, never proposed again
    review.locator(".cx-table__scroller").focus()
    page.keyboard.press("ArrowDown")
    review.locator(".cx-dup-shared").wait_for()
    violations = blocking(run_axe(ui, axe_source))
    assert violations == [], "\n".join(violations)
    page.keyboard.press("d")
    _until(lambda: _get(ui, "/api/people/duplicates?limit=1")["counts"]["distinct"] == 1)
    # the next pair comes up; 2: one person, keeping the right one
    review.locator(".cx-dup-shared").wait_for()
    page.keyboard.press("2")
    _until(lambda: _merged(ui) == 1)

    # the clear pairs: a preview, then one step, then one « Undo »
    left = _get(ui, "/api/people/duplicates?limit=1")["counts"]["clear"]
    assert 0 < left <= counts["clear"]
    page.get_by_role("button", name="Merge the clear pairs").click()
    page.locator(".cx-dup-auto").wait_for()
    page.get_by_role("button", name="Merge them").click()
    _until(lambda: _merged(ui) > 1)
    banner = page.locator(".cx-dup-last")
    banner.wait_for()
    banner.get_by_role("button", name="Undo").click()
    _until(lambda: _merged(ui) == 1)  # the pair decided by hand stays merged
    assert _get(ui, "/api/people/duplicates?limit=1")["last_auto"] is None


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
