# Themes in the engine, at any depth

The project's theme tree ({doc}`themes`) has 1 to 4 levels, keywords on nodes of
any level and per-keyword attributions. This page says how the engine builds
such a tree (`themes.group`), applies it (`themes.apply`) and hands it to the
map, the trajectories, the projected people and the bundle, and how depth 2
stays identical to the two-level engine it replaces. The files are described in
{doc}`../format/derived`.

## The tree inside the engine

The engine reads and writes a tree as the project's own document,
`cartolex-themes/1` (the shape of `decisions/themes.json`, keyed by keyword
text), without importing the project package. `cartolex.lexicon.theme_tree`
reads it into an `EngineTree` over the rows of the lexical data:

| field | what it holds |
| --- | --- |
| `depth`, `level_names` | 1 to 4 levels, from the top, and their names per language |
| `nodes` | each node's id, parent, level, order and names, in tree order (depth first, siblings by `order` then id) |
| `node_of` | per keyword row: the index of its node, or −1 (set aside, or not in the tree) |
| `counts_to` | per keyword row: how many levels, from the top, its usage counts toward: its node's level, or its attribution (`0`: none) |

A keyword counts toward the level-`k` ancestor of its node for every
`k ≤ counts_to`; `membership(k)` is that as a sparse keywords × nodes matrix.
The engine refuses a tree that holds a keyword the vocabulary does not have
(the build rebases the tree first) and counts nowhere a keyword the tree does
not hold.

## `themes.group`: the proposal

1. **The finest level** is the term clustering, as before: Ward on the
   L2-normalised leading SVD components of the keywords, cut at the finest
   level's size; exact below a size threshold, micro-clusters then Ward above
   it (below).
2. **Each coarser level** is a Ward cut, at that level's size, of the
   L2-normalised centroids (from their keywords) of the nodes of the level
   below (`cartolex.atlas.hierarchy.level_groups`): at depth 2 exactly the
   cut of the topics into themes the draft always made.
3. **Nodes.** Ids: `s<k>` on the top level, `c<k>` on the finest, `m<l>-<k>`
   on a level `l` between them, `k` being the group's position on its level
   (at depth 2: the subfield and concept ids). Siblings are ordered by it.
   Names, in each display language: the form of the node's most used keyword
   (highest summed score) that has one there (its own language, or a form the
   consolidation pairs attest), else the reference-language form of the most
   used keyword that has one, else the most used keyword; siblings' names are
   kept distinct in each language (`cartolex.lexicon.labels.node_names`).
4. **Keywords** sit on the finest level (at depth 1 on the only level), with no
   attribution; nothing is set aside.

The stage writes `themes_draft.json` at every depth, `based_on` its run and the
vocabulary, and, at depth 2, the two-level `subfields_draft.json` too,
unchanged. At depth 2 the proposal is the two-level draft read as a tree
(`from_curated`), names included (a test checks this). The level sizes come
from `theme_levels(...)`.

## `themes.apply`: the weights

The stage applies `decisions/themes.json` when it exists (rebased by its
`prepare`), else the proposal, at any depth, and keeps a copy of the tree it
applied (`themes_tree.json`). With `X` the people × keywords matrix of the
weights basis (TF by default):

- **usage shares.** Each person's row is divided by its sum over the keywords
  the tree places (every placed keyword, whatever its attribution): each
  person with usage carries one unit;
- **person weights.** For level `k`, `W_k = shares · M_k`: a node's weight is
  the person's share on the keywords counting toward it; its **share** is that
  weight over the person's weights on level `k` (each level sums to 1 for a
  person with usage there);
