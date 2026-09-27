# SPDX-License-Identifier: MIT
"""Stage 1: Split corpus by language, extract raw keywords, filter garbage."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

from .config import KeywordsConfig
from .io_helpers import CorpusError, load_documents_split_by_language, slot_indexes
from .lexical_filters import filter_global_terms
from .lexicon_store import (
    load_canonical_decision_blacklist,
    load_manual_blacklist,
)
from .stopwords_config import StopwordLists, packaged_lists
from .text_utils import heal_split_words, length_bonus

if TYPE_CHECKING:
    from cartolex.context import RunContext

logger = logging.getLogger(__name__)

#: Columns of a raw keyword table (``raw_keywords_<lang>.csv``).
_RAW_COLUMNS = ["term", "score", "len", "score_len"]


def run_extraction(
    lang: str,
    docs: list[str],
    blacklist: set[str] | frozenset[str],
    cfg: KeywordsConfig | None = None,
    *,
    stopwords: StopwordLists | None = None,
):
    """Run TF-IDF extraction for one language.

    Args:
        lang: Language code (``"fr"`` or ``"en"``).
        docs: Per-document texts for that language.
        blacklist: Terms to exclude from the vocabulary.
        cfg: Pipeline configuration (n-gram range, df thresholds, scoring);
            defaults to ``KeywordsConfig()``.
        stopwords: The stop-word lists of the filters (default: the packaged ones).

    Returns the scored keyword candidates for the language.
    """
    if cfg is None:
        cfg = KeywordsConfig()
    lists = stopwords if stopwords is not None else packaged_lists()
    # Rejoin words PDF extraction split mid-token (pypdf inserts spurious
    # intra-word spaces): "adh esion" → "adhesion". Done before vectorizing so
    # the n-gram statistics see the healed words, using the corpus as its own
    # dictionary (see cartolex.lexicon.text_utils.heal_split_words).
    docs, n_healed = heal_split_words(docs)
    if n_healed:
        logger.info("[%s] Rejoined %d split-word artifact(s) before vectorizing.", lang, n_healed)

    logger.info("[%s] Vectorizing %d documents...", lang, len(docs))

    vectorizer = TfidfVectorizer(
        lowercase=True,
        ngram_range=cfg.ngram_range,
        min_df=cfg.min_df,
        max_df=cfg.max_df,
        max_features=cfg.max_features,
    )

    X = vectorizer.fit_transform(docs)
    terms = vectorizer.get_feature_names_out()

    # Global score summing
    scores = X.sum(axis=0).A1

    df = pd.DataFrame({"term": terms, "score": scores})

    # 1. Length Bonus
    scores_len, lens = length_bonus(
        df["term"].values, df["score"].values, alpha=cfg.length_bonus_alpha
    )
    df["len"] = lens
    df["score_len"] = scores_len

    # 2. Filtering
    logger.info("[%s] Filtering %d raw terms...", lang, len(df))

    df_filtered = filter_global_terms(
        df,
        names=lists.person_names,
        blacklist=blacklist,
        midwords=lists.midwords,
        single_blacklist=lists.single_blacklist,
        admin_patterns=list(lists.admin_patterns),
        junk_patterns=list(lists.junk_patterns),
    )

    # Sort
    df_filtered = df_filtered.sort_values("score_len", ascending=False)

    return df_filtered


def run_pipeline_stage_1(
    ctx: RunContext,
    *,
    progress_callback=None,
) -> None:
    """Stage 1 (TF-IDF extraction) over the corpus slots that build the map.

    Loads the index of every ``fit`` corpus slot (``ctx.settings.corpus_slots``,
    in order), detects the language of each paragraph, applies the lexical
    filters (the packaged stop-word lists) and writes one raw keyword table per
    corpus language plus the merged list. Progress also reaches the context's
    ``progress``, and its ``cancel`` is honoured at each report.
    """
    with ctx.threads.applied():
        _extract(ctx, ctx.percent_reporter(progress_callback))


def _extract(ctx: RunContext, progress_callback) -> None:
    cfg = ctx.settings
    paths = ctx.paths
    lists = ctx.stopwords.packaged
    paths.automatic_dir.mkdir(parents=True, exist_ok=True)

    def log(p, m):
        if progress_callback:
            progress_callback(p, m)
        logger.info("[%d%%] %s", p, m)

    log(0, "Loading documents and splitting by language...")
    docs_by_lang, meta_df = load_documents_split_by_language(
        slot_indexes(ctx),
        progress_callback=lambda p, m: log(p, m),
        corpus_languages=cfg.corpus_languages,
        n_jobs=ctx.threads.workers(cfg.extraction_n_jobs),
        now_year=ctx.now_year,
        recency_years=cfg.kw_recency_years or None,
    )

    log(
        20,
        " | ".join(
            f"Docs {lang.upper()}: {len(docs_by_lang[lang])}" for lang in cfg.corpus_languages
        ),
    )

    # Prepare blacklist
    bl_manual = load_manual_blacklist(paths.manual_blacklist_csv)
    bl_canon = load_canonical_decision_blacklist(paths.canonical_decisions_json)

    # Standard static blacklist
    full_blacklist = (
        lists.basic_blacklist
        | lists.admin_tokens
        | lists.geo_terms
        | lists.org_acronyms
        | lists.person_names
    )
    # User dynamic blacklist
    full_blacklist |= bl_manual
    full_blacklist |= bl_canon

    # --- Per-language extraction (one TF-IDF pass and one raw_keywords_<lang>.csv per stream) ---
    n_langs = max(len(cfg.corpus_languages), 1)
    global_parts: list[pd.DataFrame] = []
    skipped: list[str] = []
    for i, lang in enumerate(cfg.corpus_languages):
        pct = 30 + int(55 * i / n_langs)
        out_path = paths.raw_terms_csv(lang)
        if not any(doc.strip() for doc in docs_by_lang[lang]):
            # A corpus language no paragraph was detected in (an English-only
            # corpus with the default two languages): nothing to vectorize.
            # Its raw table is written empty so later stages find every
            # configured language.
            logger.warning(
                "No text in corpus language %r: its extraction is skipped "
                "(check KeywordsConfig.corpus_languages).",
                lang,
            )
            pd.DataFrame(columns=_RAW_COLUMNS).to_csv(out_path, index=False)
            skipped.append(lang)
            continue
        log(pct, f"Extracting {lang.upper()} keywords...")
        df_lang = run_extraction(
            lang.upper(), docs_by_lang[lang], full_blacklist, cfg=cfg, stopwords=lists
        )
        df_lang.to_csv(out_path, index=False)
        log(pct + int(55 / n_langs), f"Saved {len(df_lang)} {lang.upper()} keywords to {out_path}")
        g = df_lang[["term", "score", "len", "score_len"]].copy()
        g["lang"] = lang
        g.rename(columns={"score": "score_raw"}, inplace=True)
        global_parts.append(g)

    if len(skipped) == len(cfg.corpus_languages):
        raise CorpusError(
            "No text in any corpus language "
            f"({', '.join(cfg.corpus_languages)}): nothing to extract."
        )

    # --- Merge all language streams into keywords_global.csv for Stage 2 (LLM triage) ---
    log(92, "Merging language streams into keywords_global.csv...")
    df_global = pd.concat(global_parts, ignore_index=True)
    # Deduplicate: keep highest score_len per term
    df_global = (
        df_global.sort_values("score_len", ascending=False)
        .drop_duplicates(subset="term", keep="first")
        .reset_index(drop=True)
    )
    df_global.to_csv(paths.global_terms_csv, index=False)
    log(95, f"Saved {len(df_global)} deduplicated terms to {paths.global_terms_csv}")

    log(100, "Done.")
