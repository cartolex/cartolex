# SPDX-License-Identifier: MIT
"""The keyword extraction a text at a time, in worker processes: corpora of any size.

The candidates of one language are scored from the texts of the people in the
window (:mod:`cartolex.lexicon.scoring`). Here every text is read, split by
language, healed, cut and parsed **once**, however many of its authors are in the
project, and the counts the scoring weighs by person and text count it once per
author (its *weight*, the pairs that name it). Four passes, each over blocks of
texts given to worker processes (:func:`cartolex.scale.ordered_map`, results in
order: the output does not depend on the number of workers):

1. **languages**: each text's paragraphs by language (:func:`~.io_helpers._split_paragraphs`),
   and the words of each language counted (the healing's dictionary);
2. **parsing**, a language at a time: each text healed
   (:func:`~.text_utils.heal_text`), cut (:func:`~.extract_raw.person_pieces`) and its
   pieces analysed, from the parse cache or parsed; the words' lemmas counted (the
   corpus lemma table);
3. **keys**: which candidates the texts hold, a 64-bit hash each; a candidate the
   texts of fewer than ``min_df`` people (counted with their weights) hold cannot
   reach the window, and is not counted further;
4. **counts**: the other candidates' occurrences, surface forms, classes and
   containers, text by text, gathered into :class:`~.scoring.Aggregates`.

The analyses wait between passes in a scratch folder, read front to back. Memory
holds the counts of the candidates that can reach the window, never the corpus
nor its analyses.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import tempfile
from array import array
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
from scipy import sparse

from cartolex.scale import ordered_map

from .corpus_store import CorpusIndex
from .io_helpers import _split_paragraphs
from .noun_phrases import TextAnalysis, _Keyer, foreign_words, language_patterns, spans
from .parse_cache import ParseCache, text_key
from .scoring import Aggregates, ScoringOptions
from .text_utils import _WORD_RE, heal_text

__all__ = ["TASK_TEXTS", "Extraction", "prepare", "language_aggregates"]

logger = logging.getLogger(__name__)

#: Texts given to a worker at a time.
TASK_TEXTS = 256
#: A word is real (healing) when it occurs this often in the language (weighted).
MIN_REAL = 5
#: A worker forgets the keys of the word units it has seen past this many.
_KEYER_CACHE = 500_000

Progress = Callable[[float, str], None]
_STATE: dict[str, Any] = {}


def _hash(key: str) -> int:
    return int.from_bytes(hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest(), "little")


def _blocks(items: Iterable[Any], size: int = TASK_TEXTS) -> Iterator[list[Any]]:
    block: list[Any] = []
    for item in items:
        block.append(item)
        if len(block) >= size:
            yield block
            block = []
    if block:
        yield block


class _Spill:
    """Rows ``(text, weight, payload)`` written to an Arrow file and read back in order."""

    SCHEMA = pa.schema([("t", pa.int64()), ("w", pa.int32()), ("payload", pa.large_string())])

    def __init__(self, path: Path) -> None:
        self.path = path
        self._writer = pa.ipc.new_file(str(path), self.SCHEMA)
        self._rows: list[tuple[int, int, str]] = []
        self.count = 0

    def add(self, t: int, w: int, payload: str) -> None:
        self._rows.append((t, w, payload))
        self.count += 1
        if len(self._rows) >= 2048:
            self._flush()

    def _flush(self) -> None:
        if self._rows:
            t, w, p = zip(*self._rows, strict=True)
            self._writer.write_table(
                pa.table([pa.array(t), pa.array(w, pa.int32()), pa.array(p, pa.large_string())],
                         schema=self.SCHEMA)
            )  # fmt: skip
            self._rows = []

    def close(self) -> None:
        self._flush()
        self._writer.close()

    def rows(self) -> Iterator[tuple[int, int, str]]:
        with pa.memory_map(str(self.path)) as source:
            reader = pa.ipc.open_file(source)
            for i in range(reader.num_record_batches):
                batch = reader.get_batch(i)
                yield from zip(
                    batch.column(0).to_pylist(),
                    batch.column(1).to_pylist(),
                    batch.column(2).to_pylist(),
                    strict=True,
                )


# ── 1. languages ─────────────────────────────────────────────────────────────


def _set_languages(langs: tuple[str, ...]) -> None:
    _STATE["langs"] = langs


def _split_task(
    items: list[tuple[int, int, str]],
) -> tuple[list[tuple[int, bool, dict[str, str]]], dict[str, Counter[str]]]:
    """In a worker: each text's paragraphs by language, and each language's words counted
    with the texts' weights."""
    langs = _STATE["langs"]
    words: dict[str, Counter[str]] = {lang: Counter() for lang in langs}
    out = []
    for t, w, text in items:
        docs: dict[str, str] = {}
        blank = not text.strip()
        if not blank:
            for lang, paras in _split_paragraphs(text, langs).items():
                if paras:
                    doc = docs[lang] = "\n\n".join(paras)
                    count = words[lang]
                    for m in _WORD_RE.finditer(doc):
                        count[m.group(0).lower()] += w
        out.append((t, blank, docs))
    return out, words


