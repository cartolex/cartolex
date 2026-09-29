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
    "PREVIEW_SAMPLE",
    "STEPS",
    "grouping_view",
    "keywords_view",
    "layout_preview",
    "layout_view",
    "preview_key",
    "space_view",
    "step_view",
    "texts_view",
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
#: The layout parameters the screen offers per method, with the engine's defaults
#: (``cartolex.atlas.driver.AtlasDefaults``) and their limits.
LAYOUT_DEFAULTS: dict[str, list[dict[str, Any]]] = {
    "umap": [
        {"name": "n_neighbors", "type": "int", "default": 25, "minimum": 2, "maximum": 200},
        {"name": "min_dist", "type": "float", "default": 0.3, "minimum": 0.0, "maximum": 1.0},
    ],
    "tsne": [
        {"name": "perplexity", "type": "float", "default": 30.0, "minimum": 2.0, "maximum": 200.0}
    ],
    "tree": [],
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


def _engine_settings(ctx: Any) -> list[dict[str, Any]]:
    """The scoring's settings this version fixes (not parameters): what they are and their value."""
    from cartolex.lexicon.scoring import BandRules, ScoringOptions

    rules, options = BandRules(), ScoringOptions()
    out = [
        {"name": "vote", "value": options.vote},
        {"name": "length_bonus_alpha", "value": options.length_bonus_alpha},
    ]
    hyper = ctx.layout.stage("keywords.build") / "keywords_hyperparams.json"
    if hyper.is_file():
        try:
            doc = json.loads(hyper.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        for name in ("ngram_range", "weights_basis", "nested_threshold"):
            if name in doc:
                out.append({"name": name, "value": doc[name]})
    for name in (
        "fragment_share",
        "drop_share",
        "keep_share",
        "generic_spread",
        "name_share",
        "stop_words",
        "even_spread",
        "even_people",
    ):
        out.append({"name": f"bands.{name}", "value": getattr(rules, name)})
    return out


def keywords_view(runtime: Any, ctx: Any) -> dict[str, Any]:
    """The candidates of the extraction by band, reason and language; the scores' spread; the
    vocabulary's size against its cap; the scoring's fixed settings."""
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
        },
        "fixed": _engine_settings(ctx),
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

    view = dict(_cached(runtime, ("space", ctx.id, record.run_id), compute))
    return {**view, "run": record.run_id, "wanted": _value(record, "dimensions")}


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
        from cartolex.build.engine import theme_levels
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


def _methods() -> list[str]:
    from cartolex.atlas.reducers import opentsne_available

    return ["umap", "tsne", "tree"] if opentsne_available() else ["umap", "tree"]


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
        "methods": _methods(),
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
                "points": [[*p, h] for p, h in zip(_round(cur), hues, strict=True)],
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
