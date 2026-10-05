# SPDX-License-Identifier: MIT
"""Stage 1: split the corpus by language and extract candidate terms (noun phrases).

Each text is split into paragraphs by language; every paragraph is parsed by
the language's model (:mod:`cartolex.lexicon.language_models`); the noun
phrases found by the language's patterns (:mod:`cartolex.lexicon.noun_phrases`),
nested spans included and grouped by lemma, are the candidates. They are
scored by :mod:`cartolex.lexicon.scoring`: a window on the people who use
them (``min_df`` people at least, ``max_df`` of them at most, and
``min_texts`` distinct texts at least), a TF-IDF whose
documents are the counting unit (``KeywordsConfig.counting_unit``: a person
by default), summed, times the length bonus; each kept candidate falls in a
band (kept, to check, set aside) with a reason, and a candidate of the
rejection snapshot (``paths.rejects_json``, :mod:`cartolex.lexicon.rejects`)
in the ``rejected`` band.

Output per language (``paths.raw_terms_csv(lang)``): ``term`` (the candidate's
most frequent surface form), ``score``, ``len`` (words of the term),
``score_len``, ``forms`` (every surface form, most frequent first, separated
by ``|``), ``people`` and ``texts`` (how many use it), ``band`` and ``reason``,
sorted by ``score_len``; the merged list (``paths.global_terms_csv``); and who
uses each candidate, as indices of people (``paths.term_people_npz``).
"""

from __future__ import annotations