- **organisations** (the engine's groups, `unit`) add up their people's
  weights, shares per level likewise;
- **keyword weights** are the column sums of the shares, as
  `lexicon_weights.csv` always was;
- **node weights.** A node's weight is the weights of the keywords whose
  counting stops at it (by the post-order of their nodes, then by row), plus its
  children's weights, each rounded to six decimals; its share is that sum over
  the people counted. This is the two-level rule (a subfield is its
  subfield-only terms plus its rounded concepts), so depth 2 gives the same
  numbers bit for bit;
- **top keywords** are the 15 keywords counting toward the node with the highest
  summed score (ties by row).

**Never dense.** The matrix stays sparse. Each person's total is added in
column order, which is how numpy adds up the rows of the dense matrix it lays
out column by column when it selects the placed keywords. The keyword weights
are summed by chunks of keywords, each chunk made dense for every person and
summed with numpy's own summation of a column, so they are those of the dense
matrix bit for bit (checked against the dense computation on every supported
numpy, beyond numpy's 8 192-element buffer). The person weights are sparse
products. The two-level `compute_lexicon_weights` uses the same functions.

When the applied tree has depth 2 the stage also writes the two-level
documents, unchanged: `curated.json` (by `to_curated`, for a curated tree),
`subfields.json`, `subfield_weights.csv` and `lexicon_weights.csv`.

**The rule for readers.** The app, the site and every new consumer read only
the files of any depth (`themes_draft.json`, `themes_applied.json`,
`theme_keywords.csv`, `theme_people.parquet`, `theme_organisations.parquet`,
`trajectory_themes.parquet`, the `levels` of `positions.json`). The two-level
files exist for the reference comparison and for migrating older projects;
nothing new reads them.

**What changes for a reader of the map.** A person's themes now come from their
keywords: their pie shows the themes whose keywords they use, in proportion to
that use, where the two-level `subfield_weights.csv` gave a slice to every theme
near them in the space. The main theme stays the same for about nine people in
ten; the order of the smaller slices changes more. At depth 2
(`python tools/theme_shares_study.py`; per person, Spearman correlation of the
two readings over every theme, whether the top theme agrees, and how many
themes get a share):

| world | people | themes | ρ (quartiles) | same top theme | themes with a share: usage / proximity |
| --- | --- | --- | --- | --- | --- |
| S, reference settings (12 themes over 150 topics) | 38 | 12 | 0.66 · 0.74 · 0.83 | 100 % | 3 / 9 |
| L, reference settings | 343 | 12 | 0.58 · 0.72 · 0.80 | 89 % | 5 / 9 |
| S project at depth 2 (15 over 23) | 38 | 15 | 0.59 · 0.70 · 0.78 | 87 % | 3 / 12 |

## Consumers

| consumer | at any depth |
| --- | --- |
| `prepare` of `themes.apply` | a new keyword goes to the node of the curated tree that holds a strict majority of the other keywords of its group (its node in the new `themes_draft.json`) that the tree places: from the deepest level up, each such keyword votes for the ancestor of its node on that level (a keyword on a higher node votes only from its own level up); the first node with more than half of the votes wins. A tie or no majority on every level, or no placed keyword in the group: set aside, « to check » (`cartolex.build.engine.proposed_places`) |
| `map.layout` | applies the tree again after the layout (`themes_applied.json` with each node's map position: the weighted mean of its people's positions); the anchored layout anchors on the keywords of each node of the finest level |
| plots | each keyword in its node's colour, the top-level names at their positions (`plots.theme_view`); the trajectory figures colour the keywords behind the paths the same way |
| `map.trajectories` | `trajectory_themes.parquet`: each time window's weights on every level; `trajectory_windows.json` keeps its two-level weights at depth 2 (empty lists at other depths) |
| `overlays.position` | each projected person's `levels`: weights and shares on every level, from the vector that places them (their kept terms, TF-IDF with the length bonus); `themes` and `topics` (by proximity) at depth 2 only |
| map bundle | `build_bundle(..., themes=…, theme_weights=…)` adds `themes.json` (`map_themes/1`: levels, nodes, parents, names, colours) and `theme_weights.csv` (`entity_id, level, node, weight, share`): such a bundle is `map_bundle/3`; one without stays `map_bundle/2`; the reader takes both. `cartolex.build.bundle.project_bundle` builds a project's. A bundle is built from the matrix's non-zero entries and its vocabulary totals by chunks of columns, with the numbers the dense computation gave |

**Colours.** One hue per top-level node: `s<k>` takes palette entry `k`, other
top-level ids the next free entries in tree order (the two-level rule for
curated trees). Each node below takes a shade of its top node's hue by its place
among that node's descendants in tree order (depth 2: the concept shades). The
same ids give the same colours on every rebuild; a keyword takes its node's.

## On disk

New at every depth: `themes.group/themes_draft.json`;
`themes.apply/themes_tree.json`, `themes_applied.json`, `theme_keywords.csv`,
`theme_people.parquet`, `theme_organisations.parquet`;
`map.layout/themes_applied.json`; `map.trajectories/trajectory_themes.parquet`;
`levels` in `positions.json`. Written only at depth 2: `subfields_draft.json`,
`curated.json`, `subfields.json`, `subfield_weights.csv`, `lexicon_weights.csv`,
and the two-level weights of the trajectory windows and the positions. The
stage versions of `themes.group` and `themes.apply` rise to 2, those of
`map.layout`, `map.trajectories` and `overlays.position` to 3, so earlier results
need an update. A default project follows the depth rule: the S demo world is
one level of 15 themes.

## Depth 2 stays identical

The engine functions the workspace run calls give the same outputs; the
project runners add the files of any depth beside the two-level ones, which
today's code still computes from today's inputs. What the drift baseline
compares (the clustering, the proto-subfields, the draft, the applied
document, the person and lexicon weights, the trajectories, the projection, the
bundle) is therefore unchanged below the clustering threshold, and the parts
rewritten without a dense matrix (the lexicon weights, the bundle) are proved
bit-identical to the dense computation by tests and by the full check,
directly and through a project. On the demo, at depth 2, the tables of any depth
equal the two-level outputs exactly, for the proposal and for a curated tree
with keywords on themes and attributions (`tests/test_build_themes.py`).

## Clustering at any size

Scipy's Ward stores every pairwise distance twice (the condensed matrix and its
working copy): about 8·n² bytes, 80 GB at 10⁵ keywords
(`cartolex.atlas.clustering`).

- **Exact Ward** up to `EXACT_WARD_LIMIT` keywords, the largest size whose
  measured peak stays under about 2 GB. The demo worlds and the reference stay
  on this path, unchanged; so does every project with the default vocabulary cap
  (10 000 keywords).
- **Above it: micro-clusters, then Ward.** Mini-batch k-means (fixed seed,
  k-means++ start drawn from at most 3·m points, no reassignment) makes
  `m = min(n, EXACT_WARD_LIMIT)` micro-clusters: as many as the weighted Ward
  can hold in the memory the exact path is allowed. Ward then merges their
  centroids weighted by their sizes: Ward's merge cost is
  `w_a·w_b/(w_a + w_b)·|c_a − c_b|²`, so a plain Ward on the centroids would be
  wrong. `weighted_ward_linkage` runs the nearest-neighbour chain on the
  heights `sqrt(2Δ)` with the Lance–Williams update with sizes, in scipy's
  order and arithmetic: with unit sizes it gives scipy's linkage matrix exactly,
  and with integer sizes the tree of Ward on the points repeated (both tested).
  Each keyword takes its micro-cluster's group; when a level asks for more groups
  than half the micro-clusters, the k-means partition is the answer.
- **The coarser levels** use the same function (`group_subfields`): exact below
  the threshold, which a level rarely passes.

Measures (`python tools/theme_clustering_study.py threshold|agreement|scale`,
through the memory-capped runner; synthetic keywords are unit vectors in 20
dimensions around `n/20` topics of unequal sizes, cut at `n/20` groups):

**Exact Ward: the threshold.** The peak is the whole process's (a fresh one per
measure); 15 000 keywords is the largest size under about 2 GB, so
`EXACT_WARD_LIMIT = 15 000`.

