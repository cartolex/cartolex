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
- **The theme tree.** `cartolex.project.themes` edits the tree of
  `decisions/themes.json` with pure operations (rename, move, merge, split,
  create, delete, set aside, put back, review states, insert or remove a
  level), each returning the new tree and a short description of the change;
  it rebases a tree onto a new vocabulary with a reconciliation list, and
  compares two trees. A keyword sits on a node of any level (a keyword on a
  higher node is broader than every node below it) and counts toward its node
  and every node above; its attribution lowers that to the top levels, or to
  none, and a move that gives it another node keeps only an attribution of 0.
  `cartolex.project.themes_versions` saves the tree as
  versions, removing empty nodes and stamping each version with its action,
  and restores them; `cartolex.project.themes_curated` converts a depth-2
  tree to and from the engine's curated document, so the apply stage runs on
  a tree (a theme's own keywords weigh as subfield-only terms), and lists the
  document's merge variants as `keywords.csv` rows. A
  tree's review states name only keywords it holds (see
  `docs/dev/themes.md`).
- **Errors a caller can catch.** `SettingsError`, `CorpusError` and
  `FileNotFoundError` instead of `SystemExit`, with English messages. A corpus
  language with no text is skipped with a warning.
- **The build.** `cartolex.build` runs a project's stages
  (`docs/dev/build.md`). Each stage declares what it reads, its parameters
  (a default, a rule computed from the project's sizes, or a value set in
  `decisions/params.json`, refused with the reason when impossible), its cost
  and whether it asks consent. `status()` gives each stage one of six states
  from its `run.json` and the current fingerprints of what it read, with the
  reasons for an update; `plan()` is the dry run, with estimates of time and
  memory; `build()` runs exactly that plan, in staging folders swapped into
  place through a journal that opening a project for writing completes or
  undoes, with consent, a memory budget, monotonic progress with a heartbeat,
  a cooperative cancel and resumption of a killed run from its last chunk. The
  last failed or cancelled attempt of a stage is recorded in
  `derived/.attempts/<stage id>.json`. A stage has a behaviour version,
  recorded in `run.json` (`code.stage_version`): a deliberate change of what a
  stage produces makes its earlier results need an update.
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
- **Counting units and bands in the extraction.** The scoring
  (`cartolex.lexicon.scoring`) reads each person's texts apart:
  `KeywordsConfig.counting_unit` (and the build's
  `keywords.extract.counting_unit`) makes a TF-IDF document a person (the
  default, unchanged), a text or an organisation. Each candidate falls in a
  band — `kept`, `check` or `aside` — with a reason code (`multiword`,
  `single-word`, `common-modifier: …`, `part-of: …`), and the raw keyword
  tables gain the columns `people`, `texts`, `band` and `reason` (the merged
  list gains `band` and `reason`). Bands remove nothing yet. The settings
  snapshot records the counting unit.
- **The lexicon lab.** `python tools/lexicon_lab/run.py --suite quick|full`
  compares the open choices of the extraction (the English `of` complement,
  counting units, text votes, part weights, the length bonus, name
  recognition, the band rules, the AI triage routes) on the demo worlds and
  on public benchmarks, and writes a report; see `docs/dev/lexicon-lab.md`.
  It set three defaults: English phrases take no `of` complement (on the
  demo worlds most `X of Y` spans are phrasing, and they hid the terms inside
  them), nothing is set aside for a low score (the least specific tenth held
  gold the rule lost), and a candidate is a fragment of a longer one only
  when it is never seen outside it. The other choices are lab switches, not
  settings.
- **The owner's lexicon decisions (gate G2).** The common-modifier band rule
  is off by default (a lab switch only): a phrase is kept, a single word is
  to check, a fragment of a longer candidate is set aside
  (`keywords.extract`, stage version 2). The AI clean-up judges the kept and
  to-check bands and never sends the set-aside band (`keywords.triage`,
  stage version 2); its prompt keeps F for broken pieces, accepts a process,
  property or measure of an object of the field, treats a single everyday
  word as too generic unless it is a term of art, and gives a term of
  another language the canonical form of the reference-language term that
  names the same thing. The AI caches' keys are unchanged.
