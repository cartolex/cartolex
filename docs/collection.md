# Collecting people and texts

A project maps the people of a field from their texts. Collection brings them
in: a list of people, then the records a bibliographic service holds for each
of them, then their works. Documents you already have come in without any
service. Everything collected lands in the project's source tables
({doc}`format/sources`); what you decide along the way (who is who, who is
the same person) lands in `decisions/people.csv`.

Before anything leaves your computer, cartolex says what will be sent, to
which host and why: see {doc}`privacy`.

## Ways in

### A list of people

```bash
cartolex collect people my-project people.csv --dry-run   # show how the columns are read
cartolex collect people my-project people.csv
cat names.txt | cartolex collect people my-project -       # a pasted list
```

A list is a CSV file or pasted text, separated by tabs, semicolons or commas,
with or without a header. cartolex proposes how to read each column:

| column | read as | header words it recognises (English, French, Portuguese, Spanish) |
| --- | --- | --- |
| a full name | the name, split at a comma (`Last, First`), at the words in capitals (`TAVELIN Ada`), or before the last word and its particles | `name`, `full name`, `nom complet`, `nome completo` |
| last and first names | the name | `last name`, `surname`, `family name`, `nom` (with `prénom`), `sobrenome`, `apellido`; `first name`, `given name`, `prénom`, `nombre` |
| identifiers | the person's ORCID (checked), OpenAlex id, HAL id; also found from the values alone | `orcid`, `openalex`, `idhal` |
| e-mail | **refused**: an address is never an identifier and is never stored | any header with `mail`, or a column of addresses |
| role | `mapped`, `context`, `projected` or `excluded` | `role` |
| projected set | the set a projected person belongs to | `set`, `overlay` |
| organisations | one level each: by the project's level ids and names, else lab-like words (`lab`, `team`, `unit`, `department`…) go to the smallest level and institution-like words (`institution`, `university`, `affiliation`…) to the largest | |
| anything else | a **filter**: kept as a person attribute, to colour or select people later | |

A list without a header is read from its values: one text column is a full
name, two are the last then the first name. `--dry-run` prints the proposal
and the mapping as JSON; edit it and give it back with `--mapping`.

Each new person gets a row in `decisions/people.csv` with the role the list
gives (`mapped` by default) and the identity `pending`. Importing the same list
again changes nothing; a person already decided keeps their decisions.

**Duplicates** are proposed, never merged: two rows that share an identifier,
whose names are the same once case, accents, hyphens and particles are set
aside, or whose first names agree (one may be an initial) with one surname
containing the other.

```bash
cartolex collect duplicates my-project
cartolex collect merge my-project p000012 p000031   # p000031 is p000012
```

A merge is recorded in `decisions/people.csv` (`merged_into`) and the other
name goes to the person's aliases, which searches try too.

### A folder of documents

```bash
cartolex collect folder my-project reports/ [--create-people]
```

PDF and text files, matched to people by a sub-folder per person
(`reports/Ada Tavelin/…`) or a name in the file name
(`tavelin-2021-report.pdf`). Each file is read on its own: a file that cannot
be read, that holds no text (a scan without a text layer), that takes more than
two minutes to read (a PDF is read in a separate process, which is stopped
then), or that names nobody or several people is reported with its reason, and
the others come in. With
`--create-people`, a sub-folder that names nobody creates a person. A year in
the file name dates the text.

### An existing corpus

```bash
cartolex collect corpus my-project manual_index.csv
```

A corpus in the engine's contract (an index with `last_name`, `first_name`,
`unit`, `txt_path` and optionally `doc_year`, `doc_type`, and text files) comes
in whole: one person per name and unit, the unit as an organisation of the
project's first level, other columns as person attributes, and a text file
listed for several people as one text with several authors.

