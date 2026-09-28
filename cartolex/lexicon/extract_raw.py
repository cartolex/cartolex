# SPDX-License-Identifier: MIT
"""Stage 1: split the corpus by language and extract candidate terms (noun phrases).

Each text is split into paragraphs by language; every paragraph is parsed by
the language's model (:mod:`cartolex.lexicon.language_models`); the noun
phrases found by the language's patterns (:mod:`cartolex.lexicon.noun_phrases`),
nested spans included and grouped by lemma, are the candidates. They are
scored by :mod:`cartolex.lexicon.scoring`: a window on the people who use
them (``min_df`` people at least, ``max_df`` of them at most), a TF-IDF whose
documents are the counting unit (``KeywordsConfig.counting_unit``: a person
by default), summed, times the length bonus; each kept candidate falls in a
band (kept, to check, set aside) with a reason.

Output per language (``paths.raw_terms_csv(lang)``): ``term`` (the candidate's
most frequent surface form), ``score``, ``len`` (words of the term),
``score_len``, ``forms`` (every surface form, most frequent first, separated
by ``|``), ``people`` and ``texts`` (how many use it), ``band`` and ``reason``,
sorted by ``score_len``; and the merged list (``paths.global_terms_csv``).
"""

from __future__ import annotations

import logging
import multiprocessing
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from . import language_models
from .config import KeywordsConfig
from .io_helpers import (
    CorpusError,
    PersonTexts,
    load_texts_split_by_language,
    slot_indexes,
)
from .lexicon_store import (
    load_canonical_decision_blacklist,
    load_manual_blacklist,
)
from .noun_phrases import TextAnalysis, analyse
from .parse_cache import ParseCache, text_key
from .scoring import (
    FORMS_SEPARATOR,
    RAW_COLUMNS,
    ScoredCandidates,
    ScoringOptions,
    TextUnit,
    score_units,
)
from .text_utils import heal_split_words

if TYPE_CHECKING:
    from cartolex.context import RunContext

logger = logging.getLogger(__name__)

__all__ = [
    "FORMS_SEPARATOR",
    "RAW_COLUMNS",
    "analyse_texts",
    "language_units",
    "options_of",
    "parse_texts",
    "run_pipeline_stage_1",
    "score_language",
]
#: Texts per parser batch (and per task of a worker process).
PARSE_BATCH = 64
#: Longest text parsed in one piece, in characters; a longer paragraph is cut
#: at line breaks or sentence ends first.
MAX_PIECE_CHARS = 10_000

ProgressCallback = Callable[[int, str], None]


# ── Texts ───────────────────────────────────────────────────────────────────


def split_long(text: str, limit: int = MAX_PIECE_CHARS) -> list[str]:
    """*text* cut into pieces of at most *limit* characters.

    Cuts go at the last line break, else the last sentence end, else the last
    space before the limit (a phrase never spans a line break or a sentence
    end, so candidates are unaffected), and only as a last resort mid-word.
    """
    pieces = []
    rest = text.strip()
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! ")) + 1
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return [p for p in pieces if p]


def person_pieces(doc: str) -> list[str]:
    """The texts one person's language stream is parsed as: its paragraphs, long ones cut."""
    out: list[str] = []
    for para in doc.split("\n\n"):
        if para.strip():
            out += split_long(para)
    return out


# ── Parsing ─────────────────────────────────────────────────────────────────


def _parse_batch(lang: str, texts: Sequence[str]) -> list[TextAnalysis]:
    """Analyses of *texts*, parsed with *lang*'s model (loaded once per process)."""
    nlp = language_models.load(lang)
    return [analyse(doc, lang) for doc in nlp.pipe(texts, batch_size=PARSE_BATCH)]


