# The project format, `cartolex-project/1`

A cartolex **project** is a folder. It holds what cartolex was given (sources),
what people decided (decisions), what cartolex computed (derived), what is
costly to compute again (cache), and what it hands out (outputs). Anything that
can open the folder can rebuild every result from the first two.

This page is the contract. The pages below it describe each part:

```{toctree}
:maxdepth: 1

project-json
sources
decisions
derived
schemas
```

## The folder

```text
<project>/
  project.json          identity, languages, slots, overlays and bases; the format version
  .lock                 present while an application has the project open
  sources/              what cartolex was given or collected
    tables/             the data model: texts, their parts, people, organisations,
                        affiliations, authorships (Parquet)
    <slot>/             a slot's raw material: imported files, collected records
  decisions/            what people decided; small, readable, keyed by stable names
    history/            every earlier version of every decision file
  derived/              what cartolex computed; one folder per stage, each with its run.json
  cache/                costly to recompute: AI answers, parsed texts, service responses
  outputs/              offline atlases, figures, tables, map bundles (default root)
  logs/                 one record per job
```

## Rules

1. **Lifecycle decides what is safe.** `derived/` can always be deleted: a build
   recomputes it. `cache/` can be deleted, at a price (AI answers cost money,
   parsing and collection cost time). `sources/` and `decisions/` are the
   project; the backup set is `project.json`, `sources/`, `decisions/` and
   `cache/ai/`.
2. **Decisions are keyed by stable names.** A keyword is named by its text, a
   person by their person id, an organisation by its organisation id, never by a
   row number. A re-extraction or a re-collection therefore never shifts a
   decision onto the wrong thing.
3. **Provenance travels with results.** Every stage folder in `derived/` holds a
   `run.json`: its inputs' fingerprints, its effective parameters, the runs it
   read and the code that ran. Whether a result is up to date is decided from
   these records, never from file dates, which copies and backups do not keep.
4. **Nothing stored runs code.** No pickle, no joblib: JSON, CSV, Parquet, and
   NumPy arrays read with `allow_pickle=False`.
5. **No absolute path inside a file.** Every stored path is relative to the file
   that stores it, except the roots a host application declares on purpose
   (an overlay or a base that lives outside the project, an output root).
6. **One writer per folder.** cartolex writes only inside the project and the
   roots declared in `project.json`. A host application keeps its own files
   outside the project folder.
7. **Every file names its format.** JSON files carry a `format` key such as
   `cartolex-params/1`; tables have a fixed schema, listed in {doc}`sources` and
   {doc}`decisions`.

## Opening a project: the lock

An application that opens a project for writing creates `.lock` with its process
id, host name, application name and start time, and removes it when it closes.
The file is created atomically; if it already exists, opening fails and names
the holder. A lock whose process no longer runs on the same host is reported as
**stale**, with the command that removes it; it is never removed silently, and
a lock held on another host is never judged stale. Reading a project needs no
lock.

## Writing a file: atomic writes

Every file cartolex writes is written to a temporary file in the same folder,
flushed to disk, then renamed over the target. A reader sees the old file or the
new one, never a part of either. A stage writes into a staging folder and swaps
it into place when it succeeds ({doc}`derived`), so a killed build leaves the
previous results intact.

Decision files are written with the fingerprint (SHA-256) of the version the
writer read. If the file changed in between, the write is refused and nothing
is lost: the writer reloads and merges. Every accepted write first moves the
previous version into `decisions/history/`.

## Versions and upgrades

`project.json` holds `"format": "cartolex-project/1"`; every JSON file carries
its own format id. Within one major version, a newer cartolex may add optional
keys and optional table columns; an older reader ignores what it does not know,
and a writer keeps unknown keys when it rewrites a file. A new major version is
never read silently: opening such a project fails with the version found and
the version expected, and `cartolex project upgrade` converts a project after
copying the files it changes into `decisions/history/upgrade-<date>/`.

## Sizes

The format is built for projects of 10⁵ people and more. Bulk tables (texts,
their parts, people, organisations, affiliations, authorships) are Parquet
files that a reader streams by row group; large matrices are sparse NumPy
arrays; only decisions are CSV and JSON, because people read and edit them.
