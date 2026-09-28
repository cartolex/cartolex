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

| stage | parameter | default | allowed |
| --- | --- | --- | --- |
| `corpus.assemble` | `parts` | title, abstract | title, abstract, body, full |
| `corpus.assemble` | `provider_priority` | folder, openalex, hal, scielo, europepmc, arxiv, biorxiv | provider names; the others follow alphabetically |
| `corpus.assemble` | `recency_years` | 5 | 0–200; 0 keeps every year |
| `keywords.extract` | `counting_unit` | person | person, text, organisation |
| `keywords.extract` | `min_people` | 3 | ≥ 1, and no more than the people whose texts build the lexicon |
| `keywords.extract` | `max_share` | 0.6 | 0.01–1 |
| `keywords.triage` | `enabled` | false | true, false |
| `keywords.build` | `max_keywords` | 10 000 | ≥ 10 |
| `themes.space` | `dimensions` | 20 | 2–1000; a space never has more dimensions than people or keywords (the run says so) |
| `themes.group` | `depth` | rule `theme_depth` | 1–4 |
| `themes.group` | `top_groups` | 15 | 2–500, fewer than the kept keywords |
| `themes.group` | `keywords_per_group` | 20 | 2–10 000; the levels must grow from the top |
| `themes.group` | `level_sizes` | none | 1 to 4 whole numbers, the groups per level from the top; when set, they replace `depth`, `top_groups` and `keywords_per_group` |
| `map.trajectories` | `window_years` | 3 | 1–50 |

The layout of a map is not a build parameter: each map version keeps its own
method, seed and settings in `decisions/maps.json`, an input of `map.layout`.

**The theme rules.** With K kept keywords and U mapped units (the units the map
places, the mapped people by default), the depth of the theme tree is
min(⌊log₁₀ K⌋ − 1, ⌊log₁₀ U⌋), clamped to 1–4: 5 000 keywords on 800 people
give two levels (Theme › Topic). The top level has about `top_groups` groups,
the finest about `keywords_per_group` keywords per group, and the levels in
between grow geometrically: `theme_level_sizes(50_000, 3, 15, 20)` is
`(15, 194, 2500)`. At depth 1 the one level follows `top_groups`.

**Sizes.** A rule reads `ProjectSizes`: the people whose texts build the
lexicon, their texts, the characters of those texts, the kept keywords and the
mapped units. Each is taken from the counts of the stage that `provides` it, in
its current `run.json`; before that stage has run, `people`, `texts`,
`characters` and `mapped_units` are estimated from the sources (the Parquet
metadata and `people.csv`), and a rule that needs a size nobody knows yet waits:
the plan shows its value as unknown, and the run computes it once the upstream
stage has reported it.

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

An estimate scales the stage's last measures by the ratio of its cost driver
now to what it was then, or, without a previous run, uses the stage's
`CostModel` (a fixed part plus a part per unit of the driver, to a power; a
stand-in size while the driver is unknown, the vocabulary from the people). The
cost models of `STAGES` are fitted on fresh builds of the S and L demo worlds
(one process, whole-process peak memory; the layout's fixed time is mostly the
compilation of the layout library in a new process): on those builds every
stage's estimate is within a factor of 2 of its measure (1.9 at most, for a
stage of under a second), and the totals within 15 %. The AI clean-up's model is a guess, its cost being the provider's.
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
  each stage that reaches the network or costs money; without a callback, or on
  a refusal, that stage and everything downstream of it do not run
  (`BuildResult.refused` says why); the rest runs.
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
  and times, never texts or names.

One build runs at a time on a project: `plan` raises `BuildBusy` while a job
runs a stage it would run.

## The engine on a project

`cartolex.build.engine` holds one runner per stage. A runner builds the engine's
`RunContext` for its stage and calls the engine; the engine never learns that a
project exists (it imports neither `cartolex.build` nor `cartolex.project`).

