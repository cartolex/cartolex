# SPDX-License-Identifier: MIT
"""The atlas stages: SVD, term clustering, 2-D layout, plots and trajectories.

Every stage takes a :class:`~cartolex.context.RunContext` and reads and writes
only the files its ``paths`` name; nothing happens at import. Default
parameters live in :data:`DEFAULTS` (immutable); a project may freeze other
values in its ``paths.atlas_params_json``, read when a stage runs.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from cartolex.atlas.clustering import cluster_terms, prepare_cluster_embeddings
from cartolex.atlas.io import build_lexical_matrix, load_run_settings
from cartolex.atlas.model_files import (
    load_embeddings,
    load_lexical_data,
    load_svd,
    load_vectorizer,
    reject_legacy,
    save_embeddings,
    save_lexical_data,
)
from cartolex.atlas.placement import MapAnchors
from cartolex.atlas.plots import (
    aggregate_labs,
    plot_individuals_and_labs,
    plot_lab_panels,
    plot_superposed_map,
    plot_term_clusters,
)
from cartolex.atlas.reducers import compute_svd_embeddings, compute_umap, umap_available
from cartolex.lexicon.io_helpers import SlotIndex, slot_indexes

if TYPE_CHECKING:
    from cartolex.context import RunContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AtlasDefaults:
    """Default parameters of the atlas stages (a stage argument overrides its default).

    SVD: 20 leading components (tuned 2026-06-09: a low dimension denoises to
    broad themes and yields clean deterministic concept/subfield clustering).

    Layout: a moderate ``umap_n_neighbors`` with a non-zero ``umap_min_dist``
    over the person-fit layout gives a smoother, more globally-structured map
    that is less likely to be over-read as crisp "islands". The earlier
    aggressive bag-splitting recipe (n_neighbors=8 / min_dist=0) stays available
    by pinning the parameters. ``umap_layout`` "researcher" fits on persons and
    transforms terms (keeps person structure, terms intermingle); "joint"
    co-embeds both.

    Clustering happens in SVD space with cosine distance (see
    :mod:`cartolex.atlas.clustering`): agglomerative Ward into
    ``clustering_n_concepts`` concepts, every term assigned; these clusters ARE
    the hierarchy's concepts. Proto-subfields: a Ward cut groups the concept
    centroids into exactly ``clustering_target_subfields`` candidates (fewer
    only when there are fewer concepts).

    Trajectories: per-person topical mobility in the reference space, in
    ``traj_bin_years``-wide bins of the documents of the trajectory slots
    (``KeywordsConfig.corpus_slots``).
    """

    n_components_svd: int = 20
    umap_n_neighbors: int = 25
    umap_min_dist: float = 0.3
    umap_n_components: int = 2
    umap_metric: str = "cosine"
    umap_layout: str = "researcher"
    umap_random_state: int = 3
    umap_n_epochs: int | None = None
    umap_spread: float = 1.0
    umap_set_op_mix_ratio: float = 1.0
    umap_local_connectivity: int = 1
    umap_repulsion_strength: float = 1.0
    umap_negative_sample_rate: int = 5
    clustering_n_concepts: int = 150
    clustering_n_components: int = 50
    clustering_target_subfields: int = 30
    top_n_terms_per_cluster: int = 10
    min_researchers_per_lab: int = 3
    n_main_clusters: int = 10
    n_top_terms_per_main_cluster: int = 20
    n_top_terms_per_lab: int = 15
    lab_xy_padding: float = 0.02
    traj_bin_years: int = 3
    traj_min_docs_per_bin: int = 1
    traj_top_k_terms: int = 8
    #: ``(last_name, first_name)`` pairs drawn highlighted on the person maps.
    highlight_persons: tuple[tuple[str, str], ...] = ()


#: The engine's atlas defaults; never modified (a run reads its own copy).
DEFAULTS = AtlasDefaults()

#: Keys of a frozen-parameter file (upper-case, as written by earlier releases)
#: → the field they set and how to read the value.
_FROZEN_KEYS: dict[str, tuple[str, Any]] = {
    "N_COMPONENTS_SVD": ("n_components_svd", int),
    "UMAP_N_NEIGHBORS": ("umap_n_neighbors", int),
    "UMAP_MIN_DIST": ("umap_min_dist", float),
    "UMAP_N_COMPONENTS": ("umap_n_components", int),
    "UMAP_METRIC": ("umap_metric", str),
    "UMAP_RANDOM_STATE": ("umap_random_state", int),
    "UMAP_N_EPOCHS": ("umap_n_epochs", int),
    "UMAP_SPREAD": ("umap_spread", float),
    "UMAP_SET_OP_MIX_RATIO": ("umap_set_op_mix_ratio", float),
    "UMAP_LOCAL_CONNECTIVITY": ("umap_local_connectivity", int),
    "UMAP_REPULSION_STRENGTH": ("umap_repulsion_strength", float),
    "UMAP_NEGATIVE_SAMPLE_RATE": ("umap_negative_sample_rate", int),
    "CLUSTERING_N_CONCEPTS": ("clustering_n_concepts", int),
    "CLUSTERING_N_COMPONENTS": ("clustering_n_components", int),
    "CLUSTERING_TARGET_SUBFIELDS": ("clustering_target_subfields", int),
    "TOP_N_TERMS_PER_CLUSTER": ("top_n_terms_per_cluster", int),
    "MIN_RESEARCHERS_PER_LAB": ("min_researchers_per_lab", int),
    "N_MAIN_CLUSTERS": ("n_main_clusters", int),
    "N_TOP_TERMS_PER_MAIN_CLUSTER": ("n_top_terms_per_main_cluster", int),
    "N_TOP_TERMS_PER_LAB": ("n_top_terms_per_lab", int),
    "LAB_XY_PADDING": ("lab_xy_padding", float),
    "TRAJ_BIN_YEARS": ("traj_bin_years", int),
    "TRAJ_MIN_DOCS_PER_BIN": ("traj_min_docs_per_bin", int),
    "TRAJ_TOP_K_TERMS": ("traj_top_k_terms", int),
}


def load_frozen_params(path: Path, base: AtlasDefaults = DEFAULTS) -> AtlasDefaults:
    """*base* with the values frozen in the JSON file *path* (unchanged when absent).

    Unparsable files and invalid values are logged and ignored.
    """
    if not path.exists():
        return base
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to parse frozen params at %s", path)
        return base
    changes: dict[str, Any] = {}
    for key, (name, cast) in _FROZEN_KEYS.items():
        if key in payload:
            try:
                changes[name] = cast(payload[key])
            except Exception:
                logger.warning("Invalid frozen value for %s: %s", key, payload[key])
    return dataclasses.replace(base, **changes)


def atlas_defaults(ctx: RunContext) -> AtlasDefaults:
    """The atlas defaults of a run: :data:`DEFAULTS` with the project's frozen values."""
    return load_frozen_params(ctx.paths.atlas_params_json)


