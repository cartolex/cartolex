# SPDX-License-Identifier: MIT
"""Scoring candidate terms: counting units, text votes, part weights and the three bands.

The candidates of one language are scored from the analysed texts of the
corpus (:class:`TextUnit`: a person's text, its organisation and its parts).

1. **Window.** A candidate is kept when at least ``min_df`` people use it and
   at most ``max_df`` of them (a share); ``max_features`` keeps the most
   frequent. The window counts people; a candidate must also occur in at
   least ``min_texts`` distinct texts (a phrase of one co-authored text is
   used by all its authors, but is one text's evidence).
2. **Vote.** Each text votes for the candidates it holds: the number of
   occurrences (``frequency``), one vote (``presence``) or ``1 + ln n``
   (``sublinear``). A text's parts (title, abstract, body…) can weigh
   differently (``part_weights``, 1 by default).
3. **Counting unit.** The votes are summed into documents: one per person
   (``person``), one per text (``text``: a text two people wrote counts once),
   or one per organisation (``organisation``: a text counts once for an
   organisation, however many of its members wrote it). Each document's
   TF-IDF vector is L2-normalised, and a candidate's ``score`` is the sum over
   documents: each document weighs the same.
4. **Length bonus.** ``score_len = score × (1 + α (L − 1))``, ``L`` the words of
   the term.
5. **Bands.** Each kept candidate falls in one band, with a reason code:

   ============  =====================================================
   ``kept``      ``multiword``: a phrase of two content words or more
   ``check``     ``single-word``; ``common-modifier: <word>`` (its edge
                 adjective is used by many people; off by default);
                 ``below-threshold`` (off by default)
   ``aside``     ``stop-word`` (one word among the language's stop
                 words, or a closed word of another language in text of
                 that language); ``stop-word-edge: <word>`` (a phrase that
                 starts or ends with a closed word of another language);
                 ``even-spread`` (one word used by many people, as evenly
                 as words scattered at random); ``part-of: <term>`` (never
                 seen outside that longer candidate); ``low-score`` (the
                 least specific tail, off by default); ``name:
                 person|place`` (when names are known)
   ``rejected``  ``rejected-list`` (on cartolex's list of rejections) or
                 ``rejected-earlier`` (rejected by an AI in an earlier
                 project): set by the extraction from a rejection
                 snapshot (:mod:`cartolex.lexicon.rejects`), after scoring
   ============  =====================================================

   Only the ``kept`` and ``check`` bands (:data:`LEXICON_BANDS`) can reach the
   lexicon without the AI; the AI judges the ``aside`` band too
   (:data:`AI_BANDS`), never the ``rejected`` one.

The scores reproduce the historical scoring: people, raw frequency, equal
parts, α = 2. The other choices are switches of the lexicon lab
(``tools/lexicon_lab``, ``docs/dev/lexicon-lab.md``), which set the defaults.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import TfidfTransformer

from .lexical_filters import is_malformed_term
from .noun_phrases import (
    FOREIGN_CLASS,
    FOREIGN_READING,
    MAX_UNITS,
    TextAnalysis,
    _Keyer,
    closed_form,
    foreign_words,
    language_patterns,
    lemma_table,
    spans,
    stop_words,
)
from .text_utils import length_bonus, term_words

__all__ = [
    "BANDS",
    "COUNTING_UNITS",
    "AI_BANDS",
    "LEXICON_BANDS",
    "RAW_COLUMNS",
    "VOTES",
    "Aggregates",
    "BandRules",
    "Candidate",
    "ScoredCandidates",
    "ScoringOptions",
    "TextUnit",
    "aggregate_units",
    "score_aggregates",
    "score_units",
    "window_keys",
]

#: What a document is when candidates are scored (see the module docstring).
COUNTING_UNITS = ("person", "text", "organisation")
#: How a text votes for a candidate it holds.
VOTES = ("frequency", "presence", "sublinear")
#: The bands, in display order.
BANDS = ("kept", "check", "aside", "rejected")
#: The bands that can reach the lexicon: the AI clean-up judges them, and the
#: consolidation keeps nothing of the others (the set-aside band) unless a
#: person keeps it explicitly.
LEXICON_BANDS = ("kept", "check")
#: The bands an AI judges: every candidate but those the rejection lists banned.
AI_BANDS = ("kept", "check", "aside")
#: Columns of a raw keyword table (``raw_keywords_<lang>.csv``).
RAW_COLUMNS = ["term", "score", "len", "score_len", "forms", "people", "texts", "band", "reason"]
#: Separator of the surface forms in the ``forms`` column.
FORMS_SEPARATOR = "|"


@dataclass(frozen=True)
class BandRules:
    """The thresholds of the three bands; ``None`` turns a rule off.

    ``fragment_share``: a candidate found this often (a share of its
    occurrences) inside one and the same longer candidate is a fragment of it
    (set aside); by default, every time. ``drop_share``: the least specific
    share of the candidates, by ``score_len``, is set aside (0: none, the
    default). ``keep_share``: a multi-word phrase is kept only within the best
    ``keep_share`` of the candidates (1: every one, the default).
    ``generic_spread``: an edge adjective used by at least this share of
    people makes a phrase common (to check; off by default, a switch of the
    lexicon lab). ``name_share``: a candidate this
    often inside a recognised name of a person or a place is set aside (only
    when names are known). ``stop_words``: stop words and closed words of
    other languages are set aside (``stop-word``, and ``stop-word-edge`` when
    ``closed_edges`` is on too).
    ``even_spread`` and ``even_people``: a single word used by at least
    ``even_people`` of the people who have texts in the language, and by at
    least ``even_spread`` times as many people as its occurrences would reach
    if they were scattered at random over the texts (in proportion to each
    person's text volume), is spread evenly: set aside (``even-spread``;
    ``None`` turns it off). The defaults are the lexicon lab's.
    """

    fragment_share: float | None = 1.0
    drop_share: float = 0.0
    keep_share: float = 1.0
    generic_spread: float | None = None
    name_share: float = 0.5
    stop_words: bool = True
    even_spread: float | None = 0.9
    even_people: float = 0.2
    closed_edges: bool = True


@dataclass(frozen=True)
class ScoringOptions:
    """How candidates are scored (see the module docstring); the defaults are the lab's.

    ``max_units``: the longest candidate, in word units; ``foreign_reading``: a
    paragraph whose phrases hold this many different closed words of another
    language is read as that language (:func:`cartolex.lexicon.noun_phrases.spans`).
    ``of_complement`` and ``genitive`` are the lab's switches of the English ``of``
    and the German genitive complements
    (:func:`cartolex.lexicon.noun_phrases.language_patterns`).
    """

    counting_unit: str = "person"
    vote: str = "frequency"
    part_weights: Mapping[str, float] = field(default_factory=dict)
    length_bonus_alpha: float = 2.0
    of_complement: bool = False
    genitive: bool = False
    bands: BandRules = field(default_factory=BandRules)
    max_units: int = MAX_UNITS
    foreign_reading: int = FOREIGN_READING

    def __post_init__(self) -> None:
        if self.counting_unit not in COUNTING_UNITS:
            raise ValueError(
                f"unknown counting unit {self.counting_unit!r}; expected one of {COUNTING_UNITS}"
            )
        if self.vote not in VOTES:
            raise ValueError(f"unknown vote {self.vote!r}; expected one of {VOTES}")

    def weight(self, part: str) -> float:
        """The weight of a text part (1 unless set)."""
        return float(self.part_weights.get(part, 1.0))


@dataclass(frozen=True)
class TextUnit:
    """One person's text in one language: its organisation and its analysed parts.

    ``person`` is the person's row; ``text`` the text's identity (a text two
    people wrote appears once per author, with the same identity); ``parts``
    pairs a part name (``title``, ``abstract``, ``body``, or ``full`` when
    parts are not known) with the analyses of its paragraphs.
    """

    person: int
    organisation: str
    text: str
    parts: tuple[tuple[str, tuple[TextAnalysis, ...]], ...]


@dataclass
class Candidate:
    """What the scoring knows about one kept candidate (the evidence a reviewer sees)."""

    key: str
    term: str
    score: float
    score_len: float
    words: int
    forms: list[tuple[str, int]]
    people: int
    texts: int
    occurrences: int
    containers: list[tuple[str, int]]
    classes: str
    content_words: int
    band: str = "check"
    reason: str = "single-word"
    percentile: float = 0.0
    name_share: float = 0.0
    name_kind: str = ""
    #: People who use it, as a share of the people with texts in the language.
    people_share: float = 0.0
    #: People who use it over the people its occurrences would reach at random.
    spread: float = 0.0


@dataclass
class ScoredCandidates:
    """The scored candidates of one language: the raw keyword table and the evidence."""

    lang: str
    table: pd.DataFrame
    candidates: dict[str, Candidate]
    n_people: int
    n_texts: int
    n_documents: int
    #: The people (their indices among the *n_people*) who use each term of the table.
    people_of: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.table.empty


def _empty(lang: str, n_people: int = 0, n_texts: int = 0) -> ScoredCandidates:
    return ScoredCandidates(lang, pd.DataFrame(columns=RAW_COLUMNS), {}, n_people, n_texts, 0)


def _features(doc: list[str]) -> list[str]:
    """The window's analyzer: a person's candidate keys are already the features."""
    return doc


def _blocked(term: str, blacklist: Collection[str]) -> bool:
    """Whether *term*, or one of its words (an elided word apart), is among the rejections."""
    low = term.lower()
    return low in blacklist or any(t in blacklist for t in term_words(low))


def _vote(parts: Mapping[str, float], present: Mapping[str, float], how: str) -> float:
    """A text's vote from its weighted count (*parts*) and its best present part weight."""
    n = sum(parts.values())
    if how == "frequency":
        return n
    if how == "presence":
        return max(present.values())
    return 1.0 + math.log(n) if n >= 1.0 else n


