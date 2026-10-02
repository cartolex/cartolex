# SPDX-License-Identifier: MIT
"""A local app stops once no page of its interface is open and no job runs."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from cartolex.app.presence import PAGE_GONE_S, Presence, stop_when_unused


def test_pages_keep_the_app_in_use_until_they_close_or_go_silent():
    now = [0.0]
    presence = Presence(clock=lambda: now[0])
    presence.ping("a")
    presence.ping("b")
    now[0] = 100.0
    presence.ping("a", bye=True)
    assert presence.pages() == 1 and presence.idle_for() == 0.0
    now[0] = 30.0 + PAGE_GONE_S  # b said nothing since 0: closed (no goodbye after a crash)
    assert presence.pages() == 0 and presence.idle_for() == now[0] - 100.0
    presence.ping("c")
    assert presence.idle_for() == 0.0


class _Jobs:
    def __init__(self, *states: str) -> None:
        self.states = list(states)

    def list(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(state=s) for s in self.states]


def test_the_app_stops_only_after_its_running_jobs_end():
    jobs = _Jobs("running", "failed")
    stopped = threading.Event()
    watcher = threading.Thread(
        target=stop_when_unused,
        args=(Presence(), jobs, 0.05, stopped.set, stopped),
        kwargs={"check_s": 0.01},
        daemon=True,
    )
    watcher.start()
    assert not stopped.wait(0.3)
    jobs.states = ["succeeded", "failed"]
    assert stopped.wait(5)
    watcher.join(5)
