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
  "code": {"version": "1.0.0", "fingerprint": "sha256:…"},
  "parameters": {
    "min_people": {"value": 3, "from": "default"},
    "counting_unit": {"value": "person", "from": "params.json"}
  },
  "inputs": [
    {"kind": "stage", "stage": "corpus.assemble", "run_id": "20260928T101150Z-0a9d"},
    {"kind": "decision", "path": "decisions/stopwords.json", "fingerprint": "sha256:…"}
  ],
  "identity": {"language_models": {"en": "en_core_web_md@3.8.0"}},
  "measures": {"seconds": 41.2, "peak_memory_mb": 812, "counts": {"candidates_en": 5214}},
  "warnings": []
}
```

- `parameters` holds every effective value and where it came from: `default`,
  `rule` (computed from the project's sizes; the rule is named), or
  `params.json`.
- `inputs` lists what the stage read: the runs of upstream stages by id, and
  source and decision files by fingerprint. Large tables are fingerprinted by
  their Parquet metadata and row-group statistics, so a check stays fast.
- `measures` feed the cost estimates of later dry runs.

## Is a result up to date? Six states

A stage is in exactly one state, computed from the records alone:

| state | when |
| --- | --- |
| never built | no `run.json` |
| up to date | its last run succeeded, every input fingerprint matches the current file, every upstream run it read is still the current one, and its parameters equal today's effective ones |
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

The AI cache keys are part of the format and never change within a major
version, so a project, or an application migrating its own caches into one,
re-bills nothing.

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
