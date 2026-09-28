# SPDX-License-Identifier: MIT
"""The engine on a project: one runner per stage, and the settings each run gets.

Each runner builds the engine's :class:`~cartolex.context.RunContext` for its
stage (paths from :mod:`cartolex.build.enginefiles`, settings from the project
and the parameters), calls the engine, and returns the counts to record.

**Settings.** ``project.json`` gives the corpus slots (in order), the languages
and the domain title; ``decisions/stopwords.json`` the stop-word additions and
removals; ``decisions/prompts/`` the prompt overrides. Parameters map onto the
engine's settings (``docs/dev/build.md`` has the table); a stage reads the
parameters of the stages before it from their ``run.json``, so every stage runs
with the values the results it reads were made with.

**The AI clean-up** needs an :class:`AIAccess`: a key, or a client of one's own
(a test, the reference run). :func:`engine_registry` gives cartolex's stages
with it.
"""

from __future__ import annotations

import csv
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..project.models import ProjectFile
from .enginefiles import UNAVAILABLE, copy_amended, engine_paths
from .execution import Cancelled, StageRefused
from .params import theme_level_sizes

if TYPE_CHECKING:
    from ..context import RunContext
    from ..lexicon.config import KeywordsConfig
    from ..project.project import Project
    from .execution import StageContext
    from .stages import Registry

__all__ = [
    "AIAccess",
    "engine_registry",
    "keywords_settings",
    "run_context",
    "stopword_overrides",
    "theme_levels",
]


@dataclass(frozen=True)
class AIAccess:
    """How the AI clean-up reaches its provider.

    *api_key* is the provider's key; *client_factory*, when given, replaces the
    provider SDK's client class (a test or a reference run answers with a model
    of its own). *max_concurrent* caps the calls in flight (default: the
    engine's).
    """

    api_key: str | None = None
    client_factory: Callable[..., Any] | None = None
    max_concurrent: int | None = None

    def __repr__(self) -> str:  # never print a key
        return (
            f"AIAccess(api_key={'set' if self.api_key else None}, "
            f"client_factory={'set' if self.client_factory else None}, "
            f"max_concurrent={self.max_concurrent})"
        )


# ── settings ─────────────────────────────────────────────────────────────────


def _param(ctx: StageContext, stage_id: str, name: str, default: Any = None) -> Any:
    """A parameter of *stage_id*: this run's, or the one recorded by that stage's results."""
    if stage_id == ctx.stage.id:
        return ctx.params.get(name, default)
    record = ctx.record(stage_id)
    if record is None or name not in record.parameters:
        return default
    return record.parameters[name].value


def stopword_overrides(project: Project) -> dict[str, Any]:
    """``decisions/stopwords.json`` as the engine's override blocks (``{}`` when absent or empty).

    The words added or removed, in every language, extend or shrink the
    engine's list of words that are never keywords.
    """
    from ..project.files import read_model
    from ..project.models import StopwordsFile

    path = project.layout.stopwords_json
    if not path.exists():
        return {}
    doc = read_model(path, StopwordsFile)
    add = sorted({w for words in doc.add.values() for w in words})  # type: ignore[attr-defined]
    remove = sorted({w for words in doc.remove.values() for w in words})  # type: ignore[attr-defined]
    if not add and not remove:
        return {}
    return {"add": {"basic_blacklist": add}, "remove": {"basic_blacklist": remove}}


