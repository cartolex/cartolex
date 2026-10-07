# SPDX-License-Identifier: MIT
"""Where each engine file lives in a project: the ownership table.

The engine names its files through :class:`cartolex.context.EnginePaths`. In a
project, every stage writes only into its own folder, so each field of
``EnginePaths`` is given a place here (:data:`ENGINE_FILES`):

- :class:`Owned`: a file a stage writes, at a path relative to its folder. A
  later stage may *amend* it (the layout adds its coordinates to the stored
  embeddings, for example): the amending stage starts from a copy in its own
  folder, and the stages after it read the amended copy.
- :class:`FromProject`: a file of the project itself, a decision or a cache,
  relative to the project's root.
- :class:`OwnFolder`: a folder, the running stage's own.
- :class:`NotProvided`: a file of the old workspace layout that a project does
  not provide (its settings come from ``project.json`` or ``params.json``, or
  the file is a figure drawn outside the build). The engine sees no file there.

:func:`engine_paths` builds the ``EnginePaths`` of one stage run from the
table: its own files in its staging folder, every other stage's in that stage's
current results. The engine never learns that a project exists.
"""

from __future__ import annotations

import dataclasses
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..context import EnginePaths, PathPattern

__all__ = [
    "ENGINE_FILES",
    "UNAVAILABLE",
    "VERSION_FILES",
    "FromProject",
    "NotProvided",
    "OwnFolder",
    "Owned",
    "copy_amended",
    "engine_paths",
    "files_of",
    "results_paths",
]

#: The folder, inside a staging folder, where paths the stage cannot see point.
UNAVAILABLE = ".unavailable"


@dataclass(frozen=True)
class Owned:
    """A file written by *stage*, at *rel* in its folder (``{}`` in *rel*: a family of files).

    *model*: a model descriptor with its ``.npz`` array file beside it.
    *amended_by*: later stages that rewrite it in their own folder.
    """

    stage: str
    rel: str
    model: bool = False
    amended_by: tuple[str, ...] = ()

    @property
    def writers(self) -> tuple[str, ...]:
        return (self.stage, *self.amended_by)


@dataclass(frozen=True)
class FromProject:
    """A file of the project: a decision (read) or a cache (read and written)."""

    kind: str  # "decision" or "cache"
    rel: str


@dataclass(frozen=True)
class OwnFolder:
    """A folder field: the running stage's own folder, or *rel* inside it."""

    rel: str = ""


@dataclass(frozen=True)
class NotProvided:
    """A path a project does not provide; the reason says where the information comes from.

    *figure* names a figure file: a build draws no figure, but a view of the
    results (:func:`results_paths`) draws it there, in its scratch folder.
    """

    reason: str
    figure: str | None = None


Place = Owned | FromProject | OwnFolder | NotProvided

_SETTINGS = "a project's settings come from project.json and decisions/params.json"
_FIGURE = "figures are outputs, drawn outside the build"
_OPERATOR = "a workspace's operator file; a project has no such decision yet"

