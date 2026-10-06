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
ran in a fresh process on one machine (x86-64, 20 cores, 32 GB, a build budget
of 12 GB), on 6 October 2026; a stage's peak memory is what its process and
its worker processes held together.

## The tiers

| machine | measured | a first build takes |
| --- | --- | --- |
| a laptop of 20 cores and 32 GB | the synthetic world of 10⁴ people (6.1 × 10⁴ texts) | 7.5 minutes |
| the same laptop | a national harvest: 1.7 × 10⁵ people, 5.9 × 10⁶ texts | about 6 hours from the raw runs to the map, every step under 16 GB |

The national project is described in {doc}`large-projects`. The stages that
grow with the texts (the extraction, the keywords' build, the text space, the
trajectories) size their worker processes to the build's memory budget (by
default 40 % of the computer's memory, at most 12 GB), so a smaller computer
takes longer rather than more memory; the time is mostly the keyword
extraction (it parses every text) and the trajectories (every period of every
person is placed among all the people).

## What each stage costs

Measured (the stage's time, then its peak memory), and estimated from the
fitted cost models for a full world of 10⁵ people (6.1 × 10⁵ texts, 8.2 × 10⁸
characters):

| stage | 10³ | 10⁴ | 10⁵, one title each | 10⁵ (estimate) |
| --- | ---: | ---: | ---: | ---: |
| `corpus.assemble` | 0.8 s, 304 MB | 3.9 s, 606 MB | 8.7 s, 801 MB | 37 s, 1.7 GB |
| `keywords.extract` | 1 min, 5.7 GB | 4 min, 7.3 GB | 2 min, 6.1 GB | 39 min, at most the budget |
| `keywords.build` | 31 s, 536 MB | 53 s, 1.5 GB | 1 min, 1.2 GB | 8 min, 4.4 GB |
| `themes.space` | 7.8 s, 749 MB | 18 s, 2.1 GB | 40 s, 1.7 GB | 1 min, 2.7 GB |
| `themes.group` | 7.9 s, 744 MB | 16 s, 2.1 GB | 13 s, 1.6 GB | 15 s, 1.6 GB |
| `themes.apply` | 2.1 s, 329 MB | 3.2 s, 536 MB | 15 s, 1.0 GB | 11 s, 1.1 GB |
| `map.layout` | 32 s, 732 MB | 56 s, 714 MB | 3 min, 1.2 GB | 3 min, 1.1 GB |
| `map.trajectories` | 13 s, 637 MB | 33 s, 3.6 GB | 5 min, 3.7 GB | 16 min, at most the budget |
| `overlays.position` | 2.9 s, 240 MB | 15 s, 486 MB | 8 min, 2.7 GB | 4 min, 2.6 GB |

The worlds: 965, 9 698 and 97 052 people with texts, 6 205, 60 687 and 97 052
texts, 5 016, 9 705 and 9 217 kept keywords. The world of 10⁵ people has one
work per person, read by its title only (`corpus.assemble.parts = ["title"]`),
so that its extraction takes minutes: its people, keywords and map are those
of a full world, its texts a sixth. The extraction's memory is mostly its
worker processes, each holding its language's model, as many as the budget
allows: on a smaller computer, fewer. Its own process and every other stage's
stayed under 3 GB at 10⁵ people: `tests/test_scale_stages.py` (run with
`--heavy`) builds that world and fails if a stage other than the themes' passes
3 GB in its own process, below the 7.6 GB of one dense people × keywords
matrix.

The cost models (`cartolex.build.STAGES`) are fitted on these builds and on
the demo worlds S and L, to the smallest worst ratio: every stage's estimate is
within a factor of 2.25 of its measure on each of them
(`tests/test_build_costs.py`), and within 1.85 but for the overlays' time. On a
national sample (611,000 texts of 106,000 people) the corpus, the keywords'
build and the trajectories are estimated within 1.2 of their time,
but the themes and the layout of real texts cost two to five times what the
synthetic worlds predict; after a first build, a dry run scales the project's
own last run instead. The estimates beyond 10⁵ people are extrapolations.

**What grows with what.** The corpus and the keyword stages grow with the
texts and their characters (parsing a short title costs a few milliseconds
whatever its length); the space, the layout, the application of the themes and
the placed points with the people mapped; the grouping with the kept keywords,
which stop growing at 10 000 (`keywords.build.max_keywords`).

**Disk.** Measured on the national project (1.7 × 10⁵ people, 5.9 × 10⁶
texts): the raw runs of the harvest 65 GB, their digests 37 GB (kept so that a
rebuild reads only new runs), the source tables 2.0 GB, the parse cache
2.3 GB, the stages' results 6.2 GB (the trajectories 2.7 GB, the corpus
1.4 GB), the app's texts view 1.3 GB; a stage built again keeps its previous
results until its next build. The parse cache and a rebuild's scratch folder
(15 GB at its peak) are best on a fast internal disk when the project lives on
a hard disk.

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
of 2.25 of the measures on the worlds of 10³ to 10⁵ people. A stage whose
estimated peak exceeds the memory available now is marked « cannot run »,
with the numbers, and so is every stage after it:

```text
  run   themes.group       group keywords into topics and themes: about 5 min, 11133 MB (the last run, scaled by kept keywords); asked for; themes.space will be rebuilt; CANNOT RUN: needs about 10.9 GB of memory; the budget is 7.8 GB
```

The build then runs the stages before it, stops there and says why. Free
memory (close other programs) or build on a larger machine; the Python API's
`build(allow_over_budget=True)` runs the stage anyway.

The extraction, the keywords' build, the text space and the trajectories size
their work (their worker processes, their blocks) to the job's memory budget
(`cartolex build --memory`, Settings › Build in the app; by default 40 % of the
computer's memory, at most 12 GB): their estimated peak is at most that budget,
whatever their cost model gives for the project's sizes (`Stage.bounded`).

## A million people

The synthetic world of a million people is prepared, not run: `tools/million.py
plan` prints its sizes and each stage's estimate (from the cost models, scaled
beyond the worlds they were fitted on), and `tools/million.py run --out DIR`
writes the world (in parallel) and builds it stage by stage, recording each
stage's measures. Its 6.1 × 10⁶ texts are as many as the national project's,
which was built on a laptop (see {doc}`large-projects`); its people are six
times as many, and the stages that grow with the people mapped (the text
space, the layout, the trajectories) have not been measured at that size.
