# Integrating cartolex into your application

This guide targets a Python web application (e.g. FastAPI) that wants the full
cartolex feature set on its own corpus: keyword lexicon → named concepts →
2-D atlas → projection of new documents. It follows the calls a consuming
application makes, stage by stage.

Start from the runnable, fully offline walkthrough:

```bash
python examples/synthetic_cohort.py /tmp/demo-workspace
```

## 1. Install

```bash
# pinned git dependency (private repo — use a deploy key in CI)
pip install "cartolex @ git+ssh://git@github.com/<org>/cartolex@v0.1.0"
# or, during development
pip install -e ../cartolex
```

Two engine packages under the `cartolex` namespace: `cartolex.lexicon` (extraction, LLM triage,
consolidation, subfields, positioning) and `cartolex.atlas` (SVD, concept
clustering, UMAP, trajectories, plots, and the `driver` that orchestrates
them). Everything is pure Python; heavy lifting is numpy/scikit-learn/umap.

## 2. The workspace and the corpus contract

The engine is **file-based and stateless between calls**: every stage reads
and writes inside one *workspace* directory that you own — one workspace per
cohort/study. No database, no daemon. Minimal layout you must provide, for
the default corpus slot `manual`:

```
<workspace>/
  manual_index.csv                    # the slot's corpus index (see below)
  automatic_data/
    corpus_manual/*.txt               # one plain-text file per DOCUMENT
  manual_data/                        # optional overrides (prompts, stopwords)
```

**Corpus slots.** The documents come from one or more *corpus slots*, an
ordered registry in the settings (`KeywordsConfig.corpus_slots`). Each
`CorpusSlot(id, fit=True, trajectory=True, doc_types=None)` is an index CSV
plus the text files it lists; the workspace layout derives both from the id
(`<workspace>/<id>_index.csv`, `automatic_data/corpus_<id>/`). `fit` slots
build the map (extraction, consolidation, roster); `trajectory` slots feed the
time trajectories; `doc_types` keeps only documents of those types (a document
without a type always passes; `None` or an empty list reads every document).
Slot order is document order: a person's documents are concatenated slot by
slot. The default is one slot, `manual`:

```python
from cartolex.lexicon.config import CorpusSlot, KeywordsConfig

cfg = KeywordsConfig(corpus_slots=(
    CorpusSlot("reports", doc_types=("annual_report",)),   # its own type filter
    CorpusSlot("papers"),
    CorpusSlot("history", fit=False),                      # trajectories only
))
```

Index columns (every slot):

| column | required | meaning |
|---|---|---|
| `last_name`, `first_name` | yes | entity identity (person, team, any label pair) |
| `unit` | yes | grouping label — drives lab/group aggregation on the atlas |
| `txt_path` | yes | path to the document text, relative to the index's directory |
| `doc_year` | no | enables recency windows and time trajectories |
| `doc_type` | no | free consumer-defined document kind, filtered by the slot's `doc_types` |
| `source` | no | the document's origin (written by the PDF corpus builder) |
| any other column | no | a person attribute (a rank, a start year, …): the roster keeps its first non-empty value per person under the same name, to colour the person map (`color_persons_by`) or define cohorts (`cohort_by`) |

⚠️ **Recency gotcha:** if you provide `doc_year`, the default
`KeywordsConfig.kw_recency_years = 5` excludes older documents from keyword
construction. Set it to `0` to use the full history. Undated rows are never
excluded.

## 3. The pipeline, stage by stage

All hyperparameters flow through one dataclass, carried by an explicit **run
context** that every stage takes:

```python
from cartolex.context import RunContext
from cartolex.lexicon import KeywordsConfig

cfg = KeywordsConfig(
    min_df=3,                    # lower for small corpora
    kw_recency_years=0,          # 0 = no recency window
    domain_title="sociologie du travail",   # your domain title, used in LLM prompts
    # Language model (defaults below reproduce the historical FR/EN behaviour):
    reference_language="en",                 # canonical concept-key language (the pivot)
    corpus_languages=("fr", "en"),           # detected ingest streams
    display_languages=("fr", "en"),          # translation skins at display surfaces
)
ctx = RunContext.for_workspace(workspace, cfg)   # paths, settings, stop words, prompts, …
```