- **The browser-handoff test.** `tools/lexicon_lab/handoff_bundles.py` writes, from
  a project build of a demo world, the bundles a person hands to a chat
  assistant (a prompt, the numbered terms with their evidence, the answer
  format, zipped; parts sized to fit one conversation), and
  `tools/lexicon_lab/score_handoff.py` scores the answers against the
  world's truth, beside the lab's oracle.
- **Demo bodies.** `generate(..., bodies=True)` (`--bodies`) gives every work
  a long, repetitive body with generic filler (introduction, methods,
  results, discussion, captions) in its language, from a random stream of its
  own; the rest of the world, and every default world, is unchanged. The
  truth's lexicon lists the body templates' pieces as filler.
- **Collection foundations.** `cartolex.collect` holds the HTTP layer every
  finder uses (`HttpClient`: per-host pacing, finite timeouts, retries with
  backoff and `Retry-After`, typed errors naming host, status and what to do,
  cursor pages checked against the announced count, the `cache/http/` cache
  with a lifetime per kind of request and `refresh` / `cache_only` modes,
  cancel, progress and a record of the hosts contacted and the kinds of data
  sent), text hygiene at the boundary, and the source writers: raw records per
  slot and run, stable ids from a registry that never gives a number twice,
  and the six source tables rebuilt from the raw records, byte for byte.
  `cartolex.demo.services` serves the subset of OpenAlex and the ORCID public
  API that cartolex uses, on the loopback interface, from a bibliographic
  layer derived from a demo world (homonyms, split and mixed records, people
  without records, registry works, affiliation histories, outside
  co-authors), with fault injection; `python -m cartolex.demo services`
  starts it. See `docs/dev/collection.md`.
- **Collection.** `cartolex collect` brings people and texts into a project
  (`docs/collection.md`): a list of people (CSV or pasted, with a column
  mapping proposed from the header; e-mail addresses refused and never
  stored; other columns kept as filters; duplicates proposed, never merged),
  a folder of documents matched file by file, or a corpus in the engine's
  contract. Resolution finds each person's OpenAlex records from every
  variant of their name, with the stated institution ranking candidates and
  never filtering them, explains each score, accepts a single clear match
  automatically (to review), and compares records with the ORCID registry
  to separate people an index merged. The harvest unites the works of every
  confirmed record and registry, removes duplicates, and fills the source
  tables with texts, parts, authorships and dated affiliations. Before every
  collection, cartolex says what will leave the computer, where and why
  (`--dry-run`, `cartolex.collect.privacy`); `docs/privacy.md` describes
  what is sent, what never is, what is kept and how to delete it.
  `--services demo` runs everything against the demo services, offline.
- **HAL, SciELO, merging and text providers.** `collect_hal` finds people's
  deposits by idHAL (cursor paging, the DOI link, titles and abstracts per
  language, stated structures with their parents) and `collect_scielo` their
  articles by ORCID, with abstracts in every language (ArticleMeta API); a
  name only proposes candidates. When the tables are rebuilt, texts found by
  several finders are merged (same DOI, a shared identifier, then title and
  year for the same person), fields filled by a finder priority, and every
  merge, refusal and conflict listed in `sources/merges.json`; a preprint
  stays apart from its published version (`version_of`) and the build reads
  only the published one. `improve_texts` fills missing abstracts and, on
  request, fetches full texts (JATS from Europe PMC, bioRxiv/medRxiv and
  SciELO, LaTeX from arXiv, then PDFs from HAL and OpenAlex's open-access
  links), each provider declaring what it sends. `body` and `full` parts are
  private (`PRIVATE_PARTS`). The HTTP client reads bytes answers
  (`get_bytes`), and the demo services serve all of these services offline.
