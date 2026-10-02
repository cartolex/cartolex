# SPDX-License-Identifier: MIT
"""The app's cache of computed views: one computation per key, however many ask at once."""

from __future__ import annotations

import threading
import time

from cartolex.app.runtime import Cache


def test_requests_asking_at_once_share_one_computation() -> None:
    cache = Cache(4)
    runs: list[int] = []

    def compute() -> str:
        runs.append(1)
        time.sleep(0.2)
        return "view"

    got: list[str] = []
    threads = [
        threading.Thread(target=lambda: got.append(cache.get("k", compute))) for _ in range(4)
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert got == ["view"] * 4 and len(runs) == 1
