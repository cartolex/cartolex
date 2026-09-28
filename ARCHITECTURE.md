# Architecture

## Data flow

```
            corpus contract                      workspace artifacts
┌─────────────────────────────┐   ┌──────────────────────────────────────────┐
│ corpus slot(s):             │   │ automatic_data/                          │
│   *.txt (one per document)  │   │   raw_keywords_{lang}.csv    (stage 1)   │
│   index CSV                 │──▶│   keywords_global*.csv       (stage 3)   │
│   (last_name, first_name,   │   │   keywords_by_researcher_…   (stage 3)   │
│    unit, txt_path,          │   │   subfields_draft.json       (subfields) │
│    [doc_year, doc_type,     │   │ researcher_index.csv         (roster)    │
│     person attributes])     │   │                                          │
└─────────────────────────────┘   │ lexical_analysis/                        │
                                  │   pca_*.csv, umap_*.csv, clusters_*.csv  │
          RunContext              │   proto_subfields.json, plots (png/svg)  │
 (paths, settings, stop words, …) │   models/*.json + *.npz  (no pickle)     │
                                  └──────────────────────────────────────────┘
```

Every stage is a plain function over files in one **workspace** directory,
called with an explicit `RunContext`; re-runs overwrite their own outputs. No
daemon, no database, no global state: importing a module has no side effect,
and two runs in one process share nothing unless the caller shares it.

## The run context (`cartolex.context`)

| Object | Role |
|---|---|
| `EnginePaths` | Names every file and folder the engine reads or writes. `EnginePaths.for_workspace(base)` builds today's workspace layout, where each corpus slot's index and text folder derive from the slot's id; stages never join path parts themselves |
| `RunContext` | `paths`, `settings` (`KeywordsConfig`), `stopwords` (`StopwordProfile`), `prompt_dir`, `staleness_guard` / `staleness_erase`, `now_year`, `threads` (`ThreadLimits`), `usage` (`UsageRecorder`), `progress`, `cancel`, `ai_client`. `RunContext.for_workspace(base, settings, **overrides)` builds the default |

See `docs/dev/engine.md`.

## Module map

### `cartolex.lexicon` — lexicon construction

| Module | Role |
|---|---|
| `config` | `KeywordsConfig` — the settings dataclass a run context carries, with its ordered registry of `CorpusSlot`s (which sources build the map, which feed the trajectories, which document types each keeps) |
| `extract_raw` | **Stage 1**: corpus loading, per-language detection & split (`KeywordsConfig.corpus_languages`), parsing (cached, optionally in worker processes), noun-phrase candidates scored by TF-IDF with the length bonus |
| `noun_phrases` | The part-of-speech patterns of English, French and Portuguese: word units, classes, nested spans, keys grouped by lemma (see `docs/dev/extraction.md`) |
| `language_models` | The pinned spaCy model of each corpus language: install check (`LanguageModelMissing`), loading, identity |
| `parse_cache` | The parse cache: each text's analysis, keyed by text, model and pattern version (immutable JSON-lines parts) |
| `io_helpers` | Corpus-contract readers; the roster stage `build_researcher_index(ctx)` and `write_roster` |
| `stopwords_config` | `StopwordLists` (immutable, read from `cartolex/_data/stopwords/core.json`) and the per-run `StopwordProfile` |
| `lexical_filters`, `text_utils`, `lang_utils`, `tfidf_utils` | The malformed-term gate, tokenization, language detection, scoring |
| `llm_triage`, `triage_typed`, `llm_filter`, `llm_prompts`, `llm_usage`, `mistral_client`, `credentials` | **Stage 2 (optional)**: LLM keyword triage — term strings only, cached; token usage recorded by the run's `UsageRecorder` |
| `prompt_store` | Loads prompt templates from a prompt directory (default: `cartolex/_data/prompts/`), checked when used |
| `consolidation`, `canonicalization` | **Stage 3**: canonical dedup, scoring, per-entity/unit/global outputs |
| `subfields`, `subfields_edit`, `labels`, `lexicon_store` | Concept → subfield hierarchy (deterministic draft), curation apply/edit, optional LLM translation of display labels |
| `positioning` | Project new documents (a projected set) into a fitted SVD/UMAP space; nearest terms and concept/subfield weights of a projected vector |
| `pdf_text`, `pdf_corpus` | Generic PDF→text extraction with a per-document hook |
| `whitelist` | The axis and person whitelists, read from the explicit files given |
| `occurrences` | Occurrence/concordance scanner mirroring the vectorizer (spans and context snippets of a concept's surface forms in one document) |
| `name_match`, `config_loader`, `utils` | Support utilities (name matching, the workspace override file, name canonicalisation) |

### `cartolex.atlas` — the atlas

| Module | Role |
|---|---|
| `driver` | **Orchestrator**: `run_svd` / `run_clustering` / `run_umap` / `run_lexical_plots` / `run_trajectories`, each taking a `RunContext`; defaults in the immutable `DEFAULTS` (`AtlasDefaults`), with a project's frozen values read per run |
| `io` | TF-IDF matrix assembly from the corpus contract |
| `reducers` | SVD embeddings, UMAP computation |
| `clustering` | Ward clustering of terms (cosine, SVD space) → concepts + proto-subfields |
| `hierarchy` | Concept/subfield hierarchy documents |
| `plots` | Static maps (entities+labs, term clusters, superposed, panels); matplotlib is imported on first use |
| `trajectories` | Per-(entity, time-bin) fingerprints projected into the reference UMAP |
| `map_bundle` | The portable `map_bundle/2` format (build, write, read, validate) — in-memory inputs, explicit destinations |
| `model_files` | The fitted objects a project stores (vectorizer, lexical data, embeddings, SVD, layout model) as a JSON descriptor and an `.npz` array file, never a pickle; loading rebuilds them (a UMAP model is re-fitted and checked against its stored map) |
| `map_merge`, `reconcile`, `map_metrics` | Merging several fitted maps into one (multi-cohort reconciliation + quality metrics) |
| `diagnostics`, `types` | Diagnostics and shared dataclasses |

## Layering rules

- The engine packages import only the standard library, their declared
  dependencies, `cartolex._data` and each other; `cartolex.context` only for
  annotations or inside a function — enforced by `tests/test_layering.py`
  (an AST-level allowlist).
- Consuming applications integrate through: the corpus contract, `RunContext`
  and `EnginePaths`, `KeywordsConfig`, and the public functions above. Anything
  else is private and may change without notice.
- The only permitted network egress is the opt-in Mistral triage
  (anonymized term strings). See AGENTS.md for the full rules.
