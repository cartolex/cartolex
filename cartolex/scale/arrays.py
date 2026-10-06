# SPDX-License-Identifier: MIT
"""Array operations that stay fast on millions of values."""

from __future__ import annotations

from typing import Any

__all__ = ["sorted_unique"]


def sorted_unique(values: Any) -> Any:
    """The distinct values of the 1-D array *values*, sorted: what ``np.unique(values)``
    gives, by a sort. A plain ``np.unique`` call goes through a hash table since numpy
    2.3, about forty times slower than a sort on ten million distinct integers. Values
    other than integers (floats, whose NaNs ``np.unique`` takes as one) go to
    ``np.unique``."""
    import numpy as np

    a = np.asarray(values).reshape(-1)
    if a.dtype.kind not in "iub":
        return np.unique(a)
    a = np.sort(a)
    if len(a) < 2:
        return a
    keep = np.empty(len(a), dtype=bool)
    keep[0] = True
    np.not_equal(a[1:], a[:-1], out=keep[1:])
    return a[keep]
