# Sources: the data model

`sources/` holds what cartolex was given or collected. Six tables describe it,
in `sources/tables/`, as Parquet files. A slot's raw material (the CSV someone
imported, a folder of documents, the records a service returned) stays in
`sources/<slot>/`, so the tables can always be rebuilt from it.

```text
texts ──< text_parts
  │
  └──< authorships >── people ──< affiliations >── organisations ──┐
                          (person_id)                 (org_id)   <─┘ parents
```

Columns marked nullable are optional: a file written before one existed reads it as empty.

Every table is sorted by its key, and every key is a stable string: an id, once
given, never changes and is never reused. Ids are opaque: those cartolex gives
start with `t`, `p` or `o`, and an importer may keep its own. The ids a source
uses (DOI, ORCID, OpenAlex, ROR, HAL, arXiv…) are kept in their own columns.

Dates are kept everywhere: every text has its year, every affiliation its
years, every row its retrieval time. Changes over time are computed from them.

## `texts.parquet`: one row per text

A text is one document: an article, a preprint, a report, a thesis. A work
written by five people is one text with five authorships.

| column | type | meaning |
| --- | --- | --- |
| `text_id` | string | the key |
| `slot` | string | the slot the text came through |
| `position` | int64 | its order inside the slot; with the slot order, it fixes the order a person's texts are read in |
| `year` | int32, nullable | publication year |
| `date` | string, nullable | a finer date, ISO 8601, when known |
| `doc_type` | string | `article`, `preprint`, `report`, `thesis`, `book`, `chapter`, `communication`, or another value a slot declares |
| `title` | string | the display title |
| `doi` | string, nullable | lower case, without the `https://doi.org/` prefix |
| `ids` | map<string, string> | other identifiers by scheme: `openalex`, `hal`, `arxiv`, `pmid`, `pmcid`, `scielo`… |
| `version_of` | string, nullable | the `text_id` of the published version this preprint became |
| `n_authors` | int32 | the number of authors on the text, including those outside the project |
| `source` | string | the finder that brought it in: `openalex`, `orcid`, `hal`, `scielo`, `import`, `folder` |
| `retrieved_at` | timestamp (UTC) | when the record was read |

## `text_parts.parquet`: what a text says

A text has parts, each from a provider: a title, an abstract per language, a
full text. Keeping them apart lets a project choose what feeds the lexicon
(titles and abstracts by default, the whole document for a folder or a corpus
slot), weigh the parts, and fill a missing abstract from another provider.

| column | type | meaning |
| --- | --- | --- |
| `text_id` | string | the text |
| `part` | string | `title`, `abstract`, `body` or `full` (a document read whole, such as a file in a folder) |
| `language` | string | the language detected or declared, `en`, `fr`, `pt`… or `und` |
| `provider` | string | where the words came from: `openalex`, `hal`, `scielo`, `arxiv`, `biorxiv`, `europepmc`, `folder`… |
| `format` | string | `plain`, `jats` or `latex` before cleaning; the content is always plain text after cleaning |
| `content` | large string | the cleaned text |
| `retrieved_at` | timestamp (UTC) | when it was read |

The key is (`text_id`, `part`, `language`, `provider`). Which provider wins when
several give the same part is a parameter (`sources.provider_priority`), so
the choice can change without collecting again.

**Private parts.** `body` and `full` parts are full texts: collected on
request from open-access services, or a user's own documents. They never
leave the project: a shared output (a site, a map bundle, an export, a share)
carries titles and abstracts at most. The rule is on the part, whatever its
provider (`PRIVATE_PARTS` and `shareable_parts()` in
`cartolex.project.tables`); the privacy page and the share step follow it.

**Reading order.** A text is read as its chosen parts in the order title,
abstract, body, each part in every language it has, separated by a blank line
and ending with a newline; `full` stands alone. People are read in `person_id`
order, and each person's texts in slot order, then `position`. A preprint
whose published version is in the tables (its `version_of`) is not read: the
published version is, so that a work counts once. Likewise, texts of a slot
with the same normalised title (at least 25 characters), years at most one
apart and an author in common are one work: only the version of record
(article, review, chapter, conference version, then other types, a preprint
last; else the text with the most words in its parts) is read, by the authors
of every copy.

## `people.parquet`: one row per person record

