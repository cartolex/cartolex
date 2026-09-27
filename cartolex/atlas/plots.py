# SPDX-License-Identifier: MIT
from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .types import Embeddings, LexicalData, col_sums

logger = logging.getLogger(__name__)


class _LazyModule:
    """A module imported on first use.

    Importing matplotlib writes its configuration and font-cache folders, so
    this module imports it only when a figure is drawn: importing the engine
    creates no file.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        return getattr(importlib.import_module(self._name), attr)


matplotlib = _LazyModule("matplotlib")
plt = _LazyModule("matplotlib.pyplot")


def Ellipse(*args: Any, **kwargs: Any) -> Any:  # noqa: N802 - stands in for the matplotlib class
    """``matplotlib.patches.Ellipse``, imported on first use."""
    from matplotlib.patches import Ellipse as _Ellipse

    return _Ellipse(*args, **kwargs)


def adjust_text(*args: Any, **kwargs: Any) -> Any:
    """The optional label de-overlapper; without it labels stay where matplotlib put them."""
    try:
        from adjustText import adjust_text as _adjust_text
    except ImportError:
        return None
    return _adjust_text(*args, **kwargs)


def _save_figure(fig_path, dpi: int = 600, extra_formats: tuple[str, ...] = ("pdf", "svg")) -> None:
    path = Path(fig_path)
    plt.savefig(path, dpi=dpi)
    for ext in extra_formats:
        plt.savefig(path.with_suffix(f".{ext}"))


def lab_covariance(xs, ys) -> dict[str, float] | None:
    """2x2 covariance of a lab's member UMAP positions, or None if undefined.

    Returns ``{cov_xx, cov_yy, cov_xy, sx, sy, rho}`` when there are >=3 points
    forming a rank-2 covariance; ``None`` for fewer points or a rank-deficient
    (collinear) set — callers then draw no ellipse.
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    if xs.size < 3:
        return None
    cov = np.cov(np.vstack([xs, ys]))
    if np.linalg.matrix_rank(cov) < 2:
        return None
    cxx, cyy, cxy = float(cov[0, 0]), float(cov[1, 1]), float(cov[0, 1])
    sx, sy = float(np.sqrt(cxx)), float(np.sqrt(cyy))
    rho = float(cxy / (sx * sy)) if sx > 0 and sy > 0 else 0.0
    return {"cov_xx": cxx, "cov_yy": cyy, "cov_xy": cxy, "sx": sx, "sy": sy, "rho": rho}


def aggregate_labs(
    meta_ind: pd.DataFrame,
    emb: Embeddings,
    *,
    min_researchers_per_lab: int,
) -> pd.DataFrame:
    """Aggregate individual UMAP positions into per-group centroids.

    One row per value of the group column (``unit``) with at least
    *min_researchers_per_lab* persons, sorted by group; the group value is also
    its label on the maps.
    """
    if emb.umap_ind is None:
        raise ValueError("UMAP embeddings not computed for individuals.")

    df = meta_ind.copy()
    df["umap_x"] = emb.umap_ind[:, 0]
    df["umap_y"] = emb.umap_ind[:, 1]

    rows = []
    for unit, grp in df.groupby("unit"):
        if len(grp) < min_researchers_per_lab:
            continue
        row = {
            "unit": unit,
            "umap_x": grp["umap_x"].mean(),
            "umap_y": grp["umap_y"].mean(),
            "size": len(grp),
        }
        cov = lab_covariance(grp["umap_x"].to_numpy(), grp["umap_y"].to_numpy())
        if cov is not None:
            row.update(cov)
        rows.append(row)

    df_labs = pd.DataFrame(rows)
    df_labs = df_labs.sort_values("unit")
    return df_labs


