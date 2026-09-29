# The demo world

`cartolex.demo` generates an invented research community: people, groups,
works and their texts, in English and French — or, as a variant, in English,
French and Portuguese. Everything is synthetic and deterministic. The same size and seed always give the same files, byte for
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
the field, each with a list of technical terms in English, French and
Portuguese (Brazilian spelling):

| Theme | English name | French name | Portuguese name |
| --- | --- | --- | --- |
| `coastal-geomorphology` | Coastal geomorphology and sediment transport | Géomorphologie côtière et transport sédimentaire | Geomorfologia costeira e transporte de sedimentos |
| `ocean-circulation` | Ocean circulation and tides | Circulation océanique et marées | Circulação oceânica e marés |
| `marine-ecology` | Marine ecology and biodiversity | Écologie marine et biodiversité | Ecologia marinha e biodiversidade |
| `fisheries-aquaculture` | Fisheries and aquaculture | Pêche et aquaculture | Pesca e aquicultura |
| `plankton-biogeochemistry` | Plankton and biogeochemistry | Plancton et biogéochimie | Plâncton e biogeoquímica |
| `coastal-hazards` | Coastal hazards and sea-level rise | Risques côtiers et élévation du niveau de la mer | Riscos costeiros e elevação do nível do mar |
| `ocean-observation` | Ocean observation and remote sensing | Observation de l'océan et télédétection | Observação do oceano e sensoriamento remoto |
| `marine-pollution` | Marine pollution and microplastics | Pollution marine et microplastiques | Poluição marinha e microplásticos |
| `coastal-governance` | Coastal communities and governance | Sociétés littorales et gouvernance | Sociedades costeiras e governança |
| `blue-economy` | Ports, shipping and the blue economy | Ports, transport maritime et économie bleue | Portos, transporte marítimo e economia azul |
| `estuaries-wetlands` | Estuaries and wetlands | Estuaires et zones humides | Estuários e áreas úmidas |
| `paleoceanography` | Paleoceanography and climate archives | Paléocéanographie et archives climatiques | Paleoceanografia e arquivos climáticos |

Each theme has about seventy hand-written terms (`sediment transport` /
`le transport sédimentaire` / `o transporte de sedimentos`) and several
hundred compound terms built from families of aspects and objects
(`hake biomass` / `la biomasse du merlu` / `a biomassa da merluza`,
`glider calibration` / `l'étalonnage des planeurs` /
`a calibração dos planadores`): 5,669 distinct terms in all, each with one
form per language. A few terms belong to two themes, so themes overlap as they do in a real
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

### Bodies: full texts

```bash
python -m cartolex.demo create --size S --seed 0 --bodies --out demo-S-full --corpus
```

With `--bodies` (`generate(..., bodies=True)`), every work also gets a body
(`Work.body`), written after its title and abstract in its text file: an
introduction, material and methods, results with two figure or table captions,
and a discussion, each under its heading, about 700 words in all. Sentences
are drawn with replacement from a small pool of templates filled with the
work's focal terms, so the body is long, repetitive and full of generic
phrasing (`the present study`, `the data set`, `a significant difference`),
as full texts are. Bodies come from a random stream of their own: people,
bibliography, titles and abstracts are those of the world without bodies.
Size S with bodies holds about 243,000 words. They exist to measure choices
that matter for full texts (lexicon lab, {doc}`dev/lexicon-lab`).

### The trilingual variant

```bash
python -m cartolex.demo create --size S --seed 0 --languages en,fr,pt --out demo-S-pt --corpus
```

```python
world = generate(size="S", seed=0, languages="en,fr,pt")
```

A trilingual world has the groups, people and bibliography of the default
world of the same size and seed: the same works, years, types, authors, DOIs
and sources. About one group in five writes often in Portuguese (never one
that writes often in French), so some English works become Portuguese ones;
French works stay French. Every text is written again, and Portuguese
articles go to Portuguese venues. For seed 0:

| Size | Works | English | French | Portuguese | Words |
| --- | --- | --- | --- | --- | --- |
| `XS` | 75 | 45 | 19 | 11 | 15,027 |
| `S` | 259 | 143 | 76 | 40 | 53,326 |
| `L` | 2,309 | 1,551 | 460 | 298 | 475,044 |

The default worlds (`en,fr`) do not change with this option: their files are
byte-identical to those of earlier versions (`tests/test_demo_trilingual.py`
pins their manifests), since the numeric reference is made on them.

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

A world made with other options than the default ones (trilingual, or with
bodies) also records its `languages`, `bodies` when it has them, the
Portuguese `name_pt`, `pt` and `pt_article` of its themes and terms when it is
trilingual, and a `lexicon`: one record per phrase the texts are written with
and per language — `text`, `lang`, `kind` and `field` (whether it is a field
term):

| `kind` | what | `field` | more |
| --- | --- | --- | --- |
| `theme` | a theme term, without its article | yes | `canonical` (English form), `themes`, `technique` |
| `method` | a shared method | yes | `canonical`, `scope` (natural, social, any) |
| `driver` | a driver of change (`climate change`) | no | `canonical` |
| `setting` | a study-setting phrase (`on sandy beaches`) | no | `canonical` |
| `template` | a literal piece of a sentence template or lead-in: generic filler | no | |

