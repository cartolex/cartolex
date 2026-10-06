# SPDX-License-Identifier: MIT
"""The explicit context of one engine run.

:class:`EnginePaths` names every file and folder the engine reads or writes.
:class:`RunContext` carries those paths with everything else a stage needs:
the settings, the stop-word profile, the prompt directory, the staleness
hooks, the current year, the thread limits and the AI usage recorder. Every
stage entry point takes a :class:`RunContext`; no stage joins
path parts itself, and nothing here touches the file system before a stage
runs (building a context reads the project's override file, nothing else).
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from importlib.abc import Traversable

    from cartolex.lexicon.config import KeywordsConfig
    from cartolex.lexicon.llm_usage import UsageRecorder
    from cartolex.lexicon.stopwords_config import StopwordProfile

    #: A folder of prompt templates: a directory on disk or packaged resources.
    PromptDir = Path | Traversable

__all__ = ["EnginePaths", "PathPattern", "RunCancelled", "RunContext", "ThreadLimits"]


class RunCancelled(Exception):
    """The run was stopped through its context's ``cancel`` callable."""


@dataclass(frozen=True)
class PathPattern:
    """A family of files in one folder, told apart by one key (a language, a group, a slot).

    ``PathPattern(folder, "raw_{}.csv")("fr")`` is ``folder / "raw_fr.csv"``.
    """

    folder: Path
    name: str  # holds one ``{}`` placeholder

    def __call__(self, key: str) -> Path:
        """The file of *key*."""
        return self.folder / self.name.format(key)


