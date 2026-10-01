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
4. **Keywords.** With the stage's `comb` (the default), each keyword sits on
   the level its texts support, and the keywords too broad for any theme are
   set aside (« too broad for any theme »): see *Combing* below. Without it,
   every keyword sits on the finest level (at depth 1 on the only level), with
   no attribution, and nothing is set aside.
5. **Names**, in each display language, from the forms a keyword has there
   (its own language, or a form the consolidation pairs attest), else its
   reference-language form, else the keyword
   (`cartolex.lexicon.labels.node_names`):
   - combed: from the top down, a node takes the most used keyword (highest
     summed score) of its own, among those of which at least half the use
     falls in the node rather than in its siblings; a node without one takes
     the keyword of its subtree most distinctive of it (its score times that
     share). A node never takes a keyword an ancestor is named after, nor a
     name an ancestor has in that language: no « X › X »
     (`cartolex.lexicon.labels.tree_names`);
   - not combed: the most used keyword the node holds, on it or under it.

   Siblings' names are kept distinct in each language.

The stage writes `themes_draft.json` at every depth, `based_on` its run and the
vocabulary, and, at depth 2, the two-level `subfields_draft.json` too,
unchanged. At depth 2 and without the comb, the proposal is the two-level draft
read as a tree (`from_curated`), names included (a test checks this); the
reference run turns the comb off, as the workspace run it mirrors has none.
The level sizes come from `theme_levels(...)`.

## Combing: each keyword on its level

The grouping puts every keyword on a topic. A keyword used across themes (a
method, a driver of change, a word of the whole field) then counts toward
one of them. The comb (`cartolex.lexicon.theme_comb`) reads the texts
instead. The unit is the text, not the person: a person working on two themes
does not make their keywords look broad.

1. **The texts.** Each text of the corpus indexes, once (a text signed by
   several people is read once), is read with the vocabulary's vectorizer and
   its forms folded onto the keywords, as the trajectories read them:
   texts × keywords, 1 where the text uses the keyword.
2. **Where a text sits, without the keyword.** For a keyword, each text using
   it is placed by its *other* keywords: their shares on the finest nodes
   (each text one unit; a text with no other placed keyword gives nothing).
   The keyword's **spread** is the sum over its texts.
3. **Its place.** From the finest level up, on each level the node with the
   most of the spread; the keyword goes to the first such node whose share
   reaches θ. The share is read *relative* to what any keyword gives the
   node: `(s − b) / (1 − b)`, `b` being the node's share of every keyword's
   spread together, so that one θ means the same on a level of 3 nodes and
   on one of 150 (a keyword spread like the texts scores 0, one wholly on the
   node 1). No top-level node reaching θ: **too broad for any theme**, set
   aside. A keyword with fewer than 5 texts of evidence keeps its topic.
