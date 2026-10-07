# SPDX-License-Identifier: MIT
"""The method screen's diagnostics: what each step of the build produced, read from its outputs.

Each step (:data:`STEPS`) groups the stages whose parameters the screen shows;
its diagnostic is computed from the files the stages wrote and cached by their
run ids (a new run, a new answer). Nothing here writes to the project. The
layout's preview (:func:`layout_preview`) draws a sample of the people with a
method and its parameters, compared with the map on the same sample; it runs
as a job and is cached by the space's run.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping
from typing import Any

import numpy as np

from .messages import empty

__all__ = [
    "LAYOUT_DEFAULTS",
    "LAYOUT_METHODS",
    "PREVIEW_SAMPLE",
    "STEPS",
    "grouping_view",
    "keywords_preview",
    "keywords_view",
    "layout_preview",
    "layout_view",
    "preview_key",
    "space_view",
    "step_view",
    "texts_view",
    "unavailable_methods",
]

#: The steps of the method screen, in pipeline order, and the stages of each.
STEPS: dict[str, tuple[str, ...]] = {
    "texts": ("corpus.assemble",),
    "keywords": ("keywords.extract", "keywords.triage", "keywords.build"),
    "space": ("themes.space",),
    "grouping": ("themes.group",),
    "layout": ("map.layout", "map.trajectories"),
}
#: The neighbours compared when a reduction is measured.
NEIGHBOURS = 10
#: At most this many people are measured (exact kNN) and drawn in a preview.
PREVIEW_SAMPLE = 800
#: The score histogram's bins.
SCORE_BINS = 24
#: The metrics the screen offers for the space's distances.
_METRICS = ["cosine", "euclidean"]
#: The layout parameters the screen offers per method, with the engine's defaults
#: (``cartolex.atlas.driver.AtlasDefaults``, ``cartolex.atlas.tree_layout``) and their
#: limits: every parameter a map version's method takes (``cartolex.build.engine.LAYOUT_METHODS``)
#: but the UMAP recipe (``layout``), which a preview of the people cannot show. ``tier``
#: says how prominent each is on the screens (docs/dev/params-tiers.md).
LAYOUT_DEFAULTS: dict[str, list[dict[str, Any]]] = {
    "umap": [
        {
            "name": "n_neighbors",
            "tier": "essential",
            "type": "int",
            "default": 25,
            "minimum": 2,
            "maximum": 200,
        },
        {
            "name": "min_dist",
            "tier": "essential",
            "type": "float",
            "default": 0.3,
            "minimum": 0.0,
            "maximum": 1.0,
        },
        {
            "name": "metric",
            "tier": "advanced",
            "type": "str",
            "default": "cosine",
            "choices": _METRICS,
        },
        {
            "name": "n_epochs",
            "tier": "advanced",
            "type": "int",
            "default": None,
            "minimum": 10,
            "maximum": 5000,
            "nullable": True,
        },
        {
            "name": "spread",
            "tier": "advanced",
            "type": "float",
            "default": 1.0,
            "minimum": 0.1,
            "maximum": 10.0,
        },
        {
            "name": "set_op_mix_ratio",
            "tier": "advanced",
            "type": "float",
            "default": 1.0,
            "minimum": 0.0,
            "maximum": 1.0,
        },
        {
            "name": "local_connectivity",
            "tier": "advanced",
            "type": "int",
            "default": 1,
            "minimum": 1,
            "maximum": 50,
        },
        {
            "name": "repulsion_strength",
            "tier": "advanced",
            "type": "float",
            "default": 1.0,
            "minimum": 0.0,
            "maximum": 10.0,
        },
        {
            "name": "negative_sample_rate",
            "tier": "advanced",
            "type": "int",
            "default": 5,
            "minimum": 1,
            "maximum": 50,
        },
    ],
    "tsne": [
        {
            "name": "perplexity",
            "tier": "essential",
            "type": "float",
            "default": 30.0,
            "minimum": 2.0,
            "maximum": 200.0,
        },
        {
            "name": "metric",
            "tier": "advanced",
            "type": "str",
            "default": "cosine",
            "choices": _METRICS,
        },
    ],
    "tree": [
        {
            "name": "fill",
            "tier": "essential",
            "type": "float",
            "default": 0.62,
            "minimum": 0.05,
            "maximum": 1.0,
        },
        {
            "name": "gap",
            "tier": "intermediate",
            "type": "float",
            "default": 0.04,
            "minimum": 0.0,
            "maximum": 1.0,
        },
        {
            "name": "lean",
            "tier": "intermediate",
            "type": "float",
            "default": 0.4,
            "minimum": 0.0,
            "maximum": 1.0,
        },
        {
            "name": "sharp",
            "tier": "advanced",
            "type": "float",
            "default": 8.0,
            "minimum": 0.0,
            "maximum": 100.0,
        },
    ],
}


def _record(ctx: Any, stage: str) -> Any:
    from cartolex.build.records import read_record

    return read_record(ctx.layout, stage)


def _runs(ctx: Any, stages: tuple[str, ...]) -> dict[str, str | None]:
    out = {}
    for s in stages:
        r = _record(ctx, s)
        out[s] = r.run_id if r is not None else None
    return out


def _counts(record: Any) -> dict[str, Any]:
    return dict(record.measures.counts) if record is not None else {}


def _value(record: Any, name: str) -> Any:
    p = record.parameters.get(name) if record is not None else None
    return getattr(p, "value", p)


def _sample(n: int, size: int = PREVIEW_SAMPLE) -> np.ndarray:
    """The same even random sample of *n* rows every time (seed 0)."""
    if n <= size:
        return np.arange(n)
    return np.sort(np.random.default_rng(0).choice(n, size=size, replace=False))


def _cached(runtime: Any, key: tuple, compute: Any) -> Any:
    return runtime.table_cache.get(("method", *key), compute)


# ── texts ────────────────────────────────────────────────────────────────────


def texts_view(runtime: Any, ctx: Any) -> dict[str, Any]:
    """What gathering the texts gave: people, texts, characters, mapped people."""
    record = _record(ctx, "corpus.assemble")
    if record is None:
        return {"run": None, "empty": empty("empty_no_corpus")}
    counts = _counts(record)
    keep = ("people", "texts", "characters", "mapped_units")
    return {"run": record.run_id, "counts": {k: counts.get(k) for k in keep}}


# ── keywords ─────────────────────────────────────────────────────────────────


def _reason_code(reason: str) -> str:
    return str(reason or "").split(":", 1)[0].strip() or "none"


def _histogram(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts of the candidates' scores per band, on a log scale."""
    scores = [r["score_len"] for r in rows if r["score_len"] > 0]
    if not scores:
        return {"edges": [], "counts": {}, "scale": "log"}
    lo, hi = math.log10(min(scores)), math.log10(max(scores))
    if hi - lo < 1e-9:
        hi = lo + 1.0
    width = (hi - lo) / SCORE_BINS
    counts: dict[str, list[int]] = {}
    for r in rows:
        if r["score_len"] <= 0:
            continue
        i = min(SCORE_BINS - 1, int((math.log10(r["score_len"]) - lo) / width))
        counts.setdefault(r["band"], [0] * SCORE_BINS)[i] += 1
    edges = [round(10 ** (lo + i * width), 6) for i in range(SCORE_BINS + 1)]
    return {"edges": edges, "counts": counts, "scale": "log"}