def _percentiles(values: np.ndarray) -> np.ndarray:
    """Percentile ranks in [0, 1]; ties share their mean rank."""
    if len(values) == 0:
        return values
    order = values.argsort(kind="stable")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    _, inverse = np.unique(values, return_inverse=True)
    ranks = (np.bincount(inverse, weights=ranks) / np.bincount(inverse))[inverse]
    return ranks / max(len(values) - 1, 1)


@dataclass
class Aggregates:
    """What the scoring needs of one language's texts, counted once per text.

    ``keys`` are the candidate keys counted (the columns). ``parts`` holds, per
    part name, each text's occurrences of each key (texts × keys; a text without
    parts known has one, ``full``); ``P`` says who read each text (people × texts,
    one per person and text: a text two people wrote counts for both);
    ``organisations`` names the rows of ``org_texts`` (organisations × texts, one
    per organisation and text). ``surfaces`` counts each key's surface forms once per
    person and text; ``classes`` and ``containers`` count each key's unit classes
    and the keys of the longer candidates around it, once per occurrence over
    distinct texts; ``names`` its occurrences inside a known name, by kind.
    ``volume`` is each person's number of candidate occurrences, every key counted
    (the window's keys or not), and ``word_people`` how many people use each word in
    any of their candidates (only the rule of common modifiers reads it). The evidence
    may be given for the keys of the window only (:func:`window_keys`), by index.
    """

    keys: list[str]
    parts: dict[str, sparse.csr_matrix]
    P: sparse.csr_matrix
    org_texts: sparse.csr_matrix
    organisations: list[str]
    surfaces: Sequence[Counter[str]] | Mapping[int, Counter[str]]
    classes: Sequence[Counter[str]] | Mapping[int, Counter[str]]
    containers: Sequence[Counter[str]] | Mapping[int, Counter[str]]
    volume: np.ndarray
    n_texts: int
    names: list[Counter[str]] | None = None
    word_people: Counter[str] | None = None