def keywords_settings(
    config: ProjectFile,
    *,
    recency_years: int | None = None,
    min_people: int | None = None,
    max_share: float | None = None,
    max_keywords: int | None = None,
    counting_unit: str | None = None,
    llm_max_concurrent: int | None = None,
) -> KeywordsConfig:
    """The engine's settings for a project: its slots, languages, domain and parameters.

    A parameter left ``None`` keeps the engine's default.
    """
    from ..lexicon.config import CorpusSlot, KeywordsConfig

    values: dict[str, Any] = {
        "corpus_slots": tuple(
            CorpusSlot(
                s.id,
                fit=s.fit,
                trajectory=s.trajectory,
                doc_types=tuple(s.doc_types) if s.doc_types else None,
            )
            for s in config.slots
        ),
        "reference_language": config.languages.reference,
        "corpus_languages": tuple(config.languages.corpus),
        "display_languages": tuple(config.languages.display),
        "domain_title": config.identity.domain_title,
        "domain_description": config.identity.domain_description,
    }
    if config.identity.ai is not None:
        values["llm_model"] = config.identity.ai.model
    for field, value in (
        ("kw_recency_years", recency_years),
        ("min_df", min_people),
        ("max_df", max_share),
        ("global_top_n", max_keywords),
        ("counting_unit", counting_unit),
        ("llm_max_concurrent", llm_max_concurrent),
    ):
        if value is not None:
            values[field] = value
    return KeywordsConfig(**values)


def _folders(ctx: StageContext) -> dict[str, Path]:
    return {**ctx.upstream, ctx.stage.id: ctx.out}


def _progress_bridge(ctx: StageContext, lo: float = 0.0, hi: float = 1.0) -> Callable:
    """The engine's progress, scaled into [lo, hi] of the stage, at most one event per 1 %."""
    last = {"f": -1.0, "t": 0.0}

    def forward(fraction: float, message: str) -> None:
        f = lo + (hi - lo) * float(fraction)
        now = time.monotonic()
        if f - last["f"] >= 0.01 or now - last["t"] >= 1.0:
            last["f"], last["t"] = f, now
            ctx.progress(f, message)

    return forward


def run_context(
    ctx: StageContext,
    settings: KeywordsConfig,
    *,
    lo: float = 0.0,
    hi: float = 1.0,
    ai_client: Callable[..., Any] | None = None,
) -> RunContext:
    """The engine's run context for this stage run."""
    from ..context import RunContext
    from ..lexicon.stopwords_config import StopwordProfile

    year = _param(ctx, "corpus.assemble", "year", None)
    extra: dict[str, Any] = {} if year is None else {"now_year": int(year)}
    return RunContext(
        paths=engine_paths(ctx.stage.id, _folders(ctx), ctx.layout.root),
        settings=settings,
        stopwords=StopwordProfile.default().with_overrides(stopword_overrides(ctx.project)),
        progress=_progress_bridge(ctx, lo, hi),
        cancel=lambda: ctx.cancel_requested,
        ai_client=ai_client,
        **extra,
    )


def _settings(ctx: StageContext, **more: Any) -> KeywordsConfig:
    return keywords_settings(
        ctx.project.config,
        recency_years=_param(ctx, "corpus.assemble", "recency_years"),
        min_people=_param(ctx, "keywords.extract", "min_people"),
        max_share=_param(ctx, "keywords.extract", "max_share"),
        max_keywords=_param(ctx, "keywords.build", "max_keywords"),
        counting_unit=_param(ctx, "keywords.extract", "counting_unit"),
        **more,
    )


def _engine_call(ctx: StageContext, fn: Callable[[], Any]) -> Any:
    """Call the engine; turn its cancel into the build's, and check where it wrote."""
    from ..context import RunCancelled
    from ..lexicon.mistral_client import LLMCancelled

    ctx.check_cancel()
    try:
        result = fn()
    except (RunCancelled, LLMCancelled) as exc:
        raise Cancelled(f"{ctx.stage.id} was cancelled") from exc
    stray = ctx.out / UNAVAILABLE
    if stray.exists():
        wrote = sorted(p.relative_to(stray).as_posix() for p in stray.rglob("*") if p.is_file())
        raise RuntimeError(
            f"{ctx.stage.id} wrote files it does not own (the ownership table is wrong): {wrote}"
        )
    return result


def _rows(path: Path) -> int:
    with open(path, encoding="utf-8", newline="") as fh:
        return max(0, sum(1 for _ in fh) - 1)


# ── the runners ──────────────────────────────────────────────────────────────


