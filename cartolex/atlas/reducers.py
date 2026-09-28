from __future__ import annotations

import importlib.util
import logging
import warnings
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize
from threadpoolctl import threadpool_limits

from .types import Embeddings, LexicalData

logger = logging.getLogger(__name__)


def umap_available() -> bool:
    """True when ``umap-learn`` can be imported.

    UMAP is imported lazily: it drags in numba/llvmlite, compiled wheels a
    network-less consumer sandbox (an offline curation tool, for example) cannot
    install.
    Everything else in this module runs on numpy/scipy/scikit-learn alone.
    """
    try:
        return importlib.util.find_spec("umap") is not None
    except (ImportError, ValueError):  # a finder that refuses the name == not available
        return False


def _umap_module():
    try:
        import umap
    except ImportError as exc:  # pragma: no cover - exercised via umap_available()
        raise ImportError(
            "umap-learn is not installed. Install it for the production layout, or call "
            "compute_umap(..., fallback='tsne') for a t-SNE preview layout."
        ) from exc
    return umap


class AnchoredTSNE:
    """Anchored t-SNE layout of a reference set: the researchers plus the concept anchors.

    Fits a 2-D t-SNE on the reference set; every other point (the terms at fit
    time, projected documents and time-bin fingerprints later) is placed on it by
    its nearest researchers (:mod:`cartolex.atlas.placement`), as on a UMAP map.
    Needs only scikit-learn (no umap-learn / numba) and runs in a couple of
    seconds. Deterministic per ``random_state``. Perplexity follows
    ``2 * n_neighbors`` (clamped to 10–50 and to the sample count).

    A plain *joint* t-SNE of researchers and terms was tried first and fragments
    subfields into satellites — never use it for this map.
    """

    layout_engine = "tsne_anchored"

    def __init__(self, *, n_neighbors: int = 15, metric: str = "cosine", random_state: int = 0):
        self.n_neighbors = int(n_neighbors)
        self.metric = metric
        self.random_state = int(random_state)
        self.embedding_: np.ndarray | None = None
        self.reference_: np.ndarray | None = None

    def get_params(self) -> dict[str, Any]:
        """The constructor arguments of this reducer."""
        return {
            "n_neighbors": self.n_neighbors,
            "metric": self.metric,
            "random_state": self.random_state,
        }

    def fit(self, Z_ref: np.ndarray) -> AnchoredTSNE:
        """Embed the reference set (researchers [+ anchors])."""
        from sklearn.manifold import TSNE

        Z = np.asarray(Z_ref, dtype=float)
        ref = normalize(Z) if self.metric == "cosine" else Z
        n_ref = ref.shape[0]
        perplexity = float(max(2, min(max(10, 2 * self.n_neighbors), 50, n_ref - 1)))
        self.embedding_ = TSNE(
            n_components=2,
            perplexity=perplexity,
            metric="cosine" if self.metric == "cosine" else self.metric,
            init="pca",
            random_state=self.random_state,
        ).fit_transform(ref)
        self.reference_ = ref
        return self