def _load_hierarchy_doc(ctx: RunContext) -> dict | None:
    """Return the full applied hierarchy doc (subfields ⊃ concepts), or None.

    The plots stage normally runs before subfields exist; this lets the term
    maps colour keywords by CONCEPT when the plots are regenerated after the
    subfields are applied, and fall back to plain clusters otherwise.
    """
    sf_json = ctx.paths.subfields_json
    if not sf_json.exists():
        return None
    try:
        doc = json.loads(sf_json.read_text(encoding="utf-8"))
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


def _load_applied_subfields(ctx: RunContext) -> list[dict] | None:
    """Return kept applied subfields, or None when the subfields are not applied yet."""
    doc = _load_hierarchy_doc(ctx)
    if doc is None:
        return None
    kept = [sf for sf in doc.get("subfields", []) if sf.get("keep", True)]
    return kept or None


def _concept_anchor_vectors(
    terms: list[str],
    Z_terms: np.ndarray,
    *,
    doc: dict | None = None,
    cluster_labels: np.ndarray | None = None,
    ctx: RunContext | None = None,
) -> np.ndarray | None:
    """Concept centroids in SVD space, the anchors of the anchored UMAP layout.

    Prefers the applied hierarchy's concepts (their ``term_indices``); falls
    back to the raw term clusters when no hierarchy is applied yet (the concepts
    ARE the term clusters). Returns one L2-normalised centroid per group, or None
    when neither source exists. *doc*/*cluster_labels* are injectable for tests;
    by default they are loaded from the run's artefacts (*ctx*).
    """
    from sklearn.preprocessing import normalize

    Zn = normalize(np.asarray(Z_terms, dtype=float))
    groups: list[list[int]] = []
    if doc is None and ctx is not None:
        doc = _load_hierarchy_doc(ctx)
    if isinstance(doc, dict):
        for c in doc.get("concepts", []):
            idxs = [int(t) for t in c.get("term_indices", []) if 0 <= int(t) < len(terms)]
            if idxs:
                groups.append(idxs)
    if not groups:
        labels = cluster_labels
        if labels is None and ctx is not None:
            labels = _load_term_cluster_labels(ctx.paths.terms_clustered_csv, terms)
        if labels is None:
            return None
        for cid in sorted({int(c) for c in labels if int(c) >= 0}):
            idxs = [i for i, c in enumerate(labels) if int(c) == cid]
            if idxs:
                groups.append(idxs)
    if not groups:
        return None
    anchors = normalize(np.vstack([Zn[idx].mean(axis=0) for idx in groups]))
    logger.info("Concept anchors built: %d (for the anchored UMAP layout).", len(anchors))
    return anchors


def _load_term_cluster_labels(terms_clustered_csv: Path, terms: list[str]):
    """Per-term SVD cluster id aligned to *terms* (−1 where unknown), or None.

    Reads the clustering stage's per-term table; used to nudge a semi-supervised
    UMAP toward the SVD clusters. Returns None if the clustering has not run.
    """
    import numpy as np

    if not terms_clustered_csv.exists():
        return None
    try:
        df = pd.read_csv(terms_clustered_csv)
    except Exception:
        return None
    if "term" not in df.columns or "cluster" not in df.columns:
        return None
    term_to_cluster = dict(zip(df["term"].astype(str), df["cluster"].astype(int), strict=False))
    return np.array([int(term_to_cluster.get(str(t), -1)) for t in terms], dtype=int)


def _refresh_clustered_umap_coords(terms_clustered_csv: Path, terms: list[str], umap_terms) -> None:
    """Refresh the per-term clustered CSV's umap_x/umap_y from a new projection.

    The clustering may run before the UMAP layout; in that case its overlay
    coords are NaN. After UMAP, this rewrites those coords (joined by term) so the
    clustered map renders correctly — cluster *membership* is untouched.
    """
    if not terms_clustered_csv.exists():
        return
    try:
        df = pd.read_csv(terms_clustered_csv)
    except Exception:
        return
    if "term" not in df.columns:
        return
    coords = {str(t): (float(x), float(y)) for t, (x, y) in zip(terms, umap_terms, strict=False)}
    df["umap_x"] = (
        df["term"].astype(str).map(lambda t: coords.get(t, (float("nan"), float("nan")))[0])
    )
    df["umap_y"] = (
        df["term"].astype(str).map(lambda t: coords.get(t, (float("nan"), float("nan")))[1])
    )
    df.to_csv(terms_clustered_csv, index=False)
    logger.info("Refreshed clustered overlay coords in %s", terms_clustered_csv)


