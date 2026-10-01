# The theme editor

The themes screen (`/themes`, `static/pages/themes.js` and its modules in
`static/pages/themes/`, listed in {doc}`ui`) edits the project's
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
- **Versions**: the list (when, by which action; the nodes an action names
  are named as in the versions around it, so a node merged away since keeps
  its name), a version opened read-only,
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
  « Keep my tree », once (`POST /api/themes/proposal`). An apply made while
  the question waits does not drop it silently: it keeps the tree over the
  proposal as a version of its own (« keep the curated tree over the proposal
  … at an apply », in the versions list), and a one-line notice says so. A
  rebase agrees with the grouping it placed the new keywords from: the
  question comes back only if the grouping runs again after it.

## Borderline keywords and suggested places

- The **« Borderline » tab** lists the placed keywords nearest the border
  between their node and another (`POST /api/themes/borderline`, paged,
  smallest margin first), each with the other node and its margin; a
  « Compare at » choice compares every keyword at one level (through its
  node's ancestor there) instead of its own node's. Its keys: A keeps a
  keyword here (`set_review` kept: reviewed, and it leaves the list; a review in the « To check » queue leaves it listed), O moves it to the
  other node (`move_keywords`), S sets it aside, J and K move; the same
  actions are in its context menu.
- **Suggested places**: a keyword set aside or « to check » shows its three
  nearest nodes in the side panel (`POST /api/themes/suggestions`), and the
  first in its row of the tray or the queue; a button, or 1, 2 or 3 in the tray
  or the queue, puts it there (`put_back`, or `move_keywords` and
  `set_review` from the queue): one step of the undo list.

**The measure** (`cartolex.lexicon.theme_fit`): the margin of a keyword is
`cos(k, own) − cos(k, other)` in the keywords' space (`themes.space`), where
`own` is the centroid of the other keywords of its node (the keyword left
out) and `other` the nearest centroid of another node of the same level; a
centroid is the renormalised mean of the L2-normalised vectors of the
keywords on or under the node. Why: the grouping cut the same space with Ward's
method on these normalised vectors and centroids, so the margin says how near
the keyword came to being grouped elsewhere; leaving it out of its own node
removes its pull on a small node; one level at a time keeps a node from
competing with its parent; a silhouette over every pair of keywords would say
much the same at a cost that grows with the square of the vocabulary. A
suggestion's score is the cosine to the centroid of the keywords placed on a
node. Both read the tree being edited, so they follow each change; they are
asked only while shown, a quarter of a second after the last change, and cost
a few milliseconds on S.

## Playground

The centre column's third tab, « Playground » (`playground/`: `panel.js`,
`controls.js`, `store.js`, `icicle.js`, `balance.js`, `adopt.js`), lets the
curator try the grouping's settings and see the tree **before** adopting it or
sending it to AI. While it is shown it takes the outline's and the side
panel's room.

- **Controls** along the top, each the shared `ParamControl` of its parameter:
  the levels (1 to 4), the keywords per topic and the top themes, or the size
  of each level by hand; the comb and its θ (« Calibrated », with the θ the
  calibration kept); the space (people or texts). They start from the
  project's parameters (`GET /api/params`, read when the tab opens) and stay
  for the visit; a save of the « Tune » panel shows here, and the other way
  round.
- **The tree as an icicle**, one column per level, aligned: a node's children
  sit beside it in the next column, in one group whose total height is the
  parent's. A block's height follows its keyword count, never below the room
  of its label; the node's own keywords show as a hatched band at its foot and
  as « N its own ». The top level is in its hue family, the levels below in
  tints of it. A parent of more than 20 children (the top level: 60) shows its
  largest and « + N more » inside its own span. The whole icicle scrolls as
  one, the levels' names staying in view; hovering or focusing a node
  highlights its path (ancestors and descendants). A block opens its most used
  keywords in a popover. Keys: one tab stop, ↑ ↓ within a level, ← → to the
  parent and the first child, Enter or Space, Escape.
- **The side panel**: the balance across levels (per level, the share of the
  keywords on its nodes, and the keywords on each node, median and middle
  half, against the target: few keywords on the top level, about as many on
  each node of a level); against the editor's tree (themes split and merged,
  keywords that would move to another theme, be set aside or placed); then
  « Adopt as my draft » and « Send to AI ».