ENGINE_FILES: dict[str, Place] = {
    # ── folders ──
    "root": OwnFolder(),
    "automatic_dir": OwnFolder(),
    "manual_dir": NotProvided("the operator folder of a workspace"),
    "config_dir": NotProvided("the configuration folder of a workspace"),
    "atlas_dir": OwnFolder(),
    "models_dir": OwnFolder("models"),
    # ── the corpus contract ──
    "corpus_index_csv": Owned("corpus.assemble", "{}/index.csv"),
    "corpus_text_dir": Owned("corpus.assemble", "{}/texts"),
    # ── operator inputs ──
    "overrides_json": NotProvided(_SETTINGS + ", and decisions/stopwords.json"),
    "overrides_template_json": NotProvided(_SETTINGS),
    "manual_blacklist_csv": Owned("keywords.build", "decisions/excluded.csv"),
    "manual_keep_csv": Owned("keywords.build", "decisions/kept.csv"),
    "accepted_csv": Owned("keywords.build", "decisions/accepted.csv"),
    "canonical_decisions_json": NotProvided("keyword decisions come from decisions/keywords.csv"),
    "whitelist_json": NotProvided(_OPERATOR),
    "person_whitelist_csv": NotProvided(_OPERATOR),
    "api_key_json": NotProvided("the AI key is given to the build, never stored in a project"),
    "triage_prompt_override_txt": FromProject(
        "decision", "decisions/prompts/triage_typed_system.txt"
    ),
    "subfields_curated_json": Owned("themes.apply", "curated.json"),
    "themes_json": FromProject("decision", "decisions/themes.json"),
    # ── extraction, triage and consolidation ──
    "raw_terms_csv": Owned("keywords.extract", "raw_keywords_{}.csv"),
    "global_terms_csv": Owned("keywords.extract", "keywords_global.csv"),
    "rejects_json": Owned("keywords.extract", "rejects.json"),
    "keyword_categories_json": Owned("keywords.build", "categories.json"),
    "term_people_npz": Owned("keywords.extract", "term_people.npz"),
    "refined_terms_csv": Owned("keywords.build", "keywords_global_refined.csv"),
    "refined_terms_lang_csv": Owned("keywords.build", "keywords_global_refined_{}.csv"),
    "refined_pairs_csv": Owned("keywords.build", "keywords_global_refined_pairs.csv"),
    "concept_terms_csv": Owned("keywords.build", "concept_terms.csv"),
    "run_settings_json": Owned("keywords.build", "keywords_hyperparams.json"),
    "person_terms_csv": Owned("keywords.build", "keywords_by_researcher_restricted.csv"),
    "person_terms_json": Owned("keywords.build", "models/person_terms.json", model=True),
    "group_terms_csv": Owned("keywords.build", "keywords_by_unit_restricted.csv"),
    "domain_terms_csv": Owned("keywords.build", "keywords_domain_restricted.csv"),
    "canonical_map_json": Owned("keywords.build", "decisions/merged.json"),
    "translation_cache_json": Owned("keywords.triage", "translation_cache.json"),
    "triage_decisions_json": Owned("keywords.triage", "llm_decisions.json"),
    "triage_batch_cache_json": FromProject("cache", "cache/ai/triage_batch_cache.json"),
    "triage_term_cache_json": FromProject("cache", "cache/ai/triage_term_cache.json"),
    "ai_usage_json": FromProject("cache", "cache/ai/usage.json"),
    "parse_cache_dir": FromProject("cache", "cache/parse"),
    "roster_csv": Owned("keywords.build", "researcher_index.csv"),
    "vectorizer_json": Owned("keywords.build", "models/tfidf_restricted.json", model=True),
    "term_aliases_csv": Owned("keywords.build", "models/term_aliases.csv"),
    # ── the atlas ──
    "atlas_terms_csv": Owned("themes.space", "models/restricted_terms.csv"),
    "lexical_data_json": Owned("themes.space", "models/lexical_data.json", model=True),
    "embeddings_json": Owned(
        "themes.space", "models/embeddings.json", model=True, amended_by=("map.layout",)
    ),
    "svd_model_json": Owned("themes.space", "models/svd.json", model=True),
    "atlas_params_json": NotProvided(_SETTINGS),
    "pca_persons_csv": Owned("themes.space", "pca_individuals.csv"),
    "pca_terms_csv": Owned("themes.space", "pca_terms.csv"),
    "layout_persons_csv": Owned("map.layout", "umap_individuals.csv"),
    "layout_terms_csv": Owned("map.layout", "umap_terms.csv"),
    "layout_groups_csv": Owned("map.layout", "umap_labs.csv"),
    "layout_diagnostics_json": Owned("map.layout", "umap_diagnostics.json"),
    "clusters_csv": Owned("themes.group", "clusters_terms.csv"),
    "terms_clustered_csv": Owned(
        "themes.group", "umap_terms_clustered.csv", amended_by=("map.layout",)
    ),
    "proto_subfields_json": Owned("themes.group", "proto_subfields.json"),
    "trajectories_csv": Owned("map.trajectories", "umap_trajectories.csv"),
    "trajectory_windows_json": Owned("map.trajectories", "trajectory_windows.json"),
    "trajectory_themes_parquet": Owned("map.trajectories", "trajectory_themes.parquet"),
    "persons_groups_png": NotProvided(_FIGURE, "umap_individuals_labs.png"),
    "term_clusters_png": NotProvided(_FIGURE, "umap_terms_clusters.png"),
    "superposed_png": NotProvided(_FIGURE, "umap_superposed_all.png"),
    "cohort_trajectories_png": NotProvided(_FIGURE, "umap_cohort_trajectories.png"),
    "group_panel_png": NotProvided(_FIGURE, "umap_lab_{}.png"),
    # ── the theme tree ──
    "themes_draft_json": Owned("themes.group", "themes_draft.json"),
    "text_keywords_npz": Owned("themes.group", "text_keywords.npz"),
    "themes_tree_json": Owned("themes.apply", "themes_tree.json"),
    "themes_applied_json": Owned("themes.apply", "themes_applied.json", amended_by=("map.layout",)),
    "theme_keywords_csv": Owned("themes.apply", "theme_keywords.csv"),
    "theme_people_parquet": Owned("themes.apply", "theme_people.parquet"),
    "theme_organisations_parquet": Owned("themes.apply", "theme_organisations.parquet"),
    # ── subfields (the two-level documents, at depth 2) ──
    "subfields_draft_json": Owned("themes.group", "subfields_draft.json"),
    "subfields_json": Owned("themes.apply", "subfields.json", amended_by=("map.layout",)),
    "subfield_weights_csv": Owned(
        "themes.apply", "subfield_weights.csv", amended_by=("map.layout",)
    ),
    "lexicon_weights_csv": Owned("themes.apply", "lexicon_weights.csv", amended_by=("map.layout",)),
}


def _pattern_field(name: str) -> bool:
    return any(
        f.name == name and f.type in ("PathPattern", PathPattern)
        for f in dataclasses.fields(EnginePaths)
    )