`ctx.paths` (an `EnginePaths`) names every file the engine reads or writes in
the workspace; stages never build a path themselves. Other context fields —
`now_year`, `prompt_dir`, the staleness hooks, thread limits — can be
passed to `RunContext.for_workspace(...)` as keyword arguments; see
`docs/dev/engine.md`. Build one context per run: nothing is shared between
contexts, so several runs (or workspaces) can live in one process.

**Language model.** Three settings generalize the former hard-wired FR/EN split
and hidden English pivot (the defaults above reproduce it exactly):

- `corpus_languages` — the languages extracted. Each becomes one TF-IDF stream
  and one `raw_keywords_<lang>.csv`; a paragraph detected in another language is
  dropped.
- `reference_language` — the canonical concept-key language the LLM triage
  normalizes every term into (the pivot), and the language the subfield/concept
  `label` is written in. It need **not** be one of `corpus_languages` (an
  all-Portuguese corpus keyed to an English pivot is `reference_language="en"`).
- `display_languages` — extra translation skins carried alongside the reference
  label: `term_<lang>` columns in `keywords_global_refined_pairs.csv` and
  `label_<lang>` on subfields/concepts, selected at display time.

A Portuguese study is `reference_language="pt", corpus_languages=("pt","en"),
display_languages=("pt","en")`; the LLM prompts are rendered in the reference
language. Merging cohorts (§7) requires them to share a `reference_language`.

### 3.1 Extraction (noun phrases, TF-IDF)

```python
from cartolex.lexicon import run_pipeline_stage_1
run_pipeline_stage_1(ctx)
```

Each paragraph is language-detected and routed to its `corpus_languages` stream
(any subset of `en`, `fr`, `pt`; default `("fr", "en")`; text in a
non-configured language is dropped). Each stream is parsed by the language's
spaCy model and its noun phrases, scored by TF-IDF, are written to
`automatic_data/raw_keywords_<lang>.csv`. Offline. The model of every language
with text must be installed (pinned versions in
`cartolex/lexicon/language_models.py`; otherwise `LanguageModelMissing` gives
the install command); parsed texts are kept in `ctx.paths.parse_cache_dir`, so
a re-run parses only new texts. See `docs/dev/extraction.md`.

### 3.2 Keyword triage — optional LLM stage

```python
from cartolex.lexicon.llm_triage import run_pipeline_stage_2_llm
run_pipeline_stage_2_llm(ctx, api_key=None)   # None → MISTRAL_API_KEY env
```

- Sends **only batches of keyword term strings + your domain title** to the
  Mistral API — never document text or entity metadata. Responses are cached
  in the workspace, so re-runs are cheap and reproducible.
- Key resolution: explicit argument → `MISTRAL_API_KEY` →
  `ctx.paths.api_key_json` (`manual_data/llm_api.json`).
- **Prompts are files, not code.** The default templates live at the repo
  surface in [`cartolex/_data/prompts/`](cartolex/_data/prompts/README.md) — placeholder defaults meant to
  be edited (or forked) to match your domain; nothing else in the engine pins
  a field of research. The shipped texts lean natural-science — see the
  prompts README before running on humanities/social-science corpora. To use
  your own copies, pass `prompt_dir=` to `RunContext.for_workspace`; templates
  are checked when a stage uses them (a missing file or placeholder raises
  `PromptTemplateError`). A per-workspace override of the triage template
  also exists (`ctx.paths.triage_prompt_override_txt`, placeholders preserved).
- **Prompt anchoring (optional):** ship a
  `<workspace>/config/domain_catalog.json` (`ctx.paths.domain_catalog_jsons`;
  schema and loader in `cartolex/lexicon/domain_catalog.py`) and set
  `cfg.domain_id` to anchor
  the prompts on your domain's reference keywords. Without a catalog,
  prompts run unanchored — perfectly fine for most deployments.
- Skipping this stage entirely is supported — consolidation then works from
  the raw extraction (the synthetic example does exactly that).

### 3.3 Consolidation and scoring

```python
from cartolex.lexicon import run_pipeline_stage_3
run_pipeline_stage_3(ctx)
```

