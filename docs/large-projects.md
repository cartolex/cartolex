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