def _resolve(
    name: str,
    place: Place,
    stage_id: str,
    folders: Mapping[str, Path],
    project_root: Path,
) -> Path | PathPattern | tuple[Path, ...]:
    own = folders[stage_id]
    pattern = _pattern_field(name)
    if isinstance(place, OwnFolder):
        return own / place.rel if place.rel else own
    if isinstance(place, FromProject):
        return project_root / place.rel
    if isinstance(place, Owned):
        writer = next((w for w in reversed(place.writers) if w in folders), None)
        if stage_id in place.writers:
            writer = stage_id
        base = folders[writer] if writer is not None else own / UNAVAILABLE / name
        rel = place.rel if writer is not None else Path(place.rel).name
        if pattern:
            return PathPattern(base, rel)  # the key may name a folder: "{}/index.csv"
        return base / rel
    # Not provided: a path in the staging folder that nothing writes.
    target = own / UNAVAILABLE / name
    if pattern:
        return PathPattern(target, "{}")
    return target


def engine_paths(stage_id: str, folders: Mapping[str, Path], project_root: Path) -> EnginePaths:
    """The engine's paths for a run of *stage_id*.

    *folders* maps the running stage to its staging folder and every stage it
    may read (its upstream stages, directly or not, that have results) to
    their current results. A file whose writer is not among them points into
    ``<staging>/.unavailable/``, where nothing exists.
    """
    values = {
        name: _resolve(name, place, stage_id, folders, Path(project_root))
        for name, place in ENGINE_FILES.items()
    }
    return EnginePaths(**values)  # type: ignore[arg-type]


def results_paths(derived: Path, scratch: Path, project_root: Path) -> EnginePaths:
    """The engine's paths over a project's current results, to read them.

    Each file is taken from the latest stage that wrote it among those whose
    folder exists when the path is built (else from its first writer); folders,
    and files a project does not provide, point into *scratch* (figures drawn
    from the results go there).
    """
    derived, scratch = Path(derived), Path(scratch)
    present = {
        s: derived / s
        for s in {p.stage for p in ENGINE_FILES.values() if isinstance(p, Owned)}
        | {w for p in ENGINE_FILES.values() if isinstance(p, Owned) for w in p.amended_by}
        if (derived / s).is_dir()
    }
    values: dict[str, object] = {}
    for name, place in ENGINE_FILES.items():
        if isinstance(place, Owned):
            writer = next((w for w in reversed(place.writers) if w in present), place.stage)
            base = derived / writer
            values[name] = (
                PathPattern(base, place.rel) if _pattern_field(name) else base / place.rel
            )
        elif isinstance(place, NotProvided) and place.figure is not None:
            fig = place.figure
            values[name] = PathPattern(scratch, fig) if _pattern_field(name) else scratch / fig
        else:
            values[name] = _resolve(name, place, "_", {"_": scratch}, Path(project_root))
    return EnginePaths(**values)  # type: ignore[arg-type]


def copy_amended(stage_id: str, folders: Mapping[str, Path]) -> list[str]:
    """Copy into the running stage's folder the files it amends, from their latest writer.

    Returns the relative paths copied. A file with no earlier writer available
    is not copied (the stage then writes it from scratch, or fails clearly).
    """
    own = folders[stage_id]
    copied: list[str] = []
    for place in ENGINE_FILES.values():
        if not isinstance(place, Owned) or stage_id not in place.amended_by:
            continue
        earlier = place.writers[: place.writers.index(stage_id)]
        source = next((w for w in reversed(earlier) if w in folders), None)
        if source is None:
            continue
        rels = [place.rel] + ([str(Path(place.rel).with_suffix(".npz"))] if place.model else [])
        for rel in rels:
            src = folders[source] / rel
            if src.exists():
                dst = own / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(rel)
    return copied


#: What a stage writes for each built map version other than the pinned one, in
#: ``versions/<id>/`` of its folder, under the names of the pinned version's files (which
#: stay where they are): only what is placed on the map. ``overlays.position`` writes
#: ``versions/<id>/<set>/positions.json`` beside its sets' own (not engine files).
VERSION_FILES: dict[str, tuple[str, ...]] = {
    "map.layout": (
        "umap_individuals.csv",
        "umap_terms.csv",
        "umap_labs.csv",
        "umap_diagnostics.json",
        "themes_applied.json",
    ),
    "map.trajectories": ("umap_trajectories.csv", "trajectory_windows.json"),
}


def files_of(stage_id: str) -> list[str]:
    """The relative paths (``{}`` for a family) a stage writes, amended copies and the
    other built map versions' files (``versions/{}/…``) included."""
    out: list[str] = []
    for place in ENGINE_FILES.values():
        if isinstance(place, Owned) and stage_id in place.writers:
            out.append(place.rel)
            if place.model:
                out.append(str(Path(place.rel).with_suffix(".npz")))
    out += [f"versions/{{}}/{rel}" for rel in VERSION_FILES.get(stage_id, ())]
    return out