@dataclass(frozen=True)
class EnginePaths:
    """Every file and folder the engine reads or writes for one project.

    A constructor knows a layout; today there is one, :meth:`for_workspace`,
    the workspace layout of the 0.x line. Stages ask this object for their
    paths and never build one, so another layout only needs another
    constructor.
    """

    # ── folders ──
    root: Path
    automatic_dir: Path  # generated data
    manual_dir: Path  # operator-edited inputs
    config_dir: Path  # project configuration
    atlas_dir: Path  # atlas outputs (tables and figures)
    models_dir: Path  # fitted models and the atlas vocabulary

    # ── the corpus contract ──
    #: The index CSV of a corpus slot, by the slot's id (see ``KeywordsConfig.corpus_slots``).
    corpus_index_csv: PathPattern
    #: The folder of a corpus slot's text files, by the slot's id (for corpus producers;
    #: the engine reads each document from its index's ``txt_path``).
    corpus_text_dir: PathPattern

    # ── operator inputs ──
    overrides_json: Path  # the domain title and the stop-word additions and removals
    overrides_template_json: Path  # fallback for the domain title
    manual_blacklist_csv: Path
    manual_keep_csv: Path
    #: The terms the copilot's acceptance gate lets in (one per line, ``term`` header);
    #: absent, the bands decide (see cartolex.lexicon.consolidation).
    accepted_csv: Path
    canonical_decisions_json: Path
    whitelist_json: Path
    person_whitelist_csv: Path
    api_key_json: Path
    triage_prompt_override_txt: Path
    subfields_curated_json: Path
    #: The curated theme tree (``cartolex-themes/1``, the project's ``decisions/themes.json``).
    themes_json: Path

    # ── extraction, triage and consolidation ──
    #: Folder of the parse cache: the analysed texts of every corpus language,
    #: kept between runs (see cartolex.lexicon.parse_cache).
    parse_cache_dir: Path
    raw_terms_csv: PathPattern  # per corpus language
    global_terms_csv: Path
    #: The rejection snapshot (``cartolex-rejects/1``, see cartolex.lexicon.rejects): the
    #: candidates the extraction sets in the ``rejected`` band; absent, none.
    rejects_json: Path
    #: The categories of the keywords (lower-case term → category), from the AI's answers
    #: and the decisions (see cartolex.lexicon.categories); absent, none known.
    keyword_categories_json: Path
    #: Who uses each candidate (person indices of the extraction), for the copilot's bundle.
    term_people_npz: Path
    refined_terms_csv: Path
    refined_terms_lang_csv: PathPattern  # per display language
    refined_pairs_csv: Path
    run_settings_json: Path
    person_terms_csv: Path
    #: Each person's keywords as the people × keywords matrices the space reads (a model
    #: descriptor; see cartolex.atlas.model_files.save_person_terms).
    person_terms_json: Path
    group_terms_csv: Path
    domain_terms_csv: Path
    canonical_map_json: Path
    translation_cache_json: Path
    triage_decisions_json: Path
    triage_batch_cache_json: Path
    triage_term_cache_json: Path
    ai_usage_json: Path
    roster_csv: Path
    vectorizer_json: Path  # a model descriptor (see cartolex.atlas.model_files)
    term_aliases_csv: Path

    # ── the atlas ──
    atlas_terms_csv: Path
    lexical_data_json: Path  # model descriptors, each with its .npz array file
    embeddings_json: Path
    svd_model_json: Path
    atlas_params_json: Path  # optional frozen atlas parameters
    pca_persons_csv: Path
    pca_terms_csv: Path
    layout_persons_csv: Path
    layout_terms_csv: Path
    layout_groups_csv: Path
    layout_diagnostics_json: Path
    clusters_csv: Path
    terms_clustered_csv: Path
    proto_subfields_json: Path
    trajectories_csv: Path
    trajectory_windows_json: Path
    persons_groups_png: Path
    term_clusters_png: Path
    superposed_png: Path
    cohort_trajectories_png: Path
    group_panel_png: PathPattern  # per group
    #: Each time window's weights on every level of the theme tree.
    trajectory_themes_parquet: Path

    # ── the theme tree, at any depth ──
    themes_draft_json: Path  # the grouping's proposal tree
    text_keywords_npz: Path  # the texts × keywords the comb read, beside the proposal
    themes_tree_json: Path  # the tree the apply stage applied, as it read it
    themes_applied_json: Path  # the applied tree: nodes, weights, colours, positions
    theme_keywords_csv: Path
    theme_people_parquet: Path
    theme_organisations_parquet: Path

    # ── subfields (the two-level documents, written at depth 2) ──
    subfields_draft_json: Path
    subfields_json: Path
    subfield_weights_csv: Path
    lexicon_weights_csv: Path

    @classmethod
    def for_workspace(cls, base: str | Path) -> EnginePaths:
        """The paths of a workspace folder (the layout of the 0.x line)."""
        root = Path(base)
        auto = root / "automatic_data"
        manual = root / "manual_data"
        config = root / "config"
        atlas = root / "lexical_analysis"
        models = atlas / "models"
        return cls(
            root=root,
            automatic_dir=auto,
            manual_dir=manual,
            config_dir=config,
            atlas_dir=atlas,
            models_dir=models,
            corpus_index_csv=PathPattern(root, "{}_index.csv"),
            corpus_text_dir=PathPattern(auto, "corpus_{}"),
            overrides_json=manual / "overrides.json",
            overrides_template_json=config / "overrides_template.json",
            manual_blacklist_csv=manual / "manual_blacklist.csv",
            manual_keep_csv=manual / "manual_keep.csv",
            accepted_csv=manual / "manual_accepted.csv",
            canonical_decisions_json=manual / "canonical_decisions.json",
            whitelist_json=manual / "whitelist.json",
            person_whitelist_csv=manual / "person_whitelist.csv",
            api_key_json=manual / "llm_api.json",
            triage_prompt_override_txt=manual / "llm_prompts" / "triage_typed.txt",
            subfields_curated_json=manual / "subfields.json",
            themes_json=manual / "themes.json",
            parse_cache_dir=auto / "parse_cache",
            raw_terms_csv=PathPattern(auto, "raw_keywords_{}.csv"),
            global_terms_csv=auto / "keywords_global.csv",
            rejects_json=auto / "rejects.json",
            keyword_categories_json=auto / "keyword_categories.json",
            term_people_npz=auto / "term_people.npz",
            refined_terms_csv=auto / "keywords_global_refined.csv",
            refined_terms_lang_csv=PathPattern(auto, "keywords_global_refined_{}.csv"),
            refined_pairs_csv=auto / "keywords_global_refined_pairs.csv",
            run_settings_json=auto / "keywords_hyperparams.json",
            person_terms_csv=auto / "keywords_by_researcher_restricted.csv",
            person_terms_json=models / "person_terms.json",
            group_terms_csv=auto / "keywords_by_unit_restricted.csv",
            domain_terms_csv=auto / "keywords_domain_restricted.csv",
            canonical_map_json=auto / "canonical_map.json",
            translation_cache_json=auto / "translation_cache.json",
            triage_decisions_json=auto / "llm_decisions.json",
            triage_batch_cache_json=auto / "llm_cache.json",
            triage_term_cache_json=auto / "llm_term_cache.json",
            ai_usage_json=auto / "llm_usage.json",
            roster_csv=root / "researcher_index.csv",
            vectorizer_json=models / "tfidf_restricted.json",
            term_aliases_csv=models / "term_aliases.csv",
            atlas_terms_csv=models / "restricted_terms.csv",
            lexical_data_json=models / "lexical_data.json",
            embeddings_json=models / "embeddings.json",
            svd_model_json=models / "svd.json",
            atlas_params_json=models / "umap_params.json",
            pca_persons_csv=atlas / "pca_individuals.csv",
            pca_terms_csv=atlas / "pca_terms.csv",
            layout_persons_csv=atlas / "umap_individuals.csv",
            layout_terms_csv=atlas / "umap_terms.csv",
            layout_groups_csv=atlas / "umap_labs.csv",
            layout_diagnostics_json=atlas / "umap_diagnostics.json",
            clusters_csv=atlas / "clusters_terms.csv",
            terms_clustered_csv=atlas / "umap_terms_clustered.csv",
            proto_subfields_json=atlas / "proto_subfields.json",
            trajectories_csv=atlas / "umap_trajectories.csv",
            trajectory_windows_json=atlas / "trajectory_windows.json",
            persons_groups_png=atlas / "umap_individuals_labs.png",
            term_clusters_png=atlas / "umap_terms_clusters.png",
            superposed_png=atlas / "umap_superposed_all.png",
            cohort_trajectories_png=atlas / "umap_cohort_trajectories.png",
            group_panel_png=PathPattern(atlas, "umap_lab_{}.png"),
            trajectory_themes_parquet=atlas / "trajectory_themes.parquet",
            themes_draft_json=auto / "themes_draft.json",
            text_keywords_npz=auto / "text_keywords.npz",
            themes_tree_json=auto / "themes_tree.json",
            themes_applied_json=auto / "themes_applied.json",
            theme_keywords_csv=auto / "theme_keywords.csv",
            theme_people_parquet=auto / "theme_people.parquet",
            theme_organisations_parquet=auto / "theme_organisations.parquet",
            subfields_draft_json=auto / "subfields_draft.json",
            subfields_json=auto / "subfields.json",
            subfield_weights_csv=auto / "subfield_weights.csv",
            lexicon_weights_csv=auto / "lexicon_weights.csv",
        )