def plot_individuals_and_labs(
    data: LexicalData,
    emb: Embeddings,
    df_labs: pd.DataFrame,
    *,
    highlight_researchers: list[tuple[str, str]],
    fig_path,
    xlim,
    ylim,
    color_by: str | None = None,
) -> None:
    """Plot researchers and laboratory centroids on the UMAP map.

    *color_by* names a category column of the person table (``data.meta_ind``,
    e.g. a rank or a status column of the corpus index): persons are coloured
    by its values, blanks grouped as "unknown". Without it (or when the column
    is absent), every person is grey.
    """
    df_ind = data.meta_ind.copy()
    df_ind["umap_x"] = emb.umap_ind[:, 0]
    df_ind["umap_y"] = emb.umap_ind[:, 1]

    plt.figure(figsize=(10, 8))

    if color_by and color_by in df_ind.columns:
        values = df_ind[color_by].fillna("").astype(str).str.strip().replace("", "unknown")
        categories = sorted(values.unique())
        cmap = matplotlib.colormaps["tab20"].resampled(max(len(categories), 1))
        for k, category in enumerate(categories):
            grp = df_ind[values == category]
            plt.scatter(
                grp["umap_x"],
                grp["umap_y"],
                s=12,
                alpha=0.4,
                color="grey" if category == "unknown" else cmap(k),
                label=category,
            )
    else:
        if color_by:
            logger.warning("No column %r in the person table; persons drawn in grey.", color_by)
        plt.scatter(
            df_ind["umap_x"],
            df_ind["umap_y"],
            s=12,
            alpha=0.25,
            color="grey",
            label="Researchers",
        )

    if not df_labs.empty:
        for _, row in df_labs.iterrows():
            plt.text(
                row["umap_x"],
                row["umap_y"],
                str(row["unit"]),
                fontsize=9,
                ha="center",
                va="center",
            )

    for last, first in highlight_researchers:
        mask = (df_ind["last_name"].astype(str) == last) & (
            df_ind["first_name"].astype(str) == first
        )
        sub = df_ind[mask]
        for _, row in sub.iterrows():
            plt.scatter(
                row["umap_x"],
                row["umap_y"],
                s=60,
                marker="D",
                color="red",
            )
            plt.text(
                row["umap_x"],
                row["umap_y"],
                f"{row['first_name']} {row['last_name']}",
                fontsize=9,
                ha="left",
                va="bottom",
                color="red",
            )

    plt.xlabel("UMAP-1")
    plt.ylabel("UMAP-2")
    plt.xlim(xlim)
    plt.ylim(ylim)
    plt.title("UMAP map of researchers and labs")
    plt.legend(loc="best", fontsize=9)
    plt.gca().set_aspect("equal", adjustable="box")  # same aspect/orientation on every UMAP
    plt.tight_layout()
    _save_figure(fig_path)
    plt.close()
    logger.info("Saved %s", fig_path)


def _concept_colors_for_rows(df_terms: pd.DataFrame, hierarchy_doc: dict | None) -> dict[int, str]:
    """Positional row → persisted concept colour ({} when the hierarchy lacks concepts).

    Valid only when *df_terms* preserves the clustered-CSV row order (row position
    = SVD term index) — true in :func:`run_lexical_plots`, which passes the file
    unfiltered.
    """
    from cartolex.lexicon.subfields import concept_term_index

    term_to_cid, info = concept_term_index(hierarchy_doc)
    if not info:
        return {}
    n = len(df_terms)
    return {
        ti: info[cid]["color"] for ti, cid in term_to_cid.items() if 0 <= ti < n and cid in info
    }