def run_corpus(ctx: StageContext) -> dict[str, int]:
    """``corpus.assemble``: the engine's corpus from the project's tables and decisions."""
    from ..project.corpus import assemble_corpus

    config = ctx.project.config
    ctx.progress(0.0, "gathering the texts")
    assemble_corpus(
        ctx.layout,
        config,
        ctx.out,
        parts=ctx.params["parts"],
        provider_priority=ctx.params["provider_priority"],
        doc_types=ctx.params["doc_types"],
    )
    people: set[tuple[str, str, str]] = set()
    texts = characters = 0
    for slot in config.slots:
        if not slot.fit:
            continue
        with open(ctx.out / slot.id / "index.csv", encoding="utf-8", newline="") as fh:
            people |= {(r["last_name"], r["first_name"], r["unit"]) for r in csv.DictReader(fh)}
        folder = ctx.out / slot.id / "texts"
        files = list(folder.iterdir()) if folder.is_dir() else []
        texts += len(files)
        characters += sum(len(f.read_text(encoding="utf-8")) for f in files)
    if not people:
        raise StageRefused(
            "no mapped person has a text in a fit slot: set roles in decisions/people.csv"
        )
    return {
        "people": len(people),
        "mapped_units": len(people),
        "texts": texts,
        "characters": characters,
    }


def run_extract(ctx: StageContext) -> dict[str, int]:
    """``keywords.extract``: candidates per corpus language (the engine's extraction)."""
    from ..lexicon import run_pipeline_stage_1

    rctx = run_context(ctx, _settings(ctx))
    _engine_call(ctx, lambda: run_pipeline_stage_1(rctx))
    counts = {
        f"candidates_{lang}": _rows(rctx.paths.raw_terms_csv(lang))
        for lang in rctx.settings.corpus_languages
    }
    counts["candidates"] = _rows(rctx.paths.global_terms_csv)
    return counts


def triage_runner(ai: AIAccess | None) -> Callable[[StageContext], dict[str, int]]:
    """The ``keywords.triage`` runner, reaching the provider through *ai*."""

    def run_triage(ctx: StageContext) -> dict[str, int]:
        from ..lexicon.llm_triage import run_pipeline_stage_2_llm

        identity = ctx.project.config.identity.ai
        if identity is None or identity.provider != "mistral":
            raise StageRefused(
                "the AI clean-up needs identity.ai in project.json with the provider "
                "'mistral' (the one cartolex can call)"
            )
        if ai is None or (not ai.api_key and ai.client_factory is None):
            raise StageRefused(
                "no AI key was given to the build: pass one (cartolex build reads "
                "MISTRAL_API_KEY), or switch the AI clean-up off"
            )
        rctx = run_context(
            ctx,
            _settings(ctx, llm_max_concurrent=ai.max_concurrent),
            ai_client=ai.client_factory,
        )
        result = _engine_call(
            ctx, lambda: run_pipeline_stage_2_llm(rctx, api_key=ai.api_key or "given-client")
        )
        if result is None:
            raise RuntimeError("the AI clean-up did not run")
        return {
            "accepted": len(result.get("accepted", [])),
            "rejected": len(result.get("rejected", [])),
        }

    return run_triage


def _keyword_decisions(ctx: StageContext) -> None:
    """``decisions/keywords.csv`` as the engine's exclusion, keep and merge files."""
    from ..project.files import atomic_write_bytes, json_bytes
    from ..project.tables import read_decision_csv

    rows = read_decision_csv(ctx.layout.keywords_csv, "keywords")
    if not rows:
        return
    by = {
        d: sorted({r["term"].strip().lower() for r in rows if r["decision"] == d})
        for d in ("exclude", "keep")
    }
    for decision, name in (("exclude", "excluded.csv"), ("keep", "kept.csv")):
        if by[decision]:
            text = "term\n" + "".join(
                f'"{t}"\n' if "," in t or '"' in t else f"{t}\n" for t in by[decision]
            )
            atomic_write_bytes(ctx.out / "decisions" / name, text.encode("utf-8"))
    merged = {
        r["term"].strip().lower(): r["target"].strip().lower()
        for r in rows
        if r["decision"] == "merge" and r["target"].strip()
    }
    if merged:
        atomic_write_bytes(
            ctx.out / "decisions" / "merged.json", json_bytes(dict(sorted(merged.items())))
        )