def _require_lexical_models(paths: Any) -> None:
    """Fail clearly when the SVD stage's lexical data or embeddings are not there."""
    reject_legacy(paths.lexical_data_json, paths.embeddings_json, stage="SVD")
    missing = [p.name for p in (paths.lexical_data_json, paths.embeddings_json) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"{', '.join(missing)} not found — run the SVD stage first.")


def run_svd(
    ctx: RunContext,
    *,
    svd_n_components: int | None = None,
    force: bool = False,
) -> None:
    """SVD stage: build the TF-IDF matrix and run the SVD reduction.

    Reads the consolidation outputs (``paths.person_terms_csv``,
    ``paths.roster_csv``, ``paths.run_settings_json``); writes the PCA-like
    coordinate tables, the atlas vocabulary, the SVD model and the persisted
    lexical data and embeddings (without layout coordinates yet).
    """
    with ctx.threads.applied():
        _run_svd(ctx, svd_n_components=svd_n_components, force=force)


def _run_svd(ctx: RunContext, *, svd_n_components: int | None, force: bool) -> None:
    paths = ctx.paths
    defaults = atlas_defaults(ctx)
    ctx.enforce_staleness("svd", force=force)
    snapshot = load_run_settings(paths.run_settings_json)
    slots = [str(s.get("id", "?")) for s in snapshot.get("corpus_slots", []) if s.get("fit", True)]
    logger.info("Corpus slots (from %s): %s", paths.run_settings_json, ", ".join(slots) or "?")

    eff_svd_n_components = svd_n_components or defaults.n_components_svd

    ctx.report(0.0, "building the person × keyword matrix")
    data = build_lexical_matrix(
        kw_researcher_csv=paths.person_terms_csv,
        researcher_index_csv=paths.roster_csv,
    )
    ctx.report(0.3, "fitting the space")
    paths.atlas_dir.mkdir(parents=True, exist_ok=True)
    paths.models_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"term": data.terms}).to_csv(paths.atlas_terms_csv, index=False)
    logger.info("Wrote restricted term list to %s", paths.atlas_terms_csv)

    emb = compute_svd_embeddings(
        data,
        n_components=eff_svd_n_components,
        model_path=paths.svd_model_json,
    )

    ctx.report(0.8, "writing the space")
    pcs_ind = pd.DataFrame(
        emb.Z_ind,
        columns=[f"PC{k + 1}" for k in range(emb.Z_ind.shape[1])],
    )
    df_pca_ind = pd.concat([data.meta_ind.reset_index(drop=True), pcs_ind], axis=1)
    df_pca_ind.to_csv(paths.pca_persons_csv, index=False)
    logger.info("Wrote PCA-like coordinates for individuals to %s", paths.pca_persons_csv)

    pcs_terms = pd.DataFrame(
        emb.Z_terms,
        columns=[f"PC{k + 1}" for k in range(emb.Z_terms.shape[1])],
    )
    df_pca_terms = pd.concat([pd.DataFrame({"term": data.terms}), pcs_terms], axis=1)
    df_pca_terms.to_csv(paths.pca_terms_csv, index=False)
    logger.info("Wrote PCA-like coordinates for terms to %s", paths.pca_terms_csv)

    # Persist for subsequent independent steps.
    save_lexical_data(data, paths.lexical_data_json)
    save_embeddings(emb, paths.embeddings_json)
    logger.info(
        "SVD complete: %d researchers × %d terms → %d components",
        len(data.individuals),
        len(data.terms),
        emb.Z_ind.shape[1],
    )


def run_umap(
    ctx: RunContext,
    *,
    umap_n_neighbors: int | None = None,
    umap_min_dist: float | None = None,
    umap_metric: str | None = None,
    umap_random_state: int | None = None,
    umap_n_epochs: int | None = None,
    umap_spread: float | None = None,
    umap_set_op_mix_ratio: float | None = None,
    umap_local_connectivity: int | None = None,
    umap_repulsion_strength: float | None = None,
    umap_negative_sample_rate: int | None = None,
    respect_clusters: bool = False,
    cluster_target_weight: float | None = None,
    umap_layout: str | None = None,
    force: bool = False,
    umap_fallback: str | None = None,
) -> None:
    """UMAP layout stage: project the SVD space to 2D (the final lexical step).

    ``umap_fallback="tsne"`` accepts an anchored t-SNE *preview* layout when umap-learn
    is not installed (see :func:`cartolex.atlas.reducers.compute_umap`); the diagnostics
    JSON then records ``layout_engine: "tsne-preview"`` instead of ``"umap"``.

    Requires the SVD stage (reads the lexical data and the embeddings) and is meant to
    run *after* the term clustering so the map can be coloured by the term clusters.
    Writes the person, term and group layout tables and adds the layout coordinates
    to the stored embeddings, which hold the map: the terms (like every later point)
    are placed by their nearest researchers (:mod:`cartolex.atlas.placement`), and no
    layout model is stored.

    ``umap_layout`` picks the recipe: ``"researcher"`` (default) fits on the researchers and
    projects terms in, keeping researcher structure; ``"researcher_concepts"`` additionally
    anchors the fit on the concept centroids (hierarchy concepts, or the raw term clusters
    before the hierarchy is applied) so the projected keywords land next to their concept
    instead of forming coronas; ``"joint"`` co-embeds both; ``"tsne_anchored"`` is the
    umap-learn-free anchored t-SNE (see :class:`cartolex.atlas.reducers.AnchoredTSNE`).
    The map is coloured by the high-dimensional term clusters. Every default comes from
    :func:`atlas_defaults`; pin ``umap_n_neighbors`` / ``umap_min_dist`` to override.
    """
    with ctx.threads.applied():
        _run_umap(
            ctx,
            umap_n_neighbors=umap_n_neighbors,
            umap_min_dist=umap_min_dist,
            umap_metric=umap_metric,
            umap_random_state=umap_random_state,
            umap_n_epochs=umap_n_epochs,
            umap_spread=umap_spread,
            umap_set_op_mix_ratio=umap_set_op_mix_ratio,
            umap_local_connectivity=umap_local_connectivity,
            umap_repulsion_strength=umap_repulsion_strength,
            umap_negative_sample_rate=umap_negative_sample_rate,
            respect_clusters=respect_clusters,
            cluster_target_weight=cluster_target_weight,
            umap_layout=umap_layout,
            force=force,
            umap_fallback=umap_fallback,
        )