# ── 2. parsing ───────────────────────────────────────────────────────────────


def _set_parsing(lang: str, real: frozenset[str], cache_dir: str | None, model: str) -> None:
    _STATE.update(
        lang=lang,
        real=real,
        cache=ParseCache(cache_dir, model, readonly=True) if cache_dir else None,
    )


def _parse_task(
    items: list[tuple[int, int, str]],
) -> tuple[list[tuple[int, int, str]], dict[str, dict], Counter[tuple[str, str]], int]:
    """In a worker: each text healed, cut and analysed (from the cache, or parsed); the
    analyses of each text (JSON), those newly parsed, the lemmas counted with the texts'
    weights, and the words healed."""
    from . import extract_raw

    lang, real, cache = _STATE["lang"], _STATE["real"], _STATE["cache"]
    healed_total = 0
    texts = []
    wanted: dict[str, str] = {}
    for t, w, doc in items:
        healed, n = heal_text(doc, real.__contains__)
        healed_total += n
        keys = []
        for piece in extract_raw.person_pieces(healed):
            key = text_key(piece)
            wanted.setdefault(key, piece)
            keys.append(key)
        texts.append((t, w, keys))
    found: dict[str, TextAnalysis] = cache.read(wanted) if cache is not None else {}
    missing = sorted(k for k in wanted if k not in found)
    new: dict[str, dict] = {}
    if missing:
        parsed = extract_raw.parse_texts(lang, [wanted[k] for k in missing])
        for key, analysis in zip(missing, parsed, strict=True):
            found[key] = analysis
            new[key] = analysis.to_json()
    lemmas: Counter[tuple[str, str]] = Counter()
    out = []
    for t, w, keys in texts:
        analyses = [found[k].to_json() for k in keys]
        for a in analyses:
            for word, lemma, n in a["lemmas"]:
                lemmas[(word, lemma)] += n * w
        out.append((t, w, json.dumps(analyses, ensure_ascii=False, separators=(",", ":"))))
    return out, new, lemmas, healed_total


# ── 3. and 4. keys and counts ────────────────────────────────────────────────


def _set_keys(lang: str, lemmas: dict[str, str], options: ScoringOptions, kept: Any) -> None:
    lp = language_patterns(lang, of_complement=options.of_complement)
    _STATE.update(
        lp=lp,
        lemmas=lemmas,
        keyer=_Keyer(lp, lemmas),
        foreign=foreign_words(lang) if options.bands.stop_words else frozenset(),
        max_units=options.max_units,
        foreign_reading=options.foreign_reading,
        kept=set(kept.tolist()) if kept is not None else None,
    )


def _text_spans(payload: str) -> Iterator[Any]:
    keyer = _STATE["keyer"]
    if len(keyer.cache) > _KEYER_CACHE:
        keyer.cache.clear()
    for a in json.loads(payload):
        yield from spans(
            TextAnalysis.from_json(a),
            _STATE["lp"],
            _STATE["lemmas"],
            keyer=keyer,
            foreign=_STATE["foreign"],
            max_units=_STATE["max_units"],
            foreign_reading=_STATE["foreign_reading"],
        )


def _hash_task(items: list[tuple[int, int, str]]) -> list[tuple[int, np.ndarray]]:
    """In a worker: the hashes of the candidates each text holds, with the text's weight."""
    out = []
    for _t, w, payload in items:
        found = {_hash(s.key) for s in _text_spans(payload)}
        out.append((w, np.fromiter(found, dtype=np.uint64, count=len(found))))
    return out


def _count_task(
    items: list[tuple[int, int, str]],
) -> list[tuple[int, int, int, dict[str, list]]]:
    """In a worker: per text, its candidate occurrences (every key), and for each counted
    key its occurrences, surface forms, classes and containers (counted keys only)."""
    kept = _STATE["kept"]
    known: dict[str, bool] = {}

    def counted(key: str) -> bool:
        hit = known.get(key)
        if hit is None:
            hit = known[key] = _hash(key) in kept
        return hit

    out = []
    for t, w, payload in items:
        total = 0
        per: dict[str, list] = {}
        for s in _text_spans(payload):
            total += 1
            if not counted(s.key):
                continue
            entry = per.get(s.key)
            if entry is None:
                entry = per[s.key] = [0, Counter(), Counter(), Counter()]
            entry[0] += 1
            entry[1][s.surface] += 1
            entry[2][s.classes] += 1
            entry[3].update(c for c in s.containers if counted(c))
        out.append((t, w, total, per))
    return out