def plot_term_clusters(
    df_terms: pd.DataFrame,
    *,
    top_n_terms_per_cluster: int,
    fig_path,
    xlim,
    ylim,
    subfields: list[dict] | None = None,
    hierarchy_doc: dict | None = None,
) -> None:
    """UMAP map of keywords.

    When *subfields* (the kept applied-subfield records) is given, keywords are
    coloured by their CONCEPT (persisted shade from *hierarchy_doc*, subfield hue
    fallback), subfield names are written in bold at their centroids and the
    legend lists subfield names in their own colour. Without subfields the map
    falls back to unlabelled cluster-coloured dots — never "cluster N" labels.
    """
    if subfields:
        _plot_term_clusters_by_subfield(
            df_terms,
            fig_path=fig_path,
            xlim=xlim,
            ylim=ylim,
            subfields=subfields,
            hierarchy_doc=hierarchy_doc,
        )
        return

    plt.figure(figsize=(10, 8))

    clusters = sorted(df_terms["cluster"].unique())
    non_noise = [c for c in clusters if c != -1]
    n_real_clusters = len(non_noise) or 1
    cmap = matplotlib.colormaps["tab20"].resampled(n_real_clusters)

    for idx, cid in enumerate(clusters):
        sub = df_terms[df_terms["cluster"] == cid]
        if sub.empty:
            continue
        noise = cid == -1
        plt.scatter(
            sub["umap_x"],
            sub["umap_y"],
            s=18,
            alpha=0.3 if noise else 0.7,
            color="lightgrey" if noise else cmap(idx % n_real_clusters),
            marker=".",
        )

    plt.xlim(xlim)
    plt.ylim(ylim)
    plt.xlabel("UMAP-1")
    plt.ylabel("UMAP-2")
    plt.title("UMAP map of keywords (hierarchy not applied)")

    plt.gca().set_aspect("equal", adjustable="box")  # same aspect/orientation on every UMAP
    plt.tight_layout()
    _save_figure(fig_path)
    plt.close()
    logger.info("Saved %s", fig_path)


def _plot_term_clusters_by_subfield(
    df_terms: pd.DataFrame,
    *,
    fig_path,
    xlim,
    ylim,
    subfields: list[dict],
    hierarchy_doc: dict | None = None,
) -> None:
    """Term map coloured by concept / labelled by subfield (see plot_term_clusters)."""
    from matplotlib.lines import Line2D

    from cartolex.lexicon.subfields import subfield_color, term_cluster_subfield_map

    cl2sf = term_cluster_subfield_map(subfields)
    sf_color = {
        # Persisted colour wins (e.g. the grey "Other" bag); palette by id fallback.
        int(sf["id"]): sf.get("color") or subfield_color(int(sf["id"]))
        for sf in subfields
        if "id" in sf
    }
    sf_label = {int(sf["id"]): sf.get("label", f"#{sf['id']}") for sf in subfields if "id" in sf}
    concept_color = _concept_colors_for_rows(df_terms, hierarchy_doc)

    plt.figure(figsize=(10, 8))
    texts = []

    # Representative keywords (top per term cluster), coloured by CONCEPT
    # (subfield hue when the hierarchy carries no concepts).
    for cid in sorted(df_terms["cluster"].unique()):
        if cid == -1:
            continue
        sub = df_terms[df_terms["cluster"] == cid]
        if sub.empty:
            continue
        sid = cl2sf.get(int(cid))
        group_color = sf_color.get(sid, "lightgrey") if sid is not None else "lightgrey"
        for pos, row in sub.sort_values("global_score", ascending=False).head(3).iterrows():
            texts.append(
                plt.text(
                    row["umap_x"],
                    row["umap_y"],
                    row["term"],
                    fontsize=7,
                    ha="center",
                    va="center",
                    color=concept_color.get(int(pos), group_color),
                )
            )

    # Bold subfield names at their centroids.
    for sf in subfields:
        cx, cy = sf.get("centroid_umap_x"), sf.get("centroid_umap_y")
        if cx is None or cy is None or "id" not in sf:
            continue
        texts.append(
            plt.text(
                cx,
                cy,
                sf.get("label", "?"),
                fontsize=11,
                fontweight="bold",
                ha="center",
                va="center",
                color=sf_color.get(int(sf["id"]), "black"),
            )
        )

    plt.xlim(xlim)
    plt.ylim(ylim)
    if texts:
        adjust_text(texts)

    # Legend: subfield names rendered in their own colour.
    handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            color="w",
            markerfacecolor=sf_color[sid],
            markersize=8,
            label=sf_label[sid],
        )
        for sid in sf_label
    ]
    if handles:
        leg = plt.legend(handles=handles, fontsize=8, loc="best")
        for text, sid in zip(leg.get_texts(), sf_label, strict=False):
            text.set_color(sf_color[sid])

    plt.xlabel("UMAP-1")
    plt.ylabel("UMAP-2")
    plt.title("UMAP map of keywords (subfields)")
    plt.gca().set_aspect("equal", adjustable="box")  # same aspect/orientation on every UMAP
    plt.tight_layout()
    _save_figure(fig_path)
    plt.close()
    logger.info("Saved %s", fig_path)