def _run_umap(
    ctx: RunContext,
    *,
    umap_n_neighbors: int | None,
    umap_min_dist: float | None,
    umap_metric: str | None,
    umap_random_state: int | None,
    umap_n_epochs: int | None,
    umap_spread: float | None,
    umap_set_op_mix_ratio: float | None,
    umap_local_connectivity: int | None,
    umap_repulsion_strength: float | None,
    umap_negative_sample_rate: int | None,
    respect_clusters: bool,
    cluster_target_weight: float | None,
    umap_layout: str | None,
    force: bool,
    umap_fallback: str | None,
) -> None:
    paths = ctx.paths
    d = atlas_defaults(ctx)
    _require_lexical_models(paths)
    ctx.enforce_staleness("umap", force=force)

    eff_umap_n_neighbors = umap_n_neighbors or d.umap_n_neighbors
    eff_umap_min_dist = umap_min_dist if umap_min_dist is not None else d.umap_min_dist
    eff_umap_metric = umap_metric or d.umap_metric
    eff_umap_random_state = (
        umap_random_state if umap_random_state is not None else d.umap_random_state
    )
    eff_umap_n_epochs = umap_n_epochs if umap_n_epochs is not None else d.umap_n_epochs
    eff_umap_spread = umap_spread if umap_spread is not None else d.umap_spread
    eff_umap_set_op_mix_ratio = (
        umap_set_op_mix_ratio if umap_set_op_mix_ratio is not None else d.umap_set_op_mix_ratio
    )
    eff_umap_local_connectivity = (
        umap_local_connectivity
        if umap_local_connectivity is not None
        else d.umap_local_connectivity
    )
    eff_umap_repulsion_strength = (
        umap_repulsion_strength
        if umap_repulsion_strength is not None
        else d.umap_repulsion_strength
    )
    eff_umap_negative_sample_rate = (
        umap_negative_sample_rate
        if umap_negative_sample_rate is not None
        else d.umap_negative_sample_rate
    )

    data = load_lexical_data(paths.lexical_data_json)
    emb = load_embeddings(paths.embeddings_json)

    # Optional semi-supervised nudge: align existing SVD term clusters to the
    # term order so UMAP keeps cluster members together.
    term_cluster_labels = None
    if respect_clusters:
        term_cluster_labels = _load_term_cluster_labels(paths.terms_clustered_csv, data.terms)
        if term_cluster_labels is None:
            logger.warning(
                "respect_clusters set but no clusters found — run the clustering stage first; "
                "falling back to unsupervised UMAP."
            )
    eff_cluster_target_weight = cluster_target_weight if cluster_target_weight is not None else 0.3
    eff_umap_layout = umap_layout or d.umap_layout

    # Concept anchors for the anchored layout (compute_umap falls back to the
    # plain researcher layout when none are available).
    anchor_vectors = None
    preview = (
        eff_umap_layout != "tsne_anchored" and umap_fallback == "tsne" and not umap_available()
    )
    if eff_umap_layout in ("researcher_concepts", "tsne_anchored") or preview:
        # Anchored layouts (UMAP or t-SNE) and the t-SNE preview all need the anchors.
        anchor_vectors = _concept_anchor_vectors(data.terms, emb.Z_terms, ctx=ctx)

    ctx.report(0.1, "fitting the layout")
    emb = compute_umap(
        emb,
        n_neighbors=eff_umap_n_neighbors,
        min_dist=eff_umap_min_dist,
        n_components=d.umap_n_components,
        metric=eff_umap_metric,
        random_state=eff_umap_random_state,
        n_epochs=eff_umap_n_epochs,
        spread=eff_umap_spread,
        set_op_mix_ratio=eff_umap_set_op_mix_ratio,
        local_connectivity=eff_umap_local_connectivity,
        repulsion_strength=eff_umap_repulsion_strength,
        negative_sample_rate=eff_umap_negative_sample_rate,
        term_cluster_labels=term_cluster_labels,
        target_weight=eff_cluster_target_weight,
        layout=eff_umap_layout,
        anchor_vectors=anchor_vectors,
        fallback=umap_fallback,
    )
    layout_engine = (
        "tsne_anchored"
        if eff_umap_layout == "tsne_anchored"
        else ("tsne-preview" if preview else "umap")
    )

    ctx.report(0.8, "writing the layout")
    df_umap_ind = data.meta_ind.copy()
    df_umap_ind["umap_x"] = emb.umap_ind[:, 0]
    df_umap_ind["umap_y"] = emb.umap_ind[:, 1]
    paths.atlas_dir.mkdir(parents=True, exist_ok=True)
    df_umap_ind.to_csv(paths.layout_persons_csv, index=False)
    logger.info("Wrote UMAP embeddings for individuals to %s", paths.layout_persons_csv)

    df_umap_terms = pd.DataFrame(
        {
            "term": data.terms,
            "umap_x": emb.umap_terms[:, 0],
            "umap_y": emb.umap_terms[:, 1],
        }
    )
    df_umap_terms.to_csv(paths.layout_terms_csv, index=False)
    logger.info("Wrote UMAP embeddings for terms to %s", paths.layout_terms_csv)

    # If clustering already ran (before the layout), its per-term overlay coords are
    # stale/NaN — refresh them from this projection so the clustered map is correct
    # without forcing a re-run of the clustering (membership is SVD-based and unchanged).
    _refresh_clustered_umap_coords(paths.terms_clustered_csv, data.terms, emb.umap_terms)

    df_labs = aggregate_labs(
        data.meta_ind,
        emb,
        min_researchers_per_lab=d.min_researchers_per_lab,
    )
    df_labs.to_csv(paths.layout_groups_csv, index=False)
    logger.info("Wrote lab embeddings to %s", paths.layout_groups_csv)

    # Update persisted embeddings with UMAP coordinates for subsequent steps.
    save_embeddings(emb, paths.embeddings_json)

    # Quantify the joint embedding (intermingling / corona / faithfulness) so the
    # operator gets a number instead of eyeballing the map for a corona artifact.
    _write_umap_diagnostics(
        emb,
        out_json=paths.layout_diagnostics_json,
        high_metric=d.umap_metric,
        layout_engine=layout_engine,
    )

    logger.info(
        "UMAP complete: %d individuals, %d terms projected to 2D",
        len(data.individuals),
        len(data.terms),
    )