| column | type | meaning |
| --- | --- | --- |
| `person_id` | string | the key |
| `last_name`, `first_name` | string | as the source gives them |
| `orcid` | string, nullable | |
| `ids` | map<string, list<string>> | other identifiers by scheme; a person may have several OpenAlex records; an idHAL confirmed as a record in `people.csv` (`hal:<idHAL>`) joins `idhal` |
| `source` | string | how the person entered: `import`, `collaborators`, `institution`, `folder` |
| `columns` | map<string, string> | the extra columns of an imported list, kept as text; each becomes a filter, and a person attribute of the engine's index |
| `aliases` | list<struct<last_name, first_name, source>> | every other name form a source gives for this person (marital name, name added by an institution, one half of a double surname, a transliteration); matching tries all of them |
| `retrieved_at` | timestamp (UTC) | |

**Aliases.** Two rows are proposed as one person when they share a strong
identifier (ORCID, an OpenAlex record, an institutional email). Without one,
they are proposed when the first names match, one last name contains or extends
the other, and they share an affiliation or co-authors, with publication years
that follow on rather than overlap. A proposal is listed for review and never
applied on its own: only `merged_into` in `people.csv` joins the rows, and the
name forms of both then go to `aliases`.

Who is mapped, who only feeds the lexicon and who is projected is a decision,
not a source: see `people.csv` in {doc}`decisions`.

## `organisations.parquet`: teams, labs, universities, panels

| column | type | meaning |
| --- | --- | --- |
| `org_id` | string | the key |
| `name` | string | |
| `acronym` | string, nullable | |
| `level` | string, nullable | the project level it belongs to (see `levels` in {doc}`project-json`), when known |
| `parents` | list<string> | the `org_id`s it belongs to; an organisation may have several |
| `ids` | map<string, string> | `ror`, `openalex`, `hal`… |
| `country` | string, nullable | ISO 3166-1 alpha-2 |
| `location` | struct<lat: double, lon: double>, nullable | |
| `source` | string | `ror`, `openalex`, `hal`, `import` |
| `retrieved_at` | timestamp (UTC) | |

## `affiliations.parquet`: who belonged where, when

| column | type | meaning |
| --- | --- | --- |
| `person_id` | string | |
| `org_id` | string | |
| `start_year`, `end_year` | int32, nullable | inclusive; open when unknown |
| `source` | string | `stated` (read on a text), `import`, `orcid`, `hal`, `openalex` |

## `authorships.parquet`: who wrote which text, from where

| column | type | meaning |
| --- | --- | --- |
| `text_id` | string | |
| `person_id` | string | |
| `position` | int32 | the author's rank on the text, from 1 |
| `orgs` | list<string> | the `org_id`s stated on this text for this author |
| `last` | bool, nullable | true for the final author; null when the source truncates the list or the authors are in alphabetical order, where first and last carry no meaning |
| `corresponding` | bool, nullable | as the source flags it; null when it says nothing |

## Which organisation a text counts for

A text counts once for an organisation, however many of its members wrote it.
For each author, the organisations are, in this order:

1. the organisations stated on the text for that author (`authorships.orgs`);
2. otherwise, the author's affiliations whose years contain the text's year;
3. otherwise, the author's current affiliations, and the text is flagged.

Higher levels follow the `parents` links.

## A slot's own folder: the raw runs

The tables are rebuilt from what each slot keeps in `sources/<slot>/raw/`, so
the raw runs and the id registry are part of the format.

```text
sources/<slot>/raw/
  ids.json                  the id registry (cartolex-ids/1)
  <kind>/<run id>.jsonl.gz  one run (cartolex-raw/1)
sources/merges.json         every merge of texts across finders (cartolex-merges/1)
```

**A run** is one job's material, written whole or not at all and never
changed afterwards. It is a gzip-compressed UTF-8 JSON-lines file
(`.jsonl.gz`; the header and the records may be separate gzip members, read as
one stream); a run written by an earlier version is a plain `.jsonl` file,
read the same way. The first line is the **header**, an object with `format` (`cartolex-raw/1`), `kind` (the folder's
name), `run_id` and what the job was asked (its window of years, its people);
each further line is one **record**, an object. Run ids are
`<UTC time to the microsecond>Z-<6 hex>` (`20260928T101200123456Z-3f2a1c`); a
slot's runs of one kind are read in the order of their ids, and a new run's id
always sorts after the earlier ones. A file whose header is not
`cartolex-raw/1` is refused when the tables are rebuilt.

