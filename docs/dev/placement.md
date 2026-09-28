# Placing points on a finished map

A map is fitted once, on the mapped people. Everything else is *placed* on it:
the keywords, the projected people, and each person's texts of one period (the
trajectories). The people's own positions come from the fit and never move.

**Decision (gate G2): points are placed by their nearest people.** Until then
cartolex placed them with UMAP's own `transform`, which needs the fitted UMAP
model (re-fitted when read, not exactly the same on every machine) and runs a
stochastic optimisation per call. That method, and the stored layout model with
it, are retired: the map is the people's and the keywords' positions in the
stored embeddings, and `cartolex.atlas.placement.place` places everything else.

## The method

A vector is placed from its *k* = 8 nearest mapped people, by cosine distance
in the SVD space:

1. **weights.** The neighbours weigh as in UMAP's fuzzy neighbourhood: the
   nearest weighs 1, the others `exp(-(d - d₁)/σ)`, `σ` set so the weights sum
   to `log₂ k`;
2. **the heaviest group.** Two neighbours are *linked* when their positions on
   the map are within the link radius of each other, a quarter of the map's
   radius (the root mean square distance of the people from their centre). The
   linked neighbours form groups (the connected parts of the links), and the
   point goes to the weighted mean of the group with the largest total weight;
   on a tie, the group of the nearest neighbour.

A point whose neighbours sit together lands at their weighted mean. A point
whose neighbours are split between two distant places lands in the place that
weighs most, never in the empty space between them: a plain weighted mean of
all eight would strand it there.

No fitted model is needed and nothing is random. The arithmetic runs in a fixed
order (`np.einsum` without optimisation; neighbours ranked by distance, then by
index), so the positions are the same on any machine and whatever the chunk
size; the work goes in chunks, so memory stays proportional to the chunk times
the number of people. Placing the 3,072 keywords of the large demo world takes
0.09 s.

| where | what is placed |
| --- | --- |
| `map.layout` | the keywords (except in the joint layout, which fits people and keywords together) |
| `map.trajectories` | each person's time bins and time windows (all windows in one call) |
| `overlays.position` | the projected people, from their texts |

## The study

`python tools/placement_study.py --size L --seed 0` runs the engine with the
numeric reference's settings up to the layout, then places the same points
three ways: UMAP's `transform` (the study fits a UMAP of its own on the people,
with the layout's settings, and checks it gives the engine's map), the plain
weighted mean of the eight neighbours (a switch of the study only), and the
heaviest group. *Neighbours* is the share of a placed point's 10 nearest people
on the map that are also among its 10 nearest in the SVD space (higher is
better); *stranded* the share of points farther than the link radius from every
one of their eight nearest people on the map; the shift is the median distance
from UMAP's position (for people left out one at a time, from their fitted
position), in map radii. For UMAP, the people's row shows their fitted
positions.

Demo world L, seed 0 (343 people, 3,072 keywords):

| points | method | neighbours | stranded | median shift |
| --- | --- | ---: | ---: | ---: |
| people, left out one at a time | UMAP (fitted) | 0.830 | 0.0 % | |
| | average | 0.819 | 1.2 % | 0.027 |
| | heaviest group | 0.836 | 0.0 % | 0.028 |
| keywords (3,072) | UMAP | 0.794 | 0.7 % | |
| | average | 0.784 | 5.0 % | 0.067 |
| | heaviest group | **0.824** | **0.0 %** | 0.067 |
| projected people (35) | UMAP | 0.631 | 2.9 % | |
| | average | 0.591 | 11.4 % | 0.078 |
| | heaviest group | **0.697** | **0.0 %** | 0.079 |

Demo world S, seed 0 (38 people, 490 keywords; on a map this small most people
are more than a quarter of the map's radius from each other, so *stranded* says
little):

| points | method | neighbours | stranded | median shift |
| --- | --- | ---: | ---: | ---: |
| keywords | UMAP | 0.521 | 100 % | |
| | average | 0.622 | 7.3 % | 0.795 |
| | heaviest group | 0.597 | 0.0 % | 0.738 |
| projected people (4) | UMAP | 0.475 | 100 % | |
| | average | 0.550 | 0.0 % | 0.576 |
| | heaviest group | 0.525 | 0.0 % | 0.479 |

## What the numbers say

- On a realistic map (L), the heaviest group keeps neighbourhoods better than
  UMAP's `transform` for keywords (0.824 against 0.794) and projected people
  (0.697 against 0.631), and strands nothing. The plain average strands 5 % of
  keywords and 11 % of projected people between two places.
- UMAP's keywords on the small map sit away from all their neighbours: the
  halos around groups of people that the map used to show were largely an
  artifact of the transform.
- A person left out and placed lands on average 0.03 map radii from where the
  fit put them: placing a new person gives very nearly where fitting them would.
- Placement is exact across chunkings, needs no layout model, and makes the
  trajectories stage 3.3 times faster on L (18.2 s to 5.5 s).
