# The demo world

`cartolex.demo` generates an invented research community: people, groups,
works and their texts, in English and French. Everything is synthetic and
deterministic. The same size and seed always give the same files, byte for
byte, so the demo world serves the test suite, the numeric reference run,
this documentation, screenshots and usability sessions alike.

```bash
python -m cartolex.demo create --size S --seed 0 --out demo-S --corpus
```

```python
from cartolex.demo import generate

world = generate(size="S", seed=0)
world.write("demo-S")                  # the neutral cartolex-demo/1 files
world.write_corpus("demo-S/workspace")  # the engine's corpus contract
```

`--corpus` also writes the corpus contract into `OUT/workspace`. A folder that
already holds a demo world is refused unless `--force` (or `overwrite=True`)
is given.

## The invented field

The community works on **coastal and marine systems**. Twelve themes make up
the field, each with a list of technical terms in English and French:

| Theme | English name | French name |
| --- | --- | --- |
| `coastal-geomorphology` | Coastal geomorphology and sediment transport | Géomorphologie côtière et transport sédimentaire |
| `ocean-circulation` | Ocean circulation and tides | Circulation océanique et marées |
| `marine-ecology` | Marine ecology and biodiversity | Écologie marine et biodiversité |
| `fisheries-aquaculture` | Fisheries and aquaculture | Pêche et aquaculture |
| `plankton-biogeochemistry` | Plankton and biogeochemistry | Plancton et biogéochimie |
| `coastal-hazards` | Coastal hazards and sea-level rise | Risques côtiers et élévation du niveau de la mer |
| `ocean-observation` | Ocean observation and remote sensing | Observation de l'océan et télédétection |
| `marine-pollution` | Marine pollution and microplastics | Pollution marine et microplastiques |
| `coastal-governance` | Coastal communities and governance | Sociétés littorales et gouvernance |
| `blue-economy` | Ports, shipping and the blue economy | Ports, transport maritime et économie bleue |
| `estuaries-wetlands` | Estuaries and wetlands | Estuaires et zones humides |
| `paleoceanography` | Paleoceanography and climate archives | Paléocéanographie et archives climatiques |

Each theme has about seventy hand-written terms (`sediment transport` /
`le transport sédimentaire`) and several hundred compound terms built from
families of aspects and objects (`hake biomass` / `la biomasse du merlu`,
`glider calibration` / `l'étalonnage des planeurs`): 5,669 distinct terms in
all. A few terms belong to two themes, so themes overlap as they do in a real
field. Terms are tagged as topics (what a work is about) or techniques (how
it is done), and a shared pool holds 53 methods, 20 study settings and 10
drivers (`climate change`, `storm events` …). Two themes use the phrasing of
the social sciences; the others that of the natural sciences.

## People, groups and identifiers

- **People** have invented surnames built from syllables and first names from
  a mixed international list. Full names are unique, even without accents.
  There is no gender attribute anywhere. Each person has a career stage
  (`phd`, `postdoc`, `researcher`, `senior`), one to three dominant themes and
  a preference for a few objects within them, as specialists do.
- **Groups** have invented names and acronyms, belong to invented
  institutions at fictional places (Port Aurel, Veldhaven, Saint-Onval …) and
  have synthetic coordinates that are not those of any real place. Each group
  has a theme profile; about one group in five writes often in French.
- **Identifiers can never be real.** ORCID-format identifiers use the unissued
  block `0000-0000-XXXX-XXXX` with a valid ISO 7064 MOD 11-2 check character;
  author identifiers are `A999` followed by seven digits; open-archive author
  slugs are `demo-<first>-<last>`; DOIs use the test prefix,
  `10.5555/cartolex-demo.<size>.<n>`, and are unique within one world. Some
  people lack some identifiers and some works have no DOI.

## Works and texts

Each work has a title and an abstract of 120 to 250 words written from
templates filled with theme terms, methods and settings, in English (about
80 % of works) or real French (about 20 %, more in some groups). Years run
from 2012 to 2026 (the pinned current year). Document types are `article`,
`preprint`, `proceedings`, `report` and `thesis` (theses only for doctoral
candidates). Works have one to four authors, mostly from one group, sometimes
from two. Each work is findable in one or more sources (`openalex`, `hal`,
`orcid`). Some people have few works and a few have none, for collection
tests.

About 10 % more people form a **projected set** (role `overlay:applicants`)
with works of their own. They are never part of the fitted cohort: they never
co-author with it and never reach the corpus index of the cohort.

## Sizes

| Size | Cohort | Projected set | Groups | Works | In French | Words | Generation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `XS` | 12 | 2 | 4 | 75 | 19 | 15,322 | 0.1 s |
| `S` | 40 | 4 | 7 | 259 | 76 | 52,784 | 0.2 s |
| `L` | 350 | 35 | 33 | 2,309 | 460 | 470,741 | 2.2 s |

Figures for seed 0; works include those of the projected set. `XS` is for
fast unit tests, `S` for the quick reference run and most screenshots, `L`
for scale.

## The neutral format: `cartolex-demo/1`

`DemoWorld.write(out_dir)` writes:

