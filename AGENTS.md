# AGENTS.md — rules for contributors and AI coding agents

Everyone who changes this repository, person or AI agent, follows these rules.
`CONTRIBUTING.md` explains how to set up and submit a change.

## 1. What this repository is

cartolex maps a scientific field from the texts of the people who work in it:
collection of texts, keywords, themes, a two-dimensional atlas, and an offline
site to share it. It is generic: it knows nothing about any institution, any
evaluation procedure or any particular field.

## 2. Hard rules

- **Synthetic data only.** Code, tests, fixtures, docs and screenshots never
  contain real people's names, real profiles, real identifiers (ORCID,
  OpenAlex, HAL) or real corpora. Use the demo generator (`cartolex.demo`) or
  hand-written fabricated data.
- **Generic words.** Code, identifiers, comments, docs, tests, file names,
  commit messages and tag messages describe generic behaviour. Describe a bug
  by its mechanism ("a document whose font maps to lone surrogates"), never by
  who met it. `tools/vocab_scan.py` checks the tree and the commit messages
  against a list of project-specific terms kept by the maintainers.
- **Network.** Only declared features may reach the network: collection from
  open bibliographic services, and the optional AI clean-up, which sends term
  strings only, never texts or people. Tests never reach the network: the test
  suite blocks every connection except loopback.
- **Explicit paths and settings.** Functions take the paths they read and
  write, and settings come from the run context, never from environment
  variables or the current directory. Importing a module never writes a file.
- **Layering.** The engine packages never import the app, the collection
  code, the site builder or web frameworks. `tests/test_layering.py` enforces
  it.
- **Numeric continuity.** A change that can move an engine output is compared
  with the stored runs (`tools/reference/`, see `docs/dev/reference.md`): the
  baseline of this tree must be reproduced, and every difference from the
  released engine's reference is explained. A difference is removed, or
  explained in the change's description, before it is accepted. Never edit a
  stored run to make a comparison pass; the baseline changes only through
  `check_reference.py --update-baseline` with a reason.
- **AI cache keys never change.** `tests/test_ai_cache_keys.py` pins them:
  changing one would make existing users pay again for answers they already
  have.
- **Type hints and docstrings** on public functions. Docs change with the code
  they describe.

## 3. Checks

`python tools/check.py` must pass before any merge (see `docs/dev/checks.md`).
It runs ruff, the vocabulary scan, the tests on every supported Python, the
reference comparison and the strict docs build. Lint with the pinned ruff from
the project's development extra, not another version.

## 4. Commits

Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`,
`chore:`), in generic words. One logical change per commit; the checks pass
at every commit. A commit has one author, the person who makes it: no
`Co-Authored-By` or other attribution trailer (`tools/vocab_scan.py` refuses
them).
