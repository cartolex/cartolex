# SPDX-License-Identifier: MIT
"""Helpers of the browser tests: the page under test, readiness, metrics.

`UI` wraps a Playwright page opened on the fixture server. Every wait uses
a function predicate, never a string: the app's Content-Security-Policy
forbids `eval`, and Playwright evaluates string predicates with it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PREFS_KEY = "cartolex.prefs/1"


@dataclass
class Collected:
    """What a page said that a test did not ask for."""

    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    csp_violations: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)


class UI:
    """The app in one browser page."""

    def __init__(self, page, base: str, collected: Collected) -> None:
        self.page = page
        self.base = base
        self.collected = collected

    # ── navigation and readiness ────────────────────────────────────────────
    def token(self) -> int:
        """The token of the last navigation that became ready (0 before any)."""
        value = self.page.evaluate("() => document.documentElement.dataset.routeReady || '0'")
        return int(value)

    def wait_ready(self, after: int = 0, timeout: float = 10000) -> int:
        """Wait until a navigation newer than *after* is ready; return its token."""
        self.page.wait_for_function(
            "(after) => Number(document.documentElement.dataset.routeReady || 0) > after",
            arg=after,
            timeout=timeout,
        )
        return self.token()

    def open(self, path: str = "/gallery") -> None:
        """Load the app at *path* and wait for its first page."""
        self.page.goto(self.base + path)
        self.wait_ready(0)

    def navigate(self, path: str) -> int:
        """Navigate inside the app (as a link would) and wait for the page."""
        before = self.token()
        self.page.evaluate("(path) => window.__cartolex.navigate(path)", path)
        return self.wait_ready(before)

    def ready_log(self) -> list[dict]:
        """The last navigations: page id and time to ready (ms)."""
        return self.page.evaluate("() => window.__cartolex.readyLog()")

    def missing_keys(self) -> list[str]:
        """Catalogue keys the pages asked for and no catalogue had."""
        return self.page.evaluate("() => window.__cartolex.missingKeys()")

    def active(self) -> dict:
        """A description of the focused element."""
        return self.page.evaluate(
            """() => {
              const el = document.activeElement;
              if (!el || el === document.body) return {tag: 'body'};
              return {tag: el.tagName.toLowerCase(), role: el.getAttribute('role'),
                label: el.getAttribute('aria-label') || '', text: (el.textContent || '').trim().slice(0, 60),
                id: el.id, section: (el.closest('[data-gallery]') || {}).dataset?.gallery || null,
                classes: el.className && el.className.baseVal === undefined ? el.className : ''};
            }"""
        )

    def section(self, name: str):
        """The gallery section *name* (a locator)."""
        return self.page.locator(f'[data-gallery="{name}"]')

    # ── metrics ─────────────────────────────────────────────────────────────
    def metrics(self, cdp) -> dict[str, float]:
        """DOM nodes, event listeners and used JS heap, after a full garbage collection."""
        for _ in range(3):
            cdp.send("HeapProfiler.collectGarbage")
        values = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
        return {
            "nodes": values["Nodes"],
            "listeners": values["JSEventListeners"],
            "heap": values["JSHeapUsedSize"],
        }


def prefs_script(theme: str | None = None, locale: str | None = None) -> str:
    """An init script that stores the interface preferences before the app starts."""
    data = {"theme": theme or "system", "locale": locale, "dismissedJobs": []}
    return f"try {{ localStorage.setItem({json.dumps(PREFS_KEY)}, {json.dumps(json.dumps(data))}); }} catch (e) {{}}"


def write_measures(path: str | None, name: str, values: dict) -> None:
    """Add *values* under *name* to the JSON file *path* (when a path is given)."""
    if not path:
        return
    target = Path(path)
    data = json.loads(target.read_text()) if target.is_file() else {}
    data[name] = values
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=1, sort_keys=True))