Folders and corpora bring whole documents: the build reads the texts of a
folder or a corpus slot whole by default, and a collection slot's by their
title and abstract (the rule `parts_by_slot_kind`, recorded as such in the
stage's `run.json`). Parts you set in `params.json` (`corpus.assemble.parts`)
apply to every slot, and the import says so when they leave the documents out.

## Who is who: resolution

```bash
cartolex collect resolve my-project --dry-run    # what would be sent
cartolex collect resolve my-project --auto
```

For each person whose identity is pending, cartolex looks for their author
records in OpenAlex:

- every **variant of the name** is searched: as written, without accents, each
  half of a compound surname, without particles, with the first name as an
  initial;
- a search **restricted to the stated institutions** is added, on the full name
  only. The institution only **ranks** candidates, it never filters them: a
  lab-level affiliation often resolves to an institution record people never
  cite, and a filter would drop the right person and keep a homonym;
- an ORCID or OpenAlex id given in the list is looked up directly.

Each candidate is shown with its evidence: the name as recorded, its
institutions with years, its number of works, first and last year, top topics,
its ORCID, and how each piece of evidence counted in its score (the name up to
0.6, the same ORCID 0.4 or another one −0.5, an id given in the list 0.4, the
stated institution 0.25, an institution it belongs to 0.2, a similar name
0.15).

With `--auto`, a person is accepted automatically only when **a single
candidate** scores 0.8 or more (`--threshold`) and nothing contradicts it. The
identity is then `auto`: it stays marked for review. Everyone else waits
(`pending`) with their candidates.

**The registry separates people an index merged.** An index can merge two
people into one record and copy one person's ORCID onto every work of that
record, so filtering the index by ORCID does not separate them. When the
person or a likely record carries an ORCID, cartolex reads the works declared
in the ORCID registry and compares them with the record's, by DOI. When they
disagree (many of the record's works are not declared, or many declared works
are not on the record), both are shown with their counts and you decide.

Confirming records:

```bash
cartolex collect confirm my-project p000007 openalex:A123 openalex:A456   # two records
cartolex collect confirm my-project p000007 https://orcid.org/0000-…      # the registry
cartolex collect confirm my-project p000009 --none                        # no record exists
```

A person can have several records: list them all. « None » is a valid answer.
An OpenAlex id, an ORCID, or a URL holding one can be pasted.

## Collecting the works: the harvest

```bash
cartolex collect harvest my-project --dry-run
cartolex collect harvest my-project [--years 2012-2026] [--refresh | --cache-only]
```

For each confirmed person, the harvest collects every work of their OpenAlex
records and every work they declared in the registry (fetched from OpenAlex by
DOI), unites them, and removes duplicates by DOI, then by title and year. The
works of several people's records are asked for together, up to 50 records a
request: a large list of people costs about one request per 100 works, not
one or more per person. A list names a work's first 100 authors only: a work
with more (a large collaboration) is read again on its own, free of charge, so
that the people further down its list are found on it. Each
work becomes a text with its title and abstract, the language of each detected;
its authorships name every project person on it at their rank, with the
institutions it states for them, which date their affiliations. Employments
declared in the registry date affiliations too.

The index also lists datasets, software, peer reviews and other records that
are not texts: they stay in the tables, and by default the build reads a
collection slot's articles, preprints, reviews, books, chapters, theses,
reports and communications only (the rule `doc_types_by_slot_kind`); a slot's
own `doc_types` in `project.json` replace the list.

Harvesting a person again replaces what the earlier harvest brought for them;
texts keep their ids. `--years` keeps a window of years; without it, the
harvest uses the slot's window, set once for all its collections:

```bash
cartolex collect window my-project 2015-      # from 2015 on (2015-2024, 2020, none)
```

(`years` of the slot in `project.json`; by default, every year). Answers are
cached in the project: `--refresh` fetches again, `--cache-only` works offline
from what was fetched before and says what is missing. Ctrl-C stops after the
current request: the people harvested before it are kept. A person whose
collection fails (a service that keeps failing, a page cut short) is reported
with the cause and the others go on; after three failures in a row the
harvest stops and keeps what it collected (see the coverage report below).

**The OpenAlex key.** OpenAlex counts list requests against a daily budget:
$0.10 a day without a key (about 100,000 works), $1 with a free key (about a
million). Save the key once in the app (Settings › Data sources), or give it
with `--openalex-key` or `$OPENALEX_API_KEY`, which win over the saved one. The
command line uses the key saved in the app when it is given none (`--data-dir`
names the app's folder when it is not the default one); a key saved while the
app runs serves its next collection.

## From institutions

A project can start from one or several institutions instead of a list:

```bash
cartolex collect institutions my-project --search "Marine Station"     # find it
cartolex collect institutions my-project --institution I999… --dry-run  # what is sent
cartolex collect institutions my-project --institution https://ror.org/0… --years 2015-
cartolex collect institutions my-project --take all                     # or --take A1 A2+A3
cartolex collect harvest my-project
```

An institution is given by its OpenAlex id or its ROR id, or searched by name:
the search only lists the institutions that bear the name, with their type,
ROR id, parents and works, for you to choose. cartolex then reads the
institution and **every unit below it** (labs, departments), and every work
signed there in the window, and proposes the authors with at least two works
there (`--min-works`). Each comes with its evidence: the works in the window,
the first and the last year, the units they stated (a lab below the
institution, several when they moved), their ORCID when the index shows one,
and the person of the project they already are, if any.

- **Years come from the works.** Each work states where each author was that
  year: a person who left keeps the years they were there, and only those.
- **Levels.** The units enter as organisations of the project's levels: the
  index's types of institution are mapped to them (by default, education,
  government, health, companies and non-profits to the largest level, the
  others, such as facilities, to the smallest; a project without levels gets
  `unit` and `institution`). The proposal prints the mapping; change it with
  `--level facility=lab`.
- **Several parents.** A unit that belongs to several institutions (a joint
  unit) enters with all of them.
- **Split records** of one person (the same name, first names that agree, one
  may be an initial, at the same unit, with no work in common; or a shared
  ORCID) are suggested, never merged: `--take A1+A2` takes them as one person.
- **Large institutions.** The works are read 100 at a time (the most a page
  holds), with only the three fields the proposal reads, and folded into each
  author's counts as they come: memory grows with the authors, not with the
  works. A national institution signs hundreds of thousands of works a year:
  one request per 100 works, so a million works is 10,000 requests, a whole
  day of OpenAlex's free budget with a key (a tenth of it without one). Narrow
  the years or choose units below the institution, or read the OpenAlex
  snapshot instead (`collect snapshot`, below). In the app, a list of more
  than 100,000 works waits for your confirmation after its first page, and
  the progress shows the works read, the requests, the rate and the time left.
- **Stop and resume.** Every 100 pages the place reached and the counts are
  saved in the slot's `raw/checkpoints/`. Ctrl-C (Stop in the app), or a page
  that still fails after its retries (a spent budget, a service down), pauses
  the reading with its cause; `--resume` (Resume in the app) goes on from
  there and gives the proposal an uninterrupted reading gives. A pause older
  than a week, or a place the service no longer takes, starts again from the
  first page, with a note.

The people taken are `mapped` by default (`--role context`, `projected` or
`excluded` otherwise), with their records confirmed; their works come with the
harvest.

## From collaborators

From confirmed seeds (every mapped person with an OpenAlex record, or
`--seeds`), their co-authors are proposed round by round:

```bash
cartolex collect collaborators my-project --dry-run
cartolex collect collaborators my-project              # round 1; again: round 2
cartolex collect collaborators my-project --decide p000123=mapped p000124=no
```

Each collaborator comes with the works written together, the people of the
round before they wrote with (the seeds, in round 1), the **path** back to a
seed, the last joint year, the organisation stated on the latest joint work,
and their **topical fit**: the cosine similarity between the words of their
own titles and abstracts (those not written with the seeds; the joint ones
when there is no other) and the seeds', each word weighted by its frequency
and its rarity; 1 is the same vocabulary, 0 no word in common.

- A work with **more than 25 authors** is left out of the co-author graph: a
  large collaboration says little about who works with whom
  (`collect.snowball.max_authors` in `params.json`, or `--max-authors`).
- **Whole rounds only**, up to a **cap** of 200 people proposed
  (`collect.snowball.cap`, or `--cap`): a round that would pass it is left out
  whole, with a warning that names it.
- A collaborator is **context** by default: their texts shape the lexicon
  with a weight, they are not on the map. Decide otherwise with `--decide`:
  `mapped`, `projected`, `no` (excluded) or `later`. Decisions go to
  `decisions/snowball.csv`; the next round starts from the collaborators not
  refused.

## Large collections: the OpenAlex snapshot

From a national size up, asking OpenAlex person by person costs days of its
daily budget; the **snapshot** holds the same records, to download once:

- **What:** every OpenAlex entity (works, authors, institutions…) as
  gzip-compressed JSON lines, one entity per line in the API's shape, under
  `data/jsonl/<entity>/updated_date=YYYY-MM-DD/part_NNNN.gz`, with a
  `manifest.json` per entity and a log of deleted works (a Parquet copy sits
  beside it; cartolex reads the JSON lines).
- **Size:** about 626 million records and 745 GB compressed in the September
  2026 release (the manifests give the current figures).
- **Licence:** CC0: public domain, no condition of use.
- **Updates:** the public snapshot is released four times a year, on the
  second Wednesday of January, April, July and October (daily snapshots are a
  paid service).
- **Download:** free, no account: `aws s3 sync "s3://openalex/data/jsonl"
  "openalex-snapshot/data/jsonl" --no-sign-request` (the whole of it, or only
  `works`, `authors` and `institutions`, the entities cartolex reads).

(Checked on 28 September 2026 on OpenAlex's help pages, « Snapshot » and
« Snapshot data format », dated 24 September 2026.)

```bash
cartolex collect snapshot my-project openalex-snapshot/ --dry-run
cartolex collect snapshot my-project openalex-snapshot/          # the harvest, from it
cartolex collect institutions my-project --institution I… --snapshot openalex-snapshot/
cartolex collect collaborators my-project --snapshot openalex-snapshot/
```

The snapshot is read on your computer: nothing is sent to OpenAlex (the ORCID
registry is still asked for what people declared). It is **streamed**: each
part is read line by line and only the lines that may concern the people,
institutions or identifiers asked for are parsed, so memory holds what is
found, never a part. A harvest makes one pass over the authors and one over
the works for all its people; a round of collaborators two passes over the
works. `--jobs 4` reads four parts at once, in worker processes. The tables are the same as from the API; a record's retrieval time is
the snapshot's release date.

## What was collected for whom: coverage

```bash
cartolex collect coverage my-project                    # every person's state
cartolex collect coverage my-project --person p000017   # why this profile
cartolex collect coverage my-project --json
cartolex collect coverage my-project --retry --dry-run  # what a retry would send
```

Each person is **good** (three texts with an abstract or more:
`collect.coverage.good` in `params.json`, or `--good`), **thin** (fewer;
titles without an abstract are counted apart), **failed** (the latest
collection for them failed, with its cause) or **no data** (nothing exists).
A failure is never shown as « no data », and never kept once a later
collection reaches the person. The report counts the states by organisation,
and the texts by year and by language.

The person sheet says the sources used (each finder, with its texts) and
discarded (candidate records not confirmed, proposals found by name, preprints
read through their published version), and the **first blocking cause**: a
service failure; no record found; not collected yet; a record found but no
works in the window; works without abstracts; fewer texts with an abstract
than a good profile has. It offers what can be done: **retry** (a failure
only: `--retry` collects again for the people who failed, and them only),
**add documents** (`--add-documents p000017 reports/`, a folder of that
person's documents) and **exclude** (`--exclude p000017`).

## In the app: the corpus screen

The app's **People** screen (`/people`) does all of this without the command
line. Its tabs:

- **People**: everyone, with their role (mapped, context, projected), identity
  (to check, confirmed, no record) and coverage state; filters by role,
  identity, coverage and by the columns of your own list (a list's extra
  columns become filters); change the role of the people selected, or of every
  person the filters keep.
- **Identities**: the queue of the people whose identity waits, with the
  candidate records of every finder (OpenAlex with the ORCID registry as
  evidence, HAL, SciELO), their score and evidence. The keyboard does it all:
  ↑ ↓ a person, 1–9 a candidate, N none of these, ⏎ confirm; each decision is
  saved at once. « Accept the clear matches » confirms, in one go, the people
  with a single candidate and a high score.
- **Organisations**: levels, parents, units and people, each affiliation with
  its years; below, the people of institutions (search an institution by its
  name, choose it, read its people, then take them).
- **Texts**: each text with its richest part (title only, abstract, full
  text), the providers of its parts, the records merged into it and its
  preprints.
- **Collaborators**: the co-authors found round by round, with their joint
  texts, fit and path, the cap and whether a round was cut; decide on one or
  many.
- **Coverage**: good, thin, failed and no data, the first blocking causes, the
  states by organisation, the texts by year and by language; retry what
  failed, exclude the people without data.

**Import** takes a list (CSV or pasted; each column's reading is proposed and
editable, e-mail columns are refused), a folder of documents or a corpus (a
zip), and proposes the possible duplicates. **Collect** runs identities,
harvest, institutions, collaborators or a retry: it always shows first what
leaves the computer (each host, why, what it receives, about how many
requests and their cost, what never leaves), and starts only once you have
read it; the collection then runs in the background, with its progress and a
Stop button in the Activity drawer. A person's **sheet** (⏎ on a row) says why
their profile is what it is, and offers retry, add documents and exclude.

`cartolex app --services demo --world S:0` runs the app against the demo
services of a demo world, on your computer.

## Trying it offline

The demo services answer like OpenAlex and the ORCID registry, on your own
computer, for a demo world ({doc}`demo`):

```bash
python -m cartolex.demo services --size S --people-list people.csv --list-only
cartolex init demo-project --name "Demo" --field "Coastal systems" --languages en,fr
cartolex collect people demo-project people.csv
cartolex collect resolve demo-project --services demo --world S:0 --auto
cartolex collect harvest demo-project --services demo --world S:0
cartolex build demo-project
```

Or from an institution of the demo world, and a mini snapshot of its index:

```bash
cartolex init inst-project --name "Demo" --field "Coastal systems" --languages en,fr
cartolex collect institutions inst-project --search "Marine" --services demo --world S:0
cartolex collect institutions inst-project --institution I999… --services demo --world S:0
cartolex collect institutions inst-project --take all
python -m cartolex.demo snapshot --size S --out demo-snapshot
cartolex collect snapshot inst-project demo-snapshot --services demo --world S:0
cartolex collect coverage inst-project
cartolex build inst-project
```

`python -m cartolex.demo services` without `--list-only` keeps them running
and prints their address, for `--services http://127.0.0.1:PORT/…`.