| stage | the engine's work |
| --- | --- |
| `corpus.assemble` | `cartolex.project.corpus.assemble_corpus`: one index and one text per document for each fit slot, and for each projected set, with `people.csv` naming the `person_id` behind each engine identity |
| `keywords.extract` | extraction (`run_pipeline_stage_1`) |
| `keywords.triage` | the AI triage (`run_pipeline_stage_2_llm`), through `AIAccess` |
| `keywords.build` | consolidation (`run_pipeline_stage_3`), then the person roster; `decisions/keywords.csv` becomes the engine's exclusion, keep and merge files first |
| `themes.space` | the SVD space (`run_svd`) |
| `themes.group` | the term clustering (`run_clustering`) and the subfield draft (`draft_subfields`) |
| `themes.apply` | `apply_subfields` on the curated tree (below), or on the draft |
| `map.layout` | the layout of the pinned map version (`run_umap`: the people fitted, the keywords placed by their nearest people), then the themes applied again on the map |
| `map.trajectories` | `run_trajectories`: time bins and windows placed by their nearest people |
| `overlays.position` | each projected set projected with `cartolex.lexicon.positioning` and placed by its nearest people: `<set>/positions.json` |

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
| `corpus.assemble.parts`, `.provider_priority` | `assemble_corpus(parts=…, provider_priority=…)` |
| `corpus.assemble.recency_years` | `KeywordsConfig.kw_recency_years` |
| the `year` | `RunContext.now_year` |
| `keywords.extract.min_people`, `.max_share`, `.counting_unit` | `KeywordsConfig.min_df`, `.max_df`, `.counting_unit` |
| `keywords.build.max_keywords` | `KeywordsConfig.global_top_n` |
| `themes.space.dimensions` | `run_svd(svd_n_components=…)` |
| the theme levels | the top level: `draft_subfields(n_subfields=…)`; the finest: `run_clustering(n_concepts=…)` |
| `map.trajectories.window_years` | `run_trajectories(bin_years=…)` |
| the pinned map version | `run_umap(umap_random_state=seed, …)` with its layout parameters (`n_neighbors`, `min_dist`, `metric`, `layout`…) |
| `identity.ai.model` | `KeywordsConfig.llm_model` |
| `identity.domain_title`, `identity.domain_description` | `KeywordsConfig.domain_title`, `.domain_description` (the AI's only context besides the terms) |
| `decisions/stopwords.json` | the stop-word profile: every word added or removed, in any language, extends or shrinks the list of words that are never keywords |

The engine builds two theme levels, themes over topics. A tree of one level
keeps about `keywords_per_group` keywords per topic beneath its themes, and a
tree of three or four keeps its top and finest levels; the run says so in its
warnings.

**Map versions.** Before the first layout, `map.layout`'s `prepare` adds and
pins map version `v1` (layout `umap`, the seed of `params.json`). A rebuild uses
the pinned version; `cartolex versions` pins another or adds one with another
seed.

**The curated theme tree.** Before `themes.apply` runs, its `prepare` rebases
`decisions/themes.json` onto the current vocabulary when it is based on
another one (`cartolex.project.themes.rebase`): a new keyword goes to the node
of its draft topic when the tree has it, else aside, both marked « to check »,
and the result is saved as a new version. The stage converts the tree with
`cartolex.project.themes_curated.to_curated` and applies it. A tree of
another depth than two is refused with the reason, until the engine applies
other depths.

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
(`docs/dev/reference.md`).

`tests/_build_fakes.py` declares small fake stages on the real stage ids, each
writing a result that depends only on what it read and logging its calls
outside the project. The tests run the six states and their transitions, the
reasons, parameter validation and rules, the dry run against the run, consent
and budget refusals, progress and cancel, and kill a build process
(`os._exit`) at stage boundaries, inside a chunk and during a swap, then check
that every stage folder holds one whole generation and that the next build
gives the results of an uninterrupted one. `tests/test_generations.py` covers
every step of the swap and its recovery in both directions.