def aggregate_units(
    lang: str,
    units: Sequence[TextUnit],
    n_people: int,
    options: ScoringOptions | None = None,
    names: Mapping[int, Mapping[str, str]] | None = None,
) -> Aggregates:
    """The :class:`Aggregates` of analysed texts held in memory (one :class:`TextUnit` per
    person and text): every key counted, the corpus lemma table computed from them."""
    opts = options if options is not None else ScoringOptions()
    lp = language_patterns(lang, of_complement=opts.of_complement, genitive=opts.genitive)
    analyses = [a for unit in units for _, part in unit.parts for a in part]
    lemmas = lemma_table(analyses)
    keyer = _Keyer(lp, lemmas)
    foreign = foreign_words(lang) if opts.bands.stop_words else frozenset()
    found: dict[int, list] = {}
    for a in analyses:
        if id(a) not in found:
            found[id(a)] = spans(
                a,
                lp,
                lemmas,
                keyer=keyer,
                foreign=foreign,
                max_units=opts.max_units,
                foreign_reading=opts.foreign_reading,
            )
    key_id: dict[str, int] = {}
    surfaces: list[Counter[str]] = []
    classes: list[Counter[str]] = []
    containers: list[Counter[str]] = []
    hits: list[Counter[str]] = []

    def kid(key: str) -> int:
        i = key_id.get(key)
        if i is None:
            i = key_id[key] = len(surfaces)
            surfaces.append(Counter())
            classes.append(Counter())
            containers.append(Counter())
            hits.append(Counter())
        return i

    text_row: dict[str, int] = {}
    cells: dict[str, dict[tuple[int, int], int]] = {}
    volume = np.zeros(n_people, dtype=float)
    person_keys: list[set[str]] = [set() for _ in range(n_people)]
    readers: set[tuple[int, int]] = set()
    org_reads: set[tuple[str, int]] = set()
    for unit in units:
        first_time = unit.text not in text_row
        row = text_row.setdefault(unit.text, len(text_row))
        readers.add((unit.person, row))
        org_reads.add((unit.organisation, row))
        for part, part_analyses in unit.parts:
            for a in part_analyses:
                known = names.get(id(a)) if names else None
                for span in found[id(a)]:
                    i = kid(span.key)
                    volume[unit.person] += 1
                    person_keys[unit.person].add(span.key)
                    surfaces[i][span.surface] += 1
                    if not first_time:
                        continue
                    cell = cells.setdefault(part, {})
                    cell[(row, i)] = cell.get((row, i), 0) + 1
                    classes[i][span.classes] += 1
                    containers[i].update(span.containers)
                    if known:
                        low = span.surface.lower()
                        kind = next((k for n, k in known.items() if low in n), None)
                        if kind is not None:
                            hits[i][kind] += 1
    n_rows, n_keys = len(text_row), len(key_id)

    def matrix(entries: Mapping[tuple[int, int], int]) -> sparse.csr_matrix:
        if not entries:
            return sparse.csr_matrix((n_rows, n_keys))
        (r, c), v = zip(*entries.keys(), strict=True), list(entries.values())
        return sparse.csr_matrix((np.asarray(v, dtype=np.float64), (r, c)), shape=(n_rows, n_keys))

    def incidence(pairs: Iterable[tuple[int, int]], n: int) -> sparse.csr_matrix:
        pairs = sorted(pairs)
        if not pairs:
            return sparse.csr_matrix((n, n_rows))
        r, c = zip(*pairs, strict=True)
        return sparse.csr_matrix((np.ones(len(pairs)), (r, c)), shape=(n, n_rows))

    orgs = sorted({o for o, _ in org_reads})
    org_row = {o: i for i, o in enumerate(orgs)}
    word_people: Counter[str] = Counter()
    for keys in person_keys:
        word_people.update({w for key in keys for w in key.split(" ")})
    return Aggregates(
        keys=list(key_id),
        parts={part: matrix(entries) for part, entries in sorted(cells.items())},
        P=incidence(readers, n_people),
        org_texts=incidence(((org_row[o], t) for o, t in org_reads), len(orgs)),
        organisations=orgs,
        surfaces=surfaces,
        classes=classes,
        containers=containers,
        volume=volume,
        n_texts=n_rows,
        names=hits if names else None,
        word_people=word_people,
    )