def run_build(ctx: StageContext) -> dict[str, int]:
    """``keywords.build``: the vocabulary, per-person keywords and the person roster."""
    import pandas as pd

    from ..lexicon import run_pipeline_stage_3
    from ..lexicon.io_helpers import build_researcher_index

    _keyword_decisions(ctx)
    rctx = run_context(ctx, _settings(ctx), hi=0.9)
    _engine_call(ctx, lambda: run_pipeline_stage_3(rctx))
    roster = rctx.paths.roster_csv
    before = roster.read_bytes() if roster.exists() else b""
    people = _engine_call(ctx, lambda: build_researcher_index(rctx))
    terms = pd.read_csv(rctx.paths.person_terms_csv, usecols=["term"])["term"]
    return {
        "kept_keywords": int(terms.astype(str).str.lower().nunique()),
        "concepts": _rows(rctx.paths.refined_pairs_csv),
        "roster_people": int(people),
        "roster_rewrite_identical": int(before == roster.read_bytes()),
    }


def run_space(ctx: StageContext) -> dict[str, int]:
    """``themes.space``: the person × keyword matrix and its SVD space."""
    from ..atlas import driver

    rctx = run_context(ctx, _settings(ctx))
    wanted = ctx.params["dimensions"]
    _engine_call(ctx, lambda: driver.run_svd(rctx, svd_n_components=wanted))
    from ..atlas.model_files import load_svd

    got = int(load_svd(rctx.paths.svd_model_json).n_components)
    if got < wanted:
        ctx.warn(
            f"the space has {got} dimensions, not {wanted}: no more than the people or keywords"
        )
    return {"terms": _rows(rctx.paths.atlas_terms_csv), "dimensions": got}


def theme_levels(params: Mapping[str, Any], kept_keywords: int) -> tuple[int, ...]:
    """The number of groups per theme level, from the top (``level_sizes`` when set)."""
    if params.get("level_sizes"):
        return tuple(int(n) for n in params["level_sizes"])
    return theme_level_sizes(
        kept_keywords, params["depth"], params["top_groups"], params["keywords_per_group"]
    )


def engine_levels(params: Mapping[str, Any], kept_keywords: int) -> tuple[int, int, list[str]]:
    """The engine's two levels (themes, topics) for the theme levels, with any warning.

    The engine builds themes over topics: the top level gives the themes, the
    finest the topics. One level gives the themes, with about
    ``keywords_per_group`` keywords per topic beneath; three or four levels keep
    their top and finest levels.
    """
    levels = theme_levels(params, kept_keywords)
    warnings: list[str] = []
    themes = levels[0]
    if len(levels) >= 2:
        topics = levels[-1]
        if len(levels) > 2:
            warnings.append(
                f"the engine builds two levels: themes ({themes}) and topics ({topics}); "
                f"the {len(levels) - 2} level(s) between are not built yet"
            )
    else:
        topics = max(themes, round(kept_keywords / params["keywords_per_group"]))
        warnings.append(
            f"the engine builds two levels: the {themes} themes have {topics} topics beneath"
        )
    return themes, topics, warnings


def run_group(ctx: StageContext) -> dict[str, int]:
    """``themes.group``: topics (the term clustering) and the draft theme tree."""
    from ..atlas import driver
    from ..lexicon.subfields import draft_subfields

    kept = int(ctx.sizes.kept_keywords or 0)
    themes, topics, warnings = engine_levels(ctx.params, kept)
    for w in warnings:
        ctx.warn(w)
    rctx = run_context(ctx, _settings(ctx), hi=0.7)
    _engine_call(ctx, lambda: driver.run_clustering(rctx, n_concepts=topics))
    rctx = rctx.replace(progress=_progress_bridge(ctx, 0.7, 1.0))
    drafted = _engine_call(ctx, lambda: draft_subfields(rctx, n_subfields=themes))
    return {"topics": topics, "themes": len(drafted)}