def fit_anchored_tsne(
    Z_ind: np.ndarray,
    Z_terms: np.ndarray,
    anchors: np.ndarray | None,
    *,
    n_neighbors: int,
    metric: str,
    random_state: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Fit :class:`AnchoredTSNE` on researchers (+ *anchors*); place the terms; return both."""
    Zi = np.asarray(Z_ind, dtype=float)
    ref = (
        Zi
        if anchors is None or not len(anchors)
        else np.vstack([Zi, np.asarray(anchors, dtype=float)])
    )
    reducer = AnchoredTSNE(n_neighbors=n_neighbors, metric=metric, random_state=random_state).fit(
        ref
    )
    assert reducer.embedding_ is not None
    umap_ind = reducer.embedding_[: Zi.shape[0]]
    return umap_ind, place_terms(Z_terms, Zi, umap_ind)


def place_terms(Z_terms: np.ndarray, Z_ind: np.ndarray, xy_ind: np.ndarray) -> np.ndarray:
    """The map positions of the terms, placed by their nearest researchers."""
    from .placement import place

    if not len(Z_terms):
        return np.zeros((0, 2))
    return place(Z_terms, Z_ind, xy_ind).xy


def fit_tsne_preview(
    Z_ind: np.ndarray,
    Z_terms: np.ndarray,
    *,
    n_neighbors: int,
    metric: str,
    random_state: int,
    anchor_vectors: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Coordinates only — the same as :func:`fit_anchored_tsne` (kept for callers)."""
    return fit_anchored_tsne(
        Z_ind,
        Z_terms,
        anchor_vectors,
        n_neighbors=n_neighbors,
        metric=metric,
        random_state=random_state,
    )


def opentsne_available() -> bool:
    """True when the optional ``openTSNE`` package can be imported (the ``tsne`` layout)."""
    try:
        return importlib.util.find_spec("openTSNE") is not None
    except (ImportError, ValueError):
        return False


def fit_tsne_layout(
    Z_ind: np.ndarray,
    Z_terms: np.ndarray,
    *,
    perplexity: float = 30.0,
    metric: str = "cosine",
    random_state: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """A t-SNE of the people (openTSNE, FFT-accelerated), the terms placed on it.

    Needs the optional ``openTSNE`` package (``pip install cartolex[tsne]``).
    Runs on one thread with the seed given, so the map is the same on every run;
    the perplexity is capped for small maps. Returns ``(umap_ind, umap_terms)``.
    """
    try:
        from openTSNE import TSNE
    except ImportError as exc:
        raise ImportError(
            "the tsne layout needs the optional openTSNE package: pip install 'cartolex[tsne]'"
        ) from exc

    Zi = np.asarray(Z_ind, dtype=float)
    n = Zi.shape[0]
    eff = float(max(2.0, min(float(perplexity), (n - 1) / 3.0)))
    if eff != perplexity:
        logger.warning("Clamping t-SNE perplexity %.1f → %.1f (%d people).", perplexity, eff, n)
    fit_input = normalize(Zi) if metric == "cosine" else Zi
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning)
        embedding = TSNE(
            n_components=2,
            perplexity=eff,
            metric="euclidean" if metric == "cosine" else metric,
            initialization="pca",
            # exact neighbours on a small map, the bundled Annoy index above: never
            # the optional pynndescent, whose import compiles for seconds
            neighbors="exact" if n < 1000 else "annoy",
            random_state=int(random_state),
            n_jobs=1,
            verbose=False,
        ).fit(fit_input)
    umap_ind = np.asarray(embedding, dtype=float)
    return umap_ind, place_terms(Z_terms, Zi, umap_ind)


def fit_tree_layout(
    Z_ind: np.ndarray, Z_terms: np.ndarray, *, tree: Any, usage: Any
) -> tuple[np.ndarray, np.ndarray]:
    """The theme tree's map of the people (:mod:`cartolex.atlas.tree_layout`), the terms placed on it."""
    from .tree_layout import people_paths, tree_layout

    Zi = np.asarray(Z_ind, dtype=float)
    umap_ind = tree_layout(tree, people_paths(tree, usage, Zi), Zi)
    return umap_ind, place_terms(Z_terms, Zi, umap_ind)


def compute_svd_embeddings(
    data: LexicalData,
    *,
    n_components: int,
    model_path: Path,
    random_state: int = 42,
) -> Embeddings:
    """Reduce the lexical matrix with truncated SVD; return researcher and term embeddings.

    The fitted model is stored at *model_path* (a model descriptor, see
    :mod:`cartolex.atlas.model_files`).
    """
    from cartolex.atlas.model_files import save_svd

    X = data.X
    X_norm = normalize(X, norm="l2", axis=1)
    logger.info("Running TruncatedSVD (PCA-like) on normalised X...")

    n_components = min(n_components, X_norm.shape[0], X_norm.shape[1])
    svd = TruncatedSVD(n_components=n_components, random_state=random_state)
    # Pin BLAS to one thread for the factorization: multi-threaded OpenBLAS/MKL can deadlock when
    # this runs inside a server worker-thread pool (an application froze here while a standalone
    # process ran it in under a second). The matrices are small, so there is no cost.
    with threadpool_limits(limits=1, user_api="blas"):
        Z_ind = svd.fit_transform(X_norm)

    save_svd(svd, model_path)
    logger.info("Saved SVD model to %s", model_path)

    explained = svd.explained_variance_ratio_
    logger.info(
        "Explained variance ratio (first components): %s",
        ", ".join(f"PC{k}: {v:.3f}" for k, v in enumerate(explained[:10], start=1)),
    )
    logger.info("Cumulative (PC1..PC%d): %.3f", len(explained), explained.cumsum()[-1])

    Vt = svd.components_
    S = svd.singular_values_
    Z_terms = Vt.T * S

    return Embeddings(
        Z_ind=Z_ind,
        Z_terms=Z_terms,
        umap_ind=None,
        umap_terms=None,
    )


def compute_umap(
    emb: Embeddings,
    *,
    n_neighbors: int,
    min_dist: float,
    n_components: int,
    metric: str,
    random_state: int,
    n_epochs: int | None = None,
    spread: float = 1.0,
    set_op_mix_ratio: float = 1.0,
    local_connectivity: int = 1,
    repulsion_strength: float = 1.0,
    negative_sample_rate: int = 5,
    term_cluster_labels: np.ndarray | None = None,
    target_weight: float = 0.3,
    layout: str = "researcher",
    anchor_vectors: np.ndarray | None = None,
    fallback: str | None = None,
    tsne_perplexity: float = 30.0,
    tree: Any = None,
    usage: Any = None,
) -> Embeddings:
    """Project the SVD embeddings down to 2D with UMAP (or another layout).

    ``fallback="tsne"`` opts in to the anchored t-SNE layout as a *preview* when a
    UMAP layout was requested but umap-learn is not importable. Without the
    opt-in a missing umap-learn raises.

    The layouts (``layout``):

    - ``"researcher"`` (default) — fit UMAP on the researchers
      (:func:`fit_researcher_umap`); the researcher geometry shapes the map.
    - ``"researcher_concepts"`` — fit on the researchers PLUS the few dozen
      concept centroids passed as ``anchor_vectors`` (:func:`fit_anchored_umap`),
      landmarks shared by researchers and terms. Falls back to ``"researcher"``
      when no anchors are provided.
    - ``"joint"`` — co-embed researchers and terms in one fit
      (:func:`fit_joint_umap`), optionally semi-supervised by ``term_cluster_labels``.
    - ``"tsne_anchored"`` — no umap-learn needed: :class:`AnchoredTSNE` on the
      researchers + concept anchors. ``min_dist`` does not apply.
    - ``"tsne"`` — a t-SNE of the researchers with openTSNE (:func:`fit_tsne_layout`,
      ``tsne_perplexity``), the layout that scales to the largest maps.
    - ``"tree"`` — the theme tree's map (:func:`fit_tree_layout`): needs the applied
      *tree* and the people × keywords *usage*.

    Except in the joint layout, the terms are then *placed* on the researchers'
    map by their nearest researchers (:mod:`cartolex.atlas.placement`), as every
    later point is (projected documents, the trajectories' time bins): no
    fitted model is kept.
    """
    if layout == "tsne":
        logger.info("Computing a t-SNE layout of the researchers (openTSNE, terms placed)...")
        emb.umap_ind, emb.umap_terms = fit_tsne_layout(
            emb.Z_ind,
            emb.Z_terms,
            perplexity=tsne_perplexity,
            metric=metric,
            random_state=random_state,
        )
        return emb
    if layout == "tree":
        if tree is None or usage is None:
            raise ValueError("the tree layout needs the applied theme tree and the usage matrix")
        logger.info("Computing the theme tree's layout (themes first, researchers inside)...")
        emb.umap_ind, emb.umap_terms = fit_tree_layout(
            emb.Z_ind, emb.Z_terms, tree=tree, usage=usage
        )
        return emb
    if layout == "tsne_anchored" or (fallback == "tsne" and not umap_available()):
        if layout != "tsne_anchored":
            logger.warning(
                "umap-learn is unavailable — computing an anchored t-SNE PREVIEW layout "
                "(researchers + concept anchors, terms kNN-placed); not the requested UMAP map."
            )
        else:
            logger.info("Computing anchored t-SNE layout (researchers + concept anchors)...")
        umap_ind, umap_terms = fit_anchored_tsne(
            emb.Z_ind,
            emb.Z_terms,
            anchor_vectors,
            n_neighbors=n_neighbors,
            metric=metric,
            random_state=random_state,
        )
        emb.umap_ind = umap_ind
        emb.umap_terms = umap_terms
        return emb

    if layout == "researcher_concepts" and (anchor_vectors is None or not len(anchor_vectors)):
        logger.warning(
            "researcher_concepts layout requested but no concept anchors available "
            "(no applied hierarchy and no term clusters) — falling back to 'researcher'."
        )
        layout = "researcher"

    if layout == "researcher_concepts":
        assert anchor_vectors is not None
        logger.info(
            "Computing UMAP on SVD space (researchers + %d concept anchors, terms placed)...",
            len(anchor_vectors),
        )
        umap_ind, umap_terms = fit_anchored_umap(
            emb.Z_ind,
            emb.Z_terms,
            anchor_vectors,
            n_neighbors=n_neighbors,
            min_dist=min_dist,
            n_components=n_components,
            metric=metric,
            random_state=random_state,
            n_epochs=n_epochs,
            spread=spread,
            set_op_mix_ratio=set_op_mix_ratio,
            local_connectivity=local_connectivity,
            repulsion_strength=repulsion_strength,
            negative_sample_rate=negative_sample_rate,
        )
    elif layout == "researcher":
        logger.info("Computing UMAP on SVD space (researcher-fit, terms placed)...")
        umap_ind, umap_terms = fit_researcher_umap(
            emb.Z_ind,
            emb.Z_terms,
            n_neighbors=n_neighbors,
            min_dist=min_dist,
            n_components=n_components,
            metric=metric,
            random_state=random_state,
            n_epochs=n_epochs,
            spread=spread,
            set_op_mix_ratio=set_op_mix_ratio,
            local_connectivity=local_connectivity,
            repulsion_strength=repulsion_strength,
            negative_sample_rate=negative_sample_rate,
        )
    else:
        logger.info("Computing UMAP on SVD space (researchers + terms jointly)...")
        umap_ind, umap_terms = fit_joint_umap(
            emb.Z_ind,
            emb.Z_terms,
            n_neighbors=n_neighbors,
            min_dist=min_dist,
            n_components=n_components,
            metric=metric,
            random_state=random_state,
            n_epochs=n_epochs,
            spread=spread,
            set_op_mix_ratio=set_op_mix_ratio,
            local_connectivity=local_connectivity,
            repulsion_strength=repulsion_strength,
            negative_sample_rate=negative_sample_rate,
            term_cluster_labels=term_cluster_labels,
            target_weight=target_weight,
        )

    emb.umap_ind = umap_ind
    emb.umap_terms = umap_terms
    return emb


def _fit_umap(
    params: dict[str, Any], fit_input: np.ndarray, *, fit_target: np.ndarray | None = None
) -> np.ndarray:
    """Fit ``umap.UMAP(**params)`` on *fit_input* on one BLAS thread; return the embedding."""
    umap = _umap_module()
    with threadpool_limits(limits=1), warnings.catch_warnings():
        # A seeded UMAP always runs on one thread; its notice says so on every fit.
        warnings.filterwarnings("ignore", message="n_jobs value", category=UserWarning)
        return umap.UMAP(**params).fit_transform(fit_input, y=fit_target)


def _plain(value: Any) -> Any:
    """A numpy scalar as the Python value it holds; anything else unchanged."""
    return value.item() if isinstance(value, np.generic) else value


def _umap_params(**params: Any) -> dict[str, Any]:
    """The UMAP constructor arguments of a layout, as plain values (stored with the model)."""
    return {name: _plain(value) for name, value in params.items()}


def fit_joint_umap(
    Z_ind: np.ndarray,
    Z_terms: np.ndarray,
    *,
    n_neighbors: int,
    min_dist: float,
    n_components: int,
    metric: str,
    random_state: int,
    n_epochs: int | None = None,
    spread: float = 1.0,
    set_op_mix_ratio: float = 1.0,
    local_connectivity: int = 1,
    repulsion_strength: float = 1.0,
    negative_sample_rate: int = 5,
    term_cluster_labels: np.ndarray | None = None,
    target_weight: float = 0.3,
):
    """Fit one UMAP on the stacked researcher+term SVD space; return split coords.

    Used by the layout stage for the joint recipe. Returns ``(umap_ind, umap_terms)``.

    When ``term_cluster_labels`` (one cluster id per term, ``-1`` = noise/unknown)
    is given, UMAP runs **semi-supervised**: terms sharing a cluster are nudged
    together while researchers stay unlabelled (``-1``). ``target_weight`` in
    ``[0, 1]`` sets how strongly the layout respects the clusters (0 = ignore,
    1 = dominate); the default 0.3 is a gentle nudge that keeps cluster integrity
    without overriding the data geometry.
    """
    n_ind = Z_ind.shape[0]
    n_terms = Z_terms.shape[0]
    joint = np.vstack([Z_ind, Z_terms])

    # UMAP requires n_neighbors < n_samples; clamp on the *joint* sample count.
    n_samples = joint.shape[0]
    eff_n_neighbors = max(2, min(n_neighbors, n_samples - 1))
    if eff_n_neighbors != n_neighbors:
        logger.warning(
            "Clamping UMAP n_neighbors %d → %d (only %d joint points).",
            n_neighbors,
            eff_n_neighbors,
            n_samples,
        )

    y = None
    if term_cluster_labels is not None:
        # Researchers are unlabelled (-1); terms carry their SVD cluster id.
        y = np.concatenate(
            [np.full(n_ind, -1, dtype=int), np.asarray(term_cluster_labels, dtype=int)]
        )
        n_labelled = int((y[n_ind:] >= 0).sum())
        logger.info(
            "Semi-supervised UMAP: nudging %d labelled terms (target_weight=%.2f).",
            n_labelled,
            target_weight,
        )

    params = _umap_params(
        n_neighbors=eff_n_neighbors,
        min_dist=min_dist,
        n_components=n_components,
        metric=metric,
        random_state=random_state,
        n_epochs=n_epochs,
        spread=spread,
        set_op_mix_ratio=set_op_mix_ratio,
        local_connectivity=local_connectivity,
        repulsion_strength=repulsion_strength,
        negative_sample_rate=negative_sample_rate,
        target_weight=target_weight,
    )
    joint_2d = _fit_umap(params, joint, fit_target=y)
    return joint_2d[:n_ind], joint_2d[n_ind : n_ind + n_terms]


def fit_researcher_umap(
    Z_ind: np.ndarray,
    Z_terms: np.ndarray,
    *,
    n_neighbors: int,
    min_dist: float,
    n_components: int,
    metric: str,
    random_state: int,
    n_epochs: int | None = None,
    spread: float = 1.0,
    set_op_mix_ratio: float = 1.0,
    local_connectivity: int = 1,
    repulsion_strength: float = 1.0,
    negative_sample_rate: int = 5,
):
    """Fit UMAP on **researchers only**, then place the terms on it.

    The alternative to :func:`fit_joint_umap`. Stacking every term into the fit
    lets the ~10× more numerous term points dominate the optimisation, so
    researchers collapse into a diffuse central blob. Fitting the manifold on the
    researchers alone lets the *researcher* geometry shape the map; the terms are
    then placed by their nearest researchers (:func:`place_terms`). Returns
    ``(umap_ind, umap_terms)``.

    Terms are not in the fit, so there is no term-cluster supervision here — the
    layout is unsupervised and the map is coloured by the high-dimensional term
    clusters.
    """
    n_samples = Z_ind.shape[0]
    eff_n_neighbors = max(2, min(n_neighbors, n_samples - 1))
    if eff_n_neighbors != n_neighbors:
        logger.warning(
            "Clamping UMAP n_neighbors %d → %d (only %d researchers).",
            n_neighbors,
            eff_n_neighbors,
            n_samples,
        )

    params = _umap_params(
        n_neighbors=eff_n_neighbors,
        min_dist=min_dist,
        n_components=n_components,
        metric=metric,
        random_state=random_state,
        n_epochs=n_epochs,
        spread=spread,
        set_op_mix_ratio=set_op_mix_ratio,
        local_connectivity=local_connectivity,
        repulsion_strength=repulsion_strength,
        negative_sample_rate=negative_sample_rate,
    )
    umap_ind = _fit_umap(params, Z_ind)
    return umap_ind, place_terms(Z_terms, Z_ind, umap_ind)


def fit_anchored_umap(
    Z_ind: np.ndarray,
    Z_terms: np.ndarray,
    anchors: np.ndarray,
    *,
    n_neighbors: int,
    min_dist: float,
    n_components: int,
    metric: str,
    random_state: int,
    n_epochs: int | None = None,
    spread: float = 1.0,
    set_op_mix_ratio: float = 1.0,
    local_connectivity: int = 1,
    repulsion_strength: float = 1.0,
    negative_sample_rate: int = 5,
):
    """Fit UMAP on researchers PLUS concept ``anchors``, then place the terms.

    A middle ground between :func:`fit_researcher_umap` and :func:`fit_joint_umap`
    (thousands of terms wash out the researcher geometry): the few dozen concept
    centroids are landmarks shared by both geometries, so researchers knit to
    the anchors of the concepts they write about. Returns
    ``(umap_ind, umap_terms)``; the anchor rows only shape the fit, and the terms
    are placed by their nearest researchers (:func:`place_terms`).
    """
    n_ind = Z_ind.shape[0]
    stacked = np.vstack([Z_ind, np.asarray(anchors, dtype=Z_ind.dtype)])
    n_samples = stacked.shape[0]
    eff_n_neighbors = max(2, min(n_neighbors, n_samples - 1))
    if eff_n_neighbors != n_neighbors:
        logger.warning(
            "Clamping UMAP n_neighbors %d → %d (%d researchers + %d anchors).",
            n_neighbors,
            eff_n_neighbors,
            n_ind,
            len(anchors),
        )

    params = _umap_params(
        n_neighbors=eff_n_neighbors,
        min_dist=min_dist,
        n_components=n_components,
        metric=metric,
        random_state=random_state,
        n_epochs=n_epochs,
        spread=spread,
        set_op_mix_ratio=set_op_mix_ratio,
        local_connectivity=local_connectivity,
        repulsion_strength=repulsion_strength,
        negative_sample_rate=negative_sample_rate,
    )
    umap_ind = _fit_umap(params, stacked)[:n_ind]
    return umap_ind, place_terms(Z_terms, Z_ind, umap_ind)
