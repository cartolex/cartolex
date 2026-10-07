# Collecting people and texts

A map is made from the texts of people: collection brings them into a
project. Most projects start in one of three ways:

- **a list of names** you have (a team, a network, a committee), whose
  records cartolex then finds in the open bibliographic services, before it
  gathers their works ({doc}`tutorial-names`);
- **an institution** (a laboratory, a department, a university), whose
  authors cartolex reads from the works signed there
  ({doc}`tutorial-institution`);
- **documents you already have** (PDF or text files, or a prepared corpus),
  which need no service at all.

Two more ways add people later: the **collaborators** of your people, round by
round, and a **retry** for the people whose collection failed.

The services are OpenAlex (an open index of scholarly works) with the ORCID
registry, and optionally HAL and SciELO ({doc}`collection-sources`). Before
anything leaves your computer, cartolex says what will be sent, to which
service and why, and waits for you to agree: see {doc}`privacy`. Everything
collected stays in the project's folder ({doc}`format/sources`); what you
decide along the way (who is who, who is the same person) is kept with your
other decisions, and can be undone.

This page explains each way in, what cartolex does and how to judge its
proposals. The boxes « On the command line » do the same without the app
({doc}`dev/command-line`).

## The People screen

Everything below is done on the **People** screen. Its tabs:

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

## Ways in

### A list of people

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

**Duplicates** are proposed, never merged on their own: two rows that share an
identifier, whose names are the same once case, accents, hyphens and particles
are set aside (letters such as « ø » or « ł » read as « o » and « l », every
script kept), the same words split another way between surname and given names
or in another order, the same surname with given names that agree (one may be an
initial, or have a middle name), or one surname containing the other. Each pair
is weighed on what the project knows of both (`cartolex.collect.duplicates`): a
shared ORCID or record, the names and the other names, an organisation,
co-authors, texts where both hold the same place (one author recorded twice) or
different known places (two authors of one text), a text of the same title on
each side (one work recorded under both), years that follow on; two different
ORCIDs count strongly against. Evidence against lowers a pair, never hides it:
two rows of the same full name are always proposed, unless someone said they are
two people. When more than 50 people bear a name, two of them are proposed only
with evidence beyond it (an identifier, an organisation, co-authors, a text), and
the Duplicates tab names such names. A pair
is **clear** when both carry the same ORCID or record and their names agree, or
when one author is recorded twice at the same place of a text, never with two
different ORCIDs or a text they wrote together: a name, an organisation and
co-authors in common are not enough, since two namesakes of one lab have all
three. After a merge, a row's pairs are those of the person it is merged into, so a
namesake of a merged row stays proposed.

The corpus screen's Duplicates tab works on **groups**: the pairs at least 70 % likely
join their people (three records of one person are one group), never across a pair
you said is two people nor two different ORCIDs, at most 20 people; every other pair
is a group of two. A group is compared side by side, then merged into the one you
keep (all of them, or those you tick), split (the ticked ones set apart from the
others: two people each), or left for later, each in one step; a merge is undone in
one. « Merge the clear pairs » merges the clear ones in one step, undone in one;
« Merge above a likelihood… » does the same for every pair whose likelihood is at
least a threshold (50 % at first), after showing how many people it would merge and
the groups nearest the threshold. Both join pairs into groups the same way, and
neither ever merges two different ORCIDs, a pair you said is two people (or left for
later), or two people such a pair holds apart through a third. People you know to be
one person are merged by hand: select them in the People list, « Same person… », or
« Same person as… » in a person's sheet; the institutions' proposal takes the people
you select as one person.

````{admonition} On the command line
:class: note

```bash
cartolex collect duplicates my-project [--merge-clear]
cartolex collect merge my-project p000012 p000031   # p000031 is p000012
cartolex collect unmerge my-project p000031         # they stand apart again
```
````

A merge is recorded in `decisions/people.csv` (`merged_into`) and the other
name goes to the person's aliases, which searches try too. The merged row keeps
its own records, identity and role, so an unmerge gives it back as it was; while
merged, the person it is merged into stands for both: the collection asks for
all their records, and the corpus reads all their texts. Two rows with
different ORCIDs are merged only with `--override-orcid`. Pairs judged two
people (or left for later) are remembered in `decisions/people_pairs.csv`.

Measured on the demo worlds with duplicates and namesakes added
(`cartolex.demo.duplicates`: 15 % of the people given a second row written six
ways, 8 % made namesakes of someone, half of them in the same group; worlds S
and L, five seeds each): every one of the 250 duplicates is proposed, the clear
pairs are all right (111 of 111) and hold 44 % of the duplicates; the others wait
for a person, among the most likely pairs (236 of the 250 first pairs are
duplicates), and no group of three or more joins two different people. On a
world of 179,000 people (170,500 generated, 5 % given one or two more rows written
another way, 4,000 renamed into 80 common names), the review lists 26,933 groups;
the first list takes 29 s while the project's views are built, then a page
0.2 s.