A change regroups **in the background** (`POST /api/themes/playground`, a job
of the `preview` group), 0.4 s after the last change, cached by the settings
and the runs it read; a newer change supersedes the job (the request names it,
the server cancels it) and the newest settings are asked for when it ends.
Nothing is saved. The preview runs the build's own runners, `themes.group`'s
and, when the space's unit changes, `themes.space`'s, into a scratch folder
(`cartolex.app.playground`): it is what a build with those settings proposes.
**Stability** per node (the copilot kit's measure) is not shown: it regroups a
space fitted again three times, many times a preview's cost.

« **Adopt as my draft** » writes the settings to `params.json` as the Tune
panel's Save does, builds `themes.group` (a build that would also run other
stages goes to the pre-flight sheet instead), carries the curator's work onto
its proposal (`POST /api/themes/carry`, `cartolex.project.themes_carry`:
names, set-asides, attributions and reviews kept wherever a node continues,
a node of the proposal holding more than half of an old node's keywords and
taking more than half of its own from it) and puts it in place of the
editor's tree as one step of the undo list: the draft, saved by Save as any
other edit; the « new grouping » question does not come back for it.
« **Send to AI** » adopts, then opens the copilot's dialog on the draft (the
theme curation has no API route).

## Apply

« Save and apply » saves if needed and starts `POST /api/themes/apply` in the
background: a banner and the header's Activity indicator follow the job, the
editor stays usable, and a failure shows an error card with its code and what
to do. What depends on people is not recomputed while editing: people's
shares (the side panel's « people who weigh most »), their colours and the
map itself refresh when the apply ends, and the editor says so under the map
and in the side panel. The keywords' places, their colours on the map and
the treemap's areas follow each edit at once.

## Curate with AI

« Curate with AI », in the editor's header, opens the copilot's dialog
({doc}`copilot`): the tree being edited, unsaved edits included, goes into a
bundle an assistant that runs code works from on its own, with the tree's
levels and measures, the curator's standing rules and cartolex's kit. Its
`result.json` comes back through the same dialog (or a result imported before
is opened again), and is shown as a list: each change with the assistant's
reason, those that cannot apply to the tree with why. The person chooses,
previews the chosen ones on the tree (read-only), and applies them: they are
saved at once, as a version whose action starts with `ai-copilot:`.

Earlier versions also offered a *handoff* (instructions and the tree as text
to paste in a chat, the answer pasted back); the answers they imported stay
readable ({doc}`api`), and open in the same review.

## Components

The screen uses the component library and adds three components to it:

| component | what it does |
| --- | --- |
| `TreeView` | a virtualised tree (the rows in view only) with the tree pattern's keyboard, single and range selection, type-ahead, context menu, drag and drop, and a caller's own keys (the queue's A, M, S) |
| `Treemap` | squarified nested rectangles by weight, hue families from `--cx-hue-1` to `--cx-hue-12`, labels that fit or are cut, zoom, keyboard, drop targets |
| `MapFrame` | points on a canvas: a scene (layers of points, a palette of colours or tokens, a highlight), a view (pan, zoom, fit), hit testing through a grid, resize, theme changes, and a renderer interface (`resize`, `draw`, `invalidate`, `destroy`); WebGL draws it when the browser has it, Canvas 2D otherwise ({doc}`ui`) |

## Measures

On the L demo world (depth 2: 2 955 keywords, 163 nodes; the app on the same
computer). The browser check keeps measuring the page's readiness and the
search (`tests/browser/test_theme_editor.py`, in
`.cache/check/ui-measures.json`); the other lines were measured while the
screen was built, with the same harness:

| measure | budget | measured |
| --- | --- | --- |
| page ready | < 1 s | 0.24 s |
| search over every keyword, until the outline shows the matches | < 100 ms | 4 to 18 ms |
| an operation's feedback (a rename, from Enter to the outline) | < 100 ms | 21 to 43 ms |
| undo | < 100 ms | 20 to 23 ms |
| map pan, 10⁴ points | 60 frames a second | a frame every 16.7 ms (median and 95th percentile) |
| ten visits of the editor and back | no leak | DOM nodes +0, listeners +0 |

A rename, a move, a set aside or a review shows at once in the tree (the
browser applies it by the same rules), then the server's tree, the one kept,
replaces it; merges, splits and levels wait for the server (about 0.1 s on L,
with a spinner beside the status meanwhile). The first read after the app
starts loads the scientific libraries for the keywords' use (about half a
second): the tree shows first, sized by its keyword counts, then by use.