# ── the run ──────────────────────────────────────────────────────────────────


@dataclass
class Extraction:
    """What the passes over the corpus give before scoring: the people read (their
    indices in the corpus, in order), each text's weight, and per language the spill of
    the texts with paragraphs in it."""

    corpus: CorpusIndex
    people: list[int]
    weight: dict[int, int]
    spills: dict[str, _Spill]
    folder: Path

    @property
    def n_people(self) -> int:
        return len(self.people)

    def close(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)


def prepare(
    corpus: CorpusIndex,
    langs: Sequence[str],
    *,
    scratch: Path,
    workers: int,
    progress: Progress | None = None,
) -> tuple[Extraction, dict[str, Counter[str]]]:
    """Pass 1: every text of the window read once and split by language (see the module
    docstring); returns the extraction's state and each language's words counted."""
    langs = tuple(langs)
    folder = Path(tempfile.mkdtemp(prefix="extract-", dir=scratch))
    texts, weights = np.unique(corpus.text, return_counts=True)
    weight = dict(zip(texts.tolist(), weights.tolist(), strict=True))
    spills = {lang: _Spill(folder / f"docs-{lang}.arrow") for lang in langs}
    words = {lang: Counter() for lang in langs}
    blank: set[int] = set()
    tasks = _blocks((t, weight[t], text) for t, text in corpus.texts(texts))
    done = 0
    for out, counts in ordered_map(
        _split_task, tasks, workers=workers, initializer=_set_languages, initargs=(langs,)
    ):
        for t, is_blank, docs in out:
            if is_blank:
                blank.add(t)
            for lang, doc in docs.items():
                spills[lang].add(t, weight[t], doc)
        for lang, count in counts.items():
            words[lang].update(count)
        done += len(out)
        if progress:
            progress(done / max(len(texts), 1), f"Languages of {done}/{len(texts)} texts")
    for spill in spills.values():
        spill.close()
    # A person is read when one of their texts is not blank (in the corpus's order).
    readable = np.array([t not in blank for t in corpus.text.tolist()], dtype=bool)
    people = sorted(set(corpus.person[readable].tolist()))
    return Extraction(corpus, people, weight, spills, folder), words


def language_aggregates(
    ex: Extraction,
    lang: str,
    words: Counter[str],
    *,
    cache_dir: Path | None,
    model: str,
    options: ScoringOptions,
    min_df: float,
    workers: int,
    progress: Progress | None = None,
) -> Aggregates | None:
    """Passes 2 to 4 for one language (see the module docstring): its texts parsed, then
    the candidates that can reach the window counted; ``None`` without text."""
    docs = ex.spills[lang]
    if not docs.count:
        return None
    report = progress or (lambda f, m: None)
    real = frozenset(w for w, n in words.items() if n >= MIN_REAL)
    writer = ParseCache(cache_dir, model) if cache_dir is not None else None
    parsed = _Spill(ex.folder / f"analyses-{lang}.arrow")
    lemma_counts: Counter[tuple[str, str]] = Counter()
    healed = done = 0
    try:
        for out, new, lemmas, n_healed in ordered_map(
            _parse_task,
            _blocks(docs.rows()),
            workers=workers,
            initializer=_set_parsing,
            initargs=(lang, real, str(cache_dir) if cache_dir is not None else None, model),
        ):
            if writer is not None and new:
                writer.write({k: TextAnalysis.from_json(a) for k, a in new.items()})
            for t, w, payload in out:
                parsed.add(t, w, payload)
            lemma_counts.update(lemmas)
            healed += n_healed
            done += len(out)
            report(0.6 * done / docs.count, f"Parsed {done}/{docs.count} {lang.upper()} texts")
    finally:
        parsed.close()
        if writer is not None:
            writer.close()
    if healed:
        logger.info("[%s] Rejoined %d split-word artifact(s) before parsing.", lang, healed)
    lemmas = _lemma_table(lemma_counts)
    del lemma_counts

    # 3. The candidates the texts of enough people (weighted) hold.
    hashes: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for out in ordered_map(
        _hash_task,
        _blocks(parsed.rows()),
        workers=workers,
        initializer=_set_keys,
        initargs=(lang, lemmas, options, None),
    ):
        for w, found in out:
            hashes.append(found)
            weights.append(np.full(len(found), w, dtype=np.int64))
    report(0.75, f"Candidates of the {lang.upper()} texts found")
    every = np.concatenate(hashes) if hashes else np.zeros(0, dtype=np.uint64)
    reach = np.concatenate(weights) if weights else np.zeros(0, dtype=np.int64)
    del hashes, weights
    unique, inverse = np.unique(every, return_inverse=True)
    reach = np.bincount(inverse, weights=reach, minlength=len(unique))
    floor = min_df if isinstance(min_df, int) else math.ceil(min_df * ex.n_people)
    kept = unique[reach >= floor]
    del every, unique, inverse, reach
    logger.info("[%s] %d candidate(s) can reach the window.", lang, len(kept))

    # 4. Their counts.
    return _count(ex, lang, parsed, lemmas, options, kept, workers, report)