def plot_superposed_map(
    data: LexicalData,
    emb: Embeddings,
    df_labs: pd.DataFrame,
    df_terms: pd.DataFrame,
    *,
    highlight_researchers: list[tuple[str, str]],
    fig_path,
    xlim,
    ylim,
    hierarchy_doc: dict | None = None,
) -> None:
    """Plot researchers, laboratories and terms superposed on a single UMAP map.

    Term dots take their CONCEPT's persisted shade when *hierarchy_doc* carries
    concepts; otherwise the unlabelled cluster colours are used.
    """
    df_ind = data.meta_ind.copy()
    df_ind["umap_x"] = emb.umap_ind[:, 0]
    df_ind["umap_y"] = emb.umap_ind[:, 1]

    plt.figure(figsize=(11, 9))

    concept_color = _concept_colors_for_rows(df_terms, hierarchy_doc)
    if concept_color:
        plt.scatter(
            df_terms["umap_x"],
            df_terms["umap_y"],
            s=12,
            alpha=0.25,
            color=[concept_color.get(i, "#cbd5e1") for i in range(len(df_terms))],
        )
    else:
        clusters = sorted(df_terms["cluster"].unique())
        real_clusters = [c for c in clusters if c != -1]
        n_real = len(real_clusters) or 1
        cmap = matplotlib.colormaps["tab20"].resampled(n_real)

        for k, cid in enumerate(clusters):
            sub = df_terms[df_terms["cluster"] == cid]
            if sub.empty:
                continue
            if cid == -1:
                color = "lightgrey"
                alpha = 0.08
            else:
                color = cmap(k % n_real)
                alpha = 0.25
            plt.scatter(
                sub["umap_x"],
                sub["umap_y"],
                s=12,
                alpha=alpha,
                color=color,
            )

    if not df_labs.empty:
        for _, lab in df_labs.iterrows():
            plt.text(
                lab["umap_x"],
                lab["umap_y"],
                str(lab["unit"]),
                fontsize=9,
                ha="center",
                va="center",
                fontweight="bold",
                color="black",
            )

    for last, first in highlight_researchers:
        mask = (df_ind["last_name"].astype(str) == last) & (
            df_ind["first_name"].astype(str) == first
        )
        sub = df_ind[mask]
        for _, row in sub.iterrows():
            plt.scatter(
                row["umap_x"],
                row["umap_y"],
                s=60,
                marker="D",
                color="red",
            )
            plt.text(
                row["umap_x"],
                row["umap_y"],
                f"{row['first_name']} {row['last_name']}",
                fontsize=9,
                ha="left",
                va="bottom",
                color="red",
            )

    plt.xlabel("UMAP-1")
    plt.ylabel("UMAP-2")
    plt.xlim(xlim)
    plt.ylim(ylim)
    plt.title("Global UMAP: keywords + labs")
    plt.gca().set_aspect("equal", adjustable="box")  # same aspect/orientation on every UMAP
    plt.tight_layout()
    _save_figure(fig_path)
    plt.close()
    logger.info("Saved %s", fig_path)


def _file_key(value: str) -> str:
    """*value* with every character that is not a letter, a digit, ``-`` or ``_`` as ``_``."""
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(value))