import logging
import multiprocessing
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from . import language_models
from .config import KeywordsConfig
from .io_helpers import (
    CorpusError,
    PersonTexts,
    slot_indexes,
)
from .lexicon_store import (
    load_canonical_decision_blacklist,
    load_manual_blacklist,
)
from .noun_phrases import TextAnalysis, analyse
from .parse_cache import ParseCache, text_key
from .rejects import read_snapshot
from .scoring import (
    FORMS_SEPARATOR,
    RAW_COLUMNS,
    BandRules,
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
    share: dict | None = None,
) -> list[TextAnalysis]:
    """The analyses of *texts* (in order), parsed with *lang*'s model.

    With *n_jobs* > 1 the texts are parsed in batches by that many worker
    processes, each with its own copy of the model; the analysis of a text
    does not depend on the batch it is parsed in, so the result is the same
    whatever the number of workers. *progress* is called with the number of
    texts done and the total. With *share*, each batch's analyses are kept in
    their shared form (:meth:`TextAnalysis.shared`), through that table.
    """

    def keep(batch: list[TextAnalysis]) -> list[TextAnalysis]:
        return batch if share is None else [a.shared(share) for a in batch]

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
                out += keep(batch)
                if progress:
                    progress(len(out), total)
        return out
    for batch in batches:
        out += keep(_parse_batch(lang, batch))
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
    :mod:`cartolex.lexicon.parse_cache`). The analyses are held in their shared
    form (:meth:`TextAnalysis.shared`: one object per distinct word and unit of
    the language), which takes about half the memory.
    """
    model = language_models.require(lang)
    by_key = {text_key(t): t for t in texts}
    share: dict = {}
    cache = ParseCache(cache_dir, model.identity) if cache_dir is not None else None
    found = cache.read(by_key, share=share) if cache is not None else {}
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
        parsed = parse_texts(
            lang, [by_key[k] for k in missing], n_jobs=n_jobs, progress=progress, share=share
        )
        new = dict(zip(missing, parsed, strict=True))
        if cache is not None:
            cache.write(new)
        found.update(new)
    return found


# ── Candidates and scores ───────────────────────────────────────────────────


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=RAW_COLUMNS)


def options_of(cfg: KeywordsConfig) -> ScoringOptions:
    """The scoring options a run's settings give (the part weights keep their default: the
    corpus gives each text as one part)."""
    return ScoringOptions(
        counting_unit=cfg.counting_unit,
        vote=cfg.vote,
        length_bonus_alpha=cfg.length_bonus_alpha,
        of_complement=cfg.of_complement,
        max_units=cfg.max_units,
        foreign_reading=cfg.foreign_reading,
        bands=BandRules(
            fragment_share=cfg.band_fragment_share,
            drop_share=cfg.band_drop_share,
            keep_share=cfg.band_keep_share,
            generic_spread=cfg.band_generic_spread,
            name_share=cfg.band_name_share,
            stop_words=cfg.band_stop_words,
            even_spread=cfg.band_even_spread,
            even_people=cfg.band_even_people,
            closed_edges=cfg.band_closed_edges,
        ),
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
        min_texts=cfg.min_texts,
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


def reject_band(table: pd.DataFrame, rejected: Mapping[str, str]) -> pd.DataFrame:
    """*table* with the candidates of *rejected* (lower-case term → ``list`` or ``earlier``)
    in the ``rejected`` band, their reason ``rejected-list`` or ``rejected-earlier``.

    A candidate is rejected when its shown form, or one of its other forms, is listed.
    """
    if table.empty or not rejected:
        return table

    def found(row: tuple[str, str]) -> str | None:
        term, forms = row
        for form in [term, *str(forms or "").split(FORMS_SEPARATOR)]:
            hit = rejected.get(" ".join(str(form).split()).casefold())
            if hit:
                return hit
        return None

    forms = table["forms"] if "forms" in table.columns else pd.Series("", index=table.index)
    origin = pd.Series(
        [found(r) for r in zip(table["term"].astype(str), forms.fillna(""), strict=True)],
        index=table.index,
        dtype=object,
    )
    hit = origin.notna()
    if not hit.any():
        return table
    table = table.copy()
    table.loc[hit, "band"] = "rejected"
    table.loc[hit, "reason"] = "rejected-" + origin[hit].astype(str)
    return table


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


def write_term_people(
    path: Path, users: Mapping[str, Mapping[str, np.ndarray]], n_people: int
) -> None:
    """Who uses each candidate: for every language and term, the indices of its people.

    Indices only (the order of the extraction's people), never a name: a handoff
    groups the terms that the same people use (a term and its translation) with it.
    """
    langs, terms, lengths, chunks = [], [], [], []
    for lang, of in users.items():
        for term, idx in of.items():
            langs.append(lang)
            terms.append(term)
            lengths.append(len(idx))
            chunks.append(np.asarray(idx, dtype=np.int32))
    indptr = np.concatenate([[0], np.cumsum(lengths, dtype=np.int64)])
    indices = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int32)
    with open(path, "wb") as fh:
        np.savez_compressed(
            fh,
            langs=np.asarray(langs, dtype=str),
            terms=np.asarray(terms, dtype=str),
            indptr=indptr,
            indices=indices,
            n_people=np.asarray([n_people]),
        )


def read_term_people(path: Path) -> tuple[dict[tuple[str, str], np.ndarray], int]:
    """(term, language) → the indices of its people, and the number of people (empty if none)."""
    if not Path(path).is_file():
        return {}, 0
    with np.load(path, allow_pickle=False) as z:
        indptr, indices = z["indptr"], z["indices"]
        out = {
            (str(t), str(lang)): indices[indptr[i] : indptr[i + 1]]
            for i, (lang, t) in enumerate(zip(z["langs"], z["terms"], strict=True))
        }
        return out, int(z["n_people"][0])


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
    from . import extract_stream
    from .corpus_store import load_corpus
    from .scoring import score_aggregates

    cfg = ctx.settings
    paths = ctx.paths
    paths.automatic_dir.mkdir(parents=True, exist_ok=True)

    def log(p: int, m: str) -> None:
        if progress_callback:
            progress_callback(p, m)
        logger.info("[%d%%] %s", p, m)

    log(0, "Loading documents and splitting by language...")
    n_jobs = ctx.threads.workers(cfg.extraction_n_jobs)
    indexes = slot_indexes(ctx)
    if not indexes:
        raise CorpusError("No corpus slot to read: the settings declare none for this stage.")
    corpus = load_corpus(indexes, recency_years=cfg.kw_recency_years or None, now_year=ctx.now_year)
    if not corpus.rows_read:
        raise CorpusError(
            "No valid corpus index files found (slots: "
            + ", ".join(f"{tag} → {index.name}" for tag, index, _ in indexes)
            + ")."
        )
    ex, words = extract_stream.prepare(
        corpus,
        cfg.corpus_languages,
        scratch=paths.automatic_dir,
        workers=n_jobs,
        progress=lambda f, m: log(int(20 * f), m),
    )
    try:
        _score_languages(ctx, ex, words, n_jobs, log, score_aggregates)
    finally:
        ex.close()


def _score_languages(ctx, ex, words, n_jobs, log, score_aggregates) -> None:  # noqa: ANN001
    from . import extract_stream

    cfg = ctx.settings
    paths = ctx.paths
    texts_in = {lang: ex.spills[lang].count for lang in cfg.corpus_languages}
    log(20, " | ".join(f"Texts {lang.upper()}: {n}" for lang, n in texts_in.items()))

    with_text = [lang for lang in cfg.corpus_languages if texts_in[lang]]
    # Every model the run needs, checked before any parsing starts.
    for lang in with_text:
        language_models.require(lang)

    # The project's own rejections (never packaged stop lists).
    blacklist = load_manual_blacklist(paths.manual_blacklist_csv) | (
        load_canonical_decision_blacklist(paths.canonical_decisions_json)
    )
    # The rejection lists' terms: the rejected band (never judged, never in the lexicon).
    rejects = read_snapshot(paths.rejects_json)

    n_langs = max(len(cfg.corpus_languages), 1)
    global_parts: list[pd.DataFrame] = []
    users: dict[str, Mapping[str, np.ndarray]] = {}
    options = options_of(cfg)
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

        def step(f: float, m: str, lo: int = lo, span: int = span) -> None:
            log(lo + int(0.9 * span * f), m)

        try:
            agg = extract_stream.language_aggregates(
                ex,
                lang,
                words[lang],
                cache_dir=paths.parse_cache_dir,
                model=language_models.require(lang).identity,
                options=options,
                min_df=cfg.min_df,
                workers=n_jobs,
                progress=step,
            )
        finally:
            # A model holds a few hundred MB: only one is loaded at a time, and
            # none once the stage is over.
            language_models.release(lang)
        scored = score_aggregates(
            lang,
            agg,
            ex.n_people,
            min_df=cfg.min_df,
            max_df=cfg.max_df,
            max_features=cfg.max_features,
            min_texts=cfg.min_texts,
            options=options,
            blacklist=blacklist,
        )
        if scored.empty:
            logger.warning(
                "[%s] No candidate term within the document-frequency window "
                "(%d people, min_df=%s, max_df=%s).",
                lang,
                ex.n_people,
                cfg.min_df,
                cfg.max_df,
            )
        df_lang = reject_band(scored.table, rejects.get(lang, {}))
        df_lang.to_csv(out_path, index=False)
        users[lang] = scored.people_of
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
    write_term_people(paths.term_people_npz, users, ex.n_people)
    log(95, f"Saved {len(df_global)} deduplicated terms to {paths.global_terms_csv}")
    log(100, "Done.")