````{admonition} On the command line
:class: note

```bash
cartolex collect people my-project people.csv --dry-run   # show how the columns are read
cartolex collect people my-project people.csv
cat names.txt | cartolex collect people my-project -       # a pasted list
```
````

### A folder of documents

PDF and text files, matched to people by a sub-folder per person
(`reports/Ada Tavelin/…`) or a name in the file name
(`tavelin-2021-report.pdf`). Each file is read on its own: a file that cannot
be read, that holds no text (a scan without a text layer), that takes more than
two minutes to read (a PDF is read in a separate process, which is stopped
then), or that names nobody or several people is reported with its reason, and
the others come in. With
`--create-people`, a sub-folder that names nobody creates a person. A year in
the file name dates the text.

````{admonition} On the command line
:class: note

```bash
cartolex collect folder my-project reports/ [--create-people]
```
````

### An existing corpus

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

````{admonition} On the command line
:class: note

```bash
cartolex collect corpus my-project manual_index.csv
```
````

## Who is who: resolution

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

Confirming records: the **Identities** tab, or for an identity already
decided (confirmed, accepted automatically or none) **Change identity…** on
the person's sheet, which opens the same choice again.

````{admonition} On the command line
:class: note

```bash
cartolex collect confirm my-project p000007 openalex:A123 openalex:A456   # two records
cartolex collect confirm my-project p000007 https://orcid.org/0000-…      # the registry
cartolex collect confirm my-project p000009 --none                        # no record exists
```
````

A person can have several records: list them all. « None » is a valid answer.
An OpenAlex id, an ORCID, or a URL holding one can be pasted.

````{admonition} On the command line
:class: note

```bash
cartolex collect resolve my-project --dry-run    # what would be sent
cartolex collect resolve my-project --auto
```
````

## Collecting the works: the harvest

For each confirmed person, the harvest collects every work of their OpenAlex
records and every work they declared in the registry (fetched from OpenAlex by
DOI), unites them, and removes duplicates by DOI, then by title and year. The
works of several people's records are asked for together, up to 50 records a
request: a large list of people costs about one request per 100 works, not
one or more per person. A list names a work's first 100 authors only: a work
with more (a large collaboration) is read again on its own, free of charge, so
that the people further down its list are found on it. Each work is asked for
with the fields cartolex reads and a few small ones kept for later (its other
identifiers, whether it was retracted, where an open copy is, its references);
the index's classifications, locations, funding and citation metrics are left
out. The progress says how
many people are done, the texts received, the requests sent and the time left. Each
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

````{admonition} On the command line
:class: note

```bash
cartolex collect window my-project 2015-      # from 2015 on (2015-2024, 2020, none)
```
````

(`years` of the slot in `project.json`; by default, every year). Answers are
cached in the project: `--refresh` fetches again, `--cache-only` works offline
from what was fetched before and says what is missing. A harvest writes what
it collected every 2,000 people or 10 minutes, whichever comes first: whatever
happens to the job afterwards (a stop, a crash, a computer switched off), those
people are kept. Ctrl-C stops it after the current request (so does SIGTERM:
`kill`, or a service manager stopping the job; its worker processes leave the
stop to it, and a second signal stops at once); it pauses, and the
same command with `--resume` goes on with the people not yet done. A person
whose collection fails (a service that keeps failing, a page cut short) is
reported with the cause and the others go on; after three failures in a row (a
spent daily budget, a service down) the harvest pauses the same way, and
`--resume` goes on later, asking again for the people whose collection failed
(see the coverage report below). In the app, a paused harvest has « Resume »
in the Activity drawer.

