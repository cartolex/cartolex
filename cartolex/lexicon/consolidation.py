# SPDX-License-Identifier: MIT
"""Stage 3: Consolidate raw keywords, build dictionary, and score researchers."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

from cartolex.atlas.model_files import save_vectorizer

from .canonicalization import (
    canonical_concept,
    fold_number_variants,
    fold_tfidf_to_canonical,
    nested_filter,
)
from .io_helpers import CorpusError, load_documents_selected, slot_indexes, write_roster
from .lexicon_store import (
    load_manual_blacklist,
    load_translation_map,
)
from .tfidf_utils import (
    compute_keywords_by_researcher,
    compute_keywords_by_unit,
    compute_keywords_domain,
)
from .whitelist import build_whitelist_set, load_person_whitelist, load_whitelist, whitelist_terms

if TYPE_CHECKING:
    from cartolex.context import RunContext

logger = logging.getLogger(__name__)


def load_and_merge_raw_keywords(ctx: RunContext) -> pd.DataFrame:
    """Load and merge the per-language raw keyword tables produced by Stage 1.

    One raw table per configured corpus language (``ctx.paths.raw_terms_csv``),
    each tagged with its ``lang_source``. The empty table of a corpus language
    without text (skipped by the extraction) contributes nothing.
    """
    parts: list[pd.DataFrame] = []
    for lang in ctx.settings.corpus_languages:
        df_lang = pd.read_csv(ctx.paths.raw_terms_csv(lang))
        if df_lang.empty:
            continue
        df_lang["lang_source"] = lang
        # Use score_len when present, else fall back to score.
        if "score_len" not in df_lang.columns:
            df_lang["score_len"] = df_lang["score"]
        parts.append(df_lang)

    if not parts:
        raise ValueError(
            "Every raw keyword table is empty: no corpus language has candidate terms."
        )
    df = pd.concat(parts, ignore_index=True)
    # Ensure terms are strings (handle NaNs or numeric keywords). Drop real NA first:
    # pandas >= 3 read_csv yields the string dtype whose NA sentinel is a float ``nan``
    # that ``astype(str)`` no longer turns into the literal ``"nan"``, so the subsequent
    # ``replace("nan", "")`` would not catch it. The replace still strips the literal
    # ``"nan"`` string that older (pandas 2) runs may have baked into a saved CSV.
    df = df[df["term"].notna()].copy()
    df["term"] = df["term"].astype(str).replace("nan", "")
    df = df[df["term"].str.strip() != ""]
    return df


def _run_pipeline_core(
    ctx: RunContext, progress_callback: Callable[[int, str], None] | None = None
) -> None:
    def report(pct: int, msg: str) -> None:
        if progress_callback:
            progress_callback(pct, msg)
        logger.info("[%d%%] %s", pct, msg)

    cfg = ctx.settings
    paths = ctx.paths
    # The run's additions and removals apply here (and only here): the merge
    # map, and the global blacklist, which exists only when the workspace has
    # an override file.
    stopwords = ctx.stopwords.adjusted
    global_blacklist = ctx.stopwords.consolidation_blacklist
    # Pre-existing mislabel kept for behavioral stability: the operational
    # post-triage cutoff reads global_top_n, not cfg.refined_top_n.
    refined_top_n = cfg.global_top_n
    paths.automatic_dir.mkdir(parents=True, exist_ok=True)
    paths.models_dir.mkdir(parents=True, exist_ok=True)

    # 1. Verification
    report(5, "Checking inputs...")
    missing = [lang for lang in cfg.corpus_languages if not paths.raw_terms_csv(lang).exists()]
    if missing:
        raise FileNotFoundError(
            f"Raw keywords file(s) missing for: {', '.join(missing)}. "
            "Run the extraction stage first."
        )

    # 2. Load Extraction Results
    report(10, "Loading extracted keywords...")
    global_df = load_and_merge_raw_keywords(ctx)

    # 3. Canonicalization
    report(15, "Applying translation and canonicalization logic...")

    # 3b. Load Maps
    translation_map = load_translation_map(paths.translation_cache_json)
    merged_map = {**stopwords.merge_map, **translation_map}

    if paths.canonical_map_json.exists():
        canon_map = json.loads(paths.canonical_map_json.read_text(encoding="utf-8"))
    else:
        canon_map = {}

    # 3b-LLM. Load LLM decisions if available (overrides/supplements above)
    llm_decisions = {}
    llm_rejected = set()
    llm_term_lang: dict[str, str] = {}  # term -> detected language (fr/en)
    if paths.triage_decisions_json.exists():
        try:
            llm_decisions = json.loads(paths.triage_decisions_json.read_text(encoding="utf-8"))
            report(
                16,
                f"Loaded LLM decisions ({len(llm_decisions.get('accepted', []))} accepted, "
                f"{len(llm_decisions.get('rejected', []))} rejected)",
            )
            # LLM canonical map takes priority
            llm_canon = llm_decisions.get("canonical_map", {})
            if llm_canon:
                canon_map.update(llm_canon)
            # LLM translation map supplements existing
            llm_trans = llm_decisions.get("translation_map", {})
            if llm_trans:
                merged_map.update(llm_trans)
            # LLM rejected terms become additional blacklist
            llm_rejected = set(llm_decisions.get("rejected", []))
            # Per-term language from LLM (Phase 1/2 decisions)
            llm_term_lang = llm_decisions.get("term_lang", {})
            if not llm_term_lang:
                # Backward compat: reconstruct from unigram/multiword dicts
                for t, d in llm_decisions.get("unigrams", {}).items():
                    if isinstance(d, dict) and "lang" in d:
                        llm_term_lang[t] = d["lang"]
                for t, d in llm_decisions.get("multiword", {}).items():
                    if isinstance(d, dict) and "lang" in d:
                        llm_term_lang[t] = d["lang"]
        except Exception as e:
            report(16, f"Warning: could not load LLM decisions: {e}")

    # 3c. Compute 'Concept' column
    # Priority: LLM canonical_en (already singular English) → translation/merge
    # map → heuristic singularization (legacy fallback for unclassified terms)
    def get_concept(term):
        # 1. Direct LLM lookup (handles FR→EN, plurals, abbreviations)
        if term in canon_map:
            return canon_map[term]
        # 2. Translation/merge map on raw term
        if term in merged_map:
            mapped = merged_map[term]
            return canon_map.get(mapped, mapped)
        # 3. Merge map via canonical_concept (lowercased lookup)
        mapped = canonical_concept(term, merged_map)
        return canon_map.get(mapped, mapped)

    global_df["concept"] = global_df["term"].apply(get_concept)

    # Safety net under the LLM keys: concepts that differ only by number
    # (« critical edition » / « critical editions ») collapse onto the heaviest.
    number_fold = fold_number_variants(global_df.groupby("concept")["score_len"].sum().to_dict())
    n_folded = sum(1 for c, r in number_fold.items() if c != r)
    if n_folded:
        report(16, f"Merged {n_folded} singular/plural concept variant(s).")
        global_df["concept"] = global_df["concept"].map(number_fold)

    # 3d. Aggregate Scores by Concept
    # We want to pick the 'best' representative term for each concept
    # Strategy: Group by concept -> Pick term with highest individual score as representative
    #           Sum scores for the concept

    concept_stats = []

    for concept, sub in global_df.groupby("concept"):
        total_score = sub["score_len"].sum()
        # Find representative term (max score)
        best_idx = sub["score_len"].idxmax()
        rep_term = sub.loc[best_idx, "term"]
        rep_len = sub.loc[best_idx, "len"]

        # Determine language for this concept.
        # Priority: LLM-detected language of the representative term,
        # then LLM-detected language of the concept string,
        # then fallback to dominant corpus source language.
        if rep_term in llm_term_lang:
            concept_lang = llm_term_lang[rep_term]
        elif concept in llm_term_lang:
            concept_lang = llm_term_lang[concept]
        else:
            # Fallback: dominant corpus source language
            lang_scores = sub.groupby("lang_source")["score_len"].sum()
            concept_lang = lang_scores.idxmax() if not lang_scores.empty else cfg.reference_language

        concept_stats.append(
            {
                "term": rep_term,  # Representative
                "concept": concept,  # ID
                "score": total_score,  # Combined Importance
                "len": rep_len,
                "lang": concept_lang,  # Dominant source language (fr/en)
            }
        )

    df_refined = pd.DataFrame(concept_stats)

    # 4. Filtering
    report(25, "Filtering refined list...")

    def _nested_filter_callback(current, total):
        pct = 25 + int(5 * current / total)
        report(pct, f"Filtering refined list ({current}/{total})...")

    # Nested Filter
    df_refined = nested_filter(
        df_refined,
        score_col="score",
        keep_threshold=cfg.nested_threshold,
        progress_callback=_nested_filter_callback,
    )

    # Blacklists
    manual_blacklist = load_manual_blacklist(paths.manual_blacklist_csv)
    full_blacklist = set(global_blacklist) | manual_blacklist | llm_rejected

    # Also check canon decisions (concepts rejected)
    if paths.canonical_decisions_json.exists():
        decisions = json.loads(paths.canonical_decisions_json.read_text())
        rejected = set(decisions.get("rejected", []))
        full_blacklist |= rejected

    # Manual keeplist: force-include terms even if LLM rejected them
    manual_keep = load_manual_blacklist(paths.manual_keep_csv)  # same CSV format
    full_blacklist -= manual_keep  # remove kept terms from blacklist

    df_refined = df_refined[~df_refined["term"].isin(full_blacklist)]
    df_refined = df_refined[~df_refined["concept"].isin(full_blacklist)]

    # LLM acceptance gate: if LLM decisions exist, only keep terms whose
    # concept was explicitly accepted by the LLM (or manually kept).
    # This discards terms that were below the score cutoff and never sent
    # to the LLM in the first place.
    llm_accepted = set(llm_decisions.get("accepted", []))
    if llm_accepted:
        # Build the set of allowed concepts: anything the LLM accepted maps to
        allowed_concepts = set()
        for t in llm_accepted:
            allowed_concepts.add(t)
            c = canon_map.get(t)
            if c:
                allowed_concepts.add(c)
        allowed_concepts |= manual_keep
        n_before = len(df_refined)
        df_refined = df_refined[
            df_refined["concept"].isin(allowed_concepts)
            | df_refined["term"].isin(llm_accepted)
            | df_refined["term"].isin(manual_keep)
        ]
        n_dropped = n_before - len(df_refined)
        if n_dropped:
            report(29, f"LLM acceptance gate removed {n_dropped} terms not seen by LLM")

    df_refined = df_refined.sort_values("score", ascending=False).reset_index(drop=True)

    # Save Refined List (full — curation UI needs all terms)
    df_refined.to_csv(paths.refined_terms_csv, index=False)

    # Post-triage top-N cutoff: keep only the N highest-scoring concepts
    # for downstream processing (pairs, vectorizer, attribution).
    # The full list is still in the refined-terms file for curation.
    if refined_top_n > 0 and len(df_refined) > refined_top_n:
        n_before_cutoff = len(df_refined)
        df_refined = df_refined.head(refined_top_n).reset_index(drop=True)
        n_cut = n_before_cutoff - refined_top_n
        report(
            30,
            f"Post-triage top-{refined_top_n} cutoff removed {n_cut} tail concepts, "
            f"{len(df_refined)} remain",
        )

    # 5. Parallel display-language lists
    report(30, "Building parallel language lists...")

    # For each surviving concept, pick the best available term in each display
    # language from the raw terms (grouped by their source language). A concept
    # with no term in a given language falls back to the representative term.
    parallel_rows = []
    concept_groups = global_df.groupby("concept")

    for row in df_refined.itertuples():
        conc = row.concept
        score = row.score
        if conc not in concept_groups.groups:
            continue
        sub = concept_groups.get_group(conc)

        entry = {"concept": conc}
        for lang in cfg.display_languages:
            lang_sub = sub[sub["lang_source"] == lang]
            if not lang_sub.empty:
                best = lang_sub.loc[lang_sub["score_len"].idxmax(), "term"]
                score_lang = float(lang_sub["score_len"].sum())
            else:
                best = row.term
                score_lang = 0.0
            entry[f"term_{lang}"] = best
            entry[f"score_{lang}"] = score_lang
        entry["total_score"] = score
        parallel_rows.append(entry)

    pairs_df = pd.DataFrame(parallel_rows).sort_values("total_score", ascending=False)
    pairs_df.to_csv(paths.refined_pairs_csv, index=False)

    # Save individual single-language lists (just terms).
    for lang in cfg.display_languages:
        pairs_df[[f"term_{lang}", "concept", "total_score"]].rename(
            columns={f"term_{lang}": "term", "total_score": "score"}
        ).to_csv(paths.refined_terms_lang_csv(lang), index=False)

    # 6. Restricted Vectorization Preparation
    report(50, "Preparing Restricted Vectorizer...")

    # Vocabulary: All concepts (representative terms) + Aliases from Raw
    # We need a map: Raw Term -> Concept

    # We want to feed the vectorizer a vocabulary of ALL raw terms that map to accepted concepts.
    # This ensures that when we scan a researcher's doc, we count "algos" as "algorithm".

    # Accepted concepts
    accepted_concepts = set(df_refined["concept"].values)

    # Filter global_df to only terms mapping to accepted concepts
    relevant_raw = global_df[global_df["concept"].isin(accepted_concepts)]

    # Build Term -> Concept map for 'folding' later
    # Note: 'term' in global_df might be duplicated (same term in FR and EN file).
    # Unique terms:
    term_alias_map = dict(zip(relevant_raw["term"], relevant_raw["concept"], strict=False))

    # Whitelist handling: axis whitelist plus the operator-curated person
    # whitelist, so force-accepted person keywords ride the same protected
    # path through vocabulary building and top-N attribution.
    whitelist_set = build_whitelist_set(
        whitelist_terms(load_whitelist(paths.whitelist_json)),
        load_person_whitelist(paths.person_whitelist_csv),
    )

    # Vocabulary for Vectorizer is all unique raw terms that are accepted + whitelist
    vocab_list = sorted(set(term_alias_map.keys()) | whitelist_set)

    pd.DataFrame(
        {"alias": list(term_alias_map.keys()), "canonical": list(term_alias_map.values())}
    ).to_csv(paths.term_aliases_csv, index=False)

    # 7. Load Corpus & Vectorize
    report(60, "Loading corpus for attribution...")

    def _corpus_progress(pct: int, msg: str) -> None:
        # Map load_documents_selected 0-100% into our 60-72% range
        report(60 + int(pct * 0.12), f"Loading corpus: {msg}")

    # The corpus INDEX of each fit slot (files carrying a ``txt_path`` column),
    # in the settings' order: slot order is document order.
    fit_indexes = slot_indexes(ctx)
    docs, meta_df = load_documents_selected(
        fit_indexes,
        progress_callback=_corpus_progress,
        now_year=ctx.now_year,
        recency_years=cfg.kw_recency_years or None,
    )

    report(73, f"Vectorizing {len(docs)} documents against {len(vocab_list)} terms...")
    vectorizer = TfidfVectorizer(
        lowercase=True,
        vocabulary=vocab_list,
        ngram_range=cfg.ngram_range,
    )

    X = vectorizer.fit_transform(docs)
    feature_names = vectorizer.get_feature_names_out()

    # Plain-TF quantity track: X stores l2-normalised tf·idf; dividing each
    # column by idf_ recovers tf up to a per-row constant that cancels under
    # the per-researcher L1 shares computed downstream. Only valid while the
    # vectorizer keeps use_idf=True and sublinear_tf=False, and only on the
    # expanded matrix — folded concept columns mix per-alias IDFs.
    assert vectorizer.use_idf and not vectorizer.sublinear_tf
    X_tf = X.multiply(1.0 / vectorizer.idf_).tocsr()

    report(77, f"Vectorization done ({X.shape[0]} docs × {X.shape[1]} features). Saving model...")

    # Save Model
    save_vectorizer(vectorizer, paths.vectorizer_json)

    # 8. Folding (Raw Terms -> Concepts)
    report(80, f"Folding {len(feature_names)} raw terms to {len(accepted_concepts)} concepts...")

    # Our 'term_alias_map' maps raw terms to concepts.
    # Features in X are raw terms.
    # We want to sum columns of X that map to same concept.

    # Use helper `fold_tfidf_to_canonical`
    # It expects: alias_to_canon dict.
    # And canonical_terms list (the target columns).

    target_concepts = sorted(
        accepted_concepts | whitelist_set
    )  # Whitelist might not be in concept map, treat as self

    # Ensure whitelist terms in alias map map to themselves if missing
    for w in whitelist_set:
        if w not in term_alias_map:
            term_alias_map[w] = w

    X_folded = fold_tfidf_to_canonical(
        X_expanded=X,
        expanded_terms=feature_names,
        canonical_terms=target_concepts,
        alias_to_canon=term_alias_map,
    )
    X_tf_folded = fold_tfidf_to_canonical(
        X_expanded=X_tf,
        expanded_terms=feature_names,
        canonical_terms=target_concepts,
        alias_to_canon=term_alias_map,
    )

    final_features = np.array(target_concepts, dtype=object)

    # Build concept -> lang lookup for attribution outputs
    concept_lang_map = dict(zip(df_refined["term"], df_refined["lang"], strict=False))

    # 9. Attribution (researchers, units, the whole domain)
    report(85, "Computing per-researcher keywords...")
    res_df = compute_keywords_by_researcher(
        X_folded,
        final_features,
        meta_df,
        whitelist_set,
        cfg.top_n_researcher,
        cfg.length_bonus_alpha,
        X_tf=X_tf_folded.tocsr(),
    )
    res_df["lang"] = res_df["term"].map(concept_lang_map).fillna(cfg.reference_language)
    res_df.to_csv(paths.person_terms_csv, index=False)
    report(90, f"Researcher keywords saved ({len(res_df)} rows). Computing per-unit keywords...")

    unit_df = compute_keywords_by_unit(
        X_folded,
        final_features,
        meta_df,
        whitelist_set,
        cfg.top_n_unit,
        cfg.length_bonus_alpha,
    )
    unit_df["lang"] = unit_df["term"].map(concept_lang_map).fillna(cfg.reference_language)
    unit_df.to_csv(paths.group_terms_csv, index=False)
    report(95, f"Unit keywords saved ({len(unit_df)} rows). Computing domain keywords...")

    domain_df = compute_keywords_domain(
        X_folded, final_features, whitelist_set, cfg.top_n_domain, cfg.length_bonus_alpha
    )
    domain_df["lang"] = domain_df["term"].map(concept_lang_map).fillna(cfg.reference_language)
    domain_df.to_csv(paths.domain_terms_csv, index=False)
    report(98, f"Domain keywords saved ({len(domain_df)} rows). Finalizing...")

    # Regenerate the researcher roster required by the atlas stages. Built from
    # the fit slots' indexes (full roster, one row per researcher), independent
    # of the keyword recency and document-type windows applied above.
    try:
        n_researchers = write_roster(
            index_csvs=[index_csv for _, index_csv, _ in fit_indexes], out_csv=paths.roster_csv
        )
        report(98, f"Researcher index saved ({n_researchers} researchers).")
    except CorpusError as exc:
        logger.warning("Could not build the researcher roster: %s", exc)

    # Write the run's settings snapshot, so the atlas stages and a later
    # analysis can see the processing configuration (corpus slots and window,
    # TF-IDF/extraction, consolidation cutoffs, LLM triage). snapshot_version 3
    # records the corpus slot registry. Values are the RUN's — read from the
    # run context's settings, never freshly re-read defaults.
    paths.run_settings_json.write_text(
        json.dumps(
            {
                "corpus_slots": [slot.as_dict() for slot in cfg.corpus_slots],
                # Legacy mislabel kept as-is: carries cfg.global_top_n, NOT
                # cfg.refined_top_n (see "triage_refined_top_n" below).
                "refined_top_n": refined_top_n,
                "snapshot_version": 3,
                # ISO calendar date of this pipeline run, so downstream window
                # reconstruction (e.g. a contribution-bundle manifest) doesn't
                # have to guess the run date from the export/build date, which
                # is wrong whenever the export happens across a year boundary
                # from the run.
                "snapshot_date": date.today().isoformat(),
                "kw_recency_years": cfg.kw_recency_years,
                "ngram_range": list(cfg.ngram_range),
                "min_df": cfg.min_df,
                "max_df": cfg.max_df,
                "max_features": cfg.max_features,
                "weights_basis": cfg.weights_basis,
                "length_bonus_alpha": cfg.length_bonus_alpha,
                "global_top_n": cfg.global_top_n,
                "nested_threshold": cfg.nested_threshold,
                "top_n_researcher": cfg.top_n_researcher,
                "top_n_unit": cfg.top_n_unit,
                "top_n_domain": cfg.top_n_domain,
                "use_llm": cfg.use_llm,
                "llm_model": cfg.llm_model,
                "llm_min_score": cfg.llm_min_score,
                # The TRUE cfg.refined_top_n (post-triage keep-top-N cutoff,
                # 0 = all), under an honest name — "refined_top_n" above is
                # the legacy mislabeled duplicate of global_top_n.
                "triage_refined_top_n": cfg.refined_top_n,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    report(99, f"Saved {paths.run_settings_json}")

    report(100, "Pipeline complete.")


def run_pipeline(ctx: RunContext, *, progress_callback=None) -> None:
    """Run Stage 3 (consolidation, scoring, outputs) of a run.

    Reads the raw keyword tables, the triage decisions and the operator
    files named by ``ctx.paths``; applies the run's stop-word additions and
    removals (``ctx.stopwords``); writes the refined lists, the per-person,
    per-group and domain tables, the restricted vectorizer and term aliases,
    the person roster and the run's settings snapshot.
    """
    with ctx.threads.applied():
        _run_pipeline_core(ctx, progress_callback=ctx.percent_reporter(progress_callback))


run_pipeline_stage_3 = run_pipeline