def _lemma_table(counts: Mapping[tuple[str, str], int]) -> dict[str, str]:
    """Each word's most frequent lemma (ties: the first in alphabetical order), kept only
    where it differs from the word (a key reads a word without one as itself)."""
    best: dict[str, tuple[int, str]] = {}
    for (word, lemma), n in counts.items():
        mine = (-n, lemma)
        if word not in best or mine < best[word]:
            best[word] = mine
    return {w: lemma for w, (_n, lemma) in best.items() if lemma != w}


def _count(
    ex: Extraction,
    lang: str,
    parsed: _Spill,
    lemmas: dict[str, str],
    options: ScoringOptions,
    kept: np.ndarray,
    workers: int,
    report: Progress,
) -> Aggregates:
    key_id: dict[str, int] = {}
    surfaces: list[Counter[str]] = []
    classes: list[Counter[str]] = []
    containers: list[Counter[str]] = []
    rows, cols, vals = array("q"), array("q"), array("d")
    row_of: dict[int, int] = {}
    totals: list[float] = []
    done = 0
    for out in ordered_map(
        _count_task,
        _blocks(parsed.rows()),
        workers=workers,
        initializer=_set_keys,
        initargs=(lang, lemmas, options, kept),
    ):
        for t, w, total, per in out:
            row = row_of[t] = len(row_of)
            totals.append(float(total))
            for key, (n, forms, kinds, around) in per.items():
                i = key_id.get(key)
                if i is None:
                    i = key_id[key] = len(surfaces)
                    surfaces.append(Counter())
                    classes.append(Counter())
                    containers.append(Counter())
                rows.append(row)
                cols.append(i)
                vals.append(float(n))
                for form, k in forms.items():
                    surfaces[i][form] += k * w
                classes[i].update(kinds)
                containers[i].update(around)
        done += len(out)
        report(0.75 + 0.25 * done / max(parsed.count, 1), f"Counted {done} {lang.upper()} texts")
    n_rows, n_keys = len(row_of), len(key_id)
    T = sparse.csr_matrix(
        (np.frombuffer(vals, dtype=np.float64), (np.frombuffer(rows, dtype=np.int64),
         np.frombuffer(cols, dtype=np.int64))), shape=(n_rows, n_keys)
    )  # fmt: skip
    # Who read each text: one per person and text (the people read, in order).
    corpus = ex.corpus
    person_row = {p: i for i, p in enumerate(ex.people)}
    pairs = sorted(
        {
            (person_row[p], row_of[t])
            for p, t in zip(corpus.person.tolist(), corpus.text.tolist(), strict=True)
            if t in row_of and p in person_row
        }
    )
    P = sparse.csr_matrix(
        (np.ones(len(pairs)), ([a for a, _ in pairs], [b for _, b in pairs])),
        shape=(ex.n_people, n_rows),
    )
    units = [corpus.people[p].unit for p in ex.people]
    orgs = sorted(set(units))
    org_row = {o: i for i, o in enumerate(orgs)}
    org_pairs = sorted({(org_row[units[a]], b) for a, b in pairs})
    org_texts = sparse.csr_matrix(
        (np.ones(len(org_pairs)), ([a for a, _ in org_pairs], [b for _, b in org_pairs])),
        shape=(len(orgs), n_rows),
    )
    volume = np.asarray(P @ np.asarray(totals, dtype=float)).ravel()
    word_people = None
    if options.bands.generic_spread is not None:
        # Counted on the keys that can reach the window (the others are rare).
        used = (P @ T).tocsr()
        keys = list(key_id)
        word_people = Counter()
        for r in range(used.shape[0]):
            cols_r = used.indices[used.indptr[r] : used.indptr[r + 1]]
            word_people.update({w for c in cols_r for w in keys[c].split(" ")})
    return Aggregates(
        keys=list(key_id),
        parts={"full": T},
        P=P,
        org_texts=org_texts,
        organisations=orgs,
        surfaces=surfaces,
        classes=classes,
        containers=containers,
        volume=volume,
        n_texts=n_rows,
        word_people=word_people,
    )
