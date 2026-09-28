# Decisions

`decisions/` holds what people decided. Its files are small enough to read,
compare and edit, and they name things by stable keys: keywords by their text,
people and organisations by their ids. A re-extraction or a re-collection
never moves a decision onto something else; a decision whose key disappears is
listed, never dropped silently.

```text
decisions/
  people.csv            roles, identities, merges
  organisations.csv     levels, parents, merges, names, set by people
  affiliations.csv      affiliations added or removed by people
  params.json           the parameters people set
  keywords.csv          keep, exclude, merge
  themes.json           the theme tree, keyed by keyword text
  maps.json             map versions, the pinned one
  snowball.csv          collaborators proposed, round by round, and what was decided
  stopwords.json        additions to and removals from the function-word lists
  prompts/              prompt overrides, one file per prompt
  history/              every earlier version
```

## Writing decisions

A writer passes the fingerprint (SHA-256) of the version it read. If the file
changed since, the write is refused and nothing is lost. An accepted write moves
the previous version to `decisions/history/<file>/<UTC time>-<action>.<ext>`
first, then writes atomically. `project.json` follows the same rule. Restoring a
version is a write like any other, so it is itself undoable.

## `people.csv`

One row per person the project knows. Columns:

| column | meaning |
| --- | --- |
| `person_id` | the key, as in `sources/tables/people.parquet` |
| `role` | `mapped` (their texts shape the lexicon and the map, and they appear on it), `context` (their texts shape the lexicon and the space with a weight; they are not on the map), `projected` (placed on the finished map), `excluded`, `undecided` |
| `set` | for `projected`: the overlay set |
| `identity` | `confirmed`, `auto` (a single match above the threshold, accepted automatically), `none` (no record exists), `pending` |
| `records` | the service records that are this person, `;`-separated (`openalex:A…;orcid:0000-…`) |
| `merged_into` | when two rows are one person, the `person_id` that remains |
| `note` | free text |
| `decided_at` | UTC time of the last change to the row |

Keywords are decided by the `mapped` people: a keyword is kept only if enough
mapped people use it. `context` texts count in the statistics, never in that
threshold.

## `organisations.csv` and `affiliations.csv`

`organisations.csv` changes what the sources say about organisations: `org_id`,
`level`, `parents` (`;`-separated), `name`, `merged_into`, `note`, `decided_at`.
An empty cell leaves the source's value. `affiliations.csv` adds or removes
affiliations: `person_id`, `org_id`, `start_year`, `end_year`, `action`
(`add` or `remove`), `note`, `decided_at`.

## `params.json`

Only the parameters people set. Every other value comes from a default, most of
them computed from the project's sizes by a simple rule; the effective value and
where it came from are written into each stage's `run.json`.

```json
{
  "format": "cartolex-params/1",
  "seed": 20260928,
  "pinned_year": null,
  "stages": {
    "keywords.extract": {"counting_unit": "person"},
    "themes.group": {"top_groups": 15}
  }
}
```

`seed` fixes every random choice; `pinned_year` fixes the year that date windows
count back from (none: the current year). A parameter a stage does not know, or
an impossible value (as many topics as keywords), is refused when the file is
read, with the reason.

## `keywords.csv`

| column | meaning |
| --- | --- |
| `term` | the keyword, as shown |
| `language` | its language |
| `decision` | `keep`, `exclude` or `merge` |
| `target` | for `merge`: the keyword it merges into |
| `reason` | why, in words |
| `source` | `person`, `ai-handoff` or `ai-api` |
| `decided_at` | UTC time |

A proposal imported from an AI (by handoff or by API) is kept as it came in
`history/ai/`, and only the changes someone accepts reach `keywords.csv`.

## `themes.json`: the theme tree

The tree has a depth from 1 to 4 levels of nodes. Its nodes have stable ids and
names in each interface language; each keyword text sits on one node, at any
level: a keyword on a higher node is broader than every node below it.

