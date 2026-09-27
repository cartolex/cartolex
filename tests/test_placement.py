# SPDX-License-Identifier: MIT
"""Nearest-neighbour placement: weights, geometry, determinism."""

from __future__ import annotations

import numpy as np
import pytest

from cartolex.atlas.placement import neighbour_weights, place


def test_weights_sum_to_one_and_decrease_with_distance():
    d = np.array([[0.1, 0.2, 0.4, 0.8], [0.3, 0.3, 0.3, 0.3], [0.0, 0.5, 0.5, 1.0]])
    w = neighbour_weights(d)
    assert np.allclose(w.sum(axis=1), 1.0)
    assert np.all(np.diff(w[0]) < 0)
    assert np.allclose(w[1], 0.25)
    assert neighbour_weights(np.array([[0.7]])).tolist() == [[1.0]]


def _anchors():
    rng = np.random.default_rng(0)
    centres = np.eye(3) * 5
    z = np.vstack([c + rng.normal(scale=0.3, size=(10, 3)) for c in centres])
    xy = np.vstack([np.full((10, 2), (i * 10.0, 0.0)) + rng.normal(size=(10, 2)) for i in range(3)])
    return z, xy


def test_a_vector_lands_among_its_neighbours():
    z, xy = _anchors()
    placed = place(np.array([[5.0, 0.1, 0.0], [0.0, 0.0, 5.0]]), z, xy, k=5)
    assert placed.xy[0, 0] < 3 and placed.xy[1, 0] > 17
    assert set(placed.neighbours[0]) <= set(range(10))
    assert np.allclose(placed.weights.sum(axis=1), 1.0)


def test_placement_is_the_same_whatever_the_chunking():
    z, xy = _anchors()
    vectors = np.random.default_rng(1).normal(size=(57, 3))
    whole = place(vectors, z, xy, k=7, chunk=1000)
    rows = place(vectors, z, xy, k=7, chunk=1)
    assert np.array_equal(whole.xy, rows.xy)
    assert np.array_equal(whole.neighbours, rows.neighbours)


def test_leave_one_out_and_limits():
    z, xy = _anchors()
    loo = place(z, z, xy, k=40, exclude_self=True)
    assert loo.neighbours.shape == (30, 29)
    assert all(i not in row for i, row in enumerate(loo.neighbours))
    with pytest.raises(ValueError, match="expected"):
        place(z, z, xy[:5], k=3)
    with pytest.raises(ValueError, match="dimensions"):
        place(np.ones((1, 4)), z, xy, k=3)
