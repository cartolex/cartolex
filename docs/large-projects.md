# Very large projects: the OpenAlex snapshot

Most projects never need this page. cartolex collects from OpenAlex through
its API, which is the faster way **up to about a million works**: with a free
key, OpenAlex allows a daily budget of about 10,000 list requests of up to 100
works, so a collection of that size takes a day or less, and nothing has to be
downloaded. Beyond that (a whole country's research, a whole discipline), the
daily budget spreads a collection over many days, and a downloaded copy of
OpenAlex, the **snapshot**, is the way: nothing is sent to OpenAlex and no
budget applies, but it costs a download, disk space, and hours of reading.

## What it takes

| | |
| --- | --- |
| Download | about 750 GB of gzip JSON lines (the works 660 GB, the authors 82 GB), free, no account; about 10 hours at 20 MB/s |
| Disk | an external disk of 2 TB is enough for one release and its index; the internal disk needs room for a harvest's working files (about 17 GB for 170,000 people) |
| Memory | 16 GB or more for a national harvest |
| Releases | four a year (the second Wednesday of January, April, July and October) |

A national harvest (170,000 people, about 12 million works) measured on a
laptop with the snapshot on a USB hard disk: about 5 hours of reading, then
about 0.6 ms of processing a work.

## From the harvest to the map

Measured on that national harvest (169,287 people, 11.3 million records of
6.3 million distinct works), on the same laptop (20 cores, 32 GB of memory,
the project on the USB hard disk, the scratch folder on the internal disk),
every step capped at 16 GB of memory with a build budget of 12 GB:

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

About 6 hours from the raw runs to the map. The extraction and the
trajectories went past the budget: their worker pools were sized beside a
parent process assumed to hold 2 GB. A pool is now sized beside what its parent holds, and the keywords'
build and the trajectories no longer hold every text's counts in their parent
(see {doc}`sizes`).

In the app, what takes long to make is made once per version of what it reads
and kept in the project's cache: the texts' view, the copies of a work, the
texts' orders, the map's texts layer and its time windows. Measured on that
project, in a new session of the app (the project on the hard disk): the
people list about 40 s the first time (half of it checking tables written
before cartolex stamped them: tables it writes are not read again to be
checked), the coverage 7 s, the map 9 s, then about a second or less for every
screen; a person's sheet takes a second. The first build of the texts' view
takes 40 s, the texts layer 30 s and the windows 10 s, once. The app held at
most 5.5 GB. The map's bundle is 67 MB, the time windows 37 MB, the texts
layer a sample of 100,000 texts (27 MB).

## Download it

```bash
aws s3 sync "s3://openalex/data/jsonl" "openalex-snapshot/data/jsonl" --no-sign-request
```

A download that stops resumes when the same command runs again: finished
files are skipped. Check it against its manifests before use (Settings › Data
sources says whether the folder is complete, incomplete or not found). Download
a new release into a new folder: a sync over an indexed snapshot downloads every
file again.

## Use it

In the app, give the folder in Settings › Data sources. Each collection that
can read it (a harvest, an institutions' reading, a round of collaborators, a
retry) then offers both ways, the API and the snapshot, each with its time, and
chooses the faster. From the command line, see {doc}`collection` (`collect
snapshot`, `--snapshot`).

On a hard disk:

- **`--jobs 3`**: three worker processes read the snapshot; more make the disk
  seek between files and read slower.
- **`--spill DIR` on an internal SSD**: a harvest keeps what it finds in a
  working folder and reads it back person by person, in no particular order,
  hours of seeking on a hard disk, minutes on an SSD.

## Stopping and going on

A harvest writes what it collected every 2,000 people or 10 minutes and keeps
what its reading of the snapshot found: stopped (Ctrl-C, a service stop, a
computer switched off), it goes on with `--resume`, or « Resume » in the app,
without reading again what it finished. The working folder is removed when the
harvest completes.

## The index (optional)

`cartolex collect snapshot-index SNAPSHOT` cuts the snapshot's files into small
blocks and records which blocks hold each author, institution and DOI (a few
hours once per release, 24 GB). A collection then reads only the blocks it
needs: a laboratory's harvest takes minutes instead of hours. A national
harvest needs most blocks anyway, so the index saves it little; it is worth it
for repeated collections of a few thousand people.
