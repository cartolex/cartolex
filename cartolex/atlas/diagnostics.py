from __future__ import annotations

import logging

import numpy as np
from sklearn.manifold import trustworthiness
from sklearn.metrics import silhouette_score
from sklearn.neighbors import NearestNeighbors

logger = logging.getLogger(__name__)

# Cap the point count for the O(n²) trustworthiness computation; subsample above.
_MAX_TRUSTWORTHINESS_POINTS = 3000


def neighborhood_preservation(
    coords2d: np.ndarray,
    z_high: np.ndarray,
    *,
    k: int = 15,
    high_metric: str = "cosine",
    random_state: int = 0,
) -> float:
    """High-D faithfulness of a 2D layout: kNN overlap between 2D and high-D.

    For each point, the fraction of its ``k`` nearest high-D neighbours that are
    also among its ``k`` nearest neighbours in 2D, averaged over all points
    (``0``..``1``; higher = more faithful). Unlike trustworthiness (rank-based)
    this is a plain set overlap, and unlike :func:`cluster_separation` it needs no
    labels — so the tuner can reward layouts that keep real high-D neighbourhoods
    together (which is what makes the in-2D clusters split along true theme seams).
    Subsamples above :data:`_MAX_TRUSTWORTHINESS_POINTS` for the O(n²) kNN.
    """
    coords2d = np.asarray(coords2d, dtype=float)
    z_high = np.asarray(z_high, dtype=float)
    n = coords2d.shape[0]
    if n > _MAX_TRUSTWORTHINESS_POINTS:
        rng = np.random.default_rng(random_state)
        sample = rng.choice(n, size=_MAX_TRUSTWORTHINESS_POINTS, replace=False)
        coords2d, z_high = coords2d[sample], z_high[sample]
        n = coords2d.shape[0]
    eff_k = min(k, n - 1)
    if eff_k < 1:
        return 0.0
    hi = (
        NearestNeighbors(n_neighbors=eff_k + 1, metric=high_metric)
        .fit(z_high)
        .kneighbors(z_high, return_distance=False)[:, 1:]
    )
    lo = (
        NearestNeighbors(n_neighbors=eff_k + 1)
        .fit(coords2d)
        .kneighbors(coords2d, return_distance=False)[:, 1:]
    )
    overlap = [len(set(hi[i]) & set(lo[i])) / eff_k for i in range(n)]
    return float(np.mean(overlap))


def cluster_separation(coords2d: np.ndarray, labels: np.ndarray) -> float | None:
    """How cleanly the high-D clusters separate in a 2D layout (silhouette).

    Measures the visual quality of colouring the map by the high-D term clusters:
    the silhouette of the labelled points in 2D (cosine→euclidean is moot in 2D).
    Noise (``label < 0``) is excluded. Returns ``None`` when fewer than two
    clusters survive (silhouette undefined) so callers can treat it as "no signal".
    """
    coords2d = np.asarray(coords2d, dtype=float)
    labels = np.asarray(labels)
    mask = labels >= 0
    if mask.sum() < 2 or len(set(labels[mask].tolist())) < 2:
        return None
    return float(silhouette_score(coords2d[mask], labels[mask]))


