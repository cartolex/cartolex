# SPDX-License-Identifier: MIT
"""Stage 1: split the corpus by language and extract candidate terms (noun phrases).

Each person is one document: their texts, concatenated, split into one stream
per corpus language by paragraph. Every paragraph is parsed by the language's
model (:mod:`cartolex.lexicon.language_models`); the noun phrases found by the
language's patterns (:mod:`cartolex.lexicon.noun_phrases`), nested spans
included and grouped by lemma, are the candidates. They are scored as before:
a TF-IDF with the person as the document (``min_df`` people at least,
``max_df`` of them at most), summed over people, times the length bonus.

Output per language (``paths.raw_terms_csv(lang)``): ``term`` (the candidate's
most frequent surface form), ``score``, ``len`` (words of the term),
``score_len`` and ``forms`` (every surface form of the candidate, most frequent
first, separated by ``|``), sorted by ``score_len``; and the merged list
(``paths.global_terms_csv``).
"""

from __future__ import annotations

import logging
import multiprocessing
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

from . import language_models
from .config import KeywordsConfig
from .io_helpers import CorpusError, load_documents_split_by_language, slot_indexes
from .lexical_filters import is_malformed_term
from .lexicon_store import (
    load_canonical_decision_blacklist,
    load_manual_blacklist,
)
from .noun_phrases import TextAnalysis, analyse, lemma_table, occurrences
from .parse_cache import ParseCache, text_key
from .text_utils import heal_split_words, length_bonus, tokenize

if TYPE_CHECKING:
    from cartolex.context import RunContext

logger = logging.getLogger(__name__)

#: Columns of a raw keyword table (``raw_keywords_<lang>.csv``).
RAW_COLUMNS = ["term", "score", "len", "score_len", "forms"]
#: Separator of the surface forms in the ``forms`` column.
FORMS_SEPARATOR = "|"
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


def _features(doc: list[str]) -> list[str]:
    """The TF-IDF analyzer: a person's candidate keys are already the features."""
    return doc


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=RAW_COLUMNS)


def _blocked(term: str, blacklist: Collection[str]) -> bool:
    low = term.lower()
    return low in blacklist or any(t in blacklist for t in tokenize(low))


def score_candidates(
    lang: str,
    person_analyses: Sequence[Sequence[TextAnalysis]],
    cfg: KeywordsConfig,
    *,
    blacklist: Collection[str] = frozenset(),
) -> pd.DataFrame:
    """The scored candidate terms of one language (its raw keyword table).

    *person_analyses* holds, for each person, the analyses of their texts in
    this language. Candidates are grouped by key; a person's counts form their
    document; the TF-IDF keeps the keys found in at least ``cfg.min_df``
    people and at most ``cfg.max_df`` of them, and a key's score is its TF-IDF
    summed over people. A candidate whose shown form, or one of its words, is
    in *blacklist* (the project's own rejections) is left out, as are
    malformed strings. No language without candidates raises: it gives an
    empty table, with a warning.
    """
    lemmas = lemma_table(a for person in person_analyses for a in person)
    per_text: dict[int, Counter[tuple[str, str]]] = {}
    surfaces: dict[str, Counter[str]] = {}
    docs: list[list[str]] = []
    for person in person_analyses:
        counts: Counter[str] = Counter()
        for a in person:
            pairs = per_text.get(id(a))
            if pairs is None:
                pairs = per_text[id(a)] = Counter(occurrences([a], lang, lemmas))
            for (key, surface), n in pairs.items():
                counts[key] += n
                surfaces.setdefault(key, Counter())[surface] += n
        docs.append(list(counts.elements()))

    vectorizer = TfidfVectorizer(
        analyzer=_features,
        min_df=cfg.min_df,
        max_df=cfg.max_df,
        max_features=cfg.max_features,
    )
    try:
        X = vectorizer.fit_transform(docs)
    except ValueError as exc:
        # No candidate at all, none inside the document-frequency window, or a
        # window that too few people cannot satisfy.
        logger.warning(
            "[%s] No candidate term within the document-frequency window "
            "(%d people, min_df=%s, max_df=%s): %s",
            lang,
            len(docs),
            cfg.min_df,
            cfg.max_df,
            exc,
        )
        return _empty()
    keys = vectorizer.get_feature_names_out()
    scores = X.sum(axis=0).A1

    rows = []
    for key, score in zip(keys, scores, strict=True):
        ranked = sorted(surfaces[key].items(), key=lambda kv: (-kv[1], kv[0]))
        term = ranked[0][0]
        if is_malformed_term(term) or _blocked(term, blacklist):
            continue
        rows.append(
            {
                "term": term,
                "score": float(score),
                "forms": FORMS_SEPARATOR.join(form for form, _ in ranked),
            }
        )
    if not rows:
        return _empty()
    df = pd.DataFrame(rows)
    scores_len, lens = length_bonus(
        df["term"].tolist(), df["score"].to_numpy(), alpha=cfg.length_bonus_alpha
    )
    df["len"] = lens
    df["score_len"] = scores_len
    df = df.sort_values(["score_len", "term"], ascending=[False, True], kind="mergesort")
    # Two keys practically never share their most frequent form; keep one if they do.
    df = df.drop_duplicates(subset="term", keep="first").reset_index(drop=True)
    return df[RAW_COLUMNS]


