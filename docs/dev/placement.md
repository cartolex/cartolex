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

Placement is exact across chunkings (maximum difference 0) and takes
milliseconds. It keeps neighbourhoods as well as UMAP or better, but it puts
keywords in visibly different places: among the people who use them, where
UMAP's transform spreads them further. Too large a *k* for the number of
people pulls points towards the centre.

Still to measure: size L (350 people), trajectories, and the effect on the
atlas figures.