| keywords | groups | seconds | peak |
| --- | --- | --- | --- |
| 5 000 | 250 | 0.7 | 368 MB |
| 10 000 | 500 | 3.3 | 941 MB |
| 15 000 | 750 | 8.6 | 1 896 MB |
| 20 000 | 1 000 | 16.7 | 3 232 MB |

**The engine's path by size** (exact up to the threshold, two stages above):

| keywords | groups | path | seconds | peak |
| --- | --- | --- | --- | --- |
| 10³ | 50 | exact | 0.04 | 184 MB |
| 10⁴ | 500 | exact | 3.3 | 941 MB |
| 10⁵ | 5 000 | two stages, 15 000 micro-clusters | 155 | 1 544 MB |
| 10⁵ | 5 000 | the same with a random start | 70 | 1 528 MB |

At 10⁵ exact Ward would need about 80 GB. `tests/test_scale_themes.py` (marked
`heavy`, run once by the full check) clusters 10⁵ keywords into 5 000 groups
under a cap of 3 000 MB.

**Agreement with exact Ward**, where both run. Ward is sensitive to small moves
of the points, so the change is read against Ward's own: on the same keywords
moved by 0.1 %, exact Ward moves the share of keywords given as « Ward's own ».
On synthetic keywords both cuts are also compared with the topics the keywords
were drawn around (adjusted Rand index). Above the threshold (the engine's
rule, 15 000 micro-clusters):

