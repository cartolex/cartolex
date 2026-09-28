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


def test_neighbours_together_give_their_weighted_mean():
    z, xy = _anchors()
    placed = place(np.array([[5.0, 0.2, 0.1]]), z, xy, k=5)
    assert placed.in_group.all()
    plain = np.einsum("ik,ikj->ij", placed.neighbour_weights, xy[placed.neighbours])
    assert np.allclose(placed.xy, plain)


def test_a_point_between_two_places_goes_to_the_heavier_one():
    # Anchors in two tight places on the map; the vector sits between them in the space,
    # a little nearer to the three anchors of place A than to the five of place B.
    a = np.array([[1.0, 0.0], [0.98, 0.02], [0.99, -0.01]])
    b = np.array([[0.0, 1.0], [0.02, 0.98], [-0.01, 0.99], [0.01, 1.01], [0.0, 0.97]])
    vectors_a = np.hstack([a, np.zeros((3, 1))])
    vectors_b = np.hstack([b, np.zeros((5, 1))])
    anchors = np.vstack([vectors_a, vectors_b])
    xy = np.vstack([np.full((3, 2), (0.0, 0.0)), np.full((5, 2), (10.0, 0.0))])
    xy = xy + np.random.default_rng(0).normal(scale=0.05, size=xy.shape)
    placed = place(np.array([[0.6, 0.55, 0.0]]), anchors, xy, k=8)
    x = placed.xy[0, 0]
    assert x < 1 or x > 9  # in one place, never in the empty space between
    group = placed.neighbours[0][placed.in_group[0]]
    other = placed.neighbours[0][~placed.in_group[0]]
    heavier = placed.neighbour_weights[0][placed.in_group[0]].sum()
    assert heavier >= placed.neighbour_weights[0][~placed.in_group[0]].sum()
    assert len(set(group) & set(other)) == 0 and len(group) + len(other) == 8
    assert np.allclose(placed.weights.sum(axis=1), 1.0)
    assert np.all(placed.weights[0][~placed.in_group[0]] == 0)


def test_on_equal_weights_the_group_of_the_nearest_neighbour_wins():
    anchors = np.array([[1.0, 0.0], [0.0, 1.0]])
    xy = np.array([[0.0, 0.0], [10.0, 0.0]])
    placed = place(np.array([[1.0, 1.0]]), anchors, xy, k=2)
    assert placed.neighbours[0].tolist() == [0, 1]  # equal distances: by index
    assert placed.xy.tolist() == [[0.0, 0.0]]


def test_the_map_radius_and_anchors():
    from cartolex.atlas.placement import LINK_RADIUS, K, MapAnchors, map_radius

    assert (K, LINK_RADIUS) == (8, 0.25)
    assert map_radius(np.array([[1.0, 0.0], [-1.0, 0.0]])) == 1.0
    z, xy = _anchors()
    anchors = MapAnchors(z, xy)
    assert anchors.place(np.zeros((0, 3))).shape == (0, 2)
    assert np.array_equal(anchors.place(z[:3]), place(z[:3], z, xy).xy)
