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

The tree has a depth from 1 to 4 above the keywords. Its leaves are keyword
texts; its inner nodes have stable ids and names in each interface language.

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
  "keywords": {"storm surge model": "n7", "coastal flooding": "n7"},
  "set_aside": {"numerical results": {"from": "n7", "reason": "too general"}},
  "review": {"tidal inlet": "to_check"},
  "based_on": {"run": "themes.group/20260928T101500Z-3f2a", "vocabulary": "sha256:…"}
}
```

- Every keyword of the current vocabulary is either under a node of the deepest
  level or set aside.
- `based_on` names the vocabulary the tree was built or last rebased on. After a
  new extraction the tree is **rebased**: kept keywords stay where they are, new
  keywords go to « To check » with a proposed place, vanished keywords are
  listed, and a node left empty is removed. The rebase writes a new version and
  a reconciliation list that names every changed keyword.
- Level names are the defaults for the depth (Theme; Theme › Topic; Field ›
  Theme › Topic; Domain › Field › Theme › Topic) until someone renames them.

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