With bodies, the pieces of the body templates and the section headings are
`template` records too. `cartolex.demo.lexicon_truth(languages, bodies=…)`
gives the same records for any language set. A default world's truth keeps
the format above, byte for byte. The manifest of a trilingual world names its
`languages`, and its counts give `works_pt`; that of a world with bodies has
`"bodies": true`.

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
python tools/demo_stats.py --size S --languages en,fr,pt
```

Seed 0, Python 3.12, one process on a laptop, parse cache empty; the last two
columns are the trilingual worlds (read with the three corpus languages):

| | S | L | S, trilingual | L, trilingual |
| --- | --- | --- | --- | --- |
| People in the cohort (with works) | 40 (39) | 350 (343) | 40 (39) | 350 (343) |
| People in the projected set | 4 | 35 | 4 | 35 |
| Groups | 7 | 33 | 7 | 33 |
| Works in the corpus | 226 | 2,137 | 226 | 2,137 |
| … in French / in Portuguese | 66 / – | 437 / – | 66 / 34 | 437 / 266 |
| Index rows | 535 | 5,241 | 535 | 5,241 |
| Words | 46,036 | 435,872 | 46,509 | 438,880 |
| Candidate terms, English / French / Portuguese | 2,516 / 1,467 / – | 9,260 / 6,042 / – | 2,268 / 1,451 / 942 | 8,742 / 5,987 / 4,794 |
| Global keywords | 3,931 | 15,182 | 4,599 | 19,378 |
| Refined keywords | 1,820 | 8,866 | 2,142 | 11,595 |
| Atlas terms (rows of `umap_terms.csv`) | 552 | 3,254 | 574 | 3,468 |
| Concepts / proto-subfields | 150 / 30 | 150 / 30 | 150 / 30 | 150 / 30 |
| Atlas terms that are theme terms or methods | 50 % | 72 % | 44 % | 64 % |
| … parts of one (a head word, a piece) | 32 % | 22 % | 37 % | 27 % |
| … study settings or drivers | 6 % | 1 % | 7 % | 2 % |
| … generic phrasing of the templates | 12 % | 5 % | 12 % | 7 % |
| Engine run, total | 36.6 s | 120.8 s | 40.4 s | 131.6 s |
| … extraction / consolidation / UMAP | 12.7 / 5.8 / 17.8 s | 71.2 / 27.1 / 21.4 s | 15.1 / 7.5 / 17.5 s | 71.7 / 37.4 / 21.3 s |
| Peak memory | 812 MB | 952 MB | 828 MB | 959 MB |

Seed 1 gives 501 atlas terms for S and 3,137 for L. Most of the extraction
time is parsing, done once: with the parse cache filled by a first run, the
extraction of L takes about 30 s (most of it the language detection), and
with four worker processes a first run takes about 30 s too. A language
model takes a few hundred MB while its language is parsed, hence most of the
peak memory. Most of the UMAP time is the one-off compilation of its
numerical code. Generic phrasing is a long tail of template phrases that each
reach one person's list; the engine's optional clean-up stage, not run here,
is meant to remove such phrases.

Two behaviours of the current engine show in these runs:

- the English templates are full of `the X of Y` phrasing; the extraction's
  English pattern takes no `of` complement (the lexicon lab's default, see
  {doc}`dev/lexicon-lab`), so such spans give their parts (`silicic acid
  uptake`) rather than one long candidate each: with the complement, size L
  had 22,722 English candidates, about 13,500 of them containing `of`;
- at size L with Portuguese the refined keyword list is cut to the 10,000
  best-scored terms (the default of `global_top_n`).

## Engine settings

The measurements use the engine's defaults except for one setting, kept in
`ENGINE_SETTINGS` at the top of `tools/demo_stats.py`. A later reference run
reuses that dictionary verbatim.

| Setting | Value | Why |
| --- | --- | --- |
| `kw_recency_years` | `0` | works span 2012–2026: use the whole history |

The demo corpus sits in the engine's default corpus slot, `manual`, alone. A
trilingual world is read with `corpus_languages` and `display_languages` set
to its three languages.

Defaults in effect, for reference: noun-phrase candidates of at most five
word units (see [the extraction](dev/extraction.md)), `min_df` 3,
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
and the bibliography unchanged. The Portuguese shares of a trilingual world
come from a stream of their own (languages), and a work's language is one
draw whatever the language set, so a trilingual world keeps the bibliography
of the default one. The `generator` field of the manifest names
the generator version (`GENERATOR_VERSION` in `cartolex.demo`): raise it with
any change that alters the output.

## Demo services

`cartolex.demo.services` serves, on your own computer, the part of OpenAlex
and of the ORCID registry that cartolex uses, answering for a demo world. Its
bibliographic layer is derived from the world with random streams of its own
(the world never changes) and holds what a real index holds: homonyms, a
person split over two records, a record that merges two people, people
without records, works declared in the registry, affiliations that change
over time, co-authors from outside the community, and duplicate texts (the
same article under a second DOI, a conference version and a preprint of an
article). It lets the collection
run offline, in tests and in demonstrations:

```bash
python -m cartolex.demo services --size S --seed 0 --people-list people.csv
```

See {doc}`collection` for a whole collection against it, and
{doc}`dev/collection` for what it serves and how to inject failures.
