# Sizes and machines

How long a build takes and how much memory it needs depends on the project's
size: mostly the people mapped, their texts and the characters those texts
hold. This page says what each stage costs at each size, which machine a
project of that size needs, and what the build does when a stage will not fit.

The numbers below are measured on the streamed synthetic worlds of
`cartolex.demo.scale` (the demo world's groups, themes, languages and
co-authorship, grown to any number of people): each person leads a few works
in the years of their career, about 6 texts of 1 300 characters (title and
abstract) per mapped person, and a projected set a tenth as large. Each stage
ran in a fresh process on one machine (x86-64, 20 cores, the numeric libraries
free to use them), capped at 16 GB.

## The tiers

| machine | memory | projects it builds | a first build takes |
| --- | --- | --- | --- |
| a laptop | 16 GB | up to about 10⁴ people | 20 minutes at 10⁴ |
| a workstation | 64 GB | up to about 10⁵ people | 6 to 7 hours at 10⁵ |
| a server | 128 GB or more | 10⁶ people (prepared, not yet run; see below) | about two weeks today |

The memory each stage needs is well below these (at most 10 GB estimated at
10⁵ people, for the keyword extraction); the rest is for the system, the app
and headroom. The time is mostly the keyword extraction (it parses every text)
and, from 10⁵ people, the trajectories (every period of every person is placed
among all the people).

## What each stage costs

Measured (the stage's time, then its peak memory), and estimated from the
fitted cost models for a full world of 10⁵ people (6.1 × 10⁵ texts, 8.2 × 10⁸
characters):

| stage | 10³ | 10⁴ | 10⁵, one title each | 10⁵ (estimate) |
| --- | ---: | ---: | ---: | ---: |
| `corpus.assemble` | 1 s, 272 MB | 8 s, 431 MB | 11 s, 655 MB | 72 s, 3.0 GB |
| `keywords.extract` | 89 s, 848 MB | 15 min, 1.6 GB | 8 min, 900 MB | 2.3 h, 9.7 GB |
| `keywords.build` | 32 s, 319 MB | 2 min, 871 MB | 3 min, 865 MB | 35 min, 6.8 GB |
| `themes.space` | 2 s, 252 MB | 5 s, 330 MB | 34 s, 748 MB | 35 s, 734 MB |
| `themes.group` | 3 s, 454 MB | 6 s, 944 MB | 7 s, 1.1 GB | 7 s, 1.0 GB |
| `themes.apply` | 1 s, 343 MB | 5 s, 553 MB | 26 s, 773 MB | 27 s, 841 MB |
| `map.layout` | 21 s, 741 MB | 44 s, 958 MB | 4 min, 1.1 GB | 4 min, 1.3 GB |
| `map.trajectories` | 16 s, 768 MB | 3 min, 1.3 GB | 40 min, 1.0 GB | 3.4 h, 6.9 GB |
| `overlays.position` | 2 s, 244 MB | 23 s, 699 MB | 8 min, 1.1 GB | 8 min, 1.2 GB |

The worlds: 965, 9 698 and 97 052 people with texts, 6 205, 60 691 and 97 052
texts, 5 677, 9 703 and 10 000 kept keywords. The world of 10⁵ people has one
work per person, read by its title only (`corpus.assemble.parts = ["title"]`),
so that its extraction takes minutes: its people, keywords and map are those
of a full world, its texts a sixth. The measures of 10³ and 10⁴ people used a
space of 20 dimensions and a UMAP map, those of 10⁵ the rules of today (139
dimensions, a t-SNE map). No stage of 10⁵ people reached 1.2 GB:
`tests/test_scale_stages.py` (run by the full check) builds that world and
fails if a stage other than the themes' passes 3 GB, below the 7.6 GB of one
dense people × keywords matrix.

The cost models (`cartolex.build.STAGES`) are fitted on these builds and on
the demo worlds: every stage's estimate is within a factor of two of its
measure on each of them (`tests/test_build_costs.py`); the estimates beyond
10⁵ people are extrapolations.

**What grows with what.** The corpus and the keyword stages grow with the
characters of the texts (the extraction also with their number: parsing a
short title costs a few milliseconds whatever its length); the space, the
layout and the placed points with the people mapped; the grouping and the
application of the themes with the kept keywords, which stop growing at
10 000 (`keywords.build.max_keywords`).

**Disk.** A project keeps one file per text for the engine (a 4 KB block
each on most file systems: 2.7 GB for 10⁵ people), the parse cache (about
2 KB per text), and the results of each stage, plus, for a stage built again,
its previous results until the next build of that stage.

## The space's dimensions

The space (`themes.space`) keeps 20 dimensions up to 2 000 people, then
20 × √(people / 2 000), at most 200 (the rule `space_dimensions`; a value
set in `decisions/params.json` wins). A larger field holds more distinct
themes, and a space of 20 dimensions keeps less and less of it:

| people | keywords | dimensions | variance kept | people's neighbours kept | keywords' neighbours kept |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 329 (L) | 2 955 | 20 | 19 % | 0.61 | 0.48 |
| | | 50 | 37 % | 0.72 | 0.58 |
| 935 | 5 677 | 20 | 10 % | 0.39 | 0.39 |
| | | 50 | 19 % | 0.58 | 0.51 |
| | | 100 | 31 % | 0.68 | 0.58 |
| 9 400 | 9 703 | 20 | 6.0 % | 0.06 | 0.07 |
| | | 50 | 9.3 % | 0.13 | 0.15 |
| | | 100 | 13 % | 0.22 | 0.24 |
| | | 200 | 20 % | 0.36 | 0.35 |
| | | 400 | 30 % | 0.52 | 0.48 |

*Neighbours kept*: for 2 000 people (or keywords), the share of their 10
nearest by cosine in the full keyword space that are also their 10 nearest in
the space (`tools/dimension_study.py`). The 10⁴ world behaves like a real
corpus of 20 000 people, where 20 dimensions kept 6.5 % of the variance and
400 kept 36 %.

A few dimensions keep the broad themes and drop the fine distinctions (which
is why a small project keeps 20: its themes stay clean); a large field needs
more to keep people's neighbourhoods, but every dimension costs time in the
layout and in placing points. The rule doubles the dimensions for each
fourfold of people from 2 000: 45 at 10⁴ people, 141 at 10⁵, 200 (the cap) from
200 000. The grouping into themes reads at most the first 50 dimensions:
beyond 50, more dimensions change the map and the placed points, and hardly
the themes.

## When a stage will not fit

Before a build, `cartolex build PROJECT --dry-run` estimates every stage's time
and peak memory from the project's sizes (the people and texts are read from
the sources before anything is built; after a first build, from the measures
of the last run, scaled to the new sizes). The estimates are within a factor
of two of the measures on the worlds of 10³ to 10⁵ people. A stage whose
estimated peak exceeds the memory available now is marked « cannot run »,
with the numbers, and so is every stage after it:

