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
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..project.models import ProjectFile, ThemesFile
from ..scale import Budget
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
    "EngineOptions",
    "engine_registry",
    "keywords_settings",
    "proposed_places",
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


@dataclass(frozen=True)
class EngineOptions:
    """What a host application gives every stage: its prompts and its function words.

    *prompt_dir* replaces the packaged prompt templates; *stopword_overlay*
    adds or removes words, per language (``{"add": {"en": [...]}}``), on top
    of each project's ``decisions/stopwords.json``. Both are the host's code,
    like cartolex's packaged lists: changing them does not by itself make a
    result out of date (force the stages that read them).

    *rejects_folder* is the machine's rejection cache
    (:class:`cartolex.lexicon.rejects.MachineRejects`): the extraction rejects
    the terms other projects' AI answers put there, and the AI clean-up by API
    adds its ``never`` answers. ``None``: cartolex's list only. Like the other
    options, a change of the cache makes no result out of date.

    *budget* is what the computer gives the stages (:class:`cartolex.scale.Budget`):
    their worker processes and scratch folder (``None``: the computer's default).
    The results never depend on it.
    """

    prompt_dir: Path | None = None
    stopword_overlay: Mapping[str, Mapping[str, Sequence[str]]] | None = None
    rejects_folder: Path | None = None
    budget: Budget | None = None


#: The options of the stage running in this context (set around a runner's call).
_OPTIONS: ContextVar[EngineOptions | None] = ContextVar("cartolex_engine_options", default=None)


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


def _overlay_overrides(
    overlay: Mapping[str, Mapping[str, Sequence[str]]] | None,
) -> dict[str, Any]:
    """A host's stop-word overlay as the engine's override blocks (``{}`` when empty)."""
    if not overlay:
        return {}
    blocks = {
        block: sorted({w for words in (overlay.get(block) or {}).values() for w in words})
        for block in ("add", "remove")
    }
    if not blocks["add"] and not blocks["remove"]:
        return {}
    return {block: {"basic_blacklist": words} for block, words in blocks.items()}


def _merged_overrides(host: Mapping[str, Any], project: Mapping[str, Any]) -> dict[str, Any]:
    """A host's overlay, then the project's own decisions on top (the project wins)."""
    if not host:
        return dict(project)

    def words(doc: Mapping[str, Any], block: str) -> set[str]:
        return set((doc.get(block) or {}).get("basic_blacklist", []))

    p_add, p_remove = words(project, "add"), words(project, "remove")
    add = (words(host, "add") - p_remove) | p_add
    remove = (words(host, "remove") - p_add) | p_remove
    return {
        block: {"basic_blacklist": sorted(ws)}
        for block, ws in (("add", add), ("remove", remove))
        if ws
    }


def keywords_settings(
    config: ProjectFile,
    *,
    recency_years: int | None = None,
    min_people: int | None = None,
    min_texts: int | None = None,
    max_share: float | None = None,
    max_keywords: int | None = None,
    counting_unit: str | None = None,
    llm_max_concurrent: int | None = None,
    **fields: Any,
) -> KeywordsConfig:
    """The engine's settings for a project: its slots, languages, domain and parameters.

    A parameter left ``None`` keeps the engine's default. *fields* are other
    :class:`~cartolex.lexicon.config.KeywordsConfig` fields (:data:`ENGINE_SETTINGS`
    names those the parameters set); a nullable one is passed as given.
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
        ("min_texts", min_texts),
        ("max_df", max_share),
        ("global_top_n", max_keywords),
        ("counting_unit", counting_unit),
        ("llm_max_concurrent", llm_max_concurrent),
    ):
        if value is not None:
            values[field] = value
    for field, value in fields.items():
        values[field] = tuple(value) if isinstance(value, list) else value
    return KeywordsConfig(**values)


#: The parameters that set the engine's settings beyond those :func:`keywords_settings`
#: names: (stage, parameter) → the :class:`~cartolex.lexicon.config.KeywordsConfig` field.
ENGINE_SETTINGS: dict[tuple[str, str], str] = {
    ("keywords.extract", "max_candidates"): "max_features",
    ("keywords.extract", "vote"): "vote",
    ("keywords.extract", "length_bonus"): "length_bonus_alpha",
    ("keywords.extract", "max_words"): "max_units",
    ("keywords.extract", "of_complement"): "of_complement",
    ("keywords.extract", "fragment_share"): "band_fragment_share",
    ("keywords.extract", "drop_share"): "band_drop_share",
    ("keywords.extract", "keep_share"): "band_keep_share",
    ("keywords.extract", "name_share"): "band_name_share",
    ("keywords.extract", "stop_words"): "band_stop_words",
    ("keywords.extract", "closed_word_edges"): "band_closed_edges",
    ("keywords.extract", "foreign_reading"): "foreign_reading",
    ("keywords.extract", "even_spread"): "band_even_spread",
    ("keywords.extract", "even_people"): "band_even_people",
    ("keywords.extract", "common_modifier"): "band_generic_spread",
    ("keywords.build", "nested_threshold"): "nested_threshold",
    ("keywords.build", "ngram_range"): "ngram_range",
    ("keywords.build", "weights_basis"): "weights_basis",
    ("keywords.build", "keywords_per_person"): "top_n_researcher",
    ("keywords.build", "keywords_per_organisation"): "top_n_unit",
    ("keywords.build", "keywords_of_field"): "top_n_domain",
}

#: A parameter the results read do not record (made before it was a parameter).
_MISSING = object()


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
    from ..context import RunContext, ThreadLimits
    from ..lexicon.stopwords_config import StopwordProfile

    year = _param(ctx, "corpus.assemble", "year", None)
    extra: dict[str, Any] = {} if year is None else {"now_year": int(year)}
    options = _OPTIONS.get() or EngineOptions()
    if options.prompt_dir is not None:
        extra["prompt_dir"] = Path(options.prompt_dir)
    budget = _budget()
    extra["threads"] = ThreadLimits(processes=budget.workers, memory_mb=budget.memory_mb)
    if budget.scratch is not None:
        extra["scratch"] = Path(budget.scratch)
    overrides = _merged_overrides(
        _overlay_overrides(options.stopword_overlay), stopword_overrides(ctx.project)
    )
    stopwords = StopwordProfile.default().with_overrides(overrides)
    return RunContext(
        paths=engine_paths(ctx.stage.id, _folders(ctx), ctx.layout.root),
        settings=settings,
        stopwords=stopwords,
        progress=_progress_bridge(ctx, lo, hi),
        cancel=lambda: ctx.cancel_requested,
        ai_client=ai_client,
        **extra,
    )


def _budget() -> Budget:
    """What the computer gives the stages (the options', else the computer's default)."""
    options = _OPTIONS.get() or EngineOptions()
    return options.budget if options.budget is not None else Budget.for_machine()


def _settings(ctx: StageContext, **more: Any) -> KeywordsConfig:
    fields: dict[str, Any] = {"extraction_n_jobs": _budget().workers}
    for (stage_id, name), field in ENGINE_SETTINGS.items():
        value = _param(ctx, stage_id, name, _MISSING)
        if value is not _MISSING:  # else the engine's default, which is the parameter's
            fields[field] = value
    return keywords_settings(
        ctx.project.config,
        recency_years=_param(ctx, "corpus.assemble", "recency_years"),
        min_people=_param(ctx, "keywords.extract", "min_people"),
        min_texts=_param(ctx, "keywords.extract", "min_texts"),
        max_share=_param(ctx, "keywords.extract", "max_share"),
        max_keywords=_param(ctx, "keywords.build", "max_keywords"),
        counting_unit=_param(ctx, "keywords.extract", "counting_unit"),
        **fields,
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
    summary = assemble_corpus(
        ctx.layout,
        config,
        ctx.out,
        parts=ctx.params["parts"],
        provider_priority=ctx.params["provider_priority"],
        doc_types=ctx.params["doc_types"],
        **{
            name: ctx.params[name]
            for name in ("duplicate_min_title", "duplicate_year_gap")
            if name in ctx.params
        },
    )
    people: set[tuple[str, str, str]] = set()
    texts = characters = 0
    for slot in config.slots:
        if not slot.fit:
            continue
        with open(ctx.out / slot.id / "people.csv", encoding="utf-8", newline="") as fh:
            people |= {(r["last_name"], r["first_name"], r["unit"]) for r in csv.DictReader(fh)}
        texts += summary.slots.get(slot.id, {}).get("texts", 0)
        characters += summary.characters.get(ctx.out / slot.id, 0)
    if not people:
        raise StageRefused(
            "no mapped person has a text yet: collect their texts, or mark as mapped the "
            "people whose texts make the map"
        )
    return {
        "people": len(people),
        "mapped_units": len(people),
        "texts": texts,
        "characters": characters,
        "duplicate_texts": summary.duplicate_texts,
    }


def project_fingerprint(project: Project) -> str:
    """A project's fingerprint in the rejection cache: never its name, the same at every build."""
    import hashlib

    config = project.config
    seed = f"{config.name}\0{config.created.at.isoformat()}\0{config.created.by}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _machine_rejects() -> Any:
    from ..lexicon.rejects import MachineRejects

    options = _OPTIONS.get() or EngineOptions()
    return None if options.rejects_folder is None else MachineRejects(options.rejects_folder)


