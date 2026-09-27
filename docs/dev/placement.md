# Placing points on a finished map

A map is fitted once, on the mapped people. Everything else is *placed* on it:
the keywords, projected people, and each person's texts of one period
(trajectories). Today placement uses UMAP's `transform`, which needs the fitted
UMAP model (re-fitted on load, not exactly the same on every machine) and runs
a stochastic optimisation per call.

`cartolex.atlas.placement.place` is the alternative under study: a vector is
placed at the weighted mean of the positions of its *k* nearest mapped people,
by cosine distance in the SVD space, with UMAP's own neighbourhood weights
(the nearest weighs 1, the others `exp(-(d - d₁)/σ)`, `σ` set so the weights
sum to `log₂ k`). It needs no fitted layout model, has no randomness, runs in
fixed-order arithmetic, and works in chunks, so it gives the same positions on
any machine and scales to millions of points.

**Status: under study.** Nothing in the build uses it yet; switching would move
keywords, projected people and trajectories on the map, which needs the
owner's agreement and ledger entries.

## The study

`python tools/placement_study.py --size S --seed 0 --k 8` runs the engine with
the numeric reference's settings and compares both methods. Distances are in
units of the map's scale (root mean square distance of the people from their
centre); *neighbours* is the share of a point's 10 nearest people on the map
that are also among its 10 nearest in the SVD space (higher is better).

Demo world S, seed 0 (38 people, 470 keywords):

| comparison | k | median shift | p90 | neighbours, placement | neighbours, UMAP |
| --- | ---: | ---: | ---: | ---: | ---: |
| people left out one at a time, vs their fitted position | 5 | 0.30 | 0.48 | 0.66 | 0.64 (fitted) |
| people left out one at a time, vs their fitted position | 8 | 0.30 | 0.49 | 0.67 | 0.64 (fitted) |
| people left out one at a time, vs their fitted position | 15 | 0.36 | 0.68 | 0.61 | 0.64 (fitted) |
| keywords, vs UMAP `transform` | 8 | 0.87 | 1.27 | 0.62 | 0.57 |
| projected people (4), vs UMAP `transform` | 8 | 0.60 | 0.90 | 0.55 | 0.55 |

Demo world L, seed 0 (343 people, 3,018 keywords):

| comparison | k | median shift | p90 | neighbours, placement | neighbours, UMAP |
| --- | ---: | ---: | ---: | ---: | ---: |
| people left out one at a time, vs their fitted position | 8 | 0.027 | 0.053 | 0.80 | 0.81 (fitted) |
| people left out one at a time, vs their fitted position | 15 | 0.029 | 0.071 | 0.79 | 0.81 (fitted) |
| keywords, vs UMAP `transform` | 8 | 0.063 | 0.112 | 0.76 | 0.77 |
| keywords, vs UMAP `transform` | 15 | 0.063 | 0.141 | 0.74 | 0.77 |
| projected people (35), vs UMAP `transform` | 8 | 0.074 | 0.140 | 0.65 | 0.67 |

## What the numbers say

- On a realistic map (L), placement lands within a few percent of the map's
  scale of UMAP's own positions (median 0.03 to 0.07) and keeps neighbourhoods
  almost as well (0.01 to 0.02 lower). On a very small map (S, 38 people) the
  two methods differ much more, and too large a *k* pulls points towards the
  centre; *k* = 8 is the better choice at both sizes.
- Placement is exact across chunkings (maximum difference 0), needs no fitted
  layout model, and takes milliseconds: 0.07 s for 3,018 keywords.
- A left-out person lands on average 0.03 map units from where the full fit put
  them, so placing a new person gives very nearly where fitting them would.

Still to measure: trajectories, and the atlas figures side by side. The switch
is proposed at gate G2: keywords, projected people and trajectories placed by
nearest neighbours (*k* = 8), UMAP's `transform` and the re-fit on load retired.