Canonicalization, dedup, per-entity/per-unit/global scoring. Writes
`keywords_global*.csv`, `keywords_by_researcher_restricted.csv`,
`keywords_by_unit_restricted.csv`, `keywords_domain_restricted.csv` (the top
`top_n_domain` keywords of the whole domain) and the settings snapshot
`keywords_hyperparams.json` (with the corpus slot registry). Domain
stopword/merge overrides: a JSON of
`add`/`remove` lists in the workspace override file (`ctx.paths.overrides_json`,
`manual_data/overrides.json`, which may also carry the `domain_title`; schema in
`cartolex/lexicon/config_loader.py`). They are read when the context
is built, into the run's own `StopwordProfile`, and apply at consolidation;
another run in the same process is not affected.

### 3.4 Entity roster

```python
from cartolex.lexicon.io_helpers import build_researcher_index
build_researcher_index(ctx)          # the fit slots' indexes → ctx.paths.roster_csv
```

### 3.5 The atlas — SVD → concepts → UMAP → plots

```python
from cartolex.atlas import driver

driver.run_svd(ctx)                 # svd_n_components=None → default
driver.run_clustering(ctx)          # Ward concepts + proto-subfields
driver.run_umap(ctx)                # 2-D projection (entities + terms)
driver.run_lexical_plots(ctx)       # static PNG/SVG/PDF maps; color_persons_by="<column>",
                                    # panel_groups=[<group>, …] for one panel per group
driver.run_trajectories(ctx)        # optional; the trajectory slots' dated documents,
                                    # bins end at ctx.now_year; cohort_by="<numeric column>"
```

Outputs under `<workspace>/lexical_analysis/`: `umap_individuals.csv` (the
atlas coordinates your frontend renders), `umap_terms.csv`,
`clusters_terms.csv`, `proto_subfields.json`, `umap_labs.csv` (per-`unit`
aggregates, labelled by the `unit` value), diagnostic JSON, and the fitted models under `models/`
(`svd`, `umap`, `embeddings`, …: each a JSON descriptor with an `.npz` array
file, see below) — these are what later projection uses. UMAP parameters
(`n_neighbors` is auto-clamped for small cohorts) are keyword arguments on
`run_umap`; their defaults are `driver.DEFAULTS`.

**Model files never hold a pickle.** A workspace may be copied, shared or
uploaded, so `cartolex.atlas.model_files` stores every fitted object as a JSON
descriptor (parameters, vocabularies, library versions, the sha256 of its
arrays) and an `.npz` file read with `allow_pickle=False`, and rebuilds the
object on load. A UMAP model is re-fitted from its stored inputs, parameters and
seed, then checked against the stored map: the same libraries give the same
model bit for bit (a few seconds for a few hundred people; keep the loaded
models when you project often). If the installed libraries do not reproduce the
map, loading raises `ModelFileError` rather than placing points on another map;
re-running the layout stage rebuilds it. `.joblib` files of earlier releases
are never read: loading raises `ModelFileError`, naming the file and the stage
to re-run.

Two optional context fields (set by a consuming application; safe to
ignore standalone):

```python
ctx = RunContext.for_workspace(
    workspace, cfg,
    staleness_guard=guard,           # guard(stage, root, confirmed=force) before an atlas stage
    staleness_erase=erase,           # erase(stage, root) after the guard
)
```

Groups are labelled by their value of the `unit` column everywhere (the
aggregate table, the maps, the panels): write the label you want to show there.

### 3.6 Subfields — deterministic concept hierarchy, curated by hand

```python
from cartolex.lexicon.subfields import draft_subfields, apply_subfields, load_subfields
from cartolex.lexicon.subfields_edit import set_term_status, move_term, validate_doc
```

`draft_subfields(ctx)` writes `ctx.paths.subfields_draft_json`: concepts are
the clustering stage's term clusters, subfields a Ward cut over their centroids, every node
labelled by its dominant keyword, colours stamped. No LLM (the two-pass
Mistral curation was retired in 0.7.0 — it produced weak names the operator
redid by hand anyway). The operator curates the draft in an editor — rename,
move, merge, and set **term statuses**: `subfield_only_terms` (credited to the
subfield share only) and `ride_along_terms` (displayed, no share) —
`subfields_edit` holds the pure operations and `validate_doc` the invariants.
`apply_subfields(ctx)` stamps the curated hierarchy (`ctx.paths.subfields_curated_json`,
else the draft) onto the atlas and computes the three-status lexicon weights.

