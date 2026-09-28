# SPDX-License-Identifier: MIT
"""A map drawn from the theme tree: themes placed first, people inside their heaviest theme.

The layout needs no optimisation over the people, so its cost grows with the
people times the dimensions of the space, and a new edition moves only the
people who changed and the themes whose members changed.

1. **Each person's path.** A person goes to their heaviest top-level theme (by
   the usage of its keywords, as the applied themes count it), then to the
   heaviest of its children, down to the finest level. A person who uses no
   keyword of the tree goes to the node whose people are nearest in the space.
2. **Themes as discs.** The top-level themes are discs of area proportional to
   their people. Their centres come from a classical multidimensional scaling
   of the cosine distances between the themes' mean vectors in the space; the
   discs are then pushed apart until they no longer overlap. Each theme's
   children are placed the same way inside it, down to the finest level.
3. **People inside their disc.** The people of a finest node are spread by the
   first two principal axes of their vectors, scaled so that two root mean
   square radii fill the disc (the few beyond sit on its rim).

Everything is deterministic: the scaling's eigenvectors have a fixed sign, the
discs move in a fixed order, and ties go to the lower index.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import sparse
from sklearn.preprocessing import normalize

__all__ = ["FILL", "people_paths", "tree_layout"]

#: The share of a parent disc its children's discs cover.
FILL = 0.62
#: Rounds of the relaxation that pushes overlapping discs apart.
_ROUNDS = 300
#: The gap kept between two discs, as a share of the smaller radius.
_GAP = 0.04


def people_paths(tree: Any, usage: sparse.spmatrix, Z: np.ndarray) -> np.ndarray:
    """Each person's node at every level (``n × depth`` indices into ``tree.nodes``).

    *usage* is the people × keywords usage (the keyword rows of the tree), *Z*
    the people's vectors in the space.
    """
    usage = sparse.csr_matrix(usage, dtype=np.float64)
    n = usage.shape[0]
    columns = np.arange(usage.shape[1])
    paths = np.full((n, tree.depth), -1, dtype=np.int64)
    parent_of = np.asarray(tree.parent_index)
    for lv in range(1, tree.depth + 1):
        nodes = tree.at_level(lv)
        weights = (usage @ tree.membership(lv, columns)).tocoo()  # people × nodes, sparse
        row, col, val = weights.row, weights.col, weights.data
        keep = val > 0
        if lv > 1:  # only the children of the node chosen above
            keep &= parent_of[nodes[col]] == paths[row, lv - 2]
        row, col, val = row[keep], col[keep], val[keep]
        order = np.lexsort((col, -val, row))  # per person: heaviest, then first in tree order
        row, col = row[order], col[order]
        first = np.r_[True, row[1:] != row[:-1]] if len(row) else np.zeros(0, dtype=bool)
        paths[row[first], lv - 1] = nodes[col[first]]
    # People without a placed keyword: the node of the nearest centroid, level by level.
    Zn = normalize(np.asarray(Z, dtype=np.float64))
    for lv in range(1, tree.depth + 1):
        missing = np.flatnonzero(paths[:, lv - 1] < 0)
        if not len(missing):
            continue
        nodes = tree.at_level(lv)
        placed = paths[:, lv - 1] >= 0
        centroids, keep = [], []
        for j, node in enumerate(nodes.tolist()):
            members = placed & (paths[:, lv - 1] == node)
            if members.any():
                centroids.append(Zn[members].mean(axis=0))
                keep.append(j)
        if not keep:
            paths[missing, lv - 1] = nodes[0] if lv == 1 else -1
            continue
        C = normalize(np.vstack(centroids))
        sims = Zn[missing] @ C.T
        if lv > 1:
            parents = paths[missing, lv - 2]
            allowed = parent_of[nodes[keep]][None, :] == parents[:, None]
            sims = np.where(allowed, sims, -np.inf)
        best = np.argmax(sims, axis=1)
        ok = np.isfinite(sims[np.arange(len(missing)), best])
        paths[missing[ok], lv - 1] = nodes[np.asarray(keep)[best[ok]]]
        # a parent none of whose children has people: the first child
        for i in missing[~ok].tolist():
            kids = [c for c in tree.children[paths[i, lv - 2]]]
            paths[i, lv - 1] = kids[0] if kids else paths[i, lv - 2]
    return paths


def _mds(centroids: np.ndarray) -> np.ndarray:
    """Classical scaling of cosine distances to two dimensions (fixed signs)."""
    k = len(centroids)
    if k == 1:
        return np.zeros((1, 2))
    C = normalize(centroids)
    D2 = np.clip(2.0 - 2.0 * (C @ C.T), 0.0, None)  # squared chord distance
    J = np.eye(k) - 1.0 / k
    B = -0.5 * J @ D2 @ J
    vals, vecs = np.linalg.eigh(B)
    order = np.argsort(vals)[::-1][:2]
    out = vecs[:, order] * np.sqrt(np.clip(vals[order], 0.0, None))
    if out.shape[1] < 2:
        out = np.hstack([out, np.zeros((k, 2 - out.shape[1]))])
    for j in range(2):  # the sign of each axis: its largest entry positive
        col = out[:, j]
        if col[np.argmax(np.abs(col))] < 0:
            out[:, j] = -col
    return out


def _pack(guess: np.ndarray, radii: np.ndarray, outer: float) -> np.ndarray:
    """Disc centres near *guess*, not overlapping, inside a disc of radius *outer* at 0.

    Every round, each overlapping pair is pushed apart by half its overlap (all
    pairs at once), then the discs that stick out are pulled back inside.
    """
    k = len(radii)
    if k == 1:
        return np.zeros((1, 2))
    spread = float(np.sqrt((guess**2).sum(axis=1)).max())
    if spread > 0:
        pos = guess / spread * max(outer - float(radii.max()), 0.0) * 0.8
    else:
        angles = 2 * np.pi * np.arange(k) / k
        pos = np.c_[np.cos(angles), np.sin(angles)] * (outer * 0.5)
    need = radii[:, None] + radii[None, :] + _GAP * np.minimum(radii[:, None], radii[None, :])
    np.fill_diagonal(need, 0.0)
    ring = np.linspace(0.0, 2 * np.pi, k, endpoint=False)
    unit = np.c_[np.cos(ring), np.sin(ring)]
    for _ in range(_ROUNDS):
        delta = pos[None, :, :] - pos[:, None, :]  # i → j
        dist = np.hypot(delta[..., 0], delta[..., 1])
        overlap = np.clip(need - dist, 0.0, None)
        np.fill_diagonal(overlap, 0.0)
        # coincident centres separate along a fixed direction per pair
        direction = np.where(
            dist[..., None] > 1e-12,
            delta / np.maximum(dist, 1e-12)[..., None],
            (unit[None, :, :] - unit[:, None, :]),
        )
        push = (overlap / 2.0)[..., None] * direction
        pos = pos - push.sum(axis=1)
        norms = np.hypot(pos[:, 0], pos[:, 1])
        outside = norms + radii > outer
        if outside.any():
            scale = np.where(
                outside, np.maximum(outer - radii, 0.0) / np.maximum(norms, 1e-12), 1.0
            )
            pos = pos * scale[:, None]
        if not overlap.any() and not outside.any():
            break
    return pos


def _spread(Zn: np.ndarray) -> np.ndarray:
    """Two coordinates per row from its first two principal axes, in a unit disc."""
    n = len(Zn)
    if n == 1:
        return np.zeros((1, 2))
    centred = Zn - Zn.mean(axis=0)
    _, s, vt = np.linalg.svd(centred, full_matrices=False)
    axes = vt[:2]
    for j in range(len(axes)):
        if axes[j][np.argmax(np.abs(axes[j]))] < 0:
            axes[j] = -axes[j]
    xy = centred @ axes.T
    if xy.shape[1] < 2:
        xy = np.hstack([xy, np.zeros((n, 2 - xy.shape[1]))])
    rho = np.hypot(xy[:, 0], xy[:, 1])
    scale = float(np.sqrt(np.mean(rho**2)))
    if scale <= 0:
        return np.zeros((n, 2))
    # Linear inside two root mean square radii; the few beyond sit on the rim.
    xy = xy / (2.0 * scale)
    rho = rho / (2.0 * scale)
    far = rho > 1.0
    xy[far] = xy[far] / rho[far][:, None]
    return xy


def _pull(Zn: np.ndarray, groups: list[np.ndarray], centres: np.ndarray, own: int) -> np.ndarray:
    """Per member of ``groups[own]``: a vector toward the sibling discs it resembles.

    The direction is the mean of the unit vectors toward the other discs,
    weighted by the softmax of the member's cosine to each disc's mean vector;
    the length (0 to 1) grows as the member resembles another disc as much as
    its own.
    """
    members = groups[own]
    C = normalize(np.vstack([Zn[g].mean(axis=0) for g in groups]))
    sims = Zn[members] @ C.T
    others = [j for j in range(len(groups)) if j != own]
    delta = centres[others] - centres[own]
    dist = np.hypot(delta[:, 0], delta[:, 1])
    units = delta / np.maximum(dist, 1e-12)[:, None]
    logits = _SHARP * sims[:, others]
    logits -= logits.max(axis=1, keepdims=True)
    w = np.exp(logits)
    w /= w.sum(axis=1, keepdims=True)
    strength = 1.0 / (1.0 + np.exp(-_SHARP * (sims[:, others].max(axis=1) - sims[:, own])))
    return (w @ units) * strength[:, None]


#: How sharply a person's cosines to the discs decide where they lean.
_SHARP = 8.0
#: The share of a person's place in their disc given by where they lean (the rest by their spread).
_LEAN = 0.4


def tree_layout(tree: Any, paths: np.ndarray, Z: np.ndarray) -> np.ndarray:
    """The map positions (``n × 2``) of the people with *paths* (from :func:`people_paths`).

    Inside their finest disc, people are spread by their own principal axes and
    lean toward the sibling discs (at every level) whose people they resemble,
    so that a person between two themes sits on the side facing the other.
    """
    Zn = normalize(np.asarray(Z, dtype=np.float64))
    n = len(Zn)
    xy = np.zeros((n, 2))
    lean = np.zeros((n, 2))
    splits = np.zeros(n)
    depth = paths.shape[1]

    def place(members: np.ndarray, level: int, centre: np.ndarray, radius: float) -> None:
        if level > depth or not len(members):
            if not len(members):
                return
            local = (1.0 - _LEAN) * _spread(Zn[members])
            local += _LEAN * lean[members] / np.maximum(splits[members], 1.0)[:, None]
            rho = np.hypot(local[:, 0], local[:, 1])
            far = rho > 1.0
            local[far] = local[far] / rho[far][:, None]
            xy[members] = centre + local * radius
            return
        nodes = paths[members, level - 1]
        kinds = np.unique(nodes)  # sorted: tree order of indices
        groups = [members[nodes == k] for k in kinds]
        mass = np.array([len(g) for g in groups], dtype=np.float64)
        if len(groups) == 1:
            place(groups[0], level + 1, centre, radius)
            return
        radii = radius * np.sqrt(FILL * mass / mass.sum())
        guess = _mds(np.vstack([Zn[g].mean(axis=0) for g in groups]))
        centres = _pack(guess, radii, radius)
        for j, g in enumerate(groups):
            lean[g] += _pull(Zn, groups, centres, j)
            splits[g] += 1.0
        for g, c, r in zip(groups, centres, radii, strict=True):
            place(g, level + 1, centre + c, float(r))

    place(np.arange(n), 1, np.zeros(2), float(np.sqrt(n)))
    return xy