def parse_texts(
    lang: str,
    texts: Sequence[str],
    *,
    n_jobs: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> list[TextAnalysis]:
    """The analyses of *texts* (in order), parsed with *lang*'s model.

    With *n_jobs* > 1 the texts are parsed in batches by that many worker
    processes, each with its own copy of the model; the analysis of a text
    does not depend on the batch it is parsed in, so the result is the same
    whatever the number of workers. *progress* is called with the number of
    texts done and the total.
    """
    total = len(texts)
    if not total:
        return []
    batches = [texts[i : i + PARSE_BATCH] for i in range(0, total, PARSE_BATCH)]
    out: list[TextAnalysis] = []
    if n_jobs > 1 and len(batches) > 1:
        # Fresh interpreters, never a fork of this process: the threads of the
        # numeric libraries make forking unsafe.
        context = multiprocessing.get_context("spawn")
        workers = min(n_jobs, len(batches))
        with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
            for batch in pool.map(_parse_batch, repeat(lang), batches):
                out += batch
                if progress:
                    progress(len(out), total)
        return out
    for batch in batches:
        out += _parse_batch(lang, batch)
        if progress:
            progress(len(out), total)
    return out


def analyse_texts(
    lang: str,
    texts: Collection[str],
    *,
    cache_dir: Path | None,
    n_jobs: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, TextAnalysis]:
    """The analysis of every text of *texts*, by text key, from the cache or parsed.

    Needs *lang*'s model to be installed, even when every text is cached (the
    cache is keyed by the installed model). New analyses are added to the
    cache under *cache_dir* when one is given (see
    :mod:`cartolex.lexicon.parse_cache`).
    """
    model = language_models.require(lang)
    by_key = {text_key(t): t for t in texts}
    cache = ParseCache(cache_dir, model.identity) if cache_dir is not None else None
    found = cache.read(by_key) if cache is not None else {}
    missing = sorted(k for k in by_key if k not in found)
    logger.info(
        "[%s] %d text(s) to analyse: %d from the parse cache, %d to parse with %s.",
        lang,
        len(by_key),
        len(found),
        len(missing),
        model.identity,
    )
    if missing:
        parsed = parse_texts(lang, [by_key[k] for k in missing], n_jobs=n_jobs, progress=progress)
        new = dict(zip(missing, parsed, strict=True))
        if cache is not None:
            cache.write(new)
        found.update(new)
    return found


# ── Candidates and scores ───────────────────────────────────────────────────


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=RAW_COLUMNS)


def options_of(cfg: KeywordsConfig) -> ScoringOptions:
    """The scoring options a run's settings give (the others keep their defaults)."""
    return ScoringOptions(
        counting_unit=cfg.counting_unit, length_bonus_alpha=cfg.length_bonus_alpha
    )


def score_language(
    lang: str,
    units: Sequence[TextUnit],
    n_people: int,
    cfg: KeywordsConfig,
    *,
    blacklist: Collection[str] = frozenset(),
    options: ScoringOptions | None = None,
) -> ScoredCandidates:
    """Score one language's candidates with the run's settings (see :mod:`.scoring`).

    Logs a warning when no candidate reaches the document-frequency window
    (no candidate at all, a window too few people can satisfy …): the table
    is then empty.
    """
    scored = score_units(
        lang,
        units,
        n_people,
        min_df=cfg.min_df,
        max_df=cfg.max_df,
        max_features=cfg.max_features,
        options=options if options is not None else options_of(cfg),
        blacklist=blacklist,
    )
    if scored.empty:
        logger.warning(
            "[%s] No candidate term within the document-frequency window "
            "(%d people, min_df=%s, max_df=%s).",
            lang,
            n_people,
            cfg.min_df,
            cfg.max_df,
        )
    return scored