def extract_language(
    lang: str,
    docs: Sequence[str],
    cfg: KeywordsConfig | None = None,
    *,
    cache_dir: Path | None = None,
    blacklist: Collection[str] = frozenset(),
    n_jobs: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> pd.DataFrame:
    """The raw keyword table of one language from its per-person documents.

    *docs* holds one document per person (their paragraphs in *lang*, joined
    by blank lines). Words split by PDF extraction are rejoined first
    (:func:`~cartolex.lexicon.text_utils.heal_split_words`), then every
    paragraph is analysed (:func:`analyse_texts`, with the parse cache under
    *cache_dir*) and the candidates are scored (:func:`score_candidates`).
    """
    cfg = cfg if cfg is not None else KeywordsConfig()
    docs, n_healed = heal_split_words(list(docs))
    if n_healed:
        logger.info("[%s] Rejoined %d split-word artifact(s) before parsing.", lang, n_healed)
    pieces = [person_pieces(doc) for doc in docs]
    analyses = analyse_texts(
        lang,
        {p for person in pieces for p in person},
        cache_dir=cache_dir,
        n_jobs=n_jobs,
        progress=progress,
    )
    person_analyses = [[analyses[text_key(p)] for p in person] for person in pieces]
    return score_candidates(lang, person_analyses, cfg, blacklist=blacklist)


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
    docs_by_lang, _meta_df = load_documents_split_by_language(
        slot_indexes(ctx),
        progress_callback=lambda p, m: log(p * 20 // 100, m),
        corpus_languages=cfg.corpus_languages,
        n_jobs=n_jobs,
        now_year=ctx.now_year,
        recency_years=cfg.kw_recency_years or None,
    )
    log(
        20,
        " | ".join(
            f"Docs {lang.upper()}: {len(docs_by_lang[lang])}" for lang in cfg.corpus_languages
        ),
    )

    with_text = [
        lang for lang in cfg.corpus_languages if any(d.strip() for d in docs_by_lang[lang])
    ]
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
            df_lang = extract_language(
                lang,
                docs_by_lang[lang],
                cfg,
                cache_dir=paths.parse_cache_dir,
                blacklist=blacklist,
                n_jobs=n_jobs,
                progress=parsed,
            )
        finally:
            # A model holds a few hundred MB: only one is loaded at a time, and
            # none once the stage is over.
            language_models.release(lang)
        df_lang.to_csv(out_path, index=False)
        log(lo + span, f"Saved {len(df_lang)} {lang.upper()} candidate terms to {out_path}")
        g = df_lang[["term", "score", "len", "score_len"]].copy()
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
