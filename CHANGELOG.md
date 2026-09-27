# Changelog

This file covers the 1.0 line. The history of the 0.x releases stays with that
line.

## Unreleased

A generic engine with an explicit API: every stage takes a run context, and
nothing in the engine names a particular deployment, source or procedure.

- **One namespace, packaged data.** The engine is `cartolex.lexicon` and
  `cartolex.atlas`; the stop-word lists and prompt templates ship in
  `cartolex/_data/` and are read with `importlib.resources`.
- **An explicit run context.** Every stage takes a `cartolex.context.RunContext`
  (`RunContext.for_workspace(workspace, settings, **overrides)`): its
  `EnginePaths` names every file the engine reads or writes, and it carries the
  settings, the run's own stop-word profile, the prompt directory, the
  staleness hooks, the current year, thread limits and the AI usage recorder. Importing a module has no side effect.
- **Corpus slots.** `KeywordsConfig.corpus_slots` is an ordered registry of
  `CorpusSlot(id, fit=True, trajectory=True, doc_types=None)`, one slot
  `manual` by default. A slot's index and text folder derive from its id;
  `fit` slots build the map, `trajectory` slots feed the trajectories, and a
  slot's `doc_types` filter its own documents. Slot order is document order.
- **Domain words.** The settings name the mapped field `domain_title`,
  `domain_id` and `top_n_domain`; the optional catalog is
  `cartolex.lexicon.domain_catalog`; the triage placeholder is
  `{domain_title}`. The AI cache keys are unchanged.
- **Cohorts in the map merge.** `CohortInput`, `CohortBundle`, `CohortSense`,
  `cohort_id`; the bundle format is `map_bundle/2` and the reconciliation table
  `map_reconcile/2`.
- **Generic workspace files.** The override file is `manual_data/overrides.json`
  (the domain title and stop-word additions and removals); the domain keyword
  table is `keywords_domain_restricted.csv`; the settings snapshot records the
  slot registry (`snapshot_version` 3).
- **A smaller engine.** Modules and files specific to one deployment are gone
  (rank model, profile classifier, salted identifiers, member map); the person
  map colours by any category column (`run_lexical_plots(ctx,
  color_persons_by=…)`); the helpers that describe a projected vector live in
  `positioning`. The packaged stop-word lists hold generic language only.
- **Generic person attributes and group labels.** Any column of a corpus
  index that neither identifies a person nor describes a document is a person
  attribute: the roster keeps its first non-empty value per person under its
  own name (a blank cell stays blank), the PDF corpus builder carries it
  through, and it can colour the person map (`color_persons_by`) or define the
  cohorts of the trajectory figure (`run_trajectories(ctx, cohort_by=…)`, no
  figure by default). The per-person keyword table no longer repeats roster
  columns. Groups are labelled by their value of the `unit` column: the group
  aggregate table has a `unit` column, `RunContext.groups` is gone, and
  `run_lexical_plots(ctx, panel_groups=…)` names the groups that get a panel.
- **Plain stage names.** Docstrings, messages and the subfield draft's
  `instructions` text name the stages in words (term clustering, UMAP layout,
  trajectories…) instead of an application's screen codes.
- **Model files without pickle.** Every fitted object a project stores (the
  restricted vectorizer, the lexical data, the embeddings, the SVD and the
  layout model) is a JSON descriptor with an `.npz` array file
  (`cartolex.atlas.model_files`), so opening a shared project folder can never
  run code; the paths are `EnginePaths.vectorizer_json`, `lexical_data_json`,
  `embeddings_json`, `svd_model_json` and `layout_model_json`. A UMAP model is
  re-fitted from its stored inputs and seed on load and checked against the
  stored map; when it is not reproduced exactly, the stored map is kept and a
  warning gives the measured deviation (`model.refit_deviation`). `.joblib` files of 0.x workspaces are not read: re-run the stage that
  `ModelFileError` names. `joblib` is no longer a direct dependency.
- **The theme tree.** `cartolex.project.themes` edits the tree of
  `decisions/themes.json` with pure operations (rename, move, merge, split,
  create, delete, set aside, put back, review states, insert or remove a
  level), each returning the new tree and a short description of the change;
  it rebases a tree onto a new vocabulary with a reconciliation list, and
  compares two trees. `cartolex.project.themes_versions` saves the tree as
  versions and restores them; `cartolex.project.themes_curated` converts a
  depth-2 tree to and from the engine's curated document, so the apply stage
  runs on a tree. A tree's review states name only keywords it holds (see
  `docs/dev/themes.md`).
- **Errors a caller can catch.** `SettingsError`, `CorpusError` and
  `FileNotFoundError` instead of `SystemExit`, with English messages. A corpus
  language with no text is skipped with a warning.