def _write_umap_diagnostics(
    emb, *, out_json: Path, high_metric: str, layout_engine: str = "umap"
) -> None:
    """Compute and persist joint-embedding diagnostics to *out_json*."""
    import numpy as np

    from cartolex.atlas.diagnostics import embedding_diagnostics

    if emb.umap_ind is None or emb.umap_terms is None:
        return
    coords2d = np.vstack([emb.umap_ind, emb.umap_terms])
    z_high = np.vstack([emb.Z_ind, emb.Z_terms])
    is_term = np.concatenate(
        [np.zeros(emb.umap_ind.shape[0], dtype=bool), np.ones(emb.umap_terms.shape[0], dtype=bool)]
    )
    diag = embedding_diagnostics(coords2d, is_term, z_high, high_metric=high_metric)
    diag["layout_engine"] = layout_engine
    out_json.write_text(json.dumps(diag, indent=2), encoding="utf-8")
    logger.info(
        "UMAP diagnostics: mixing=%.2f radial_ratio=%.2f trustworthiness=%s corona=%s",
        diag.get("mixing_index", float("nan")),
        diag.get("radial_ratio", float("nan")),
        diag.get("trustworthiness"),
        diag.get("corona"),
    )


def _compute_proto_subfields(
    df_terms_clustered: pd.DataFrame,
    Z_terms: np.ndarray,
    *,
    target_subfields: int,
    n_components_cluster: int,
) -> dict[str, Any]:
    """Deterministic proto-subfields: group the concept centroids (Ward cut at the target).

    Each concept (term cluster) is reduced to its centroid in the same L2-normalised SVD
    subspace used for clustering; :func:`group_subfields` cuts those centroids into exactly
    ``target_subfields`` candidate subfields (fewer only when there are fewer concepts). Each
    concept and subfield is seeded by its dominant keyword (the member term with the highest
    ``global_score``). Returns the JSON payload.
    """
    from sklearn.preprocessing import normalize

    from cartolex.atlas.hierarchy import group_subfields

    labels = df_terms_clustered["cluster"].to_numpy()
    terms = df_terms_clustered["term"].astype(str).tolist()
    gscore = df_terms_clustered["global_score"].to_numpy(dtype=float)
    Zn = prepare_cluster_embeddings(np.asarray(Z_terms, dtype=float), n_components_cluster)

    concept_ids = sorted(int(c) for c in set(labels.tolist()) if int(c) >= 0)
    term_idx_of: dict[int, np.ndarray] = {c: np.where(labels == c)[0] for c in concept_ids}

    def dominant(idx: np.ndarray) -> str:
        return terms[int(idx[int(np.argmax(gscore[idx]))])]

    centroids = np.array(
        [normalize(Zn[term_idx_of[c]].mean(axis=0).reshape(1, -1))[0] for c in concept_ids]
    )
    sub_of_pos = group_subfields(centroids, target=target_subfields)  # per-concept subfield idx

    subfields: list[dict[str, Any]] = []
    concept_subfield: dict[str, int] = {}
    for si in sorted(set(sub_of_pos.tolist())):
        cpos = [p for p, s in enumerate(sub_of_pos.tolist()) if s == si]
        cids = [concept_ids[p] for p in cpos]
        all_terms = np.concatenate([term_idx_of[c] for c in cids])
        subfields.append({"id": int(si), "label": dominant(all_terms), "concept_ids": cids})
        for c in cids:
            concept_subfield[str(c)] = int(si)

    return {
        "schema_version": "1.0",
        "target_subfields": int(target_subfields),
        "n_subfields": len(subfields),
        "concept_labels": {str(c): dominant(term_idx_of[c]) for c in concept_ids},
        "subfields": subfields,
        "concept_subfield": concept_subfield,
    }


