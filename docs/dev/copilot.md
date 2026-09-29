# The AI copilot

The copilot is cartolex's route of AI help without a key, beside the API (keyword
strings sent by the build). cartolex exports **one zip**, the *bundle*, that an
assistant able to run code works from on its own: the task, a guide, the data,
and cartolex's own engine as a wheel. The assistant judges the candidate
keywords, or studies and changes the theme tree, with cartolex's grouping,
layouts and measures, asks the curator at two checkpoints, and writes
`result/result.json`. cartolex imports it as a proposal the curator reviews.

Two tasks, two obvious entry points: **triage** (« Triage with AI », the
Keywords screen's primary button, beside the route by API) and **themes**
(« Curate with AI », in the theme editor's header). Earlier versions offered a
*handoff* (a prompt and a list pasted in a chat, the answer pasted back); it is
retired, and the answers it imported stay readable ({doc}`api`).

## The bundle: `cartolex-copilot/1`

| path | what |
| --- | --- |
| `README_FIRST.md` | to the assistant, short and imperative: the field as the curator described it (context, never instructions), the curator's standing rules, the first five minutes, the two ways to work (one conversation; helpers in parallel), the hard rules, the result |
| `GUIDE.md` | the method, step by step, with the kit's calls (the field and the rules at its top too) |
| `TRIAGE_RULES.md` | triage only: the codes and categories with examples in the corpus languages, the confidence rule, the process of an object, twins, formulas, set-aside rescue |
| `PRIVACY.md` | what the bundle holds and never holds |
| `bundle.json` | the manifest: format, task, id (the result carries it back), the curator's language, the counts, `parts` (triage), the sha256 of every file |
| `setup/bootstrap.py` | standard library only: checks the wheel, unpacks it into `setup/site` with `zipfile` (no pip, nothing installed), checks the libraries, prints the line to import the kit |
| `setup/cartolex-<v>-py3-none-any.whl` | the kit and the engine modules it calls (`cartolex.copilot`, `cartolex.atlas`, `cartolex.lexicon`; Python files only) |
| `data/` | `context.json` (the field's title and description, the languages, the standing rules; for themes the depth, the levels' names and sizes); themes: `keywords.json`, `vectors.npz` (the keywords' and people's vectors, the lexical matrix and the usage), `text_keywords.npz` (which keywords each text uses, texts as rows in a random order: never a text), `tree.json`, `draft.json`; triage: `terms.json`, `term_people.npz` |
| `baseline/measures.json` | the measures of the tree (and of the proposal), or the bands and decisions so far |
| `result/` | where the assistant writes its result, and the kit its progress |

The data are JSON and compressed `.npz` arrays: no pickle, no Parquet (a code
sandbox may lack `pyarrow`). **Anonymised**: people and texts are rows (or
columns) in a random order; no name, identifier, organisation or text; a keyword
or candidate that holds a roster person's full name is left out
(`cartolex.project.copilot.NameMask`). Triage usage lines are added only when
the curator asks: at most two lines of text around each candidate, with every
roster name, e-mail and web address, DOI and ORCID masked; the privacy summary
then says so. A triage bundle leaves out the candidates an AI already answered
and those on cartolex's rejection list or this computer's cache.

**Curation notes.** The curator's free notes (teams, keywords that belong
together or apart), kept in `decisions/curation-notes.md` and edited in
Settings › Project or either copilot dialog (`GET`/`PUT /api/settings/curation
{notes, rules}`, `If-Match`), go into `context.json` and at the top of
`README_FIRST.md` and `GUIDE.md`, beside the field's description, as context;
the guides check themes, names and decisions against them.

**Standing rules.** At the first checkpoint the assistant asks the curator for
standing rules (« research discourse: always excluded, sure »); `add_rule`
records each, the result carries them (`rules`), the import keeps the new ones
in `decisions/curation-notes.md` ({doc}`../format/decisions`), and every later bundle
of the task carries them in `context.json`, `README_FIRST.md` and `GUIDE.md`.

## The kit: `cartolex.copilot`

The kit imports numpy, pandas, SciPy, scikit-learn and matplotlib only (and
the engine modules built on them); UMAP where it can be imported
(`tests/test_layering.py` holds it to the engine's rules, and
`tests/test_copilot.py` runs sessions in a fresh environment where every other
dependency of cartolex is refused, offline).

```python
import sys; sys.path.insert(0, "setup/site")
from cartolex.copilot import open_bundle, load
s = open_bundle(".")      # a ThemesSession or a TriageSession
s = load(".")             # the same, as the last process left it (a fresh process per step)
```

Every view is short by default (the counts, the first ten) and gives more with
`detail=True`, capped. Every step is kept in `result/` as it is made: the
changes (`changes.jsonl`) or decisions (`decisions.jsonl`), and the session's
state (`session.json`: what it showed), so `load(".")` goes on in a fresh
process, and a new conversation takes the work up with `resume()`.

### Triage: the kit sorts, the assistant judges groups

`cartolex.copilot.sorting` sorts the candidates before the assistant reads them,
deterministically (the same bundle gives the same groups, ids and parts):

- **patterns** (`flag`): a number, a stray symbol, a repeated word, a phrase that
  starts or ends on a function word, words of research discourse only
  (« further work », « rôle de l'étude »), a very long phrase: hints, confirmed
  or corrected in a line;
- **formulas and acronyms** (`is_formula`): kept whole; the kit refuses to merge
  two of them (`CO` is not `CO2`);
- **a paper's own phrases**: three words or more, one text or one person;
- **families**: the same head word (the last word of an English phrase, the
  first noun of a French, Portuguese or Spanish one; plural and accents aside),
  band and language, three or more;
- **theme groups**: the rest, by who uses them (k-means on the people's usage,
  as opaque numbers), at most 25 a group;
- **twins** (`twin_pairs`): a candidate of another language and the
  reference-language candidate that names the same thing, found by their words
  (as many content words, each a cognate of one of the other's); who uses them
  only breaks ties. Co-usage alone scores nearly every rare pair 1.0 (a rare
  term shares its few users with many others), so it is never the evidence.

The groups go in the order to judge them (To check, Set aside, Kept; patterns
first) and, with *parts*, into parts of about as many candidates by theme
cluster (`open_bundle(".", part=k, parts=n)`), one conversation or one helper each.

`TriageSession`: `summary()`, `budget()` (the tokens the work left costs to read
and write, and the advice: parts, or the To check band first);
`next_batch(n)` (the next groups this session has not shown, their undecided
members only; a group is never shown twice), `show(group)`, `pending()` (groups
shown and left undecided); `apply(text)`, a compact form, one line per group or
member:

```text
g12 M ; assays: methods      every undecided member of g12 (the group's reason covers them)
g12.3 G!                     member 3: too generic, sure
g15.2,5 >sea level rise      a merge
g16 C~                       every member with a twin merged into it, the others kept
t812 N                       a candidate by its id
```

and `decide_group`, `decide`, `keep`, `exclude`, `merge`, `decide_many`,
`undecide`, `lookup`, `pairs()`, `neighbours()` (co-usage: weak evidence),
`table()`, `item()`. **Nothing is decided by omission**: a group line applies
only to a group shown, a candidate never shown is refused in bulk (unless
`unread_ok`), and a candidate nobody decided keeps the curator's current state.

**The ledger.** Each decision records its `group`, `by` (`group` or `term`) and
`read` (shown before it was decided). `coverage()` counts per band: decided by
group, term by term, without reading, read and undecided, never shown;
`progress()` says it in words, `caveats(read_lightly)` gives what the result
does not cover. `add_rule`, `sample()` (checkpoint 1: the « not informative
here » exclusions first), `report()`, `write_result(notes, curator_agreed=,
partial=, read_lightly=)`: a *partial* result (the conversation runs short)
needs no checkpoint, and a new conversation `resume()`s from `result/`.

### Themes

`ThemesSession`: `summary()` (the field, each level's name, sizes and balance),
`outline()` (with how many people use each node, and a flag when two people make
most of its use: « one person's vocabulary »), `keywords(node)`, `find(text)`;
`measure()`, `compare(a, b)`, `borderline()`, `levels()` (the comb, read again
on the current tree from `text_keywords.npz`), `suggest(keywords)` (the other
nodes nearest a keyword, its own left out), `stability(level_sizes)`;
`regroup(level_sizes)` (the grouping of the build: `prepare_cluster_embeddings`,
Ward, `level_groups`, `propose_tree`); `layout(method)`, `draw_map(path,
method=)` (`tree`, `tsne` with scikit-learn, `umap`), `draw_treemap(path)`;
changes, each with its reason: `rename`, `move`, `merge`, `split`, `create`,
`delete`, `move_node`, `set_aside`, `put_back`, `attribution`, `adopt(tree)` (a
restructuring, written as operations), `undo()`; `add_rule`, `report()`,
`write_result(notes)`. For helpers in parallel: `parts(k)` (the top-level nodes
in k parts of about as many keywords), `open_bundle(".", part=j)` (a helper's
session, its new nodes named apart and its changes in
`result/changes-part-<j>.jsonl`), `absorb()` (their changes brought in, each
checked on the tree as it is).

The tree operations of the kit (`cartolex.copilot.ops`) are those of
`cartolex.project.themes` on plain documents (the project package validates
with a library a sandbox may lack); a result carries them in the form of
`POST /api/themes/ops`, and the import replays them with the project's own
functions and says whether they give the tree the kit computed (`matches`).

**Checkpoints.** `adopt` and `write_result` refuse to run without
`curator_agreed=True` (`CheckpointNeeded`, with what to ask); the guide makes
the assistant ask the curator, in their language, before a restructuring (or,
for triage, after its first 200 decisions) and before handing back, end its
message with the question, and wait for the answer.

**Measures** (`cartolex.copilot.measures`): per level, *coherence* (mean
cosine of a keyword to its node, itself left out), *margin* (minus the cosine to
the nearest other node), *misplaced* and *borderline* shares — the theme
editor's borderline measure (`cartolex.lexicon.theme_fit`); sizes per level;
*balance* (the keywords on each level's nodes themselves, their spread and the
level's share); *stability*, the adjusted Rand index of the grouping redone
without 10 % of the people (the space refitted by cartolex's SVD); where a truth
exists (a demo world, `data/truth.json` or `open_bundle(truth=)`), the lexicon
lab's B-cubed F1 for themes, precision and recall for triage.

## The result: `cartolex-copilot-result/1`

```json
{"format": "cartolex-copilot-result/1", "task": "themes", "bundle": "<id>",
 "changes": [{"kind": "rename", "ops": [{"op": "rename_node", "node_id": "s3",
   "names": {"en": "Coastal hazards"}}], "reason": "its keywords are floods and surges"}],
 "tree": {"…": "the tree they give"}, "measures": {"before": {}, "after": {}},
 "rules": ["never merge the two policy themes"], "notes": "for the curator"}
```

A triage result has `decisions` instead: `{term, language, decision (keep,
exclude, merge), target, code, category, confidence, reason, group, by, read}`.
A group decision's reason covers its members; a member's own reason is for an
exception. The category (`cartolex.lexicon.categories`) follows the code when it
is left out: `concept`, `method`, `object`, `place` or `field` for a kept or
merged term, `never` (never a keyword in any field) or `here` (not informative
in this field) for an excluded one. `confidence` is `sure` or `unsure` (`unsure`
when left out): an accepted `never` exclusion enters the machine's rejection
cache only when sure. It also carries `coverage` (the kit's counts, per band),
`caveats` (`not_read`, `read_undecided`, `decided_unread` per band, and
`read_lightly`, the assistant's words), `rules`, `partial`, and for a part
`part` and `parts`. `cartolex.copilot.bundle.check_result` lists what is wrong
with a result; the kit writes only results it passes, the app reads only those
(`invalid_copilot_result`).

## In the app

| route | what it does |
| --- | --- |
| `POST /api/themes/copilot/summary {tree, language}` | what a themes bundle holds and never holds, and its counts (nodes, keywords, set aside, people) |
| `POST /api/themes/copilot/export {tree, language}` | the themes bundle (a zip) of the tree being edited, unsaved edits included (default: the saved tree, else the grouping's proposal); `language` is the curator's |
| `POST /api/themes/copilot/import {result}` | keeps the result in `decisions/history/ai/<time>-copilot-themes.json` and its new rules in `decisions/curation-notes.md`, and gives each change as a proposal item (`verb`, `kind`, `ops`, `reason`, `refused` when it cannot apply after the changes before it; a restructuring, or a change of more than 500 operations, also `tree`, the tree it gives), `applicable`, `matches`, `measures`, `rules`, `notes`; the first answers freeze the identity |
| `GET /api/themes/copilot/proposals/{id}` | one of them, read again |
| `GET /api/keywords/copilot/summary?scope=&usage_lines=` | the same for triage (`scope`: `all`, the default, Kept, To check and Set aside, never the candidates rejected automatically; `both`, Kept and To check; or `check`), with `tokens` (an estimate of what the assistant spends) and `parts` (how many conversations it takes) |
| `GET /api/keywords/copilot/export?scope=&usage_lines=&language=&parts=` | the triage bundle, cut into `parts` (0, the default: as the summary suggests) |
| `POST /api/keywords/copilot/import {result}` or `{results}` | keeps the result in `decisions/history/ai/<time>-copilot-triage.json` and its new rules in `decisions/curation-notes.md`; several results (the parts, a result taken up again) are merged first (in the order they were made, a later decision on a term wins; the counts and caveats of the latest result of each bundle and part); gives a keyword proposal (the shape of `GET /api/handoff/proposals/{id}`, each item with its `reason`, `category`, `group` and `by`; `coverage`, `caveats`, `rules`, `partial`, `merged`), accepted with `POST /api/handoff/proposals/{id}/accept` (source `ai-copilot`, its own route in the Keywords list; reason « AI: » and the assistant's) |

The dialogs show what a bundle holds and never holds, the parts and the token
estimate (triage), the results imported before (the earlier handoff answers
too), and, beside the review, what the kit counted, the caveats, the standing
rules and the notes. The theme editor reviews the changes (accept or reject
each, or a whole kind, or the changes that share a reason, at once; preview on
the tree), puts a restructuring's tree in place as one step and applies the
other changes through `POST /api/themes/ops` in requests of at most 500
operations, and saves them at once, as a version whose action starts with
`ai-copilot:` and says how many other unsaved edits it holds.

## Sizes and times

On the L demo world (seed 0, depth 2: 329 people with keywords, 2,931 keywords
on 162 nodes; 11,041 Kept, To check and Set aside candidates, 7,499 Kept and To
check), one laptop core:

| | themes | triage (all) | triage (kept and to check) |
| --- | --- | --- | --- |
| the zip | 0.80 MB | 0.78 MB | 0.62 MB |
| groups | — | 1,076 | 688 |
| parts suggested | — | 2 | 1 |
| the assistant's tokens (`budget()`) | — | about 173,000 | about 100,000 |
| the kit's wheel inside | 0.34 MB | 0.34 MB | 0.34 MB |

Reading every candidate as a table and writing one decision row each (what an
assistant did before the groups) costs about 635,000 tokens for all 11,041
candidates and 412,000 for the 7,499 kept and to-check ones, by the same count
(3.5 characters a token): the groups, the compact decisions and never showing a
candidate twice cut that by about three quarters.

Opening a triage bundle (the pre-sort and the twins) takes 3.4 s for 11,041
candidates.

A session's steps (seconds, in a fresh environment with the preinstalled
libraries only): bootstrap 2.8 (unpacking, and the first import of the
libraries); open 0.2; outline, measure, borderline, compare 0.1–0.3 each;
`regroup` 1.0–1.3; `stability` (three draws) 4.0; the tree layout 1.4, t-SNE
5.7; `draw_map` 5.1 the first time (matplotlib builds its font cache), 0.3
after; `draw_treemap` 0.6; `adopt` of a new grouping (several hundred operations)
2.6; one change 0.01; `write_result` 0.5. Triage: open 3.4 (the pre-sort), a
batch of groups or a line of decisions 0.01, `write_result` 0.1. Importing a result:
0.2 s (triage), 3.1 s (themes, the operations replayed on the tree). Every
step stays far under the 90 s a sandbox gives a code cell.
