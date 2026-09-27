# The theme tree

The theme tree of a project lives in `decisions/themes.json` (its format is in
[Decisions](../format/decisions.md)). Three modules work on it:

| module | what it holds |
| --- | --- |
| `cartolex.project.themes` | the operations on a tree, the rebase onto a new vocabulary, the comparison of two trees |
| `cartolex.project.themes_versions` | saving a tree, listing, reading and restoring its versions |
| `cartolex.project.themes_curated` | the converters to and from the engine's two-level curated document |

Every function of `themes` is pure: it takes a `ThemesFile` and returns a new
one, never changing its argument. Every tree it returns passes the
`ThemesFile` checks and is in the canonical form (nodes in tree order,
keywords sorted), so equal trees are written as equal files. A refused
operation raises `ThemeEditError` (a `ValueError`) with the reason, and the
tree is unchanged.

```python
from cartolex.project import Project
from cartolex.project.themes import create_node, move_keywords, new_tree, rebase
from cartolex.project.themes_versions import read_themes, save_themes

with Project.open(root, write=True) as project:
    tree, fp = read_themes(project)  # (None, None) before the first save
    tree = tree or new_tree(depth=2)
    edit = create_node(tree, None, {"en": "Coastal hazards", "fr": "Aléas côtiers"})
    edit = create_node(edit.tree, "n1", {"en": "Storm surge"})
    vocabulary = ["storm surge model", "sea level"]
    result = rebase(edit.tree, vocabulary, {"storm surge model": "n2", "sea level": None})
    fp = save_themes(project, result.tree, expected=fp, action=result.description)
    edit = move_keywords(result.tree, ["storm surge model"], "n2")
```

## Operations

Each operation returns an `Edit`: the new `tree` and a short `description` of
the change in English (`move 3 keywords to n7`). An undo list keeps the trees;
the description names the step and becomes the action name of the saved
version.

| operation | what it does | refused when |
| --- | --- | --- |
| `rename_node(tree, id, names)` | sets the node's name in each language of `names`; `None` or a blank name removes that language | unknown node or language |
| `rename_level(tree, level, names)` | the same for a level (1 = top) | the level would have no name left |
| `move_keywords(tree, keywords, id)` | moves placed keywords under a node of the deepest level | the node is not on the deepest level; a keyword is set aside (put it back instead) or unknown |
| `move_node(tree, id, parent, position=None)` | moves a node and everything under it; `position` is its 0-based place among its new siblings (default: last) | the new parent is not on the level just above the node; the node is already there and no position is given |
| `merge_nodes(tree, source, target)` | what `source` holds goes under `target`, after what it already holds; `source` is removed and set-aside origins follow | the nodes are on different levels, or the same node |
| `split_node(tree, id, parts, ids=None)` | each part `(members, names)` becomes a new sibling right after the node; the members no part names stay | a part is empty, members are not the node's or appear twice, or nothing would stay |
| `create_node(tree, parent, names, node_id=None, position=None)` | an empty node, named, under `parent` (`None`: the top level) | the parent is on the deepest level; no name; the id is taken or invalid |
| `delete_node(tree, id)` | removes an empty node | the node holds nodes or keywords |
| `set_aside(tree, keywords, reason="")` | sets placed keywords aside, recording where each came from and why; for a keyword already set aside, changes the reason | a keyword is unknown |
| `put_back(tree, keywords, id=None)` | places set-aside keywords under `id`, or where each came from | a keyword is not set aside; its origin is gone and no target is given |
| `set_review(tree, keywords, state)` | `to_check`, `reviewed` or `None` | unknown state or keyword |
| `insert_level(tree, at, root_names=None)` | adds a level (see below) | the tree has 4 levels already |
| `remove_level(tree, at)` | removes a level (see below) | the tree has one level |

No operation adds or loses a keyword: the set of keywords a tree holds changes
only with a rebase. New nodes get the id `n<k>`, the next free number; a number
that a set-aside origin still names is never given again, so putting a keyword
back never lands in an unrelated node.

## Changing the depth

A tree has 1 to 4 levels. `insert_level(tree, at)` adds a level at position
`at`, from 1 (a new top level) to `depth + 1` (a new bottom level):

- **inside or at the bottom** (`at ≥ 2`): every node of level `at − 1` gets one
  new child, with the same names, that takes over everything it held — its
  child nodes, or its keywords. Set-aside keywords that came from a node of the
  old deepest level now come from its new child;
- **at the top** (`at = 1`): one new root takes over every top-level node. It
  is named `root_names`, or with the new level's name.

Every keyword keeps its path; the new level repeats the level above it (or
groups everything, at the top), and people then split, merge or rename it.

`remove_level(tree, at)` removes level `at`: each of its nodes dissolves into
its parent (into the top level, when `at = 1`). What a removed node held goes
to its parent, in the order of the removed nodes and then of what each held,
numbered 1, 2, 3… Set-aside keywords that came from a removed node of the
deepest level now come from its parent. The names of the removed nodes are
lost from the tree (the previous version keeps them).

Inserting a level and removing it again at the same position gives the tree
back, up to the numbering of siblings.

**Level names.** A new level takes the default name of its position at the new
depth (Theme; Theme › Topic; Field › Theme › Topic; Domain › Field › Theme ›
Topic). An existing level keeps the names someone gave it; a name still equal
to the default of its old position becomes the default of its new position.
So a default depth-2 tree (Theme › Topic) with a new bottom level reads Field ›
Theme › Topic, and a level renamed « Axis » stays « Axis ». The rule applies
per language.

## Rebase