@dataclass(frozen=True)
class ThreadLimits:
    """How much parallelism one run may use; ``None`` keeps the libraries' own default.

    ``numeric`` caps the threads of the numeric libraries (BLAS, OpenMP) while
    a stage runs; the SVD fits stay single-threaded regardless, for
    reproducibility. ``processes`` caps the worker processes of the parallel
    steps (the per-language split of the extraction), on top of the settings'
    own ``extraction_n_jobs``.
    """

    numeric: int | None = None
    processes: int | None = None
    #: The memory the run may use in all, its workers included, in MB (``None``: no cap).
    memory_mb: int | None = None

    @contextlib.contextmanager
    def applied(self) -> Iterator[None]:
        """Apply the numeric thread cap for the duration of a ``with`` block."""
        if self.numeric is None:
            yield
            return
        from threadpoolctl import threadpool_limits

        with threadpool_limits(limits=int(self.numeric)):
            yield

    def workers(self, wanted: int) -> int:
        """The number of worker processes to use when a step asks for *wanted*."""
        if self.processes is None:
            return wanted
        return max(1, min(int(wanted), int(self.processes)))

    def workers_within(self, wanted: int, worker_mb: float, parent_mb: float | None = None) -> int:
        """:meth:`workers`, and no more than the memory holds, each worker taking
        *worker_mb* beside a parent of *parent_mb* (by default: what this process holds
        now and a quarter more, at least :data:`PARENT_MB`)."""
        n = self.workers(wanted)
        if self.memory_mb is None:
            return n
        if parent_mb is None:
            from cartolex.scale import resident_mb

            held = resident_mb()
            parent_mb = max(PARENT_MB, 1.25 * held) if held is not None else PARENT_MB
        return max(1, min(n, int((self.memory_mb - parent_mb) // max(worker_mb, 1.0))))


#: The least memory a pool leaves to its parent process, in MB.
PARENT_MB = 2000.0


def _packaged_stopwords() -> StopwordProfile:
    from cartolex.lexicon.stopwords_config import StopwordProfile

    return StopwordProfile.default()


def _packaged_prompts() -> PromptDir:
    from cartolex.lexicon.prompt_store import packaged_prompt_dir

    return packaged_prompt_dir()


def _new_usage_recorder() -> UsageRecorder:
    from cartolex.lexicon.llm_usage import UsageRecorder

    return UsageRecorder()


def _this_year() -> int:
    return datetime.date.today().year


@dataclass(frozen=True)
class RunContext:
    """Everything one engine run reads besides its input files.

    Build it with :meth:`for_workspace`; change a copy with :meth:`replace`.
    Nothing in it is shared between runs unless the caller shares it: two
    contexts never see each other's settings or stop words.

    ``staleness_guard(stage, root, confirmed=force)`` and
    ``staleness_erase(stage, root)`` are called before an atlas stage rewrites
    its outputs, when the guard is set. ``now_year`` is the year every date
    window is computed from. :meth:`replace` copies share the objects they do
    not replace (the usage recorder, for example).

    ``progress(fraction, message)`` receives the stages' progress (0 to 1 within
    the running stage) and ``cancel()`` is asked between steps and inside the long
    loops: when it returns true, the stage raises :class:`RunCancelled` (the AI
    triage raises its own cancel error). ``ai_client``, when set, builds the AI
    provider's client (called like the provider SDK's client class), so a test
    or a reference run can answer with a model of its own. ``scratch`` is a folder
    on a fast local disk for a stage's temporary files (``None``: the stage's own
    output folder).
    """

    paths: EnginePaths
    settings: KeywordsConfig
    stopwords: StopwordProfile = field(default_factory=_packaged_stopwords)
    prompt_dir: PromptDir = field(default_factory=_packaged_prompts)
    staleness_guard: Callable[..., Any] | None = None
    staleness_erase: Callable[..., Any] | None = None
    now_year: int = field(default_factory=_this_year)
    threads: ThreadLimits = field(default_factory=ThreadLimits)
    usage: UsageRecorder = field(default_factory=_new_usage_recorder)
    progress: Callable[[float, str], Any] | None = None
    cancel: Callable[[], bool] | None = None
    ai_client: Callable[..., Any] | None = None
    scratch: Path | None = None

    @classmethod
    def for_workspace(
        cls,
        base: str | Path,
        settings: KeywordsConfig | None = None,
        **overrides: Any,
    ) -> RunContext:
        """The context of a run on the workspace folder *base*.

        *settings* defaults to ``KeywordsConfig()``. When it names no domain,
        the domain title is read from the workspace's override file (or its
        template), as before. The stop-word profile is the packaged lists with
        the workspace's additions and removals. Any field can be given in
        *overrides* (``now_year=2026``, ``prompt_dir=…``, ``threads=…``).
        """
        from cartolex.lexicon.config import KeywordsConfig, default_domain_title
        from cartolex.lexicon.config_loader import load_overrides
        from cartolex.lexicon.stopwords_config import StopwordProfile

        paths = overrides.pop("paths", None) or EnginePaths.for_workspace(base)
        settings = settings if settings is not None else KeywordsConfig()
        if not settings.domain_title:
            title = default_domain_title((paths.overrides_json, paths.overrides_template_json))
            settings = dataclasses.replace(settings, domain_title=title)
        if "stopwords" not in overrides:
            additions = load_overrides(paths.overrides_json)
            overrides["stopwords"] = StopwordProfile.default().with_overrides(additions)
        return cls(paths=paths, settings=settings, **overrides)

    def report(self, fraction: float, message: str = "") -> None:
        """Report how far the running step is (0 to 1), then stop here if the run was cancelled."""
        if self.progress is not None:
            self.progress(min(1.0, max(0.0, float(fraction))), message)
        self.check_cancel()

    def check_cancel(self) -> None:
        """Raise :class:`RunCancelled` when the run's ``cancel`` callable says so."""
        if self.cancel is not None and self.cancel():
            raise RunCancelled("the run was cancelled")

    def percent_reporter(
        self, callback: Callable[[int, str], Any] | None = None
    ) -> Callable[[int, str], None]:
        """A ``(percent, message)`` callback for a stage's own progress reports.

        It calls *callback* when there is one, then :meth:`report`, so the
        stage's progress reaches the context and a cancel stops the stage at
        its next report.
        """

        def reporter(percent: int, message: str) -> None:
            if callback is not None:
                callback(percent, message)
            self.report(percent / 100.0, message)

        return reporter

    def replace(self, **changes: Any) -> RunContext:
        """A copy of this context with *changes* applied."""
        return dataclasses.replace(self, **changes)

    def enforce_staleness(self, stage: str, *, force: bool) -> None:
        """Run the staleness hooks before *stage* rewrites its outputs (no-op without a guard)."""
        if self.staleness_guard is None:
            return
        self.staleness_guard(stage, self.paths.root, confirmed=force)
        if self.staleness_erase is not None:
            self.staleness_erase(stage, self.paths.root)
