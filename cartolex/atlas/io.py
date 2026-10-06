from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse as sp

from cartolex.lexicon.utils import canonicalize_names, make_researcher_id

from .types import LexicalData

logger = logging.getLogger(__name__)

#: The columns that name a person (their identity). A table read with pandas keeps them as
#: written: its default reading would turn a unit ``NA`` (a person without one), or a name
#: ``NULL`` or ``None``, into a missing value, and the person's id would no longer be the
#: one the keyword stage wrote.
IDENTITY_COLUMNS = ("last_name", "first_name", "unit")


def _read_people_table(path: Path) -> pd.DataFrame:
    """*path* read with pandas, its identity columns (:data:`IDENTITY_COLUMNS`) as text
    exactly as written (an empty cell is empty); every other column as pandas reads it."""
    return pd.read_csv(path, converters={c: str for c in IDENTITY_COLUMNS})


def load_run_settings(run_settings_json: Path) -> dict:
    """The settings snapshot the consolidation stage wrote (the atlas needs that stage first)."""
    if not run_settings_json.exists():
        raise FileNotFoundError(
            f"{run_settings_json} not found (run the consolidation stage first)."
        )
    data = json.loads(run_settings_json.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{run_settings_json} is not a settings snapshot (a JSON object).")
    return data


def load_restricted_keywords(kw_researcher_csv: Path) -> pd.DataFrame:
    """Load the per-researcher restricted keyword table as a DataFrame."""
    if not kw_researcher_csv.exists():
        raise FileNotFoundError(f"{kw_researcher_csv} not found.")
    kw_df = _read_people_table(kw_researcher_csv)
    expected = {"last_name", "first_name", "unit", "term", "score"}
    missing = expected.difference(kw_df.columns)
    if missing:
        raise ValueError(f"{kw_researcher_csv} is missing columns: {missing}")
    return kw_df


def load_index(researcher_index_csv: Path) -> pd.DataFrame:
    """Load the researcher index CSV (the roster) as a DataFrame.

    Its identity columns are read as text and cleaned, and an ``id`` column added
    (:func:`~cartolex.lexicon.utils.make_researcher_id`, as the keyword stage makes it);
    any other column is a person attribute, kept as pandas reads it.
    """
    if not researcher_index_csv.exists():
        raise FileNotFoundError(
            f"{researcher_index_csv} not found (run the consolidation or roster stage first)."
        )

    idx_df = _read_people_table(researcher_index_csv)

    for col in ["last_name", "first_name", "unit"]:
        if col not in idx_df.columns:
            raise ValueError(f"{researcher_index_csv} is missing required column '{col}'.")

    idx_df["last_name"] = idx_df["last_name"].astype(str).str.strip()
    idx_df["first_name"] = idx_df["first_name"].astype(str).str.strip()
    idx_df["unit"] = idx_df["unit"].astype(str).str.strip()

    if "last_name_canon" not in idx_df.columns:
        idx_df["last_name_canon"] = idx_df["last_name"].map(canonicalize_names)
    if "first_name_canon" not in idx_df.columns:
        idx_df["first_name_canon"] = idx_df["first_name"].map(canonicalize_names)

    idx_df["id"] = [
        make_researcher_id(ln, fn, u)
        for ln, fn, u in zip(
            idx_df["last_name"],
            idx_df["first_name"],
            idx_df["unit"],
            strict=False,
        )
    ]

    dup = idx_df["id"].duplicated(keep=False)
    if dup.any():
        ex = idx_df.loc[dup, ["last_name", "first_name", "unit", "id"]].head(30)
        raise ValueError(
            "Ambiguous researcher index: multiple rows share the same canonical id.\n"
            f"Examples:\n{ex.to_string(index=False)}"
        )

    return idx_df


def build_lexical_matrix(
    kw_researcher_csv: Path,
    researcher_index_csv: Path,
    person_terms_json: Path | None = None,
) -> LexicalData:
    """Build the term-by-researcher lexical matrix used for SVD/UMAP.

    Reads the people × keywords matrices of the consolidation (*person_terms_json*, see
    :func:`cartolex.atlas.model_files.save_person_terms`) when they exist, else the
    per-person keyword table *kw_researcher_csv* (a run of an earlier version, whose table
    held each person's whole row). Rows follow the roster; the keywords are those someone
    uses, in lower case, sorted. Returns a :class:`LexicalData` bundle (sparse matrix plus
    row/column labels).
    """
    idx_df = load_index(researcher_index_csv)
    if person_terms_json is not None and Path(person_terms_json).exists():
        X, X_tf, terms = _matrix_rows(person_terms_json, idx_df["id"].tolist())
        return _lexical_data(X, X_tf, terms, idx_df)
    kw_df = load_restricted_keywords(kw_researcher_csv)

    kw_df = kw_df.copy()
    kw_df["last_name"] = kw_df["last_name"].astype(str).str.strip()
    kw_df["first_name"] = kw_df["first_name"].astype(str).str.strip()
    kw_df["unit"] = kw_df["unit"].astype(str).str.strip()

    kw_df["id"] = [
        make_researcher_id(ln, fn, u)
        for ln, fn, u in zip(
            kw_df["last_name"],
            kw_df["first_name"],
            kw_df["unit"],
            strict=False,
        )
    ]

    individuals = idx_df["id"].tolist()
    id_to_row = {rid: i for i, rid in enumerate(individuals)}

    terms = sorted(kw_df["term"].astype(str).str.lower().unique())
    term_to_col = {t: j for j, t in enumerate(terms)}

    # Sparse build (0.5.0): the dense np.zeros((n, m)) pair cost 2 x 8 bytes
    # per cell (48 GB at 3e5 x 1e4) and an iterrows fill; COO->CSR keeps only
    # the populated cells and vectorizes the fill. Semantics preserved: rows
    # with unknown id/term or a non-finite score are skipped entirely.
    has_tf = "score_tf" in kw_df.columns
    if not has_tf:
        logger.warning(
            "keywords_by_researcher CSV has no score_tf column — the TF quantity track "
            "is unavailable (lexicon weights will fall back to TF-IDF). Re-run the consolidation stage."
        )
    i_idx = kw_df["id"].map(id_to_row)
    j_idx = kw_df["term"].astype(str).str.lower().map(term_to_col)
    score = pd.to_numeric(kw_df["score"], errors="coerce")
    ok = i_idx.notna() & j_idx.notna() & np.isfinite(score)
    i_arr = i_idx[ok].astype(int).to_numpy()
    j_arr = j_idx[ok].astype(int).to_numpy()
    shape = (len(individuals), len(terms))
    X = sp.coo_matrix((score[ok].to_numpy(dtype=float), (i_arr, j_arr)), shape=shape).tocsr()
    X_tf = None
    if has_tf:
        tf = pd.to_numeric(kw_df["score_tf"], errors="coerce")[ok].to_numpy(dtype=float)
        tf_ok = np.isfinite(tf)
        X_tf = sp.coo_matrix((tf[tf_ok], (i_arr[tf_ok], j_arr[tf_ok])), shape=shape).tocsr()

    return _lexical_data(X, X_tf, terms, idx_df)


def _matrix_rows(
    person_terms_json: Path, individuals: list[str]
) -> tuple[sp.csr_matrix, sp.csr_matrix | None, list[str]]:
    """The stored people × keywords matrices on the roster's rows (*individuals*), their
    keywords in lower case (two keywords that differ by case only add up), those nobody uses
    left out, sorted."""
    from .model_files import load_person_terms

    score, tf, stored_terms, stored_ids = load_person_terms(person_terms_json)
    id_to_row = {rid: i for i, rid in enumerate(individuals)}
    row_of = np.array([id_to_row.get(r, -1) for r in stored_ids], dtype=np.int64)
    used = np.asarray((score != 0).sum(axis=0)).ravel() > 0
    lower = [str(t).lower() for t in stored_terms]
    terms = sorted({t for t, u in zip(lower, used.tolist(), strict=True) if u})
    term_to_col = {t: j for j, t in enumerate(terms)}
    col_of = np.array([term_to_col.get(t, -1) for t in lower], dtype=np.int64)
    shape = (len(individuals), len(terms))

    def placed(M: sp.spmatrix) -> sp.csr_matrix:
        coo = sp.coo_matrix(M)
        r, c = row_of[coo.row], col_of[coo.col]
        ok = (r >= 0) & (c >= 0) & np.isfinite(coo.data)
        return sp.coo_matrix((coo.data[ok], (r[ok], c[ok])), shape=shape).tocsr()

    return placed(score), (placed(tf) if tf is not None else None), terms


def _lexical_data(
    X: sp.csr_matrix, X_tf: sp.csr_matrix | None, terms: list[str], idx_df: pd.DataFrame
) -> LexicalData:
    individuals = idx_df["id"].tolist()
    # Drop individuals with no keywords (all-zero rows): they carry no signal,
    # survive L2-normalisation as zero vectors, and otherwise collapse into a
    # single spurious cluster in the UMAP. They are simply omitted here.
    row_mask = np.asarray((X != 0).sum(axis=1)).ravel() > 0
    n_empty = int((~row_mask).sum())
    if n_empty:
        logger.warning(
            "Omitting %d of %d individual(s) with no keywords from the lexical matrix.",
            n_empty,
            len(individuals),
        )
        X = X[row_mask]
        if X_tf is not None:
            X_tf = X_tf[row_mask]
        individuals = [
            rid for rid, keep in zip(individuals, row_mask.tolist(), strict=False) if keep
        ]
        idx_df = idx_df[row_mask].reset_index(drop=True)

    if X.shape[0] == 0:
        raise ValueError(
            "No individuals with keywords found — cannot build the lexical matrix. "
            "Run the keyword stages (extraction to consolidation) so the per-person keyword "
            "table is populated, then retry."
        )

    meta_ind = idx_df.copy()

    logger.info("Lexical matrix: %d individuals × %d terms.", X.shape[0], X.shape[1])
    return LexicalData(X=X, terms=terms, individuals=individuals, meta_ind=meta_ind, X_tf=X_tf)