def run_clustering(
    ctx: RunContext,
    *,
    n_concepts: int | None = None,
    n_components: int | None = None,
    target_subfields: int | None = None,
    force: bool = False,
) -> None:
    """Cluster terms in SVD space (cosine) into concepts via **agglomerative Ward**.

    Ward bottom-up cut at ``n_concepts`` (default ``clustering_n_concepts``): every term is
    assigned (full coverage, no noise) and ``n_concepts`` is a direct knob. These clusters
    **ARE the hierarchy's concepts**, so changing ``n_concepts`` changes the hierarchy.
    Also groups the concept centroids into exactly ``target_subfields`` deterministic **proto-
    subfields** (the candidate subfields of the subfield draft). Needs only the SVD; writes
    the cluster table, the per-term clustered table and the proto-subfields.
    """
    with ctx.threads.applied():
        _run_clustering(
            ctx,
            n_concepts=n_concepts,
            n_components=n_components,
            target_subfields=target_subfields,
            force=force,
        )


def _run_clustering(
    ctx: RunContext,
    *,
    n_concepts: int | None,
    n_components: int | None,
    target_subfields: int | None,
    force: bool,
) -> None:
    paths = ctx.paths
    d = atlas_defaults(ctx)
    _require_lexical_models(paths)
    ctx.enforce_staleness("cluster", force=force)

    data = load_lexical_data(paths.lexical_data_json)
    emb = load_embeddings(paths.embeddings_json)
    eff_n_concepts = n_concepts or d.clustering_n_concepts
    eff_n_components = n_components or d.clustering_n_components
    eff_target_subfields = target_subfields or d.clustering_target_subfields

    paths.atlas_dir.mkdir(parents=True, exist_ok=True)
    ctx.report(0.1, "grouping keywords into topics")
    df_terms_clustered = cluster_terms(
        data,
        emb,
        n_clusters=eff_n_concepts,
        top_n_terms_per_cluster=d.top_n_terms_per_cluster,
        clusters_terms_csv=paths.clusters_csv,
        n_components_cluster=eff_n_components,
    )
    df_terms_clustered.to_csv(paths.terms_clustered_csv, index=False)
    n_clusters = df_terms_clustered["cluster"].nunique()

    ctx.report(0.8, "grouping topics")
    proto = _compute_proto_subfields(
        df_terms_clustered,
        emb.Z_terms,
        target_subfields=eff_target_subfields,
        n_components_cluster=eff_n_components,
    )
    paths.proto_subfields_json.write_text(
        json.dumps(proto, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Agglomerative clustering complete: %d concepts (full coverage) → %d proto-subfields "
        "(target %d).",
        n_clusters,
        proto["n_subfields"],
        eff_target_subfields,
    )


def run_lexical_plots(
    ctx: RunContext,
    *,
    force: bool = False,
    color_persons_by: str | None = None,
    panel_groups: Collection[str] | None = None,
) -> None:
    """Plots stage: generate all static PNG plots from the lexical analysis.

    Requires run_clustering() to have been run first.  Reads the stored
    lexical data and embeddings and the per-term cluster assignments from disk.
    Produces the term-cluster map, the person-and-group map and the superposed
    map, each group labelled by its value of the group column (``ctx.paths``).
    *color_persons_by* names a column of the person roster (any attribute
    column of the corpus index, a rank for example) that colours the persons of
    the person-and-group map. *panel_groups* lists the groups (values of the
    group column) that also get a panel of their own; none by default.
    """
    with ctx.threads.applied():
        _run_lexical_plots(
            ctx, force=force, color_persons_by=color_persons_by, panel_groups=panel_groups
        )


def _run_lexical_plots(
    ctx: RunContext,
    *,
    force: bool,
    color_persons_by: str | None,
    panel_groups: Collection[str] | None,
) -> None:
    paths = ctx.paths
    d = atlas_defaults(ctx)
    ctx.enforce_staleness("plots", force=force)
    reject_legacy(paths.lexical_data_json, paths.embeddings_json, stage="SVD")
    for required in (paths.lexical_data_json, paths.embeddings_json, paths.terms_clustered_csv):
        if not required.exists():
            raise FileNotFoundError(
                f"{required.name} not found — run the SVD, clustering and layout stages first."
            )

    data = load_lexical_data(paths.lexical_data_json)
    emb = load_embeddings(paths.embeddings_json)
    df_terms_clustered = pd.read_csv(paths.terms_clustered_csv)
    df_labs = pd.read_csv(paths.layout_groups_csv)
    paths.atlas_dir.mkdir(parents=True, exist_ok=True)

    hierarchy_doc = _load_hierarchy_doc(ctx)
    xs = df_terms_clustered["umap_x"].values
    ys = df_terms_clustered["umap_y"].values
    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()
    dx = x_max - x_min
    dy = y_max - y_min
    xlim = (x_min - d.lab_xy_padding * dx, x_max + d.lab_xy_padding * dx)
    ylim = (y_min - d.lab_xy_padding * dy, y_max + d.lab_xy_padding * dy)

    plot_term_clusters(
        df_terms_clustered,
        top_n_terms_per_cluster=d.top_n_terms_per_cluster,
        fig_path=paths.term_clusters_png,
        xlim=xlim,
        ylim=ylim,
        subfields=_load_applied_subfields(ctx),
        hierarchy_doc=hierarchy_doc,
    )
    plot_individuals_and_labs(
        data,
        emb,
        df_labs,
        highlight_researchers=list(d.highlight_persons),
        fig_path=paths.persons_groups_png,
        xlim=xlim,
        ylim=ylim,
        color_by=color_persons_by,
    )
    plot_superposed_map(
        data,
        emb,
        df_labs,
        df_terms_clustered,
        highlight_researchers=list(d.highlight_persons),
        fig_path=paths.superposed_png,
        xlim=xlim,
        ylim=ylim,
        hierarchy_doc=hierarchy_doc,
    )
    plot_lab_panels(
        data,
        emb,
        df_terms_clustered,
        groups=tuple(panel_groups or ()),
        n_main_clusters=d.n_main_clusters,
        n_top_terms_per_main_cluster=d.n_top_terms_per_main_cluster,
        n_top_terms_per_lab=d.n_top_terms_per_lab,
        lab_xy_padding=d.lab_xy_padding,
        panel_png=paths.group_panel_png,
    )
    logger.info("Static plots written to %s", paths.atlas_dir)


def _load_per_document_corpus(indexes: Sequence[SlotIndex]) -> pd.DataFrame:
    """Load per-document corpus rows (year/type + text) for trajectory binning.

    Reads the per-document index of each trajectory slot, in order, and the
    referenced text files, keeping only the columns the trajectory stage
    needs. A slot's document types filter its rows (a row without a type
    always passes). Indexes lacking the per-document schema (a year per
    document) are skipped with a warning.
    """
    frames: list[pd.DataFrame] = []
    needed = {"last_name", "first_name", "unit", "doc_year", "txt_path"}
    for slot_id, idx, doc_types in indexes:
        if not idx.exists():
            continue
        df = pd.read_csv(idx)
        if not needed.issubset(df.columns):
            logger.warning(
                "Index %s (slot %s) lacks per-document columns (doc_year, txt_path); "
                "skipping it for trajectories.",
                idx.name,
                slot_id,
            )
            continue
        if "doc_type" not in df.columns:
            df["doc_type"] = ""
        if doc_types is not None:
            types = df["doc_type"].fillna("").astype(str).str.strip().str.lower()
            df = df[(types == "") | types.isin(set(doc_types))]
        texts: list[str] = []
        for _, row in df.iterrows():
            p = Path(str(row["txt_path"]))
            if not p.is_absolute():
                p = idx.parent / p
            try:
                texts.append(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                texts.append("")
        out = df[["last_name", "first_name", "unit", "doc_year", "doc_type"]].copy()
        # An empty unit (a person without a group) reads back as NaN; keep it a
        # blank string so researcher_id is "<last>||<first>||" and not "...||nan",
        # matching make_researcher_id(last, first, "") on the bundle side.
        out["unit"] = out["unit"].fillna("").astype(str)
        out["text"] = texts
        frames.append(out)

    if not frames:
        return pd.DataFrame(
            columns=["last_name", "first_name", "unit", "doc_year", "doc_type", "text"]
        )
    return pd.concat(frames, ignore_index=True)


def _lexicon_maps(
    subfields_json: Path, terms_by_idx: list[str]
) -> tuple[dict[str, int], dict[int, int]]:
    """Evidence-based attribution maps from the applied hierarchy for the time machine.

    Returns ``(term_to_concept, concept_to_subfield)`` resolved against the SVD
    term order (*terms_by_idx*), so per-window trajectory weights are aggregated
    from the window's own terms — the same lexicon attribution used for the
    fitted persons and for any projected set. Empty maps when the hierarchy
    is absent (the time-machine weights then degrade to empty; positions still
    computed).
    """
    from cartolex.lexicon.subfields import term_to_group_maps

    sf_json = subfields_json
    doc = json.loads(sf_json.read_text(encoding="utf-8")) if sf_json.exists() else {}
    doc = doc if isinstance(doc, dict) else {}
    term_to_concept, concept_to_subfield, _cl, _sl = term_to_group_maps(doc, terms_by_idx)
    return term_to_concept, concept_to_subfield


def run_trajectories(
    ctx: RunContext,
    *,
    bin_years: int | None = None,
    doc_types: tuple[str, ...] | None = None,
    min_docs_per_bin: int | None = None,
    length_alpha: float = 2.0,
    force: bool = False,
    cohort_by: str | None = None,
) -> None:
    """Trajectories stage: project per-(researcher, time-bin) fingerprints into the reference map.

    Requires the consolidation artifacts (the restricted vectorizer,
    ``term_aliases.csv``), the stored SVD model and the map (the layout's
    embeddings). Reads the
    per-document index of every trajectory slot (``ctx.settings.corpus_slots``,
    each filtered by its own document types; *doc_types*, when given, keeps
    only those types across all slots), bins each researcher's documents into
    fixed-width time windows, folds them onto the canonical concept vocabulary,
    projects them through the same SVD as the static map and places them on it by
    their nearest mapped people (:mod:`cartolex.atlas.placement`). Bins are
    counted back from ``ctx.now_year``. Writes the trajectory points and the
    per-window reprojections. Skipped with a warning if prerequisites are absent.

    *cohort_by* names a numeric column of the person roster (a start year, for
    example): the stage then also draws the mobility of the cohorts it defines
    (ten-unit bands of its values) in ``ctx.paths.cohort_trajectories_png``.
    """
    with ctx.threads.applied():
        _run_trajectories(
            ctx,
            bin_years=bin_years,
            doc_types=doc_types,
            min_docs_per_bin=min_docs_per_bin,
            length_alpha=length_alpha,
            force=force,
            cohort_by=cohort_by,
        )


def _run_trajectories(
    ctx: RunContext,
    *,
    bin_years: int | None,
    doc_types: tuple[str, ...] | None,
    min_docs_per_bin: int | None,
    length_alpha: float,
    force: bool,
    cohort_by: str | None,
) -> None:
    paths = ctx.paths
    d = atlas_defaults(ctx)
    ctx.enforce_staleness("trajectories", force=force)
    from cartolex.atlas.plots import compute_cohort_trajectories, plot_cohort_trajectories
    from cartolex.atlas.trajectories import (
        build_trajectory_matrix,
        build_trajectory_windows,
        project_trajectories,
    )

    vectorizer_path = paths.vectorizer_json
    aliases_path = paths.term_aliases_csv
    reject_legacy(vectorizer_path, stage="consolidation")
    reject_legacy(paths.svd_model_json, paths.lexical_data_json, stage="SVD")
    reject_legacy(paths.embeddings_json, stage="UMAP layout")
    prereqs = [
        vectorizer_path,
        aliases_path,
        paths.svd_model_json,
        paths.embeddings_json,
        paths.lexical_data_json,
    ]
    missing = [p.name for p in prereqs if not p.exists()]
    if missing:
        logger.warning(
            "Trajectories skipped — missing prerequisites: %s. Run the keyword "
            "consolidation and SVD/UMAP stages first.",
            ", ".join(missing),
        )
        return

    eff_now = ctx.now_year
    eff_bins = bin_years or d.traj_bin_years
    eff_types = tuple(doc_types) if doc_types else None
    eff_min = min_docs_per_bin if min_docs_per_bin is not None else d.traj_min_docs_per_bin

    docs = _load_per_document_corpus(slot_indexes(ctx, trajectory=True))
    if docs.empty:
        logger.warning("Trajectories: no per-document corpus rows found; nothing to do.")
        return

    vectorizer = load_vectorizer(vectorizer_path)
    aliases_df = pd.read_csv(aliases_path)
    alias_to_canon = {
        str(a).strip().lower(): str(c).strip().lower()
        for a, c in zip(aliases_df["alias"], aliases_df["canonical"], strict=False)
    }
    data = load_lexical_data(paths.lexical_data_json)
    svd_model = load_svd(paths.svd_model_json)
    emb = load_embeddings(paths.embeddings_json)
    if emb.umap_ind is None:
        logger.warning("Trajectories skipped — the map is not drawn yet: run the layout first.")
        return
    anchors = MapAnchors(emb.Z_ind, emb.umap_ind)

    ctx.report(0.2, "time windows")
    traj = build_trajectory_matrix(
        docs,
        vectorizer=vectorizer,
        alias_to_canon=alias_to_canon,
        ref_terms=list(data.terms),
        now_year=eff_now,
        bin_years=eff_bins,
        doc_types=eff_types,
        length_alpha=length_alpha,
        top_k_terms=d.traj_top_k_terms,
        min_docs_per_bin=eff_min,
    )

    coords = project_trajectories(traj.B, svd_model, anchors)
    out_df = traj.meta.copy()
    out_df["umap_x"] = coords[:, 0] if len(out_df) else []
    out_df["umap_y"] = coords[:, 1] if len(out_df) else []
    out_csv = paths.trajectories_csv
    paths.atlas_dir.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_csv, index=False)
    logger.info("Wrote %d trajectory points to %s", len(out_df), out_csv)

    # Time machine: each window projected through the SVD and placed on the map;
    # the per-window subfield/concept weights are aggregated from each window's
    # own terms through the applied lexicon (evidence-based, not SVD proximity).
    term_to_concept, concept_to_subfield = _lexicon_maps(paths.subfields_json, list(traj.terms))
    windows = build_trajectory_windows(
        traj,
        svd_model=svd_model,
        anchors=anchors,
        term_to_concept=term_to_concept,
        concept_to_subfield=concept_to_subfield,
        report=lambda f, m: ctx.report(0.5 + 0.45 * f, m),
    )
    windows_path = paths.trajectory_windows_json
    windows_path.write_text(json.dumps(windows, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote trajectory windows for %d researcher(s) to %s", len(windows), windows_path)

    # Cohort dynamics figure: mobility of the cohorts of a numeric person attribute.
    if cohort_by and len(out_df):
        terms_df = pd.read_csv(paths.layout_terms_csv) if paths.layout_terms_csv.exists() else None
        cohort_df = compute_cohort_trajectories(out_df, data.meta_ind, cohort_column=cohort_by)
        if cohort_df.empty:
            logger.warning("No numeric %r value in the person roster: no cohort figure.", cohort_by)
        else:
            plot_cohort_trajectories(
                cohort_df,
                fig_path=paths.cohort_trajectories_png,
                terms_df=terms_df,
                legend_title=f"Cohort ({cohort_by})",
            )


def run_lexical_analysis(
    ctx: RunContext,
    *,
    svd_n_components: int | None = None,
    umap_n_neighbors: int | None = None,
    umap_min_dist: float | None = None,
    n_concepts: int | None = None,
    force: bool = False,
) -> None:
    """Run the full lexical pipeline in one call (SVD → Clustering → UMAP → Plots → Trajectories).

    UMAP runs *last* so the projection can be coloured by the term clusters. Subfields
    (draft and apply) are a separate stage between clustering and UMAP — they are
    SVD-based and not run here.
    A convenience wrapper; for interactive use, prefer calling the steps individually so
    each can be inspected and re-run.
    """
    run_svd(ctx, svd_n_components=svd_n_components, force=force)
    run_clustering(ctx, n_concepts=n_concepts, force=force)
    run_umap(ctx, umap_n_neighbors=umap_n_neighbors, umap_min_dist=umap_min_dist, force=force)
    run_lexical_plots(ctx, force=force)
    run_trajectories(ctx, force=force)
