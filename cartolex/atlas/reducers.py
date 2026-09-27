from __future__ import annotations

import importlib.util
import logging
import warnings
from dataclasses import dataclass
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
    """Anchored t-SNE layout — a genuine reducer with a k-NN ``transform``.

    Fits a 2-D t-SNE on a small *reference set* (the researchers plus the concept
    anchors), then places any other point — the terms at fit time, projected
    documents and time-bin fingerprints later — by cosine k-nearest-neighbour regression
    from SVD space onto the fitted 2-D coordinates. That is also what the
    production ``researcher_concepts`` UMAP does (fit on researchers + anchors,
    ``transform`` the terms), so the two layouts share their neighbourhoods; the
    difference is that this one needs only scikit-learn (no umap-learn /
    numba) and runs in a couple of seconds. Deterministic per ``random_state``.
    Perplexity follows ``2 * n_neighbors`` (clamped to 10–50 and to the sample
    count).

    A plain *joint* t-SNE of researchers and terms was tried first and fragments
    subfields into satellites — never use it for this map.

    A stored reducer is rebuilt with :meth:`from_fitted` from its parameters,
    its reference set (as fitted) and its 2-D coordinates: the k-NN regressor
    is fitted again on those arrays, which gives the same ``transform``.
    """

    layout_engine = "tsne_anchored"

    def __init__(
        self,
        *,
        n_neighbors: int = 15,
        metric: str = "cosine",
        random_state: int = 0,
        jitter: float = 0.02,
    ) -> None:
        self.n_neighbors = int(n_neighbors)
        self.metric = metric
        self.random_state = int(random_state)
        self.jitter = float(jitter)
        self.embedding_: np.ndarray | None = None
        self.reference_: np.ndarray | None = None
        self._reg = None
        self._extent: np.ndarray | None = None

    def get_params(self) -> dict[str, Any]:
        """The constructor arguments of this reducer."""
        return {
            "n_neighbors": self.n_neighbors,
            "metric": self.metric,
            "random_state": self.random_state,
            "jitter": self.jitter,
        }

    def _prep(self, Z: np.ndarray) -> np.ndarray:
        Z = np.asarray(Z, dtype=float)
        return normalize(Z) if self.metric == "cosine" else Z

    def fit(self, Z_ref: np.ndarray) -> AnchoredTSNE:
        """Embed the reference set (researchers [+ anchors]) and fit the k-NN regressor."""
        from sklearn.manifold import TSNE

        ref = self._prep(Z_ref)
        n_ref = ref.shape[0]
        perplexity = float(max(2, min(max(10, 2 * self.n_neighbors), 50, n_ref - 1)))
        coords = TSNE(
            n_components=2,
            perplexity=perplexity,
            metric="cosine" if self.metric == "cosine" else self.metric,
            init="pca",
            random_state=self.random_state,
        ).fit_transform(ref)
        return self._set_fitted(ref, coords)

    def _set_fitted(self, ref: np.ndarray, coords: np.ndarray) -> AnchoredTSNE:
        """Fit the k-NN regressor from the prepared reference set onto its 2-D coordinates."""
        from sklearn.neighbors import KNeighborsRegressor

        k = max(1, min(self.n_neighbors, ref.shape[0]))
        self._reg = KNeighborsRegressor(n_neighbors=k, weights="distance", metric="cosine").fit(
            ref, coords
        )
        self.reference_ = ref
        self.embedding_ = coords
        self._extent = coords.max(axis=0) - coords.min(axis=0)
        return self

    @classmethod
    def from_fitted(
        cls, reference: np.ndarray, embedding: np.ndarray, **params: Any
    ) -> AnchoredTSNE:
        """The reducer of a stored fit, without running t-SNE again.

        *reference* is the reference set as fitted (``reference_``, already
        normalised for the cosine metric), *embedding* its 2-D coordinates
        (``embedding_``) and *params* the constructor arguments
        (:meth:`get_params`).
        """
        return cls(**params)._set_fitted(np.asarray(reference), np.asarray(embedding))

    def transform(self, Z: np.ndarray, *, jitter: bool = False) -> np.ndarray:
        """Place new SVD-space points onto the fitted map (k-NN regression).

        ``jitter=True`` adds a small seeded scatter so points that share the same
        neighbours (terms used by a single researcher) do not stack on one pixel;
        projected documents and trajectories are placed without it.
        """
        if self._reg is None or self._extent is None:
            raise RuntimeError("AnchoredTSNE.transform called before fit")
        coords = np.asarray(self._reg.predict(self._prep(Z)), dtype=float)
        if jitter and self.jitter > 0:
            rng = np.random.default_rng(self.random_state)
            coords = coords + rng.normal(0.0, self.jitter * self._extent, coords.shape)
        return coords


