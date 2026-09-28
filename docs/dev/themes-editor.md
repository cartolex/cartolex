# The theme editor

The themes screen (`/themes`, `static/pages/themes.js`) edits the project's
theme tree ({doc}`themes`) at any depth, one to four levels. It shows one
tree in three views kept in sync, with a side panel, and every change goes
through the operations of `POST /api/themes/ops`; nothing reaches
`decisions/themes.json` before someone saves.

## Layout

| part | what it shows | keyboard |
| --- | --- | --- |
| outline (left) | the tree at any depth, each node with its keywords (under it), its share of the use, its keywords on demand; a search over every keyword and node name; the « Set aside » tray and the « To check » queue as tabs | arrows, Home, End, PageUp, PageDown, Right and Left to open and close, letters for type-ahead, Enter to open in the panel, F2 to rename, Delete, Shift+F10 or the Menu key for the actions, Space and Shift for ranges of keywords, Ctrl+A for a node's keywords |
| treemap (centre) | nested rectangles sized by the use of the keywords counting toward each node, the top level by hue family, a hatched part for a node's own keywords; zoom into a node and back | arrows choose, Enter zooms in, Escape zooms out |
| map (centre, a tab) | the people and keywords of `GET /api/atlas` coloured by top-level node (keywords by the tree being edited, people by the last apply), the top-level names at their keywords' centre, the selection highlighted | arrows move, + and − zoom, 0 fits |
| side panel (right) | the selected node (its keywords with their use, the people who weigh most on it, its names per language, what it had in the saved version and on the map), keywords (their use, place, review and attribution) or person | Escape goes back to the outline |

Page keys: Ctrl+Z undo, Ctrl+Shift+Z or Ctrl+Y redo, Ctrl+S save, `/` search.
Selecting in one view selects in the others: a node chosen in the treemap or
the map opens its path in the outline.

## Actions

Every action has a menu item (the context menu, the panel's buttons, the
toolbar's « Levels » and « More ») and a key; drag and drop is only a
shortcut: keywords or a node dropped on a row of the outline or a rectangle of
the treemap (keywords onto any node; a node under a node of the level above).

| action | where | operation |
| --- | --- | --- |
| rename (per language) | F2, menu | `rename_node` |
| move to… | menu, drag | `move_keywords`, `move_node`, `put_back` |
| merge with… | menu | `merge_nodes` (same level only) |
| split | menu: tick keywords and child nodes, name the new node | `split_node` |
| create | toolbar, menu (inside, beside) | `create_node` |
| delete | Delete, menu (empty nodes only) | `delete_node` |
| set aside, with a reason | Delete on keywords, menu | `set_aside` |
| put back | the tray's menu (where it came from, or into…) | `put_back` |
| attribution | menu: its own level, down to a level, nowhere | `set_attribution` |
| insert or remove a level, rename a level | « Levels » | `insert_level`, `remove_level`, `rename_level` |
| accept, move or set aside what a rebase added | the « To check » queue (A, M, S; J and K) | `set_review` with the change |

New nodes get their ids from the browser (`n<k>`, the next free number, as the
server would): the operations of the undo list are self-contained, so they can
be applied again to another tree (below).

## Nothing is lost

- **Undo and redo** of every operation, unlimited within the visit. An entry
  holds the operations, their descriptions (from `POST /api/themes/ops`,
  shown in the interface language with the nodes' names at that time: « Merge
  “Tidal flats” into “Estuaries” ») and a patch of what changed: undo and redo
  need no call, and a long session costs little memory.
- **The draft** is kept in the browser's local storage, per project
  (`cartolex.themes-draft/1:<project id>`): the tree, the version of the
  saved tree it started from, and the undo list. It is written right after
  each change is on screen and whenever the tab is hidden or closed, so it
  survives a reload and a crash of the tab or of the browser; the page
  restores it with a notice (« Your unsaved changes … were restored »).
  *Why the browser and not a draft route:* a draft is personal and unfinished.
  A file in the project would be written at every operation, into a folder
  that other people of a hosted project see and that file sync copies around;
  the project's decisions stay what people saved. The cost: a draft stays in
  the browser it was made in, and a private window loses it when closed.
- **Leaving** with unsaved changes asks in the page (« Leave, keep the draft »
  or « Stay »), never with the browser's `confirm()`; closing the tab asks the
  browser's own question.
- **Save** writes a new version with `If-Match` (the version the edits started
  from); its action names the steps since the last save. Saving removes the
  empty nodes and says which; that removal is a step of the undo list too.
- **Reload and merge.** A save refused because someone saved meanwhile (412),
  or a draft made on an older version, re-applies the draft's operations to
  the newest tree (`POST /api/themes/ops` with `lenient`); what no longer
  applies is listed with the reason, and nothing is saved until the person
  saves.
- **Versions**: the list (when, by which action), a version opened read-only,
  compared with the current tree (keywords added, removed and moved, nodes
  renamed, added, removed, moved), restored (a restore is a new version).

## Review and the vocabulary

- The **« To check » queue** lists the keywords a rebase added, each with its
  proposed place; accepting, moving or setting one aside marks it
  `reviewed`, saved with the tree.
- A **banner** says when the tree is based on another vocabulary than the
  current one, with « Rebase onto the current vocabulary »
  (`POST /api/themes/rebase`); its new keywords then wait in the queue.
- A **clustering-only change** (the grouping ran again with other parameters
  on the same keywords) leaves the tree as it is: a banner offers the new
  proposal as a comparison with the tree, then « Adopt the proposal » or
  « Keep my tree », once (`POST /api/themes/proposal`).

## Apply

« Save and apply » saves if needed and starts `POST /api/themes/apply` in the
background: a banner and the header's Activity indicator follow the job, the
editor stays usable, the map and the treemap's people refresh when it ends,
and a failure shows an error card with its code and what to do.

## AI curation through the theme handoff

« More › AI curation… » exports the tree being edited
(`POST /api/themes/handoff/export`; the format is in {doc}`api`): the
instructions to paste, `tree.txt` to attach, `bundle.json` to keep. The
answer, pasted or opened, is read back (`POST /api/themes/handoff/import`,
kept in `decisions/history/ai/`), and shown as a list: each operation with the
assistant's reason, those that cannot apply to the tree with why, the lines
that could not be read. The person chooses, previews the chosen ones on the
tree (read-only), and applies them: one step of the undo list.

**The blind test.** `tools/themes_handoff_lab.py bundle FOLDER` writes the
handoff of the S demo world's proposal (one level, 15 themes, 454 keywords;
about 4 000 tokens); a fresh assistant that saw only the instructions and
`tree.txt` answered; `tools/themes_handoff_lab.py score FOLDER` scores each
operation, applied alone, against the world's known themes (a keyword's
theme is the primary theme of most works that use it):

