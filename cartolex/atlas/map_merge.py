# SPDX-License-Identifier: MIT
"""Pooled multi-cohort matrix assembly and joint embedding (the pooled geometry).

Given N per-cohort inputs (plain-TF researcher×term matrices + vocabularies,
from map bundles or in memory) and a
:class:`~cartolex.atlas.reconcile.ReconciliationTable`, this module builds the
joint researcher×sense matrix and its embedding:

- :func:`assemble_joint_matrix` — pool the **plain-TF** tracks onto the merged
  sense vocabulary V*. Only ``X_tf`` may be pooled: the boosted ``X`` carries
  per-cohort IDF, which would give identical terms cohort-dependent weights.
- :func:`weight_matrix` — sublinear TF · joint IDF (pooled or macro-averaged) ·
  length bonus (the single-cohort pipeline's ``score * (1 + α(L−1))``) · row L2.
- :func:`balanced_svd` — fit ``TruncatedSVD`` on a **cohort-balanced,
  deterministic** subsample (big cohorts must not own the variance axes), then
  transform every researcher; senses embed as ``Vt.T · S`` exactly like
  :func:`cartolex.atlas.reducers.compute_svd_embeddings`.
- :func:`anchor_concepts` — re-anchor each cohort's *curated* concepts as the
  (L2-normalized) centroids of their member senses in the joint space; these are
  the ``researcher_concepts`` UMAP anchors and the concept scaffold.
- :func:`researcher_concept_weights` — evidence-based researcher→concept weights
  (the `researcher_group_weights` philosophy: a researcher weighs on a concept
  only through terms they actually use).
- :func:`procrustes_residual` — a diagnostic of how far two cohorts' own
  term spaces are from rigidly compatible on their shared senses.

Researcher identity: rows are namespaced ``{cohort_id}:{local_id}`` — local
opaque ids never collide across cohorts and provenance stays a public facet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize
from threadpoolctl import threadpool_limits

from .reconcile import ReconciliationTable
from .types import to_dense

logger = logging.getLogger(__name__)

__all__ = [
    "CohortInput",
    "JointData",
    "assemble_joint_matrix",
    "weight_matrix",
    "balanced_svd",
    "anchor_concepts",
    "researcher_concept_weights",
    "procrustes_residual",
]


@dataclass
class CohortInput:
    """One cohort's anonymized inputs for the merge (plain-TF track only)."""

    cohort_id: str
    terms: list[str]
    X_tf: np.ndarray
    researcher_ids: list[str]
    units: list[str] | None = None

    def __post_init__(self) -> None:
        X = to_dense(self.X_tf)
        if X.ndim != 2 or X.shape != (len(self.researcher_ids), len(self.terms)):
            raise ValueError(
                f"Cohort {self.cohort_id!r}: X_tf shape {X.shape} does not match "
                f"({len(self.researcher_ids)} researchers, {len(self.terms)} terms)"
            )
        self.X_tf = X
        if self.units is not None and len(self.units) != len(self.researcher_ids):
            raise ValueError(f"Cohort {self.cohort_id!r}: units misaligned with researchers")


@dataclass
class JointData:
    """The pooled researcher×sense matrix and its row/column identities."""

    T: np.ndarray  # (n_researchers_total, n_senses) raw pooled plain-TF
    sense_ids: list[str]
    researcher_ids: list[str]  # namespaced "{cohort_id}:{local_id}"
    cohorts: list[str]  # per-row cohort id
    units: list[str]  # per-row unit ("" when unknown)
    sense_cohorts: dict[str, list[str]]  # sense id → contributing cohorts
    unmapped_terms: dict[str, int]  # cohort id → raw terms without a sense

    @property
    def cohort_of_row(self) -> np.ndarray:
        return np.asarray(self.cohorts, dtype=object)


def namespaced_id(cohort_id: str, local_id: str) -> str:
    """Joint-map researcher id: cohort-namespaced local opaque id."""
    return f"{cohort_id}:{local_id}"