def _apply(ctx: StageContext, rctx: RunContext) -> dict[str, int]:
    from ..lexicon.subfields import apply_subfields

    returned = _engine_call(ctx, lambda: apply_subfields(rctx))
    written = json.loads(rctx.paths.subfields_json.read_text(encoding="utf-8"))
    return {
        "themes": len(written.get("subfields", [])),
        "topics": len(written.get("concepts", [])),
        "applied_matches_file": int(returned == written),
    }


def run_apply(ctx: StageContext) -> dict[str, int]:
    """``themes.apply``: the curated theme tree when there is one, else the draft."""
    counts: dict[str, int] = {}
    if ctx.layout.themes_json.exists():
        counts["curated"] = 1
        _curated_from_themes(ctx)
    rctx = run_context(ctx, _settings(ctx))
    return {**counts, **_apply(ctx, rctx)}


def _vocabulary(folder: Path) -> tuple[list[str], dict[str, float]]:
    """The lexical data's terms in row order, and each term's summed score."""
    import numpy as np

    from ..atlas.model_files import load_lexical_data

    data = load_lexical_data(folder / "models" / "lexical_data.json")
    terms = [str(t) for t in data.terms]
    sums = np.asarray(data.X.sum(axis=0)).ravel()
    return terms, {t: float(v) for t, v in zip(terms, sums, strict=True)}


def prepare_themes(project: Project) -> list[str]:
    """Before ``themes.apply``: rebase ``decisions/themes.json`` onto the current vocabulary.

    A new keyword is proposed the node of its draft topic when the tree has it,
    else set aside; either way it is marked « to check ». The rebase is saved as
    a new version of the tree (its reconciliation is in the description).
    """
    from ..project.themes import rebase, vocabulary_fingerprint, vocabulary_of
    from ..project.themes_versions import read_themes, save_themes
    from .records import read_record

    tree, fp = read_themes(project)
    space = project.layout.stage("themes.space")
    if tree is None or not (space / "models" / "lexical_data.json").exists():
        return []
    terms, _ = _vocabulary(space)
    if tree.based_on.vocabulary == vocabulary_fingerprint(terms):
        return []
    draft_path = project.layout.stage("themes.group") / "subfields_draft.json"
    topic_of: dict[str, str] = {}
    if draft_path.exists():
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        for concept in draft.get("concepts", []):
            for i in concept.get("term_indices", []):
                if 0 <= int(i) < len(terms):
                    topic_of[terms[int(i)]] = f"c{concept['id']}"
    deepest = {n.id for n in tree.nodes if n.id not in {m.parent for m in tree.nodes if m.parent}}
    known = vocabulary_of(tree)
    proposals = {
        t: (topic_of[t] if topic_of.get(t) in deepest else None) for t in terms if t not in known
    }
    record = read_record(project.layout, "themes.space")
    rebased = rebase(
        tree, terms, proposals, run=f"themes.space/{record.run_id}" if record else None
    )
    saved = save_themes(project, rebased.tree, expected=fp, action="rebase onto the new vocabulary")
    return [f"theme tree rebased: {rebased.description}"] if saved.written else []


def _curated_from_themes(ctx: StageContext) -> None:
    """Write the engine's curated document (``curated.json``) from ``decisions/themes.json``."""
    from ..project.files import atomic_write_bytes, json_bytes
    from ..project.themes import vocabulary_fingerprint
    from ..project.themes_curated import to_curated
    from ..project.themes_versions import read_themes

    tree, _ = read_themes(ctx.project)
    if tree is None:
        return
    if tree.depth != 2:
        raise StageRefused(
            f"decisions/themes.json has {tree.depth} level(s); the engine applies trees of "
            "two levels (themes over topics) for now"
        )
    terms, scores = _vocabulary(ctx.folder("themes.space"))
    if tree.based_on.vocabulary != vocabulary_fingerprint(terms):
        raise StageRefused(
            "decisions/themes.json is not based on the current vocabulary (its rebase failed)"
        )
    config = ctx.project.config
    doc = to_curated(
        tree,
        terms,
        reference_language=config.languages.reference,
        domain_title=config.identity.domain_title,
        scores=scores,
    )
    atomic_write_bytes(ctx.out / "curated.json", json_bytes(doc))