- **The engine on a project.** Each stage runs the engine
  (`cartolex.build.engine`): an ownership table gives every engine file a
  place in the project, the layout's amendments of earlier files are copied
  into its own folder, and the settings come from `project.json`, the
  parameters (with explicit theme level sizes when wanted) and the earlier
  stages' records. The first layout adds and pins map version `v1`; a curated
  `decisions/themes.json` is rebased onto the current vocabulary and applied;
  the AI clean-up takes a key or an injected client (`AIAccess`), with its
  answers cached in `cache/ai/`. `RunContext` gains `progress`, `cancel` and
  `ai_client`: the engine's long steps report progress and stop on a cancel.
  The numeric reference also runs through a project build
  (`check_reference.py --via-project`), identical to the stored baseline. New
  commands: `cartolex build`, `status`, `params` and `versions`
  (`docs/build.md`).
- **Placement by nearest people (gate G2).** Everything placed on a finished
  map — the keywords of the layout, the trajectories' time bins and windows,
  the projected people — goes to the weighted mean of the heaviest linked group
  of its eight nearest people in the SVD space (two neighbours are linked when
  their map positions are within a quarter of the map's radius;
  `cartolex.atlas.placement`). UMAP's `transform` and the re-fit of a stored
  layout model are retired: no layout model is stored (`layout_model_json` is
  gone from `EnginePaths`), the map is the stored embeddings, and
  `positioning.load_positioning_models` returns the map's anchors instead of a
  model. On the large demo world the keywords keep their neighbourhoods better
  than with UMAP's transform (0.824 against 0.794), nothing is stranded between
  two places, and the trajectories stage is 3.3 times faster. `map.layout`,
  `map.trajectories` and `overlays.position` are at version 2; the baseline is
  updated (`tests/baseline/LOG.md`). See `docs/dev/placement.md`.
- **The web interface's foundations.** `cartolex/app/static/` holds the new
  interface as native ES modules on vendored Preact, hooks, htm and signals
  (`tools/vendor_ui.py`, hashes in `vendor/VENDOR.md`), with no build step:
  the shell (manifest, catalogues, extensions, then the route), a history
  router with a page lifecycle (navigation token, `AbortSignal`, teardown,
  late answers dropped, a guard for unsaved edits), registries for pages,
  slots, facets and layers, an extension API (`register(api)`), stores for
  the project state (cached status dots), jobs (one poller) and preferences,
  an API client (CSRF header, `ETag`/`If-Match` with a typed stale result,
  errors turned into ErrorCards), catalogues in English, French and
  Portuguese (Brazil), design tokens for a black-and-white look in light and
  dark themes with state coded by shape, and the component library with a
  gallery at `/gallery` (`docs/dev/ui.md`). Two new checks: `js`
  (`tools/ui_check.py`: parse, imports, literal text, bans, vendored hashes,
  catalogues, contrast) and `browser` (`tests/browser/` in headless Chromium,
  offline: axe, keyboard scripts, budgets, teardown and leaks). A fixture
  server (`tools/ui_fixture_server.py`) stands in for the app's routes.
- **The app's core.** `cartolex.app` is the web app, for one person on their
  computer or a hosted service (`docs/dev/api.md`): `create_app(settings,
  extensions)` returns the ASGI app; FastAPI, uvicorn and python-multipart
  become dependencies. `cartolex` (or `cartolex app [FOLDER]`) starts it on a
  free loopback port and opens the browser with a launch link that works once;
  `cartolex api` serves it for hosting. Every request gets its project from
  the app's own state (one project open locally, many chosen by the route or
  the principal when hosted): nothing is process-wide. Builds and collections
  run as jobs (`JobRunner`, a local thread runner): one per project at a time
  (a second gets 409 naming the first), cancelled at a safe point, logged in
  `logs/jobs/<job id>.jsonl`, and reported `interrupted` when their process is
  gone. Security: a host check, the launch token exchanged for a session, a
  CSRF header bound to it, no CORS, a strict Content-Security-Policy on every
  response, bounded inputs and checked uploads. Every route calls
  `authorize(principal, action, resource)`; decision files are read with their
  version as `ETag` and written with `If-Match` (412 when stale). Every error,
  empty result, skipped stage and failed attempt carries a stable code and its
  params, for the interface to show in its own language (English text as a
  fallback), and the next action. Per-person preferences
  (`/api/me/preferences`) are kept in the app's own folder for a hosted
  service. The manifest the interface starts from
  is `cartolex-manifest/1`, with a generated JSON Schema
  (`docs/dev/app-manifest.md`). Host applications add pages, routes, slots,
  stage declarations and patches, branding and more through `Extension`
  (`docs/dev/extensions.md`). The routes cover the project state, building,
  parameters, map versions (discarding a version nobody pinned is new),
  snapshots, people and their import, collection behind a protocol with
  stand-ins, keywords in their three bands, the theme tree's operations,
  versions and apply, the atlas bundle cached by lineage, sharing, settings
  and the AI handoff, whose reusable part (the parts a person gives a chat
  assistant, the prompt, reading the answer back; `cartolex-handoff/1`) is
  ported from the lexicon lab into `cartolex.project.handoff`. JSON log lines
  and a diagnostic without project data; a container image
  (`deploy/Dockerfile`, `docs/hosting.md`). The build takes the job id of its
  log from a runner, and a host's prompt folder and function words
  (`EngineOptions`).