| kind | the job | records | rows built |
| --- | --- | --- | --- |
| `people` | a list imported | one per row (never an e-mail address): names, identifiers, filter columns, organisations with levels and parents | people (`import`), organisations, affiliations |
| `folder` | a folder of documents | one per file read: its path in the folder, digest, person, title, year, language, text | texts (`folder`) with a `full` part, authorships; people created from sub-folders |
| `corpus` | a corpus imported | one per index row: names, unit, attributes, file, year, type, language, text | people, units as organisations, texts with a `full` part, authorships |
| `institution` | people taken from institutions | the units (institution records), then one per person: records, names, ORCID, units with years | people (`institution`), organisations with levels and every parent, affiliations (`stated`) |
| `snowball` | a round of collaborators | one per collaborator: round, record, name, the people they wrote with, path, joint works (with the institutions stated), fit | people (`collaborators`), organisations, affiliations (`stated`) |
| `openalex` | a harvest | author records and works as received, each with the person it was collected for | texts, title and abstract parts, authorships, organisations, affiliations (`stated`, `openalex`) |
| `orcid` | a harvest | the registry's works and records, per person | affiliations (`orcid`) |
| `hal`, `scielo` | the archive, the journal platform | deposits and articles as received, with the people found (by idHAL, by ORCID) and structures | texts, parts per language, authorships, organisations, affiliations |
| `improve` | text providers | the parts each provider found for a text, and the links it stated | parts; a preprint's `version_of` |
| `resolve`, `hal_candidates`, `scielo_candidates`, `institution_proposals` | proposals | candidates with their evidence | none: they wait for a decision |
| `failures` | any finder | one per person whose collection failed: finder, service, status, cause, time | none: the coverage report reads them |

A harvest names its people in its header; a person's latest harvest replaces
their earlier ones. Other kinds add up, the first record of a text, part or
authorship winning and later ones only filling what it lacks, except a text
provider's part, which replaces the same service's part as a finder. A reader
that meets a kind it does not know skips it and says so.

**The id registry**, `ids.json`, is a JSON object:

```json
{"format": "cartolex-ids/1",
 "next": {"organisations": 4, "people": 13, "texts": 57},
 "keys": {"people": {"import:3f1c…": "p000001", "openalex:A999…": "p000001"},
          "organisations": {"openalex:I999…": "o000002"}, "texts": {"doi:10.5555/…": "t000001"}}}
```

`keys` maps every natural key a slot has met (a DOI, a service record, an
imported row) to the id it was given; `next` is the next number of each
table. An id is given once and never again: text keys are looked up in their
slot's registry, people and organisations in every slot's, and a new number
comes after every number any registry gave.

**The merge log**, `sources/merges.json` (`cartolex-merges/1`), is rebuilt with
the tables: `merges` (each with `slot`, the text `kept`, the texts `merged`,
the `rule` and its `evidence`), `versions` (a `preprint` and its `published`
text), `refused` (two `texts`, the `rule` and the `reason`), `conflicts` (a
`field` of a text whose finders disagree: the value `kept` and where it came
from, the `other` and where it came from) and `fills` (parts one text took
from another).

Rebuilding reads a harvest from a **digest** of each run kept in
`cache/sources/` (see {doc}`../dev/collection`): a cache, which gives the same
tables as the runs and can be deleted.

| slot kind | `sources/<slot>/` also holds |
| --- | --- |
| `folder` | nothing else: the documents' text is in the runs, never their path on your computer |
| `corpus` | nothing else: the index's rows and texts are in the runs |

**Merged texts.** A work found by several finders is one text: the tables are
rebuilt with texts merged by DOI, by a shared identifier, then by title and
year for the same person, each field taken from the highest-priority finder
that has it. `sources/merges.json` (`cartolex-merges/1`), rebuilt with the
tables, lists every merge with its rule and evidence, the preprints linked to
their published version, the matches refused and why, and the values finders
gave differently (see `docs/dev/collection.md`).

Overlays and bases use the same layout under their own root, and
`sources/bases/<id>/` holds a copy of each base's map bundle.
