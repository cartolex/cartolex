# Derived results, caches, outputs and logs

## Stages

A build runs stages. Each has a stable id and a plain name, and writes into
`derived/<stage id>/`:

| id | plain name | reads |
| --- | --- | --- |
| `corpus.assemble` | gather the texts | sources, `people.csv`, parameters |
| `keywords.extract` | find keyword candidates | `corpus.assemble`, `stopwords.json` |
| `keywords.triage` | AI clean-up (opt-in) | `keywords.extract`, AI answers |
| `keywords.build` | build the vocabulary | `keywords.extract`, `keywords.triage`, `keywords.csv` |
| `themes.space` | place keywords in a common space | `keywords.build` |
| `themes.group` | group keywords into topics and themes | `themes.space` |
| `themes.apply` | apply your themes | `themes.group`, `themes.json` |
| `map.layout` | draw the map | `themes.apply`, `maps.json` |
| `map.trajectories` | change over time | `map.layout` |
| `overlays.position` | place projected people | `map.layout`, overlays |

## The people's keywords

`keywords.build` writes each person's keywords twice:

| file | what it holds |
| --- | --- |
| `keywords.build/models/person_terms.json` (and its `.npz`) | a model descriptor (`kind` `person_terms`): the people × keywords matrices the space is made of, `score` (the length-boosted TF-IDF) and `score_tf` (the plain term frequencies of the same entries), their `terms` (the vocabulary), their `individuals` (researcher ids) and `keywords_per_person` (`null`: every keyword of the lexicon a person uses; a number: only their best ones) |
| `keywords.build/keywords_by_researcher_restricted.csv` | each person's best keywords, at most 30 (`PERSON_TERMS_LISTED`), the best first: `last_name`, `first_name`, `unit`, `term`, `score`, `len`, `score_tf`, `lang`; what the app and the site show of a person, never what the space reads |
| `keywords.build/concept_terms.csv` | the candidates each keyword of the vocabulary gathers: `concept`, `term`, `language` (what a keyword decision names; the keywords screen's Lexicon tab reads it) |

Each person's row holds every keyword of the vocabulary (the gated list, cut at
`max_keywords`) that their texts use, with its real counts; `keywords_per_person`
(advanced, empty by default) keeps only each person's best ones, as versions before
1.0 did with 30. A run of an earlier version has no `person_terms.json`: the space
then reads the table, which held each person's whole row.

## The theme results

The theme stages write, at every depth of the theme tree (1 to 4), the files
readers use:

| file | what it holds |
| --- | --- |
| `themes.group/themes_draft.json` | the grouping's proposal: a `cartolex-themes/1` tree (the format of `decisions/themes.json`), each keyword on the level its texts support (on the finest level without the comb; too broad for any theme: set aside), `based_on` the grouping's run |
| `themes.group/text_keywords.npz` | with the comb: which keyword each text uses (texts × the vocabulary's keywords, in its row order; `indices`, `indptr`, `shape`), each text once; the editor reads the comb on a curated tree from it |
| `themes.apply/themes_tree.json` | the tree applied, as read: `decisions/themes.json` when it exists, else the proposal |
| `themes.apply/themes_applied.json` | `cartolex-themes-applied/1`: `depth`, `levels` (with names), `source` (`decisions` or `draft`), `weights_basis`, `people_counted`, and every node in tree order with its `id`, `parent`, `level`, `order`, `names`, `color`, `weight`, `share`, `keywords` (on the node itself), `keywords_counted` and `top_keywords` |
| `map.layout/themes_applied.json` | the same, each node with its place on the map (`x`, `y`; `z` on a map in space) |
| `themes.apply/theme_keywords.csv` | one row per placed keyword: `term`, `term_index`, `node`, `level` (its node's), `counts_to` (the levels its usage counts toward), `weight`, `share` |
| `themes.apply/theme_people.parquet` | one row per mapped person and node with a weight: `researcher_id`, `person_id`, `level`, `node`, `weight`, `share` |
| `themes.apply/theme_organisations.parquet` | one row per organisation (`unit`) and node: `unit`, `level`, `node`, `weight`, `share`, `people` |
| `map.trajectories/trajectory_themes.parquet` | one row per person, time window and node: `researcher_id`, `window`, `level`, `node`, `weight`, `share` |
| `overlays.position/<set>/positions.json` | each projected person's `levels`: `[{"level": 1, "nodes": [{"id", "weight", "share"}]}]` |

A person's usage (their TF counts, by default) is divided by its sum over the
keywords the tree places. Their weight on a node is the part on the keywords
counting toward it: the keywords on the node and below it, each down to its
attribution (`0`: nowhere). Their share on a node is that weight over their
weights on the node's level, so a person's shares sum to 1 on each level where
they have usage. An organisation adds up its people. A node's `weight` is the
sum of every person's, and its `share` that sum over the people counted; a
keyword's likewise. Colours: one hue per top-level node, a shade of it for
each node below.

At depth 2 the theme stages also write the engine's two-level documents
(`subfields_draft.json`, `curated.json`, `subfields.json`,
`subfield_weights.csv`, `lexicon_weights.csv`, the `subfields` and `concepts`
of `trajectory_windows.json`, the `themes` and `topics` of `positions.json`).
They serve the numeric reference and the migration of older projects; nothing
new reads them, and at other depths they are absent (or empty).

## Map versions

`map.layout` draws every built map version of `decisions/maps.json` (the pinned
one and those marked `built`), and the stages after it place on each. The pinned
version's files are where they always are; every other built version's are in
`versions/<id>/` of each stage's folder, under the same names, holding only what
is placed on that map:

| file | what it holds |
| --- | --- |
| `map.layout/umap_individuals.csv` | each mapped person (the people of the space, in its rows) and their place: `umap_x`, `umap_y`, and `umap_z` on a map in space |
| `map.layout/umap_terms.csv` | each keyword's place: `term`, `umap_x`, `umap_y` (`umap_z`) |
| `map.layout/umap_labs.csv` | each engine group (`unit`) of enough people: its centre (`umap_x`, `umap_y`, `umap_z`), `size`, and on a flat map its ellipse (`sx`, `sy`, `rho`) |
| `map.layout/umap_diagnostics.json` | the layout's measures (`trustworthiness`, mixing, corona) |
| `map.layout/themes_applied.json` | the applied tree, each node at the mean place of its people |
| `map.trajectories/umap_trajectories.csv` | each time window's point; in `versions/<id>/` only `researcher_id`, `bin_start`, `bin_end`, `n_docs` and the place |
| `map.trajectories/trajectory_windows.json` | each person's windows; in `versions/<id>/` only each window's `key` and place (`x`, `y`, `z`) |
| `overlays.position/<set>/positions.json` | the projected people; in `versions/<id>/<set>/` only `person_id` and the place, in the order of the set's own file |

The space, the stored embeddings (`models/embeddings.json`), the clustered
keywords and the two-level documents are the pinned version's alone. A map in
space adds a third coordinate (`umap_z`, `z`) to every place; a flat map has
none. `run.json` of `map.layout` lists the versions it built in
`measures.versions`: `[{"id", "dimensions", "method", "seconds",
"trustworthiness", "key", "reused", "pinned"}]`, the pinned one first (a kept version keeps the `seconds` it took to draw; the pinned one also keeps
the run's `counts`).

**A version unchanged is not drawn again.** Each built version has a `key`, a
digest of everything its files are made from: the run's inputs (code, stage
version, parameters such as `neighbours` and `link_radius`, the upstream runs,
the project parts) without `decisions/maps.json`, plus the version's own
`layout` (method, seed, parameters, dimensions) and whether it is pinned. When
the generation a run replaces recorded the same key for a version, its files are
copied from it instead of computed (`reused: true`; for the pinned version, every
file of the stage's folder but `versions/`, the amended embeddings, themes and
two-level documents included; for another, its `versions/<id>/` folder), and the
progress says so. Marking another version built, unmarking one, adding a version
or editing a note thus redraws only what changed; a version no longer built is
simply left out. `map.trajectories` and `overlays.position` record a key per
version too (their own inputs without the layout's run, the version's layout key
and the pinned version's): when every version's key is the one their last
generation recorded, the whole stage is copied from it; otherwise it is computed
whole (its pass over the texts serves every version at once).

## `run.json`

Every stage folder holds the record of the run that produced it:

```json
{
  "format": "cartolex-run/1",
  "stage": "keywords.extract",
  "run_id": "20260928T101200Z-7c1e",
  "outcome": "succeeded",
  "started_at": "2026-09-28T10:12:00Z",
  "finished_at": "2026-09-28T10:12:41Z",
  "code": {"version": "1.0.0", "fingerprint": "sha256:…", "stage_version": 1},
  "parameters": {
    "min_people": {"value": 3, "from": "default"},
    "min_texts": {"value": 3, "from": "default"},
    "counting_unit": {"value": "person", "from": "params.json"}
  },
  "inputs": [
    {"kind": "stage", "stage": "corpus.assemble", "run_id": "20260928T101150Z-0a9d"},
    {"kind": "decision", "path": "decisions/stopwords.json", "fingerprint": "sha256:…"}
  ],
  "identity": {"language_models": {"en": "en_core_web_md@3.8.0"}},
  "measures": {"seconds": 41.2, "peak_memory_mb": 812, "own_memory_mb": 406,
               "counts": {"candidates_en": 5214}},
  "warnings": []
}
```

- `parameters` holds every effective value and where it came from: `default`,
  `rule` (computed from the project's sizes; the rule is named), or
  `params.json`.
- `inputs` lists what the stage read: the runs of upstream stages by id, and
  source and decision files by fingerprint. Large tables are fingerprinted by
  their Parquet metadata and row-group statistics, so a check stays fast.
- `code` names the cartolex version and a fingerprint of its sources, for the
  record, and the version of the stage: cartolex raises it when it deliberately
  changes what the stage produces, and a result made by another version needs
  an update. Other code changes leave results up to date.
- `measures` feed the cost estimates of later dry runs: `peak_memory_mb` is the
  most the stage's process and its worker processes held together,
  `own_memory_mb` what its own process held (Linux only). A stage that sizes its
  workers to the job's budget is estimated at most that budget, or at what its
  own process held, scaled, when that is more.

## Is a result up to date? Six states

A stage is in exactly one state, computed from the records alone:

| state | when |
| --- | --- |
| never built | no `run.json` |
| up to date | its last run succeeded, every input fingerprint matches the current file, every upstream run it read is still the current one, its parameters equal today's effective ones, and it was made by the current version of the stage (`code.stage_version`) |
| needs update | its last run succeeded but something it read has changed; the record says what |
| running | a job holds the stage now |
| failed | its last attempt failed or was cancelled (recorded in `derived/.attempts/<stage id>.json`), or its process was killed; the previous results are still in place and still usable |
| skipped | the stage does not apply: AI clean-up switched off, no overlay |

A stage whose upstream needs an update needs one too, and says which upstream
changed. File dates never enter the decision.

## Generations: a killed build loses nothing

A stage never writes into its own folder while it runs:

1. it writes into `derived/.staging/<stage id>.<run id>/`;
2. when it succeeds, `derived/<stage id>/` moves to `derived/.previous/<stage id>/`
   and the staging folder takes its place; the move is recorded in
   `derived/.journal.json` first, so an interrupted swap is completed or undone
   when the project is next opened;
3. when it fails or is cancelled, the staging folder is removed and nothing else
   changes, except the record of the attempt.

The last failed or cancelled attempt of a stage is recorded in
`derived/.attempts/<stage id>.json`: a run record like `run.json`, whose
`outcome` is `failed` or `cancelled` and whose `error` says why. It is removed
when a later run of the stage succeeds. `derived/<stage id>/run.json` is only
ever the record of a successful run.

Long stages also checkpoint by chunk inside their staging folder, so a killed
run resumes from its last chunk instead of starting over. A cancel therefore
ends in one of two plain states: « nothing changed », or « finished before the
cancel ».

## Caches

| folder | holds | if deleted |
| --- | --- | --- |
| `cache/ai/` | AI answers: per-term and per-batch triage caches, translations, label translations, token usage — terms only | the answers are paid for again |
| `cache/parse/` | parsed texts, keyed by the text's SHA-256 and the language model's name and version | texts are parsed again |
| `cache/http/` | service responses, each with its lifetime; failures are never cached | collection fetches again |
| `cache/atlas/` | the app's replies that take long to make (the texts and time windows of the map), and `space-<key>/`: the space stage's people vectors (float32, of length one) and keyword shares by person and by keyword, copied once per run of `themes.space` for the nearest, the comparisons and the people who use a keyword | they are made again when the map is opened |

The AI cache keys are part of the format and never change within a major
version, so a project, or an application migrating its own caches into one,
re-bills nothing.

The files of `cache/ai/`, which an application migrating its own caches writes
as they are:

| file | holds | key |
| --- | --- | --- |
| `triage_term_cache.json` | one AI verdict per term | `typed_v3:<model>:<domain title>:<term>` |
| `triage_batch_cache.json` | the answer to each batch of terms | a hash of the batch's terms, the domain title and the model |
| `usage.json` | the tokens the provider reported for every run of the AI clean-up by API, added up: `prompt_tokens`, `completion_tokens`, `total_tokens`, `updated_at` (answers from the cache count none) | — |

`cache/parse/` holds parsed texts in immutable JSON-lines parts named by their
content's digest; its layout is described with the extraction
(`docs/dev/extraction.md`), and a missing or foreign part is parsed again.

## Outputs

`outputs/` is the default root for what a project hands out: `sites/`,
`figures/`, `tables/`, `bundles/`. Every build goes into a new dated folder and
is never overwritten; a `latest` marker names the newest, and a build older
than the decisions it reflects is flagged. A host application can declare
other roots.

## Logs

`logs/jobs/<job id>.jsonl`: one line per event of a job (start, phase, progress,
warning, end). Logs hold stage names, counts and times, never people's names or
texts.
