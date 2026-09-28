# Themes in the engine, at any depth

The project's theme tree ({doc}`themes`) has 1 to 4 levels, keywords on nodes of
any level and per-keyword attributions. This page says how the engine builds
such a tree (`themes.group`), applies it (`themes.apply`) and hands it to the
map, the trajectories, the projected people and the bundle, and how depth 2
stays identical to the two-level engine it replaces.

## The tree inside the engine

The engine reads and writes a tree as the project's own document,
`cartolex-themes/1` (the shape of `decisions/themes.json`, keyed by keyword
text). The engine never imports the project package: it reads that JSON into
an `EngineTree` (`cartolex.lexicon.theme_tree`) over the rows of the lexical
data:

| field | what it holds |
| --- | --- |
| `depth`, `level_names` | 1 to 4 levels, from the top, and their names per language |
| `nodes` | each node's id, parent, level, names per language and order, in tree order (depth first, siblings by `order` then id) |
| `node_of` | per keyword row: the index of its node, or −1 (set aside, or not in the tree) |
| `counts_to` | per keyword row: how many levels from the top its usage counts toward: its node's level, lowered by its attribution (`0`: nowhere) |

A keyword counts toward the level-`k` ancestor of its node for every
`k ≤ counts_to`. For each level, a sparse keywords × nodes matrix `M_k`
(one 1 per counting keyword) turns usage into node weights. The build layer
validates the document with `ThemesFile` before the engine reads it; the
engine refuses a keyword it does not know and does not count one the tree does
not hold.

## `themes.group`: the proposal

1. **The finest level** is the term clustering, as today: Ward on the
   L2-normalised leading SVD components of the keywords, cut at the finest
   level's size; exact below a size threshold, micro-clusters then Ward above
   it (below).
2. **Each coarser level** is a Ward cut, at that level's size, of the
   L2-normalised centroids (from their keywords) of the nodes of the level
   below: at depth 2 exactly today's cut of the topics into themes.
3. **Nodes.** Ids: `s<k>` on the top level, `c<k>` on the finest, `m<l>-<k>`
   on a level `l` between them, `k` being the group's number in its cut (at
   depth 2: today's subfield and concept ids). Order among siblings by number.
   Names: the dominant keyword (highest summed score) of the node's keywords in
   the reference language, its French form from the consolidation pairs, the
   reference form in other display languages, as the draft does today.
4. **Keywords** sit on the finest level (at depth 1 on the only level), with no
   attribution; nothing is set aside.

The stage writes `themes_draft.json` (a `cartolex-themes/1` tree, `based_on`
the vocabulary it grouped) at every depth, and, when the depth is 2, today's
`subfields_draft.json` too, unchanged. A test checks that at depth 2 the
proposal is `from_curated(subfields_draft.json)` exactly. The level sizes come
from `theme_levels(...)`; the engine no longer warns that it builds two.

## `themes.apply`: the weights

The stage applies `decisions/themes.json` when it exists (rebased by
`prepare`), else the proposal, at any depth. With `X` the people × keywords
matrix of the weights basis (TF by default):

- **usage shares.** Each person's row is L1-normalised over the keywords the
  tree places (every placed keyword, whatever its attribution): each person
  who uses the tree carries one unit of mass;
- **person weights.** For level `k`, `W_k = shares · M_k`: a node's weight is
  the person's mass on the keywords counting toward it; its **share** is that
  weight over the person's weights at that level (each level sums to 1 for a
  person with usage there);
- **organisations.** The sum of their mapped people's weights (each person one
  unit), shares per level likewise;
- **keyword weights.** The column sums of the shares (as `lexicon_weights.csv`);
- **node weights.** A node's weight is the weights of the keywords whose
  counting stops at it (in tree order, then by row) plus its children's
  weights, each rounded to six decimals; its share is that weight over the
  number of people with usage. This is today's rule (a subfield is its
  subfield-only terms plus its rounded concepts), so depth 2 gives the same
  numbers bit for bit;
- **top keywords.** The 15 keywords counting toward the node with the highest
  summed score (ties by row), as a concept's `top_terms` today.

**Never dense.** `X` stays sparse. The shares are computed by chunks of people
(a chunk of rows is made dense over the placed keywords, sized to a fixed
memory budget): the row sums then use numpy's own summation, and the column
sums add the rows in order, so the numbers equal today's dense computation bit
for bit (checked on every supported numpy). Today's `compute_lexicon_weights`
uses the same chunked core.

Tables written into `derived/themes.apply/`:

| file | rows | columns |
| --- | --- | --- |
| `themes_applied.json` | the tree applied | depth, levels, nodes (id, parent, level, order, names, colour, weight, share, top keywords; map position after the layout), the basis, the people counted, what was applied |
| `theme_keywords.csv` | one per placed keyword | `term, term_index, node, level, counts_to, weight, share` |
| `theme_people.parquet` | one per (person, node) with a weight | `researcher_id, person_id, level, node, weight, share` |
| `theme_organisations.parquet` | one per (organisation, node) with a weight | `unit, level, node, weight, share, people` |

