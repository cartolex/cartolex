# SPDX-License-Identifier: MIT
"""Smoke test for the static term-cluster plot's subfield branch."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import pandas as pd  # noqa: E402

from cartolex.atlas.plots import plot_term_clusters  # noqa: E402


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "term": ["spin", "qubit", "laser", "fibre"],
            "umap_x": [0.1, 0.2, 0.3, 0.4],
            "umap_y": [0.1, 0.2, 0.3, 0.4],
            "cluster": [0, 0, 1, 1],
            "global_score": [0.9, 0.8, 0.7, 0.6],
        }
    )


def test_plot_term_clusters_by_subfield_writes_png(tmp_path: Path) -> None:
    subfields = [
        {
            "id": 0,
            "label": "Quantum",
            "member_cluster_refs": ["T0"],
            "centroid_umap_x": 0.15,
            "centroid_umap_y": 0.15,
        },
        {
            "id": 1,
            "label": "Optics",
            "member_cluster_refs": ["T1"],
            "centroid_umap_x": 0.35,
            "centroid_umap_y": 0.35,
        },
    ]
    out = tmp_path / "umap_terms_clusters.png"
    plot_term_clusters(
        _df(),
        top_n_terms_per_cluster=3,
        fig_path=out,
        xlim=(0, 0.5),
        ylim=(0, 0.5),
        subfields=subfields,
    )
    assert out.exists() and out.stat().st_size > 0


def test_plot_term_clusters_fallback_without_subfields(tmp_path: Path) -> None:
    out = tmp_path / "umap_terms_clusters.png"
    plot_term_clusters(
        _df(),
        top_n_terms_per_cluster=3,
        fig_path=out,
        xlim=(0, 0.5),
        ylim=(0, 0.5),
    )
    assert out.exists() and out.stat().st_size > 0


def _capture_axes(monkeypatch, captured: dict) -> None:
    """Wrap _save_figure to snapshot the axes' texts/legend before close."""
    import matplotlib.pyplot as plt

    import cartolex.atlas.plots as plots

    real_save = plots._save_figure

    def grab(fig_path):
        ax = plt.gca()
        captured["texts"] = [t.get_text() for t in ax.texts]
        captured["text_colors"] = {t.get_text(): t.get_color() for t in ax.texts}
        captured["legend"] = ax.get_legend()
        real_save(fig_path)

    monkeypatch.setattr(plots, "_save_figure", grab)


def test_fallback_has_no_cluster_labels_or_legend(tmp_path: Path, monkeypatch) -> None:
    """Without subfields the map is coloured dots only — never 'cluster N'."""
    captured: dict = {}
    _capture_axes(monkeypatch, captured)
    out = tmp_path / "x.png"
    plot_term_clusters(_df(), top_n_terms_per_cluster=3, fig_path=out, xlim=(0, 0.5), ylim=(0, 0.5))
    assert out.exists()
    assert captured["legend"] is None
    assert not any("cluster" in t.lower() for t in captured["texts"])


def test_subfield_path_colors_keywords_by_concept(tmp_path: Path, monkeypatch) -> None:
    """With the full hierarchy, each keyword text takes its concept's shade."""
    captured: dict = {}
    _capture_axes(monkeypatch, captured)
    subfields = [
        {"id": 0, "label": "Quantum", "member_cluster_refs": ["T0"], "color": "#e6194b"},
        {"id": 1, "label": "Optics", "member_cluster_refs": ["T1"]},
    ]
    hierarchy = {
        "subfields": subfields,
        "concepts": [
            {
                "id": 0,
                "label": "Spin",
                "subfield_id": 0,
                "term_indices": [0, 1],
                "color": "#112233",
            },
            {"id": 1, "label": "Light", "subfield_id": 1, "term_indices": [2, 3]},
        ],
    }
    out = tmp_path / "y.png"
    plot_term_clusters(
        _df(),
        top_n_terms_per_cluster=3,
        fig_path=out,
        xlim=(0, 0.5),
        ylim=(0, 0.5),
        subfields=subfields,
        hierarchy_doc=hierarchy,
    )
    assert out.exists()
    assert captured["text_colors"].get("spin") == "#112233"  # persisted concept colour


def test_persons_are_coloured_by_any_category_column(tmp_path: Path, caplog) -> None:
    """The person map colours persons by a category column; an absent one falls back to grey."""
    import numpy as np
    from matplotlib import pyplot as plt

    from cartolex.atlas.plots import plot_individuals_and_labs
    from cartolex.atlas.types import Embeddings, LexicalData

    meta = pd.DataFrame(
        {
            "last_name": ["A", "B", "C", "D"],
            "first_name": ["W", "X", "Y", "Z"],
            "unit": ["G1", "G1", "G2", "G2"],
            "status": ["senior", "junior", "", "senior"],
        }
    )
    data = LexicalData(X=np.eye(4), terms=list("abcd"), individuals=list("pqrs"), meta_ind=meta)
    xy = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    emb = Embeddings(Z_ind=xy, Z_terms=xy, umap_ind=xy, umap_terms=xy)
    labels: list[list[str]] = []
    real_legend = plt.legend

    def _legend(*args, **kwargs):
        labels.append(sorted(plt.gca().get_legend_handles_labels()[1]))
        return real_legend(*args, **kwargs)

    plt.legend = _legend
    try:
        for column in ("status", "absent"):
            out = tmp_path / f"persons_{column}.png"
            plot_individuals_and_labs(
                data,
                emb,
                pd.DataFrame(),
                highlight_researchers=[],
                fig_path=out,
                xlim=(-1, 2),
                ylim=(-1, 2),
                color_by=column,
            )
            assert out.exists() and out.stat().st_size > 0
    finally:
        plt.legend = real_legend
    assert labels == [["junior", "senior", "unknown"], ["Researchers"]]
    assert "No column 'absent'" in caplog.text
