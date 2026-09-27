# SPDX-License-Identifier: MIT
"""Lab covariance ellipse: real 2x2 covariance, with guards (no degenerate ellipse)."""

from __future__ import annotations

import numpy as np

from cartolex.atlas.plots import lab_covariance


def test_returns_covariance_components() -> None:
    xs = np.array([0.0, 2.0, 0.0, 2.0, 1.0])
    ys = np.array([0.0, 0.0, 2.0, 2.0, 1.0])
    c = lab_covariance(xs, ys)
    assert c is not None
    expected = np.cov(np.vstack([xs, ys]))  # ddof=1
    assert (
        c["cov_xx"] == np.float64(expected[0, 0]).item() or abs(c["cov_xx"] - expected[0, 0]) < 1e-9
    )
    assert abs(c["cov_yy"] - expected[1, 1]) < 1e-9
    assert abs(c["cov_xy"] - expected[0, 1]) < 1e-9
    # Convenience std/correlation also provided.
    assert abs(c["sx"] - np.sqrt(expected[0, 0])) < 1e-9
    assert abs(c["sy"] - np.sqrt(expected[1, 1])) < 1e-9


def test_none_when_too_few_points() -> None:
    assert lab_covariance(np.array([0.0, 1.0]), np.array([0.0, 1.0])) is None


def test_none_when_collinear_rank_deficient() -> None:
    # Perfectly collinear points → covariance is rank 1 → no ellipse.
    xs = np.array([0.0, 1.0, 2.0, 3.0])
    ys = np.array([0.0, 1.0, 2.0, 3.0])
    assert lab_covariance(xs, ys) is None
