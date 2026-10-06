# SPDX-License-Identifier: MIT
"""A function applied in worker processes, its results given back in order."""

from __future__ import annotations

import itertools
import multiprocessing
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Any, TypeVar

__all__ = ["ordered_map", "worker_setup"]

T = TypeVar("T")
R = TypeVar("R")

#: Items in flight per worker: enough to keep every worker busy, little memory.
AHEAD = 2


def worker_setup() -> None:
    """In a worker process: Ctrl-C, which a terminal sends to the whole process group, is
    for the main process, which stops the workers itself; numerical libraries use one
    thread (the workers are the parallelism); and when memory runs out (a job capped
    below the computer's memory), a worker is stopped before the main process, which
    then says why the job failed."""
    import contextlib
    import signal

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    with contextlib.suppress(OSError), open("/proc/self/oom_score_adj", "w") as fh:
        fh.write("800")
    try:
        from threadpoolctl import threadpool_limits

        threadpool_limits(1)
    except ImportError:  # pragma: no cover - threadpoolctl is a dependency
        pass


def _start(initializer: Callable[..., None] | None, initargs: tuple[Any, ...]) -> None:
    worker_setup()
    if initializer is not None:
        initializer(*initargs)


def ordered_map(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    workers: int,
    initializer: Callable[..., None] | None = None,
    initargs: tuple[Any, ...] = (),
    ahead: int = AHEAD,
) -> Iterator[R]:
    """``fn(item)`` for each of *items*, in the items' order, computed in *workers* worker
    processes (spawned, so the same on every system), with at most ``workers × ahead``
    items in flight: memory holds a few items' results, however many there are.

    *initializer* runs once in each worker (with *initargs*), to set what every item
    needs (a large lookup table is sent once per worker, not with each item). With
    *workers* ≤ 1, or a single item, everything runs here, in order, without
    processes. Leaving the loop early cancels the items not started; an exception in
    *fn* is raised here.
    """
    items = iter(items)
    first = list(itertools.islice(items, 2))
    if workers <= 1 or len(first) < 2:
        if initializer is not None:
            initializer(*initargs)
        for item in itertools.chain(first, items):
            yield fn(item)
        return
    items = itertools.chain(first, items)
    context = multiprocessing.get_context("spawn")
    pool = ProcessPoolExecutor(
        workers, mp_context=context, initializer=_start, initargs=(initializer, initargs)
    )
    queue: deque[Future[R]] = deque()
    try:
        for item in items:
            queue.append(pool.submit(fn, item))
            while len(queue) >= workers * ahead:
                yield queue.popleft().result()
        while queue:
            yield queue.popleft().result()
    finally:
        for future in queue:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