def embedding_diagnostics(
    coords2d: np.ndarray,
    is_term: np.ndarray,
    z_high: np.ndarray,
    *,
    k: int = 15,
    high_metric: str = "cosine",
    random_state: int = 0,
) -> dict:
    """Quantify a joint researcher/term 2D embedding.

    Three orthogonal questions, each a single interpretable number:

    - **mixing** — do researchers and keywords intermingle, or segregate? For each
      point we look at its ``k`` nearest 2D neighbours and the fraction that are of
      the *opposite* type, normalised by the fraction expected under random mixing.
      ``1.0`` ≈ as intermingled as random; ``→0`` ≈ fully segregated (a corona has
      almost no cross-type neighbours at the rim).
    - **corona** — are terms pushed to the rim while researchers sit inside? We take
      the ratio of the median term radius to the median researcher radius about the
      researcher centroid. ``~1`` is healthy; ``≫1`` means a term halo (corona).
    - **trustworthiness** — does the 2D map preserve the high-dimensional (cosine)
      neighbourhoods, or did mixing come at the cost of destroying structure?
      sklearn's trustworthiness in ``[0, 1]``; higher is more faithful.

    Parameters
    ----------
    coords2d:
        ``(n, 2)`` 2D embedding coordinates.
    is_term:
        ``(n,)`` boolean/0-1 mask — ``True`` for terms, ``False`` for researchers,
        aligned row-wise with ``coords2d`` and ``z_high``.
    z_high:
        ``(n, d)`` high-dimensional SVD coordinates aligned with ``coords2d``.
    """
    coords2d = np.asarray(coords2d, dtype=float)
    is_term = np.asarray(is_term).astype(bool)
    n = coords2d.shape[0]
    n_term = int(is_term.sum())
    n_res = int((~is_term).sum())

    out: dict = {
        "n_points": n,
        "n_researchers": n_res,
        "n_terms": n_term,
        "k": k,
    }
    if n_term == 0 or n_res == 0 or n < k + 2:
        out["note"] = "Not enough points of both types for diagnostics."
        return out

    # ── Mixing: opposite-type fraction among kNN, normalised by random baseline ──
    eff_k = min(k, n - 1)
    nn = NearestNeighbors(n_neighbors=eff_k + 1).fit(coords2d)
    _, idx = nn.kneighbors(coords2d)
    neigh = idx[:, 1:]  # drop self
    neigh_is_term = is_term[neigh]  # (n, eff_k)
    opposite = np.where(is_term[:, None], ~neigh_is_term, neigh_is_term)  # opposite-type neighbour?
    opp_frac = opposite.mean(axis=1)  # per-point opposite fraction

    # Baseline opposite fraction under random placement (depends on point's type).
    base_res = n_term / (n - 1)  # a researcher's chance a neighbour is a term
    base_term = n_res / (n - 1)  # a term's chance a neighbour is a researcher
    baseline = np.where(is_term, base_term, base_res)
    mixing_per_point = np.divide(
        opp_frac, baseline, out=np.zeros_like(opp_frac), where=baseline > 0
    )
    out["mixing_index"] = float(np.clip(mixing_per_point.mean(), 0.0, None))
    out["term_neighbours_of_researchers"] = float(opp_frac[~is_term].mean())
    out["researcher_neighbours_of_terms"] = float(opp_frac[is_term].mean())

    # ── Corona: term vs researcher radius about the researcher centroid ──────────
    centroid = coords2d[~is_term].mean(axis=0)
    radii = np.linalg.norm(coords2d - centroid, axis=1)
    med_res = float(np.median(radii[~is_term])) or 1e-9
    med_term = float(np.median(radii[is_term]))
    out["radial_ratio"] = float(med_term / med_res)
    out["corona"] = bool(out["radial_ratio"] > 1.4 and out["mixing_index"] < 0.5)

    # ── Trustworthiness of the 2D map vs high-dim cosine neighbourhoods ──────────
    if n > _MAX_TRUSTWORTHINESS_POINTS:
        rng = np.random.default_rng(random_state)
        sample = rng.choice(n, size=_MAX_TRUSTWORTHINESS_POINTS, replace=False)
        z_s, c_s = z_high[sample], coords2d[sample]
    else:
        z_s, c_s = z_high, coords2d
    try:
        out["trustworthiness"] = float(
            trustworthiness(z_s, c_s, n_neighbors=min(k, z_s.shape[0] - 1), metric=high_metric)
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Trustworthiness failed: %s", exc)
        out["trustworthiness"] = None

    # ── Single composite map-quality score (high is good) ────────────────────────
    tw = out["trustworthiness"] if out["trustworthiness"] is not None else 0.0
    out["quality_score"] = float(
        min(out["mixing_index"], 1.0) - max(0.0, out["radial_ratio"] - 1.0) + tw
    )
    return out
