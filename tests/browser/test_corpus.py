# SPDX-License-Identifier: MIT
"""The corpus screen on the real app and the demo services (world XS, on this computer).

One scenario: the people imported, « what leaves the computer » before a
collection (Start waits for the consent), the collection as a job, the
identity queue decided with the keyboard (↓, 1, ⏎, N), the clear matches
accepted in bulk, a person's sheet saying why their profile is empty; axe on
the screen.
"""

from __future__ import annotations

import csv
import io
import time

import pytest
from test_accessibility import blocking, run_axe

REAL_ROW = ".cx-table__row:not(.cx-table__row--head):not(.cx-table__row--skeleton)"


@pytest.fixture(scope="module")
def services():
    from cartolex.demo import generate
    from cartolex.demo.services import DemoServices

    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture()
def corpus_app(tmp_path, services):
    """The app on a project holding the demo list, collecting from the demo services."""
    from app_harness import AppServer

    from cartolex.app.collect_service import ServiceCollection
    from cartolex.collect import local_settings
    from cartolex.collect.people_import import import_people
    from cartolex.project import Project
    from cartolex.project.models import Level

    project = Project.init(
        tmp_path / "project", name="Coast", domain_title="Coastal systems",
        corpus_languages=("en", "fr"),
    )  # fmt: skip
    levels = [Level(id="lab", names={"en": "Lab"}), Level(id="institution", names={"en": "Inst"})]
    project.save_config(project.config.model_copy(update={"levels": levels}), action="levels")
    rows = services.bibliography.people_rows()
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    import_people(project, out.getvalue())
    project.close()
    collection = ServiceCollection(local_settings(services.endpoints()), local=True)
    server = AppServer(tmp_path / "project", tmp_path / "app", collection=collection)
    yield server
    server.stop()


def _get(ui, path: str) -> dict:
    return ui.page.evaluate("async (p) => (await fetch(p)).json()", path)