def plot_lab_panels(
    data: LexicalData,
    emb: Embeddings,
    df_terms: pd.DataFrame,
    *,
    groups: Collection[str],
    n_main_clusters: int,
    n_top_terms_per_main_cluster: int,
    n_top_terms_per_lab: int,
    lab_xy_padding: float,
    panel_png: Callable[[str], Path],
) -> None:
    """Render one UMAP panel for each group of *groups* (small multiples).

    *groups* are values of the group column (``unit``); a value no person has
    draws nothing. *panel_png* gives the file of a group's panel from its value,
    with any character other than a letter, a digit, ``-`` or ``_`` replaced by
    ``_`` (a run's ``ctx.paths.group_panel_png``).
    """
    wanted = {str(g) for g in groups}
    if not wanted:
        return
    df_ind = data.meta_ind.copy()
    df_ind["umap_x"] = emb.umap_ind[:, 0]
    df_ind["umap_y"] = emb.umap_ind[:, 1]

    terms = data.terms
    df_terms_idx = df_terms.set_index("term")

    all_x = df_terms["umap_x"].values
    all_y = df_terms["umap_y"].values
    x_min, x_max = all_x.min(), all_x.max()
    y_min, y_max = all_y.min(), all_y.max()
    dx = x_max - x_min
    dy = y_max - y_min
    x_pad = lab_xy_padding * dx
    y_pad = lab_xy_padding * dy
    xlim = (x_min - x_pad, x_max + x_pad)
    ylim = (y_min - y_pad, y_max + y_pad)

    valid = df_terms[df_terms["cluster"] != -1]
    if valid.empty:
        bg_df = pd.DataFrame(columns=df_terms.columns)
    else:
        cluster_stats = (
            valid.groupby("cluster")["global_score"]
            .sum()
            .reset_index()
            .sort_values("global_score", ascending=False)
        )
        main_clusters = cluster_stats["cluster"].head(n_main_clusters).tolist()

        bg_list = []
        for cid in main_clusters:
            grp = valid[valid["cluster"] == cid]
            top_bg = grp.sort_values("global_score", ascending=False).head(
                n_top_terms_per_main_cluster
            )
            bg_list.append(top_bg)
        bg_df = (
            pd.concat(bg_list, ignore_index=True)
            if bg_list
            else pd.DataFrame(columns=df_terms.columns)
        )

    units_list = df_ind["unit"].astype(str).replace("nan", np.nan).dropna().unique()
    units_list = sorted(u for u in units_list if u in wanted)

    for unit in units_list:
        sub = df_ind[df_ind["unit"].astype(str) == unit]
        if sub.empty:
            continue

        lab_row_indices = sub.index.to_numpy()
        lab_term_scores = col_sums(data.X[lab_row_indices, :])

        order = np.argsort(-lab_term_scores)
        top_terms = []
        for j in order:
            if lab_term_scores[j] <= 0:
                break
            t = terms[j]
            if t in df_terms_idx.index:
                top_terms.append(t)
            if len(top_terms) >= n_top_terms_per_lab:
                break

        bg_df_lab = bg_df[~bg_df["term"].isin(top_terms)].copy()

        fig, ax = plt.subplots(figsize=(8, 6))
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)

        texts = []

        for _, row in bg_df_lab.iterrows():
            txt = ax.text(
                row["umap_x"],
                row["umap_y"],
                row["term"],
                fontsize=7,
                ha="center",
                va="center",
                color="0.6",
                alpha=0.7,
            )
            texts.append(txt)

        for t in top_terms:
            row = df_terms_idx.loc[t]
            txt = ax.text(
                row["umap_x"],
                row["umap_y"],
                t,
                fontsize=8,
                ha="center",
                va="center",
                color="tab:red",
                fontweight="bold",
            )
            texts.append(txt)

        xs = sub["umap_x"].values
        ys = sub["umap_y"].values

        for _, r in sub.iterrows():
            label = f"{r['first_name']}\n{r['last_name']}"
            txt = ax.text(
                r["umap_x"],
                r["umap_y"],
                label,
                fontsize=7,
                ha="center",
                va="center",
                color="black",
            )
            texts.append(txt)

        mx = xs.mean()
        my = ys.mean()
        sig_txt = ax.text(
            mx,
            my,
            unit,
            fontsize=10,
            ha="center",
            va="center",
            fontweight="bold",
            color="black",
        )
        texts.append(sig_txt)

        if len(sub) >= 4:
            pts = np.vstack([xs, ys]).T
            cov = np.cov(pts, rowvar=False)
            if np.linalg.matrix_rank(cov) == 2:
                vals, vecs = np.linalg.eigh(cov)
                order_eig = np.argsort(vals)
                v1, v2 = vals[order_eig[1]], vals[order_eig[0]]
                evec1 = vecs[:, order_eig[1]]
                angle = np.degrees(np.arctan2(evec1[1], evec1[0]))
                k = 2.0
                width = 2 * k * np.sqrt(v1)
                height = 2 * k * np.sqrt(v2)
                ell = Ellipse(
                    (mx, my),
                    width=width,
                    height=height,
                    angle=angle,
                    edgecolor="black",
                    facecolor="none",
                    alpha=0.4,
                    linewidth=1.0,
                )
                ax.add_patch(ell)

        if texts:
            adjust_text(
                texts,
                ax=ax,
                autoalign="xy",
                expand_text=(1.02, 1.02),
                force_text=0.5,
                lim=200,
            )

        ax.set_xlabel("UMAP-1")
        ax.set_ylabel("UMAP-2")
        ax.set_title(f"{unit} – researchers and keywords")

        ax.set_aspect("equal", adjustable="box")  # same aspect/orientation on every UMAP
        plt.tight_layout()
        fname = panel_png(_file_key(unit))
        _save_figure(fname)
        plt.close()
        logger.info("Saved %s", fname)