### 3.7 Projecting new documents into a fitted field

The stored SVD and layout models (`models/svd.json`, `models/umap.json`)
transform any new document into the existing map: vectorize the new text
against the fitted vocabulary, `svd.transform(...)`, then `umap.transform(...)`. The primitives
live in `cartolex.lexicon.positioning` (`load_positioning_models(ctx)`,
`project_text(...)`, and the helpers that describe a projected vector:
`scored_top_terms_for_vector`, `subfield_svd_centroids`,
`concept_svd_centroids`, `subfield_weights_for_vector`) and
`cartolex.atlas.reducers`. This places a *projected set* — people or
documents outside the fitted cohort — on the map without refitting. (A
packaged one-call helper is on the roadmap — until then, follow
`positioning.py`.)

## 4. Wiring it into FastAPI

- **Run stages in worker processes, not request handlers.** Extraction and
  UMAP take seconds-to-minutes at realistic corpus sizes; run each stage as a
  job outside the request. The engine keeps no module-level state —
  each run carries its own `RunContext` — so several workspaces can be
  processed in one process; a process pool with one stage per task remains
  the natural fit for long stages. `ctx.threads` caps a run's threads and
  worker processes.
- **Progress reporting:** long stages log `[N%]`-prefixed messages
  (`logging`, logger names `cartolex.lexicon.*` / `cartolex.atlas.*`).
  Parse them from the job's log stream to drive a progress bar.