def keywords_view(runtime: Any, ctx: Any) -> dict[str, Any]:
    """The candidates of the extraction by band, reason and language; the scores' spread; the
    vocabulary's size against its cap."""
    from .routes.keywords import extracted

    rows, run_id = extracted(runtime, ctx)
    if run_id is None:
        return {"run": None, "empty": empty("empty_no_keywords")}
    build = _record(ctx, "keywords.build")
    extract = _record(ctx, "keywords.extract")

    def compute() -> dict[str, Any]:
        bands: Counter[str] = Counter()
        reasons: dict[str, Counter[str]] = {}
        languages: dict[str, Counter[str]] = {}
        for r in rows:
            bands[r["band"]] += 1
            reasons.setdefault(r["band"], Counter())[_reason_code(r["reason"])] += 1
            languages.setdefault(r["language"], Counter())[r["band"]] += 1
        return {
            "candidates": len(rows),
            "bands": dict(bands),
            "reasons": {b: dict(c.most_common()) for b, c in reasons.items()},
            "languages": {lang: dict(c) for lang, c in sorted(languages.items())},
            "histogram": _histogram(rows),
        }

    view = dict(_cached(runtime, ("keywords", ctx.id, run_id), compute))
    counts = _counts(build)
    return {
        **view,
        "run": run_id,
        "build_run": build.run_id if build is not None else None,
        "counting_unit": _value(extract, "counting_unit"),
        "vocabulary": {
            "kept_keywords": counts.get("kept_keywords"),
            "max_keywords": _value(build, "max_keywords"),
            # The scored list the cap cuts (its length before the cap): the cap is reached
            # when it is longer than the cap, whatever the kept keywords count.
            "scored": _scored_count(runtime, ctx, build),
        },
    }