def assemble_joint_matrix(cohorts: list[CohortInput], table: ReconciliationTable) -> JointData:
    """Pool per-cohort plain-TF matrices onto the merged sense vocabulary.

    Raw terms that fold to the same sense (within or across cohorts) sum; raw
    terms absent from *table* are skipped and counted in ``unmapped_terms``
    (they should be none when the table was built from the same vocabularies).
    """
    sense_ids = table.sense_ids
    col_of_sense = {sid: j for j, sid in enumerate(sense_ids)}

    blocks: list[np.ndarray] = []
    researcher_ids: list[str] = []
    row_cohorts: list[str] = []
    units: list[str] = []
    unmapped: dict[str, int] = {}

    for cohort in sorted(cohorts, key=lambda s: s.cohort_id):
        target_cols = np.full(len(cohort.terms), -1, dtype=int)
        n_unmapped = 0
        for j, term in enumerate(cohort.terms):
            sid = table.sense_of(cohort.cohort_id, term)
            if sid is None:
                n_unmapped += 1
                continue
            target_cols[j] = col_of_sense[sid]
        if n_unmapped:
            unmapped[cohort.cohort_id] = n_unmapped
            logger.warning(
                "Cohort %s: %d/%d raw terms have no sense in the reconciliation "
                "table and are dropped from the joint matrix.",
                cohort.cohort_id,
                n_unmapped,
                len(cohort.terms),
            )
        M = np.zeros((cohort.X_tf.shape[0], len(sense_ids)))
        for j, target in enumerate(target_cols):
            if target >= 0:
                M[:, target] += cohort.X_tf[:, j]
        blocks.append(M)
        researcher_ids.extend(namespaced_id(cohort.cohort_id, rid) for rid in cohort.researcher_ids)
        row_cohorts.extend([cohort.cohort_id] * len(cohort.researcher_ids))
        units.extend(
            cohort.units if cohort.units is not None else [""] * len(cohort.researcher_ids)
        )

    if len(set(researcher_ids)) != len(researcher_ids):
        raise ValueError("Duplicate namespaced researcher ids across cohorts.")

    return JointData(
        T=np.vstack(blocks) if blocks else np.zeros((0, len(sense_ids))),
        sense_ids=list(sense_ids),
        researcher_ids=researcher_ids,
        cohorts=row_cohorts,
        units=units,
        sense_cohorts=table.sense_cohorts(),
        unmapped_terms=unmapped,
    )


def _sense_token_count(sense_id: str) -> int:
    return max(1, len(sense_id.split("#", 1)[0].split(" ")))


def joint_idf(
    T: np.ndarray, cohorts: list[str] | np.ndarray, *, mode: str = "pooled"
) -> np.ndarray:
    """Column IDF for the pooled matrix.

    ``"pooled"`` — smooth researcher-level IDF over all rows,
    ``log((1+N)/(1+df)) + 1`` (the sklearn convention the single-cohort pipeline uses).
    ``"macro"`` — ``log(1/(ε + mean_s df_s/N_s))``: cohort-size-free, but boosts
    cohort-marker terms (opposite bias — compute both to compare).
    """
    T = np.asarray(T, dtype=float)
    present = T > 0
    if mode == "pooled":
        df = present.sum(axis=0)
        return np.log((1.0 + T.shape[0]) / (1.0 + df)) + 1.0
    if mode == "macro":
        cohort_of = np.asarray(cohorts, dtype=object)
        fracs = []
        for s in sorted(set(cohort_of.tolist())):
            rows = cohort_of == s
            n_s = int(rows.sum())
            if n_s:
                fracs.append(present[rows].sum(axis=0) / n_s)
        mean_frac = np.mean(np.vstack(fracs), axis=0) if fracs else np.zeros(T.shape[1])
        return np.log(1.0 / (1e-6 + mean_frac))
    raise ValueError(f"Unknown idf mode {mode!r} (expected 'pooled' or 'macro')")


def weight_matrix(
    joint: JointData,
    *,
    idf_mode: str = "pooled",
    sublinear: bool = True,
    length_alpha: float = 2.0,
) -> np.ndarray:
    """Weight the pooled TF matrix: sublinear TF · joint IDF · length bonus · L2.

    Mirrors the single-cohort pipeline's scoring shape (TF-IDF with the
    ``score * (1 + α(L−1))`` length bonus on the sense's token count) so joint
    rows live in the same kind of space single-cohort rows do; the IDF is the
    *joint* one.
    """
    W = np.asarray(joint.T, dtype=float).copy()
    if sublinear:
        nz = W > 0
        W[nz] = 1.0 + np.log(W[nz])
    W *= joint_idf(joint.T, joint.cohorts, mode=idf_mode)[None, :]
    lengths = np.array([_sense_token_count(s) for s in joint.sense_ids], dtype=float)
    W *= (1.0 + length_alpha * (lengths - 1.0))[None, :]
    return normalize(W, norm="l2", axis=1)


@dataclass
class JointEmbedding:
    """Joint SVD embedding: researchers (all rows) + senses, plus fit provenance."""

    Z_ind: np.ndarray
    Z_senses: np.ndarray
    fit_rows: np.ndarray  # bool mask over rows — the balanced fit subsample
    svd: TruncatedSVD
    explained_variance: float


