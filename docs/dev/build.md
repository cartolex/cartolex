# The build

`cartolex.build` runs the stages of a project ({doc}`../format/derived`): it
knows what each stage reads, decides from the records alone whether a result
is up to date, runs what needs it in staging folders that a killed process
cannot damage, and says beforehand what it will do. The engine packages
(`cartolex.lexicon`, `cartolex.atlas`) never import it.

```python
from cartolex.build import build, plan, status
from cartolex.project import Project

with Project.open(folder, write=True) as project:   # repairs an interrupted swap
    for s in status(project).values():                # the six states, with reasons
        print(s.describe())
    print(plan(project).describe())                   # the dry run
    result = build(project, consent=ask, progress=show, cancel=stop)
    print(result.summary())
```

| module | what it holds |
| --- | --- |
| `stages` | `Stage`, `Registry`, `CostModel`, `Estimate`; `STAGES`, cartolex's registry |
| `params` | `ParamSpec`, `Rule` and `RULES`, `CrossCheck`, `ProjectSizes`; reading and resolving parameters; the theme rules |
| `fingerprints` | file, table and code fingerprints |
| `records` | run ids, `run.json` and attempt records, staging markers |
| `validity` | `StageState`, `Reason`, `StageStatus`, `status()` |
| `planning` | `PlanItem`, `BuildPlan`, `plan()` |
| `execution` | `StageContext`, `Progress`, `ConsentRequest`, `BuildResult`, `build()` |
| `machine` | available memory, peak memory, the boot id |
| `enginefiles` | the ownership table: where each engine file lives in a project; `engine_paths()`, `results_paths()` |
| `engine` | the runners of cartolex's stages, the settings they give the engine, `AIAccess`, `engine_registry()` |

The swap of a staging folder into place and its journal are part of the format
and live in `cartolex.project.generations`, so that opening a project for
writing can repair them without the build.

## Declaring a stage

A stage is a frozen `Stage` in a `Registry`. The registry follows the order of
`STAGE_IDS` and checks that the stages fit together: every upstream stage comes
earlier, parameter names are unique and are not global, and every size a rule
needs is reported by an upstream stage.

```python
Stage(
    "themes.group",
    "group keywords into topics and themes",   # the plain name people see
    upstream=("themes.space",),
    decisions=(),                               # decision files it reads
    sources=(),                                 # source tables it reads
    project=(),                                 # parts of project.json it reads
    params=(ParamSpec("top_groups", "int", "about this many groups at the top level",
                      default=15, minimum=2, maximum=500), ...),
    checks=(CrossCheck(...),),                  # refusals that need the project's sizes
    uses=(),                                    # global parameters: seed, year
    provides=(),                                # sizes its counts report
    cost=CostModel("kept_keywords", 1.0, 1e-3, 100.0, 8e-6, memory_exponent=2.0),
    run=runner,                                 # run(ctx) -> counts
    prepare=None,                               # prepare(project) before it runs
    version=1,                                  # raised when what it produces changes
)
```

