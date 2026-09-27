# The engine's run context

Every stage of the engine takes one explicit object, a `RunContext`
(`cartolex/context.py`). It holds everything a stage used to find in
module-level state, so two runs in one process share nothing unless the
caller shares it, and importing an engine module has no side effect: it
creates no folder, reads no file and binds no path.

## `EnginePaths`: every file and folder

`EnginePaths` is a frozen dataclass that names each file and folder the engine
reads or writes: the corpus slots' indexes and text folders, the operator's
files, the outputs of every stage, the fitted models and the figures. Files
that come in families (one per language, one per group, one per corpus slot)
are `PathPattern`s, called with their key: `paths.raw_terms_csv("fr")`,
`paths.group_panel_png("G1")`, `paths.corpus_index_csv("manual")`. Which
slots a run reads is a setting (`KeywordsConfig.corpus_slots`), not a path.

A constructor knows a layout. Today there is one, `EnginePaths.for_workspace(base)`,
the workspace layout of the 0.x line (`automatic_data/`, `manual_data/`,
`lexical_analysis/`, …); another layout only needs another constructor.

**Rule:** a stage never joins path parts itself. It asks `ctx.paths` for what
it reads and writes, and creates the folders it writes into when it runs.
Lower-level functions take the explicit files they need (for example
`write_roster(index_csvs=…, out_csv=…)` or `apply_subfield_files(…)`).

## `RunContext`: the rest of a run

| field | what it is | default |
| --- | --- | --- |
| `paths` | the `EnginePaths` of the run | — |
| `settings` | the settings dataclass (`KeywordsConfig`) | `KeywordsConfig()` |
| `stopwords` | a `StopwordProfile`: the packaged lists and the run's additions and removals | the packaged lists and the workspace override file |
| `prompt_dir` | the folder of prompt templates | the packaged prompts |
| `staleness_guard`, `staleness_erase` | called as `guard(stage, root, confirmed=force)` then `erase(stage, root)` before an atlas stage rewrites its outputs | none |
| `now_year` | the year every date window is counted from | the current year |
| `threads` | `ThreadLimits`: a cap on the numeric libraries' threads and on worker processes | no cap |
| `usage` | the run's `UsageRecorder` of AI token counts | a new recorder |

`RunContext.for_workspace(base, settings, **overrides)` builds the default
context of a workspace folder; any field can be overridden
(`now_year=2026`, `prompt_dir=…`, `threads=…`). When the settings name no
domain, the domain title is read from the workspace override file. Building a
context reads that file and nothing else.

```python
from cartolex.atlas import driver
from cartolex.context import RunContext
from cartolex.lexicon import KeywordsConfig, run_pipeline_stage_1, run_pipeline_stage_3

ctx = RunContext.for_workspace(workspace, KeywordsConfig(min_df=2))
run_pipeline_stage_1(ctx)
run_pipeline_stage_3(ctx)
driver.run_svd(ctx)
```

## Stop words

The packaged lists (`cartolex/_data/stopwords/core.json`) are read once, with
`importlib.resources`, into an immutable `StopwordLists`. A run's additions and
removals (the `add` / `remove` blocks of the workspace override file) produce
a new object; nothing shared is modified. Extraction and triage use
`ctx.stopwords.packaged`; consolidation uses `ctx.stopwords.adjusted` and
`ctx.stopwords.consolidation_blacklist` — where the additions and removals have
always taken effect.

## Packaged data and prompts

The stop-word lists and the prompt templates live in `cartolex/_data/` and are
read with `importlib.resources`, so a checkout, a wheel or a zip all work;
nothing is installed outside the package. Prompt templates are read from
`ctx.prompt_dir` when a stage uses them — never at import — and a missing or
malformed template raises `PromptTemplateError`, naming the file and the
placeholder.

## Model files

A project folder may be copied, shared or uploaded, so nothing the engine
stores may run code when it is read: the package never calls `pickle` or
`joblib` to store or load, and never `numpy.load(..., allow_pickle=True)`
(`tests/test_model_files.py` checks the source). `cartolex.atlas.model_files`
writes each fitted object as two files side by side:

| file | content |
| --- | --- |
| `<name>.json` | the descriptor: format (`cartolex-model/1`), kind, parameters, vocabularies and tables, library versions, and the name, sha256 and array list of its array file |
| `<name>.npz` | the arrays, without pickling (fixed member dates: equal models give equal bytes), read with `allow_pickle=False` |

| model (`ctx.paths`) | written by | rebuilt as |
| --- | --- | --- |
| `vectorizer_json` | consolidation | a `TfidfVectorizer` from its parameters, vocabulary and IDF |
| `lexical_data_json` | SVD | the matrices (CSR arrays), term and person lists and the person table (typed JSON columns) |
| `embeddings_json` | SVD, then the layout | SVD and layout coordinates |
| `svd_model_json` | SVD | a `TruncatedSVD` with its fitted attributes |
| `layout_model_json` | UMAP layout | a UMAP **re-fitted** from its stored inputs, parameters and seed, then checked against the stored map; or an anchored t-SNE rebuilt from its reference set and coordinates |

The UMAP fit and its re-fit run the same calls in the same order on one BLAS
thread (`reducers.UmapModel`), so the same libraries give the same model, bit
for bit, and the same `transform`. When the re-fit does not reproduce the
stored map exactly (other library versions, another machine), the model is
still loaded: the stored coordinates remain the map, only points placed from
then on use the re-fitted model, and a warning gives the largest displacement
relative to the map's extent and what differs (`model.refit_deviation`).
Re-running the layout stage rebuilds an exact model. Making the re-fit
reproducible across machines is an open item.

A `.joblib` file of an earlier release is never read: asking for a model whose
descriptor is absent while such a file is present raises `ModelFileError`,
naming the file and the stage to re-run. A descriptor and an array file that do
not belong together (another run, another kind, a damaged file) raise it too.

## Checks

`tests/test_engine_context.py` checks that importing every module of
`cartolex` in a fresh interpreter creates no file anywhere, that two runs with
different stop-word settings in one process do not affect each other, that the
packaged data loads from a wheel installed in a clean environment, and that a
broken prompt template fails only when it is used. `tests/test_layering.py`
allows the engine packages to import the context only for annotations or
inside a function.