def _wait_jobs(ui, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        jobs = _get(ui, "/api/jobs")["jobs"]
        if jobs and jobs[0]["state"] not in ("queued", "running", "cancelling"):
            return jobs[0]
        assert time.monotonic() < deadline, jobs
        time.sleep(0.1)


def _count(ui, identity: str) -> int:
    return _get(ui, f"/api/people?identity={identity}&limit=1")["total"]


def _until(test, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not test():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def test_collect_identities_by_keyboard_and_a_sheet_that_says_why(corpus_app, open_app, axe_source):
    ui = open_app(corpus_app, bypass_csp=True)
    page = ui.page
    ui.navigate("/people")
    page.locator(f".cx-corpus {REAL_ROW}").first.wait_for()

    # ── what leaves the computer comes first; Start waits for the consent ──
    page.get_by_role("button", name="Collect").click()
    page.get_by_role("menuitem", name="Find identities").click()
    page.get_by_role("button", name="What leaves the computer").click()
    notice = page.locator(".cx-corpus-notice")
    notice.wait_for()
    assert "OpenAlex" in notice.inner_text() and "HAL" in notice.inner_text()
    assert "never leaves" in notice.inner_text()
    start = page.get_by_role("button", name="Start")
    assert start.is_disabled()
    page.get_by_label("I have read what leaves the computer").check()
    start.click()
    assert _wait_jobs(ui)["state"] == "succeeded"

    # ── the queue, by the keyboard: ↓ a person, 1 a candidate, ⏎ confirm, N none ──
    page.get_by_role("tab", name="Identities").click()
    queue = page.locator(".cx-corpus-queue")
    queue.locator(REAL_ROW).first.wait_for()
    before = _count(ui, "pending")
    queue.locator(".cx-table__scroller").focus()
    page.keyboard.press("ArrowDown")
    queue.locator(".cx-corpus-panel__head").wait_for()
    assert queue.locator(".cx-corpus-cand").count() >= 1
    page.keyboard.press("1")
    assert queue.locator(".cx-corpus-cand.is-picked").count() == 1
    page.keyboard.press("Enter")
    _until(lambda: _count(ui, "pending") == before - 1)
    # the next person comes up; N says none of the candidates is them
    queue.locator(".cx-corpus-panel__head").wait_for()
    page.keyboard.press("n")
    _until(lambda: _count(ui, "none") == 1)
    assert _count(ui, "pending") == before - 2 and _count(ui, "confirmed") == 1
    violations = blocking(run_axe(ui, axe_source))
    assert violations == [], "\n".join(violations)

    # ── the single clear matches in bulk ──
    accept = page.get_by_role("button", name="Accept the")
    accept.click()
    _until(lambda: _count(ui, "confirmed") > 1)

    # ── a person without a usable profile has a sheet saying why ──
    page.get_by_role("tab", name="People").click()
    page.get_by_label("Coverage", exact=True).select_option("no_data")
    rows = page.locator(f".cx-corpus {REAL_ROW}")
    rows.first.wait_for()
    page.locator(".cx-corpus .cx-table__scroller").focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    sheet = page.locator(".cx-corpus-sheet")
    sheet.wait_for()
    assert "First blocking cause" in sheet.inner_text()
    assert "No data" in sheet.inner_text()


def test_two_quick_decisions_on_a_slow_machine_are_both_saved(corpus_app, open_app):
    # A slow list (a loaded machine): a read asked before the first decision answers during
    # its save. The second decision, made at once on the next person, waits for the first
    # save and uses the version it answered; the decided person does not come back.
    ui = open_app(corpus_app)
    page = ui.page
    ui.navigate("/people")
    page.locator(f".cx-corpus {REAL_ROW}").first.wait_for()
    page.get_by_role("button", name="Collect").click()
    page.get_by_role("menuitem", name="Find identities").click()
    page.get_by_role("button", name="What leaves the computer").click()
    page.get_by_label("I have read what leaves the computer").check()
    page.get_by_role("button", name="Start").click()
    assert _wait_jobs(ui)["state"] == "succeeded"
    page.get_by_role("tab", name="Identities").click()
    queue = page.locator(".cx-corpus-queue")
    queue.locator(REAL_ROW).first.wait_for()
    before = _count(ui, "pending")
    collection = corpus_app.app.state.cartolex.collection
    candidates = collection.candidates

    def slow(*args, **kwargs):
        time.sleep(1.0)
        return candidates(*args, **kwargs)

    collection.candidates = slow
    queue.get_by_label("Found by", exact=True).select_option("openalex")  # a read starts
    queue.locator(".cx-table__scroller").focus()
    page.keyboard.press("ArrowDown")
    queue.locator(".cx-corpus-panel__head").wait_for()
    page.keyboard.press("1")
    page.keyboard.press("Enter")
    # the next person at once, while the first save and the read are still out
    page.keyboard.press("ArrowDown")
    queue.locator(".cx-corpus-panel__head").wait_for()
    page.keyboard.press("n")
    _until(lambda: _count(ui, "none") == 1, timeout=15)
    assert _count(ui, "pending") == before - 2
    # the late read has answered: the queue shows neither decided person, and no error
    left = _get(ui, "/api/collection/identities?state=pending&finder=openalex&limit=1")["total"]
    _until(lambda: queue.locator(REAL_ROW).count() == left, timeout=15)
    assert queue.locator(".cx-error-card").count() == 0


@pytest.fixture()
def snapshot_app(tmp_path, services):
    """The app on a project whose people are confirmed, with a mini snapshot to save."""
    from app_harness import AppServer

    from cartolex.app.collect_service import ServiceCollection
    from cartolex.collect import local_settings
    from cartolex.collect.people_import import import_people
    from cartolex.collect.resolve import confirm
    from cartolex.demo.services import write_snapshot
    from cartolex.project import Project
    from cartolex.project.tables import read_source_table

    bib = services.bibliography
    project = Project.init(tmp_path / "project", name="Coast", domain_title="Coastal systems")
    rows = bib.people_rows()
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    import_people(project, out.getvalue())
    by_name = {(t.last_name, t.first_name): t for t in bib.truth.values()}
    for p in read_source_table(project.layout.table("people"), "people").to_pylist():
        truth = by_name[(p["last_name"], p["first_name"])]
        if truth.records:
            confirm(project, p["person_id"], truth.records)
    project.close()
    write_snapshot(bib, tmp_path / "openalex", per_part=20)
    collection = ServiceCollection(local_settings(services.endpoints()), local=True)
    server = AppServer(tmp_path / "project", tmp_path / "app", collection=collection)
    yield server, tmp_path / "openalex"
    server.stop()


def test_the_snapshot_is_saved_then_offered_beside_the_api(snapshot_app, open_app, axe_source):
    server, folder = snapshot_app
    ui = open_app(server, bypass_csp=True)
    page = ui.page

    # ── Settings › Data sources: the folder, checked against its manifests ──
    ui.navigate("/settings?section=sources")
    page.get_by_label("Snapshot folder").fill(str(folder))
    page.get_by_role("button", name="Save the folder").click()
    page.get_by_text("Ready: release of").wait_for()
    toast = page.locator(".cx-toast", has_text="Snapshot folder saved on this computer")
    toast.get_by_role("button", name="Dismiss").click()  # it covers the page a moment
    toast.wait_for(state="detached")
    page.evaluate("window.scrollTo(0, 0)")  # scrolled, the sticky bar covers the side links
    violations = blocking(run_axe(ui, axe_source))
    assert violations == [], "\n".join(violations)

    # ── a harvest offers both ways of reading OpenAlex; the API plans again ──
    ui.navigate("/people")
    page.locator(f".cx-corpus {REAL_ROW}").first.wait_for()
    page.get_by_role("button", name="Collect").click()
    page.get_by_role("menuitem", name="Harvest texts").click()
    page.get_by_role("button", name="What leaves the computer").click()
    route = page.get_by_role("group", name="Read OpenAlex from")
    route.wait_for()
    snapshot = route.get_by_role("radio", name="the snapshot of")
    api = route.get_by_role("radio", name="its API, online")
    assert snapshot.is_checked() or api.is_checked()
    snapshot.check()
    _until(
        lambda: (
            "OpenAlex is read from the snapshot" in page.locator(".cx-corpus-notice").inner_text()
        )
    )
    api.check()
    _until(
        lambda: (
            "OpenAlex is read from the snapshot"
            not in page.locator(".cx-corpus-notice").inner_text()
        )
    )
    assert api.is_checked() and not snapshot.is_checked()
    violations = blocking(run_axe(ui, axe_source))
    assert violations == [], "\n".join(violations)
