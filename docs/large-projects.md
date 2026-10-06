# Very large projects: a national campaign

Most projects never need this page. cartolex collects from OpenAlex through
its API, which is the faster way **up to about a million works**: with a free
key, OpenAlex allows a daily budget of about 10,000 list requests of up to 100
works, so a collection of that size takes a day or less, and nothing has to be
downloaded. Beyond that (a whole country's research, a whole discipline), the
daily budget spreads a collection over many days, and a downloaded copy of
OpenAlex, the **snapshot**, is the way: nothing is sent to OpenAlex and no
budget applies, but it costs a download, disk space, and hours of reading.

This page is the whole campaign, from the download to a shared site, with what
each step took for a national project: 169,287 people, 11.3 million records of
6.3 million distinct works, 5.9 million texts, built on a laptop.

## What it takes

| | |
| --- | --- |
| Download | about 750 GB of gzip JSON lines (the works 660 GB, the authors 82 GB), free, no account; about 10 hours at 20 MB/s |
| Disk | an external disk of 2 TB holds a release, its index and the project (about 110 GB for the national project: the raw records 65 GB and their digests 37 GB, the tables 2 GB, the map 6 GB); the internal disk needs room for the working files (about 17 GB for a harvest, 15 GB for a rebuild of the tables, 2.3 GB of parse cache) |
| Memory | the national project was built on a laptop of 32 GB and 20 cores, every build step under 16 GB |
| Time | the snapshot read in about 5 hours for a harvest; the tables 4 hours; the map 2 hours 10 |
| Releases | four a year (the second Wednesday of January, April, July and October); a campaign needs one |

## A campaign, step by step

**1. Download the snapshot.**

```bash
aws s3 sync "s3://openalex/data/jsonl" "openalex-snapshot/data/jsonl" --no-sign-request
```

A download that stops resumes when the same command runs again: finished
files are skipped. Download a new release into a new folder: a sync over an
indexed snapshot downloads every file again.

**2. Tell the app where it is, and what this computer gives.** In Settings ›
Data sources, give the snapshot's folder: the app checks it against its
manifests and says whether it is complete, incomplete or not found. In
Settings › Build, give the memory a build may use (by default 40 % of the
computer's memory, at most 12 GB), the worker processes, and a **folder on a
fast internal disk** for temporary files: the builds, and the rebuild of the
tables after a collection, put their working files there.

**3. The people, and the years.** Import the list of people
({doc}`collection`). By default a harvest takes every year of everyone's
career, so the trajectories follow whole careers; a window of years (First
and Last year in the harvest's form, or `cartolex collect window my-project
2016-`) makes a shorter campaign and a lighter map.

**4. Harvest from the snapshot.** In the app, the harvest offers two ways of
reading OpenAlex, each with its time, and chooses the snapshot at this size.
From the command line:

```bash
cartolex collect snapshot my-project openalex-snapshot/ --dry-run
cartolex collect snapshot my-project openalex-snapshot/ --jobs 3 --spill /fast/disk/folder
```

On a hard disk, three worker processes (`--jobs 3`) read fastest: more make
the disk seek between files. `--spill` keeps what the reading finds on a fast
internal disk: the harvest reads it back person by person, in no particular
order, which takes hours on a hard disk and minutes on an SSD. A harvest
writes what it collected every 2,000 people or 10 minutes: stopped (Ctrl-C, a
service stop, a computer switched off), it goes on with `--resume`, or
« Resume » in the app, without reading again what it finished.

**5. The tables.** When the harvest ends, the source tables are rebuilt from
the raw records, in a process of its own for a large collection, in the folder
of Settings › Build. From the command line:

```bash
cartolex collect rebuild my-project --scratch /fast/disk/folder
```

**6. The map.** Start with a dry run: it estimates each stage's time and
memory, from the project's sizes before a first build and from the last run
after it ({doc}`build`, {doc}`sizes`). Then build, in the app or with:

```bash
cartolex build my-project --dry-run
cartolex build my-project --scratch /fast/disk/folder
```

The stages that grow with the texts size their worker processes to the
build's memory: a smaller computer takes longer, not more memory. When the
project lives on a hard disk, put its **parse cache** on the internal disk:
the extraction reads and writes it throughout. Move `cache/parse` there and
leave a link in its place (`ln -s /fast/disk/parse my-project/cache/parse`;
on Windows, `mklink /J`).

**7. Open it in the app.** The first look at each screen makes what it shows
and keeps it in the project's cache for the next sessions: the people list
takes about 40 seconds the first time, the map 9 seconds, then about a second
or less (the measures below).

**8. Share it.** Publish builds an offline site. Its texts make most of its
size, and the page says how much each choice adds: on the national project,
the titles add about 1.1 GB and the abstracts about 8.7 GB more. Leave the
abstracts out of a large site; the titles may be left out too. The site with
titles took 6 minutes and weighs 1.24 GB; the people's nearest neighbours are
most of that time.

## Measured on a national project

On the laptop above, the project on a USB hard disk, the folder for working
files on the internal disk, every build step capped at 16 GB of memory with a
build budget of 12 GB:

| step | time | peak memory |
| --- | ---: | ---: |
| `collect rebuild`: reading the raw runs | 1 h 52 | 7.2 GB |
| `collect rebuild`: writing the tables (5.9 million texts, 10.4 million authorships) | 2 h 08 | 7.4 GB, and 15 GB of scratch |
| `corpus.assemble` | 3 min | 3.2 GB |
| `keywords.extract` | 59 min | 15.0 GB |
| `keywords.build` | 17 min | 11.2 GB |
| `themes.space` | 8 min | 5.8 GB |
| `themes.group` | 5 min | 8.3 GB |
| `themes.apply` | 37 s | 1.9 GB |
| `map.layout` | 7 min | 2.2 GB |
| `map.trajectories` | 32 min | 15.2 GB |
| the shared site, with titles (149,760 people, 9.6 million entries) | 6 min | 6.4 GB |

About 6 hours from the raw runs to the map. The extraction and the
trajectories went past the budget: their worker pools were sized beside a main
process assumed to hold 2 GB. A pool is now sized beside what its main process
holds, and the keywords' build and the trajectories no longer hold every
text's counts in it: on a sample of 825,000 texts, the keywords' build went
from 5.9 to 3.9 GB.

In the app, what takes long to make is made once per version of what it reads
and kept in the project's cache: the texts' view, the copies of a work, the
texts' orders, the map's texts layer and its time windows. In a new session of
the app: the people list about 40 s the first time (half of it checking tables
written before cartolex stamped them: tables it writes are not read again to be
checked), the coverage 7 s, the map 9 s, then about a second or less for every
screen; a person's sheet takes a second. The first build of the texts' view
takes 40 s, the texts layer 30 s and the windows 10 s, once. The app held at
most 5.5 GB. The map's bundle is 67 MB, the time windows 37 MB, the texts layer
a sample of 100,000 texts (27 MB).

## The index (optional)

`cartolex collect snapshot-index SNAPSHOT` cuts the snapshot's files into small
blocks and records which blocks hold each author, institution and DOI (a few
hours once per release, 24 GB). A collection then reads only the blocks it
needs: a laboratory's harvest takes minutes instead of hours. A national
harvest needs most blocks anyway, so the index saves it little; it is worth it
for repeated collections of a few thousand people. The snapshot in detail, and
every command: {doc}`collection`.
