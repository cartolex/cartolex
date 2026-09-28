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
(titles and abstracts by default), weigh the parts, and fill a missing abstract
from another provider.

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

**Reading order.** A text is read as its chosen parts in the order title,
abstract, body, each part in every language it has, separated by a blank line
and ending with a newline; `full` stands alone. People are read in `person_id`
order, and each person's texts in slot order, then `position`.

## `people.parquet`: one row per person record

| column | type | meaning |
| --- | --- | --- |
| `person_id` | string | the key |
| `last_name`, `first_name` | string | as the source gives them |
| `orcid` | string, nullable | |
| `ids` | map<string, list<string>> | other identifiers by scheme; a person may have several OpenAlex records |
| `source` | string | how the person entered: `import`, `collaborators`, `institution`, `folder` |
| `columns` | map<string, string> | the extra columns of an imported list, kept as text; each becomes a filter |
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

## A slot's own folder

| slot kind | `sources/<slot>/` holds |
| --- | --- |
| `collection` | the service records as received, one JSON-lines file per service and run |
| `folder` | the documents, or a pointer to where they are, and the file-to-person matching |
| `corpus` | the imported index and texts, as given |

Overlays and bases use the same layout under their own root, and
`sources/bases/<id>/` holds a copy of each base's map bundle.