def _scored_count(runtime: Any, ctx: Any, build: Any) -> int | None:
    """The number of terms of the build's scored list (cached by its run)."""
    path = ctx.layout.stage("keywords.build") / "keywords_global_refined.csv"
    if build is None or not path.is_file():
        return None

    def compute() -> int:
        with open(path, encoding="utf-8") as fh:
            return max(0, sum(1 for _ in fh) - 1)

    return _cached(runtime, ("keywords-scored", ctx.id, build.run_id), compute)


# ── the keywords' thresholds, previewed ──────────────────────────────────────

#: The thresholds a preview applies to the stored candidates, without a new extraction: each
#: parameter's stage and the side a value may move to and still be previewed (``up``: a larger
#: value only removes candidates; ``down``: a smaller one only removes them; ``both``: the cap
#: of the vocabulary, applied to the full scored list ``keywords.build`` keeps). The others
#: (``counting_unit``, ``max_candidates``, the scoring and the bands' rules) change the scores
#: or the bands themselves: they need a new extraction.
PREVIEW_THRESHOLDS: dict[str, tuple[str, str]] = {
    "min_people": ("keywords.extract", "up"),
    "min_texts": ("keywords.extract", "up"),
    "max_share": ("keywords.extract", "down"),
    "max_keywords": ("keywords.build", "both"),
}
#: The candidates (and vocabulary entries) a preview names, the strongest first.
PREVIEW_NAMED = 8
_BANDS = ("kept", "check", "aside", "rejected")