def score_units(
    lang: str,
    units: Sequence[TextUnit],
    n_people: int,
    *,
    min_df: int = 3,
    max_df: float = 0.6,
    max_features: int | None = 1_000_000,
    min_texts: int = 1,
    options: ScoringOptions | None = None,
    blacklist: Collection[str] = frozenset(),
    names: Mapping[int, Mapping[str, str]] | None = None,
) -> ScoredCandidates:
    """Score the candidates of one language (see the module docstring).

    *units* are the texts of the *n_people* people (a person without a text in
    this language still counts in the window's shares). A candidate found in
    fewer than *min_texts* distinct texts is left out (1: no such floor; the
    build's default is 3, :attr:`KeywordsConfig.min_texts`). *blacklist* holds the
    project's own rejections: a candidate whose shown form, or one of its
    words, is listed is left out, as are malformed strings. *names* optionally
    maps an analysis (by ``id``) to the lower-case surfaces of the names of
    people and places found in it, with their kind (``person`` or ``place``):
    candidates mostly inside such names are set aside.

    Raises nothing when no candidate reaches the window: the table is empty.
    The texts are counted by :func:`aggregate_units`, then scored by
    :func:`score_aggregates`, which a corpus too large for memory reaches by
    counting its texts a block at a time (:mod:`cartolex.lexicon.extract_stream`).
    """
    if not any(part for unit in units for _, part in unit.parts):
        return _empty(lang, n_people, len({unit.text for unit in units}))
    agg = aggregate_units(lang, units, n_people, options, names)
    return score_aggregates(
        lang,
        agg,
        n_people,
        min_df=min_df,
        max_df=max_df,
        max_features=max_features,
        min_texts=min_texts,
        options=options,
        blacklist=blacklist,
    )