def fit_anchored_tsne(
    Z_ind: np.ndarray,
    Z_terms: np.ndarray,
    anchors: np.ndarray | None,
    *,
    n_neighbors: int,
    metric: str,
    random_state: int,
) -> tuple[np.ndarray, np.ndarray, AnchoredTSNE]:
    """Fit :class:`AnchoredTSNE` on researchers (+ *anchors*), place the terms; return all three."""
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
    umap_terms = reducer.transform(Z_terms, jitter=True)
    return umap_ind, umap_terms, reducer


def fit_tsne_preview(
    Z_ind: np.ndarray,
    Z_terms: np.ndarray,
    *,
    n_neighbors: int,
    metric: str,
    random_state: int,
    anchor_vectors: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Coordinates only — thin wrapper over :func:`fit_anchored_tsne` (kept for callers)."""
    ind, terms, _ = fit_anchored_tsne(
        Z_ind,
        Z_terms,
        anchor_vectors,
        n_neighbors=n_neighbors,
        metric=metric,
        random_state=random_state,
    )
    return ind, terms


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
    model_path: Path,
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
) -> Embeddings:
    """Project the SVD embeddings down to 2D with UMAP.

    ``fallback="tsne"`` opts in to the anchored t-SNE layout as a *preview* when a
    UMAP layout was requested but umap-learn is not importable (the stored model
    is then an :class:`AnchoredTSNE`). Without the opt-in a missing umap-learn raises.

    Three layouts (``layout``):

    - ``"researcher"`` (default) — fit UMAP on the researchers, then ``transform``
      the terms onto it (:func:`fit_researcher_umap`). The researcher geometry
      shapes the map and the ~10× more numerous terms can no longer wash it into a
      diffuse blob; with cosine + small ``min_dist`` the transformed terms
      intermingle rather than forming the old corona. The map is coloured by the
      high-dimensional term clusters. ``term_cluster_labels``/``target_weight`` do
      not apply (terms are not in the fit).
    - ``"researcher_concepts"`` — fit on the researchers PLUS the few dozen
      concept centroids passed as ``anchor_vectors`` (:func:`fit_anchored_umap`),
      then ``transform`` the terms. The anchors are landmarks shared by both
      geometries, so the projected terms land next to their concept instead of
      forming corona shells around the researcher islands. Falls back to
      ``"researcher"`` when no anchors are provided.
    - ``"joint"`` — co-embed researchers and terms in one ``fit_transform``
      (:func:`fit_joint_umap`), optionally semi-supervised by ``term_cluster_labels``.
    - ``"tsne_anchored"`` — no umap-learn needed: :class:`AnchoredTSNE` on the
      researchers + concept anchors, terms (and later any new point) placed by
      cosine k-NN regression. Fast, deterministic, same neighbourhoods as the
      anchored UMAP; ``min_dist`` does not apply.

    Any fitted reducer still supports ``transform`` for new points (e.g. the
    time-bin fingerprints of the trajectories). The model is stored at
    *model_path* (see :func:`cartolex.atlas.model_files.save_layout_model`).
    """
    from cartolex.atlas.model_files import save_layout_model

    if layout == "tsne_anchored" or (fallback == "tsne" and not umap_available()):
        if layout != "tsne_anchored":
            logger.warning(
                "umap-learn is unavailable — computing an anchored t-SNE PREVIEW layout "
                "(researchers + concept anchors, terms kNN-placed); not the requested UMAP map."
            )
        else:
            logger.info("Computing anchored t-SNE layout (researchers + concept anchors)...")
        umap_ind, umap_terms, reducer = fit_anchored_tsne(
            emb.Z_ind,
            emb.Z_terms,
            anchor_vectors,
            n_neighbors=n_neighbors,
            metric=metric,
            random_state=random_state,
        )
        save_layout_model(reducer, model_path)
        logger.info("Saved anchored t-SNE model to %s", model_path)
        emb.umap_ind = umap_ind
        emb.umap_terms = umap_terms
        return emb

    if layout == "researcher_concepts" and (anchor_vectors is None or not len(anchor_vectors)):
        logger.warning(
            "researcher_concepts layout requested but no concept anchors available "
            "(no applied hierarchy and no term clusters) — falling back to 'researcher'."
        )
        layout = "researcher"

    model: UmapModel
    if layout == "researcher_concepts":
        assert anchor_vectors is not None
        logger.info(
            "Computing UMAP on SVD space (researchers + %d concept anchors, terms transformed)...",
            len(anchor_vectors),
        )
        umap_ind, umap_terms, model = fit_anchored_umap(
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
        logger.info("Computing UMAP on SVD space (researcher-fit, terms transformed)...")
        umap_ind, umap_terms, model = fit_researcher_umap(
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
        umap_ind, umap_terms, model = fit_joint_umap(
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

    save_layout_model(model, model_path)
    logger.info("Saved UMAP model to %s", model_path)

    emb.umap_ind = umap_ind
    emb.umap_terms = umap_terms
    return emb


@dataclass(eq=False)
class UmapModel:
    """A fitted UMAP with everything a re-fit needs.

    UMAP has no stored form other than pickle, so a stored UMAP model keeps
    what made it instead: the constructor arguments (``params``, the random
    seed among them), the fitted arrays (``fit_input`` and, for a
    semi-supervised fit, ``fit_target``) and the points transformed right
    after the fit (``replay_input``, the terms). :meth:`fit` runs the same
    calls in the same order, on one BLAS thread, so the same libraries give
    the same model, bit for bit; ``embedding`` and ``replay_output`` are the
    results a re-fit is checked against.
    """

    reducer: Any
    params: dict[str, Any]
    fit_input: np.ndarray
    fit_target: np.ndarray | None
    replay_input: np.ndarray | None
    embedding: np.ndarray
    replay_output: np.ndarray | None
    #: Set when a stored model is re-fitted: ``None`` if the re-fit reproduced the stored
    #: map exactly, else the largest displacement (relative to the map's extent) and why.
    refit_deviation: dict[str, Any] | None = None

    layout_engine = "umap"

    @classmethod
    def fit(
        cls,
        params: dict[str, Any],
        fit_input: np.ndarray,
        *,
        fit_target: np.ndarray | None = None,
        replay_input: np.ndarray | None = None,
    ) -> UmapModel:
        """Fit ``umap.UMAP(**params)`` on *fit_input*, then transform *replay_input*."""
        umap = _umap_module()
        with threadpool_limits(limits=1), warnings.catch_warnings():
            # A seeded UMAP always runs on one thread; its notice says so on every fit.
            warnings.filterwarnings("ignore", message="n_jobs value", category=UserWarning)
            reducer = umap.UMAP(**params)
            embedding = reducer.fit_transform(fit_input, y=fit_target)
            replay_output = reducer.transform(replay_input) if replay_input is not None else None
        return cls(
            reducer=reducer,
            params=dict(params),
            fit_input=fit_input,
            fit_target=fit_target,
            replay_input=replay_input,
            embedding=embedding,
            replay_output=replay_output,
        )

    def transform(self, Z: np.ndarray) -> np.ndarray:
        """Place new points of the fitted space on the map (``umap.UMAP.transform``)."""
        return self.reducer.transform(Z)


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

    Used by the layout stage for the joint recipe. Returns
    ``(umap_ind, umap_terms, model)``, the model a :class:`UmapModel`.

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
    model = UmapModel.fit(params, joint, fit_target=y)
    joint_2d = model.embedding
    return joint_2d[:n_ind], joint_2d[n_ind : n_ind + n_terms], model


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
    """Fit UMAP on **researchers only**, then ``transform`` the terms onto it.

    The alternative to :func:`fit_joint_umap`. Stacking every term into the fit
    lets the ~10× more numerous term points dominate the optimisation, so
    researchers collapse into a diffuse central blob. Fitting the manifold on the
    researchers alone and projecting the terms in via ``transform`` instead lets
    the *researcher* geometry shape the map; with cosine distance and a small
    ``n_neighbors``/``min_dist`` the transformed terms intermingle rather than
    forming the old "corona" rim (verified per-run by the embedding diagnostics).
    Returns ``(umap_ind, umap_terms, model)`` like :func:`fit_joint_umap`; the
    fitted model transforms new points (terms here, the trajectories' time bins
    later).

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
    model = UmapModel.fit(params, Z_ind, replay_input=Z_terms)
    return model.embedding, model.replay_output, model


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
    """Fit UMAP on researchers PLUS concept ``anchors``, then ``transform`` the terms.

    A middle ground between :func:`fit_researcher_umap` (terms projected onto a
    researcher-only manifold tend to land in shells — the "corona") and
    :func:`fit_joint_umap` (thousands of terms wash out the researcher
    geometry). The few dozen concept centroids are landmarks shared by BOTH
    geometries: a researcher's SVD vector is a mixture of term vectors, and
    each anchor is the term-side summary of one such mixture, so researchers
    knit to the anchors of the concepts they write about and the transformed
    terms then land next to their concept anchor instead of on a rim.

    Returns ``(umap_ind, umap_terms, model)``; the anchor rows only shape the
    fit and are dropped from the returned researcher embedding. The fitted
    model transforms new points (terms here, the trajectories' time bins later).
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
    model = UmapModel.fit(params, stacked, replay_input=Z_terms)
    return model.embedding[:n_ind], model.replay_output, model