```json
{
  "format": "cartolex-themes/1",
  "depth": 2,
  "levels": [
    {"names": {"en": "Theme", "fr": "Thème", "pt": "Tema"}},
    {"names": {"en": "Topic", "fr": "Sujet", "pt": "Tópico"}}
  ],
  "nodes": [
    {"id": "n1", "parent": null, "names": {"en": "Coastal hazards"}, "order": 1},
    {"id": "n7", "parent": "n1", "names": {"en": "Storm surge"}, "order": 1}
  ],
  "keywords": {"coastal flooding": "n7", "extreme events": "n1", "storm surge model": "n7",
               "tidal inlet": "n7", "wave climate": "n7"},
  "attribution": {"wave climate": 1},
  "set_aside": {"numerical results": {"from": "n7", "reason": "too general", "attribution": 0}},
  "review": {"tidal inlet": "to_check"},
  "based_on": {"run": "themes.group/20260928T101500Z-3f2a", "vocabulary": "sha256:…"},
  "saved": {"at": "2026-09-28T10:20:00Z", "action": "move 3 keywords to n7"}
}
```

| key | meaning |
| --- | --- |
| `depth`, `levels` | the number of levels (1 to 4) and their names, from the top level down |
| `nodes` | every node: a stable `id` (letters, digits, `-` and `_`), its `parent` (`null` on the top level), its `names` per language (possibly none), its `order` among its siblings (ties by id) |
| `keywords` | each placed keyword and the node it is on, at any level |
| `attribution` | for a placed keyword that does not count at its node's level: how many levels, from the top, its usage counts toward (see below) |
| `set_aside` | each keyword set aside: `from`, the node it was set aside from (`null` when it never had a place), `reason`, and the `attribution` it had, if any |
| `review` | `to_check` for a keyword a rebase added, `reviewed` once someone checked it; a keyword with nothing to check is absent |
| `based_on` | the run and the vocabulary the tree was built or last rebased on |
| `saved` | when this version was saved, and the action that saved it |

- Every keyword of the current vocabulary is on exactly one node or set aside,
  never both; `review` names only keywords the tree holds.
- A node's level is its distance from the top (1 for a top-level node); no node
  sits below the tree's depth. A node may hold keywords and child nodes at the
  same time. In the example, « extreme events » sits on the theme itself: it
  belongs to coastal hazards, not to storm surge in particular.
- **Attribution.** A keyword's usage counts toward its node and every node
  above it (a keyword on a top-level node counts toward that node only).
  `attribution` lowers that: `n` counts it toward levels 1 to `n` only, `0`
  shows the keyword without counting it anywhere, and a keyword absent from the
  map counts down to its node's level. `n` is below the level of the keyword's
  node (0 to level − 1), and only placed keywords appear. In the example, « wave
  climate » is shown under storm surge but counts toward coastal hazards only.
- **The carry rule.** A move, a merge or a split that gives a keyword another
  node drops an attribution other than `0`; `0` always stays. A keyword that
  keeps its node keeps its attribution (moving a node moves its keywords with
  it). A set-aside keyword keeps its attribution in its entry; putting it back
  on the node it came from gives it back, anywhere else only `0` comes back.
- **Depth changes** keep keywords on their nodes; the nodes' levels shift.
  Inserting a level gives each node just above it that has child nodes one new
  child, named like it, that takes them over (at the top: one new root over
  every top-level node; at the bottom: nothing, the level starts empty), and an
  attribution counting toward the new level's position becomes `n + 1`.
  Removing a level dissolves its nodes into their parents, which receive their
  child nodes and keywords; the keywords of a removed top-level node are set
  aside with the reason `its node was removed with its level`. An attribution
  counting toward the removed level becomes `n − 1` (a keyword that counted
  toward the removed top level only then counts nowhere); a keyword moved to
  the parent with `n` equal to the parent's level loses its attribution.
- `from` may name a node that no longer exists (merged away, or removed by a
  rebase or a save); putting the keyword back then needs a target. cartolex
  never gives a new node such an id.
- cartolex writes the file in one form: nodes in tree order (depth first,
  siblings by `order` then id), keywords, attributions, set-aside keywords and
  review states sorted by text; a set-aside entry without an attribution has no
  `attribution` key.