```text
manifest.json      format, size, seed, generator, counts, and the sha256 of every other file
people.csv         person_id, last_name, first_name, group, institution, site,
                   career_stage, orcid, openalex_id, idhal, role
groups.csv         group_id, acronym, name, institution, site, lat, lon
works.csv          work_id, title, year, doc_type, language, doi, venue,
                   sources (semicolon list), text_path
authorships.csv    work_id, person_id, position
texts/<work_id>.txt   title, blank line, abstract
truth.json         the ground truth (below)
```

`role` is `cohort` or `overlay:<set>`; `group` is a `group_id`. All files are
UTF-8 with `\n` line endings.

`truth.json` holds what the generator knows and a map can only estimate:

- `themes`: each theme's `id`, `name_en`, `name_fr` and `terms`, every term
  with `en`, `fr`, `fr_article` and `technique`;
- `groups` and `people`: theme weights per group and per person (summing
  to 1);
- `works`: the themes of each work, primary first;
- `coverage`: `good`, `thin` or `no_data` per person.

## The corpus contract

`DemoWorld.write_corpus(workspace)` writes what the engine reads (see
`INTEGRATION.md`, section 2):

- the index of the engine's default corpus slot, `manual_index.csv`, with one
  row per cohort authorship: `last_name`, `first_name`, `unit` (the group
  acronym), `txt_path` (relative to the index's folder), `doc_year`,
  `doc_type`;
- `automatic_data/corpus_manual/<work_id>.txt`;
- for each projected set, `overlay/<set>/index.csv` with the same columns and
  `overlay/<set>/texts/`. Projected sets never reach the corpus slot.

People without works have no rows.

## Measured statistics

`tools/demo_stats.py` generates a world, writes its corpus contract into a
scratch folder, runs the current engine offline (extraction, consolidation,
roster, SVD, concept clustering, UMAP) and reports what comes out:

```bash
python tools/demo_stats.py --size S
python tools/demo_stats.py --size L --json stats-L.json
```

Seed 0, Python 3.12, one process on a laptop:

| | S | L |
| --- | --- | --- |
| People in the cohort (with works) | 40 (39) | 350 (343) |
| People in the projected set | 4 | 35 |
| Groups | 7 | 33 |
| Works in the corpus (in French) | 226 (66) | 2,137 (437) |
| Index rows | 535 | 5,241 |
| Words | 46,036 | 435,872 |
| Raw keywords, English / French | 3,687 / 1,976 | 14,030 / 7,163 |
| Global keywords | 5,582 | 20,998 |
| Refined keywords | 2,147 | 10,358 |
| Atlas terms (rows of `umap_terms.csv`) | 553 | 3,215 |
| Concepts / proto-subfields | 150 / 30 | 150 / 30 |
| Atlas terms that are theme terms or methods | 45 % | 68 % |
| … parts of one (a head word, a piece) | 31 % | 23 % |
| … study settings or drivers | 6 % | 2 % |
| … generic phrasing of the templates | 18 % | 8 % |
| Engine run, total | 25.1 s | 70.3 s |
| … extraction / consolidation / UMAP | 3.5 / 4.1 / 17.3 s | 29.7 / 18.7 / 21.0 s |
| Peak memory | 579 MB | 827 MB |

Seed 1 gives 535 atlas terms for S and 3,071 for L. Most of the UMAP time is
the one-off compilation of its numerical code. Generic phrasing is a long
tail of template phrases that each reach one person's list; the engine's
optional clean-up stage, not run here, is meant to remove such phrases. It is
higher for S seed 0, the most French of these worlds.

Two behaviours of the current engine show in these runs:

- a French term that contains a word of one or two letters (`le trait de
  côte`, `la biomasse du merlu`) is never kept whole by the extraction
  filters, so French terms reach the atlas as adjective phrases
  (`érosion dunaire`) or as pieces (`biomasse`, `merlu`);
- at size L the refined keyword list is cut to the 10,000 best-scored terms
  (the default of `global_top_n`).

## Engine settings

The measurements use the engine's defaults except for one setting, kept in
`ENGINE_SETTINGS` at the top of `tools/demo_stats.py`. A later reference run
reuses that dictionary verbatim.

| Setting | Value | Why |
| --- | --- | --- |
| `kw_recency_years` | `0` | works span 2012–2026: use the whole history |

The demo corpus sits in the engine's default corpus slot, `manual`, alone.

Defaults in effect, for reference: n-grams of 1 to 4 words, `min_df` 3,
`max_df` 0.6, length bonus 2.0, `nested_threshold` 1.3, 30 keywords per
person, `global_top_n` 10,000, `corpus_languages` French and English with
English as the reference language; 20 SVD components, 150 concepts from 50
components, 30 proto-subfields; UMAP with 25 neighbours, minimum distance
0.3, cosine metric, the researcher layout and random state 3. No setting was
changed to reach the size targets; the vocabulary and the generator were
tuned instead.

## Determinism

The generator draws everything from `random.Random` instances seeded from the
size, the seed and one named stream per concern (structure, people, works,
text), never from the global random state, and writes rows and keys in a
fixed order. A change to the sentence templates therefore leaves the people
and the bibliography unchanged. The `generator` field of the manifest names
the generator version (`GENERATOR_VERSION` in `cartolex.demo`): raise it with
any change that alters the output.