| field | meaning |
| --- | --- |
| `upstream` | stages whose results it reads; a skipped upstream stage is simply not read |
| `decisions`, `sources`, `extra_inputs` | the files whose fingerprints go into `run.json`; `extra_inputs(project)` adds files found at run time (an overlay's tables) |
| `project` | parts of `project.json` (`languages`, `slots`, `levels`, `overlays`, `identity.ai`, `identity.language_models`…) whose values are recorded in the run's `identity` |
| `opt_in` | the stage runs only when its `enabled` parameter is true; otherwise it is *skipped* |
| `applies(config, params)` | another reason to skip it (no overlay) |
| `network`, `paid`, `consent_note` | the build asks consent first, and says what goes out and what it costs |
| `chunked` | a killed run resumes from its last chunk |
| `provides` | the sizes its counts report: `people`, `texts`, `characters`, `kept_keywords`, `mapped_units` |
| `cost`, `estimator` | how its time and peak memory are estimated |
| `prepare(project)` | called by a build, not a dry run, just before the stage's inputs are fingerprinted: it may write a decision the stage needs (the first map version, the rebased theme tree); what it did goes into the run's warnings |
| `version` | the stage's behaviour version (see below) |

`STAGES` declares cartolex's ten stages, each with the runner that calls the
engine (below). `Registry.with_runners({...})` returns a registry with other
runners (a test's fakes, the AI clean-up with its key), and
`Registry.replace(stage_id, **changes)` changes any other field.

## Parameters

`decisions/params.json` holds only what people set, per stage:
`{"stages": {"themes.group": {"top_groups": 12}}}`. Every other value is the
parameter's default or its rule. `seed` and `pinned_year` sit at the top of the
file and reach the stages that declare them in `uses` as `seed` and `year` (the
year every date window counts back from: the pinned year, or the current one).

Each stage's `run.json` records every effective value and its origin:

```json
"parameters": {
  "depth": {"value": 2, "from": "rule", "rule": "theme_depth"},
  "top_groups": {"value": 12, "from": "params.json", "rule": null},
  "keywords_per_group": {"value": 20, "from": "default", "rule": null},
  "level_sizes": {"value": null, "from": "default", "rule": null}
}
```

No stage of cartolex's declares `seed` today: the engine's steps are
deterministic, and the layout takes the seed of its map version, which the
first build sets from `params.json`.

`load_params(project)` reads the file and refuses, with every reason at once, a
stage it does not know, a parameter a stage does not know, a value of the wrong
type, outside its range or not among its choices. A `CrossCheck` refuses a
value that is only impossible for this project, once the sizes it needs are
known: the plan marks the stage as unable to run, and a run that meets it
records a failed attempt with the reason. When a stage upstream in the same
build will report a size again (the vocabulary is being rebuilt), the plan
leaves the check to the run.

A `ParamSpec` also carries what the screens need, which never changes what a
value may be: its `tier` (`essential`, `intermediate`, `advanced`: how
prominent it is, from the one table `PARAM_TIERS` of `stages.py`, see
{doc}`params-tiers`), a `widget` when its type does not say its control
(`order` for `provider_priority`, `levels` for `level_sizes`; `shape` infers
the others), `keys` for a list that may be given per slot kind (`parts`,
`doc_types`: then its value is one list, or an object with a list, or `null`
when the parameter is nullable, for each kind) and `suggestions`, the items a
control offers for an open list. `GET /api/params` gives them with each
parameter (`tier`, `widget`, `keys`, `suggestions`).

| stage | parameter | default | allowed | on the method screen |
| --- | --- | --- | --- | --- |
| `corpus.assemble` | `parts` | rule `parts_by_slot_kind`: title, abstract for a collection slot; title, abstract, full for a folder or a corpus slot | title, abstract, body, full: one list for every slot, or an object giving each slot kind (`collection`, `folder`, `corpus`) its own | Texts |
| `corpus.assemble` | `doc_types` | rule `doc_types_by_slot_kind`: article, book, chapter, communication, preprint, proceedings, report, review, thesis for a collection slot; every type for a folder or a corpus slot; a slot's own `doc_types` replace it | document types: one list for every slot without its own, or an object giving each slot kind its own (`null`: every type) | Texts |
| `corpus.assemble` | `provider_priority` | folder, openalex, hal, scielo, europepmc, arxiv, biorxiv | provider names; the others follow alphabetically | Texts |
| `corpus.assemble` | `recency_years` | 5 | 0–200; 0 keeps every year | Texts |
| `corpus.assemble` | `duplicate_min_title` | 25 | 1–1 000 characters of the normalised title: shorter titles never make two texts one work | Texts › The same work |
| `corpus.assemble` | `duplicate_year_gap` | 1 | 0–50 years between two texts of the same title and a common author | Texts › The same work |
| `keywords.extract` | `counting_unit` | person | person, text, organisation | Keywords |
| `keywords.extract` | `min_people` | 3 | ≥ 1, and no more than the people whose texts build the lexicon | Keywords |
| `keywords.extract` | `min_texts` | 3 | ≥ 1, and no more than the texts that build the lexicon: a candidate must occur in this many distinct texts (one co-authored text is one text's evidence, however many people signed it) | Keywords |
| `keywords.extract` | `max_share` | 0.6 | 0.01–1 | Keywords |
| `keywords.extract` | `rejects` | true | true, false: candidates on cartolex's list of rejections or in the machine's cache go to the `rejected` band | Keywords |
| `keywords.extract` | `max_candidates` | 1 000 000 | ≥ 1: the window keeps the most used candidates of each language | Keywords |
| `keywords.extract` | `vote` | frequency | frequency, presence, sublinear (`cartolex.lexicon.scoring`) | Keywords › Scoring |
| `keywords.extract` | `length_bonus` | 2 | 0–20: α of `score × (1 + α (L − 1))`; the consolidation, the trajectories and the projected sets use the same α | Keywords › Scoring |
| `keywords.extract` | `max_words` | 5 | 1–12 word units, prepositions and articles included | Keywords › Scoring |
| `keywords.extract` | `of_complement` | false | true, false: English phrases with one « of » complement | Keywords › Scoring |
| `keywords.extract` | `fragment_share` | 1 | 0–1, or none (the rule off) | Keywords › Bands |
| `keywords.extract` | `drop_share` | 0 | 0–1 | Keywords › Bands |
| `keywords.extract` | `keep_share` | 1 | 0–1 | Keywords › Bands |
| `keywords.extract` | `name_share` | 0.5 | 0–1 (only when names are known) | Keywords › Bands |
| `keywords.extract` | `stop_words` | true | true, false | Keywords › Closed words |
| `keywords.extract` | `closed_word_edges` | true | true, false (acts with `stop_words` on) | Keywords › Closed words |
| `keywords.extract` | `foreign_reading` | 2 | 1–50 different closed words of another language (acts with `stop_words` on) | Keywords › Closed words |
| `keywords.extract` | `even_spread` | 0.9 | 0–10, or none (the rule off) | Keywords › Generic words |
| `keywords.extract` | `even_people` | 0.2 | 0–1 | Keywords › Generic words |
| `keywords.extract` | `common_modifier` | none (off) | 0–1, or none | Keywords › Generic words |
| `keywords.triage` | `enabled` | false | true, false | Keywords |
| `keywords.build` | `max_keywords` | 10 000 | ≥ 10 | Keywords |
| `keywords.build` | `nested_threshold` | 1.3 | 1–100 | Keywords |
| `keywords.build` | `ngram_range` | [1, 4] | two whole numbers, 1–12, not going down; the most grows to the longest form | Keywords › Keywords of people and organisations |
| `keywords.build` | `weights_basis` | tf | tf, tfidf | Keywords › Keywords of people and organisations |
| `keywords.build` | `keywords_per_person` | 30 | 1–10 000 | Keywords › Keywords of people and organisations |
| `keywords.build` | `keywords_per_organisation` | 50 | 1–10 000 | Keywords › Keywords of people and organisations |
| `keywords.build` | `keywords_of_field` | 200 | 1–100 000 | Keywords › Keywords of people and organisations |
| `themes.space` | `space_unit` | rule `space_unit_texts`: text | person, text: what the space is fitted on (the texts by default; the people's space may suit a corpus in several languages better, see {doc}`themes-engine`) | Space |
| `themes.space` | `dimensions` | rule `space_dimensions` | 2–1000; a space never has more dimensions than people or keywords (the run says so) | Space |
| `themes.space` | `svd_seed` | 42 | 0–2³² − 1 | Space › The SVD |
| `themes.space` | `svd_iterations` | 5 | 1–100 | Space › The SVD |
| `themes.space` | `svd_algorithm` | randomized | randomized, arpack (at as many dimensions as the matrix's smaller side, the randomized solver runs, with a warning) | Space › The SVD |
| `themes.group` | `depth` | rule `theme_depth` | 1–4 | Grouping |
| `themes.group` | `top_groups` | 15 | 2–500, fewer than the kept keywords | Grouping |
| `themes.group` | `keywords_per_group` | 20 | 2–10 000; the levels must grow from the top | Grouping |
| `themes.group` | `level_sizes` | none | 1 to 4 whole numbers, the groups per level from the top; when set, they replace `depth`, `top_groups` and `keywords_per_group` | Grouping |
| `themes.group` | `cluster_dimensions` | 50 | 2–1000 (at most the space's dimensions are used) | Grouping › Ward's grouping |
| `themes.group` | `exact_ward_limit` | 15 000 | 2–1 000 000 points; exact Ward holds about 8·n² bytes | Grouping › Ward's grouping |
| `themes.group` | `micro_clusters` | none: as many as `exact_ward_limit` | 2–1 000 000, or none | Grouping › Ward's grouping |
| `themes.group` | `micro_seed` | 0 | 0–2³² − 1 | Grouping › Ward's grouping |
| `themes.group` | `comb` | true | true, false; true puts each keyword of the proposal on the level its texts support and sets aside the keywords too broad for any theme ({doc}`themes-engine`) | Grouping › The comb |
| `themes.group` | `comb_theta` | none: calibrated on `comb_grid` | 0–1, or none | Grouping › The comb |
| `themes.group` | `comb_grid` | rule `comb_grid_by_space`: 0.125 to 0.275 by 0.025 on the texts' space, 0.1 to 0.25 on the people's | 1 to 100 numbers, 0–1 | Grouping › The comb |
| `themes.group` | `comb_theta_one_level` | rule `comb_theta_one_level_by_space`: 0.3 on the texts' space, 0.2 on the people's | 0–1 | Grouping › The comb |
| `themes.group` | `comb_sideways` | rule `comb_sideways_by_space`: within_parent on the texts' space, anywhere on the people's | anywhere, within_parent (below the top level, only to a group under the same parent), up_only | Grouping › The comb |
| `themes.group` | `comb_min_texts` | 5 | 1–100 000 | Grouping › The comb |
| `themes.group` | `comb_max_cells` | 50 000 000 | ≥ 1 keywords × finest groups | Grouping › The comb |
| `themes.group` | `own_name_floor` | 0.5 | 0–1 | Grouping › Names |
| `map.layout` | `neighbours` | 8 | 1–500; the trajectories and the projected sets are placed alike | Layout › Placing on the map |
| `map.layout` | `link_radius` | 0.25 | 0–10, a share of the map's radius | Layout › Placing on the map |
| `map.trajectories` | `window_years` | 3 | 1–50 | Layout |
| `map.trajectories` | `min_texts_per_window` | 1 | 1–1000 | Layout |

The layout of a map is not a build parameter: each map version keeps its own
method, seed and settings in `decisions/maps.json`, an input of `map.layout`.
`map.layout`'s own parameters say how points are placed on any map (the
keywords, and after it the time windows and the projected people).

**The theme rules.** With K kept keywords and U mapped units (the units the map
places, the mapped people by default), the depth of the theme tree is
min(⌊log₁₀ K⌋ − 1, ⌊log₁₀ U⌋), clamped to 1–4: 5 000 keywords on 800 people
give two levels (Theme › Topic). The top level has about `top_groups` groups,
the finest about `keywords_per_group` keywords per group, and the levels in
between grow geometrically: `theme_level_sizes(50_000, 3, 15, 20)` is
`(15, 194, 2500)`. At depth 1 the one level follows `top_groups`.

**The space's dimensions.** With P people whose texts build the lexicon, the
space keeps 20 dimensions up to 2 000 people, then 20 × √(P / 2 000), at most
200: 8 000 people give 40, 100 000 give 141. A small project keeps the 20 it
always had (both reference worlds); the measures behind the rule are in
[Sizes and machines](../sizes.md).

**Sizes.** A rule reads `ProjectSizes`: the people whose texts build the
lexicon, their texts, the characters of those texts, the kept keywords and the
mapped units. Each is taken from the counts of the stage that `provides` it, in
its current `run.json`; before that stage has run, `people`, `texts`,
`characters` and `mapped_units` are estimated from the sources (the Parquet
metadata and `people.csv`), and a rule that needs a size nobody knows yet waits:
the plan shows its value as unknown, and the run computes it once the upstream
stage has reported it.

## No hidden parameter

Every engine constant that shapes a result is a parameter of its stage: its
default is the value the engine had before it became a parameter, so existing
results stay what they are. A parameter a stage's `run.json` does not record
(the run was made before it was declared) is not a change when its value is its
default: the stage stays up to date (a value set in `params.json` is a change,
« parameter X is new »), and the method screen does not mark it as not built.
`tests/test_build_params.py` holds the inventory: every declared parameter is a
row of the table above, has its one-line explanation in every interface
language (`param.<stage>.<name>`), and its section (`ParamSpec.section`, the
heading it is shown under on the method screen, `method.section.<id>`).

What became a parameter:

| module | constant | value | parameter |
| --- | --- | --- | --- |
| `project.corpus` | `DUPLICATE_MIN_TITLE`, `DUPLICATE_YEAR_GAP` | 25, 1 | `corpus.assemble.duplicate_min_title`, `.duplicate_year_gap` |
| `lexicon.config` | `KeywordsConfig.max_features` | 1 000 000 | `keywords.extract.max_candidates` |
| `lexicon.scoring` | `ScoringOptions.vote`, `.length_bonus_alpha`, `.of_complement` | frequency, 2, false | `keywords.extract.vote`, `.length_bonus`, `.of_complement` |
| `lexicon.noun_phrases` | `MAX_UNITS`, `FOREIGN_READING` | 5, 2 | `keywords.extract.max_words`, `.foreign_reading` |
| `lexicon.scoring` | `BandRules.fragment_share`, `.drop_share`, `.keep_share`, `.name_share` | 1, 0, 1, 0.5 | `keywords.extract.fragment_share`, `.drop_share`, `.keep_share`, `.name_share` |
| `lexicon.scoring` | `BandRules.stop_words` (and the edges it switched with it) | true | `keywords.extract.stop_words`, `.closed_word_edges` |
| `lexicon.scoring` | `BandRules.even_spread`, `.even_people`, `.generic_spread` | 0.9, 0.2, none | `keywords.extract.even_spread`, `.even_people`, `.common_modifier` |
| `lexicon.config` | `nested_threshold`, `ngram_range`, `weights_basis` | 1.3, (1, 4), tf | `keywords.build.nested_threshold`, `.ngram_range`, `.weights_basis` |
| `lexicon.config` | `top_n_researcher`, `top_n_unit`, `top_n_domain` | 30, 50, 200 | `keywords.build.keywords_per_person`, `.keywords_per_organisation`, `.keywords_of_field` |
| `atlas.reducers` | `TruncatedSVD` seed, power iterations, solver | 42, 5, randomized | `themes.space.svd_seed`, `.svd_iterations`, `.svd_algorithm` |
| `atlas.driver` | `AtlasDefaults.clustering_n_components` | 50 | `themes.group.cluster_dimensions` |
| `atlas.clustering` | `EXACT_WARD_LIMIT`, `micro_cluster_count` (min(points, limit)), `MICRO_SEED` | 15 000, the rule, 0 | `themes.group.exact_ward_limit`, `.micro_clusters`, `.micro_seed` |
| `lexicon.theme_comb` | θ (calibrated), `CALIBRATION` (`THETA_GRID`, `TEXT_THETA_GRID`, `DEFAULT_THETA`, `TEXT_THETA`, `SIDEWAYS`), `MIN_TEXTS`, `MAX_CELLS` | calibrated; per space unit: people 0.1–0.25 by 0.025, θ 0.2 at depth 1, anywhere; texts 0.125–0.275, θ 0.3, within the parent; 5, 5·10⁷ | `themes.group.comb_theta`, `.comb_grid`, `.comb_sideways`, `.comb_theta_one_level`, `.comb_min_texts`, `.comb_max_cells` |
| `lexicon.theme_tree` | `OWN_NAME_FLOOR` | 0.5 | `themes.group.own_name_floor` |
| `atlas.placement` | `K`, `LINK_RADIUS` | 8, 0.25 | `map.layout.neighbours`, `.link_radius` |
| `atlas.driver` | `AtlasDefaults.traj_min_docs_per_bin` | 1 | `map.trajectories.min_texts_per_window` |
| `atlas.driver` | the trajectories' `length_alpha` | 2 | follows `keywords.extract.length_bonus` |
| `app.method` | the layout parameters the method screen offered (`n_neighbors`, `min_dist`, `perplexity`) | | every parameter of a map version's method: UMAP's `metric`, `n_epochs`, `spread`, `set_op_mix_ratio`, `local_connectivity`, `repulsion_strength`, `negative_sample_rate`; t-SNE's `metric` |
| `atlas.tree_layout` | `FILL`, `GAP`, `LEAN`, `SHARP` | 0.62, 0.04, 0.4, 8 | the tree layout's `fill`, `gap`, `lean`, `sharp` (map versions, method screen) |

What stays a constant, and why:

| module | constant | value | why |
| --- | --- | --- | --- |
| `lexicon.scoring` | `ScoringOptions.part_weights` | 1 per part | the corpus gives each text as one part (`full`): a weight per part cannot act |
| `lexicon.config` | `extraction_n_jobs`, `llm_max_concurrent` | 1, 4 | worker processes and calls in flight: the output is the same whatever their number |
| `lexicon.config` | `refined_top_n` | 0 | recorded only: the consolidation cuts at `max_keywords` |
| `lexicon.config`, `lexicon.llm_triage` | `llm_model`, `llm_batch_size`, `llm_temperature`, `llm_min_score`, `llm_timeout_s` | | the AI clean-up's settings: the model is `identity.ai`, the others belong to the AI steps' own parameters |
| `project.corpus` | `VERSION_RANK` | article, review, chapter, communication, proceedings | the version of record is one rule at collection (`collect.merge`) and at reading: a build parameter would split them |
| `project.corpus` | `PART_ORDER`, `PARTS_BATCH` | title, abstract, body; 20 000 | the order a text's parts are written in, and a batch size |
| `lexicon.extract_raw`, `lexicon.parse_cache` | `PARSE_BATCH`, `MAX_PIECE_CHARS`, `PART_SIZE` | 64, 10 000, 1000 | the parser's batches and pieces (cut at paragraph ends) and the cache's files: memory bounds |
| `lexicon.text_utils`, `lexicon.lexical_filters`, `lexicon.lang_utils` | the split-word repair (`min_real` 5, `max_passes` 2), the malformed-string gate, the language markers | | repair and recognition of the texts, not a choice of method; the stop words are edited in `decisions/stopwords.json` |
| `atlas.reducers` | `TruncatedSVD`'s oversampling and normaliser, openTSNE's initialisation and neighbour search | scikit-learn's and openTSNE's defaults | solver internals: `svd_iterations` and `svd_algorithm` are the SVD's accuracy knobs |
| `atlas.clustering` | `MICRO_INIT`, the mini-batch k-means' batches (max(1024, 4m)), first sample (3m), iterations (50) | | the micro-clustering's solver, its seed and count being parameters |
| `atlas.placement` | `CHUNK_CELLS`, `_MARGIN`, the weights' 64 bisection steps | 2²², 10⁻⁹ | memory bound, exact ranking margin and convergence: the positions do not depend on them |
| `atlas.tree_layout` | `_ROUNDS` | 300 | the relaxation stops as soon as no disc overlaps; the cap only bounds its time |
| `atlas.driver` | `umap_n_components` | 2 | the map is two-dimensional |
| `atlas.driver` | `clustering_target_subfields`, `top_n_terms_per_cluster`, `min_researchers_per_lab` | 30, 10, 3 | they shape `proto_subfields.json`, the clusters' table and the groups' layout table, which no stage, screen or site reads |
| `atlas.driver` | `cluster_target_weight`, `respect_clusters` | 0.3, off | a semi-supervised UMAP the build never asks for |
| `build.engine` | `TSNE_FROM_PEOPLE` | 1000 | the rule of the first map version's method; each version records its method, and another can be tried and pinned |
| `lexicon.theme_tree` | `TOP_KEYWORDS`, `MAX_DEPTH`, `CHUNK_BYTES` | 15, 4, 64 MB | how many keywords a theme lists (its name is its most used keyword; the list only breaks a tie between siblings' names), the format's depth, a chunk size |
| `lexicon.theme_comb` | `TEXT_CHUNK` | 2000 | texts read at a time |
| `atlas.driver`, `build.engine` | `traj_top_k_terms`, the projected sets' `top_k` and `near_terms` | 8, 10, 10 | how many keywords a window or a projected person lists |
| `atlas.driver` | `TRAJECTORY_CHUNK`, `atlas.blocks.BLOCK_CELLS` | 1000, 2²⁴ | chunk sizes |
| `app.method` | `NEIGHBOURS`, `PREVIEW_SAMPLE`, `SCORE_BINS` | 10, 800, 24 | the method screen's measures, not results |
| `build.params` | the rules' constants (`SPACE_DIMENSIONS`, `SPACE_FROM_PEOPLE`, `SPACE_MAX_DIMENSIONS`, the depth formula, the parts and document types by slot kind) | | defaults computed by a rule: the parameter they set (`dimensions`, `depth`, `parts`, `doc_types`) takes any value |

## Run records and fingerprints

A run id is the UTC start time and six random hex digits
(`20260928T101200Z-7c1e2a`); ids sort by time. `run.json` (see
{doc}`../format/derived`) holds:

- `inputs`: the run id of each upstream stage read, and the fingerprint of each
  declared file that exists (a file that does not exist is not listed, so its
  appearance is a change too). Decision files are fingerprinted by the SHA-256
  of their bytes. A Parquet table up to 64 MB likewise; a larger one by its
  size and its footer, which holds the schema and, for every row group and
  column, the row counts, byte sizes, offsets and statistics (a rewritten table
  is seen as changed without reading its data).
- `identity`: the values of the parts of `project.json` the stage declares
  (`languages`, `slots`, `ai`, `language_models`…).
- `code`: the cartolex version, a SHA-256 over the source and data files of
  the `cartolex` package (the demo generator excepted), computed once per
  process, and the stage's own version (`Stage.version`, from 1). A change of
  code alone leaves results up to date (`StageStatus.code_changed` says so); a
  deliberate change of what a stage produces raises its version, and every
  result made by the earlier version then needs an update.
- `measures`: wall seconds, peak memory and counts. The counts include the
  stage's cost driver (the size its cost grows with), so a later dry run can
  scale the measures to the project's new size. The peak memory is the process's
  peak resident memory during the stage: exact on Linux, where the kernel's
  high-water mark is reset when the stage starts; elsewhere the process's peak so
  far, an upper bound. The largest worker process that ran is added.

## The six states

`status(project)` gives each stage one `StageState`, computed from its
`run.json`, its attempt record, its staging folders and the current
fingerprints, never from file dates:

1. **running** when a staging folder's marker names the process that holds the
   project's lock now;
2. **skipped** when the stage does not apply;
3. **failed** when an attempt record exists (the last attempt failed or was
   cancelled) or a staging folder was left by a killed process;
4. **never built** when there is no `run.json`;
5. **needs update** when something changed, with one `Reason` per change;
6. **up to date** otherwise.

The reasons name what changed: the stage's version (« cartolex changed how this
stage works (version 1 → 2) »), an input file (changed, added, removed), a
parameter (`min_people: 3 → 2 (from params.json)`, `year: 2026 → 2027
(default)`), a part of `project.json`, or an upstream stage (rebuilt, without
results, now skipped, now with results, itself needing an update, failed or
running). A stage whose upstream stage needs an update needs one too.

A result computed by other code of the same stage version is not out of date:
`StageStatus.code_changed` says so, as information. A reader that opens a project while another process
swaps a stage may see that stage without results for an instant; a writer
never does.

## Generations and safe re-runs

A stage writes only into its staging folder,
`derived/.staging/<stage>.<run id>/`, which holds its results and two things of
its own:

- `.staging.json`: the run id, the process, the host and the project lock it
  runs under, the boot of the machine, and a key: a digest of the code and the
  stage's version, the effective parameters, the upstream runs, the input fingerprints and the
  `project.json` parts it reads;
- `.chunks/`: for a chunked stage, `chunks.json` (the number of chunks), a
  folder per chunk for its own files, and an empty `<n>.done` file per finished
  chunk.

When the stage succeeds, `cartolex.project.generations.swap_in` writes the
journal `derived/.journal.json` (the stage, the new run, the run it replaces),
writes `run.json` into the staging folder, removes `.staging.json` and
`.chunks/`, moves `derived/.previous/<stage>/` aside, moves `derived/<stage>/`
to `derived/.previous/<stage>/`, moves the staging folder to
`derived/<stage>/`, removes the attempt record, the journal, then the
generation moved aside. Each move is a rename inside `derived/`. A writer's
`Project.open(write=True)` calls `generations.recover`: when the new generation
is whole (a `run.json` with the journal's run id), the swap is completed;
otherwise it is undone and the previous results are put back. It also removes
what a swap moved aside and staging folders without a marker.

When a stage fails or is cancelled, its staging folder is removed and the
attempt is recorded in `derived/.attempts/<stage>.json`. When its process is
killed, the staging folder stays. The next run of the stage looks for it: when
the stage is chunked, its key matches (same code, parameters and inputs) and the
machine has not restarted since (checkpoints are not forced to disk), the run
continues in that folder and skips the chunks already done, and its `run.json`
says so in `warnings`; otherwise the folder is removed and the run starts over.

## The dry run and the build

`plan(project, targets=None, force=(), budget_mb=None)` returns a `BuildPlan`:
one `PlanItem` per stage considered (the targets and every stage upstream of
them; every stage by default), in build order, with its action (`run`, `keep`,
`skip`), its reasons, its effective parameters as known now, an estimate of its
time and peak memory, whether it asks consent, whether it will resume a killed
run, and why it cannot run, if it cannot. A stage runs when it was never built,
needs an update, failed, is forced, or an upstream stage runs.
`BuildPlan.describe()` says all this in words.

An estimate scales the part of the stage's last measures above its fixed part
by what the stage's `CostModel` gives now over what it gave then (from the
sizes the run recorded in its counts), or, without a previous run, uses the
model itself: a fixed part plus a part per unit of the driver, to a power,
plus optionally a linear part of a second size (the extraction costs per text
as well as per character); a stand-in size while the driver is unknown (the
vocabulary from the people, twelve per person, at most 10 000). The cost
models of `STAGES` are fitted with `tools/cost_fit.py` on each stage run in a
fresh process (`tools/scale_study.py`) on the demo worlds and on streamed
worlds of 10³, 10⁴ and 10⁵ people ([Sizes and machines](../sizes.md)): from
10³ to 10⁵ people every stage's estimate is within a factor of 2 of its
measure, in time and in peak memory (`tests/test_build_costs.py` holds the
measures). The AI clean-up's model is a guess, its cost being the provider's.
A stage whose estimated peak memory exceeds the budget cannot run, nor can
anything downstream of it, unless `build(allow_over_budget=True)` or a list of
stage ids allows it. The budget is `budget_mb`, or by default the memory
available now (`/proc/meminfo` on Linux, `vm_stat` on macOS,
`GlobalMemoryStatusEx` on Windows) plus what the process already holds (read on
Linux; elsewhere nothing is added, which errs on the safe side), since a peak is
measured for the whole process.

`build(project, targets, …)` takes the same arguments, computes the same plan
and runs exactly its `run` items, one at a time, in order:

- **Consent.** Before anything runs, `consent(ConsentRequest)` is called for
  each stage that reaches the network or costs money, once. Without a callback,
  or on a refusal, an opt-in stage (the AI clean-up) is skipped for this build
  as if it were switched off: the plan is made again without it
  (`plan(..., off=...)`), it is listed with the skipped stages, and the stages
  after it run without it, as they do when it is off; `params.json` does not
  change, so the next build asks again. Any other such stage, and everything
  downstream of it, does not run (`BuildResult.refused` says why); the rest
  runs.
- **Progress.** `progress(Progress)` receives « phase k of n » events: the
  stage, its plain name, how far the stage is, how far the whole build is
  (weighted by the estimates) and a message. Neither fraction ever goes back,
  and a heartbeat repeats the last event whenever the stage has been silent for
  `heartbeat_s` seconds (5 by default, at most 10).
- **Cancel.** `cancel` is a `threading.Event`, checked before each stage and
  before each chunk. The stage it stops removes its staging folder and records a
  cancelled attempt; `BuildResult.summary()` says « nothing changed » or
  « finished before the cancel » with the stages that did.
- **Failure.** A stage that raises removes its staging folder and records a
  failed attempt; the build stops there, and the stages after it do not run.
- **Log.** `logs/jobs/<job id>.jsonl` records the start, each phase, each stage's
  end (run id, seconds, peak memory, counts), and the end: stage names, counts
  and times, never texts or names. The job id is a new run id, or the
  `job_id` a job runner passes (letters, digits, `-` and `_`): the build then
  appends to the log the runner started.

One build runs at a time on a project: `plan` raises `BuildBusy` while a job
runs a stage it would run.

## The engine on a project

`cartolex.build.engine` holds one runner per stage. A runner builds the engine's
`RunContext` for its stage and calls the engine; the engine never learns that a
project exists (it imports neither `cartolex.build` nor `cartolex.project`).

| stage | the engine's work |
| --- | --- |
| `corpus.assemble` | `cartolex.project.corpus.assemble_corpus`: one index and one text per document for each fit slot, and for each projected set, with `people.csv` naming the `person_id` behind each engine identity; each person's attributes (the filter columns of an imported list) follow the index's columns, so the roster, and the map, can colour and filter people by them (an attribute named like a column of the contract is written `person_<name>`) |
| `keywords.extract` | extraction (`run_pipeline_stage_1`) |
| `keywords.triage` | the AI triage (`run_pipeline_stage_2_llm`), through `AIAccess` |
| `keywords.build` | consolidation (`run_pipeline_stage_3`), then the person roster; `decisions/keywords.csv` becomes the engine's exclusion, keep and merge files first |
| `themes.space` | the SVD space (`run_svd`) |
| `themes.group` | the term clustering (`run_clustering`, the finest level), the levels above it and the proposal tree (`draft_themes`); at depth 2 also the two-level draft (`draft_subfields`) |
| `themes.apply` | `apply_themes` on the curated tree (below) or on the proposal, at any depth; at depth 2 also `apply_subfields` on the two-level document |
| `map.layout` | the layout of the pinned map version (`run_umap`: the people fitted, the keywords placed by their nearest people), then the themes applied again on the map (each node gets a position) |
| `map.trajectories` | `run_trajectories`: time bins and windows placed by their nearest people, with each window's weights on every theme level |
| `overlays.position` | each projected set projected with `cartolex.lexicon.positioning` and placed by its nearest people, with its weights on every theme level: `<set>/positions.json` |

The figures and the portable bundle are outputs, not build stages.

**Where the engine's files go.** `enginefiles.ENGINE_FILES` gives every field of
`EnginePaths` a place: a file a stage writes (`Owned`, relative to its folder),
a file of the project (`FromProject`: the triage prompt override in
`decisions/prompts/`, the AI caches in `cache/ai/`, the parse cache in
`cache/parse/`), the running stage's own folder (`OwnFolder`), or nothing
(`NotProvided`: a workspace's operator files, the frozen atlas parameters, the
API key file, and the figures). `engine_paths(stage, folders, root)` points a stage's own files into
its staging folder and every other file at its latest writer among the stages
the run may read (those upstream of it, directly or not, that have results);
a file with no such writer points into `<staging>/.unavailable/`, where nothing
exists, and a runner fails if the engine writes there. Three files are
*amended*: the layout adds its coordinates to the stored embeddings, refreshes
the clustered keyword table, and applies the themes again so they carry their
places on the map. `map.layout` starts from copies of the first two in its own
folder, and the stages after it read its versions. The tests check that every
field has exactly one place and that each stage folder holds only its own
files.

**Settings.** `keywords_settings(project.json, …)` gives the engine its corpus
slots (in the project's order), languages and domain title; the parameters map
onto the engine's settings; a stage reads the parameters of the stages before
it from their `run.json`, so it runs with the values the results it reads were
made with.

| parameter | engine setting |
| --- | --- |
| `corpus.assemble.parts`, `.provider_priority` | `assemble_corpus(parts=…, provider_priority=…)`; `parts` is a list, or the rule's parts by slot kind |
| `corpus.assemble.recency_years` | `KeywordsConfig.kw_recency_years` |
| `corpus.assemble.duplicate_min_title`, `.duplicate_year_gap` | `assemble_corpus(duplicate_min_title=…, duplicate_year_gap=…)`, then `duplicate_groups(min_title=…, year_gap=…)`; the corpus view groups the copies with the values of `params.json` |
| the `year` | `RunContext.now_year` |
| `keywords.extract.min_people`, `.min_texts`, `.max_share`, `.counting_unit` | `KeywordsConfig.min_df`, `.min_texts`, `.max_df`, `.counting_unit` |
| `keywords.extract.rejects` | the rejection snapshot the runner writes (`EnginePaths.rejects_json`): cartolex's list and the machine's cache (`EngineOptions.rejects_folder`), minus the terms a person decided on; empty when false |
| `keywords.extract.max_candidates`, `.vote`, `.length_bonus`, `.max_words`, `.of_complement`, `.foreign_reading` | `KeywordsConfig.max_features`, `.vote`, `.length_bonus_alpha`, `.max_units`, `.of_complement`, `.foreign_reading`, which `extract_raw.options_of` turns into `ScoringOptions` |
| `keywords.extract.fragment_share`, `.drop_share`, `.keep_share`, `.name_share`, `.stop_words`, `.closed_word_edges`, `.even_spread`, `.even_people`, `.common_modifier` | `KeywordsConfig.band_*`, then `BandRules.fragment_share`, `.drop_share`, `.keep_share`, `.name_share`, `.stop_words`, `.closed_edges`, `.even_spread`, `.even_people`, `.generic_spread` |
| `keywords.build.max_keywords` | `KeywordsConfig.global_top_n` |
| `keywords.build.nested_threshold`, `.ngram_range`, `.weights_basis`, `.keywords_per_person`, `.keywords_per_organisation`, `.keywords_of_field` | `KeywordsConfig.nested_threshold`, `.ngram_range`, `.weights_basis`, `.top_n_researcher`, `.top_n_unit`, `.top_n_domain` |
| `themes.space.dimensions`, `.space_unit` | `run_svd(svd_n_components=…, space_unit=…)` |
| `themes.space.svd_seed`, `.svd_iterations`, `.svd_algorithm` | `run_svd(svd_seed=…, svd_iterations=…, svd_algorithm=…)`: scikit-learn's `TruncatedSVD(random_state, n_iter, algorithm)` |
| `themes.group.cluster_dimensions`, `.exact_ward_limit`, `.micro_clusters`, `.micro_seed` | `run_clustering(n_components=…, ward=WardOptions(limit, micro, seed))`; the same `WardOptions` cut the levels above (`level_groups(ward=…)`) |
| `themes.group.comb_theta`, `.comb_grid`, `.comb_sideways`, `.comb_theta_one_level`, `.comb_min_texts`, `.comb_max_cells`, `.own_name_floor` | `draft_themes(comb_options=CombOptions(theta, grid, one_level, min_texts, max_cells, sideways), own_floor=…)`; the method screen's calibration and the comb's suggestions on a curated tree read the options the grouping recorded (`cartolex.build.engine.comb_options`), and the copilot's themes bundle carries them (`data/context.json`, `grouping`) for its kit's grouping, comb and names |
| the theme levels | every level: `draft_themes(level_sizes=…)`; the finest: `run_clustering(n_concepts=…)`; at depth 2 the top level of the two-level draft: `draft_subfields(n_subfields=…)` |
| `map.layout.neighbours`, `.link_radius` | `run_umap(neighbours=…, link_radius=…)`, `run_trajectories(…)`, `load_positioning_models(…)`: `MapAnchors(k, link_radius)` |
| `map.trajectories.window_years`, `.min_texts_per_window` | `run_trajectories(bin_years=…, min_docs_per_bin=…)`, with `length_alpha` from `keywords.extract.length_bonus` |
| the pinned map version | `run_umap(umap_random_state=seed, …)` with its method's parameters: for `umap` (`n_neighbors`, `min_dist`, `metric`, `n_epochs`, `spread`, `set_op_mix_ratio`, `local_connectivity`, `repulsion_strength`, `negative_sample_rate`, `layout`), `umap_layout="tsne"` with `tsne_perplexity` and `metric` for `tsne`, `umap_layout="tree"` with `tree_fill`, `tree_gap`, `tree_lean`, `tree_sharp` for `tree` (`fill`, `gap`, `lean`, `sharp` in the version) |
| `identity.ai.model` | `KeywordsConfig.llm_model` |
| `identity.domain_title`, `identity.domain_description` | `KeywordsConfig.domain_title`, `.domain_description` (the AI's only context besides the terms) |
| `decisions/stopwords.json` | the stop-word profile: every word added or removed, in any language, extends or shrinks the list of words that are never keywords |

The engine builds every level of the depth the parameters give, from 1 to 4
(see [Themes in the engine](themes-engine.md)): a tree of one level has its
keywords on its themes directly.

**Map versions.** Before the first layout, `map.layout`'s `prepare` adds and
pins map version `v1` (layout `umap`, the seed of `params.json`). A rebuild uses
the pinned version; `cartolex versions` pins another or adds one with another
seed (`--try-another --seed N`) or another layout method (`--method umap|tsne|tree`,
which starts from that method's defaults). A method the stage does not know, a
parameter its method does not take, or `tsne` without the optional openTSNE
package is refused with the reason ([layouts](layouts.md)).

**The curated theme tree.** Before `themes.apply` runs, its `prepare` rebases
`decisions/themes.json` onto the current vocabulary when it is based on
another one (`cartolex.project.themes.rebase`): a new keyword goes to the node
of the tree that holds a strict majority of the other keywords of its group in
the new proposal, looked for from the finest level up
(`cartolex.build.engine.proposed_places`), else aside, both marked « to
check », and the result is saved as a new version. The stage applies the tree
at its depth; at depth 2 it also converts it with
`cartolex.project.themes_curated.to_curated` into the two-level document and
applies that, for the two-level outputs.

**A host's options.** `engine_registry(ai, EngineOptions(prompt_dir=…,
stopword_overlay=…))` gives every stage a host application's prompt folder
(replacing the packaged templates) and its function words (`{"add": {"en":
[…]}, "remove": {…}}`), applied under each project's own
`decisions/stopwords.json`, which wins where both name a word. Like the
packaged lists, they are the host's code: changing them does not by itself
make a result out of date; force the stages that read them.

**The AI clean-up** needs an `AIAccess`: the provider's key, or a client of
one's own (`client_factory`, called like the provider SDK's client; the tests
and the reference run answer with a fake model), and optionally the calls in
flight. `engine_registry(AIAccess(...))` returns cartolex's stages with it;
without one, the stage is refused with the reason. `project.json` must name the
provider (`mistral`) and model. Answers are cached in
`cache/ai/triage_batch_cache.json` and `cache/ai/triage_term_cache.json`, keyed
as always, so a forced second run asks nothing.

**Progress and cancel.** Every runner gives the engine's `RunContext` a
`progress` callable (at most one event per percent) and a `cancel` callable
(the build's event). Extraction, triage and consolidation report through them
and stop at their next report after a cancel; the SVD, clustering, layout and
trajectory steps report between their steps, and the trajectories per person
in their time windows. What cannot be interrupted: one layout fit, one SVD, one
clustering, and a single AI call in flight (the triage stops before the next).

## Writing a runner

A runner is `run(ctx: StageContext) -> Mapping[str, int] | None`. It:

- writes its results into `ctx.out`, and nowhere else in the project;
- reads an upstream stage's results from `ctx.folder(stage_id)`, the project's
  files from `ctx.layout`, its parameters from `ctx.params` (effective values,
  with `seed` and `year` when it declares them), the sizes from `ctx.sizes`, and
  the `project.json` parts it declared from `ctx.identity`;
- reports progress with `ctx.progress(fraction, message)` and warnings with
  `ctx.warn(message)`;
- in a long loop, iterates `for i in ctx.chunks(n):` and keeps each chunk's
  files in `ctx.chunk_dir(i)`; a chunk counts as done when the loop asks for the
  next one, and a cancel is checked before each chunk (`ctx.check_cancel()`
  checks at any other point);
- returns its counts, including the sizes it `provides`.

## Tests

`tests/test_build_engine.py` builds an XS demo project with cartolex's own
stages, then checks the ownership table, the stages that need an update after
`stopwords.json`, `params.json`, `people.csv` or `themes.json` change, a
curated theme tree applied, the AI clean-up through an injected client (and
from its cache), a cancel and a killed process on real stages, and the command
line. The numeric reference is also run through a project build
(`docs/dev/reference.md`). `tests/test_build_themes.py` builds the S demo world
at depths 1 to 4 with the command line, checks at depth 2 that the tables of
any depth equal the two-level outputs exactly, and reads each depth's map
bundle back.

`tests/_build_fakes.py` declares small fake stages on the real stage ids, each
writing a result that depends only on what it read and logging its calls
outside the project. The tests run the six states and their transitions, the
reasons, parameter validation and rules, the dry run against the run, consent
and budget refusals, progress and cancel, and kill a build process
(`os._exit`) at stage boundaries, inside a chunk and during a swap, then check
that every stage folder holds one whole generation and that the next build
gives the results of an uninterrupted one. `tests/test_generations.py` covers
every step of the swap and its recovery in both directions.
