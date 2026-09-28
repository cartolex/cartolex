# SPDX-License-Identifier: MIT
"""umap-learn is optional at import time; a t-SNE preview layout is an explicit opt-in."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from cartolex.atlas import reducers
from cartolex.atlas.types import Embeddings


def _emb(seed: int = 0, n_ind: int = 12, n_terms: int = 30, dim: int = 8) -> Embeddings:
    rng = np.random.default_rng(seed)
    return Embeddings(
        Z_ind=rng.normal(size=(n_ind, dim)),
        Z_terms=rng.normal(size=(n_terms, dim)),
        umap_ind=None,
        umap_terms=None,
    )


def test_reducers_import_without_umap_learn() -> None:
    """The module must import (and SVD must run) in an environment lacking umap-learn."""
    code = (
        "import sys, importlib.abc\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('umap', 'pynndescent', 'numba', 'adjustText'):\n"
        "            raise ImportError('blocked: ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "from cartolex.atlas import reducers, plots, driver\n"
        "assert not reducers.umap_available()\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_tsne_preview_is_deterministic_and_shaped() -> None:
    e = _emb()
    anchors = np.random.default_rng(1).normal(size=(4, 8))
    a_ind, a_terms = reducers.fit_tsne_preview(
        e.Z_ind, e.Z_terms, n_neighbors=5, metric="cosine", random_state=3, anchor_vectors=anchors
    )
    b_ind, b_terms = reducers.fit_tsne_preview(
        e.Z_ind, e.Z_terms, n_neighbors=5, metric="cosine", random_state=3, anchor_vectors=anchors
    )
    assert a_ind.shape == (12, 2) and a_terms.shape == (30, 2)  # anchors are not returned
    np.testing.assert_allclose(a_ind, b_ind)
    np.testing.assert_allclose(a_terms, b_terms)
    # without anchors (researcher-only reference set) it still works
    c_ind, c_terms = reducers.fit_tsne_preview(
        e.Z_ind, e.Z_terms, n_neighbors=5, metric="cosine", random_state=3
    )
    assert c_ind.shape == (12, 2) and c_terms.shape == (30, 2)


def test_tsne_preview_places_terms_near_their_researchers() -> None:
    """A term identical (in SVD space) to one researcher lands next to that researcher."""
    e = _emb(n_ind=20, n_terms=5)
    e.Z_terms[0] = e.Z_ind[7]
    ind, terms = reducers.fit_tsne_preview(
        e.Z_ind, e.Z_terms, n_neighbors=3, metric="cosine", random_state=0
    )
    d = np.linalg.norm(ind - terms[0], axis=1)
    extent = np.ptp(ind, axis=0).max()
    assert d[7] < 0.1 * extent


def test_compute_umap_falls_back_to_tsne_only_when_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(reducers, "umap_available", lambda: False)
    monkeypatch.setattr(
        reducers,
        "_umap_module",
        lambda: (_ for _ in ()).throw(ImportError("umap-learn is not installed")),
    )
    e = _emb()
    kw = dict(n_neighbors=5, min_dist=0.1, n_components=2, metric="cosine", random_state=3)
    with pytest.raises(ImportError):
        reducers.compute_umap(_emb(), layout="joint", **kw)
    out = reducers.compute_umap(e, layout="joint", fallback="tsne", **kw)
    assert out.umap_ind.shape == (12, 2) and out.umap_terms.shape == (30, 2)


def test_plots_adjust_text_fallback_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "adjustText", None)  # import raises ImportError
    from cartolex.atlas import plots

    reloaded = importlib.reload(plots)
    try:
        assert reloaded.adjust_text([]) is None
    finally:
        monkeypatch.undo()
        importlib.reload(plots)


def test_tsne_anchored_is_a_first_class_layout(tmp_path: Path) -> None:
    """layout="tsne_anchored" works with or without umap-learn; the terms are placed on it."""
    from cartolex.atlas.placement import place

    e = _emb(n_ind=25, n_terms=60)
    anchors = np.random.default_rng(2).normal(size=(6, 8))
    out = reducers.compute_umap(
        e,
        n_neighbors=5,
        min_dist=0.1,
        n_components=2,
        metric="cosine",
        random_state=7,
        layout="tsne_anchored",
        anchor_vectors=anchors,
    )
    assert out.umap_ind.shape == (25, 2) and out.umap_terms.shape == (60, 2)
    assert np.array_equal(out.umap_terms, place(e.Z_terms, e.Z_ind, out.umap_ind).xy)
    # a copy of researcher 3 lands next to researcher 3
    xy = place(e.Z_ind[[3]], e.Z_ind, out.umap_ind).xy
    d = np.linalg.norm(out.umap_ind - xy[0], axis=1)
    assert d[3] < 0.2 * np.ptp(out.umap_ind, axis=0).max()