def balanced_svd(
    X_w: np.ndarray,
    cohorts: list[str] | np.ndarray,
    researcher_ids: list[str],
    *,
    n_components: int = 100,
    per_cohort_cap: int = 150,
    random_state: int = 42,
) -> JointEmbedding:
    """Fit SVD on a cohort-balanced deterministic subsample, transform all rows.

    Balancing by *fit rows* (min(N_s, cap) per cohort, chosen by sorted
    researcher id — deterministic, no RNG) keeps big cohorts from owning the
    variance axes without reweighting rows (which would break the unit row norms
    the cosine geometry relies on). ``Z_senses = Vt.T · S`` places senses in the
    same latent space, exactly like the per-cohort pipeline.
    """
    X_w = np.asarray(X_w, dtype=float)
    cohort_of = np.asarray(cohorts, dtype=object)
    fit_mask = np.zeros(X_w.shape[0], dtype=bool)
    for s in sorted(set(cohort_of.tolist())):
        rows = np.nonzero(cohort_of == s)[0]
        order = sorted(rows, key=lambda i: researcher_ids[i])
        fit_mask[order[: min(per_cohort_cap, len(order))]] = True

    n_fit = int(fit_mask.sum())
    eff = min(n_components, max(1, n_fit - 1), max(1, X_w.shape[1] - 1))
    if eff != n_components:
        logger.warning("Clamping joint SVD n_components %d → %d.", n_components, eff)

    svd = TruncatedSVD(n_components=eff, random_state=random_state)
    # Same BLAS pinning as cartolex.atlas.reducers (server-thread deadlock guard).
    with threadpool_limits(limits=1, user_api="blas"):
        svd.fit(X_w[fit_mask])
    Z_ind = svd.transform(X_w)
    Z_senses = svd.components_.T * svd.singular_values_
    return JointEmbedding(
        Z_ind=Z_ind,
        Z_senses=Z_senses,
        fit_rows=fit_mask,
        svd=svd,
        explained_variance=float(svd.explained_variance_ratio_.sum()),
    )


def anchor_concepts(
    taxonomies: dict[str, dict[str, Any]],
    table: ReconciliationTable,
    Z_senses: np.ndarray,
    sense_ids: list[str],
) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Re-anchor curated concepts as L2-normalized sense-centroid vectors.

    *taxonomies* maps cohort id → taxonomy doc (``concepts`` carrying resolved
    term **strings**, as in a map bundle's taxonomy). Curation is
    term-sets, not geometry — so each concept transfers to the joint space as
    the centroid of its member senses. Concepts whose terms all fell out of the
    table are skipped. Returns ``(concept_meta, anchor_matrix)`` aligned row-wise.
    """
    col_of_sense = {sid: j for j, sid in enumerate(sense_ids)}
    meta: list[dict[str, Any]] = []
    vectors: list[np.ndarray] = []
    for cohort_id in sorted(taxonomies):
        doc = taxonomies[cohort_id] or {}
        for concept in doc.get("concepts", []):
            terms = [str(t) for t in concept.get("terms", [])]
            cols = sorted(
                {
                    col_of_sense[sid]
                    for t in terms
                    if (sid := table.sense_of(cohort_id, t)) is not None
                }
            )
            if not cols:
                continue
            centroid = Z_senses[cols].mean(axis=0)
            norm = float(np.linalg.norm(centroid))
            if norm <= 0:
                continue
            meta.append(
                {
                    "cohort_id": cohort_id,
                    "concept_id": concept.get("id"),
                    "label": str(concept.get("label", "")),
                    "subfield_id": concept.get("subfield_id"),
                    "n_terms": len(terms),
                    "n_senses": len(cols),
                    "sense_cols": cols,
                }
            )
            vectors.append(centroid / norm)
    anchors = np.vstack(vectors) if vectors else np.zeros((0, Z_senses.shape[1]))
    return meta, anchors


def researcher_concept_weights(
    joint: JointData,
    concept_meta: list[dict[str, Any]],
) -> np.ndarray:
    """Evidence-based researcher→concept weights (L1 rows; a concept mixture per researcher).

    A researcher weighs on a concept only through the pooled TF mass they carry
    on that concept's senses (never proximity). Rows with no evidence stay zero.
    """
    n_res = joint.T.shape[0]
    W = np.zeros((n_res, len(concept_meta)))
    for k, c in enumerate(concept_meta):
        cols = c["sense_cols"]
        if cols:
            W[:, k] = joint.T[:, cols].sum(axis=1)
    row_sums = W.sum(axis=1, keepdims=True)
    return np.divide(W, row_sums, out=np.zeros_like(W), where=row_sums > 0)


def procrustes_residual(Z_a: np.ndarray, Z_b: np.ndarray) -> float:
    """Normalized orthogonal-Procrustes residual on matched rows (a compatibility diagnostic).

    ``min_R ||Z_a R − Z_b||_F / ||Z_b||_F`` over rotations. Rows must be matched
    (the same shared senses in two cohorts' own term spaces); dimensions may
    differ (the narrower matrix is zero-padded). 0 = rigidly compatible spaces;
    the higher the residual, the less a rotation can reconcile the geometries.
    """
    from scipy.linalg import orthogonal_procrustes

    A = np.asarray(Z_a, dtype=float)
    B = np.asarray(Z_b, dtype=float)
    if A.shape[0] != B.shape[0]:
        raise ValueError(f"Row mismatch: {A.shape[0]} vs {B.shape[0]} (rows must be matched)")
    d = max(A.shape[1], B.shape[1])
    if A.shape[1] < d:
        A = np.hstack([A, np.zeros((A.shape[0], d - A.shape[1]))])
    if B.shape[1] < d:
        B = np.hstack([B, np.zeros((B.shape[0], d - B.shape[1]))])
    R, _ = orthogonal_procrustes(A, B)
    denom = float(np.linalg.norm(B))
    if denom <= 0:
        return 0.0
    return float(np.linalg.norm(A @ R - B) / denom)
