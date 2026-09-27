# cartolex

A generic **lexical cartography engine**: given a corpus of raw text documents per
entity (researchers, teams, any document-producing population), it builds a curated
keyword lexicon, clusters it into named subfields, lays entities and concepts out on a
2-D atlas, and can **project new documents into a fitted field**.

The engine is **domain-agnostic and PII-free by construction** — it contains no data
acquisition code and never sees personal metadata beyond the identity columns you put
in the corpus index. It consumes only the *corpus contract* below, whatever produced it.

## Packages

- `cartolex.lexicon` — extraction (noun phrases found with spaCy in English, French
  and Portuguese, TF-IDF, length bonus), optional LLM
  triage (Mistral, term strings only), canonical consolidation and scoring, the
  subfield hierarchy (deterministic draft, curated by hand), positioning of new
  documents, corpus/index helpers.
- `cartolex.atlas` — SVD reduction, Ward clustering (cosine, SVD space), UMAP 2-D
  projection (entities + terms co-projected), trajectories, plots, and the `driver`
  that orchestrates the whole atlas stage.

## The corpus contract

Input is one or more *corpus slots*, declared in the settings
(`KeywordsConfig.corpus_slots`; the default is one slot, `manual`). Each slot is:

- a directory of plain-text files, one per **document**;
- an index CSV with columns `last_name`, `first_name`, `unit` (any grouping label),
  `txt_path` (relative to the index's directory) — plus optional `doc_year`,
  `doc_type` for recency windowing, per-slot type filters and trajectories, and
  any person attribute column (kept by the roster under its own name).

Everything downstream (lexicon, subfields, atlas coordinates, projections) derives
from that contract.

## Documentation

| Document | What it covers |
|---|---|
| [INTEGRATION.md](INTEGRATION.md) | Stage-by-stage guide for driving the full feature set from your own (e.g. FastAPI) application |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Module map, data flow, and the workspace artifact inventory |
| [`examples/synthetic_cohort.py`](examples/synthetic_cohort.py) | Runnable, fully offline end-to-end walkthrough |
| [`examples/merge_two_cohorts.py`](examples/merge_two_cohorts.py) | Runnable, fully offline multi-cohort merge walkthrough (`map_bundle/2`) |
| [`cartolex/_data/prompts/`](cartolex/_data/prompts/README.md) | The editable LLM prompt templates (placeholder defaults — not domain-pinned) |
| [AGENTS.md](AGENTS.md) | Rules for (AI) contributors: layering, no-PII, conventions |
| [CHANGELOG.md](CHANGELOG.md) | Changes of this release line |

## Quickstart (development)

```bash
pip install -e ".[dev]"
pip install --require-hashes -r tools/requirements-models.txt   # the language models
ruff check .
pytest tests/ -q
python examples/synthetic_cohort.py /tmp/demo-workspace
```

The test suite is fully synthetic and offline. The only network call the engine can
make at runtime is the optional Mistral LLM triage (anonymized term batches); skip the
triage stage and the engine is fully offline.

## Status

The 1.0 line is in development (`1.0.0.dev0`). The public API surface is
`cartolex.lexicon` (`KeywordsConfig`, `CorpusSlot`, the stage functions and
errors), `cartolex.context` (`RunContext`, `EnginePaths`), `cartolex.atlas.driver`
and the documented module functions; the corpus-contract columns are part of
that surface.

## License

MIT — see [LICENSE](LICENSE).
