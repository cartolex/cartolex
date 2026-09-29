# The theme tree

The theme tree of a project lives in `decisions/themes.json` (its format is in
[Decisions](../format/decisions.md)). Three modules work on it:

| module | what it holds |
| --- | --- |
| `cartolex.project.themes` | the operations on a tree, the rebase onto a new vocabulary, the comparison of two trees |
| `cartolex.project.themes_versions` | saving a tree, listing, reading and restoring its versions |
| `cartolex.project.themes_curated` | the converters to and from the engine's two-level curated document (depth 2) |

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
    saved = save_themes(project, result.tree, expected=fp, action=result.description)
    edit = move_keywords(saved.tree, ["storm surge model"], "n2")
```

The theme editor of the app ({doc}`themes-editor`) is built on these
functions: every change it makes is one of the operations below.

## Operations

Each operation returns an `Edit`: the new `tree` and a short `description` of
the change in English (`move 3 keywords to n7`). An undo list keeps the trees;
the description names the step and becomes the action name of the saved
version.

| operation | what it does | refused when |
| --- | --- | --- |
| `rename_node(tree, id, names)` | sets the node's name in each language of `names`; `None` or a blank name removes that language | unknown node or language |
| `rename_level(tree, level, names)` | the same for a level (1 = top) | the level would have no name left |
| `move_keywords(tree, keywords, id)` | moves placed keywords onto a node of any level | unknown node; a keyword is set aside (put it back instead) or unknown |
| `move_node(tree, id, parent, position=None)` | moves a node and everything under it; `position` is its 0-based place among its new siblings (default: last) | the new parent is not on the level just above the node; the node is already there and no position is given |
| `merge_nodes(tree, source, target)` | what `source` holds (keywords and child nodes) goes to `target`, after what it already holds; `source` is removed and set-aside origins follow | the nodes are on different levels, or the same node |
| `split_node(tree, id, parts, ids=None)` | each part `(members, names)` becomes a new sibling right after the node; members are keywords on the node and ids of its child nodes; the members no part names stay | a part is empty, members are not the node's, appear twice or are both a keyword and a child id, or nothing would stay |
| `create_node(tree, parent, names, node_id=None, position=None)` | an empty node, named, under `parent` (`None`: the top level) | the parent is on the deepest level; no name; the id is taken or invalid |
| `delete_node(tree, id)` | removes an empty node | the node holds nodes or keywords |
| `set_aside(tree, keywords, reason="")` | sets placed keywords aside, recording where each came from and why; for a keyword already set aside, changes the reason | a keyword is unknown |
| `put_back(tree, keywords, id=None)` | places set-aside keywords on `id` (any level), or where each came from | a keyword is not set aside; its origin is gone and no target is given |
| `set_review(tree, keywords, state)` | `to_check`, `reviewed`, `kept` (kept from the borderline list) or `None` | unknown state or keyword |
| `set_attribution(tree, keywords, levels)` | how many levels, from the top, placed keywords count toward: `None` (down to their node's level), `0` (none) or `1` up to their node's level − 1 (see below) | a keyword is set aside or unknown; `levels` not below a keyword's node level |
| `prune_empty(tree)` | removes every node with no keyword in its subtree, as every save does | — |
| `insert_level(tree, at, root_names=None)` | adds a level (see below) | the tree has 4 levels already |
| `remove_level(tree, at)` | removes a level (see below) | the tree has one level |

No operation adds or loses a keyword: the set of keywords a tree holds changes
only with a rebase. New nodes get the id `n<k>`, the next free number; a number
that a set-aside origin still names is never given again, so putting a keyword
back never lands in an unrelated node.

## Where a keyword sits, and what it counts toward

A keyword sits on one node, at any level. A keyword on a higher node is broader
than every node below it: « soft matter » belongs to its field, not to a narrow
topic like « swollen gels ». `keywords_at(tree, id)` lists the keywords on a
node itself, `keywords_under(tree, id)` those on it and on every node below.

A keyword's usage counts toward its node and every node above it; a keyword on
a top-level node counts toward that node only. Its attribution lowers that: `n`
counts it toward levels 1 to `n` only (`n` below its node's level), `0` shows
it without counting it anywhere, and no attribution counts it down to its
node's level. At depth 2, a keyword on a theme is what the engine calls a
*subfield-only* term; a keyword on a topic with `1` counts the same way; `0` is
a *ride-along* term.

**The carry rule.** A move, a merge or a split that gives a keyword another
node drops an attribution other than `0`: the keyword then counts down to its
new node's level. `0` always stays. A keyword that keeps its node keeps its
attribution: moving a node, or merging or splitting the node above it, moves
its keywords with it. Setting a keyword aside keeps its attribution in its
set-aside entry; putting it back on the node it came from gives it back, and
anywhere else only `0` comes back.

## Changing the depth

A tree has 1 to 4 levels. Changing the depth keeps every keyword on its node;
the levels of the nodes shift.

`insert_level(tree, at)` adds a level at position `at`, from 1 (a new top
level) to `depth + 1` (a new bottom level):

- **inside** (`2 ≤ at ≤ depth`): every node of level `at − 1` that has child
  nodes gets one new child, with the same names, that takes them over; the
  node's own keywords stay on it. The keywords of the moved nodes stay on them,
  one level lower;
- **at the top** (`at = 1`): one new root takes over every top-level node. It
  is named `root_names`, or with the new level's name;
- **at the bottom** (`at = depth + 1`): no node is added; the level starts
  empty, ready for finer topics that people create and move keywords to.

Every keyword keeps its path; the new level repeats the level above it (or
groups everything, at the top), and people then split, merge or rename it. An
attribution counting toward the new level's position (`at ≤ n`) becomes
`n + 1`, so the keyword counts toward the same nodes and the inserted one.

`remove_level(tree, at)` removes level `at`: each of its nodes dissolves into
its parent (into the top level, when `at = 1`). A removed node's child nodes and
keywords go to its parent, in the order of the removed nodes and then of what
each held, numbered 1, 2, 3…; the keywords on other nodes stay, one level
higher. A removed top-level node has no parent: its keywords are set aside, with
the reason `its node was removed with its level`. Set-aside keywords that came
from a removed node now come from its parent. The names of the removed nodes
are lost from the tree (the previous version keeps them).

Attributions keep counting toward the same remaining nodes: `n` becomes `n − 1`
when it counted toward the removed level (`at ≤ n`), so a keyword that counted
toward the removed top level only counts nowhere (`0`); a keyword moved to its
parent with `n` equal to the parent's level loses its attribution (it counts
down to its node's level, as before); a set-aside attribution that would reach
the new depth is dropped.

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
keyword, the node proposed for it, at any level (computed elsewhere, for
example from the new grouping), or `None`.

1. A keyword the tree already holds stays where it is, placed or set aside,
   with its review state. Proposals for such keywords are ignored.
2. A new keyword goes on its proposed node, marked `to_check`. With `None`
   it is set aside instead, with the reason `new keyword, no place proposed`,
   also marked `to_check`.
3. A keyword that vanished is removed with its review state; a set-aside one
   is dropped.
4. A node whose subtree held keywords before and holds none after is removed,
   with the nodes under it. A node that was already empty stays in the result,
   and the save removes it (a saved tree has no empty node).
5. `based_on` records `run` and the vocabulary's fingerprint.

Nothing else changes: no node is renamed, moved or renumbered, surviving
keywords keep their attribution, and set-aside origins stay as they were, even
when they name a removed node. Nothing is
placed by default either: a new keyword without an entry in `proposals`, or a
proposal that is not a node of the tree, refuses the whole rebase.

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
| `set_aside_changed` | `keyword` | the two set-aside entries (origin, reason, attribution) |
| `attribution` | `keyword` | the two attributions (`None`: every level) |
| `review` | `keyword` | the two review states |

`Change.as_dict()` gives the JSON form, without empty fields.

## Versions

`save_themes(project, tree, expected=…, action=…)` saves a tree:

1. it removes every node with no keyword in its subtree, itself included
   (`prune_empty`, the carry-forward rule: sub-groups left empty are removed
   at save), and appends
   the removed nodes to the action: `move 2 keywords to n7; remove empty nodes
   n3, n5`;
2. it stamps the tree with `saved`: the time and that action;
3. it writes the tree in its canonical form through the guarded decision
   write, refused (`StaleWrite`) when the file changed since it was read with
   `read_themes`; the version it replaces goes to
   `decisions/history/themes.json/<UTC time>-<action>.json`.

It returns a `Saved`: the tree as written, its fingerprint, the action and the
removed nodes. When the pruned tree is the one already current (its stamp
aside), nothing is written (`Saved.written` is false). The project must be open
for writing. A tree being edited may keep empty nodes; only saved versions
never do.

`list_versions(project)` returns every version, the current one first. A
version's id is `current` or its history file's name without `.json`. Its
`made_at` and `made_by` are its own `saved` stamp; a version saved without one
takes them from the history entry of the version it replaced (the action as the
file name spells it), and the first such version has none. `replaced_at` and
`replaced_by` come from its history file's name. Entries saved in the same
second are ordered by the time their file was written.
`read_version(project, id)` reads one, and `restore_version(project, id,
expected=…)` saves it again as a new version with the action `restore <id>`, so
a restore is undone like any other change.

## The two-level curated document

The apply stage reads the tree itself, at any depth
([Themes in the engine](themes-engine.md)). At depth 2 it also writes the
engine's two-level outputs, as they always were, from a curated document with
two levels: subfields, concepts, and term indices (rows of the lexical data).
Those outputs are for the numeric reference and for migrating older projects;
new readers use the tables of any depth. `to_curated(tree, terms,
reference_language=…, domain_title=…, scores=None)` writes a depth-2 tree in
that format, and `from_curated(doc, terms, reference_language=…)` reads a
curated document or the grouping stage's two-level draft into a tree. `terms`
is the vocabulary in row order.

- Level-1 nodes are subfields, level-2 nodes concepts, keywords term indices;
  `label` holds the reference language's name, `label_<lang>` the others;
  set-aside keywords are trashed terms.
- A keyword on a topic is a term of its concept, and its attribution the
  term's status: none is *defining*, `1` *subfield-only* (`subfield_only_terms`),
  `0` *ride-along* (`ride_along_terms`).
- **A keyword on a theme itself** goes to one more concept of that subfield,
  written after its topics: named like the subfield, numbered after every other
  concept, and marked `theme_keywords_of` (the theme's node id). Each of its
  terms is *subfield-only* (`0`: *ride-along*), so the engine credits it to the
  subfield's share only, exactly as it credited the old subfield-only terms,
  and that concept's own share is zero. The weights of every subfield and of
  every other concept are those of the same terms marked subfield-only in any
  concept of the subfield (a test compares them). A per-theme concept, rather
  than one of the theme's topics, keeps a broad keyword out of a narrow topic
  (it is drawn with its own shade, labelled with the theme) and holds the
  keywords of a theme that has no topic at all.
- A trashed term's `status` carries a set-aside keyword's attribution. The
  engine matches statuses without case, so `to_curated` refuses two keywords of
  one node that differ only by case and have different attributions.
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
- `from_curated` reads a `theme_keywords_of` concept's terms onto the theme. In
  a document the engine wrote (concepts without the `theme_node` key), a
  subfield-only term goes onto its subfield's node — what the status meant —
  and the notes count them; in a concept `to_curated` wrote, it stays on its
  topic with attribution `1`. Ride-along terms stay on their topic with `0`.
- `from_curated` names nodes `s<id>` and `c<id>`, uses the default level names
  and places every keyword of `terms`. What a tree cannot hold is set aside
  with a reason and listed in `Imported.notes`: merge variants, the keywords of
  subfields not kept, stashed and trashed items, keywords in no group. Term
  statuses become attributions (kept in the set-aside entry of a keyword set
  aside); a status naming no keyword of its concept, and pinned colours, are
  dropped and noted. A document that places a keyword twice or names a row
  outside `terms` is refused.
- Merges belong in `keywords.csv`: `Imported.merges` lists each merge variant
  with the keyword it merges into, and `Imported.keyword_rows(language=…,
  decided_at=…)` turns them into `keywords.csv` rows (decision `merge`, source
  `person`).

## Tests

`tests/test_themes.py`, `tests/test_themes_versions.py` and
`tests/test_themes_curated.py` pin each rule, and run property tests on random
trees of every depth, random vocabulary changes and random sequences of
operations (`tests/themes_random.py`, seeded, no extra dependency):

- random trees place keywords on nodes of every level; every operation returns
  a valid, canonical tree, leaves its argument unchanged and keeps the set of
  keywords; only `set_attribution` and depth changes set an attribution; any
  other keeps it for a keyword that keeps its node and keeps only `0` for one
  that gets another node;
- inserting then removing a level gives the tree back;
- a rebase keeps every surviving keyword's place; its reconciliation list is
  the symmetric difference of the vocabularies plus the removed nodes; a
  rebase onto the same vocabulary changes nothing;
- every save removes the empty nodes and names them; versions read back as
  saved, each with its own action, and a restore round-trips;
- the converters' round trip is exact, keywords on themes and attributions
  included; the engine's checks accept the curated document and read the same
  placements and statuses; its apply stage runs on it and gives a theme's own
  keyword the weights of the old subfield-only status.
