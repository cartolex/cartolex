# SPDX-License-Identifier: MIT
"""Whether a page of the app is still open, and stopping a local app nobody uses.

Each page of the interface sends ``POST /api/presence`` with an id of its own
every :data:`PING_S` seconds (a hidden tab less often: browsers slow its timers
to about once a minute), and once more with ``bye`` when it closes.
:class:`Presence` keeps when each page was last heard; a page not heard for
:data:`PAGE_GONE_S` is counted closed (a crashed browser says no goodbye).

:func:`stop_when_unused` stops the local app once no page has been open for a
while and no job runs: closing the browser tab then also ends the server, its
lock on the project included, instead of leaving it running unseen.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .jobs import ACTIVE_STATES

__all__ = ["PAGE_GONE_S", "PING_S", "Presence", "stop_when_unused"]

#: How often a page says it is open.
PING_S = 30.0
#: A page not heard for this long is closed (hidden tabs call about once a minute).
PAGE_GONE_S = 600.0
#: How often the watcher looks; a longer gap means the computer slept.
CHECK_S = 15.0


class Presence:
    """The pages heard from lately, and when the app last saw a sign of life."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._guard = threading.Lock()
        self._pages: dict[str, float] = {}
        self._last = clock()

    def ping(self, page: str, *, bye: bool = False) -> None:
        """A page says it is open (or, with *bye*, that it closes)."""
        now = self._clock()
        with self._guard:
            self._last = now
            if bye:
                self._pages.pop(page, None)
            else:
                self._pages[page] = now
            for old in [p for p, seen in self._pages.items() if now - seen > PAGE_GONE_S]:
                del self._pages[old]

    def touch(self) -> None:
        """Count now as a sign of life (a job runs; the computer woke up)."""
        with self._guard:
            self._last = self._clock()

    def pages(self) -> int:
        """How many pages are open."""
        now = self._clock()
        with self._guard:
            return sum(1 for seen in self._pages.values() if now - seen <= PAGE_GONE_S)

    def idle_for(self) -> float:
        """Seconds since the last sign of life when no page is open; 0 while one is."""
        if self.pages():
            return 0.0
        with self._guard:
            return self._clock() - self._last


def stop_when_unused(
    presence: Presence,
    jobs: Any,
    after_s: float,
    stop: Callable[[], None],
    stopped: threading.Event,
    *,
    check_s: float = CHECK_S,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Call *stop* once no page has been open for *after_s* seconds and no job runs.

    Runs until *stopped* is set. A running job counts as a sign of life, so the
    app stops *after_s* after the job ends; a long gap between two looks (the
    computer slept) gives the pages *after_s* to call again.
    """
    log = logging.getLogger("cartolex.app")
    last_look = clock()
    while not stopped.wait(check_s):
        now = clock()
        if now - last_look > 3 * check_s:
            presence.touch()
        last_look = now
        if any(job.state in ACTIVE_STATES for job in jobs.list()):
            presence.touch()
            continue
        idle = presence.idle_for()
        if idle >= after_s:
            log.info(
                "no page open: stopping",
                extra={"event": "idle_stop", "idle_s": round(idle)},
            )
            stop()
            return
