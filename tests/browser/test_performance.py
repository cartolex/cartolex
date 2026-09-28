# SPDX-License-Identifier: MIT
"""Budgets and leaks: route ready within a second, few requests per navigation, a flat memory.

Budgets (on the fixture server): every navigation inside the app is ready in
under **1 s** and makes at most **5 requests** (the jobs poller's own
`GET /api/jobs`, which runs on its clock and not because of a navigation, is
not counted).

Leaks: after two warm-up rounds (modules, style sheets and caches loaded),
ten more round trips between the gallery and the overview must leave the DOM
node count, the event-listener count and the used JavaScript heap flat, each
measured after a forced garbage collection:

* DOM nodes: at most 2 % (or 50 nodes) more;
* event listeners: at most 2 % (or 10) more;
* JS heap: at most 10 % (or 2 MB) more — the heap moves with the engine's
  own caches (compiled code, inline caches), so it gets a wider band.

Set ``$CARTOLEX_UI_MEASURES`` to a file to keep the numbers.
"""

from __future__ import annotations

import os

import pytest
from browser_harness import write_measures

READY_MS = 1000
REQUESTS = 5
MEASURES = os.environ.get("CARTOLEX_UI_MEASURES")


def _counted(requests: list[str]) -> list[str]:
    return [u for u in requests if not u.split("?")[0].endswith("/api/jobs")]


def test_navigation_budgets(ui):
    ui.open("/overview")
    first = ui.ready_log()[-1]
    boot = {
        "ready_after_load_ms": round(first["at"], 1),
        "first_route_ms": round(first["duration"], 1),
        "requests": len(_counted(ui.collected.requests)),
    }
    rows = []
    for path in ("/keywords", "/gallery", "/overview", "/gallery", "/demo", "/nowhere", "/themes"):
        start = len(ui.collected.requests)
        ui.navigate(path)
        entry = ui.ready_log()[-1]
        made = _counted(ui.collected.requests[start:])
        rows.append(
            {
                "path": path,
                "page": entry["pageId"],
                "ready_ms": round(entry["duration"], 1),
                "requests": len(made),
                "urls": [u.split("/", 3)[-1] for u in made],
            }
        )
    write_measures(
        MEASURES,
        "budgets",
        {"boot": boot, "navigations": rows},
    )
    slow = [r for r in rows if r["ready_ms"] >= READY_MS]
    chatty = [r for r in rows if r["requests"] > REQUESTS]
    assert slow == [], f"route ready takes {READY_MS} ms or more: {slow}"
    assert chatty == [], f"more than {REQUESTS} requests: {chatty}"


def _round_trip(ui) -> None:
    ui.navigate("/gallery")
    ui.navigate("/overview")


@pytest.mark.slow
def test_teardown_leaves_nothing_behind(ui):
    ui.open("/overview")
    cdp = ui.page.context.new_cdp_session(ui.page)
    cdp.send("Performance.enable")
    for _ in range(2):
        _round_trip(ui)
    before = ui.metrics(cdp)
    for _ in range(10):
        _round_trip(ui)
    after = ui.metrics(cdp)
    # Nothing of the gallery remains once it is left.
    left = ui.page.evaluate(
        """() => ({
          gallery: document.querySelectorAll('[data-gallery]').length,
          dialogs: document.querySelectorAll('dialog[open]').length,
          menus: document.querySelectorAll('[role=menu]').length,
          tooltips: document.querySelectorAll('.cx-tooltip').length,
        })"""
    )
    growth = {k: after[k] - before[k] for k in before}
    write_measures(
        MEASURES,
        "leaks",
        {
            "rounds": 10,
            "before": before,
            "after": after,
            "growth": growth,
            "growth_percent": {k: round(100 * growth[k] / before[k], 2) for k in before},
        },
    )
    assert left == {"gallery": 0, "dialogs": 0, "menus": 0, "tooltips": 0}
    assert growth["nodes"] <= max(0.02 * before["nodes"], 50), (before, after)
    assert growth["listeners"] <= max(0.02 * before["listeners"], 10), (before, after)
    assert growth["heap"] <= max(0.10 * before["heap"], 2 * 1024 * 1024), (before, after)