- **Idempotence:** stages overwrite their own outputs atomically and re-runs
  are safe; gate expensive re-runs on your own dirty-flags (or pass the
  context's staleness hooks).
- **State = the workspace directory.** Back it up, version it, or throw it
  away wholesale; nothing lives outside it except the pip-installed code and
  the stop-word lists shipped in the package (`cartolex/_data/stopwords/core.json`).

## 5. "All features" checklist

| Feature | Needs | Status standalone |
|---|---|---|
| TF-IDF lexicon (FR/EN) | corpus contract only | ✅ offline |
| LLM keyword triage | Mistral key; domain title; optional prompt override | ✅ opt-in |
| Consolidation + scoring | — | ✅ offline |
| Concept clustering + proto-subfields | — | ✅ offline |
| Subfield hierarchy (deterministic draft, curated by hand) | — | ✅ offline |
| Translated display labels | Mistral key | ✅ opt-in |
| 2-D atlas (entities, terms, groups) | — | ✅ offline |
| Static maps (PNG/SVG/PDF) | — | ✅ offline |
| Time trajectories | `doc_year` column | ✅ offline |
| Projection of new documents | fitted models | ✅ primitives (helper on roadmap) |
| **Interactive web atlas (JS)** | — | ❌ not in the engine — render `umap_*.csv` with your own frontend |
| Data acquisition, demographics, geo maps, encrypted exports | — | ❌ deliberately out of scope (application layer) |

**Errors.** Stages raise ordinary exceptions a caller can catch, never
`SystemExit`: `SettingsError` (a `ValueError`) for unusable settings, such as a
corpus slot registry without a `fit` slot; `CorpusError` when the corpus slots
give nothing to read; `FileNotFoundError` when an earlier stage's output is
missing, with the stage to run in the message. A corpus language with no text
(an English-only corpus with the default two languages) is skipped with a
warning.

## 6. Ground rules

The engine is **PII-free by construction** — names never leave the workspace
you control, and the only permitted network egress is the opt-in Mistral
triage (anonymized term strings). Keep it that way in your integration: see
AGENTS.md for the layering and transmission rules the test suite enforces.

## 7. Merging multiple cohorts (`map_bundle/2`)

Several independently-built cohorts (departments, institutes, studies…) can be
merged into one joint map. Each producer serializes its inputs as a *cohort
bundle* — a directory or `.zip` — via `cartolex.atlas.map_bundle`; the merge
host reads the bundles back and runs the merge pipeline. Runnable offline
walkthrough: `python examples/merge_two_cohorts.py`.

### The bundle contract

| file | required | contents |
|---|---|---|
| `bundle_meta.json` | yes | `{"schema": "map_bundle/2", "cohort_id": str, "build_date": str, "counters": {"n_entities", "n_terms", "n_term_rows"}, "producer": str, "engine_version": str, "profile": {…opaque, may be absent…}}` — unknown keys are preserved on read (round-trip safe) |
| `entity_terms.csv` | yes | long-form `entity_id,term,tf,score` (score column may be empty) — the portable sparse encoding of `X_tf`; zeros are absent rows |
| `entities.csv` | yes | `entity_id,unit` (+ any extra columns pass through untouched into the dataframe — consumers add their own facets) |
| `vocabulary.csv` | yes | `term,n_entities,tf_total,score_total` (score_total may be empty) |
| `taxonomy.json` | optional | a `map_taxonomy/1` doc (curated concepts as labels + term strings; validated by `validate_taxonomy`) |
| `decisions.json` | optional | a `map_reconcile_decisions/1` adjudication cache |
| any other file | — | ignored by the reader (forward compatibility) |

CSV/JSON only — no pickles, no parquet. `write_bundle` is byte-deterministic
(sorted rows, sorted JSON keys, fixed zip timestamps), so bundles diff and
hash cleanly.

### The consumption pipeline

```python
from cartolex.atlas.map_bundle import read_bundle
from cartolex.atlas.map_merge import (
    anchor_concepts, assemble_joint_matrix, balanced_svd, weight_matrix,
)
from cartolex.atlas.reconcile import build_cohort_senses, reconcile

bundles = [read_bundle(p) for p in bundle_paths]        # validates each
inputs = [b.to_cohort_input() for b in bundles]         # deterministic pivot

decisions = {}  # or reconcile.load_decisions(path) to resume prior adjudication
senses = [build_cohort_senses(s.cohort_id, s.terms, s.X_tf) for s in inputs]
table = reconcile(senses, embedder=my_embedder, decisions=decisions)

joint = assemble_joint_matrix(inputs, table)            # pooled TF on senses
W = weight_matrix(joint)                                # TF·IDF·length·L2
emb = balanced_svd(W, joint.cohorts, joint.researcher_ids)
meta, anchors = anchor_concepts(
    {b.cohort_id: b.taxonomy for b in bundles if b.taxonomy},
    table, emb.Z_senses, joint.sense_ids,
)
```

`cartolex.atlas.map_metrics` then provides the validation battery
(do-no-harm kNN overlap, mixing/conductance, bridge pairs, …).

### The adjudication discipline

`reconcile` is conservative: gray-zone merge/split pairs default to **split**
and are queued in `table.pending`, each with a stable `pair_key`. A human (or
an LLM you drive) adjudicates them into a decisions cache —
`reconcile.save_decisions` / `reconcile.load_decisions`
(`map_reconcile_decisions/1`, `{pair_key: "merge"|"split"}`) — which is fed
back on the next `reconcile(...)` run and can travel inside a bundle as
`decisions.json`. Keep the cache under version control: it is what makes a
merge reproducible and auditable.

### Split of responsibilities

**The schema is the engine's; the `profile` is yours.** The engine
serializes, validates structure (schema tag, referential integrity, finite
non-negative `tf`, no duplicate `(entity_id, term)` pairs, vocabulary
consistency) and nothing more. Entity-id schemes, anonymization/k-anonymity
policy, tiering, and any envelope files you wrap around the core are consumer
concerns — stamp such policy into the opaque `profile` object of
`bundle_meta.json` (the engine never interprets it), and remember the engine
reader ignores unknown files in a bundle, so your envelope can live alongside
the core files.

## 0.5.0 consumer note

> **0.5.0**: `LexicalData.X`/`X_tf` are scipy CSR matrices (previously dense ndarrays). Consumers that need dense data must use `cartolex.atlas.types.to_dense()`; per-term totals via `col_sums()`. `np.asarray()` on a scipy matrix silently yields a 0-d object array — never use it directly on `X`.