#: Keys of a map version's layout parameters → the layout stage's arguments.
LAYOUT_PARAMS = {
    "n_neighbors": "umap_n_neighbors",
    "min_dist": "umap_min_dist",
    "metric": "umap_metric",
    "n_epochs": "umap_n_epochs",
    "spread": "umap_spread",
    "set_op_mix_ratio": "umap_set_op_mix_ratio",
    "local_connectivity": "umap_local_connectivity",
    "repulsion_strength": "umap_repulsion_strength",
    "negative_sample_rate": "umap_negative_sample_rate",
    "layout": "umap_layout",
}


def prepare_maps(project: Project) -> list[str]:
    """Before the first layout: add and pin map version ``v1`` (seed: ``params.json``'s)."""
    from ..project.maps import add_version, read_maps, save_maps

    maps, fp = read_maps(project.layout)
    if maps.pinned is not None:
        return []
    if maps.versions:
        raise StageRefused(
            "decisions/maps.json lists map versions but pins none: pin one "
            "(cartolex versions FOLDER --pin ID)"
        )
    params, _ = project.read_params()
    maps, version = add_version(maps, seed=params.seed, note="the first map, from the defaults")
    save_maps(project.layout, maps, expected=fp, action="first map version")
    return [f"added and pinned map version {version}"]


def run_layout(ctx: StageContext) -> dict[str, int]:
    """``map.layout``: the map of the pinned version, then the themes placed on it."""
    from ..atlas import driver
    from ..project.maps import pinned, read_maps

    maps, _ = read_maps(ctx.layout)
    version = pinned(maps)
    if version is None:
        raise StageRefused("no pinned map version in decisions/maps.json")
    if version.layout.method != "umap":
        raise StageRefused(f"the layout method {version.layout.method!r} is not available")
    unknown = sorted(set(version.layout.params) - set(LAYOUT_PARAMS))
    if unknown:
        raise StageRefused(
            f"map version {version.id}: unknown layout parameter(s) {unknown}; "
            f"known: {sorted(LAYOUT_PARAMS)}"
        )
    kwargs = {LAYOUT_PARAMS[k]: v for k, v in version.layout.params.items()}
    copy_amended(ctx.stage.id, _folders(ctx))
    rctx = run_context(ctx, _settings(ctx), hi=0.9)
    _engine_call(
        ctx, lambda: driver.run_umap(rctx, umap_random_state=version.layout.seed, **kwargs)
    )
    applied = _apply(ctx, rctx.replace(progress=_progress_bridge(ctx, 0.9, 1.0)))
    return {"version": int(version.id[1:]) if version.id[1:].isdigit() else 0, **applied}


def run_trajectories(ctx: StageContext) -> dict[str, int]:
    """``map.trajectories``: positions per person and time window."""
    from ..atlas import driver

    rctx = run_context(ctx, _settings(ctx))
    _engine_call(ctx, lambda: driver.run_trajectories(rctx, bin_years=ctx.params["window_years"]))
    if not rctx.paths.trajectories_csv.exists():
        raise RuntimeError("the trajectories were not computed (see the log for why)")
    return {"points": _rows(rctx.paths.trajectories_csv)}


