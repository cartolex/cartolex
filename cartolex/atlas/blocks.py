# SPDX-License-Identifier: MIT
"""Dense arithmetic on a large matrix, one block of rows at a time.

A people × keywords matrix is kept sparse; a step that needs dense arithmetic
(a logarithm of the non-zero cells, a row normalisation, a product with a
fitted basis) takes it a block of rows at a time, each block no larger than
:data:`BLOCK_CELLS` cells. Memory then stays proportional to the block, never
to the whole matrix.

The block size is fixed, never taken from the memory available, so a result
does not depend on the machine. A matrix of at most :data:`BLOCK_CELLS` cells
(every demo world) is one block: the arithmetic is exactly that of the whole
dense matrix, bit for bit. On a larger matrix, steps that work row by row
(logarithms, scaling, normalisation, sums along a row) give the same bits as
the whole matrix would; a matrix product can differ in the last bits, because
the numeric library sums in another order for a block of other dimensions.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
from scipy import sparse

__all__ = ["BLOCK_CELLS", "as_csr", "dense_rows", "one_block", "row_blocks"]

#: The cells of one dense block (2²⁴: 128 MB of float64).
BLOCK_CELLS = 1 << 24


def row_blocks(n_rows: int, n_cols: int, cells: int | None = None) -> Iterator[slice]:
    """Slices of consecutive rows, each block holding at most *cells* cells (at least one row).

    *cells* defaults to :data:`BLOCK_CELLS`, read when called.
    """
    step = max(1, int(BLOCK_CELLS if cells is None else cells) // max(1, int(n_cols)))
    for start in range(0, int(n_rows), step):
        yield slice(start, min(int(n_rows), start + step))


def one_block(n_rows: int, n_cols: int) -> bool:
    """Whether a matrix of this shape is a single block (compared with :data:`BLOCK_CELLS` now)."""
    return int(n_rows) * int(n_cols) <= BLOCK_CELLS


def dense_rows(X: np.ndarray | sparse.spmatrix, rows: slice) -> np.ndarray:
    """Rows *rows* of *X* as a C-contiguous float64 array (a copy)."""
    if sparse.issparse(X):
        return np.ascontiguousarray(X[rows].toarray(), dtype=np.float64)
    return np.array(X[rows], dtype=np.float64, order="C")


def as_csr(X: np.ndarray | sparse.spmatrix) -> sparse.csr_matrix:
    """*X* as a float64 CSR matrix with sorted column indices and no duplicate cells.

    A dense matrix keeps only its non-zero cells. Raises ``ValueError`` when *X*
    is not two-dimensional.
    """
    if sparse.issparse(X):
        M = sparse.csr_matrix(X, dtype=np.float64, copy=True)
    else:
        arr = np.asarray(X, dtype=np.float64)
        if arr.ndim != 2:
            raise ValueError(f"expected a two-dimensional matrix, got shape {arr.shape}")
        M = sparse.csr_matrix(arr)
    M.sum_duplicates()  # sorts the indices of every row too
    return M
