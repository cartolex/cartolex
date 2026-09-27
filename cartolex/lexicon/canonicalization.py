# SPDX-License-Identifier: MIT
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse

from .text_utils import tokenize


def _singular_token(tok: str) -> str:
    """Number-blind key for one token (FR and EN endings); a key, not a word.

    A trailing ``s`` goes (not after another ``s``: « process »), then a
    trailing ``e``: that is what lets « critiques » and « critique », or
    « analyses » and « analyse », meet on one key — the old ``-es`` rule cut
    « critiques » to « critiqu » and left « critique » whole.
    """
    # French plurals (must be checked before the generic -s)
    if tok.endswith("eaux") and len(tok) > 4:
        return tok[:-1]  # eaux -> eau  (e.g. réseaux -> réseau)
    if tok.endswith("aux") and len(tok) > 3:
        return tok[:-2] + "l"  # aux -> al    (e.g. matériaux -> matérial)
    # English -ies
    if tok.endswith("ies") and len(tok) > 3:
        return tok[:-3] + "y"
    if tok.endswith("s") and not tok.endswith("ss") and len(tok) > 3:
        tok = tok[:-1]
    if tok.endswith("e") and len(tok) > 3:
        tok = tok[:-1]
    return tok


def canonical_singular(term: str) -> str:
    """
    Rough singular/plural merge on **every** token (English and French endings).

    French agreement puts the plural on the adjective too: folding the last
    token only left « éditions critiques » as « éditions critique », which never
    met « édition critique ». The result is a lookup
    key, not a display form — « analysis » → « analysi » is expected.
    """
    tokens = tokenize(term)
    if not tokens:
        return term
    return " ".join(_singular_token(t) for t in tokens)


def fold_number_variants(weights: dict[str, float]) -> dict[str, str]:
    """``{concept: representative}`` merging concepts that differ only by number.

    Deterministic safety net under the LLM-chosen keys: « critical edition » and
    « critical editions », « édition critique » and « éditions critiques » share
    one singular key, so they collapse onto the heaviest of them (ties broken
    alphabetically). Concepts with a unique key map to themselves.
    """
    best: dict[str, tuple[float, str]] = {}
    for concept, w in weights.items():
        key = canonical_singular(str(concept).strip().lower())
        cand = (-float(w), str(concept))
        if key not in best or cand < best[key]:
            best[key] = cand
    return {str(c): best[canonical_singular(str(c).strip().lower())][1] for c in weights}


def canonical_concept(term: str, merge_map: dict[str, str]) -> str:
    """
    Merge FR/EN variants using a variant -> concept map.
    """
    t = term.strip().lower()
    return merge_map.get(t, t)


def resolve_canonical(term: str, canon_map: dict[str, str]) -> str:
    """
    Resolve a canonical term by following canonical_map chains.
    """
    t = term
    seen = set()
    while t in canon_map and t not in seen:
        seen.add(t)
        t = canon_map[t]
    return t


def nested_filter(
    df: pd.DataFrame, score_col: str, keep_threshold: float, progress_callback=None
) -> pd.DataFrame:
    """
    Remove terms that are strictly contained in a longer term with higher score.
    Optimized with inverted index to avoid O(N^2).
    """
    terms = [str(t) for t in df["term"].tolist()]
    scores = df[score_col].values
    n = len(terms)

    # Pre-tokenize
    # We use space padding for substring check matching whole tokens logic
    padded = [" " + t.lower() + " " for t in terms]
    # Real tokens for indexing
    tokenized = [t.lower().split() for t in terms]
    lens = [len(toks) for toks in tokenized]

    # Build Inverted Index: token -> set of indices (j) that contain this token
    # Only useful to index terms that successfully *contain* others, i.e. longer terms.
    # But for a given term i, we look for j that contains i.
    # So j must contain ALL tokens of i.

    # Let's index all terms by their tokens.
    from collections import defaultdict

    token_to_indices = defaultdict(list)
    for idx, toks in enumerate(tokenized):
        for tok in toks:
            token_to_indices[tok].append(idx)

    # Convert to sets for fast intersection, but lists might be faster for small ones.
    # Actually, Python sets are fast.
    token_to_indices_sets = {k: set(v) for k, v in token_to_indices.items()}

    max_containing = np.zeros(n)

    # Reporting interval
    report_interval = max(1, n // 20)  # Report 20 times

    for i in range(n):
        if progress_callback and i % report_interval == 0:
            progress_callback(i, n)

        toks_i = tokenized[i]
        if not toks_i:
            continue

        # Candidates j must contain all tokens of i.
        # Start with the set of indices for the first token
        first_token = toks_i[0]
        if first_token not in token_to_indices_sets:
            # Should not happen if i in list, but safety
            continue

        candidate_indices = token_to_indices_sets[first_token]

        # Intersect with other tokens
        # Optimization: sort tokens by frequency? No, simple loop.
        for tok in toks_i[1:]:
            if tok not in token_to_indices_sets:
                candidate_indices = set()
                break
            candidate_indices = candidate_indices.intersection(token_to_indices_sets[tok])
            if not candidate_indices:
                break

        if not candidate_indices:
            continue

        ti_padded = padded[i]
        len_i = lens[i]

        # Check actual candidates
        current_max = 0.0
        for j in candidate_indices:
            if i == j:
                continue

            # Optimization: check length
            # j must be strictly longer (in tokens) according to original logic:
            # "Remove terms that are strictly contained in a longer term"
            # Original code: if token_len(tj) <= len_i: continue
            if lens[j] <= len_i:
                continue

            # Check score early (if scores[j] <= current_max, it can't improve)
            # But max_containing[i] stores the max score found so far.
            # Wait, we need to find the max score of *any* containing term.
            if scores[j] <= current_max:
                continue

            # Original logic: ti in tj (substring check on padded)
            if ti_padded in padded[j]:
                current_max = scores[j]

        max_containing[i] = current_max

    keep_mask = []
    for i in range(n):
        if max_containing[i] > 0 and scores[i] < keep_threshold * max_containing[i]:
            keep_mask.append(False)
        else:
            keep_mask.append(True)

    return df[np.array(keep_mask)].copy()


def fold_tfidf_to_canonical(
    X_expanded: sparse.csr_matrix,
    expanded_terms: np.ndarray,
    canonical_terms: list[str],
    alias_to_canon: dict[str, str],
) -> sparse.csr_matrix:
    """
    Fold an expanded TF-IDF matrix onto the canonical vocabulary.
    """
    canon_index = {t: i for i, t in enumerate(canonical_terms)}
    n_exp = expanded_terms.size
    n_can = len(canonical_terms)

    tgt = np.full(n_exp, -1, dtype=np.int32)
    for j, t in enumerate(expanded_terms.tolist()):
        t = str(t).strip().lower()
        if t in canon_index:
            tgt[j] = canon_index[t]
        else:
            c = alias_to_canon.get(t)
            if c is not None:
                if c not in canon_index:
                    raise ValueError(
                        f"Alias maps to canonical term not in canonical_terms: {t} -> {c}"
                    )
                tgt[j] = canon_index[c]

    keep = np.where(tgt >= 0)[0]
    if keep.size == 0:
        return sparse.csr_matrix((X_expanded.shape[0], n_can), dtype=X_expanded.dtype)

    P = sparse.csr_matrix(
        (np.ones(keep.size, dtype=np.float32), (keep, tgt[keep])),
        shape=(n_exp, n_can),
    )
    return X_expanded @ P
