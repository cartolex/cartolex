# SPDX-License-Identifier: MIT
"""Researcher semantic trajectories (the trajectories stage).

Builds per-(researcher, time-bin) keyword fingerprints over the *same* canonical
concept vocabulary used for the static lexical map, then projects them through
the persisted SVD + UMAP models so a researcher's topical mobility can be drawn
as a path in the existing semantic space.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize

from cartolex.lexicon.canonicalization import fold_tfidf_to_canonical
from cartolex.lexicon.text_utils import length_bonus
from cartolex.lexicon.utils import make_researcher_id

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
    """Per-(researcher, time-bin) fingerprints aligned to the reference term space."""

    B: np.ndarray  # (n_bins, n_terms) folded, length-bonus-weighted vectors
    meta: pd.DataFrame  # one row per bin (see _META_COLUMNS)
    terms: list[str]  # reference term order (== static map's terms)


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
) -> TrajectoryData:
    """Build per-(researcher, time-bin) fingerprints in the reference term space.

    *docs* must carry columns ``last_name, first_name, unit, doc_year, text``
    (and ``doc_type`` when *doc_types* is given). Documents are filtered to
    *doc_types* (``None``: every document), grouped into ``bin_years``-wide
    time-bins, folded onto the canonical concept vocabulary with the persisted
    *vectorizer* + *alias_to_canon* map, length-bonus weighted (matching the
    static per-researcher scoring), and re-indexed onto *ref_terms* so each row
    is directly projectable through the persisted SVD/UMAP.
    """
    ref_terms = [str(t).lower() for t in ref_terms]
    required = {"last_name", "first_name", "unit", "doc_year", "text"}
    if doc_types is not None:
        required.add("doc_type")
    missing = required.difference(docs.columns)
    if missing:
        raise ValueError(f"docs is missing columns: {sorted(missing)}")

    empty = TrajectoryData(
        B=np.zeros((0, len(ref_terms))),
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

    rows: list[dict] = []
    texts: list[str] = []
    group_cols = ["researcher_id", "last_name", "first_name", "unit", "bin_start", "bin_end"]
    for key, sub in work.groupby(group_cols, dropna=False, sort=True):
        rid, last, first, unit, bstart, bend = key
        if len(sub) < min_docs_per_bin:
            continue
        text = "\n\n".join(t for t in sub["text"].astype(str) if t.strip())
        if not text.strip():
            continue
        texts.append(text)
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

    # Fold bin texts onto the canonical concept space, then re-index to ref_terms.
    target_concepts = sorted(set(alias_to_canon.values()))
    expanded = vectorizer.transform(texts)
    folded = fold_tfidf_to_canonical(
        X_expanded=expanded,
        expanded_terms=vectorizer.get_feature_names_out(),
        canonical_terms=target_concepts,
        alias_to_canon=alias_to_canon,
    )
    dense = folded.toarray().astype(float)

    # Length-bonus weighting matches compute_keywords_* in the consolidation stage.
    mult, _ = length_bonus(target_concepts, np.ones(len(target_concepts)), length_alpha)
    dense = dense * mult[np.newaxis, :]

    concept_to_col = {t.lower(): j for j, t in enumerate(target_concepts)}
    B = np.zeros((len(rows), len(ref_terms)), dtype=float)
    for j, term in enumerate(ref_terms):
        src = concept_to_col.get(term)
        if src is not None:
            B[:, j] = dense[:, src]

    concepts_arr = np.array(target_concepts, dtype=object)
    top_terms: list[str] = []
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
    return TrajectoryData(B=B, meta=meta, terms=ref_terms)


def project_trajectories(B: np.ndarray, svd_model, umap_model) -> np.ndarray:
    """Project bin fingerprints into the persisted reference UMAP space.

    Mirrors the static map exactly: L2-normalise → ``svd.transform`` →
    ``umap.transform``. Returns an ``(n_bins, 2)`` array; an empty input yields
    shape ``(0, 2)``.
    """
    B = np.asarray(B, dtype=float)
    if B.shape[0] == 0:
        return np.zeros((0, 2))
    Xn = normalize(B, norm="l2", axis=1)
    Z = svd_model.transform(Xn)
    return np.asarray(umap_model.transform(Z))


def build_trajectory_windows(
    traj: TrajectoryData,
    *,
    svd_model,
    umap_model,
    term_to_concept: dict[str, int],
    concept_to_subfield: dict[int, int],
    report: Callable[[float, str], Any] | None = None,
) -> dict[str, list[dict]]:
    """Exact per-(researcher, contiguous-bin-window) reprojection + re-weighting.

    For every contiguous run of a researcher's own time-bins, sum the
    length-bonus-weighted bin vectors, L2-normalise, and project through the
    persisted SVD (``z``) and UMAP for the 2-D position.  Subfield/concept
    membership is **evidence-based**: aggregated from the window's own term
    vector through the applied lexicon (:func:`researcher_group_weights`), not
    SVD-proximity — so a window is only ever attributed themes whose terms the
    researcher actually used in that span.

    Windows are keyed ``"<start_year>_<end_year>"`` (the calendar span of the
    run); the full-span key reproduces the whole-history profile to within the
    SVD/UMAP fit-vs-transform gap. Returns ``{researcher_id: [entry, ...]}`` with
    each entry ``{key, mass, x, y, subfields, concepts}``. Empty input -> ``{}``.
    *report*, when given, is called with the share of researchers done (it may
    raise to stop the loop).
    """
    from cartolex.lexicon.subfields import researcher_group_weights

    if traj.B.shape[0] == 0 or traj.meta.empty:
        return {}
    meta = traj.meta.reset_index(drop=True)
    out: dict[str, list[dict]] = {}
    groups = meta.groupby("researcher_id", sort=False)
    n_groups = max(1, groups.ngroups)
    for done, (rid, grp) in enumerate(groups):
        if report is not None:
            report(done / n_groups, "time windows")
        rows = grp.sort_values("bin_end").index.tolist()
        starts = [int(meta.at[r, "bin_start"]) for r in rows]
        ends = [int(meta.at[r, "bin_end"]) for r in rows]
        entries: list[dict] = []
        for i in range(len(rows)):
            acc = np.zeros(traj.B.shape[1], dtype=float)
            for j in range(i, len(rows)):
                acc = acc + traj.B[rows[j]]
                mass = float(acc.sum())
                if mass <= 0:
                    continue
                z = svd_model.transform(normalize(acc.reshape(1, -1), norm="l2"))
                xy = np.asarray(umap_model.transform(z))[0]
                scored = [(traj.terms[c], float(acc[c])) for c in np.nonzero(acc)[0] if acc[c] > 0]
                subfields, concepts = researcher_group_weights(
                    scored,
                    term_to_concept=term_to_concept,
                    concept_to_subfield=concept_to_subfield,
                )
                entries.append(
                    {
                        "key": f"{starts[i]}_{ends[j]}",
                        "mass": round(mass, 6),
                        "x": round(float(xy[0]), 4),
                        "y": round(float(xy[1]), 4),
                        "subfields": subfields,
                        "concepts": concepts,
                    }
                )
        if entries:
            out[str(rid)] = entries
    return out
