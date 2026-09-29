# The AI copilot

The copilot is the third route of AI help, beside the handoff (a prompt and
a list to paste in a chat) and the API (keyword strings sent by the build).
cartolex exports **one zip**, the *bundle*, that an assistant able to run code
works from on its own: the task, a guide, the data, and cartolex's own
engine as a wheel. The assistant studies and changes the theme tree or judges
the candidate keywords with cartolex's grouping, layouts and measures, asks the
curator at two checkpoints, and writes one file, `result/result.json`. cartolex
imports it as the proposal the curator already reviews for a handoff.

Two tasks: **themes** (the theme editor's More menu) and **triage** (the
Keywords screen's AI menu).

## The bundle: `cartolex-copilot/1`

| path | what |
| --- | --- |
| `README_FIRST.md` | to the assistant: the task, the rules, the two checkpoints, the result, how to start |
| `GUIDE.md` | the method, step by step, with the kit's calls |
| `PRIVACY.md` | what the bundle holds and never holds |
| `bundle.json` | the manifest: format, task, id (the result carries it back), the curator's language, the counts, the sha256 of every file |
| `setup/bootstrap.py` | standard library only: checks the wheel, unpacks it into `setup/site` with `zipfile` (no pip, nothing installed), checks the libraries, prints the line to import the kit |
| `setup/cartolex-<v>-py3-none-any.whl` | the kit and the engine modules it calls (`cartolex.copilot`, `cartolex.atlas`, `cartolex.lexicon`; Python files only) |
| `data/` | themes: `context.json`, `keywords.json` (terms, usage), `vectors.npz` (the keywords' and the people's vectors, the lexical matrix and the usage, sparse), `tree.json`, `draft.json`; triage: `context.json`, `terms.json` (each candidate with its evidence and the decision so far), `term_people.npz` |
| `baseline/measures.json` | the measures of the tree (and of the proposal), or the bands and decisions so far |
| `result/` | where the assistant writes `result.json` (and its pictures) |

The data are JSON and compressed `.npz` arrays: no pickle, no Parquet (a code
sandbox may lack `pyarrow`). **Anonymised**: people are rows (or columns) in a
random order; no name, identifier, organisation or text; a keyword or
candidate that holds a roster person's full name is left out
(`cartolex.project.copilot.NameMask`). Triage usage lines are added only when
the curator asks: at most two lines of text around each candidate, with every
roster name, e-mail and web address, DOI and ORCID masked; the privacy summary
then says so.

## The kit: `cartolex.copilot`

The kit imports numpy, pandas, SciPy, scikit-learn and matplotlib only (and
the engine modules built on them); UMAP where it can be imported
(`tests/test_layering.py` holds it to the engine's rules, and
`tests/test_copilot.py` runs a session in a fresh environment where every
other dependency of cartolex is refused, offline).

```python
import sys; sys.path.insert(0, "setup/site")
from cartolex.copilot import open_bundle
s = open_bundle(".")      # a ThemesSession or a TriageSession
```

**Themes** (`ThemesSession`): `summary()`, `outline()`, `keywords(node)`,
`find(text)`; `measure(tree)`, `compare(a, b)`, `borderline()`,
`suggest(keywords)`, `stability(level_sizes)`; `regroup(level_sizes)` (the
grouping of the build: `prepare_cluster_embeddings`, Ward, `level_groups`,
`propose_tree`); `layout(method)`, `draw_map(path, method=)` (`tree`, `tsne`
with scikit-learn, `umap`), `draw_treemap(path)`; changes, each with its
reason: `rename`, `move`, `merge`, `split`, `create`, `delete`, `move_node`,
`set_aside`, `put_back`, `attribution`, `adopt(tree)` (a restructuring, written
as operations), `undo()`; `report()`, `write_result(notes)`.

**Triage** (`TriageSession`): `summary()`, `table(band)`, `item(term)`,
`neighbours(term)` and `pairs()` (candidates the same people use: a
translation, a variant); `keep`, `exclude`, `merge`, `decide`, `decide_many`,
`undecide`; `measure()`, `report()`, `write_result(notes)`.

The tree operations of the kit (`cartolex.copilot.ops`) are those of
`cartolex.project.themes` on plain documents (the project package validates
with a library a sandbox may lack); a result carries them in the form of
`POST /api/themes/ops`, and the import replays them with the project's own
functions and says whether they give the tree the kit computed (`matches`).

**Checkpoints.** `adopt` and `write_result` refuse to run without
`curator_agreed=True` (`CheckpointNeeded`, with what to ask): the guide makes
the assistant ask the curator, in their language, before a restructuring and
before handing back.

**Measures** (`cartolex.copilot.measures`): per level, *coherence* (mean
cosine of a keyword to its node, itself left out), *margin* (minus the cosine to
the nearest other node), *misplaced* and *borderline* shares — the theme
editor's borderline measure (`cartolex.lexicon.theme_fit`); sizes per level;
*stability*, the adjusted Rand index of the grouping redone without 10 % of the
people (the space refitted by cartolex's SVD); where a truth exists (a demo
world, `data/truth.json` or `open_bundle(truth=)`), the lexicon lab's B-cubed
F1 for themes, precision and recall for triage.

## The result: `cartolex-copilot-result/1`

```json
{"format": "cartolex-copilot-result/1", "task": "themes", "bundle": "<id>",
 "changes": [{"kind": "rename", "ops": [{"op": "rename_node", "node_id": "s3",
   "names": {"en": "Coastal hazards"}}], "reason": "its keywords are floods and surges"}],
 "tree": {"…": "the tree they give"}, "measures": {"before": {}, "after": {}},
 "notes": "for the curator"}
```

A triage result has `decisions` instead: `{term, language, decision (keep,
exclude, merge), target, code, reason}`. `cartolex.copilot.bundle.check_result`
lists what is wrong with one; the kit writes only results it passes, the app
reads only those (`invalid_copilot_result`).

## In the app

| route | what it does |
| --- | --- |
| `GET /api/themes/copilot/summary` | what a themes bundle holds and never holds, and its counts (nodes, keywords, set aside, people) |
| `GET /api/themes/copilot/export?language=` | the themes bundle: the saved tree, else the grouping's proposal; `language` is the curator's |
| `POST /api/themes/copilot/import {result}` | keeps the result in `decisions/history/ai/<time>-copilot-themes.json`, and gives each change as a proposal item (`verb`, `kind`, `ops`, `reason`, `refused` when it cannot apply after the changes before it), `applicable`, `matches`, `measures`, `notes`; the first answers freeze the identity |
| `GET /api/themes/copilot/proposals/{id}` | one of them, read again |
| `GET /api/keywords/copilot/summary?scope=&usage_lines=` | the same for triage (`scope`: `both`, Kept and To check, or `check`) |
| `GET /api/keywords/copilot/export?scope=&usage_lines=&language=` | the triage bundle |
| `POST /api/keywords/copilot/import {result}` | keeps the result in `decisions/history/ai/<time>-copilot-triage.json` and gives a keyword proposal (the shape of `GET /api/handoff/proposals/{id}`, each item with its `reason`), accepted with `POST /api/handoff/proposals/{id}/accept` (source `ai-handoff`, reason « AI: » and the assistant's) |

The theme editor reviews the changes like a handoff's (accept or reject each,
preview on the tree), applies the accepted ones through `POST /api/themes/ops`
and saves them at once, as a version.

## Sizes and times

On the L demo world (seed 0, depth 2: 329 people with keywords, 2,946 keywords
on 162 nodes; 7,605 Kept and To check candidates), one laptop core:

| | themes | triage | triage with usage lines |
| --- | --- | --- | --- |
| the zip | 0.68 MB | 0.58 MB | 1.1 MB |
| unpacked | 1.0 MB | 2.5 MB | 4.7 MB |
| making it (the export) | 1.4 s | 2.8 s | 5.8 s |
| the kit's wheel inside | 0.29 MB | 0.29 MB | 0.29 MB |

A session's steps (seconds, in a fresh environment with the preinstalled
libraries only): bootstrap 2.8 (unpacking, and the first import of the
libraries); open 0.2; outline, measure, borderline, compare 0.1–0.3 each;
`regroup` 1.0–1.3; `stability` (three draws) 4.0; the tree layout 1.4, t-SNE
5.7; `draw_map` 5.1 the first time (matplotlib builds its font cache), 0.3
after; `draw_treemap` 0.6; `adopt` of a new grouping (several hundred operations)
2.6; one change 0.01; `write_result` 0.5. Triage: open 0.1, `pairs` 2.5, 50
`neighbours` 0.2, 7,605 decisions 0.01, `write_result` 0.1. Importing a result:
0.2 s (triage), 3.1 s (themes, the operations replayed on the tree). Every
step stays far under the 90 s a sandbox gives a code cell.
