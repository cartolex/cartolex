# SPDX-License-Identifier: MIT
"""How alike two people (or two organisations) are, by the project's chosen measure.

A project's ``params.json`` names the measure (``similarity``, :data:`SIMILARITIES`); it
drives Compare's headline, the nearest (``GET /api/atlas/neighbours``) and the distances'
exports (:mod:`cartolex.app.distance_exports`). Each is a similarity from 0 (nothing in
common) to 1 (the same):

- ``space`` (the default): the cosine of their vectors in the space of the themes, the
  space the map is drawn from (what the reduction keeps of their vocabulary);
- ``keywords``: the cosine of their keyword profiles (each keyword's share of their use),
  the whole vocabulary without reduction;
- ``jaccard``: the share of their keywords in common (those both use, among those either
  uses), whatever how much;
- ``themes``: the themes they share, Σ min of their top-level theme shares.

An organisation is the mean of its current members on the map (their vectors, keyword
shares or theme shares). :class:`Rows` holds items under a measure; ``cross`` compares
two sets of them, a block at a time, so that nothing holds an items × items matrix but a
block of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from cartolex.project.models import SIMILARITIES

__all__ = [
    "DEFAULT",
    "SIMILARITIES",
    "Rows",
    "measure_of",
    "org_rows",
    "people_rows",
    "query_rows",
]

#: The measure of a project that names none.
DEFAULT = "space"


def measure_of(project: Any) -> str:
    """The project's measure (``params.json``'s ``similarity``; ``space`` when unset or the
    file cannot be read)."""
    try:
        params, _ = project.read_params()
    except Exception:  # noqa: BLE001 - a params file that does not fit: the default
        return DEFAULT
    value = getattr(params, "similarity", DEFAULT)
    return value if value in SIMILARITIES else DEFAULT


@dataclass
class Rows:
    """Items under a measure: unit vectors (``space``), keyword profiles of length one
    (``keywords``), keyword sets (``jaccard``: ones, and each one's size) or top-level theme
    shares (``themes``)."""

    measure: str
    data: Any  # an ndarray (space, themes) or a CSR matrix (keywords, jaccard)
    sizes: np.ndarray | None = None  # jaccard: each one's number of keywords
    _cols: np.ndarray | None = field(default=None, repr=False)

    def __len__(self) -> int:
        return int(self.data.shape[0])

    @property
    def width(self) -> int:
        """Columns of one item (dimensions, keywords or themes)."""
        return int(self.data.shape[1]) if self.data.ndim == 2 else 0

    def take(self, rows: np.ndarray) -> Rows:
        """These items only, in the order of *rows*."""
        rows = np.asarray(rows, dtype=np.int64)
        sizes = self.sizes[rows] if self.sizes is not None else None
        return Rows(self.measure, self.data[rows], sizes)

    def cross(self, other: Rows) -> np.ndarray:
        """The similarity of each of these to each of *other*: ``(len(self), len(other))``,
        float32. *other* is small (a query, or a block of rows)."""
        a, b = self.data, other.data
        if self.measure == "space":
            return np.asarray(a @ b.T, dtype=np.float32)
        if self.measure == "themes":
            cols = self._columns()
            out = np.zeros((a.shape[0], b.shape[0]), dtype=np.float32)
            part = np.empty_like(out)
            for j in np.flatnonzero(np.asarray(b).any(axis=0)):  # a theme neither has adds 0
                np.minimum(cols[j][:, None], b[:, j][None, :], out=part)
                out += part
            return out
        dense = np.asarray(b.T.toarray(), dtype=np.float32)
        dot = np.asarray(a @ dense, dtype=np.float32)
        if self.measure == "keywords":
            return dot
        union = self.sizes[:, None] + other.sizes[None, :] - dot
        return np.divide(dot, union, out=np.zeros_like(dot), where=union > 0)

    def _columns(self) -> np.ndarray:
        """The themes' columns, each contiguous (a theme's shares of every item)."""
        if self._cols is None:
            self._cols = np.ascontiguousarray(np.asarray(self.data, dtype=np.float32).T)
        return self._cols

    def block_rows(self, n: int, budget: int) -> int:
        """Rows of a block of similarities against *n* items within *budget* bytes."""
        width = n if self.measure in ("space", "themes") else max(n, self.width)
        return max(16, min(4096, budget // max(1, 4 * width)))


def _csr(view: Any) -> Any:
    """Every person's keyword use as shares (the space's rows), as a CSR matrix."""
    from scipy import sparse

    a = view.space.arrays
    return sparse.csr_matrix(
        (np.asarray(a["row_share"]), np.asarray(a["row_cols"]), np.asarray(a["row_ptr"])),
        shape=(len(view.space.rids), len(view.space.terms)),
    )


def _from_use(measure: str, use: Any) -> Rows:
    """Keyword profiles (rows of shares) under ``keywords`` or ``jaccard``."""
    from scipy import sparse

    use = sparse.csr_matrix(use, dtype=np.float32)
    if use.nnz and not bool(np.all(np.asarray(use.data) > 0)):  # a copy: the space's is read-only
        use = use.copy()
        use.data[use.data < 0] = 0
        use.eliminate_zeros()
    if measure == "keywords":
        norms = np.sqrt(np.asarray(use.multiply(use).sum(axis=1), dtype=np.float64).ravel())
        inv = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
        return Rows(measure, sparse.csr_matrix(sparse.diags(inv.astype(np.float32)) @ use))
    ones = sparse.csr_matrix(
        (np.ones(use.nnz, dtype=np.float32), use.indices, use.indptr), shape=use.shape
    )
    return Rows(measure, ones, np.diff(use.indptr).astype(np.float32))


def _tops(view: Any) -> list[str]:
    """The top-level themes the people's shares name, in a fixed order."""
    found: set[str] = set()
    for shares in view.shares.values():
        found.update(shares)
    return sorted(found)