def person_decided(project: Project) -> dict[str, list[str]]:
    """The terms a person decided on in ``decisions/keywords.csv``, per language (``""``: all)."""
    from ..project.tables import read_decision_csv

    out: dict[str, list[str]] = {}
    for r in read_decision_csv(project.layout.keywords_csv, "keywords"):
        if r["source"] == "person":
            out.setdefault(r["language"], []).append(r["term"])
    return out


def run_extract(ctx: StageContext) -> dict[str, int]:
    """``keywords.extract``: candidates per corpus language (the engine's extraction).

    It first writes the rejection snapshot (``rejects.json``): cartolex's list and
    the machine's cache, minus the terms a person decided on (none when the
    ``rejects`` parameter is false).
    """
    from ..lexicon import run_pipeline_stage_1
    from ..lexicon.rejects import snapshot
    from ..project.files import atomic_write_bytes, json_bytes

    rctx = run_context(ctx, _settings(ctx))
    snap = snapshot(
        rctx.settings.corpus_languages,
        _machine_rejects(),
        project=project_fingerprint(ctx.project),
        exempt=person_decided(ctx.project),
        enabled=bool(ctx.params.get("rejects", True)),
    )
    atomic_write_bytes(rctx.paths.rejects_json, json_bytes(snap))
    _engine_call(ctx, lambda: run_pipeline_stage_1(rctx))
    counts = {
        f"candidates_{lang}": _rows(rctx.paths.raw_terms_csv(lang))
        for lang in rctx.settings.corpus_languages
    }
    counts["candidates"] = _rows(rctx.paths.global_terms_csv)
    counts["rejected"] = _band_rows(rctx.paths.global_terms_csv, "rejected")
    return counts


def _band_rows(path: Path, band: str) -> int:
    with open(path, encoding="utf-8", newline="") as fh:
        return sum(1 for r in csv.DictReader(fh) if r.get("band") == band)


def feed_rejects(ctx: StageContext, rctx: RunContext) -> int:
    """Put the AI's ``never`` answers by API into the machine's cache; returns how many are new."""
    machine = _machine_rejects()
    path = rctx.paths.triage_decisions_json
    if machine is None or not path.exists():
        return 0
    typed = json.loads(path.read_text(encoding="utf-8")).get("typed") or {}
    with open(rctx.paths.global_terms_csv, encoding="utf-8", newline="") as fh:
        lang_of = {r["term"]: r.get("lang", "") for r in csv.DictReader(fh)}
    rows = [
        {"term": t, "language": lang_of[t]}
        for t, d in typed.items()
        if isinstance(d, dict) and d.get("category") == "never" and lang_of.get(t)
    ]
    return machine.add(rows, route="ai-api", project=project_fingerprint(ctx.project))