def run_overlays(ctx: StageContext) -> dict[str, int]:
    """``overlays.position``: each projected set placed on the finished map.

    Writes ``<set>/positions.json`` per set: each person's place in the space
    and on the map, keywords, nearest keywords and theme and topic weights.
    """
    import numpy as np
    import pandas as pd

    from ..atlas.model_files import load_embeddings
    from ..lexicon.positioning import (
        concept_svd_centroids,
        load_positioning_models,
        project_text,
        scored_top_terms_for_vector,
        subfield_svd_centroids,
        subfield_weights_for_vector,
    )
    from ..project.files import atomic_write_bytes, json_bytes

    rctx = run_context(ctx, _settings(ctx))
    tfidf, restricted_terms, svd, anchors = _engine_call(ctx, lambda: load_positioning_models(rctx))
    aliases = pd.read_csv(rctx.paths.term_aliases_csv, dtype=str, keep_default_na=False)
    alias_map = dict(zip(aliases["alias"], aliases["canonical"], strict=True))
    emb = load_embeddings(rctx.paths.embeddings_json)
    applied = json.loads(rctx.paths.subfields_json.read_text(encoding="utf-8"))
    sf_centroids = subfield_svd_centroids(
        applied.get("subfields", []), emb.Z_terms, restricted_terms
    )
    c_centroids = concept_svd_centroids(applied.get("concepts", []), emb.Z_terms, restricted_terms)
    corpus = ctx.folder("corpus.assemble") / "overlays"
    placed = 0
    sets = ctx.project.config.overlays
    for n_set, overlay in enumerate(sets):
        folder = corpus / overlay.id
        index = folder / "index.csv"
        if not index.exists():
            continue
        with open(folder / "people.csv", encoding="utf-8", newline="") as fh:
            who = {
                (r["last_name"], r["first_name"], r["unit"]): r["person_id"]
                for r in csv.DictReader(fh)
            }
        texts: dict[tuple[str, str, str], list[str]] = {}
        with open(index, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                key = (row["last_name"], row["first_name"], row["unit"])
                texts.setdefault(key, []).append((folder / row["txt_path"]).read_text("utf-8"))
        items = []
        for i, key in enumerate(sorted(texts, key=lambda k: who.get(k, ""))):
            ctx.check_cancel()
            ctx.progress((n_set + i / max(1, len(texts))) / max(1, len(sets)), overlay.id)
            z, top = project_text(
                "\n\n".join(texts[key]),
                tfidf=tfidf,
                restricted_terms=restricted_terms,
                svd=svd,
                alias_map=alias_map,
                length_bonus_alpha=rctx.settings.length_bonus_alpha,
                top_k=10,
                top_n=rctx.settings.top_n_researcher,
            )
            items.append(
                {
                    "person_id": who.get(key, ""),
                    "z": [float(v) for v in np.asarray(z).ravel()],
                    "keywords": [{"term": t["term"], "score": float(t["score"])} for t in top],
                    "near_terms": scored_top_terms_for_vector(
                        z, emb.Z_terms, restricted_terms, k=10
                    ),
                    "themes": subfield_weights_for_vector(z, sf_centroids),
                    "topics": subfield_weights_for_vector(z, c_centroids),
                }
            )
        if items and anchors is not None:
            xy = anchors.place(np.vstack([np.asarray(it["z"]) for it in items]))
            for it, (x, y) in zip(items, xy, strict=True):
                it["x"], it["y"] = float(x), float(y)
        atomic_write_bytes(
            ctx.out / overlay.id / "positions.json",
            json_bytes({"format": "cartolex-positions/1", "set": overlay.id, "items": items}),
        )
        placed += len(items)
    return {"placed": placed}


# ── the registry ─────────────────────────────────────────────────────────────


RUNNERS: dict[str, Callable[[StageContext], Mapping[str, int] | None]] = {
    "corpus.assemble": run_corpus,
    "keywords.extract": run_extract,
    "keywords.build": run_build,
    "themes.space": run_space,
    "themes.group": run_group,
    "themes.apply": run_apply,
    "map.layout": run_layout,
    "map.trajectories": run_trajectories,
    "overlays.position": run_overlays,
}


def engine_registry(ai: AIAccess | None = None) -> Registry:
    """cartolex's stages with their runners; the AI clean-up reaches its provider through *ai*."""
    from .stages import STAGES

    return STAGES.with_runners({"keywords.triage": triage_runner(ai)})
