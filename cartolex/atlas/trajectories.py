# SPDX-License-Identifier: MIT
"""Researcher semantic trajectories (the trajectories stage).

Builds per-(researcher, time-bin) keyword fingerprints over the *same* canonical
concept vocabulary used for the static lexical map, then projects them through
the persisted SVD and places them on the map by their nearest mapped people, so
a researcher's topical mobility can be drawn as a path in the existing semantic
space.

The bins × keywords matrix is sparse; the steps that need dense arithmetic take
it a block of rows at a time (:mod:`cartolex.atlas.blocks`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.preprocessing import normalize

from cartolex.lexicon.canonicalization import fold_tfidf_to_canonical
from cartolex.lexicon.text_utils import length_bonus
from cartolex.lexicon.utils import make_researcher_id

from .blocks import as_csr, dense_rows, row_blocks

logger = logging.getLogger(__name__)

_META_COLUMNS = [
    "researcher_id",
    "last_name",
    "first_name",
    "unit",
    "bin_start",
    "bin_end",
    "n_docs",
    "top_terms",
]


@dataclass
class TrajectoryData:
    """Per-(researcher, time-bin) fingerprints aligned to the reference term space.

    ``B`` may be given dense or sparse; it is kept as a float64 CSR matrix.
    """

    B: sparse.csr_matrix  # (n_bins, n_terms) folded, length-bonus-weighted vectors
    meta: pd.DataFrame  # one row per bin (see _META_COLUMNS)
    terms: list[str]  # reference term order (== static map's terms)

    def __post_init__(self) -> None:
        self.B = as_csr(self.B)


def bin_bounds(year: int, *, bin_years: int, now_year: int) -> tuple[int, int]:
    """Return the inclusive (start, end) calendar years of *year*'s time-bin.

    Bins are anchored to *now_year* and run backwards in ``bin_years``-wide
    windows: the most recent bin is ``[now_year - bin_years + 1, now_year]``.
    """
    k = (now_year - int(year)) // bin_years
    end = now_year - k * bin_years
    start = end - bin_years + 1
    return start, end


def build_trajectory_matrix(
    docs: pd.DataFrame,
    *,
    vectorizer,
    alias_to_canon: dict[str, str],
    ref_terms: list[str],
    now_year: int,
    bin_years: int = 5,
    doc_types: Collection[str] | None = None,
    length_alpha: float = 2.0,
    top_k_terms: int = 8,
    min_docs_per_bin: int = 1,
    counts: sparse.spmatrix | None = None,
) -> TrajectoryData:
    """Build per-(researcher, time-bin) fingerprints in the reference term space.

    *docs* must carry columns ``last_name, first_name, unit, doc_year, text``
    (and ``doc_type`` when *doc_types* is given); with *counts* (each document's
    counts of the vectorizer's vocabulary, :func:`text_counts`), ``row`` (the
    document's row of *counts*) and ``blank`` (whether its text is blank) instead of
    ``text``. A bin's vector is the sum of its documents' counts, weighted as the
    vectorizer weighs a document (each text counted alone: no word sequence spans
    two texts). Documents are filtered to
    *doc_types* (``None``: every document), grouped into ``bin_years``-wide
    time-bins, folded onto the canonical concept vocabulary with the persisted
    *vectorizer* + *alias_to_canon* map, length-bonus weighted (matching the
    static per-researcher scoring), and re-indexed onto *ref_terms* so each row
    is directly projectable through the persisted SVD/UMAP.
    """
    ref_terms = [str(t).lower() for t in ref_terms]
    required = {"last_name", "first_name", "unit", "doc_year"}
    required |= {"row", "blank"} if counts is not None else {"text"}
    if doc_types is not None:
        required.add("doc_type")
    missing = required.difference(docs.columns)
    if missing:
        raise ValueError(f"docs is missing columns: {sorted(missing)}")

    empty = TrajectoryData(
        B=sparse.csr_matrix((0, len(ref_terms))),
        meta=pd.DataFrame(columns=_META_COLUMNS),
        terms=ref_terms,
    )

    work = docs.copy()
    if doc_types is not None:
        types = work["doc_type"].astype(str).str.strip().str.lower()
        work = work[types.isin({str(t).strip().lower() for t in doc_types})]
    work["doc_year_num"] = pd.to_numeric(work["doc_year"], errors="coerce")
    work = work[work["doc_year_num"].notna()]
    if work.empty:
        return empty

    work["researcher_id"] = [
        make_researcher_id(ln, fn, u)
        for ln, fn, u in zip(work["last_name"], work["first_name"], work["unit"], strict=False)
    ]
    bounds = [
        bin_bounds(int(y), bin_years=bin_years, now_year=now_year) for y in work["doc_year_num"]
    ]
    work["bin_start"] = [b[0] for b in bounds]
    work["bin_end"] = [b[1] for b in bounds]

    if counts is None:
        texts = work["text"].astype(str).tolist()
        counts = text_counts(vectorizer, texts)
        work["row"] = np.arange(len(texts))
        work["blank"] = [not t.strip() for t in texts]
    counts = sparse.csr_matrix(counts, dtype=np.float64)
    rows: list[dict] = []
    members: list[tuple[int, int]] = []  # (bin, row of counts)
    group_cols = ["researcher_id", "last_name", "first_name", "unit", "bin_start", "bin_end"]
    for key, sub in work.groupby(group_cols, dropna=False, sort=True):
        rid, last, first, unit, bstart, bend = key
        if len(sub) < min_docs_per_bin:
            continue
        own = [int(r) for r, b in zip(sub["row"], sub["blank"], strict=True) if not b]
        if not own:
            continue
        members += [(len(rows), r) for r in own]
        rows.append(
            {
                "researcher_id": rid,
                "last_name": last,
                "first_name": first,
                "unit": unit,
                "bin_start": int(bstart),
                "bin_end": int(bend),
                "n_docs": int(len(sub)),
            }
        )

    if not rows:
        return empty

    # Each bin's counts, weighted as the vectorizer weighs a document (tf·idf, L2 norm);
    # folded onto the canonical concept space, then re-indexed to ref_terms.
    which = sparse.csr_matrix(
        (np.ones(len(members)), ([b for b, _ in members], [r for _, r in members])),
        shape=(len(rows), counts.shape[0]),
    )
    expanded = vectorizer._tfidf.transform(sparse.csr_matrix(which @ counts), copy=False)
    target_concepts = sorted(set(alias_to_canon.values()))
    folded = fold_tfidf_to_canonical(
        X_expanded=expanded,
        expanded_terms=vectorizer.get_feature_names_out(),
        canonical_terms=target_concepts,
        alias_to_canon=alias_to_canon,
    )

    # Length-bonus weighting matches compute_keywords_* in the consolidation stage.
    mult, _ = length_bonus(target_concepts, np.ones(len(target_concepts)), length_alpha)

    concept_to_col = {t.lower(): j for j, t in enumerate(target_concepts)}
    source = np.array([concept_to_col.get(term, -1) for term in ref_terms], dtype=np.int64)
    mapped = np.flatnonzero(source >= 0)

    concepts_arr = np.array(target_concepts, dtype=object)
    top_terms: list[str] = []
    parts: list[sparse.csr_matrix] = []
    for block in row_blocks(folded.shape[0], folded.shape[1]):
        dense = dense_rows(folded, block)
        dense = dense * mult[np.newaxis, :]
        B = np.zeros((dense.shape[0], len(ref_terms)), dtype=float)
        B[:, mapped] = dense[:, source[mapped]]
        parts.append(sparse.csr_matrix(B))
        for i in range(dense.shape[0]):
            order = np.argsort(dense[i])[::-1]
            top_terms.append(
                ";".join(str(concepts_arr[c]) for c in order[:top_k_terms] if dense[i, c] > 0)
            )

    meta = pd.DataFrame(rows, columns=_META_COLUMNS[:-1])
    meta["top_terms"] = top_terms
    logger.info(
        "Built %d trajectory bins for %d researchers.", len(meta), meta["researcher_id"].nunique()
    )
    return TrajectoryData(B=sparse.vstack(parts, format="csr"), meta=meta, terms=ref_terms)


def count_parameters(vectorizer: Any) -> dict[str, Any]:
    """The parameters of a ``CountVectorizer`` that counts what *vectorizer* (a fitted
    ``TfidfVectorizer``) weighs: the same text processing and vocabulary."""
    params = dict(vectorizer.get_params())
    for name in ("norm", "use_idf", "smooth_idf", "sublinear_tf"):
        params.pop(name, None)
    params["vocabulary"] = dict(vectorizer.vocabulary_)
    return params


def text_counts(vectorizer: Any, texts: list[str]) -> sparse.csr_matrix:
    """Each text's counts of *vectorizer*'s vocabulary (a ``float64`` CSR row per text)."""
    from sklearn.feature_extraction.text import CountVectorizer

    return sparse.csr_matrix(
        CountVectorizer(**count_parameters(vectorizer)).transform(texts), dtype=np.float64
    )


def project_trajectories(B: np.ndarray | sparse.spmatrix, svd_model, anchors) -> np.ndarray:
    """Place bin fingerprints on the map.

    L2-normalise → ``svd.transform`` → placed by the nearest mapped people
    (*anchors*, a :class:`~cartolex.atlas.placement.MapAnchors`), a block of rows at
    a time. *B* may be dense or sparse. Returns an ``(n_bins, 2)`` array; an empty
    input yields shape ``(0, 2)``.
    """
    if not sparse.issparse(B):
        B = np.asarray(B, dtype=float)
    if B.shape[0] == 0:
        return np.zeros((0, 2))
    Z = np.vstack(
        [
            svd_model.transform(normalize(dense_rows(B, rows), norm="l2", axis=1))
            for rows in row_blocks(B.shape[0], B.shape[1])
        ]
    )
    return anchors.place(Z)


def _window_projector(svd_model) -> Callable[[np.ndarray], np.ndarray]:
    """One window's vector → its place in the space: L2-normalised, then the SVD's transform.

    For a fitted ``TruncatedSVD`` the arithmetic of ``normalize`` and
    ``transform`` is done directly (the same operations on the same arrays, so
    the same bits) without their input checks, which cost more than the
    product for one row; any other model is called as it is.
    """
    from sklearn.decomposition import TruncatedSVD

    if type(svd_model) is not TruncatedSVD:
        return lambda acc: svd_model.transform(normalize(acc.reshape(1, -1), norm="l2"))[0]
    tiny = 10 * np.finfo(np.float64).eps

    def project(acc: np.ndarray) -> np.ndarray:
        x = np.array(acc.reshape(1, -1), dtype=np.float64, order="C")
        norms = np.sqrt(np.einsum("ij,ij->i", x, x))
        norms[norms < tiny] = 1.0
        x /= norms[:, None]
        return (x @ svd_model.components_.T)[0]

    return project


def build_trajectory_windows(
    traj: TrajectoryData,
    *,
    svd_model,
    anchors,
    term_to_concept: dict[str, int],
    concept_to_subfield: dict[int, int],
    report: Callable[[float, str], Any] | None = None,
    describe: Callable[[np.ndarray, np.ndarray], Any] | None = None,
    all_spans: bool = False,
) -> dict[str, list[dict]]:
    """Exact per-(researcher, contiguous-bin-window) reprojection + re-weighting.

    For each of a researcher's own time-bins (with *all_spans*, for every contiguous
    run of them: their number grows with the square of the bins), sum the
    length-bonus-weighted bin vectors, L2-normalise, and project through the
    persisted SVD (``z``), then place it on the map by its nearest mapped people
    (*anchors*, a :class:`~cartolex.atlas.placement.MapAnchors`; every window in one
    call).  Subfield/concept
    membership is **evidence-based**: aggregated from the window's own term
    vector through the applied lexicon (:func:`researcher_group_weights`), not
    SVD-proximity — so a window is only ever attributed themes whose terms the
    researcher actually used in that span.

    Windows are keyed ``"<start_year>_<end_year>"`` (the calendar span of the
    run; with *all_spans*, the full-span key is placed like the whole-history profile).
    Returns ``{researcher_id: [entry, ...]}`` with each entry
    ``{key, mass, x, y, subfields, concepts}``. Empty input -> ``{}``.
    *report*, when given, is called with the share of researchers done (it may
    raise to stop the loop). *describe*, when given, is called with each window's
    term columns and values; what it returns is the entry's ``levels`` (the
    window's weights on every level of a theme tree, for example).
    """
    from cartolex.lexicon.subfields import researcher_group_weights

    if traj.B.shape[0] == 0 or traj.meta.empty:
        return {}
    to_space = _window_projector(svd_model)
    meta = traj.meta.reset_index(drop=True)
    out: dict[str, list[dict]] = {}
    pending: list[tuple[str, dict]] = []
    vectors: list[np.ndarray] = []
    groups = meta.groupby("researcher_id", sort=False)
    n_groups = max(1, groups.ngroups)
    for done, (rid, grp) in enumerate(groups):
        if report is not None:
            report(done / n_groups, "time windows")
        rows = grp.sort_values("bin_end").index.tolist()
        starts = [int(meta.at[r, "bin_start"]) for r in rows]
        ends = [int(meta.at[r, "bin_end"]) for r in rows]
        bins = traj.B[rows].toarray()
        for i in range(len(rows)):
            acc = np.zeros(traj.B.shape[1], dtype=float)
            for j in range(i, len(rows) if all_spans else i + 1):
                acc = acc + bins[j]
                mass = float(acc.sum())
                if mass <= 0:
                    continue
                vectors.append(to_space(acc))
                scored = [(traj.terms[c], float(acc[c])) for c in np.nonzero(acc)[0] if acc[c] > 0]
                subfields, concepts = researcher_group_weights(
                    scored,
                    term_to_concept=term_to_concept,
                    concept_to_subfield=concept_to_subfield,
                )
                entry = {
                    "key": f"{starts[i]}_{ends[j]}",
                    "mass": round(mass, 6),
                    "subfields": subfields,
                    "concepts": concepts,
                }
                if describe is not None:
                    cols = np.flatnonzero(acc > 0)
                    entry["levels"] = describe(cols, acc[cols])
                pending.append((str(rid), entry))
    xy = anchors.place(np.vstack(vectors)) if vectors else np.zeros((0, 2))
    for (rid, entry), (x, y) in zip(pending, xy, strict=True):
        entry = {**entry, "x": round(float(x), 4), "y": round(float(y), 4)}
        keys = ("key", "mass", "x", "y", "subfields", "concepts", "levels")
        out.setdefault(rid, []).append({k: entry[k] for k in keys if k in entry})
    return out