`rebase(tree, vocabulary, proposals, run=None)` carries a tree onto the
vocabulary of a new build (its kept keywords). `proposals` gives, for each new
keyword, the node of the deepest level proposed for it (computed elsewhere,
for example from the new grouping), or `None`.

1. A keyword the tree already holds stays where it is, placed or set aside,
   with its review state. Proposals for such keywords are ignored.
2. A new keyword goes under its proposed node, marked `to_check`. With `None`
   it is set aside instead, with the reason `new keyword, no place proposed`,
   also marked `to_check`.
3. A keyword that vanished is removed with its review state; a set-aside one
   is dropped.
4. A node whose subtree held keywords before and holds none after is removed,
   with the nodes under it (the carry-forward rule: a sub-group emptied by the
   new vocabulary goes). A node that was already empty stays.
5. `based_on` records `run` and the vocabulary's fingerprint.

Nothing else changes: no node is renamed, moved or renumbered, and set-aside
origins stay as they were, even when they name a removed node. Nothing is
placed by default either: a new keyword without an entry in `proposals`, or a
proposal that is not a node of the deepest level, refuses the whole rebase.

The result is a `Rebased`: the new `tree`, a `description` (`rebase: 12 new,
3 gone, 1 node removed`) and `changes`, the **reconciliation list**. It holds one
entry per added keyword (`added`, with its place), per removed keyword
(`removed`, with where it was) and per removed node (`node_removed`, with its
parent): exactly the symmetric difference of the two vocabularies and the
removed nodes. It is `compare(before, after)`, so it can be computed again from
the two versions the history keeps. A rebase onto the vocabulary the tree
already holds, with the same run, changes nothing and returns the tree itself.

## Comparing two trees

`compare(before, after)` lists every difference as `Change` entries: levels
first, then nodes by id, then keywords by text.

| kind | names | `before` → `after` |
| --- | --- | --- |
| `depth` | — | the two depths |
| `level_renamed` | `level` | the two sets of names |
| `node_added`, `node_removed` | `node` | its parent |
| `node_renamed` | `node` | the two sets of names |
| `node_moved` | `node` | the two parents |
| `node_reordered` | `node` | the two orders |
| `node_changed` | `node` | another key of the node changed |
| `added`, `removed`, `moved` | `keyword` | its places: a node id, or `SET_ASIDE` |
| `set_aside_changed` | `keyword` | the two set-aside entries (origin, reason) |
| `review` | `keyword` | the two review states |

`Change.as_dict()` gives the JSON form, without empty fields.

## Versions

`save_themes(project, tree, expected=…, action=…)` writes the tree in its
canonical form through the guarded decision write: it is refused
(`StaleWrite`) when the file changed since it was read with `read_themes`, and
the version it replaces goes to `decisions/history/themes.json/<UTC
time>-<action>.json`. Saving the tree that is already current writes nothing.
The project must be open for writing.

`list_versions(project)` returns every version, the current one first. A
version's id is `current` or its history file's name without `.json`. Its
`made_at` and `made_by` come from the history entry of the version it
replaced (so the first version the history knows has none), `replaced_at` and
`replaced_by` from its own. Entries saved in the same second are ordered by
the time their file was written. `read_version(project, id)` reads one, and
`restore_version(project, id, expected=…)` saves it again as a new version
named `restore <id>`, so a restore is undone like any other change.

## The curated document, until the apply stage reads a tree

The engine's apply stage reads a curated document with two levels: subfields,
concepts, and term indices (rows of the lexical data). `to_curated(tree, terms,
reference_language=…, domain_title=…, scores=None)` writes a depth-2 tree in
that format, and `from_curated(doc, terms, reference_language=…)` reads a
curated document or the grouping stage's draft into a tree. `terms` is the
vocabulary in row order.

- Level-1 nodes are subfields, level-2 nodes concepts, keywords term indices;
  `label` holds the reference language's name, `label_<lang>` the others;
  set-aside keywords are trashed terms.
- A subfield keeps the number of an `s<k>` node and a concept of a `c<k>` node,
  so a tree imported from a draft keeps its numbers and colours; other nodes
  take the next free numbers in tree order.
- Concepts list their first 15 keywords as `top_terms` (by descending `scores`
  when given, else by row), and subfields the first 20 of those as seeds, as
  the apply stage derives them.
- `to_curated` needs the tree rebased on `terms`: it refuses a keyword missing
  from either side.
- The tree's own data travels in keys the engine ignores (`theme_node`,
  `set_aside`, `theme_tree`), so `from_curated(to_curated(tree, terms), terms)`
  gives the tree back exactly.
- `from_curated` names nodes `s<id>` and `c<id>`, uses the default level names
  and places every keyword of `terms`. What a tree cannot hold is set aside
  with a reason and listed in `Imported.notes`: merge variants (merges belong
  in `keywords.csv`), the keywords of subfields not kept, stashed and trashed
  items, keywords in no group; term statuses and pinned colours are dropped. A
  document that places a keyword twice or names a row outside `terms` is
  refused.

## Tests

`tests/test_themes.py`, `tests/test_themes_versions.py` and
`tests/test_themes_curated.py` pin each rule, and run property tests on random
trees of every depth, random vocabulary changes and random sequences of
operations (`tests/themes_random.py`, seeded, no extra dependency):

- every operation returns a valid, canonical tree, leaves its argument
  unchanged and keeps the set of keywords;
- inserting then removing a level gives the tree back;
- a rebase keeps every surviving keyword's place; its reconciliation list is
  the symmetric difference of the vocabularies plus the removed nodes; a
  rebase onto the same vocabulary changes nothing;
- versions read back as saved, and a restore round-trips;
- the converters' round trip is exact, the engine's checks accept the curated
  document, and its apply stage runs on it.
