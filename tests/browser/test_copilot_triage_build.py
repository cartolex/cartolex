# SPDX-License-Identifier: MIT
"""A copilot's keyword triage, then builds, on the real app (the S demo world at depth 2).

The flow of a person who triages with a copilot from the Lexicon: the bundle is
downloaded from the dialog, cartolex's own kit answers it (in this process, in
parts the second time), the result goes back through the dialog and is accepted
(all, then a part); then builds with the route left at « No AI » and set to
« With your copilot », before and after the candidates are found again. The AI
clean-up must read as done with the copilot, waiting for it, or done in part,
on the pre-flight sheet, the build's tracker and result, the waiting card and
the overview; never « Skipped » once a copilot's triage applies.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from test_theme_editor import api, dialog

CLEAN_UP = "[data-stage='keywords.triage'] .cx-tracker__state"


def _answer(folder: Path, parts: int) -> list[Path]:
    """Answer each part of the bundle unpacked in *folder* with the kit: every candidate its
    session shows is kept, but the first of each part, excluded as too generic."""
    from cartolex.copilot import open_bundle

    results = []
    for part in range(1, parts + 1) if parts > 1 else [None]:
        s = open_bundle(folder, part=part)
        while not s.next_batch(50).startswith("Every group of this part has been shown"):
            pass
        shown = sorted(s.shown)
        rows = [
            {"term": s.items[i]["term"], "lang": s.items[i]["lang"], "decision": "keep",
             "code": "C", "reason": "a term of the field"}
            for i in shown[1:]
        ]  # fmt: skip
        rows.append({"term": s.items[shown[0]]["term"], "lang": s.items[shown[0]]["lang"],
                     "decision": "exclude", "code": "G", "reason": "too generic"})  # fmt: skip
        s.decide_many(rows, unread_ok=True)
        results.append(s.write_result("The field's terms are kept.", curator_agreed=True))
    return results


def _triage(ui, folder: Path, *, parts: int = 1, only_one: bool = False) -> None:
    """Lexicon › Triage with AI › With an AI copilot: download the bundle, answer it, import
    the result(s) and accept every decision (or, with *only_one*, a single one)."""
    page = ui.page
    ui.navigate("/keywords?band=check")
    page.locator(".cx-corpus__actions .cx-menubutton button").click()
    page.get_by_role("menuitem").nth(0).click()
    d = dialog(ui)
    link = d.locator("a[download]")
    link.wait_for()
    if parts > 1:
        d.get_by_label("Parts").select_option(str(parts))
        page.wait_for_function(
            "(n) => document.querySelector('dialog[open] a[download]')?.href.includes('parts=' + n)",
            arg=parts,
        )
    with page.expect_download() as download:
        link.click()
    zipfile.ZipFile(io.BytesIO(Path(download.value.path()).read_bytes())).extractall(folder)
    results = _answer(folder, parts)
    d.locator(".cx-dialog__footer button").last.click()
    d.locator("input[type=file]").set_input_files([str(r) for r in results])
    d.locator(".cx-kw-review").wait_for()
    if only_one:
        d.get_by_role("button", name="Choose none", exact=True).click()
        d.locator(".cx-kw-review [role=row][aria-rowindex='2']").click()
    d.locator(".cx-dialog__footer button").last.click()
    page.locator(".cx-toast", has_text="accepted").first.wait_for()


def _route(ui, route: str) -> None:
    version = api(ui, "POST", "/api/build", {"dry_run": True})["data"]["ai"]["version"]
    assert (
        api(ui, "PUT", "/api/build/ai", {"keywords.triage": route}, f'"{version}"')["status"] == 200
    )


def _preflight(ui, path: str) -> str:
    """The pre-flight sheet of *path*: the AI clean-up's word."""
    ui.navigate(path)
    ui.page.locator(".cx-build-pre__plan").wait_for()
    return ui.page.locator(f".cx-build-pre__plan {CLEAN_UP}").inner_text()


def _build(ui, button: str, outcome: str = "succeeded") -> str:
    """Press *button*, wait for the build's end; the AI clean-up's word in its tracker."""
    page = ui.page
    page.get_by_role("button", name=re.compile(button)).click()
    card = ".cx-build-wait" if outcome == "waiting" else ".cx-build-result"
    page.locator(f"{card} .cx-build-result__sentence[data-outcome='{outcome}']").wait_for(
        timeout=120_000
    )
    return page.locator(f"{card} {CLEAN_UP}").inner_text()


def _overview(ui, word: str) -> str:
    """The overview's tracker, once its AI clean-up says *word* (its state read again after
    the cached one): the clean-up's line."""
    ui.navigate("/overview")
    row = ui.page.locator(".cx-overview-stages [data-stage='keywords.triage']")
    row.wait_for()
    try:
        row.locator(".cx-tracker__state", has_text=word).wait_for(timeout=5000)
    except Exception:
        said = row.locator(".cx-tracker__state").inner_text()
        raise AssertionError(f"the overview says {said!r}") from None
    return row.locator(".cx-tracker__reason").first.inner_text()


DONE = "Done with your copilot"
WAITS = "Waits for your copilot"


def test_a_copilot_triage_reads_as_done_through_every_build(demo_s, app_for, open_app, tmp_path):
    ui = open_app(app_for(demo_s))
    page = ui.page

    # Route left at « No AI »: the triage accepted, then a whole build.
    _triage(ui, tmp_path / "first")
    assert _preflight(ui, "/build") == DONE
    assert _build(ui, r"^Build \d+ stages$") == DONE
    assert _overview(ui, DONE).startswith("Done with your copilot (")

    # Route « With your copilot », the candidates found again: the build waits for it.
    _route(ui, "copilot")
    assert _preflight(ui, "/build?scope=keywords&force=keywords.extract") == WAITS
    assert _build(ui, "^Build 1 stage, then pause$", "waiting") == WAITS
    _overview(ui, WAITS)
    # Its triage of the new candidates, in two parts, one decision accepted; then continue.
    _triage(ui, tmp_path / "second", parts=2, only_one=True)
    _overview(ui, DONE)
    ui.navigate("/build?scope=keywords")
    assert _build(ui, "^Continue the build$") == DONE
    _overview(ui, DONE)

    # Back to « No AI », the candidates found again: the copilot's decisions apply in part.
    _route(ui, "none")
    assert (
        _preflight(ui, "/build?scope=keywords&force=keywords.extract")
        == "Partly done with your copilot"
    )
    page.locator("[data-note='preflight_copilot_new']").wait_for()
    assert _build(ui, "^Build 2 stages$") == "Partly done with your copilot"
    line = _overview(ui, "Partly done with your copilot")
    assert line.startswith("No AI clean-up of the candidates found since your copilot's triage")
    assert ui.collected.console_errors == [] and ui.collected.page_errors == []