def _window(
    X: sparse.csr_matrix, n_rows: int, min_df: float, max_df: float, max_features: int | None
) -> np.ndarray:
    """The columns of *X* (people × keys, keys sorted) the window keeps, as
    ``CountVectorizer`` keeps them: used by at least *min_df* people and at most *max_df*
    (a count, or a share when a float), then the *max_features* most used."""
    df = np.diff(X.tocsc().indptr)
    high = max_df if isinstance(max_df, int | np.integer) else max_df * n_rows
    low = min_df if isinstance(min_df, int | np.integer) else min_df * n_rows
    if high < low:
        raise ValueError("max_df corresponds to < documents than min_df")
    mask = (df <= high) & (df >= low)
    if max_features is not None and mask.sum() > max_features:
        tfs = np.asarray(X.sum(axis=0)).ravel()
        mask_inds = (-tfs[mask]).argsort()[:max_features]
        new_mask = np.zeros(len(df), dtype=bool)
        new_mask[np.where(mask)[0][mask_inds]] = True
        mask = new_mask
    return np.flatnonzero(mask)


def _rows(M: sparse.spmatrix, n: int) -> sparse.csr_matrix:
    """*M* with at least *n* rows (people without a text in this language: empty rows)."""
    M = sparse.csr_matrix(M, dtype=np.float64)
    if M.shape[0] < n:
        M = sparse.vstack([M, sparse.csr_matrix((n - M.shape[0], M.shape[1]))], format="csr")
    M.sort_indices()
    return M