**The OpenAlex key.** OpenAlex counts list requests against a daily budget:
$0.10 a day without a key (about 100,000 works), $1 with a free key (about a
million). Save the key once in the app (Settings › Data sources), or give it
with `--openalex-key` or `$OPENALEX_API_KEY`, which win over the saved one. The
command line uses the key saved in the app when it is given none (`--data-dir`
names the app's folder when it is not the default one); a key saved while the
app runs serves its next collection.

````{admonition} On the command line
:class: note

```bash
cartolex collect harvest my-project --dry-run
cartolex collect harvest my-project [--years 2012-2026] [--refresh | --cache-only]
```
````

## From institutions

A project can start from one or several institutions instead of a list:

````{admonition} On the command line
:class: note

```bash
cartolex collect institutions my-project --search "Marine Station"     # find it
cartolex collect institutions my-project --institution I999… --dry-run  # what is sent
cartolex collect institutions my-project --institution https://ror.org/0… --years 2015-
cartolex collect institutions my-project --take all                     # or --take A1 A2+A3
cartolex collect harvest my-project
```
````

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

````{admonition} On the command line
:class: note

```bash
cartolex collect collaborators my-project --dry-run
cartolex collect collaborators my-project              # round 1; again: round 2
cartolex collect collaborators my-project --decide p000123=mapped p000124=no
```
````

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

When it is worth it, what it takes and how to run it well: {doc}`large-projects`.

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

**In the app**, give the snapshot's folder once in Settings › Data sources
(its full path; it is kept on this computer, never in a project). The app checks
it against its manifests and says whether it is ready, incomplete (a download
that stopped midway: run the download command again) or not found (a disk not
plugged in). A harvest, an institutions' reading, a round of collaborators and a
retry then offer two ways of reading OpenAlex, each with its time: the API, or
the snapshot on this computer. The faster is chosen for you, and you can switch
before starting. Each reading goes through the whole snapshot, so the API stays
faster for a small collection; the snapshot wins from a national size, where the
API's daily budget would spread the requests over days. The first reading
measures how fast this computer reads it, and the next estimates use that speed.
When an institution announces more works than the API can read in a day, the
pause offers « Read the snapshot instead ». Identities are always searched on
OpenAlex: a search by name needs the API.

**Indexing the snapshot.** Without an index, every reading goes through the
whole snapshot: hours on an external disk, however few people it is for. Index it
once per release:

````{admonition} On the command line
:class: note

```bash
cartolex collect snapshot-index openalex-snapshot/            # hours; Ctrl-C stops it
cartolex collect snapshot-index openalex-snapshot/            # the same command goes on
cartolex collect snapshot-index openalex-snapshot/ --status   # how far it is
```
````

Indexing reads the works and the authors once and cuts each of their files into
small gzip blocks of the same lines (the files keep about their size and their
content, and any tool reads them as before), then records which blocks hold the
works of each author, institution and DOI, and each author's record
({doc}`format/snapshot-index`). A collection then reads only the blocks that may
hold what it asks for, and finds the same records: a lab's harvest takes minutes
instead of hours. A national harvest reads most blocks anyway. The files no
longer match OpenAlex's sizes: download a new release into a new folder rather
than syncing over an indexed one.

````{admonition} On the command line
:class: note

```bash
cartolex collect snapshot my-project openalex-snapshot/ --dry-run
cartolex collect snapshot my-project openalex-snapshot/          # the harvest, from it
cartolex collect institutions my-project --institution I… --snapshot openalex-snapshot/
cartolex collect collaborators my-project --snapshot openalex-snapshot/
```
````

The snapshot is read on your computer: nothing is sent to OpenAlex (the ORCID
registry is still asked for what people declared). It is **streamed**: each
part is read line by line and only the lines that may concern the people,
institutions or identifiers asked for are parsed. What is found waits on disk,
compressed, in the project's `cache/snapshot/` (removed when the job ends),
with the fields the API would have been asked for; memory holds an index of it
(about a quarter of a kilobyte per work found) and the matches of the part
being read, so a national harvest or institution fits in an ordinary
computer's memory. A harvest makes one pass over the authors and one over
the works for all its people; a round of collaborators two passes over the
works. `--jobs 4` reads four parts at once, in worker processes. `--spill DIR` keeps
what the passes find in another folder than the project's `cache/snapshot/`: each
person's records are then read back in no particular order, so when the project
lives on a hard disk, a folder on a fast internal disk turns hours into minutes for a
national harvest (about 17 GB for 170,000 people). A harvest keeps what its passes
find in `snapshot-<key>/` there, its state written every 32 parts or two minutes: stopped,
it goes on with `--resume` from the last part written, without reading again the
passes it finished; the folder is removed when the harvest completes (a harvest
never resumed leaves it: delete it to free the space). The tables are the same as from the API; a record's retrieval time is
the snapshot's release date.

**Rebuilding the tables of a large collection.** After a harvest, the source
tables are rebuilt from every raw run. For millions of records the rows wait in a
scratch database while the runs are read (a few GB of memory whatever the size);
give it a folder on a fast internal disk when the project lives on a hard disk:

````{admonition} On the command line
:class: note

```bash
cartolex collect rebuild my-project --scratch /var/tmp/cartolex   # the tables again
cartolex build my-project --scratch /var/tmp/cartolex --workers 12 # then the build
```
````

`--workers` sets how many worker processes read the runs (and, for `build`, run
the stages' parallel steps); `--memory MB` caps the memory a build's workers may
take in all. The results never depend on them.

## What was collected for whom: coverage

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
service failure; no record found (also when a settled identity names no
record, so a harvest has nothing to collect from); not collected yet; a record
found but no works in the window; works without abstracts; fewer texts with an abstract
than a good profile has. It offers what can be done: **retry** (a failure
only: `--retry` collects again for the people who failed, and them only),
**add documents** (`--add-documents p000017 reports/`, a folder of that
person's documents) and **exclude** (`--exclude p000017`).

````{admonition} On the command line
:class: note

```bash
cartolex collect coverage my-project                    # every person's state
cartolex collect coverage my-project --person p000017   # why this profile
cartolex collect coverage my-project --json
cartolex collect coverage my-project --retry --dry-run  # what a retry would send
```
````
