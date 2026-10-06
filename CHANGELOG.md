# Changelog

This file covers the 1.0 line. The history of the 0.x releases stays with that
line.

## Unreleased

A generic engine with an explicit API: every stage takes a run context, and
nothing in the engine names a particular deployment, source or procedure.

- **Projects of millions of texts on an ordinary computer.** Memory no longer
  grows with the texts; their processing runs in worker processes, and the
  results do not depend on how many.
  - *Source tables*: rebuilt through a scratch SQLite database, a harvest read a
    person at a time and each work reduced to the project people on it; the id
    registry is `ids.parquet` (`cartolex-ids/2`; `ids.json` is read and
    replaced). `cartolex collect rebuild FOLDER [--workers N] [--scratch DIR]`.
  - *Corpus*: packed (`pairs.parquet`, `people.csv`, `texts.parquet`), read by
    every stage through `cartolex.lexicon.corpus_store`, which also reads the
    one-file-per-text contract. `corpus.assemble` holds each authorship as two
    codes and reads the parts' contents only to break a tie between copies of a
    work, a row group at a time (`cartolex.project.corpus.work_copies` finds
    the copies as it does).
  - *Extraction*: each text read, parsed and counted once, in worker processes;
    the parse cache is an SQLite file; scoring works from counts
    (`scoring.score_aggregates`).
  - *Text space*: above 500,000 texts, the exact SVD through the keywords' Gram
    matrix.
  - *Trajectories*: each text counted once, a bin's vector the sum of its texts'
    counts; the people's chunks in worker processes. Each person's time windows
    are described one by one (`trajectory_windows.json`,
    `trajectory_themes.parquet`); every run of consecutive windows, whose number
    grows with the square of a person's windows, only with `spans: all`.
  - *Tables read*: a table's file is checked a row group at a time; a file
    cartolex wrote says so in its footer, with its rows, and is not read again
    to be checked. `cartolex.project.text_columns` reads what lists and counts
    need of each text (year, richest part, providers, languages, people) as
    arrays, for every text or one person's.
  - *Coverage*: computed from those columns, each person's counts at once;
    a harvest writes in its run's header the works each person's records hold
    and the works received, which the cause of a missing profile reads.
  - *Processes*: a build started from the app runs in a child process under
    the app's project lock (`ProjectLock(holder=…)`, `Project.open(holder=…)`),
    with its progress and cancel; a rebuild of the source tables above 256 MB of
    raw records does too (`rebuild_sources(isolate=…)`). Their memory goes back
    to the computer when they end. Settings › Build: what the builds started
    from the app may use of this computer (memory, worker processes, a scratch
    folder), saved on this computer (`PUT /api/machine/budget`).
  - *Map*: the bundle is `cartolex-atlas/3`: it counts the people's time
    windows and gives their years; the windows themselves come apart, as
    columns (`GET /api/atlas/windows`, every one when they are shown, the
    selected person's otherwise), each with its largest top-level node. On a
    sample of 86,000 mapped people the first map request went from 205 MB,
    97 s and 22 GB to 38 MB, 27 s and 2.4 GB; all the windows are 14 MB. The
    texts layer draws at most 100,000 texts, a uniform sample of a larger
    corpus (the map says so), and reads only their parts: on 825,000 texts,
    22 s and 0.2 GB instead of 147 s and 7 GB.
  - *Decision history*: a CSV decision file of 256 KB or more keeps most
    earlier versions as deltas from the latest version kept whole
    (`.csv.delta`, `cartolex-history-delta/1`; one in 50 kept whole), read and
    restored as before (`cartolex.project.files.read_version`).
  - *App*: the texts are a view of columns built once per version of the
    tables and kept in the project's cache (`cache/views/`), memory-mapped:
    lists, filters, sorts and counts of millions of texts without an object per
    text; one text, one person, one organisation read from the row groups that
    hold it; the coverage and the organisations computed once per version.
  - *Budget*: `cartolex.scale.Budget` (memory, workers, scratch folder) reaches
    every stage; `cartolex build --workers --memory --scratch`. A scratch folder
    names its computer and process (`cartolex.scale.scratch_folder`): one a
    killed job left behind is removed by the next job that makes one.

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
- **Set-aside candidates stay out of the lexicon without AI too.** Without AI
  decisions, the consolidation's band gate keeps only the concepts with a
  candidate in the kept or to-check band
  (`cartolex.lexicon.scoring.LEXICON_BANDS`, the constant the triage now
  shares in place of `JUDGED_BANDS`) and reports what it removed in the run's
  progress. Set-aside candidates stay in the raw tables with their band and
  reason; an explicit keep (the manual keep list, a `keep` in
  `decisions/keywords.csv`) still wins, and a raw table without a `band`
  column is read whole. With AI decisions nothing changes (`keywords.build`,
  stage version 2).
- **The browser-handoff test.** `tools/lexicon_lab/handoff_bundles.py` writes, from
  a project build of a demo world, the bundles a person hands to a chat
  assistant (a prompt, the numbered terms with their evidence, the answer
  format, zipped; parts sized to fit one conversation), and
  `tools/lexicon_lab/score_handoff.py` scores the answers against the
  world's truth, beside the lab's oracle.
- **Elided words are words of their own.** An elided article, preposition,
  pronoun or conjunction (`l'`, `d'`, `qu'`, `s'` …, straight or typographic
  apostrophe) is a word unit of its own, and the word after it starts one, as
  after a space. A token the language model leaves whole is split: the
  Portuguese model keeps `d'água` as one noun, so `coluna d'água` or `massas
  d'água` were never candidates (`keywords.extract`, stage version 3; parse
  cache patterns `np2`). `text_utils.term_words` cuts a shown term back into
  the extraction's words, and a word of the project's rejections now blocks a
  term after an elision too. The part-of rule already saw the elision; the
  nested filter and the length bonus still read words between spaces, a
  choice the lexicon lab measured (`docs/dev/lexicon-lab.md`). English and
  French candidates of the demo worlds are unchanged.
- **Stop words and evenly spread words are set aside.** Text of another
  language in a stream (a mixed paragraph, a title in capitals) made its
  articles single-word candidates (`des`, `la`, `LE` among the English ones)
  and its phrasing kept phrases. `cartolex/_data/stopwords/closed_words.json`
  lists the closed words of English, French, Portuguese and Spanish; a
  paragraph whose phrases hold two different closed words of another
  language is read as that language, and there they cut phrases. New
  set-aside reasons: `stop-word` (such a word, or a single word among the
  language's spaCy stop words and packaged words), `stop-word-edge: <word>`
  (a phrase starting or ending with another language's closed word; a
  capitalised word begins a name: `La Niña`) and `even-spread` (a single word
  used by at least a fifth of the people, spread over them like a randomly
  scattered word: `étude`, `objetivo`, `study`). Switches of `BandRules`
  (`stop_words`, `even_spread`, `even_people`) for the lexicon lab, which
  gains trilingual worlds with misdetected French texts and the precision
  and recall of the lexicon without AI; on them no gold term is lost, and
  the English candidates holding a French closed word fall from 1,047 to 18
  on L (`keywords.extract`, stage version 4; the parse cache is unchanged).
- **Keyword categories and the rejection lists.** Every AI route answers a
  category with each code (`cartolex.lexicon.categories`): an accepted keyword
  is a `concept`, `method`, `object`, `place` (new code P) or `field` (D); a
  rejected candidate is `never` a keyword in any field (K, G, F, given only when
  sure) or not informative `here` (N, and the new H). The typed triage prompt,
  the handoff's (version 4) and the copilot's decisions carry them; older
  answers read the same way, and the AI cache keys are unchanged (the per-term
  answers also store the category). `decisions/keywords.csv` gains an optional
  `category` column. `never` answers enter a per-machine rejection cache
  (`<data dir>/rejects/<lang>.jsonl`, `cartolex-rejects/1`, with the route, the
  day and a fingerprint of the project; a handoff's or a copilot's answer
  enters only when marked `sure`, a new field of their answers, `unsure` when
  absent), beside cartolex's shipped list
  (`cartolex/_data/rejects/<lang>.json`, empty for now; `cartolex rejects export`
  builds one from a cache for the maintainers to review). At extraction, the
  candidates they name go to a fourth band, `rejected` (« rejected by
  cartolex's list », « rejected by your earlier projects »): never sent to an
  AI, never in the lexicon; a project's own answers never reject its own
  candidates, and a person's decision wins and takes the term out of the cache
  (put back). Every AI route now judges every other candidate, set aside
  included, so a rule's set-aside can be rescued; the answers already paid for
  are reused, and the estimate counts the new ones only. The Keywords screen
  gains the Rejected automatically tab, a category column and filter; the
  map filters and colours keywords by category; a theme's name prefers a
  concept or an object on a tie of use; the settings (Words) show both lists,
  empty the cache and switch them off per project (`keywords.extract` parameter
  `rejects`). Stage versions: `keywords.extract` 5, `keywords.triage` 3,
  `keywords.build` 3 (it writes `categories.json`). On the S demo worlds, after
  the AI triage of seed 0 (1,447 `never` answers), 608 of the 2,542 candidates
  of seed 1 are rejected automatically: the AI judges 1,934.
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
- **Institutions, collaborators, the snapshot and coverage.** People come in
  from institutions (`cartolex collect institutions`, `propose_people` and
  `take_people`: by OpenAlex or ROR id, or found by name; every unit below
  the institutions; authors with enough works in the window, with their
  evidence; affiliations dated by work; organisations with the project's
  levels, mapped from the index's types, and every parent; split records
  suggested, never merged) and from collaborators (`collect collaborators`,
  `snowball`: co-authors of confirmed seeds round by round, with the joint
  works, the path back to a seed and a topical fit; works of more than 25
  authors left out; whole rounds up to a cap; `context` by default;
  decisions in `snowball.csv`). The OpenAlex snapshot stands in for the API
  (`collect snapshot`, `--snapshot`; `cartolex.collect.snapshot` streams its
  partitions, in worker processes on request, and gives the same tables).
  The coverage report (`collect coverage`, `cartolex.collect.coverage`) says
  who is good, thin, failed or without data, why (the first blocking
  cause), by organisation, year and language, and offers a retry of what
  failed, documents for one person, or an exclusion; a person's failed
  collection is recorded and the job goes on. HAL and SciELO proposals are
  confirmed like OpenAlex records (`hal:<idHAL>`, `orcid:…`) and listed with
  them (`identity_queue`). A slot may set its window of years
  (`project.json`, `collect window`), `params.json` the collection's
  parameters (`collect`); the raw runs, the id registry and the merge log
  are documented as part of the format. Rebuilding the tables reads the
  harvests from digests in `cache/sources/`, so only new runs are read
  whole; PDFs are read in a worker process with a timeout. The build reads a
  folder's or a corpus's documents whole and a collection's texts (not its
  datasets or software) by rules on the slot's kind, and people's attributes
  reach the engine's index. The demo layer gains RORs, a joint unit, outside
  co-authors' own works, a 30-author collaboration and a mini snapshot, and
  the vocabulary scan reads the names it invents.
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
- **Sizes: 10⁵ people on a workstation.** No step holds a dense people ×
  keywords or people × people matrix any more: the map merge, the
  reconciliation, the trajectories and the pair metrics keep their matrices
  sparse and take dense blocks of rows (exactly the former results on every
  demo world), placement holds a bounded chunk of distances, the corpus is
  assembled with its text parts read in batches, the trajectories read the
  texts by chunks of people, and the extraction holds each distinct word
  once. The space's dimensions follow the rule `space_dimensions` (20 up to
  2 000 people, then 20 × √(people / 2 000), at most 200). A map version may
  use the `umap`, `tsne` (optional `openTSNE`, the `tsne` extra) or `tree`
  layout; a project's first map is a t-SNE from 1 000 mapped people. The cost
  models are fitted on builds of 10³ to 10⁵ people (within a factor of two).
  `cartolex.demo.scale` writes streamed worlds of any size into a project.
  See `docs/sizes.md` and `docs/dev/layouts.md`.
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
- **The corpus screen.** The People screen (`/people`) holds who is in the
  map and what was collected for them, in tabs: people (roles, identity,
  coverage state, filters built from a list's own columns, a role changed for
  the rows selected or for all the filters keep), the identity queue (every
  finder's candidates with their evidence and score; ↑ ↓, 1–9, N, ⏎; the
  single clear matches accepted in bulk), organisations (levels, parents,
  affiliations with their years, the people of institutions to take),
  texts (parts per provider, merges, preprints), collaborators (rounds, cap,
  decisions) and coverage (good, thin, failed, no data by organisation, year
  and language; retry, exclude). A person's sheet says the first blocking
  cause, the sources used and discarded, the texts and the affiliations.
  Imports take a list (one field per column, proposed and editable; e-mail
  columns refused), a folder of documents or a corpus, and propose the
  duplicates. Collecting always shows what leaves the computer first and
  starts only on consent (`consent_needed`, 409 without it); it runs as a job
  in the Activity drawer. `ServiceCollection` (`cartolex.app.collect_service`)
  wires the finders of `cartolex.collect` into the app (identify, harvest,
  institutions, collaborators, retry); `cartolex app --services demo` runs it
  on the demo services. The lists are paged on the server and the Table reads
  the pages in view (10⁵ people). New routes: organisations, texts, a
  person's sheet, duplicates, collaborators, the institutions' proposal,
  documents and corpora imported as a job (`docs/dev/api.md`).
- **The atlas.** The map screen (`/map`) shows the treemap of the themes,
  the map and the panel of what is selected together. The map shows people,
  keywords, organisations of one level, texts, projected people and time
  windows, several at once, one symbol per kind, as points or as regions
  spanning their keywords; filters built from the people's own columns and a
  period hide what they leave out, and one button clears them all, the period
  included; the selected person's time windows are joined by a line; « Find on
  the map » selects by name; a world view places organisations at their
  address. Every count says what it counts. The state is kept in the address.
  Map versions are pinned, discarded or tried (another seed, or UMAP, t-SNE,
  the theme tree), and another project's map can be added as a base, on which
  this project is placed by the keywords both share. The MapFrame draws with
  WebGL (10⁵ points pan at the frame rate on a laptop's integrated GPU; Canvas
  2D when WebGL is missing), reveals keywords and texts with the zoom, keeps a
  legend, shows a hover card and labels the selection. Its modules under
  `components/map/` use no library, so the offline site can load them, as
  modules or as one classic script (`cartolex.app.static_files.classic_script`).
  New routes: `GET /api/atlas/texts`, `GET /api/atlas/regions`,
  `GET|POST /api/map/bases`, `DELETE /api/map/bases/{id}`; `GET /api/atlas`
  adds the organisations, the people's columns and the years, and takes
  `base` (`docs/dev/api.md`).
- **The keywords screen.** `/keywords` lists every candidate in three bands
  (kept, to check, set aside) with its reason, its people and texts, its
  language and forms, and the route that decided it (you, an AI in a browser,
  an AI by API, or the extraction); search, filters, range selection, keep,
  exclude, merge and undo for the rows selected or for every row the filters
  keep; the history of the decisions, each one restorable; the counting unit
  in the head, and a warning when several languages have no AI filtering yet.
  The AI filtering goes by handoff (the kept and to-check keywords in parts,
  the answer reviewed term by term, all or some accepted; an accepted term
  whose English form is another term is merged into it) or by API (what is
  sent, an estimate of the calls and tokens, consent, a build job of
  `keywords.triage`, whose verdicts the list then shows). The parts of a
  handoff keep the terms the same people use together (`group_items`; the
  extraction now writes `term_people.npz`, people's indices only): on the L
  demo world, kept and to check, 40 % of the French–English twins share a part
  instead of 14 % (10 parts; 49 % instead of 25 % with 5). New routes:
  `GET /api/keywords/decisions`, `POST /api/keywords/decisions/where`,
  `GET /api/keywords/ai`, `POST /api/keywords/ai/run` (`docs/dev/api.md`).
- **Theme names in each language.** The proposal names a node, in each
  language, after its most used keyword that has a form in that language (its
  own, or one the consolidation pairs attest), else after the most used
  keyword's reference-language form: a French keyword no longer names a theme
  in English. The two-level draft follows the same rule. `themes.group` is at
  version 3.
- **Packaging.** The Mistral SDK is an optional extra, `cartolex[llm]`, next
  to `tsne`; without it the AI clean-up by API says which extra to install. The
  wheel and the source archive are checked for missing and stray files
  (`tools/package_check.py`), fresh installs on Python 3.10 and 3.14 are
  measured by `tools/install_check.py`, and two hand-started GitHub workflows
  run the checks on Linux, macOS and Windows and build the archives.
  `docs/install.md` is the user's installation guide.
- **An installer kit.** `tools/installer_zip.py` builds a small zip of
  launchers (macOS, Windows, Linux) that install a pinned release, its
  language models and a shortcut into one folder of the user's home, through
  uv, uv with the system's certificates, or the system's Python, with guides
  in English, French and Portuguese.
- **Windows and macOS.** The local server refuses to share its port on Windows,
  a rename refused by a reader is retried there, corpus indexes store
  POSIX paths, the command's output never fails on a character its encoding
  lacks, and text files are checked out with LF line endings everywhere.
- **Candidates from three texts, a work read once.** A keyword candidate must
  occur in at least `min_texts` distinct texts (3 by default), beside the
  `min_people` people: a phrase of one co-authored text is one text's
  evidence (`keywords.extract` version 6). `corpus.assemble` (version 4) reads
  one text per work: texts of a slot with the same normalised title, years at
  most one apart and an author in common are one work, read as its version of
  record; the tables keep every record, and the stage counts the
  `duplicate_texts` it read once (also on the Texts tab, `GET /api/texts`
  `counts.duplicates` and each copy's `copy_of`). A preprint that meets an
  article and its conference version is linked to the article. The demo's
  bibliographic layer holds such duplicates.
- **The AI copilot.** A third route of AI help, for an assistant that runs
  code: the theme editor (More › AI copilot) and the Keywords screen (AI menu)
  export one self-sufficient zip (`cartolex-copilot/1`: a guide, anonymised data
  — keywords, their vectors, people as numbered rows in a random order, never a
  name, identifier, organisation or text — and cartolex's engine as a wheel that
  a stdlib-only `bootstrap.py` unpacks offline). The kit, `cartolex.copilot`,
  re-groups, lays out, draws (map, treemap), measures (coherence, margins,
  borderline, stability, the lab's scores where a truth exists) and records each
  change with its reason; it stops at two checkpoints for the curator. Its
  `result.json` (`cartolex-copilot-result/1`) imports as the proposal the curator
  already reviews: theme operations applied and saved as a version, or keyword
  decisions to accept. `cartolex.lexicon` loads its public names on first use,
  so a light module of it no longer loads the extraction.
- **Parameters on their pages; the Recipe.** Each step's parameters moved
  from the method screen to a « Tune » panel on the page they shape (texts on
  the corpus screen's Texts tab, keywords, the space and the grouping on the
  theme editor, the map's layout and placement on the atlas), collapsed and
  read only when opened, its header saying « defaults » or « N changed »
  (`GET /api/project/state` adds `changed_params`), with the step's
  diagnostics, « rebuild from here », and a note on a page whose outputs need
  an update naming the stage to rebuild from. Every parameter has a short
  label in English, French and Portuguese. The Build page's Recipe tab lists
  every value with its origin and whether it differs from its default, links
  each to its panel, and exports Markdown or CSV (`GET /api/recipe`,
  `GET /api/recipe/export`). `/method` now sends to the Recipe or to a step's
  page and has left the settings menu.
- **Changes seen before a rebuild.** On the Map page, a change of the layout's
  method, seed or a parameter is previewed on the map itself (a sample of at most
  800 people, « Before » and « After » with the nearest people each keeps,
  « Keep » as the pinned map version or « Discard »); a newer change supersedes
  a preview being computed. On the Keywords page, moving `min_people`,
  `min_texts`, `max_share` or `max_keywords` gives at once the candidates kept
  and dropped against the last build and the strongest that would leave or
  enter (`GET /api/method/keywords/preview`, nothing saved; a looser window
  says it needs a new extraction). The map's frame never draws below its
  border, and its legend no longer leaves a light block over the dark map.
- **The method screen.** `/method` (the header's settings menu) shows the build
  step by step — texts, keywords, space, grouping, layout — each with its
  parameters (value, origin, limits, a mark when a value differs from its
  default or was not built yet, back to default), what the step produced, read
  from its outputs (`GET /api/method/<step>`: the candidates by band and score,
  the variance by dimension and the neighbours kept, the levels, the comb's θ
  calibration and a dendrogram of the themes, the neighbours the map keeps),
  and « rebuild from here » (`/build?force=<stage>`). A layout is previewed on
  a sample of the people beside the map (`POST /api/method/layout/preview`, a
  job, then cached) and can become a map version with its parameters
  (`POST /api/map/versions` takes `params`). The settings' list of every
  parameter moved there.
- **The copilot, round 2; the handoff retired.** Two obvious entry points:
  « Triage with AI » (a copilot, or by API) on the Keywords screen and
  « Curate with AI » in the theme editor's header. The prompt-and-paste handoff
  is gone from the interface, the API (`/api/handoff/export`, `…/import`,
  `/api/themes/handoff/export`, `…/import`), the catalogues and the lab's tools;
  the answers it imported stay readable. The triage kit sorts the candidates
  (junk patterns, formulas kept whole, a paper's own phrases, families by head
  word, theme groups, twins found by their words: `pairs()` no longer scores
  every pair of rare terms 1.0), shows each group once and decides a group in a
  line; nothing is decided by omission. A ledger counts what was truly read and
  decided (the result's `coverage` and `caveats`), every step is kept on disk
  (`resume()`, `load()`), a bundle can be cut into parts and several results
  merge on import. The guides start with the field as the curator described it,
  carry the curator's curation notes and standing rules (`decisions/curation-notes.md`, editable in Settings › Project and both dialogs), estimate the
  tokens, fit one conversation or helpers in parallel, and end each checkpoint
  with a question. A restructuring larger than one request comes back as its
  tree, put in place as one step; the review accepts a whole kind of change, or
  the changes that share a reason, at once. The themes kit counts the people
  behind each node, reads the comb on the current tree and suggests only other
  nodes; its views are short unless `detail=True`.
- **No hidden fixed parameter.** Every engine constant that shapes a result is
  a parameter of its stage, at the value it had: the duplicate rules of the
  corpus; the scoring (vote, length bonus, longest candidate, English « of »
  complements, the window's cap), the bands' thresholds, the closed-word and
  generic-word rules of the extraction; the n-gram range, weights basis, nested
  threshold and per-person, per-organisation and field keyword counts of the
  vocabulary; the SVD's seed, iterations and solver; the clustering's
  dimensions, Ward's exact limit, micro-clusters and seed, the comb's θ
  (calibrated by default, or pinned), grid, one-level θ, evidence floor and size
  limit, and the names' floor; the neighbours and link radius that place points
  on the map, and the trajectories' texts per window. The method screen shows
  each under its heading with a one-line explanation in every interface
  language, and every setting of a layout method (UMAP's, t-SNE's metric, the
  tree layout's fill, gap, lean and sharpness). A parameter a run did not record
  is no change at its default: existing results stay up to date. The copilot's
  themes bundle carries the grouping's recorded settings (`context.json`,
  `grouping`), and its kit groups, combs and names with them. The inventory,
  with the constants that stay and why, is in `docs/dev/build.md`.
- **AI help chosen on the build page.** The pre-flight sheet offers a route for
  each AI step, remembered per project (`params.json`, `ai`): the keyword
  clean-up (none, a copilot, or the API) and the theme curation (none or a
  copilot: it has no API route). With a copilot the build runs up to the step
  and ends `waiting` (a new job state, never a failure): export the bundle, give
  it to the assistant, import and accept the result, then « Continue the
  build ». The Activity drawer and the overview's next step say so. The
  manifest's capability `ai_handoff` is now `ai_copilot`,
  `/api/handoff/proposals…` is `/api/ai/proposals…` and
  `/api/themes/handoff/proposals…` is `/api/themes/ai/proposals…`.
- **Large institutions, failures that say why, jobs that pause.** The people
  of an institution are proposed from its works read page by page, with three
  fields, into per-author aggregates: memory no longer grows with the works
  (a run of 10⁵ works peaked at 5.4 GB, now at 0.13 GB). Every 100 pages the
  cursor and the aggregates are checkpointed in the slot's `raw/checkpoints/`;
  Stop, or a page that still fails after its retries, ends the job `paused`
  (a new state) with its cause, and Resume (`resume` on the collection start,
  `--resume` on the command line) gives the proposal an uninterrupted run
  gives. A list above 100,000 works asks for confirmation after its first
  page, with its requests, time and days of budget, and the snapshot and
  narrowing as other routes; the progress shows works, requests, rate and ETA.
  A failed job's log line, card and copied diagnostic carry its cause (code,
  params, exception, message, step), and a collection writes its egress lines
  whatever its end. `JobInfo.error` is now that record, not a sentence.
- **A project reopens after a force-quit.** A lock left by a process that no
  longer runs on this computer (the app force-quit, its terminal closed, a
  crash) is taken over when the project is opened, instead of refusing until
  `cartolex project unlock` removed it; the repairs of a killed build run as
  before. Two openers never both take it (`.lock.takeover`), a process id
  reused by another process counts as gone, and a lock held on another
  computer is still never taken. The error `stale_lock` and the next action
  `unlock` are gone; a project open in another cartolex on the same computer
  is `locked_here`, which says to close that one (a closed browser tab does
  not stop it).
- **Open anyway.** A project another cartolex holds (`locked`, `locked_here`,
  next action `confirm`) can be opened anyway from the start screen, after a
  warning that names the holder and what may be lost (`POST
  /api/projects/open {path, force}`, `cartolex project unlock --force`). The
  application whose lock was overridden checks before each decision write and
  each swap of results that the lock still names it, and otherwise stops
  writing with `lock_lost`; opening the project there again reopens it.
- **The app stops when no page is open.** `cartolex app` stops two minutes
  after its last browser tab closed, once no job runs, instead of running on
  unseen with its project locked. Each page says it is open (`POST
  /api/presence` every 30 s, and once more on closing; manifest capability
  `idle_stop`); a page not heard for ten minutes counts as closed, and a
  computer waking from sleep gives the pages time to call again.
  `--idle-stop MINUTES` changes the delay; `0` or `--no-browser` keeps it
  running.
- **The saved OpenAlex key is used.** A key saved in Settings › Data sources
  now serves the app's collection (read each time a job starts, so saving it
  needs no restart) and `cartolex collect` (after `--openalex-key` and
  `$OPENALEX_API_KEY`, from the app's folder, `--data-dir`); before, only a key
  given in the environment at launch was sent, and collections ran within the
  keyless budget.
- **The harvest asks for people together.** From the API, the works of up to
  50 records of consecutive people are asked for in one list and shared out
  by the records that sign them: about one request per 100 works instead of at
  least one per person, for the same tables (`harvest(…, batch=1)` keeps one
  list per person). A batch that fails is asked for person by person.
- **Large collaborations read whole.** A list answer names a work's first
  100 authors only; a work showing that many is read again on its own (free of
  charge), so that the people further down its list are found on it.
- **The harvest's progress.** It says how many people are done, the texts
  received, the requests sent and the time left (`code: harvest_people`).
- **Views computed once.** Requests asking at once for a view of the corpus
  screen wait for one computation instead of each making its own, and a
  reading's checkpoints no longer make the views computed again.
- **Raw runs compressed.** A run is now gzip-compressed JSON lines
  (`<run id>.jsonl.gz`, `docs/format/sources.md`); runs written before, plain
  `.jsonl`, are read the same way. A national harvest's runs take about a
  tenth of the space.
- **The fields of a work.** The harvest and the collaborators' rounds ask for
  the fields cartolex reads and a few small ones kept for later
  (`WORK_FIELDS`: other identifiers, retraction and paratext, bibliographic
  details, where an open copy is, the references, the index's own topic, kept
  to compare with and never read to build anything); a record holds half of a
  whole one, for the same price.
- **Snapshots at a national size.** What a snapshot pass finds waits on disk,
  compressed and indexed (`RecordStore`, the project's `cache/snapshot/`):
  memory holds about a quarter of a kilobyte per work found instead of 35 KB,
  a person's question is a lookup instead of a scan of every work, and an
  institution's works come in pages of 100 that a cursor resumes.
- **The OpenAlex snapshot in the app.** Settings › Data sources keeps the folder
  of a snapshot downloaded to this computer (`PUT /api/machine/snapshot`,
  `<data dir>/snapshot.json`, never in a project), checked against its
  manifests: ready, incomplete (parts missing or cut short, an entity absent) or
  missing (a disk not plugged in). A harvest, an institutions' reading, a round
  of collaborators or a retry then offers two ways of reading OpenAlex, each with
  its time: the API (its requests, and the days its daily budget spreads them
  over) or the snapshot (the bytes a job reads, at the speed this computer last
  read it); the faster is chosen, and the plan's `openalex` block says which.
  A large institution's pause offers « Read the snapshot instead ». Consent is
  asked only when something leaves the computer.
- **A retry on a snapshot says what still goes online.** Its plan kept the
  snapshot for the identities' searches by name too, so the notice left out the
  OpenAlex requests they still send; they are counted again, with a note that
  only the harvests are read from the snapshot.
- **An index of the snapshot.** `cartolex collect snapshot-index SNAPSHOT`
  cuts each part of the works and the authors into small gzip members of the
  same lines and records which members hold the works of each author,
  institution, DOI and work id, and each author's record
  (`cartolex.collect.snapshot_index`, `docs/format/snapshot-index.md`). A query
  then reads only those members and tests their lines as a whole reading does:
  the same records, for a fraction of the reading. The build runs in worker
  processes, replaces a part only once its new copy is synced, and resumes from
  its last sync point; an index of another release, incomplete, or whose parts
  changed size since is not used. The app's estimates go through it, and
  Settings › Data sources says whether the snapshot is indexed.
- **A parallel reading of a snapshot holds a few parts, not all.** With
  `--jobs`, every part was queued at once and their results given back in
  date order: when the results were taken more slowly than the workers read
  (a computer short of memory starts swapping, and slows down further), the
  parts read ahead piled up in memory without bound, tens of gigabytes on a
  national harvest. A reading now keeps at most two parts per worker ahead
  of the one it gives back (`READ_AHEAD`), and a stop drops the parts not
  started.
- **A few keys are found in the index in a few reads.** A key was searched
  where its bucket lies on disk: on a hard disk, two dozen reads a key, half a
  minute for a few hundred people. Each bucket now has a fence (every 4096th
  key, `NN.fence.npy`, kept in memory), so a key takes a slice of 4096 keys and
  its members; a bucket asked many keys is read whole. An index built without
  fences makes them at its first lookup (or `SnapshotIndex.ensure_fences()`).
- **Through the index, members close together are read in one go.** Two
  members needed of a part were read apart once more than 256 KB lay between
  them: on a hard disk, a national harvest (a fifth of the members) spent its
  time seeking, at a tenth of the disk's speed. A gap shorter than a megabyte
  (what a seek costs in reading) is now read through.
- **`--spill DIR` for a snapshot reading.** What its passes find waited in the
  project's `cache/snapshot/`, then was read back record by record in no
  particular order: on a hard disk, a national harvest spent a day seeking
  there. `--spill DIR` keeps it on another disk, a fast internal one.
- **A long harvest keeps what it collected.** A harvest was one run, written
  whole or not at all: after hours, a crash, a computer switched off or a
  service stop lost everything. It now writes a run every 2,000 people or 10
  minutes, with a checkpoint of the people done. A stop or three failures in a
  row (a spent daily budget) pause it (`harvest_stopped`, `harvest_paused`),
  and `--resume`, or « Resume » in the app, goes on with the people not yet
  done, those whose collection failed included.
- **A stop signal stops a collection cleanly.** Only Ctrl-C (SIGINT) asked a
  collection to stop after the current request; SIGTERM, which `kill` and a
  service manager send, ended it at once, and reached its worker processes
  too. A collection and an index build now take SIGTERM as they take Ctrl-C
  (a second one stops at once), and the snapshot's worker processes ignore
  both, leaving the stop to the main process.
- **A harvest stopped while reading the snapshot goes on from there.** What
  the passes found waited in a temporary file, gone with the job: a stop
  during hours of reading started them over. A harvest now keeps it in
  `snapshot-<key>/` of the spill folder (`RecordStore` on a named file with a
  journal of its records; the passes' state written every 32 parts or two
  minutes): resumed, it skips the passes it finished and the parts it read,
  then indexes again what it kept. The folder is removed when the harvest
  completes.
- **A harvest from a snapshot reads back only the works in its window.** The
  passes keep every work of the people asked (a window is applied to each
  question), and a harvest read each one back, decompressed and parsed it,
  before leaving out those outside its years: two in three, on a national
  harvest. Each work's year is now kept beside its place, and a work outside
  the window is left without being read: the per-person step takes about half
  the time.
- **People put together in worker processes.** From a snapshot, a harvest of
  more than 200 people gathers each person's records unparsed, in order, and
  worker processes parse them and write the run lines; the runs are the same.
  Measured on 2,000 real people: 1.7 ms a work instead of 2.5; this process's
  share still bounds it (to be measured further).
- **Digests of a large harvest in worker processes.** Rebuilding the tables
  digests each new run first (most of the time goes to detecting languages):
  up to four runs at once, a run on one processor, about 5 ms a work, 17 hours
  for a national harvest written as one run. Runs are now digested on every
  processor but two, and a run heavier than 256 MB has its records digested in
  worker processes, in batches given back in order (the same digest; 4.4 times
  faster on 20,000 real works with 16 workers, this process's reading bounding
  it).
