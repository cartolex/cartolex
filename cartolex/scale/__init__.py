# SPDX-License-Identifier: MIT
"""Modest resources for large projects: a budget, and worker processes that keep order.

A project of millions of texts is built on an ordinary computer by holding little at a
time and using several processors. This package gives the pieces every stage shares:

- :class:`Budget`: the memory, the worker processes and the scratch folder a computer
  gives to cartolex, set once for the computer (results never depend on it);
- :func:`ordered_map`: a function applied to items in worker processes, the results
  given back in the items' order, with at most a few items in flight;
- :func:`scratch_folder`: a job's folder of temporary files, swept when a killed job
  left it behind;
- :func:`sorted_unique`: the distinct values of an array, by a sort (fast on millions).

It imports only the standard library and the declared dependencies, so that the
collection, the project format and the engine can all use it.
"""

from __future__ import annotations

from .arrays import sorted_unique
from .budget import Budget, resident_mb, total_memory_mb
from .pool import ordered_map, worker_setup
from .scratch import pid_alive, scratch_folder

__all__ = [
    "Budget",
    "ordered_map",
    "pid_alive",
    "resident_mb",
    "scratch_folder",
    "sorted_unique",
    "total_memory_mb",
    "worker_setup",
]