def triage_runner(
    access: AIAccess | Callable[[], AIAccess | None] | None,
) -> Callable[[StageContext], dict[str, int]]:
    """The ``keywords.triage`` runner, reaching the provider through *access*.

    *access* may be a function, asked at each run: an app whose key is saved
    or removed while it runs gives the build the key of the moment.
    """

    def run_triage(ctx: StageContext) -> dict[str, int]:
        from ..lexicon.llm_triage import run_pipeline_stage_2_llm

        ai = access() if callable(access) else access

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
        try:
            result = _engine_call(
                ctx, lambda: run_pipeline_stage_2_llm(rctx, api_key=ai.api_key or "given-client")
            )
        finally:
            # The tokens the provider reported, paid even when the run stops: the project's
            # total (cache/ai/usage.json); answers from the cache cost none.
            used = rctx.usage.cumulative()
            if used.total_tokens:
                from ..lexicon.llm_usage import add_to_persisted

                add_to_persisted(rctx.paths.ai_usage_json, used)
        if result is None:
            raise RuntimeError("the AI clean-up did not run")
        feed_rejects(ctx, rctx)
        return {
            "accepted": len(result.get("accepted", [])),
            "rejected": len(result.get("rejected", [])),
            "tokens_in": used.prompt_tokens,
            "tokens_out": used.completion_tokens,
        }

    return run_triage


def keyword_categories(
    rows: Sequence[Mapping[str, str]], typed: Mapping[str, Any]
) -> dict[str, str]:
    """Each keyword's category (lower-case term → category): the AI's verdicts by API
    (*typed*, for the term and its English form), then the decisions' categories
    (*rows* of ``keywords.csv``, for the term and a merge's target), which win."""
    from ..lexicon.categories import CATEGORIES, category_of

    out: dict[str, str] = {}
    for term, d in typed.items():
        if not isinstance(d, Mapping):
            continue
        category = str(d.get("category") or category_of(str(d.get("verdict") or "")))
        if category in CATEGORIES:
            out[str(term).strip().lower()] = category
            if d.get("canonical_en"):
                out.setdefault(str(d["canonical_en"]).strip().lower(), category)
    for r in rows:
        category = r.get("category") or ""
        if category in CATEGORIES:
            out[r["term"].strip().lower()] = category
            if r["decision"] == "merge" and r["target"].strip():
                out[r["target"].strip().lower()] = category
    return dict(sorted(out.items()))


def _keyword_decisions(ctx: StageContext, rctx: RunContext | None = None) -> None:
    """``decisions/keywords.csv`` as the engine's exclusion, keep and merge files, and the
    keywords' categories (``categories.json``, with the AI's verdicts by API)."""
    from ..project.files import atomic_write_bytes, json_bytes
    from ..project.tables import read_decision_csv

    rows = read_decision_csv(ctx.layout.keywords_csv, "keywords")
    if rctx is not None:
        verdicts = rctx.paths.triage_decisions_json
        typed: dict[str, Any] = {}
        if verdicts.exists():
            typed = json.loads(verdicts.read_text(encoding="utf-8")).get("typed") or {}
        found = keyword_categories(rows, typed)
        if found:
            atomic_write_bytes(rctx.paths.keyword_categories_json, json_bytes(found))
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
    accepted = copilot_gate(rows, _extraction_run(ctx))
    if accepted:
        text = "term\n" + "".join(
            f'"{t}"\n' if "," in t or '"' in t else f"{t}\n" for t in sorted(accepted)
        )
        atomic_write_bytes(ctx.out / "decisions" / "accepted.csv", text.encode("utf-8"))


def _extraction_run(ctx: StageContext) -> str | None:
    """The run id of the extraction this build reads (``None`` for a context without a
    project's layout)."""
    from .records import read_record

    if not hasattr(ctx.layout, "run_json"):
        return None
    record = read_record(ctx.layout, "keywords.extract")
    return record.run_id if record is not None else None


#: The sources of ``keywords.csv`` that are a copilot's answers the person accepted.
COPILOT_SOURCE = "ai-copilot"


