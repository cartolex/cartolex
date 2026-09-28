# Layouts compared

A map is the people's positions in two dimensions, drawn from their vectors in
the space (`themes.space`); every other point — the keywords, the projected
people, each person's texts of one period — is then *placed* by its nearest
people ([placement](placement.md)). A map version names how the people's
positions are drawn (`decisions/maps.json`, `layout.method`):

| method | how the people are placed | cost grows with |
| --- | --- | --- |
| `umap` | UMAP of the people in the space (cosine), 25 neighbours, `min_dist` 0.3 | people × neighbours, plus a fixed compilation of the layout library in a new process |
| `tsne` | t-SNE of the people (openTSNE, FFT-accelerated gradients, exact neighbours below 1 000 people, an Annoy index above), perplexity 30, one thread | people × perplexity |
| `tree` | the applied theme tree: the top-level themes are discs of area proportional to their people, placed by a classical scaling of the cosine distances between their mean vectors and pushed apart; each theme's children likewise inside it; the people of a finest theme spread by its first two principal axes, leaning toward the sibling discs whose people they resemble | people × dimensions |

In every case the keywords are placed by the heaviest-group placement, and the
map is deterministic: the version's seed (UMAP, t-SNE) or no randomness at all
(the tree).

## What is measured

`python tools/layout_study.py PROJECT … --out FILE.jsonl` fits each method on
a built project, the fit in a fresh process (its time and peak memory are that
process's), and measures, on samples of at most 3 000 people and keywords:

| measure | what it says | better |
| --- | --- | --- |
| people | share of a person's 10 nearest people on the map that are also among their 10 nearest in the space | higher |
| keywords | the same for a placed keyword and its 10 nearest people | higher |
| theme links | for each pair of top-level themes, how many of the people's 10 nearest neighbours cross between them, in the space and on the map: the rank correlation of the two over the pairs (continuity between themes) | higher |
| borders | the people measure for the people who have a neighbour of another theme in the space | higher |
| stability | a second edition of the world (2 % of the people gone, 2 % changed, 2 % new) is fitted again with the same seed; for the people in both, the share of their 10 nearest people on the first map still among them on the second | higher |
| disparity | the Procrustes disparity of those people's positions between the two editions (0: the same map up to a rotation and a scale); the same for the keywords | lower |

## The measures

Demo worlds S and L (seed 0) and streamed worlds of 10³, 10⁴ and 10⁵ mapped
people (`cartolex.demo.scale`, seed 0), each built with the defaults (a space
of 20 dimensions); the version's seed is 3. Time and memory are the fit's, in
a fresh process (the keywords' placement included).

| world | people | method | people | keywords | theme links | borders | stability | disparity | keyword disparity | seconds | peak MB |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| S | 38 | umap | **0.637** | **0.639** | **0.56** | **0.637** | 0.653 | 0.743 | 0.732 | 17.0 | 470 |
| S | 38 | tsne | 0.574 | 0.610 | 0.45 | 0.574 | 0.694 | 0.379 | 0.447 | 7.3 | 384 |
| S | 38 | tree | 0.537 | 0.575 | 0.34 | 0.537 | **0.739** | **0.165** | **0.190** | 0.0 | 198 |
| L | 329 | umap | 0.794 | 0.795 | 0.61 | 0.765 | 0.761 | 0.578 | 0.603 | 16.1 | 488 |
| L | 329 | tsne | **0.837** | **0.831** | **0.67** | **0.793** | **0.782** | 0.582 | 0.575 | 7.2 | 400 |
| L | 329 | tree | 0.612 | 0.695 | 0.15 | 0.557 | 0.706 | **0.169** | **0.222** | 0.2 | 212 |
| 10³ | 935 | umap | 0.574 | 0.579 | **0.91** | 0.567 | 0.484 | 0.147 | 0.225 | 16.4 | 517 |
| 10³ | 935 | tsne | **0.649** | **0.640** | 0.90 | **0.614** | 0.543 | 0.143 | **0.217** | 8.9 | 433 |
| 10³ | 935 | tree | 0.383 | 0.469 | 0.33 | 0.311 | **0.625** | **0.113** | 0.230 | 0.6 | 238 |
| 10⁴ | 9 400 | umap | 0.362 | 0.274 | 0.96 | 0.392 | 0.398 | 0.053 | **0.088** | 38.0 | 653 |
| 10⁴ | 9 400 | tsne | **0.552** | **0.361** | **0.98** | **0.576** | **0.537** | 0.117 | 0.158 | 56.2 | 531 |
| 10⁴ | 9 400 | tree | 0.140 | 0.160 | 0.05 | 0.111 | 0.450 | **0.051** | 0.106 | 1.6 | 531 |

The 10⁵ world was not compared: the owner kept the study to the demo worlds
and one world of 10⁴ people. At 10⁵ the costs are those of the build
([sizes](../sizes.md)).

## What the numbers say

- **Neighbourhoods.** t-SNE keeps a person's neighbours best from the L world
  up, and the gap grows with the size: 0.837 against UMAP's 0.794 on L, 0.649
  against 0.574 at 10³, 0.552 against 0.362 at 10⁴; the same for the placed
  keywords and for the people on the borders between themes. On the S world
  (38 people) UMAP is better.
- **Continuity between themes.** From 10³ people both keep the links between
  themes (rank correlations 0.90 to 0.98); t-SNE is at least as good.
- **Stability.** Between two editions, t-SNE keeps more of each person's
  neighbours (0.537 against 0.398 at 10⁴), but UMAP keeps the global
  arrangement closer (Procrustes disparity 0.053 against 0.117 at 10⁴): a new
  edition's t-SNE map may move whole regions more.
- **Cost.** Both take under a minute at 10⁴ (UMAP 38 s, t-SNE 56 s, one
  thread) and under 1 GB; UMAP's time includes about 15 s of compilation in a
  fresh process.
- **The tree layout** is the fastest (under 2 s at 10⁴) and the most stable
  globally, but it keeps few neighbourhoods (0.14 at 10⁴) and few links
  between themes: its discs separate what the space keeps close. It stays
  available as a method; it is no default.

## The default

A project's first map version takes its method from the mapped people it has
when the map is first drawn (`cartolex.build.engine.default_layout_method`):
**UMAP below 1 000 mapped people, t-SNE from 1 000** when the optional openTSNE
package is installed (UMAP otherwise). Both reference worlds (38 and 343
people) keep UMAP, so their maps are unchanged. A pinned version keeps its
method: a project's map changes method only when someone pins another
version.

**openTSNE** (BSD-3-Clause) is an optional dependency, the `tsne` extra: it
has wheels for Python 3.10 to 3.14 on Linux x86-64, macOS (universal) and
Windows x86-64, but none for Linux on ARM, where it builds from source. It
needs only numpy, scipy and scikit-learn, which cartolex already requires.
