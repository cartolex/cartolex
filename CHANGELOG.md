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
- **Domain words.** The settings name the mapped field `domain_title` and
  `top_n_domain`; the triage placeholder is `{domain_title}`. The AI cache
  keys are unchanged.
- **No catalogue anchor in the triage.** The optional domain catalogue
  (`cartolex.lexicon.domain_catalog`, `KeywordsConfig.domain_id`,
  `EnginePaths.domain_catalog_jsons`) and the prompt's block of reference
  subfields are gone: the lexicon comes from the corpus alone.
  `KeywordsConfig.domain_description`, a short text the project owner writes,
  is given to the model as context through `{domain_description}`. The AI
  cache keys do not change; a prompt template that still uses
  `{subfields_block}` is refused with a message saying what replaces it. A
  workspace whose domain title came from a catalogue entry
  (`<code> — <title>`) keeps its cached answers by setting that same string as
  its `domain_title`.
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
- **Errors a caller can catch.** `SettingsError`, `CorpusError` and
  `FileNotFoundError` instead of `SystemExit`, with English messages. A corpus
  language with no text is skipped with a warning.
- **Noun-phrase candidates.** `keywords.extract` finds its candidate terms
  with spaCy part-of-speech patterns (English, French, Portuguese) instead of
  every sequence of one to four words: nested spans count, candidates are
  grouped by lemma and shown in their most frequent form, and scoring is the
  same TF-IDF as before. spaCy 3.8 is a dependency; its language models are
  separate, pinned installs (`cartolex.lexicon.language_models`), and a
  missing one stops the run with `LanguageModelMissing` and the command that
  installs it. Terms with a word of one or two letters are kept whole
  (`trait de côte`, `linha de costa`); the packaged stop-word lists no longer
  filter candidates (short function-word lists do), and the triage's safety
  net drops only numbers and malformed strings. Parsed texts are kept in a
  parse cache (`EnginePaths.parse_cache_dir`); parallel parsing gives the same
  output as serial. The raw keyword tables gain a `forms` column, and the
  attribution counts every form of a term. Portuguese is a full corpus
  language: `corpus_languages` is any subset of `en`, `fr` and `pt`, and
  another code is refused with `SettingsError`. See `docs/dev/extraction.md`.
- **A trilingual demo world.** The demo vocabulary has Portuguese forms
  (Brazilian spelling) for every theme term, compound, method, driver and
  setting, and Portuguese sentence templates; `generate(..., languages=
  "en,fr,pt")` (`--languages en,fr,pt`) writes a world whose groups, people
  and bibliography are those of the default world, with some works in
  Portuguese. Its `truth.json` records every phrase of the texts with its
  language and whether it is a field term or generic filler (`lexicon`). The
  default `en,fr` worlds are byte-identical to before.
- **A drift baseline for the numeric reference.** `tests/baseline/{S,L,merge}`
  is a second stored run in the `cartolex-reference/1` format, made by this
  tree: `tools/reference/check_reference.py` compares the current engine with
  it (identical or within tolerance, no ledger) and with the 0.7.2 reference
  (through `tools/reference/explained.toml`, where an entry may now explain a
  whole stage by an upstream cause). `--update-baseline --reason TEXT` is the
  only way to rewrite it: the reason goes into its manifest and
  `tests/baseline/LOG.md`. The released engine's environment is now called
  `released`.