| action | proposed | improves | neutral | worsens |
| --- | --- | --- | --- | --- |
| rename | 9 | 7 | 1 | 1 |
| move | 48 | 13 | 32 | 3 |
| merge | 6 | 6 | 0 | 0 |
| split | 1 | 0 | 0 | 1 |
| set aside | 51 | 35 | 5 | 11 |
| attribution (nowhere) | 21 | 9 | 8 | 4 |
| all | 136 | 70 (51 %) | 46 (34 %) | 20 (15 %) |

Every line was read (none unreadable) and every operation applied. Applying
them all, in order, raises the B-cubed F1 of the top level against the known
themes from 0.548 to 0.679, and leaves 10 top-level nodes for the world's 12
themes. The neutral moves are mostly of keywords the world spreads over
several themes; what worsens is setting aside or counting nowhere keywords
that do belong to one theme.

## Components

The screen uses the component library and adds three components to it:

| component | what it does |
| --- | --- |
| `TreeView` | a virtualised tree (the rows in view only) with the tree pattern's keyboard, single and range selection, type-ahead, context menu, drag and drop, and a caller's own keys (the queue's A, M, S) |
| `Treemap` | squarified nested rectangles by weight, hue families from `--cx-hue-1` to `--cx-hue-12`, labels that fit or are cut, zoom, keyboard, drop targets |
| `MapFrame` | points on a canvas: a scene (layers of points, a palette of colours or tokens, a highlight), a view (pan, zoom, fit), hit testing through a grid, resize, theme changes, and a renderer interface (`resize`, `draw`, `invalidate`, `destroy`) that a WebGL renderer can take over |

## Measures

On the L demo world (depth 2: 2 955 keywords, 163 nodes; the app on the same
computer, measured in the page): the page is ready in about 0.2 s; a search
over every keyword updates the outline in 3 to 25 ms; a rename, a move or a
set aside shows in 20 to 50 ms (the tree shows the change at once, then the
server's tree, the one kept, replaces it); an undo in 15 to 20 ms. The map
pans 10⁴ points at 60 frames a second (`tests/browser/test_themes.py`). The
first read after the app starts loads the scientific libraries for the
keywords' use (about 0.5 s): the tree shows first, sized by its keyword
counts, then by use.
