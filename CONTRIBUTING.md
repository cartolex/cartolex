# Contributing to cartolex

Thank you for helping. Read `AGENTS.md` first: its rules apply to every change.

## Set up

cartolex needs Python 3.10 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv venv .venvs/py3.12 --python 3.12
uv pip install -p .venvs/py3.12 -e ".[dev]"
```

## Check a change

```bash
python tools/check.py --quick   # lint, vocabulary, tests on one Python, small reference
python tools/check.py           # everything, before a merge
```

`docs/dev/checks.md` describes each check and how to read a failure.

## A new version

The version is written in three places that must agree: `pyproject.toml`,
`CITATION.cff` and `codemeta.json` (`tests/test_packaging.py` checks it, with the
authors, the licence and the repository). The documentation reads it from
`pyproject.toml` for its « How to cite » page.

## Data

Use the demo generator for anything that needs a corpus:

```bash
python -m cartolex.demo create --size S --seed 0 --out /tmp/demo
```

Never add real names, profiles or texts to the repository, even in a test.

## The code at a glance

cartolex is a generic **lexical cartography engine**: given a corpus of raw text
documents per entity (researchers, teams, any document-producing population), it
builds a curated keyword lexicon, clusters it into named subfields, lays entities
and concepts out on a 2-D atlas, and can project new documents into a fitted field.
The engine is domain-agnostic and holds no data acquisition code; it consumes only
the corpus contract below, whatever produced it.

### Packages

- `cartolex.lexicon` — extraction (noun phrases found with spaCy in English, French
  and Portuguese, TF-IDF, length bonus), optional LLM
  triage (Mistral, term strings only, the `llm` extra), canonical consolidation and scoring, the
  subfield hierarchy (deterministic draft, curated by hand), positioning of new
  documents, corpus/index helpers.
- `cartolex.atlas` — SVD reduction, Ward clustering (cosine, SVD space), a UMAP 2-D
  map of the entities with the terms placed on it by their nearest entities,
  trajectories, plots, and the `driver` that orchestrates the whole atlas stage.
- `cartolex.collect` — collection: a list of people, a folder of documents or a
  corpus; who is who in OpenAlex and the ORCID registry; the harvest of their
  works into the project's source tables; what leaves the computer, said before
  it does (`docs/collection.md`, `docs/privacy.md`).
- `cartolex.project`, `cartolex.build` — the project format and the build of its
  stages; `cartolex.app` — the web app (`cartolex` opens it in the browser,
  `cartolex api` serves it for hosting, see `docs/hosting.md`).

### The corpus contract

Input is one or more *corpus slots*, declared in the settings
(`KeywordsConfig.corpus_slots`; the default is one slot, `manual`). Each slot is:

- a directory of plain-text files, one per **document**;
- an index CSV with columns `last_name`, `first_name`, `unit` (any grouping label),
  `txt_path` (relative to the index's directory) — plus optional `doc_year`,
  `doc_type` for recency windowing, per-slot type filters and trajectories, and
  any person attribute column (kept by the roster under its own name).

Everything downstream (lexicon, subfields, atlas coordinates, projections) derives
from that contract.

### Documentation for developers

| Document | What it covers |
|---|---|
| [docs/](docs/index.md) | The user documentation (introduction, tutorials, guides), the project format and the development pages; built into the app, which links to them (`tools/build_docs.py`; its pictures by `tools/docs_screenshots.py`) |
| [docs/install.md](docs/install.md) | Installing cartolex and its language models, the optional extras, the first run, troubleshooting |
| [INTEGRATION.md](INTEGRATION.md) | Stage-by-stage guide for driving the full feature set from your own (e.g. FastAPI) application |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Module map, data flow, and the workspace artifact inventory |
| [`examples/synthetic_cohort.py`](examples/synthetic_cohort.py) | Runnable, fully offline end-to-end walkthrough |
| [`examples/merge_two_cohorts.py`](examples/merge_two_cohorts.py) | Runnable, fully offline multi-cohort merge walkthrough (`map_bundle/2`) |
| [`cartolex/_data/prompts/`](cartolex/_data/prompts/README.md) | The editable LLM prompt templates (placeholder defaults — not domain-pinned) |
| [AGENTS.md](AGENTS.md) | Rules for (AI) contributors: layering, no-PII, conventions |
| [CHANGELOG.md](CHANGELOG.md) | Changes of this release line |

### A first run from the source

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

### Status

The 1.0 line is in beta, from version 1.0.0. The public API surface is
`cartolex.lexicon` (`KeywordsConfig`, `CorpusSlot`, the stage functions and
errors), `cartolex.context` (`RunContext`, `EnginePaths`), `cartolex.atlas.driver`
and the documented module functions; the corpus-contract columns are part of
that surface.

## Report a problem

Open an issue with the version, the operating system, what you did, what you
expected and what happened. Attach a diagnostic export when the app offers
one; it holds versions and logs, never your data.