When the applied tree has depth 2 the stage also writes today's `curated.json`
(by `to_curated`, for a curated tree), `subfields.json`,
`subfield_weights.csv` and `lexicon_weights.csv`, unchanged: they are what
the drift baseline compares, and what readers of the two-level document still
use.

## Consumers

| consumer | at any depth |
| --- | --- |
| `prepare` of `themes.apply` | a new keyword goes to the curated node that holds a strict majority of the other keywords of its proposal group (its finest group in the new `themes_draft.json`), looked for from the finest level up: the level-`l` ancestor of each such keyword's node votes; the first level where one node has more than half of them wins. No majority, or no known keyword in the group: set aside, « to check » |
| `map.layout` | keeps re-applying the themes after the layout; each node gets a map position (the weighted mean of its people's positions); the anchored layout reads the finest level's groups |
| plots | a keyword takes its node's colour; top-level names at their positions |
| `map.trajectories` | `trajectory_themes.parquet`: each time window's weights at every level; `trajectory_windows.json` keeps its two-level weights at depth 2 |
| `overlays.position` | each projected person gets `levels`: weights and shares at every level, from the terms that place them; `themes` and `topics` stay at depth 2 |
| map bundle | optional `themes.json` (the tree: nodes, levels, parents, names, colours) and `theme_weights.csv` (entity, level, node, weight, share): a bundle holding them is `map_bundle/3`; one without is `map_bundle/2`, as today; the reader takes both. Building a bundle and reading one back no longer turns the matrix dense |

**Colours.** One hue per top-level node: `s<k>` takes palette entry `k`, other
top-level ids the next free entries in tree order (today's rule for curated
trees). Each descendant takes a shade of its top node's hue, by its place among
that node's descendants in tree order (depth 2: today's concept shades). The
same ids give the same colours on every rebuild.

## On disk

New: `themes.group/themes_draft.json`; `themes.apply/themes_applied.json`,
`theme_keywords.csv`, `theme_people.parquet`, `theme_organisations.parquet`;
`map.layout/themes_applied.json` (with positions); `map.trajectories/
trajectory_themes.parquet`; `levels` in `positions.json`. Written only at
depth 2: `subfields_draft.json`, `curated.json`, `subfields.json`,
`subfield_weights.csv`, `lexicon_weights.csv`, and the two-level weights in
the trajectory windows and positions. The stage versions of `themes.group`,
`themes.apply`, `map.layout`, `map.trajectories` and `overlays.position` rise,
so earlier results need an update.

## Depth 2 stays identical

The engine functions the workspace run calls are unchanged in their outputs;
the project runners add the generic files beside today's. Whatever the drift
baseline compares (the clustering, the proto-subfields, the draft, the applied
document, the person and lexicon weights, the trajectories, the projection, the
bundle) is computed by today's code, from today's inputs, at every size under
the clustering threshold; the chunked weights and the sparse bundle are proved
bit-identical by tests and by the full check (directly and through a project).
Tests also check, on the demo, that the generic tables at depth 2 equal the
two-level outputs exactly.

## Clustering at any size

Scipy's Ward stores every pairwise distance twice (the condensed matrix and its
working copy): about 8·n² bytes, 40 GB at 10⁵ keywords.

- **Exact Ward** below a threshold of keywords chosen to keep the stage's
  measured peak under about 2 GB (about 15 000 keywords before measuring); the
  demo worlds and the reference stay on this path, unchanged.
- **Above it: micro-clusters, then Ward.** Mini-batch k-means (fixed seed)
  into `m = min(n, threshold)` micro-clusters, then Ward on their centroids
  weighted by their sizes: the merge cost of Ward depends on the sizes of the
  clusters, so a plain Ward on the centroids would be wrong; the engine runs
  its own nearest-neighbour-chain Ward with the Lance–Williams update with
  sizes (checked against scipy with unit sizes). Each keyword takes its
  micro-cluster's group. A level asking for more groups than half the
  micro-clusters is the k-means partition itself.
- The coarser levels use the same function: exact below the threshold (a
  level rarely holds more than a few thousand nodes).

Measures (agreement with exact Ward: adjusted Rand index and share of keywords
whose group changes; time and peak memory at 10³, 10⁴ and 10⁵ keywords) are
added here as they are made.

## Open choices

- The per-person weights are evidence-based (usage). Today's
  `subfield_weights.csv` (similarity to the theme's seed keywords in the SVD
  space) stays at depth 2 only; no generic equivalent is written.
- The projected people's level weights use the vector that places them (their
  top keywords, TF-IDF with the length bonus), not a TF vector.
- An organisation is the engine's group (`unit`), not an `org_id`.