# ── Trajectories (visualisation) ─────────────────────────────────────────────


def compute_cohort_trajectories(
    traj_df: pd.DataFrame,
    meta_ind: pd.DataFrame,
    *,
    cohort_column: str,
    band_years: int = 10,
) -> pd.DataFrame:
    """Aggregate trajectory points into per-(cohort, time-bin) centroids.

    Cohorts are bands of width *band_years* of a numeric person attribute, the
    column *cohort_column* of *meta_ind* (a start year, for example). The
    trajectory coordinates are joined to *meta_ind* (researcher_id ↔ ``id``) to
    recover each researcher's value; researchers without a number there (or a
    person table without that column) are dropped. Returns one row per (cohort,
    bin) with the mean UMAP coordinate and the number of contributing researchers.
    """
    cols = ["cohort", "cohort_start", "bin_start", "bin_end", "umap_x", "umap_y", "n_researchers"]
    if traj_df.empty or "id" not in meta_ind.columns or cohort_column not in meta_ind.columns:
        return pd.DataFrame(columns=cols)

    meta = pd.DataFrame(
        {
            "id": meta_ind["id"],
            "_cohort_value": pd.to_numeric(meta_ind[cohort_column], errors="coerce"),
        }
    ).dropna(subset=["_cohort_value"])

    merged = traj_df.merge(meta, left_on="researcher_id", right_on="id", how="inner")
    if merged.empty:
        return pd.DataFrame(columns=cols)

    merged["cohort_start"] = (merged["_cohort_value"] // band_years * band_years).astype(int)
    merged["cohort"] = merged["cohort_start"].map(lambda s: f"{s}–{s + band_years - 1}")
    grouped = merged.groupby(
        ["cohort", "cohort_start", "bin_start", "bin_end"], as_index=False
    ).agg(
        umap_x=("umap_x", "mean"),
        umap_y=("umap_y", "mean"),
        n_researchers=("researcher_id", "nunique"),
    )
    return grouped.sort_values(["cohort_start", "bin_end"]).reset_index(drop=True)


def _draw_arrows(ax, xs, ys, color, lw: float = 1.4) -> None:
    """Draw directional arrows between consecutive points of a path."""
    for k in range(len(xs) - 1):
        ax.annotate(
            "",
            xy=(xs[k + 1], ys[k + 1]),
            xytext=(xs[k], ys[k]),
            arrowprops={"arrowstyle": "->", "color": color, "lw": lw},
            zorder=3,
        )


def plot_cohort_trajectories(
    cohort_df: pd.DataFrame,
    *,
    fig_path,
    terms_df: pd.DataFrame | None = None,
    title: str = "Thematic mobility of cohorts",
    legend_title: str = "Cohort",
) -> None:
    """Plot per-cohort centroid drift across time-bins in the reference UMAP space."""
    fig, ax = plt.subplots(figsize=(11, 9))
    if terms_df is not None and not terms_df.empty:
        ax.scatter(terms_df["umap_x"], terms_df["umap_y"], s=4, c="0.85", alpha=0.5, zorder=1)

    cmap = matplotlib.colormaps["viridis"]
    cohorts = list(dict.fromkeys(cohort_df["cohort_start"].tolist()))
    for i, cstart in enumerate(cohorts):
        sub = cohort_df[cohort_df["cohort_start"] == cstart].sort_values("bin_end")
        color = cmap(i / max(len(cohorts) - 1, 1))
        xs = sub["umap_x"].to_numpy()
        ys = sub["umap_y"].to_numpy()
        ax.plot(
            xs, ys, "-", color=color, lw=2, alpha=0.9, zorder=3, label=str(sub["cohort"].iloc[0])
        )
        ax.scatter(xs, ys, s=40, color=color, zorder=4)
        _draw_arrows(ax, xs, ys, color)

    ax.set_title(title)
    ax.set_xlabel("UMAP-1")
    ax.set_ylabel("UMAP-2")
    if cohorts:
        ax.legend(title=legend_title, fontsize=8, loc="best")
    ax.set_aspect("equal", adjustable="box")  # same aspect/orientation on every UMAP
    fig.tight_layout()
    fig.savefig(fig_path, dpi=150)
    plt.close(fig)
    logger.info("Saved cohort trajectories to %s", fig_path)


def plot_researcher_trajectory(
    traj_df: pd.DataFrame,
    *,
    researcher_id: str,
    fig_path,
    terms_df: pd.DataFrame | None = None,
    title: str | None = None,
) -> None:
    """Plot one researcher's topical mobility as a time-ordered path in UMAP space.

    Markers are coloured oldest→newest; arrows show the direction of travel. A
    no-op (no file written) when the researcher has no trajectory rows.
    """
    sub = traj_df[traj_df["researcher_id"] == researcher_id].sort_values("bin_end")
    if sub.empty:
        logger.info("No trajectory rows for researcher_id=%s; skipping plot.", researcher_id)
        return

    fig, ax = plt.subplots(figsize=(10, 8))
    if terms_df is not None and not terms_df.empty:
        ax.scatter(terms_df["umap_x"], terms_df["umap_y"], s=4, c="0.85", alpha=0.5, zorder=1)

    xs = sub["umap_x"].to_numpy()
    ys = sub["umap_y"].to_numpy()
    n = len(xs)
    cmap = matplotlib.colormaps["plasma"]
    ax.plot(xs, ys, "-", color="0.4", lw=1.5, zorder=2)
    _draw_arrows(ax, xs, ys, "0.4", lw=1.2)
    for k in range(n):
        ax.scatter([xs[k]], [ys[k]], s=60, color=cmap(k / max(n - 1, 1)), zorder=4)
        label = f"{int(sub['bin_start'].iloc[k])}–{int(sub['bin_end'].iloc[k])}"
        ax.annotate(label, (xs[k], ys[k]), fontsize=8, xytext=(4, 4), textcoords="offset points")

    ax.set_title(title or "Thematic trajectory of a researcher")
    ax.set_xlabel("UMAP-1")
    ax.set_ylabel("UMAP-2")
    ax.set_aspect("equal", adjustable="box")  # same aspect/orientation on every UMAP
    fig.tight_layout()
    fig.savefig(fig_path, dpi=150)
    plt.close(fig)
    logger.info("Saved researcher trajectory to %s", fig_path)