def _themes(view: Any, person_ids: list[str], tops: list[str]) -> np.ndarray:
    col = {n: j for j, n in enumerate(tops)}
    out = np.zeros((len(person_ids), len(tops)), dtype=np.float32)
    for i, pid in enumerate(person_ids):
        for node, share in (view.shares.get(pid) or {}).items():
            j = col.get(node)
            if j is not None:
                out[i, j] = share
    return out


def people_rows(view: Any, measure: str) -> Rows:
    """Every person of the space (its rows, in order) under *measure*; kept on *view*."""
    cache = view.__dict__.setdefault("_measure_rows", {})
    if measure not in cache:
        if measure == "space":
            rows = Rows(measure, view.space.vectors)
        elif measure == "themes":
            rows = Rows(measure, _themes(view, view.person, _tops(view)))
        else:
            rows = _from_use(measure, _csr(view))
        cache[measure] = rows
    return cache[measure]


def _members(view: Any, ids: list[str]) -> Any:
    """The organisations × people matrix of the mean over each one's members on the map."""
    from scipy import sparse

    r, c, v = [], [], []
    for i, oid in enumerate(ids):
        rows = view.member_rows(oid)
        if len(rows):
            r.extend([i] * len(rows))
            c.extend(rows.tolist())
            v.extend([1.0 / len(rows)] * len(rows))
    return sparse.csr_matrix(
        (np.asarray(v, np.float32), (np.asarray(r, np.int64), np.asarray(c, np.int64))),
        shape=(len(ids), len(view.space.rids)),
    )


def _org_rows(view: Any, measure: str, ids: list[str]) -> Rows:
    if measure == "space":
        vectors = [view.org_vector(o) for o in ids]
        d = view.space.vectors.shape[1]
        stack = [v if v is not None else np.zeros(d, np.float32) for v in vectors]
        return Rows(measure, np.vstack(stack) if stack else np.zeros((0, d), np.float32))
    mean = _members(view, ids)
    if measure == "themes":
        people = people_rows(view, "themes").data
        return Rows(measure, np.asarray(mean @ people, dtype=np.float32))
    return _from_use(measure, mean @ _csr(view))


def org_rows(view: Any, measure: str, level: str) -> tuple[list[str], Rows]:
    """The organisations of *level* with members on the map, and their rows under
    *measure*; kept on *view*."""
    cache = view.__dict__.setdefault("_measure_orgs", {})
    key = (measure, level)
    if key not in cache:
        ids, _ = view.org_vectors(level)
        cache[key] = (list(ids), _org_rows(view, measure, list(ids)))
    return cache[key]


def query_rows(view: Any, ctx: Any, measure: str, kind: str, id_: str) -> Rows | None:
    """One person or organisation under *measure* (``None``: it has no place in the space).
    A projected person is measured in the space only: their keyword use is not kept."""
    from .space_index import query_vector

    if kind == "person":
        row = view.row_of.get(id_)
        return None if row is None else people_rows(view, measure).take(np.asarray([row]))
    if kind == "organisation":
        if not len(view.member_rows(id_)):
            return None
        return _org_rows(view, measure, [id_])
    q = query_vector(view, ctx, kind, id_)
    return None if q is None else Rows("space", q.reshape(1, -1))