```text
  run   keywords.extract   find keyword candidates: about 23 h, 90 GB (the default cost model); never built; CANNOT RUN: needs about 89.9 GB of memory; the budget is 58.2 GB
```

The build then runs the stages before it, stops there and says why. Free
memory (close other programs) or build on a larger machine; the Python API's
`build(allow_over_budget=True)` runs the stage anyway.

## A million people

The run of a million people is prepared, not run: `tools/million.py plan`
prints its sizes and each stage's estimate, and `tools/million.py run --out
DIR` writes the world (in parallel) and builds it stage by stage, recording
each stage's measures. The estimates, for 9.7 × 10⁵ people with texts, 6.1 × 10⁶
texts and 8.2 × 10⁹ characters:

| stage | time | peak memory |
| --- | ---: | ---: |
| `corpus.assemble` | 12 min | 28 GB |
| `keywords.extract` | 23 h | 90 GB |
| `keywords.build` | 6 h | 66 GB |
| `themes.space` | 6 min | 3.5 GB |
| `themes.group` | 7 s | 1.0 GB |
| `themes.apply` | 4 min | 3.7 GB |
| `map.layout` | 26 min | 2.7 GB |
| `map.trajectories` | 300 h | 66 GB |
| `overlays.position` | 5 h | 3.4 GB |

The machine it needs: 128 GB of memory or more (the extraction holds every
text's analysis until it scores them), 150 GB of free disk on a file system
with 7 million free inodes (the engine reads one file per text), and 8 cores
or more (the numeric steps use every core). Two stages would be far too slow
as they are: the trajectories place every period of every person among a
million people by brute force (an index of the people's vectors would take
them from weeks to hours), and the extraction parses every text on one core
(`KeywordsConfig.extraction_n_jobs` parses in worker processes, which the
build does not use yet).