def _preview_base(runtime: Any, ctx: Any, rows: list[dict[str, Any]], run_id: str) -> dict:
    """The arrays a preview filters (cached by the extraction's and the build's runs)."""
    import csv

    build = _record(ctx, "keywords.build")
    build_run = build.run_id if build is not None else None

    def compute() -> dict[str, Any]:
        refined: list[tuple[str, float, str]] = []
        path = ctx.layout.stage("keywords.build") / "keywords_global_refined.csv"
        if build_run is not None and path.is_file():
            with open(path, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    refined.append((r["term"], float(r.get("score") or 0), r.get("lang") or ""))
        # The kept keywords (the app's count: the keywords someone's row of the space holds)
        # and the people who hold each; with a number of keywords per person, a person whose
        # row is full takes their next term when one of theirs leaves the vocabulary.
        holders, listed = _holders(ctx) if build_run is not None else ({}, Counter())
        per_person = _value(build, "keywords_per_person")
        full = (
            set() if per_person is None else {who for who, n in listed.items() if n >= per_person}
        )
        n_people = 0
        npz = ctx.layout.stage("keywords.extract") / "term_people.npz"
        if npz.is_file():
            with np.load(npz, allow_pickle=False) as z:
                n_people = int(z["n_people"][0])
        score = np.asarray([r["score_len"] for r in rows], dtype=float)
        lower = [r["term"].casefold() for r in rows]
        return {
            "people": np.asarray([r["people"] for r in rows], dtype=np.int64),
            "texts": np.asarray([r["texts"] for r in rows], dtype=np.int64),
            "score": score,
            "band": np.asarray(
                [_BANDS.index(r["band"]) if r["band"] in _BANDS else 0 for r in rows]
            ),
            "order": np.argsort(-score, kind="stable"),
            "lower": lower,
            "rows_of": Counter(lower),
            "n_people": n_people or int(max((r["people"] for r in rows), default=0)),
            "refined": refined,
            "holders": holders,
            "full": full,
            "whole": per_person is None,
            "build_run": build_run,
        }

    return _cached(runtime, ("keywords-preview", ctx.id, run_id, build_run), compute)


def _named(rows: list[dict[str, Any]], index: Any, causes: Any = None) -> list[dict[str, Any]]:
    out = []
    for i in index[:PREVIEW_NAMED]:
        r = rows[int(i)]
        item = {k: r[k] for k in ("term", "language", "score_len", "people", "texts", "band")}
        if causes is not None:
            item["cause"] = causes[int(i)]
        out.append(item)
    return out


def _holders(ctx: Any) -> tuple[dict[str, list[int]], Counter]:
    """Each keyword of the people's rows of the space (lower case) with the people who hold it,
    and how many keywords each person's row holds: from the people × keywords matrices of
    ``keywords.build``, else (an earlier run) its per-person table."""
    import csv

    folder = ctx.layout.stage("keywords.build")
    holders: dict[str, list[int]] = {}
    listed: Counter = Counter()
    matrices = folder / "models" / "person_terms.json"
    if matrices.is_file():
        from cartolex.atlas.model_files import load_person_terms

        score, _, terms, _ = load_person_terms(matrices)
        csc = score.tocsc()
        for j, term in enumerate(terms):
            rows = csc.indices[csc.indptr[j] : csc.indptr[j + 1]]
            if len(rows):
                holders.setdefault(str(term).casefold(), []).extend(rows.tolist())
        listed.update(dict(enumerate(np.diff(score.tocsr().indptr).tolist())))
        return holders, listed
    people_csv = folder / "keywords_by_researcher_restricted.csv"
    if people_csv.is_file():
        ids: dict[tuple[str, str, str], int] = {}
        with open(people_csv, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                who = ids.setdefault((r["last_name"], r["first_name"], r["unit"]), len(ids))
                holders.setdefault(r["term"].casefold(), []).append(who)
                listed[who] += 1
    return holders, listed


def keywords_preview(runtime: Any, ctx: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    """What the keywords' thresholds *values* (any of :data:`PREVIEW_THRESHOLDS`; a missing one
    keeps the last build's) would keep of the last build's candidates and vocabulary: the counts
    by band, the kept keywords (the app's count of the vocabulary: exact, or a range when lists
    that lose a term would take their next one), the candidates that would leave, the kept
    keywords that would leave and the scored terms that could enter (the strongest first). A value only a new extraction can show
    (a looser window: candidates outside the last one were never kept) is named in ``needs``
    and the last build's value is used in its place. Nothing is written."""
    import time

    from .messages import message
    from .routes.keywords import extracted

    started = time.perf_counter()
    rows, run_id = extracted(runtime, ctx)
    if run_id is None:
        return {"run": None, "empty": empty("empty_no_keywords")}
    base = _preview_base(runtime, ctx, rows, run_id)
    extract = _record(ctx, "keywords.extract")
    build = _record(ctx, "keywords.build")
    built = {
        name: _value(extract if stage == "keywords.extract" else build, name)
        for name, (stage, _) in PREVIEW_THRESHOLDS.items()
    }
    used: dict[str, Any] = {}
    needs = []
    for name, (_, side) in PREVIEW_THRESHOLDS.items():
        want, was = values.get(name), built[name]
        if want is None or was is None:
            used[name] = was
            continue
        looser = (side == "up" and want < was) or (side == "down" and want > was)
        if looser:
            needs.append(message("preview_needs_extraction", param=name, value=want, built=was))
            used[name] = was
        else:
            used[name] = want

    people, texts = base["people"], base["texts"]
    causes = np.full(len(rows), "", dtype=object)
    drop = np.zeros(len(rows), dtype=bool)
    tests = [
        ("min_people", people < (used["min_people"] or 0)),
        ("min_texts", texts < (used["min_texts"] or 0)),
        (
            "max_share",
            people
            > (used["max_share"] if used["max_share"] is not None else 1.0) * base["n_people"],
        ),
    ]
    for name, fails in tests:
        causes[fails & ~drop] = name
        drop |= fails
    band = base["band"]
    bands = {
        b: {
            "before": int((band == i).sum()),
            "after": int(((band == i) & ~drop).sum()),
        }
        for i, b in enumerate(_BANDS)
    }
    order = base["order"]
    leaving = order[drop[order]]

    # The vocabulary, counted as the app counts it: the kept keywords, the terms of the
    # people's keywords (each person's strongest terms of the scored list keywords.build
    # keeps, cut at the cap). A term all of whose candidates (one per language) leave
    # leaves the scored list too.
    refined = base["refined"]
    dropped = Counter(base["lower"][i] for i in np.flatnonzero(drop))
    gone = {t for t, n in dropped.items() if n == base["rows_of"][t]}

    def alive(term: str) -> bool:
        return not gone or term.casefold() not in gone

    cap_before = built["max_keywords"] or len(refined)
    cap_after = used["max_keywords"] or len(refined)
    before = {t.casefold() for t, _, _ in refined[:cap_before]}
    kept_after = [e for e in refined if alive(e[0])][:cap_after]
    after = {t.casefold() for t, _, _ in kept_after}
    scored = {t.casefold() for t, _, _ in refined}
    holders = base["holders"]
    # A kept keyword stays while it stays in the scored list (a term outside it, a kept one
    # by hand, stays too): removing terms only lifts the others in each person's ranking.
    leave = {t for t in holders if t in scored and t not in after}
    stay = len(holders) - len(leave)
    # Each full list that loses a term takes its next one, which only a rebuild names: one of
    # the scored terms nobody lists yet may enter. A term new to the scored list (a larger
    # cap) may also push a kept one out of a list: the low end is then unknown.
    could = [e for e in kept_after if e[0].casefold() not in holders]
    freed = sum(1 for t in leave for who in holders[t] if who in base["full"])
    new_terms = len(after - before)
    if base["whole"]:
        # Each person's row holds every keyword they use: a term new to the scored list
        # enters when someone uses it (a rebuild says), and nothing else moves.
        could = [e for e in could if e[0].casefold() not in before]
        low, high = stay, stay + len(could)
    elif new_terms:
        low, high = None, stay + len(could)
    else:
        low, high = stay, stay + min(len(could), freed)
    strength = {t.casefold(): (sc, t, lang) for t, sc, lang in refined}
    out_of = sorted((strength[t] for t in leave), key=lambda e: -e[0])

    def entries(items: list[tuple[str, float, str]]) -> list[dict[str, Any]]:
        return [
            {"term": t, "score": round(sc, 6), "language": lang}
            for t, sc, lang in items[:PREVIEW_NAMED]
        ]

    return {
        "run": run_id,
        "build_run": base["build_run"],
        "built": built,
        "used": used,
        "needs": needs,
        "people": base["n_people"],
        "candidates": {
            "before": len(rows),
            "after": int((~drop).sum()),
            "leaving": int(drop.sum()),
        },
        "bands": bands,
        "leaving": _named(rows, leaving, causes),
        "vocabulary": {
            "available": base["build_run"] is not None and bool(holders),
            "before": len(holders),
            "after": high if low == high else None,
            "after_low": low,
            "after_high": high,
            "leaving": len(leave),
            "could_enter": high - stay,
        },
        "vocabulary_entering": entries(could) if high > stay else [],
        "vocabulary_leaving": entries([(t, sc, lang) for sc, t, lang in out_of]),
        "ms": round((time.perf_counter() - started) * 1000, 2),
    }


# ── the space ────────────────────────────────────────────────────────────────


def _checkpoints(dimensions: int) -> list[int]:
    marks = [d for d in (2, 3, 5, 10, 20, 50, 100, 200, 500) if d < dimensions]
    return [*marks, dimensions]


def space_view(runtime: Any, ctx: Any) -> dict[str, Any]:
    """The share of the variance each dimension explains, and how well the space keeps each
    person's nearest people (in their keywords' TF-IDF) with its first dimensions."""
    record = _record(ctx, "themes.space")
    models = ctx.layout.stage("themes.space") / "models"
    if record is None or not (models / "svd.json").exists():
        return {"run": None, "empty": empty("empty_no_space")}

    def compute() -> dict[str, Any]:
        from cartolex.atlas.diagnostics import neighbour_overlap
        from cartolex.atlas.model_files import load_embeddings, load_lexical_data, load_svd

        svd = load_svd(models / "svd.json")
        ratio = [float(x) for x in np.asarray(svd.explained_variance_ratio_)]
        data = load_lexical_data(models / "lexical_data.json")
        emb = load_embeddings(models / "embeddings.json")
        rows = _sample(emb.Z_ind.shape[0])
        X = data.X[rows] if hasattr(data.X, "tocsr") else np.asarray(data.X)[rows]
        Z = np.asarray(emb.Z_ind, dtype=float)[rows]
        curve = []
        for d in _checkpoints(Z.shape[1]):
            curve.append(
                {
                    "dimensions": d,
                    "overlap": round(
                        neighbour_overlap(X, Z[:, :d], k=NEIGHBOURS, low_metric="cosine"), 4
                    ),
                }
            )
        return {
            "dimensions": len(ratio),
            "explained": [round(x, 6) for x in ratio],
            "explained_total": round(float(sum(ratio)), 6),
            "people": int(emb.Z_ind.shape[0]),
            "terms": int(emb.Z_terms.shape[0]),
            "neighbours": {"k": NEIGHBOURS, "sample": len(rows), "curve": curve},
        }

    from .routes.overview import space_languages

    view = dict(_cached(runtime, ("space", ctx.id, record.run_id), compute))
    return {
        **view,
        "run": record.run_id,
        "wanted": _value(record, "dimensions"),
        "unit": _value(record, "space_unit"),
        "notes": space_languages(ctx),
    }


# ── the grouping ─────────────────────────────────────────────────────────────


def _level_stats(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"min": 0, "median": 0, "max": 0, "mean": 0.0}
    arr = np.asarray(values, dtype=float)
    return {
        "min": int(arr.min()),
        "median": float(np.median(arr)),
        "max": int(arr.max()),
        "mean": round(float(arr.mean()), 2),
    }


def _dendrogram(doc: Mapping[str, Any], terms: list[str], Z_terms: np.ndarray) -> dict | None:
    """Ward's merges of the top-level themes (their keywords' centroids in the space)."""
    from scipy.cluster.hierarchy import leaves_list, linkage
    from sklearn.preprocessing import normalize

    nodes = list(doc.get("nodes") or [])
    parent = {str(n["id"]): n.get("parent") for n in nodes}
    tops = [str(n["id"]) for n in nodes if n.get("parent") is None]
    if len(tops) < 2:
        return None

    def top_of(nid: str) -> str:
        while parent.get(nid) is not None:
            nid = str(parent[nid])
        return nid

    row = {t: i for i, t in enumerate(terms)}
    members: dict[str, list[int]] = {t: [] for t in tops}
    for term, nid in (doc.get("keywords") or {}).items():
        if term in row and str(nid) in parent:
            members.setdefault(top_of(str(nid)), []).append(row[term])
    Zn = normalize(np.asarray(Z_terms, dtype=float))
    keep = [t for t in tops if members.get(t)]
    if len(keep) < 2:
        return None
    centroids = normalize(np.array([Zn[members[t]].mean(axis=0) for t in keep]))
    link = linkage(centroids, method="ward")
    return {
        "leaves": keep,
        "order": [int(i) for i in leaves_list(link)],
        "merges": [[int(a), int(b), round(float(h), 5), int(c)] for a, b, h, c in link.tolist()],
    }


def grouping_view(runtime: Any, ctx: Any) -> dict[str, Any]:
    """The proposal's levels (groups, keywords per group), the keywords too broad for any theme,
    the outline and a dendrogram of the top levels, and the comb's calibration of θ."""
    record = _record(ctx, "themes.group")
    folder = ctx.layout.stage("themes.group")
    draft = folder / "themes_draft.json"
    if record is None or not draft.exists():
        return {"run": None, "empty": empty("empty_no_grouping")}

    def compute() -> dict[str, Any]:
        from cartolex.atlas.model_files import load_embeddings, load_lexical_data
        from cartolex.build.engine import comb_options, theme_levels, ward_options
        from cartolex.lexicon.theme_tree import TEXT_KEYWORDS, TOO_BROAD, comb_calibration

        doc = json.loads(draft.read_text(encoding="utf-8"))
        nodes = list(doc.get("nodes") or [])
        parent = {str(n["id"]): n.get("parent") for n in nodes}
        level: dict[str, int] = {}

        def level_of(nid: str) -> int:
            if nid not in level:
                up = parent.get(nid)
                level[nid] = 1 if up is None else level_of(str(up)) + 1
            return level[nid]

        depth = max((level_of(str(n["id"])) for n in nodes), default=0)
        own: Counter[str] = Counter(str(v) for v in (doc.get("keywords") or {}).values())
        total: Counter[str] = Counter()
        for nid, n in own.items():
            cur: str | None = nid
            while cur is not None and cur in parent:
                total[cur] += n
                up = parent[cur]
                cur = None if up is None else str(up)
        levels = []
        for lv in range(1, depth + 1):
            ids = [str(n["id"]) for n in nodes if level_of(str(n["id"])) == lv]
            per = [own.get(i, 0) for i in ids]
            levels.append(
                {
                    "level": lv,
                    "groups": len(ids),
                    "keywords": sum(per),
                    "per_group": _level_stats(per),
                    "sizes": sorted(per, reverse=True),
                }
            )
        too_broad = sum(
            1 for e in (doc.get("set_aside") or {}).values() if e.get("reason") == TOO_BROAD
        )
        outline = [
            {
                "id": str(n["id"]),
                "parent": None if n.get("parent") is None else str(n["parent"]),
                "level": level_of(str(n["id"])),
                "names": n.get("names") or {},
                "own": own.get(str(n["id"]), 0),
                "keywords": total.get(str(n["id"]), 0),
            }
            for n in nodes
            if level_of(str(n["id"])) <= 2
        ]
        models = ctx.layout.stage("themes.space") / "models"
        dendrogram = calibration = None
        if (models / "embeddings.json").exists():
            data = load_lexical_data(models / "lexical_data.json")
            emb = load_embeddings(models / "embeddings.json")
            terms = [str(t) for t in data.terms]
            dendrogram = _dendrogram(doc, terms, emb.Z_terms)
            params = {name: _value(record, name) for name in record.parameters}
            kept = int(_counts(record).get("kept_keywords") or len(terms))
            if params.get("comb") and all(
                k in params for k in ("depth", "top_groups", "keywords_per_group")
            ):
                calibration = comb_calibration(
                    lexical_data_json=models / "lexical_data.json",
                    embeddings_json=models / "embeddings.json",
                    term_clusters_csv=folder / "umap_terms_clustered.csv",
                    text_keywords_npz=folder / TEXT_KEYWORDS,
                    level_sizes=theme_levels(params, kept),
                    comb_options=comb_options(params),
                    ward=ward_options(params),
                )
        return {
            "depth": depth,
            "levels": levels,
            "keywords": len(doc.get("keywords") or {}),
            "too_broad": too_broad,
            "outline": outline,
            "dendrogram": dendrogram,
            "calibration": calibration,
        }

    view = dict(_cached(runtime, ("grouping", ctx.id, record.run_id), compute))
    return {**view, "run": record.run_id, "comb": bool(_value(record, "comb"))}


# ── the layout ───────────────────────────────────────────────────────────────


#: Every layout method, in the order the screens list them.
LAYOUT_METHODS = ("umap", "tsne", "tree")


def unavailable_methods() -> dict[str, dict[str, Any]]:
    """The layout methods this installation cannot draw, each with the reason and the fix: the
    error ``layout_method_unavailable`` (``code``, ``params``, ``message``, ``next``)."""
    from cartolex.atlas.reducers import opentsne_available

    from .errors import body_of

    if opentsne_available():
        return {}
    reason = body_of(
        "layout_method_unavailable",
        method="tsne",
        package="openTSNE",
        command='pip install "cartolex[tsne]"',
    )
    return {"tsne": reason["error"]}


def _methods() -> list[str]:
    """The layout methods this installation can draw."""
    missing = unavailable_methods()
    return [m for m in LAYOUT_METHODS if m not in missing]


def _top_themes(ctx: Any, people: list[str]) -> tuple[list[int], list[dict[str, Any]]]:
    """Each person's heaviest top-level theme (an index, ``-1``: none) and the themes."""
    import pandas as pd

    path = ctx.layout.stage("themes.apply") / "theme_people.parquet"
    tree = ctx.layout.stage("themes.apply") / "themes_tree.json"
    if not path.exists() or not tree.exists():
        return [-1] * len(people), []
    doc = json.loads(tree.read_text(encoding="utf-8"))
    tops = [n for n in doc.get("nodes") or [] if n.get("parent") is None]
    index = {str(n["id"]): i for i, n in enumerate(tops)}
    df = pd.read_parquet(path, columns=["researcher_id", "level", "node", "weight"])
    df = df[df["level"] == 1].sort_values("weight", ascending=False)
    best = df.drop_duplicates("researcher_id").set_index("researcher_id")["node"].to_dict()
    hues = [index.get(str(best.get(p)), -1) for p in people]
    return hues, [{"id": str(n["id"]), "names": n.get("names") or {}} for n in tops]


def layout_view(runtime: Any, ctx: Any) -> dict[str, Any]:
    """The pinned map version (method, seed, parameters), how well the map keeps each person's
    nearest people of the space, the layout's own measures, and the previews computed."""
    from cartolex.project.maps import pinned, read_maps

    record = _record(ctx, "map.layout")
    maps, _ = read_maps(ctx.layout)
    version = pinned(maps)
    space = _record(ctx, "themes.space")
    out: dict[str, Any] = {
        "run": record.run_id if record is not None else None,
        "methods": list(LAYOUT_METHODS),
        "unavailable": unavailable_methods(),
        "parameters": LAYOUT_DEFAULTS,
        "pinned": None
        if version is None
        else {
            "id": version.id,
            "method": version.layout.method,
            "seed": version.layout.seed,
            "params": dict(version.layout.params),
        },
        "drawn": _counts(record).get("version"),
        "previews": list(_previews(runtime, ctx, space)),
    }
    models = ctx.layout.stage("map.layout") / "models"
    if record is None or not (models / "embeddings.json").exists():
        out["empty"] = empty("empty_no_map")
        return out

    def compute() -> dict[str, Any]:
        from cartolex.atlas.diagnostics import neighbour_overlap
        from cartolex.atlas.model_files import load_embeddings

        emb = load_embeddings(models / "embeddings.json")
        measures: dict[str, Any] = {}
        diag = ctx.layout.stage("map.layout") / "umap_diagnostics.json"
        if diag.is_file():
            try:
                raw = json.loads(diag.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            for name in ("trustworthiness", "mixing_index", "layout_engine"):
                if name in raw:
                    measures[name] = raw[name]
        if emb.umap_ind is None:
            return {"measures": measures, "overlap": None}
        rows = _sample(emb.Z_ind.shape[0])
        overlap = neighbour_overlap(
            np.asarray(emb.Z_ind)[rows], np.asarray(emb.umap_ind)[rows], k=NEIGHBOURS
        )
        return {"measures": measures, "overlap": round(overlap, 4), "sample": len(rows)}

    out.update(_cached(runtime, ("layout", ctx.id, record.run_id), compute))
    return out


# ── the layout's preview ─────────────────────────────────────────────────────


def preview_key(ctx: Any, method: str, seed: int, params: Mapping[str, Any]) -> tuple:
    """What a preview is computed from: the space's and the themes' runs, the method, its seed
    and parameters."""
    space = _record(ctx, "themes.space")
    apply = _record(ctx, "themes.apply")
    layout = _record(ctx, "map.layout")
    return (
        "preview",
        ctx.id,
        space.run_id if space is not None else None,
        apply.run_id if apply is not None and method == "tree" else None,
        layout.run_id if layout is not None else None,
        method,
        int(seed),
        tuple(sorted((k, v) for k, v in params.items() if v is not None)),
    )


def _previews(runtime: Any, ctx: Any, space: Any) -> list[dict[str, Any]]:
    """The previews computed on the current space (their scores, without the points)."""
    if space is None:
        return []
    found = []
    for key, value in runtime.preview_cache.items():
        if key[1] == ctx.id and key[2] == space.run_id:
            found.append({k: value[k] for k in ("method", "seed", "params", "overlap", "sample")})
    return found


def _round(xy: np.ndarray) -> list[list[float]]:
    return [[round(float(x), 4), round(float(y), 4)] for x, y in xy]


def layout_preview(ctx: Any, method: str, seed: int, params: Mapping[str, Any]) -> dict[str, Any]:
    """Draw a sample of the people with *method*; measure it and the map on the same sample."""
    from cartolex.atlas.diagnostics import neighbour_overlap
    from cartolex.atlas.model_files import load_embeddings, load_lexical_data
    from cartolex.atlas.reducers import preview_layout

    models = ctx.layout.stage("themes.space") / "models"
    data = load_lexical_data(models / "lexical_data.json")
    emb = load_embeddings(models / "embeddings.json")
    people = [str(p) for p in data.individuals]
    rows = _sample(len(people))
    Z = np.asarray(emb.Z_ind, dtype=float)[rows]
    tree = usage = None
    if method == "tree":
        from cartolex.lexicon.theme_tree import read_tree

        tree = read_tree(
            ctx.layout.stage("themes.apply") / "themes_tree.json", [str(t) for t in data.terms]
        )
        full = data.X_tf if getattr(data, "X_tf", None) is not None else data.X
        usage = full[rows]
    xy = preview_layout(Z, method=method, seed=seed, params=dict(params), tree=tree, usage=usage)
    hues, themes = _top_themes(ctx, [people[i] for i in rows])
    out: dict[str, Any] = {
        "method": method,
        "seed": int(seed),
        "params": dict(params),
        "sample": len(rows),
        "people": len(people),
        "overlap": round(neighbour_overlap(Z, xy, k=NEIGHBOURS), 4),
        "points": [[*p, h] for p, h in zip(_round(xy), hues, strict=True)],
        "themes": themes,
        "current": None,
    }
    current = ctx.layout.stage("map.layout") / "models" / "embeddings.json"
    if current.exists():
        drawn = load_embeddings(current)
        if drawn.umap_ind is not None and drawn.umap_ind.shape[0] == len(people):
            cur = np.asarray(drawn.umap_ind, dtype=float)[rows]
            out["current"] = {
                "overlap": round(neighbour_overlap(Z, cur, k=NEIGHBOURS), 4),
                # the preview is flat: a pinned map in space is shown from above (x, y)
                "points": [[*p, h] for p, h in zip(_round(cur[:, :2]), hues, strict=True)],
            }
    return out


def step_view(runtime: Any, ctx: Any, step: str) -> dict[str, Any]:
    """The diagnostic of *step*, with its stages and their runs."""
    view = {
        "texts": texts_view,
        "keywords": keywords_view,
        "space": space_view,
        "grouping": grouping_view,
        "layout": layout_view,
    }[step](runtime, ctx)
    return {"step": step, "stages": list(STEPS[step]), "runs": _runs(ctx, STEPS[step]), **view}