def language_units(
    lang: str,
    people: Sequence[PersonTexts],
    *,
    cache_dir: Path | None = None,
    n_jobs: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> list[TextUnit]:
    """The analysed texts of one language, one :class:`TextUnit` per person and text.

    Words split by PDF extraction are rejoined first
    (:func:`~cartolex.lexicon.text_utils.heal_split_words`, the corpus being its
    own dictionary), then every paragraph is analysed (:func:`analyse_texts`,
    with the parse cache under *cache_dir*). A text's paragraphs form one part,
    ``full``: the corpus contract does not say which paragraph is a title.
    """
    rows = [
        (i, person.unit, text_id, paragraphs[lang])
        for i, person in enumerate(people)
        for text_id, paragraphs in person.texts
        if paragraphs.get(lang)
    ]
    docs, n_healed = heal_split_words(["\n\n".join(paras) for *_, paras in rows])
    if n_healed:
        logger.info("[%s] Rejoined %d split-word artifact(s) before parsing.", lang, n_healed)
    pieces = [person_pieces(doc) for doc in docs]
    analyses = analyse_texts(
        lang,
        {p for text in pieces for p in text},
        cache_dir=cache_dir,
        n_jobs=n_jobs,
        progress=progress,
    )
    return [
        TextUnit(person, unit, text_id, (("full", tuple(analyses[text_key(p)] for p in parts)),))
        for (person, unit, text_id, _), parts in zip(rows, pieces, strict=True)
    ]


# ── The stage ───────────────────────────────────────────────────────────────


def run_pipeline_stage_1(
    ctx: RunContext,
    *,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Stage 1 (candidate terms) over the corpus slots that build the map.

    Loads the index of every ``fit`` corpus slot (``ctx.settings.corpus_slots``,
    in order), detects the language of each paragraph, extracts and scores the
    noun-phrase candidates of each corpus language (analysed texts are kept in
    ``ctx.paths.parse_cache_dir``) and writes one raw keyword table per corpus
    language plus the merged list. A corpus language without text is skipped
    with a warning (its table is written empty). The model of every language
    with text must be installed: otherwise
    :class:`~cartolex.lexicon.language_models.LanguageModelMissing` is raised
    before anything is parsed. Progress also reaches the context's ``progress``,
    and its ``cancel`` is honoured at each report.
    """
    with ctx.threads.applied():
        _extract(ctx, ctx.percent_reporter(progress_callback))


def _extract(ctx: RunContext, progress_callback: ProgressCallback | None) -> None:
    cfg = ctx.settings
    paths = ctx.paths
    paths.automatic_dir.mkdir(parents=True, exist_ok=True)

    def log(p: int, m: str) -> None:
        if progress_callback:
            progress_callback(p, m)
        logger.info("[%d%%] %s", p, m)

    log(0, "Loading documents and splitting by language...")
    n_jobs = ctx.threads.workers(cfg.extraction_n_jobs)
    people, _meta_df = load_texts_split_by_language(
        slot_indexes(ctx),
        progress_callback=lambda p, m: log(p * 20 // 100, m),
        corpus_languages=cfg.corpus_languages,
        n_jobs=n_jobs,
        now_year=ctx.now_year,
        recency_years=cfg.kw_recency_years or None,
    )
    texts_in = {
        lang: sum(1 for person in people for _, paras in person.texts if paras.get(lang))
        for lang in cfg.corpus_languages
    }
    log(20, " | ".join(f"Texts {lang.upper()}: {n}" for lang, n in texts_in.items()))

    with_text = [lang for lang in cfg.corpus_languages if texts_in[lang]]
    # Every model the run needs, checked before any parsing starts.
    for lang in with_text:
        language_models.require(lang)

    # The project's own rejections (never packaged stop lists).
    blacklist = load_manual_blacklist(paths.manual_blacklist_csv) | (
        load_canonical_decision_blacklist(paths.canonical_decisions_json)
    )

    n_langs = max(len(cfg.corpus_languages), 1)
    global_parts: list[pd.DataFrame] = []
    for i, lang in enumerate(cfg.corpus_languages):
        lo = 25 + int(65 * i / n_langs)
        span = int(65 / n_langs)
        out_path = paths.raw_terms_csv(lang)
        if lang not in with_text:
            # A corpus language no paragraph was detected in (an English-only
            # corpus with the default two languages): nothing to parse. Its raw
            # table is written empty so later stages find every language.
            logger.warning(
                "No text in corpus language %r: its extraction is skipped "
                "(check KeywordsConfig.corpus_languages).",
                lang,
            )
            _empty().to_csv(out_path, index=False)
            continue
        log(lo, f"Extracting {lang.upper()} candidate terms...")

        def parsed(done: int, total: int, lo: int = lo, span: int = span, lang: str = lang) -> None:
            pct = lo + int(0.8 * span * done / max(total, 1))
            log(pct, f"Parsed {done}/{total} new {lang.upper()} texts")

        try:
            units = language_units(
                lang, people, cache_dir=paths.parse_cache_dir, n_jobs=n_jobs, progress=parsed
            )
        finally:
            # A model holds a few hundred MB: only one is loaded at a time, and
            # none once the stage is over.
            language_models.release(lang)
        df_lang = score_language(lang, units, len(people), cfg, blacklist=blacklist).table
        df_lang.to_csv(out_path, index=False)
        log(lo + span, f"Saved {len(df_lang)} {lang.upper()} candidate terms to {out_path}")
        g = df_lang[["term", "score", "len", "score_len", "band", "reason"]].copy()
        g["lang"] = lang
        g.rename(columns={"score": "score_raw"}, inplace=True)
        global_parts.append(g)

    if not with_text:
        raise CorpusError(
            "No text in any corpus language "
            f"({', '.join(cfg.corpus_languages)}): nothing to extract."
        )

    # --- Merge the language streams into the global list the triage reads ---
    log(92, "Merging language streams into the global candidate list...")
    df_global = pd.concat(global_parts, ignore_index=True)
    # Deduplicate: keep the highest score_len per term (ties: the first language).
    df_global = (
        df_global.sort_values("score_len", ascending=False, kind="mergesort")
        .drop_duplicates(subset="term", keep="first")
        .reset_index(drop=True)
    )
    df_global.to_csv(paths.global_terms_csv, index=False)
    log(95, f"Saved {len(df_global)} deduplicated terms to {paths.global_terms_csv}")
    log(100, "Done.")
