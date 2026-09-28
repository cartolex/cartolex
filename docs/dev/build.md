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
    uses=("seed",),                             # global parameters: seed, year
    provides=(),                                # sizes its counts report
    cost=CostModel("kept_keywords", 1.0, 1e-3, 100.0, 8e-6, memory_exponent=2.0),
    run=runner,                                 # run(ctx) -> counts
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

`STAGES` declares cartolex's ten stages with runners that raise
`StageNotConnected`. The engine is connected with
`STAGES.with_runners({"keywords.extract": run_extract, ...})`, which returns a
new registry; `Registry.replace(stage_id, **changes)` changes any other field.

## Parameters

`decisions/params.json` holds only what people set, per stage:
`{"stages": {"themes.group": {"top_groups": 12}}}`. Every other value is the
parameter's default or its rule. `seed` and `pinned_year` sit at the top of the
file and reach the stages that declare them in `uses` as `seed` and `year` (the
year every date window counts back from: the pinned year, or the current one).

Each stage's `run.json` records every effective value and its origin:

```json
"parameters": {
  "seed": {"value": 20260928, "from": "params.json", "rule": null},
  "depth": {"value": 2, "from": "rule", "rule": "theme_depth"},
  "top_groups": {"value": 12, "from": "params.json", "rule": null},
  "keywords_per_group": {"value": 20, "from": "default", "rule": null}
}
```

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
| `themes.space` | `dimensions` | 20 | 2–1000, fewer than the kept keywords and the people |
| `themes.group` | `depth` | rule `theme_depth` | 1–4 |
| `themes.group` | `top_groups` | 15 | 2–500, fewer than the kept keywords |
| `themes.group` | `keywords_per_group` | 20 | 2–10 000; the levels must grow from the top |
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
- `code`: the version, and a SHA-256 over the source and data files of the
  `cartolex` package (the demo generator excepted), computed once per process.
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

The reasons name what changed: an input file (changed, added, removed), a
parameter (`min_people: 3 → 2 (from params.json)`, `year: 2026 → 2027
(default)`), a part of `project.json`, or an upstream stage (rebuilt, without
results, now skipped, now with results, itself needing an update, failed or
running). A stage whose upstream stage needs an update needs one too.

A result computed by other code is not out of date: `StageStatus.code_changed`
says so, as information. A reader that opens a project while another process
swaps a stage may see that stage without results for an instant; a writer
never does.

## Generations and safe re-runs

A stage writes only into its staging folder,
`derived/.staging/<stage>.<run id>/`, which holds its results and two things of
its own:

- `.staging.json`: the run id, the process, the host and the project lock it
  runs under, the boot of the machine, and a key: a digest of the code, the
  effective parameters, the upstream runs, the input fingerprints and the
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
`CostModel` (a fixed part plus a part per unit of the driver, to a power). The
cost models of `STAGES` are first guesses, to be calibrated on measured runs.
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

`tests/_build_fakes.py` declares small fake stages on the real stage ids, each
writing a result that depends only on what it read and logging its calls
outside the project. The tests run the six states and their transitions, the
reasons, parameter validation and rules, the dry run against the run, consent
and budget refusals, progress and cancel, and kill a build process
(`os._exit`) at stage boundaries, inside a chunk and during a swap, then check
that every stage folder holds one whole generation and that the next build
gives the results of an uninterrupted one. `tests/test_generations.py` covers
every step of the swap and its recovery in both directions.