- **Themes at any depth.** `themes.group` builds the depth its parameters give,
  1 to 4: the finest level is the term clustering, each coarser level a Ward
  cut of the one below, and the proposal is a theme tree
  (`themes_draft.json`). A default project follows the depth rule: the S demo
  world now gets one level of 15 themes, where the engine used to force 15
  themes over 23 topics. `themes.apply` applies `decisions/themes.json` or the
  proposal at any depth, keywords on any level with their attributions, and
  writes each node's weight and share for every person and organisation, the
  keywords' weights and the nodes' top keywords (`themes_applied.json`,
  `theme_keywords.csv`, `theme_people.parquet`, `theme_organisations.parquet`,
  `docs/format/derived.md`). The layout places the nodes on the map, the
  trajectories and the projected people carry the weights of every level, and
  the maps colour each keyword by its node: one hue per top-level node, a
  shade for each node below. At depth 2 the two-level documents are still
  written, unchanged; new readers use only the files of any depth. A rebase
  proposes each new keyword the curated node that holds a majority of its
  group in the new grouping. The lexicon weights and the map bundle no longer
  make the people × keywords matrix dense (the same numbers, bit for bit); a
  bundle carrying the theme tree and its weights is `map_bundle/3`, one
  without stays `map_bundle/2`. Above 15 000 keywords the clustering runs in
  two stages, mini-batch k-means micro-clusters then a size-weighted Ward, so
  its memory no longer grows with the square of the vocabulary. `themes.group`
  and `themes.apply` are at version 2; `map.layout`, `map.trajectories` and
  `overlays.position` at version 3. See `docs/dev/themes-engine.md`.
- **The theme editor.** The themes screen edits the theme tree at any depth:
  an outline with a search over every keyword, a treemap and a map of people
  and keywords kept in sync, and a side panel with each node's keywords, the
  people who weigh most on it and its names. Every action (rename per
  language, move, merge, split, create, delete, set aside and put back,
  attribution, levels) has a menu item and a key; drag and drop is a shortcut.
  Undo and redo are unlimited and named by the operations; a draft is kept in
  the browser per project and restored after a reload or a crash; leaving asks
  in the page; a save refused because the tree changed reloads and merges;
  versions can be read, compared and restored. The « To check » queue, the
  banner for another vocabulary (with a rebase on demand), agreeing once on a
  clustering-only change (an apply made without an answer keeps the tree
  over it, as a version that says so), and « Save and apply » in the
  background are part of it; people's shares and the map refresh after an
  apply. The page is a set of modules under `pages/themes/`, and the
  navigation budget counts API calls only. AI curation goes through a theme handoff
  (`cartolex.project.themes_handoff`, `cartolex-themes-handoff/1`): the tree
  and each node's most used keywords out, a list of operations back, each
  reviewed before it applies. New components: `TreeView`, `Treemap`,
  `MapFrame`; twelve hue tokens for the themes. `GET /api/atlas` is
  `cartolex-atlas/2` (levels, nodes, usage shares per level), read from the
  theme files of any depth, and the editor's draft comes from
  `themes_draft.json`, so projects of depth 1, 3 or 4 get their map and their
  tree. See `docs/dev/themes-editor.md`; `docs/dev/usability-g3.md` holds the
  materials of the usability test.