def copilot_gate(rows: Sequence[Mapping[str, str]], extraction_run: str | None) -> set[str]:
    """The terms the copilot's acceptance gate lets into the vocabulary (lower case), or none.

    Once a copilot's triage was accepted for the extraction *extraction_run* (a decision of
    ``keywords.csv`` from the copilot, made after that run started), only the terms with an
    accepting decision enter, as with the AI clean-up by API: a keep (the AI's or a
    person's) and the target of a merge. Empty, the bands decide as before: no copilot
    result was accepted for this extraction, or it accepted no term.
    """
    from datetime import datetime, timezone

    if not extraction_run:
        return set()
    try:
        since = datetime.strptime(extraction_run[:16], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return set()
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not any(
        r.get("source") == COPILOT_SOURCE and (r.get("decided_at") or "") >= stamp for r in rows
    ):
        return set()
    out: set[str] = set()
    for r in rows:
        if r["decision"] == "keep":
            out.add(r["term"].strip().lower())
        elif r["decision"] == "merge" and r["target"].strip():
            out.add(r["target"].strip().lower())
    return out


def run_build(ctx: StageContext) -> dict[str, int]:
    """``keywords.build``: the vocabulary, per-person keywords and the person roster."""
    from ..lexicon import run_pipeline_stage_3
    from ..lexicon.io_helpers import build_researcher_index

    rctx = run_context(ctx, _settings(ctx), hi=0.9)
    _keyword_decisions(ctx, rctx)
    _engine_call(ctx, lambda: run_pipeline_stage_3(rctx))
    roster = rctx.paths.roster_csv
    before = roster.read_bytes() if roster.exists() else b""
    people = _engine_call(ctx, lambda: build_researcher_index(rctx))
    return {
        "kept_keywords": vocabulary_size(rctx.paths.person_terms_json),
        "concepts": _rows(rctx.paths.refined_pairs_csv),
        "roster_people": int(people),
        "roster_rewrite_identical": int(before == roster.read_bytes()),
    }


def vocabulary_size(person_terms_json: Path) -> int:
    """How many keywords the space is made of: those someone uses in the people × keywords
    matrices ``keywords.build`` wrote (in lower case, as the space counts them)."""
    import numpy as np

    from ..atlas.model_files import load_person_terms

    score, _, terms, _ = load_person_terms(person_terms_json)
    used = np.asarray((score != 0).sum(axis=0)).ravel() > 0
    return len({str(t).lower() for t, u in zip(terms, used.tolist(), strict=True) if u})


#: A space of texts warns when at least this share of its keywords is not in the reference
#: language: each language's keywords may then form themes of their own (on the demo's L
#: world, 21 % of French keywords make one theme of French keywords only). Untested on a
#: real bilingual corpus.
SPACE_LANGUAGE_SHARE = 0.10


def space_languages(paths: Any, reference: str) -> tuple[int, int]:
    """The keywords of the space, and how many of them are not in *reference* (a keyword's
    language: the one the vocabulary gives its form, else its concept's; unknown: counted in
    *reference*)."""
    import pandas as pd

    terms = pd.read_csv(paths.atlas_terms_csv, usecols=["term"], dtype=str, keep_default_na=False)
    refined = pd.read_csv(paths.refined_terms_csv, dtype=str, keep_default_na=False)
    if "lang" not in refined.columns:
        return len(terms), 0
    lang = dict(zip(refined["concept"].str.lower(), refined["lang"], strict=True))
    lang.update(zip(refined["term"].str.lower(), refined["lang"], strict=True))
    other = sum(1 for t in terms["term"] if lang.get(t.lower(), reference) not in ("", reference))
    return len(terms), other


def space_languages_apart(counts: Mapping[str, Any], unit: str) -> float | None:
    """The share of a text space's keywords outside the reference language when it reaches
    :data:`SPACE_LANGUAGE_SHARE` (``themes.space``'s *counts*), else ``None``."""
    total, other = counts.get("terms") or 0, counts.get("terms_other_language") or 0
    if unit != "text" or not total or other / total < SPACE_LANGUAGE_SHARE:
        return None
    return other / total


def run_space(ctx: StageContext) -> dict[str, int]:
    """``themes.space``: the person × keyword matrix and its SVD space."""
    from ..atlas import driver

    rctx = run_context(ctx, _settings(ctx))
    wanted = ctx.params["dimensions"]
    unit = str(ctx.params.get("space_unit", "person"))
    solver = {
        name: ctx.params[name]
        for name in ("svd_seed", "svd_iterations", "svd_algorithm")
        if name in ctx.params
    }
    _engine_call(
        ctx, lambda: driver.run_svd(rctx, svd_n_components=wanted, space_unit=unit, **solver)
    )
    from ..atlas.model_files import load_svd

    got = int(load_svd(rctx.paths.svd_model_json).n_components)
    if got < wanted:
        ctx.warn(
            f"the space has {got} dimensions, not {wanted}: no more than the people or keywords"
        )
    reference = ctx.project.config.languages.reference
    total, other = space_languages(rctx.paths, reference)
    counts = {"terms": total, "dimensions": got, "terms_other_language": other}
    share = space_languages_apart(counts, unit)
    if share is not None:
        ctx.warn(
            f"{share:.0%} of the keywords are not in {reference}: in a space of texts, themes may "
            "split by language; the people's space may suit this corpus better"
        )
    return counts


def theme_levels(params: Mapping[str, Any], kept_keywords: int) -> tuple[int, ...]:
    """The number of groups per theme level, from the top (``level_sizes`` when set)."""
    if params.get("level_sizes"):
        return tuple(int(n) for n in params["level_sizes"])
    return theme_level_sizes(
        kept_keywords, params["depth"], params["top_groups"], params["keywords_per_group"]
    )


def ward_options(params: Mapping[str, Any]) -> Any:
    """The Ward cut's settings (:class:`cartolex.atlas.clustering.WardOptions`) of
    ``themes.group``'s *params* (its defaults for those missing)."""
    from ..atlas.clustering import WardOptions

    base = WardOptions()
    return WardOptions(
        limit=int(params.get("exact_ward_limit", base.limit)),
        micro=params.get("micro_clusters", base.micro),
        seed=int(params.get("micro_seed", base.seed)),
    )


def comb_options(params: Mapping[str, Any]) -> Any:
    """The comb's settings (:class:`cartolex.lexicon.theme_comb.CombOptions`) of
    ``themes.group``'s *params* (its defaults for those missing)."""
    from ..lexicon.theme_comb import CombOptions

    base = CombOptions()
    grid = params.get("comb_grid")
    return CombOptions(
        theta=params.get("comb_theta", base.theta),
        grid=tuple(float(t) for t in grid) if grid else base.grid,
        one_level=float(params.get("comb_theta_one_level", base.one_level)),
        min_texts=int(params.get("comb_min_texts", base.min_texts)),
        max_cells=int(params.get("comb_max_cells", base.max_cells)),
        sideways=str(params.get("comb_sideways", base.sideways)),
    )


def run_group(ctx: StageContext) -> dict[str, int]:
    """``themes.group``: the finest groups (the term clustering), the levels above, the proposal.

    Writes the proposal tree (``themes_draft.json``) at every depth and, at
    depth 2, the engine's two-level draft (``subfields_draft.json``) too.
    """
    from ..atlas import driver
    from ..lexicon.subfields import draft_subfields
    from ..lexicon.theme_tree import OWN_NAME_FLOOR, TOO_BROAD, draft_themes

    kept = int(ctx.sizes.kept_keywords or 0)
    levels = theme_levels(ctx.params, kept)
    ward = ward_options(ctx.params)
    rctx = run_context(ctx, _settings(ctx), hi=0.6)
    _engine_call(
        ctx,
        lambda: driver.run_clustering(
            rctx,
            n_concepts=levels[-1],
            n_components=ctx.params.get("cluster_dimensions"),
            ward=ward,
        ),
    )
    rctx = rctx.replace(progress=_progress_bridge(ctx, 0.6, 0.8))
    doc = _engine_call(
        ctx,
        lambda: draft_themes(
            rctx,
            level_sizes=levels,
            run=f"themes.group/{ctx.run_id}",
            comb=bool(ctx.params.get("comb", True)),
            comb_options=comb_options(ctx.params),
            own_floor=float(ctx.params.get("own_name_floor", OWN_NAME_FLOOR)),
            ward=ward,
        ),
    )
    if len(levels) == 2:
        rctx = rctx.replace(progress=_progress_bridge(ctx, 0.8, 1.0))
        _engine_call(ctx, lambda: draft_subfields(rctx, n_subfields=levels[0]))
    depth = int(doc["depth"])
    per_level = [0] * depth
    for node in _tree_levels(doc):
        per_level[node - 1] += 1
    too_broad = sum(
        1 for entry in (doc.get("set_aside") or {}).values() if entry.get("reason") == TOO_BROAD
    )
    return {
        "depth": depth,
        "themes": per_level[0],
        "topics": per_level[-1],
        **{f"groups_level_{i}": n for i, n in enumerate(per_level, start=1)},
        "too_broad": too_broad,
    }


def _tree_levels(doc: Mapping[str, Any]) -> list[int]:
    """The level of every node of a tree document."""
    parent = {n["id"]: n.get("parent") for n in doc.get("nodes", [])}
    out = []
    for nid in parent:
        level, p = 1, parent[nid]
        while p is not None:
            level, p = level + 1, parent[p]
        out.append(level)
    return out


def _apply(ctx: StageContext, rctx: RunContext) -> dict[str, int]:
    """The engine's two-level apply (depth 2): the applied document and the two-level weights."""
    from ..lexicon.subfields import apply_subfields

    returned = _engine_call(ctx, lambda: apply_subfields(rctx))
    written = json.loads(rctx.paths.subfields_json.read_text(encoding="utf-8"))
    return {
        "themes": len(written.get("subfields", [])),
        "topics": len(written.get("concepts", [])),
        "applied_matches_file": int(returned == written),
    }


def _person_ids(ctx: StageContext) -> dict[str, str]:
    """The engine's researcher id → the project's ``person_id``, from the corpus slots' people."""
    return person_ids(ctx.folder("corpus.assemble"), [slot.id for slot in ctx.project.config.slots])


def person_ids(corpus: Path, slots: list[str]) -> dict[str, str]:
    """The engine's researcher id → the project's ``person_id``, from *corpus*'s slots' people."""
    from ..lexicon.utils import make_researcher_id

    out: dict[str, str] = {}
    for slot in slots:
        people = corpus / slot / "people.csv"
        if not people.exists():
            continue
        with open(people, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                rid = make_researcher_id(r["last_name"], r["first_name"], r["unit"])
                out.setdefault(rid, r["person_id"])
    return out


def _apply_tree(ctx: StageContext, rctx: RunContext, *, tables: bool = True) -> dict[str, int]:
    """The theme tree applied at any depth: the applied tree and its tables."""
    from ..lexicon.theme_tree import apply_themes

    person_ids = _person_ids(ctx) if tables else None
    applied = _engine_call(ctx, lambda: apply_themes(rctx, tables=tables, person_ids=person_ids))
    levels = [n["level"] for n in applied.nodes]
    depth = applied.tree.depth
    return {
        "depth": depth,
        "themes": levels.count(1),
        "topics": levels.count(depth),
        "people_counted": applied.people_counted,
    }


def run_apply(ctx: StageContext) -> dict[str, int]:
    """``themes.apply``: the curated theme tree when there is one, else the proposal, at any depth.

    At depth 2 it also writes the engine's two-level documents (the curated
    document for a curated tree, the applied document and the two-level
    weights), as they always were.
    """
    counts: dict[str, int] = {}
    tree = None
    if ctx.layout.themes_json.exists():
        counts["curated"] = 1
        tree = _checked_tree(ctx)
    rctx = run_context(ctx, _settings(ctx), hi=0.6)
    counts.update(_apply_tree(ctx, rctx))
    two_levels = tree.depth == 2 if tree is not None else rctx.paths.subfields_draft_json.exists()
    if two_levels:
        if tree is not None:
            _curated_from_themes(ctx, tree)
        rctx = rctx.replace(progress=_progress_bridge(ctx, 0.6, 1.0))
        counts.update(_apply(ctx, rctx))
    return counts


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

    A new keyword is proposed a node by :func:`proposed_places` (the node of
    the curated tree that holds most of its group in the grouping's proposal),
    else set aside; either way it is marked « to check ». The rebase is saved
    as a new version of the tree (its reconciliation is in the description).
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
    draft_path = project.layout.stage("themes.group") / "themes_draft.json"
    proposal = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path.exists() else {}
    known = vocabulary_of(tree)
    proposals = proposed_places(tree, proposal, [t for t in terms if t not in known])
    record = read_record(project.layout, "themes.space")
    rebased = rebase(
        tree, terms, proposals, run=f"themes.space/{record.run_id}" if record else None
    )
    saved = save_themes(project, rebased.tree, expected=fp, action="rebase onto the new vocabulary")
    return [f"theme tree rebased: {rebased.description}"] if saved.written else []


def proposed_places(
    tree: ThemesFile, proposal: Mapping[str, Any], new: list[str]
) -> dict[str, str | None]:
    """A node of *tree* for each *new* keyword, from its group in the grouping's *proposal*.

    The keyword's group is its node in *proposal* (a ``cartolex-themes/1`` tree,
    the grouping's finest level). The other keywords of that group that *tree*
    places vote, level by level from the deepest up: each votes for the node of
    that level above its own node (or its node itself). The first node that
    gets a strict majority of the votes of the whole group is proposed. When no
    level gives one, or the group has no keyword *tree* places, the keyword gets
    ``None`` (it is set aside, « to check »).
    """
    group_of = dict(proposal.get("keywords") or {})
    members: dict[str, list[str]] = {}
    for keyword, group in group_of.items():
        members.setdefault(group, []).append(keyword)
    parent = {n.id: n.parent for n in tree.nodes}
    level: dict[str, int] = {}

    def level_of(node: str) -> int:
        if node not in level:
            up = parent[node]
            level[node] = 1 if up is None else level_of(up) + 1
        return level[node]

    for n in tree.nodes:
        level_of(n.id)

    def ancestor(node: str, at: int) -> str:
        while level[node] > at:
            node = parent[node]  # type: ignore[assignment]
        return node

    out: dict[str, str | None] = {}
    for keyword in new:
        group = group_of.get(keyword)
        voters = [
            tree.keywords[k]
            for k in members.get(group, [])  # type: ignore[arg-type]
            if k != keyword and k in tree.keywords
        ]
        out[keyword] = None
        for at in range(tree.depth, 0, -1):
            votes: dict[str, int] = {}
            for node in voters:
                if level[node] >= at:
                    top = ancestor(node, at)
                    votes[top] = votes.get(top, 0) + 1
            winner = next((n for n, v in votes.items() if 2 * v > len(voters)), None)
            if winner is not None:
                out[keyword] = winner
                break
    return out


def _checked_tree(ctx: StageContext) -> ThemesFile:
    """``decisions/themes.json``, refused unless rebased on the current vocabulary."""
    from ..project.themes import vocabulary_fingerprint
    from ..project.themes_versions import read_themes

    tree, _ = read_themes(ctx.project)
    assert tree is not None
    terms, _ = _vocabulary(ctx.folder("themes.space"))
    if tree.based_on.vocabulary != vocabulary_fingerprint(terms):
        raise StageRefused(
            "decisions/themes.json is not based on the current vocabulary (its rebase failed)"
        )
    return tree


def _curated_from_themes(ctx: StageContext, tree: ThemesFile) -> None:
    """Write the engine's two-level curated document (``curated.json``) of a depth-2 tree."""
    from ..project.files import atomic_write_bytes, json_bytes
    from ..project.themes_curated import to_curated

    terms, scores = _vocabulary(ctx.folder("themes.space"))
    config = ctx.project.config
    doc = to_curated(
        tree,
        terms,
        reference_language=config.languages.reference,
        domain_title=config.identity.domain_title,
        scores=scores,
    )
    atomic_write_bytes(ctx.out / "curated.json", json_bytes(doc))


#: Keys of a UMAP map version's layout parameters → the layout stage's arguments.
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

#: The layout methods a map version may name, each with its parameters (key →
#: the layout stage's argument) and the engine's layout it runs.
LAYOUT_METHODS: dict[str, tuple[dict[str, str], str | None]] = {
    "umap": (LAYOUT_PARAMS, None),
    "tsne": ({"perplexity": "tsne_perplexity", "metric": "umap_metric"}, "tsne"),
    "tree": (
        {"fill": "tree_fill", "gap": "tree_gap", "lean": "tree_lean", "sharp": "tree_sharp"},
        "tree",
    ),
}


#: From this many mapped people, the first map version is a t-SNE (when openTSNE is
#: installed): it keeps people's neighbourhoods better than UMAP on the measured worlds
#: of 10³ to 10⁵ people (``docs/dev/layouts.md``). Both reference worlds stay below it.
TSNE_FROM_PEOPLE = 1_000


def default_layout_method(mapped_units: int | None, *, tsne: bool | None = None) -> str:
    """The layout method of a project's first map version, by the rule of its size.

    ``tsne`` from :data:`TSNE_FROM_PEOPLE` mapped people when the optional
    openTSNE package is installed (*tsne*, default: whether it is), ``umap``
    otherwise.
    """
    if tsne is None:
        from ..atlas.reducers import opentsne_available

        tsne = opentsne_available()
    if tsne and mapped_units is not None and mapped_units >= TSNE_FROM_PEOPLE:
        return "tsne"
    return "umap"


def prepare_maps(project: Project) -> list[str]:
    """Before the first layout: add and pin map version ``v1`` (seed: ``params.json``'s).

    Its method follows :func:`default_layout_method` on the mapped people the
    corpus stage counted.
    """
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
    record = project.layout.run_json("corpus.assemble")
    mapped = None
    if record.exists():
        mapped = json.loads(record.read_text(encoding="utf-8")).get("measures", {})
        mapped = (mapped.get("counts") or {}).get("mapped_units")
    method = default_layout_method(mapped)
    note = "the first map, from the defaults"
    if method != "umap":
        note += f" ({method}: {mapped} mapped people)"
    maps, version = add_version(maps, method=method, seed=params.seed, note=note)
    save_maps(project.layout, maps, expected=fp, action="first map version")
    return [f"added and pinned map version {version} ({method} layout)"]


def _layout_kwargs(version: Any) -> dict[str, Any]:
    """The layout stage's arguments of a map version (refused when it cannot be drawn)."""
    method = LAYOUT_METHODS.get(version.layout.method)
    if method is None:
        raise StageRefused(
            f"the layout method {version.layout.method!r} is not available; "
            f"known: {sorted(LAYOUT_METHODS)}"
        )
    known, engine_layout = method
    unknown = sorted(set(version.layout.params) - set(known))
    if unknown:
        raise StageRefused(
            f"map version {version.id}: unknown {version.layout.method} layout parameter(s) "
            f"{unknown}; known: {sorted(known)}"
        )
    kwargs = {known[k]: v for k, v in version.layout.params.items()}
    if engine_layout is not None:
        kwargs["umap_layout"] = engine_layout
    if engine_layout == "tsne":
        from ..atlas.reducers import opentsne_available

        if not opentsne_available():
            raise StageRefused(
                "the tsne layout needs the optional openTSNE package: pip install 'cartolex[tsne]'"
            )
    if version.layout.dimensions == 3:
        from ..atlas.reducers import umap_available

        if not umap_available():
            raise StageRefused(f"map version {version.id}: a map in space needs umap-learn")
    return kwargs


#: The folder of a map version other than the pinned one, in each stage that places on it.
VERSIONS_DIR = "versions"


def version_folder(stage_folder: Path, version_id: str, pinned_id: str | None) -> Path:
    """Where a stage keeps what it placed on map version *version_id*: its own folder for
    the pinned version, ``versions/<id>/`` in it for another built one."""
    return stage_folder if version_id == pinned_id else stage_folder / VERSIONS_DIR / version_id


def _diagnosed(path: Path) -> float | None:
    """The trustworthiness a layout's diagnostics measured (``None``: not measured)."""
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("trustworthiness")
    except (OSError, ValueError, AttributeError):
        return None
    return round(float(value), 6) if isinstance(value, int | float) else None


def run_layout(ctx: StageContext) -> dict[str, int]:
    """``map.layout``: the map of the pinned version, then the themes placed on it; then each
    other built version's map, in ``versions/<id>/``, with the themes placed on it."""
    import time

    from ..atlas import driver
    from ..lexicon.theme_tree import apply_themes
    from ..project.maps import built_versions, read_maps

    maps, _ = read_maps(ctx.layout)
    versions = built_versions(maps)
    if not versions:
        raise StageRefused("no pinned map version in decisions/maps.json")
    version = versions[0]
    kwargs_of = {v.id: _layout_kwargs(v) for v in versions}
    copy_amended(ctx.stage.id, _folders(ctx))
    share = 1.0 / len(versions)  # of the progress, per version
    rctx = run_context(ctx, _settings(ctx), hi=0.9 * share)
    placement = _placement(ctx)
    kwargs = {**kwargs_of[version.id], **placement}
    measured = []
    t0 = time.monotonic()
    _engine_call(
        ctx,
        lambda: driver.run_umap(
            rctx,
            umap_random_state=version.layout.seed,
            n_components=version.layout.dimensions,
            **kwargs,
        ),
    )
    rctx = rctx.replace(progress=_progress_bridge(ctx, 0.9 * share, share))
    counts = _apply_tree(ctx, rctx, tables=False)
    if rctx.paths.subfields_json.exists():  # the apply stage wrote the two-level documents
        counts.update(_apply(ctx, rctx))
    measured.append((version, time.monotonic() - t0, rctx.paths.layout_diagnostics_json))
    for k, other in enumerate(versions[1:], start=1):
        ctx.check_cancel()
        out = version_folder(ctx.out, other.id, version.id)
        lo = k * share
        vctx = rctx.replace(progress=_progress_bridge(ctx, lo, lo + 0.9 * share))
        okw = {**kwargs_of[other.id], **placement}
        t0 = time.monotonic()
        emb = _engine_call(
            ctx,
            lambda vctx=vctx, other=other, out=out, okw=okw: driver.run_umap(
                vctx,
                umap_random_state=other.layout.seed,
                n_components=other.layout.dimensions,
                out_dir=out,
                **okw,
            ),
        )
        _engine_call(
            ctx,
            lambda vctx=vctx, emb=emb, out=out: apply_themes(
                vctx, tables=False, person_xy=emb.umap_ind, applied_out=out / "themes_applied.json"
            ),
        )
        measured.append((other, time.monotonic() - t0, out / "umap_diagnostics.json"))
    ctx.measures["versions"] = [
        {
            "id": v.id,
            "dimensions": v.layout.dimensions,
            "method": v.layout.method,
            "seconds": round(seconds, 3),
            "trustworthiness": _diagnosed(diagnostics),
        }
        for v, seconds, diagnostics in measured
    ]
    return {"version": int(version.id[1:]) if version.id[1:].isdigit() else 0, **counts}


def _placement(ctx: StageContext) -> dict[str, Any]:
    """How points are placed on the map: ``map.layout``'s ``neighbours`` and ``link_radius``
    (this run's, or those its results were made with; left out when not recorded)."""
    out = {}
    for name in ("neighbours", "link_radius"):
        value = _param(ctx, "map.layout", name, None)
        if value is not None:
            out[name] = value
    return out


def built_others(layout_folder: Path) -> list[str]:
    """The map versions other than the pinned one that a run of ``map.layout`` built (in
    *layout_folder*, its results), by id."""
    folder = layout_folder / VERSIONS_DIR
    if not folder.is_dir():
        return []
    return sorted(
        p.name for p in folder.iterdir() if p.is_dir() and (p / "umap_individuals.csv").is_file()
    )


def version_xy(folder: Path) -> Any:
    """The people's places on a map version (its ``umap_individuals.csv`` in *folder*), as
    an ``(n, d)`` array in the rows of the stored embeddings."""
    import numpy as np
    import pandas as pd

    table = pd.read_csv(folder / "umap_individuals.csv", usecols=lambda c: c.startswith("umap_"))
    axes = [c for c in ("umap_x", "umap_y", "umap_z") if c in table.columns]
    return table[axes].to_numpy(dtype=np.float64)


def _other_maps(ctx: StageContext) -> list[tuple[str, Any, Path]]:
    """The other built map versions: id, people's places, and this stage's folder for it."""
    layout_folder = ctx.folder("map.layout")
    return [
        (vid, version_xy(layout_folder / VERSIONS_DIR / vid), ctx.out / VERSIONS_DIR / vid)
        for vid in built_others(layout_folder)
    ]


def run_trajectories(ctx: StageContext) -> dict[str, int]:
    """``map.trajectories``: positions per person and time window (on the pinned map, and on
    each other built version, in ``versions/<id>/``)."""
    from ..atlas import driver

    others = _other_maps(ctx)
    rctx = run_context(ctx, _settings(ctx))
    _engine_call(
        ctx,
        lambda: driver.run_trajectories(
            rctx,
            bin_years=ctx.params["window_years"],
            min_docs_per_bin=ctx.params.get("min_texts_per_window"),
            length_alpha=rctx.settings.length_bonus_alpha,
            all_spans=ctx.params.get("spans") == "all",
            versions=[(xy, out) for _, xy, out in others],
            **_placement(ctx),
        ),
    )
    if not rctx.paths.trajectories_csv.exists():
        raise RuntimeError("the trajectories were not computed (see the log for why)")
    return {"points": _rows(rctx.paths.trajectories_csv)}


def run_overlays(ctx: StageContext) -> dict[str, int]:
    """``overlays.position``: each projected set placed on the finished map.

    Writes ``<set>/positions.json`` per set (and, for each other built map version,
    ``versions/<id>/<set>/positions.json`` with each person's place on it): each person's place in the space
    and on the map, keywords, nearest keywords, and their weights on every
    level of the theme tree (``levels``: from the terms that place them, as a
    mapped person's). At depth 2 each person also keeps the two-level
    ``themes`` and ``topics`` weights (by proximity to their seed keywords).
    """
    import numpy as np
    import pandas as pd

    from ..atlas.model_files import load_embeddings
    from ..lexicon.corpus_store import load_corpus
    from ..lexicon.positioning import (
        concept_svd_centroids,
        load_positioning_models,
        project_text_vector,
        scored_top_terms_for_vector,
        subfield_svd_centroids,
        subfield_weights_for_vector,
    )
    from ..lexicon.theme_tree import read_tree
    from ..project.files import atomic_write_bytes, json_bytes

    rctx = run_context(ctx, _settings(ctx))
    tfidf, restricted_terms, svd, anchors = _engine_call(
        ctx, lambda: load_positioning_models(rctx, **_placement(ctx))
    )
    aliases = pd.read_csv(rctx.paths.term_aliases_csv, dtype=str, keep_default_na=False)
    alias_map = dict(zip(aliases["alias"], aliases["canonical"], strict=True))
    emb = load_embeddings(rctx.paths.embeddings_json)
    two_levels = rctx.paths.subfields_json.exists()
    sf_centroids: dict = {}
    c_centroids: dict = {}
    if two_levels:
        applied = json.loads(rctx.paths.subfields_json.read_text(encoding="utf-8"))
        sf_centroids = subfield_svd_centroids(
            applied.get("subfields", []), emb.Z_terms, restricted_terms
        )
        c_centroids = concept_svd_centroids(
            applied.get("concepts", []), emb.Z_terms, restricted_terms
        )
    tree = (
        read_tree(rctx.paths.themes_tree_json, restricted_terms)
        if rctx.paths.themes_tree_json.exists()
        else None
    )
    corpus = ctx.folder("corpus.assemble") / "overlays"
    feature_names = tfidf.get_feature_names_out()
    other_anchors = []
    if anchors is not None:
        from ..atlas.placement import MapAnchors

        other_anchors = [
            (vid, MapAnchors(anchors.unit, xy, k=anchors.k, link_radius=anchors.link_radius,
                             normalised=True))
            for vid, xy, _ in _other_maps(ctx)
        ]  # fmt: skip
    placed = 0
    sets = ctx.project.config.overlays
    for n_set, overlay in enumerate(sets):
        folder = corpus / overlay.id
        if not (folder / "people.csv").exists():
            continue
        with open(folder / "people.csv", encoding="utf-8", newline="") as fh:
            who = {
                (r["last_name"], r["first_name"], r["unit"]): r["person_id"]
                for r in csv.DictReader(fh)
            }
        found = load_corpus([(overlay.id, folder / "index.csv", None)], doc_types=False)
        read = dict(found.texts())
        texts: dict[tuple[str, str, str], list[str]] = {}
        for i, t in zip(found.person.tolist(), found.text.tolist(), strict=True):
            person = found.people[i]
            key = (person.last_name, person.first_name, person.raw_unit)
            texts.setdefault(key, []).append(read[t])
        items = []
        for i, key in enumerate(sorted(texts, key=lambda k: who.get(k, ""))):
            ctx.check_cancel()
            ctx.progress((n_set + i / max(1, len(texts))) / max(1, len(sets)), overlay.id)
            z, top, x = project_text_vector(
                "\n\n".join(texts[key]),
                tfidf=tfidf,
                restricted_terms=restricted_terms,
                svd=svd,
                alias_map=alias_map,
                length_bonus_alpha=rctx.settings.length_bonus_alpha,
                top_k=10,
                top_n=rctx.settings.top_n_researcher,
                feature_names=feature_names,
            )
            item: dict[str, Any] = {
                "person_id": who.get(key, ""),
                "z": [float(v) for v in np.asarray(z).ravel()],
                "keywords": [{"term": t["term"], "score": float(t["score"])} for t in top],
                "near_terms": scored_top_terms_for_vector(z, emb.Z_terms, restricted_terms, k=10),
            }
            if two_levels:
                item["themes"] = subfield_weights_for_vector(z, sf_centroids)
                item["topics"] = subfield_weights_for_vector(z, c_centroids)
            if tree is not None:
                cols = np.flatnonzero(np.asarray(x) > 0)
                item["levels"] = [
                    {
                        "level": lw.level,
                        "nodes": [
                            {"id": tree.nodes[n].id, "weight": float(w), "share": float(s)}
                            for n, w, s in zip(
                                lw.nodes.tolist(), lw.weights, lw.shares, strict=True
                            )
                        ],
                    }
                    for lw in tree.describe(cols, np.asarray(x)[cols])
                ]
            items.append(item)
        if items and anchors is not None:
            vectors = np.vstack([np.asarray(it["z"]) for it in items])
            xy = anchors.place(vectors)
            for it, at in zip(items, xy, strict=True):
                it.update(_place_of(at))
            # each other built map version: the same people, their places only
            for vid, other in other_anchors:
                where = other.place(vectors)
                doc = {
                    "format": "cartolex-positions/1",
                    "set": overlay.id,
                    "version": vid,
                    "items": [
                        {"person_id": it["person_id"], **_place_of(at)}
                        for it, at in zip(items, where, strict=True)
                    ],
                }
                atomic_write_bytes(
                    ctx.out / VERSIONS_DIR / vid / overlay.id / "positions.json", json_bytes(doc)
                )
        atomic_write_bytes(
            ctx.out / overlay.id / "positions.json",
            json_bytes({"format": "cartolex-positions/1", "set": overlay.id, "items": items}),
        )
        placed += len(items)
    return {"placed": placed}


def _place_of(at: Any) -> dict[str, float]:
    """A placed point's ``x``, ``y`` (and ``z`` on a map in space)."""
    return {axis: float(v) for axis, v in zip(("x", "y", "z"), at, strict=False)}


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


def _with_options(
    run: Callable[[StageContext], Mapping[str, int] | None], options: EngineOptions
) -> Callable[[StageContext], Mapping[str, int] | None]:
    def runner(ctx: StageContext) -> Mapping[str, int] | None:
        token = _OPTIONS.set(options)
        try:
            return run(ctx)
        finally:
            _OPTIONS.reset(token)

    runner.__name__ = runner.__qualname__ = getattr(run, "__name__", "runner")
    return runner


def engine_registry(
    ai: AIAccess | Callable[[], AIAccess | None] | None = None,
    options: EngineOptions | None = None,
) -> Registry:
    """cartolex's stages with their runners; the AI clean-up reaches its provider through *ai*.

    *options* (a host's prompts and function words) reach every stage.
    """
    from .stages import STAGES

    registry = STAGES.with_runners({"keywords.triage": triage_runner(ai)})
    if options is None or options == EngineOptions():
        return registry
    return registry.with_runners({s.id: _with_options(s.run, options) for s in registry})