def _windowed(
    agg: Aggregates, n_people: int, min_df: float, max_df: float, max_features: int | None
) -> (
    tuple[list[int], dict[str, sparse.csr_matrix], sparse.csr_matrix, sparse.csr_matrix, np.ndarray]
    | None
):
    """The keys in the window's order, the counts in that order (per part, and summed),
    the people's counts, and the window's columns; ``None`` when nothing reaches it."""
    if not agg.keys or not any(T.nnz for T in agg.parts.values()):
        return None
    order = sorted(range(len(agg.keys)), key=agg.keys.__getitem__)  # the window's order
    parts = {name: T.tocsc()[:, order].tocsr() for name, T in agg.parts.items()}
    T = sum(parts.values()).tocsr()
    X_all = _rows(agg.P @ T, n_people)
    try:
        cols = _window(X_all, n_people, min_df, max_df, max_features)
    except ValueError:
        return None
    if not len(cols):
        return None
    return order, parts, T, X_all, cols


def window_keys(
    agg: Aggregates,
    n_people: int,
    *,
    min_df: int = 3,
    max_df: float = 0.6,
    max_features: int | None = 1_000_000,
) -> list[int]:
    """The keys of *agg* (their indices) the window keeps: :func:`score_aggregates` needs
    their evidence (surface forms, classes, containers), the others' it never reads."""
    found = _windowed(agg, n_people, min_df, max_df, max_features)
    if found is None:
        return []
    order, _parts, _T, _X, cols = found
    return [order[c] for c in cols]