- **Saving removes empty nodes.** Every save removes each node with no keyword
  in its subtree, itself included (set-aside keywords do not count), and names
  the removed nodes in its action (`move 2 keywords to n7; remove empty nodes
  n3, n5`). A saved file therefore never holds an empty node; a tree being
  edited may, until it is saved.
- `based_on.vocabulary` is the fingerprint of a vocabulary: `sha256:` and the
  SHA-256 of the JSON list of its distinct keywords, sorted, written without
  spaces (`["a","b"]`) in UTF-8.
- After a new extraction or a new grouping the tree is **rebased** onto the new
  vocabulary: kept keywords stay where they are (set-aside ones stay set
  aside); each new keyword goes to the node proposed for it, at any level,
  marked `to_check` (set aside, with the reason `new keyword, no place
  proposed`, when no node is proposed); vanished keywords are removed,
  set-aside ones too; a node whose subtree held keywords and holds none after
  the rebase is removed, with the nodes under it. Nothing else changes: no node
  is renamed, moved or renumbered, and surviving keywords keep their
  attribution. The rebase is saved as a new version, and its reconciliation
  list names every added or removed keyword and every removed node. That list
  is the difference between the version before the rebase and the version it
  wrote, so the history keeps it.
- Level names are the defaults for the depth (Theme; Theme › Topic; Field ›
  Theme › Topic; Domain › Field › Theme › Topic, in English, French and
  Portuguese) until someone renames them. When the depth changes, a name still
  equal to its default follows the defaults of the new depth; a name someone
  gave stays.
- Every change is a new version: `saved` records when and by which action it
  was saved, and `decisions/history/themes.json/<UTC time>-<action>.json` holds
  the version that `<action>` replaced at that time. Restoring a version saves
  it again as a new version, with the action `restore <version>`.

The operations on a tree and their rules are described in
[The theme tree](../dev/themes.md).

## `maps.json`: map versions

```json
{
  "format": "cartolex-maps/1",
  "pinned": "v3",
  "versions": [
    {"id": "v3", "shows": ["people"], "layout": {"method": "umap", "seed": 7}, "base": null,
     "created_at": "2026-09-28T11:00:00Z", "note": "after the September curation"}
  ]
}
```

`shows` lists what the map shows: `people`, `organisations:<level>`, `texts`,
possibly several. `base` names a base from `project.json` when the version is
placed on another project's map. A rebuild keeps the pinned version's layout;
another layout is tried as a new version beside it.

`layout.method` is one of:

| method | the people's map | `params` |
| --- | --- | --- |
| `umap` | UMAP of the people in the space | `n_neighbors`, `min_dist`, `metric`, `n_epochs`, `spread`, `set_op_mix_ratio`, `local_connectivity`, `repulsion_strength`, `negative_sample_rate`, `layout` |
| `tsne` | t-SNE of the people (the optional `openTSNE` package: `pip install 'cartolex[tsne]'`) | `perplexity` (30), `metric` |
| `tree` | the applied theme tree: themes as discs, people inside their heaviest theme | none |

In every method the keywords are placed on the people's map by their nearest
people, and `seed` makes the map the same on every run. {doc}`../dev/layouts`
compares them.

## `snowball.csv`

One row per collaborator proposed: `round`, `person_id`, `seeds` (the seeds they
wrote with, `;`-separated), `path` (how they connect to a seed), `joint_texts`,
`last_joint_year`, `fit`, `decision` (`mapped`, `context`, `projected`, `no`,
`later`), `decided_at`. A round is taken whole or not at all, up to the cap set
in `params.json`.

## `stopwords.json` and `prompts/`

`stopwords.json` adds to or removes from the packaged function-word lists, per
language: `{"format": "cartolex-stopwords/1", "add": {"fr": ["…"]}, "remove":
{}}`. `prompts/<name>.txt` replaces a packaged prompt; it must keep the
placeholders the packaged prompt uses, or it is refused when read.