4. **θ, calibrated.** The owner's target: about as many keywords per node on
   every level, so that the few top-level nodes hold few keywords. The θ kept
   is the one of 0.100, 0.125, …, 0.250 whose levels come closest (the sum,
   over the levels above the finest, of the absolute log ratio of their
   keywords per node to the finest level's); at depth 1, where nothing can
   be balanced, 0.2. The band is what the measures below support: above
   0.25 the small world loses specific keywords, below 0.1 it keeps the
   broad ones. These are the people's space's values; a space of texts has
   its own (*The comb on a space of texts*, below): the grid, θ at depth 1
   and where a keyword may move (`comb_sideways`) follow
   `themes.space.space_unit` by rules (`comb_grid_by_space`,
   `comb_theta_one_level_by_space`, `comb_sideways_by_space`;
   `cartolex.lexicon.theme_comb.CALIBRATION`).

**On a curated tree: suggestions only.** The grouping keeps the texts it read
(`themes.group/text_keywords.npz`). The editor's « Borderline » tab can show,
instead of the keywords nearest a border, the comb read on the tree being
edited (`POST /api/themes/levels`, `cartolex.lexicon.theme_comb.tree_levels`):
every node is a cell, each keyword's texts are placed by their other
keywords' nodes, θ is calibrated on the tree the same way, and a keyword the
comb takes to a strict ancestor of its node is suggested « move up to
<node> », one no top-level node holds « too broad for any theme », each with
its share (U applies it, A keeps it here). A move to a node elsewhere is the
borderline list's business. The copilot's themes bundle carries the same
reading of the tree it holds (`baseline/levels.json`, `session.levels()`);
the texts stay home. Nothing changes a curated tree by itself.

The comb holds the keywords × topics spread in memory; above 5·10⁷ cells
(10 000 keywords on 5 000 topics) the proposal is not combed and says so.

**Measures** (`python tools/theme_comb_study.py comb FOLDER --sizes …` on the
demo worlds built to `themes.group`; trees cut at the given sizes; the texts'
themes are the demo's truth). A keyword is *broad* when its texts (5 or more)
have no main theme holding half of them, *specific* when one holds 80 %.
Broad P / R: the precision and recall of placing broad keywords above the
level of the themes (or aside); specific kept: the share of specific
keywords left at or below the themes, in their theme (the node's theme being
that of most of its specific keywords). Fixed θ is the absolute share, the
same on every level; calibrated is the engine's rule.

| world, sizes | θ | per level (top first) · too broad | keywords per node | broad P · R | specific in their theme (before) |
| --- | --- | --- | --- | --- | --- |
| S (226 texts), 12 › 25 | fixed 0.3 | 25 · 498 · 18 | 2.1 · 19.9 | 0.94 · 0.39 | 0.93 (0.89) |
| | fixed 0.5 | 38 · 451 · 52 | 3.2 · 18.0 | 0.77 · 0.89 | 0.79 (0.89) |
| | calibrated 0.25 | 19 · 500 · 22 | 1.6 · 20.0 | 0.95 · 0.50 | 0.93 (0.89) |
| L (2 137 texts), 12 | fixed 0.3 | 3 118 · 119 | 260 | 0.82 · 0.79 | 0.97 (0.67) |
| | calibrated 0.2 (depth 1) | 3 139 · 98 | 262 | 0.88 · 0.76 | 0.97 (0.67) |
| L, 12 › 150 | fixed 0.2 | 571 · 2 635 · 31 | 47.6 · 17.6 | 1.00 · 0.31 | 0.96 (0.82) |
| | fixed 0.3 | 1 497 · 1 623 · 117 | 124.8 · 10.8 | 0.91 · 0.79 | 0.90 (0.82) |
| | fixed 0.5 | 1 155 · 1 404 · 678 | 96.2 · 9.4 | 0.19 · 0.97 | 0.59 (0.82) |
| | calibrated 0.15 | 306 · 2 868 · 63 | 25.5 · 19.1 | 1.00 · 0.60 | 0.97 (0.82) |
| L, 3 › 12 › 150 | fixed 0.3 | 117 · 1 497 · 1 623 · 0 | 39.0 · 124.8 · 10.8 | 0.91 · 0.79 | 0.91 (0.82) |
| | calibrated 0.15 | 45 · 306 · 2 868 · 18 | 15.0 · 25.5 · 19.1 | 1.00 · 0.60 | 0.97 (0.82) |
| L, 3 › 12 › 40 › 150 | fixed 0.3 | 104 · 417 · 1 093 · 1 623 · 0 | 34.7 · 34.8 · 27.3 · 10.8 | 0.86 · 0.70 | 0.94 (0.82) |
| | calibrated 0.225 | 30 · 110 · 880 · 2 149 · 68 | 10.0 · 9.2 · 22.0 · 14.3 | 0.91 · 0.74 | 0.97 (0.82) |

By kind of term of the demo truth (L, 12 › 150, calibrated): of 24 methods,
22 leave the topics (18 set aside; the 2 of the social sciences, used by
one group of themes only, stay); both drivers go up to a theme; of 26 terms of
several themes, 14 leave the topics; of 2 283 terms of one theme, 138 go up to
their theme's node and none is set aside.

In short: an absolute θ cannot serve every level (on 150 topics a text's
other keywords spread over the theme's many topics, so 0.3 sends 84 % of the
specific keywords up; on 12 themes 0.5 sets aside hundreds of specific ones),
while the relative share at the calibrated θ keeps the specific keywords
(none set aside, 97 % in their theme where the grouping had 82 %: the comb
also moves keywords sideways to the node their texts are in), sets aside or
lifts most broad ones with a precision of 0.9 to 1, and gives the levels
about as many keywords per node. Its recall of broad keywords (0.5 to 0.75)
is the price of that balance; a fixed relative 0.2 catches more (0.66 to 0.78)
at a slightly lower precision.

## The space: the texts by default, the people as a choice

`themes.space` can fit the keyword space on the people × keywords matrix
(`space_unit: person`): two keywords are near when the same people use them.
A person working on two unrelated subjects then brings their keywords
together. With `space_unit: text`, the default (the rule `space_unit_texts`,
recorded as such in `run.json`), the space is fitted on the texts instead
(`cartolex.atlas.reducers.compute_text_svd_embeddings`): the fitted slots'
texts, each read once and folded onto the keywords as the comb reads them,
their TF-IDF over the texts (presence × smoothed IDF, rows L2-normalised),
then the same truncated SVD; the keywords' vectors are its components
scaled by the singular values. The people are placed as a projected person
is, by their row of the lexical matrix through that SVD, so the mapped and
the projected people are placed alike (placing each person at the mean of
their texts' vectors measured the same). The texts became the default on
the measures below and on a real project (`tools/space_unit_project.py`, an
English corpus of 21 500 texts): of each keyword's nearest, the links made
only by people fall from 30 % to 1.5 %, and a blind judge rated 76 % of the
text space's themes and topics « one subject » against 66 %; the grouping is
less stable there (topics' ARI without a tenth of the people 0.54 → 0.37).

**Measures** (`python tools/space_unit_study.py FOLDER --size S|L
[--two-subjects 0.3] [--sizes 12,150] [--map --draft] [--merged]`, on the
demo worlds and on the same worlds where 30 % of the cohort works on two
unrelated subjects, `S2` and `L2`, {doc}`../demo`). Specific keywords: terms
of one theme in the demo's truth. Themes: Ward's cut at 12 (the truth's
number); topics: at 25 (S) or 150 (L). *Mixed*: a group whose specific
keywords hold two unrelated themes (neither the other's neighbour) at 20 %
each. *Neighbours*: of each specific keyword's 10 nearest, the share of its
theme. *Stability*: the ARI of the theme cut with the space refitted without
a tenth of the units (people or texts; two draws). *One language*: the share
of each theme's keywords in its main language (the whole vocabulary's
share in its main language in brackets). People: of each person's 5
nearest, the share with the same main theme, and the Spearman ρ of the
people's closeness with that of their true theme mixes, in the space and on
the drawn map. Proposal: the ARI of the combed proposal's top level.

| world | space | themes ARI · purity | topics purity · mixed | neighbours | stability | one language | people: space · map (ρ) | proposal ARI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S | person | 0.33 · 0.59 | 0.74 · 3 | 0.64 | 0.61 | 0.87 (0.82) | 0.46 · 0.43 (0.30) | 0.37 |
| | text | 0.62 · 0.81 | 0.87 · 2 | 0.83 | 0.66 | 1.00 | 0.51 · 0.47 (0.46) | 0.58 |
| S2 | person | 0.31 · 0.61 | 0.70 · 9 | 0.60 | 0.50 | 0.90 (0.90) | 0.34 · 0.34 (0.30) | 0.32 |
| | text | 0.68 · 0.84 | 0.90 · 2 | 0.87 | 0.72 | 1.00 | 0.44 · 0.39 (0.39) | 0.71 |
| L | person | 0.32 · 0.59 | 0.72 · 22 | 0.63 | 0.47 | 0.86 (0.86) | 0.58 · 0.56 (0.38) | 0.54 |
| | text | 0.74 · 0.84 | 0.99 · 0 | 0.97 | 0.87 | 1.00 | 0.66 · 0.61 (0.38) | 0.78 |
| L2 | person | 0.21 · 0.49 | 0.65 · 29 | 0.56 | 0.32 | 0.85 (0.85) | 0.51 · 0.48 (0.29) | 0.47 |
| | text | 0.73 · 0.88 | 0.98 · 4 | 0.96 | 0.90 | 1.00 | 0.60 · 0.54 (0.33) | 0.78 |

The collapse the text space was made for: on L2, of each specific keyword's
nearest, 19.6 % are of an unrelated theme in the people's space, 1.3 % in
the texts'. The stage takes 2.5 s instead of 1.5 s on L (it reads the texts
once more), with the same peak.

**What it costs.**

- **Languages apart.** A keyword is near only the keywords its texts use,
  and a text is in one language: every theme of the text space is in one
  language (1.00 in the table). On L the proposal has 4 themes of 15 in
  French only, each mixing several subjects (the French texts are fewer),
  where the people's space puts the French keywords with the English ones of
  their subject. Folding every translation the vocabulary holds onto its
  English keyword first (the truth's pairs, a perfect merge: 174 keywords on
  L) raises the theme ARI to 0.87 but leaves the themes in one language
  (0.996, for 0.93 of the vocabulary in English): the keywords without a
  kept translation still gather by language.
- **The comb, tuned on the people's space.** With the people's
  calibration, a text-space tree has fewer broad keywords caught (recall
  0.60 → 0.39 on L, 0.50 → 0.26 on S, precision 1.00 both) and specific
  keywords moved out of their theme (in their theme: 1.00 before the comb,
  0.89 after, where the people's space goes from 0.82 to 0.97). The comb now
  has a calibration of its own there (below).
- **The demo favours it.** Each demo text is written from one theme, or two
  neighbours: the texts carry the truth more directly than real abstracts
  may. The gain on a real corpus is not measured here.

A space fitted on the texts and the people together (their rows stacked)
keeps most of the gain and all of the language split when a person weighs
one text (L: themes ARI 0.73, one language 0.996); weighing the people as
much as all the texts halves the split and loses most of the gain (0.56,
0.89 for 0.86).

**Languages: the diagnostic.** A space of texts whose vocabulary has 10 % or
more of its keywords outside the reference language (a keyword's language: the
one the vocabulary gives its form, `keywords_global_refined.csv`) warns in its
`run.json` and in the overview and the space step (`health_space_languages`:
« themes may split by language; the people's space may suit this corpus
better »; `cartolex.build.engine.SPACE_LANGUAGE_SHARE`). The threshold is a
proposal: on the L demo world 21 % of the keywords are French and one theme of
French keywords only forms; on the real corpus above, 0.1 %. It is untested on
a real bilingual corpus.

### The comb on a space of texts

Why the people's calibration fails there (L world, 12 › 150): in a space of
texts the keywords many texts use gather in groups of their own, since they
co-occur with everything. One topic of 37 frequent words, of no theme
(its keywords' texts split between two neighbouring themes), held a larger
share of the use of 133 specific keywords than their own topic did (median
0.37 against 0.23, read relative to the 5.5 % every keyword gives it): moved
to it, they left their theme. And the French keywords make a node of their
own, which holds their broad keywords' texts: those are never broad there
(the language split, not a calibration matter; the measures below judge the
keywords of the reference language for that reason, and give the whole
vocabulary's beside them).

Three remedies were measured (`tools/theme_comb_study.py comb|relative
--sideways … --grid … --one-level … --language en`):

- **a higher θ alone** cannot restore the recall at depth 1 on L (it stops at
  0.54 while the precision falls to 0.28 at θ 0.6);
- **only up** (`up_only`: each keyword read on its own node's ancestors) keeps
  the demo's specific keywords in their theme (0.99) but sets aside a keyword
  the grouping put in the wrong theme: on the real project, 870 keywords set
  aside where the people's space sets aside 545, and on the people's space
  itself 508 of the L world's (precision 0.14);
- **within the parent** (`within_parent`, kept): below the top level a keyword
  may move only to a node under its own node's parent, at the top level to any
  node; with the grid one step higher (0.125 to 0.275) and θ 0.3 at depth 1.

The texts' calibration against the people's (keywords of the reference
language; the whole vocabulary in brackets; calibrated θ):

| world, sizes | space (calibration) | θ | broad P · R | specific in their theme (before) |
| --- | --- | --- | --- | --- |
| S, 12 | people | 0.2 | 0.90 · 0.70 (0.93 · 0.47) | 0.92 (0.88) |
|  | texts (people's calibration) | 0.2 | 1.00 · 0.52 (1.00 · 0.25) | 0.95 (0.95) |
|  | texts (own) | 0.3 | 0.85 · 0.85 (0.86 · 0.44) | 0.94 (0.95) |
| S, 12 › 25 | people | 0.25 | 0.88 · 0.78 (0.80 · 0.60) | 0.96 (0.92) |
|  | texts (people's calibration) | 0.25 | 1.00 · 0.56 (1.00 · 0.29) | 0.95 (1.00) |
|  | texts (own) | 0.275 | 0.95 · 0.67 (0.95 · 0.35) | 0.95 (1.00) |
| S, 3 › 12 › 25 | people | 0.25 | 0.88 · 0.78 (0.80 · 0.60) | 0.96 (0.92) |
|  | texts (people's calibration) | 0.25 | 1.00 · 0.56 (1.00 · 0.29) | 0.95 (1.00) |
|  | texts (own) | 0.275 | 0.90 · 0.67 (0.90 · 0.35) | 0.94 (1.00) |
| S2, 12 | people | 0.2 | 1.00 · 0.79 (0.96 · 0.83) | 0.84 (0.73) |
|  | texts (people's calibration) | 0.2 | 1.00 · 0.55 (1.00 · 0.31) | 0.94 (0.95) |
|  | texts (own) | 0.3 | 1.00 · 0.76 (1.00 · 0.44) | 0.94 (0.95) |
| S2, 12 › 25 | people | 0.25 | 0.96 · 0.86 (0.93 · 0.83) | 0.92 (0.87) |
|  | texts (people's calibration) | 0.25 | 1.00 · 0.62 (1.00 · 0.35) | 1.00 (1.00) |
|  | texts (own) | 0.275 | 1.00 · 0.72 (1.00 · 0.40) | 1.00 (1.00) |
| S2, 3 › 12 › 25 | people | 0.25 | 0.96 · 0.86 (0.93 · 0.83) | 0.92 (0.87) |
|  | texts (people's calibration) | 0.25 | 1.00 · 0.62 (1.00 · 0.35) | 1.00 (1.00) |
|  | texts (own) | 0.275 | 1.00 · 0.76 (1.00 · 0.42) | 1.00 (1.00) |
| L, 12 | people | 0.2 | 0.96 · 0.69 (0.93 · 0.68) | 0.97 (0.70) |
|  | texts (people's calibration) | 0.2 | 1.00 · 0.63 (1.00 · 0.37) | 0.93 (0.99) |
|  | texts (own) | 0.3 | 0.96 · 0.81 (0.96 · 0.47) | 0.93 (0.99) |
| L, 12 › 150 | people | 0.125 | 1.00 · 0.53 (1.00 · 0.32) | 0.95 (0.84) |
|  | texts (people's calibration) | 0.125 | 1.00 · 0.46 (1.00 · 0.26) | 0.88 (1.00) |
|  | texts (own) | 0.125 | 1.00 · 0.46 (1.00 · 0.26) | 0.94 (1.00) |
| L, 3 › 12 › 150 | people | 0.125 | 1.00 · 0.53 (1.00 · 0.32) | 0.95 (0.84) |
|  | texts (people's calibration) | 0.15 | 1.00 · 0.57 (1.00 · 0.33) | 0.88 (1.00) |
|  | texts (own) | 0.125 | 1.00 · 0.46 (1.00 · 0.26) | 0.94 (1.00) |
| L2, 12 | people | 0.2 | 0.98 · 0.74 (0.80 · 0.84) | 0.99 (0.59) |
|  | texts (people's calibration) | 0.2 | 1.00 · 0.72 (1.00 · 0.31) | 0.92 (0.99) |
|  | texts (own) | 0.3 | 0.95 · 0.76 (0.95 · 0.32) | 0.92 (0.99) |
| L2, 12 › 150 | people | 0.125 | 1.00 · 0.59 (0.87 · 0.36) | 0.90 (0.77) |
|  | texts (people's calibration) | 0.175 | 1.00 · 0.63 (1.00 · 0.27) | 0.84 (1.00) |
|  | texts (own) | 0.175 | 1.00 · 0.63 (1.00 · 0.27) | 0.84 (1.00) |
| L2, 3 › 12 › 150 | people | 0.125 | 1.00 · 0.59 (0.87 · 0.36) | 0.90 (0.77) |
|  | texts (people's calibration) | 0.175 | 1.00 · 0.63 (1.00 · 0.27) | 0.84 (1.00) |
|  | texts (own) | 0.175 | 1.00 · 0.63 (1.00 · 0.27) | 0.84 (1.00) |

At depth 1 the texts' calibration restores the people's recall of broad
keywords at a similar precision (S 0.52 → 0.85, S2 0.55 → 0.76, L 0.63 →
0.81, L2 0.72 → 0.76; the people 0.70 to 0.79). At two and three levels it
recovers part of it (S 0.56 → 0.67 against 0.78, S2 0.62 → 0.72 against 0.86;
L unchanged at 0.46 against 0.53; L2 0.63 against 0.59), and keeps more
specific keywords in their theme on L (0.88 → 0.94, the people 0.95), not on
L2 (0.84, the people 0.90). On L the calibration sits at the floor of its
grid (0.125), the balance of the levels asking for a lower θ. Over the whole
vocabulary the recall stays lower (the broad French keywords, in a node of
their language).

On the real project (both spaces built with these calibrations, 15 › 161):
the text space sets aside 522 keywords as too broad (the people's 545, the
people's calibration on the text space 289), 317 of them also set aside by
the people's space (58 % of its, against 39 % before); 1 675 keywords go up to
a theme (the people's 1 293) and 1 017 stay on the topics (1 376).

## Structure suggestions: measured, not shipped

Two signals were tried for suggesting changes to a tree
(`tools/theme_comb_study.py structure`, `tools/theme_structure.py`),
calibrated by cutting the L world's keywords at too few groups (6, where
nodes hold two themes: splits to find) and too many (24, 36: merges to find),
and at the right number (12: nothing to find). Truth: a pair of siblings
should merge when most of the specific keywords of both are of one theme; a
node should split when its second theme holds 30 % of its specific keywords.

- **Merges.** Per pair of siblings: centroid closeness, the texts' overlap
  (the texts' shares on both nodes, the smaller summed, over the smaller
  node's), mixing (keywords nearer the other node) and size. The overlap is
  the only useful signal: average precision 0.57 at 24 groups, 0.56 at 36
  (base rate 0.06 and 0.08); flagging overlap ≥ 0.4 finds 51 to 59 % of the
  merges at a precision of 0.62 to 0.66, and flags 3 pairs on the right tree
  and 3 on the too-coarse one. On the S world (226 texts) the average
  precision falls to 0.41.
- **Splits.** Ward's cut of a node's keywords in two against the same cut on
  Gaussian keywords of the node's spread (the gain in standard deviations),
  the conductance of the best cut of the keywords' co-use graph, and the
  share of texts using only one half. None separates: at 6 groups the true
  splits and the others have the same gains (medians 5.5 and 5.6 standard
  deviations), and on the right tree 10 of 12 nodes pass a gain of 2.

The split signals are noise; the merge overlap is a fair ranking but a
suggestion list built on it would be wrong about one time in three and
already speaks on a right tree. Neither is in the editor nor in the copilot
bundle.

## Names: measured

(`tools/theme_comb_study.py names`; L world; names repeating an ancestor's
name in a language; the share of names whose keyword is broad by its texts;
the share of theme-level nodes named after a keyword of the node's theme.)

| sizes | rule | « X › X » | named after a broad keyword | theme-level names of the node's theme |
| --- | --- | --- | --- | --- |
| 12 › 150 | before (the subtree's most used) | 24 | 13 of 128 | 6 of 12 |
| | combed, own most used | 0 | 2 of 29 | 9 of 11 |
| | combed, own most used holding half its use (engine) | 0 | 0 of 26 | 10 of 10 |
| 3 › 12 › 150 | before | 30 | 13 of 131 | 6 of 12 |
| | combed, own most used | 0 | 4 of 32 | 9 of 11 |
| | combed, engine | 0 | 3 of 30 | 10 of 11 |

On the S world (12 › 25) the repeats go from 24 to 0 and the theme-level
names of their node's theme from 3 of 4 to 4 of 4. Without the half-use
floor, nodes of the S world took words of the whole field (« articles »,
« jeu de données »); the floor removes some of them, not all (« articles »
stays): setting such words aside is the AI clean-up's work. Another rule can come in at
`tree_names`: a tie-break by category would rank the candidates there.

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