def score_aggregates(
    lang: str,
    agg: Aggregates,
    n_people: int,
    *,
    min_df: int = 3,
    max_df: float = 0.6,
    max_features: int | None = 1_000_000,
    min_texts: int = 1,
    options: ScoringOptions | None = None,
    blacklist: Collection[str] = frozenset(),
) -> ScoredCandidates:
    """Score the candidates of one language from their counts (see :func:`score_units`).

    The keys of *agg* need not be every candidate: a key that fewer than *min_df*
    people can use (it is in too few texts) may be left out without changing the
    result, since the window would leave it out.
    """
    opts = options if options is not None else ScoringOptions()
    lp = language_patterns(lang, of_complement=opts.of_complement, genitive=opts.genitive)
    found = _windowed(agg, n_people, min_df, max_df, max_features)
    if found is None:
        return _empty(lang, n_people, agg.n_texts)
    order, parts, T, X_all, cols = found
    X_people = X_all[:, cols].tocsr()
    keys = [agg.keys[order[c]] for c in cols]
    vocabulary = {k: j for j, k in enumerate(keys)}
    source = [order[c] for c in cols]  # each column's key in the aggregates
    parts = {name: P_[:, cols].tocsr() for name, P_ in parts.items()}
    T = T[:, cols].tocsr()

    default = (
        opts.counting_unit == "person"
        and opts.vote == "frequency"
        and all(opts.weight(p) == 1.0 for p in parts)
    )
    if default:
        U = X_people  # the historical scoring: one document per person, raw counts
    else:
        weighted = sum(
            (opts.weight(p) * M for p, M in parts.items() if opts.weight(p) > 0),
            sparse.csr_matrix(T.shape),
        ).tocsr()
        if opts.vote == "frequency":
            votes = weighted
        elif opts.vote == "presence":
            present = [
                (M > 0).astype(np.float64) * opts.weight(p)
                for p, M in parts.items()
                if opts.weight(p) > 0
            ]
            votes = present[0]
            for M in present[1:]:
                votes = votes.maximum(M)
            votes = sparse.csr_matrix(votes)
        else:
            votes = weighted.copy()
            votes.data = np.where(votes.data >= 1.0, 1.0 + np.log(votes.data), votes.data)
        if opts.counting_unit == "person":
            U = _rows(agg.P @ votes, n_people)
        elif opts.counting_unit == "text":
            U = _rows(votes, 0)
        else:
            reads = sparse.csr_matrix(agg.org_texts, dtype=np.float64)
            reads.data = np.ones_like(reads.data)  # a text counts once for an organisation
            U = _rows(reads @ votes, 0)
        U.eliminate_zeros()
    tfidf = TfidfTransformer().fit_transform(U)
    scores = np.asarray(tfidf.sum(axis=0)).ravel()

    n_texts_of = np.diff(T.tocsc().indptr)
    n_occ = np.asarray(T.sum(axis=0)).ravel()
    people_per_key = np.diff(X_people.tocsc().indptr)
    preps = set(lp.prepositions.values())
    rows_out: list[Candidate] = []
    candidates: dict[str, Candidate] = {}
    for col, key in enumerate(keys):
        i = source[col]
        ranked = sorted(agg.surfaces[i].items(), key=lambda kv: (-kv[1], kv[0]))
        term = ranked[0][0]
        if is_malformed_term(term) or _blocked(term, blacklist):
            continue
        if n_texts_of[col] < min_texts:
            continue
        cls = min((agg.classes[i] or Counter({"": 1})).items(), key=lambda kv: (-kv[1], kv[0]))[0]
        occ = max(int(n_occ[col]), 1)
        inside = Counter({k: n for k, n in agg.containers[i].items() if k in vocabulary})
        hits = agg.names[i] if agg.names is not None else Counter()
        cand = Candidate(
            key=key,
            term=term,
            score=float(scores[col]),
            score_len=0.0,
            words=0,
            forms=ranked,
            people=int(people_per_key[col]),
            texts=int(n_texts_of[col]),
            occurrences=int(n_occ[col]),
            containers=sorted(inside.items(), key=lambda kv: (-kv[1], kv[0])),
            classes=cls,
            content_words=sum(1 for part in key.split(" ") if part not in preps),
            name_share=sum(hits.values()) / occ,
        )
        if hits:
            cand.name_kind = min(hits.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        candidates[key] = cand
        rows_out.append(cand)
    if not rows_out:
        return _empty(lang, n_people, agg.n_texts)

    terms = [c.term for c in rows_out]
    raw = np.asarray([c.score for c in rows_out], dtype=float)
    scores_len, lens = length_bonus(terms, raw, alpha=opts.length_bonus_alpha)
    for c, sl, n in zip(rows_out, scores_len, lens, strict=True):
        c.score_len = float(sl)
        c.words = int(n)
    _spread(rows_out, vocabulary, X_people, agg.volume, opts.bands.even_people)
    _assign_bands(lang, rows_out, candidates, agg.word_people, n_people, opts.bands)

    df = pd.DataFrame(
        {
            "term": terms,
            "score": raw,
            "len": lens,
            "score_len": scores_len,
            "forms": [FORMS_SEPARATOR.join(f for f, _ in c.forms) for c in rows_out],
            "people": [c.people for c in rows_out],
            "texts": [c.texts for c in rows_out],
            "band": [c.band for c in rows_out],
            "reason": [c.reason for c in rows_out],
        }
    )
    df = df.sort_values(["score_len", "term"], ascending=[False, True], kind="mergesort")
    # Two keys practically never share their most frequent form; keep one if they do.
    df = df.drop_duplicates(subset="term", keep="first").reset_index(drop=True)
    by_column = X_people.tocsc()
    people_of = {}
    for c in sorted(rows_out, key=lambda c: (-c.score_len, c.term)):  # the table's order
        col = vocabulary[c.key]
        people_of.setdefault(
            c.term, by_column.indices[by_column.indptr[col] : by_column.indptr[col + 1]]
        )
    return ScoredCandidates(
        lang, df[RAW_COLUMNS], candidates, n_people, agg.n_texts, U.shape[0], people_of
    )


def _spread(
    rows: Sequence[Candidate],
    vocabulary: Mapping[str, int],
    X_people: sparse.spmatrix,
    volume: np.ndarray,
    floor: float,
) -> None:
    """Each single word's share of people, and its spread when that share reaches *floor*.

    See :class:`BandRules`. A word with ``n`` occurrences scattered at random over the texts reaches
    person ``i`` with probability ``1 − exp(−n·vᵢ)``, ``vᵢ`` being the person's
    share of all candidate occurrences of the language (*volume*); the spread is the
    number of people who use the word over the sum of these probabilities
    (about 1 for a word used like any other, well below 1 for a word
    gathered in a few people's texts).
    """
    volume = np.asarray(volume, dtype=float)
    volume = volume[volume > 0]
    if volume.size == 0:
        return
    share = volume / volume.sum()
    by_column = X_people.tocsc()
    for c in rows:
        if c.content_words >= 2:
            continue
        c.people_share = c.people / volume.size
        if c.people_share < floor:
            continue
        col = vocabulary[c.key]
        n = float(by_column.data[by_column.indptr[col] : by_column.indptr[col + 1]].sum())
        expected = float(np.sum(1.0 - np.exp(-n * share)))
        c.spread = c.people / expected if expected > 0 else 0.0


def _stop_edge(term: str, closed: frozenset[str]) -> str | None:
    """The closed word *term* starts or ends with, if any (see :func:`closed_form`).

    A closed word before a capitalised word is a name's particle (``de Vries
    model``), not an edge.
    """
    words = term.split()
    if len(words) < 2:
        return None
    first, second = words[0], words[1]
    particle = second[:1].isupper() and not second.isupper()
    if not particle and closed_form(first) in closed:
        return first
    if closed_form(words[-1]) in closed:
        return words[-1]
    return None


def _assign_bands(
    lang: str,
    rows: list[Candidate],
    candidates: Mapping[str, Candidate],
    word_people: Counter[str] | None,
    n_people: int,
    rules: BandRules,
) -> None:
    """Put each candidate in its band, with its reason (see the module docstring);
    *word_people* counts the people who use each word in any of their candidates (for
    common modifiers)."""
    pct = _percentiles(np.asarray([c.score_len for c in rows], dtype=float))
    n_people = max(n_people, 1)
    word_people = word_people or Counter()
    # the modifier comes first in English and German, last in the Romance languages
    edge_first = lang in ("en", "de")
    stops: frozenset[str] = frozenset()
    edges: dict[str, str] = {}
    if rules.stop_words:
        stops = stop_words(lang)
        closed = foreign_words(lang)
        for c in rows:
            if rules.closed_edges and c.content_words >= 2:
                word = _stop_edge(c.term, closed)
                if word is not None:
                    edges[c.key] = word
    for c, p in zip(rows, pct, strict=True):
        c.percentile = float(p)
        container = (
            next(
                (
                    k
                    for k, n in c.containers
                    if k in candidates
                    and k not in edges
                    and n >= rules.fragment_share * max(c.occurrences, 1)
                ),
                None,
            )
            if rules.fragment_share is not None
            else None
        )
        single = c.content_words < 2
        if (
            rules.stop_words
            and single
            and (c.classes == FOREIGN_CLASS or c.key in stops or c.term.lower() in stops)
        ):
            c.band, c.reason = "aside", "stop-word"
        elif c.key in edges:
            c.band, c.reason = "aside", f"stop-word-edge: {edges[c.key]}"
        elif container is not None:
            c.band, c.reason = "aside", f"part-of: {candidates[container].term}"
        elif c.name_kind and c.name_share >= rules.name_share:
            c.band, c.reason = "aside", f"name: {c.name_kind}"
        elif p < rules.drop_share:
            c.band, c.reason = "aside", "low-score"
        elif (
            single
            and rules.even_spread is not None
            and c.people_share >= rules.even_people
            and c.spread >= rules.even_spread
        ):
            c.band, c.reason = "aside", "even-spread"
        elif single:
            c.band, c.reason = "check", "single-word"
        else:
            edge = (c.classes[0] if edge_first else c.classes[-1]) if c.classes else ""
            word = c.key.split(" ")[0 if edge_first else -1]
            spread = rules.generic_spread
            if edge == "A" and spread is not None and word_people[word] >= spread * n_people:
                c.band, c.reason = "check", f"common-modifier: {word}"
            elif p < 1.0 - rules.keep_share:
                c.band, c.reason = "check", "below-threshold"
            else:
                c.band, c.reason = "kept", "multiword"