| keywords | ARI with exact | keywords whose group changes | Ward's own | ARI with the topics: exact · two stages | seconds: exact · two stages | peak: exact · two stages |
| --- | --- | --- | --- | --- | --- | --- |
| 20 000 | 0.70 | 14.8 % | 13.5 % | 0.269 · 0.273 | 18 · 49 | 3.2 · 1.5 GB |
| 30 000 | 0.76 | 19.6 % | 12.3 % | 0.481 · 0.482 | 46 · 73 | 7.0 · 1.5 GB |

Below the threshold, forcing the two stages with fewer micro-clusters:

| keywords (groups) | micro-clusters | ARI with exact | changed | Ward's own |
| --- | --- | --- | --- | --- |
| synthetic 1 000 (50) | 500 · 200 · 100 | 0.995 · 0.989 · 0.824 | 0.3 · 0.8 · 10.7 % | 0.1 % |
| synthetic 10 000 (500) | 5 000 · 2 000 · 1 000 | 0.613 · 0.590 · 0.579 | 19.6 · 23.0 · 25.4 % | 17.1 % |
| demo S, 491 (25) | 245 · 98 · 49 | 1.000 · 0.945 · 0.857 | 0.0 · 4.9 · 12.6 % | 0.0 % |
| demo L, 3 072 (154) | 1 537 · 614 · 307 | 0.864 · 0.731 · 0.663 | 11.0 · 21.6 · 28.1 % | 3.8 % |

In short: where it runs, the two-stage cut moves about as many keywords
between groups as exact Ward moves itself when the keyword vectors shift by
0.1 %, finds the planted topics as well as exact Ward does, and holds its memory
at 1.5 GB. The k-means++ start agrees with exact Ward better than a random start
in every row (for example 0.70 against 0.66 at 20 000 keywords) at about twice
its time; the engine keeps it.

## Open choices

- The per-person weights are evidence-based (usage). The two-level
  `subfield_weights.csv` (similarity to the theme's seed keywords in the SVD
  space) stays at depth 2 only; no reading of any depth keeps it.
- The projected people's level weights use the vector that places them (their
  top keywords, TF-IDF with the length bonus), not a TF vector.
- An organisation is the engine's group (`unit`), not an `org_id`.
- `CohortInput` still makes its matrix dense when built (piece S-B's), so a
  bundle read back into the merge is dense there until that changes.
