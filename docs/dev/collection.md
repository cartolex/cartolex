# Collection internals

`cartolex.collect` brings people, organisations and texts into a project. This
page describes its machinery: the HTTP layer every finder goes through, the
source writers that turn what was collected into the tables of
{doc}`../format/sources`, and the demo services that let everything run
offline.

## Layering

`cartolex.collect` imports the project format (`cartolex.project`), the
packaged data and two engine helpers (PDF text, language detection); nothing
else of cartolex. The engine never imports it. The demo world and its fake
services are for tests only: collection never imports them and they never
import collection (`tests/test_layering.py`). Settings are passed in; nothing
reads an environment variable below the command line.

## The HTTP layer

One `HttpClient` per collection job sends every request:

```python
from cartolex.collect import CollectSettings, HttpClient

client = HttpClient(
    CollectSettings(contact="me@example.org", api_keys={"openalex": key}),
    cache_dir=project.layout.cache_http,
    mode="normal",                      # or "refresh", "cache_only"
    progress=lambda fraction, message: ...,
    cancel=lambda: stop_event.is_set(),
)
page = client.get_json("openalex", "authors", {"search": name},
                       kind="person_search", sends=["name"])
works = client.get_all("openalex", "works", {"filter": ..., "per_page": 100},
                       kind="works_by_author", sends=["identifier"], paging=OPENALEX_PAGING)
```

| concern | rule |
| --- | --- |
| pacing | a token bucket per (service, host), from the service's declared `RateLimit`; `CollectSettings.rates` overrides it (the demo services use no pacing) |
| timeouts | `Timeouts(connect, read)`, finite and positive, passed on every request; `None` or infinity is refused when the settings are made |
| retries | 429, 500, 502, 503, 504, timeouts, cut connections and unreadable bodies are retried with exponential backoff and jitter (`RetryPolicy`: 5 attempts, 1 s doubling, capped at 60 s, each wait drawn between half and all of it); `Retry-After` (seconds or an HTTP date) is waited at least; a `Retry-After` beyond `max_retry_after` (300 s) stops the job at once |
| errors | `ServiceUnavailable` (retries exhausted), `NotFound` (404, not retried), `RequestRefused` (another 4xx, not retried), `MalformedResponse`, `IncompleteResults`, `CacheMiss`, `Cancelled`; each `ServiceError` names the host, the status and what to do |
| pages | `get_all` follows a cursor until the service gives none or an empty page; the items must add up to the count the first page announced, else `IncompleteResults`: a page cut short never gives a short list |
| unannounced counts | for a list that announces no count (SciELO's offset paging), `CursorPaging(confirm_empty=True)` asks an empty page once more before taking the list as complete |
| bytes | `get_bytes` reads an answer that is not JSON (XML, a PDF, an archive), from a path or a whole `http(s)` link a service gave, with the same pacing, retries, cache (`<key>.bin.gz`, `cartolex-http-cache-bytes/1`) and modes; its `validate` receives the bytes, so a cut file is retried, never cached |
| cancel | `cancel()` is asked before every request and during every wait (waits are cut in steps of a quarter second); `Cancelled` leaves the cache consistent |
| progress | `client.progress(fraction, message)` never goes backwards; long waits report why |
| egress | `client.egress` counts requests per (service, host) and the kinds of data sent (`name`, `identifier`, `DOI`, `institution name`, `contact address`, `API key`), never the values |
| secrets | the OpenAlex key goes in an `Authorization: Bearer` header; `api_key`, `mailto` and other secret parameters never enter a cache key or a cache file |

### The cache

Answers live in `cache/http/<service>/<xx>/<key>.json.gz`, one gzip-compressed
JSON entry each (`cartolex-http-cache/1`: service, kind, method, canonical URL,
parameters, status, the headers kept, retrieval time, body), written
atomically. The key is the SHA-256 of the method, the canonical URL (lower-case
scheme and host, no default port, no trailing slash) and the sorted parameters
that change the answer. A paged list is cached whole, once complete, under its
own key; its pages are not cached one by one, so a cancelled list leaves
nothing partial.

Freshness is decided when an entry is read, from its retrieval time and the
lifetime of its kind of request, so a new lifetime applies to entries already
stored:

| service | kind | lifetime |
| --- | --- | --- |
| OpenAlex | `person_search` | 3 days |
| OpenAlex | `authors_by_orcid`, `works_by_author` | 7 days |
| OpenAlex | `author` | 14 days |
| OpenAlex | `institution_search` | 30 days |
| OpenAlex | `works_by_doi` | 90 days |
| ORCID | `registry_works` | 7 days |
| ORCID | `registry_record` | 30 days |
| HAL | `hal_name_search` | 3 days |
| HAL | `hal_works` | 7 days |
| HAL | `hal_structures`, `hal_record` | 30 days |
| HAL | `hal_file` | 90 days |
| SciELO | `scielo_identifiers` | 1 day |
| SciELO | `scielo_article` | 30 days |
| SciELO | `scielo_fulltext` | 90 days |
| arXiv, bioRxiv/medRxiv, Europe PMC | every kind | 30 days |
| open-access copies (`files`) | `oa_file` | 90 days |

Failures are never cached (a 404 included). `refresh` ignores cached answers
and rewrites them; `cache_only` never reaches the network, uses an entry
whatever its age, and turns a miss into `CacheMiss` naming the kind of request
and its URL. An entry that cannot be read is removed and fetched again.

### Access policies

Each service in `cartolex.collect.services.SERVICES` carries its rate limit and
what its public documentation said when last checked.

| service | checked | what the documentation says | cartolex |
| --- | --- | --- | --- |
| OpenAlex | 2026-09-28 (help pages updated 2026-08-09 to 2026-09-25) | usage-based access: without a key, $0.10 of use a day; a free API key raises it to $1 a day; a lookup by id is free, a list or filter call costs $0.10 per 1,000, a search $1 per 1,000; more than 100 requests a second, or a spent budget, gives 429; the key goes in the `api_key` parameter or an `Authorization: Bearer` header; `per_page` is at most 100; up to 100 values can be joined with `\|` in one filter; cursor paging starts with `cursor=*` and ends when `meta.next_cursor` is null. The current pages no longer mention the `mailto` « polite pool » | 10 requests a second; the key as a bearer header; `mailto` still sent when a contact address is given (harmless, and a courtesy); DOIs fetched 50 at a time; `per_page=100` |
| ORCID public API | 2026-09-28 (usage page of 2022-11-14) | anonymous use: 12 requests a second, bursts of 40, 25,000 reads a day per address (100,000 per registered client); beyond the rate the service answers 503; JSON needs `Accept: application/json` (XML otherwise) | 8 requests a second; `Accept: application/json` |
| arXiv | 2026-09-28 (terms of use, API manual) | no more than one request every three seconds, one connection at a time, for all the user's machines together; the query API answers Atom (`summary` is the abstract, `arxiv:doi` the published version's DOI); `/e-print/<id>` gives the source, one gzipped file or a gzipped tar; e-prints may be retrieved for personal or research use, not served again | one request every 3 s, the query and the sources on one bucket; abstracts asked 50 identifiers at a time |
| HAL | 2026-09-28 (api.archives-ouvertes.fr/docs) | no rate limit, key or terms stated on the API pages; 30 rows by default, at most 10,000; cursor paging needs `sort=docid asc` and `cursorMark=*` and ends when `nextCursorMark` equals the cursor sent; the structure referential gives `parentDocid_i`; metadata are under CC0 (HAL's reuse conditions) | 2 requests a second; 200 rows a page; structures 50 at a time |
| SciELO (ArticleMeta) | 2026-09-28 (the service's documentation and repository) | no key and no rate limit stated; `article/identifiers` lists by collection or journal (ISSN) and processing date, at most 1,000 a request with `offset`, and announces no count; `article` gives one article by PID, JSON or SciELO PS XML (`format=xmlrsps`); the monthly dumps are recommended for whole-collection loads | one request a second; an empty listing page asked twice; a collection without ISSNs listed only up to 5,000 articles |
| bioRxiv and medRxiv | 2026-09-28 (api.biorxiv.org) | `details/<server>/<DOI>/na/json` gives a preprint's versions, abstract, `jatsxml` link and `published` DOI; 100 items a call with a cursor; no key and no rate limit stated | one request a second |
| Europe PMC | 2026-09-28 (the REST documentation page answered 403; the service's support list of 2024-12-11) | the limit applies per address; no key; `search` with `resultType=core` gives `abstractText`, `pmcid` and `isOpenAccess`; `<PMCID>/fullTextXML` the JATS of open-access articles; rapid requests are reported to be throttled with 503 | one request a second |
| open-access copies (`files`) | 2026-09-28 (OpenAlex help, pages of 2026-08-11 and 2026-09-18) | OpenAlex gives `best_oa_location.pdf_url` and each location's `pdf_url`, on the host that holds the copy; its own cached PDFs (content.openalex.org) need a key and are metered | one request a second per host; OpenAlex's cached PDFs are not used |

## The source writers

Collection never writes a table row directly. Each finder stores what it
received in the slot's raw folder, and `rebuild_sources` rebuilds the six
tables from every slot's raw records:

```text
sources/<slot>/raw/
  ids.json                 the slot's id registry (cartolex-ids/1)
  <kind>/<run id>.jsonl    one run: a header line, then one record per line (cartolex-raw/1)
```

- **Runs.** `RawWriter(layout, slot, kind, header)` writes a run to a
  temporary file and moves it into place when closed: a failed or cancelled
  job leaves no partial run. Run ids are `<UTC time>-<6 hex>`; runs are read in
  that order.
- **Ids.** `IdRegistry` gives `t000001`, `p000001`, `o000001` from natural keys
  (a DOI, a service record, an imported row) and remembers every key forever,
  so an id is never given twice and a new collection never renumbers
  anything. Text keys are looked up in their slot's registry only (a text
  belongs to one slot); people and organisations are shared by all slots. A
  new number comes after every number any registry gave, skipping ids already
  in the tables.
- **Readers.** A reader turns the runs of one kind of one slot into rows of a
  `SourceBuilder` (`person`, `organisation`, `affiliation`, `text`, `part`,
  `authorship`). `default_readers()` lists cartolex's; a finder adds its own
  kind there.
- **Merging.** A text reached through several keys keeps its first values and
  gains the ones it lacked; affiliations of the same person, organisation and
  source join into one span of years. A slot's texts are numbered (`position`)
  by year, then date, then id.
- **Kept rows.** Rows whose ids no registry gave (tables written by another
  tool, the demo project) are kept as they are; the others are rebuilt. The
  same raw records and the same kept rows give the same bytes.
- **Aliases.** A person merged into another (`merged_into` in
  `decisions/people.csv`) gives their name form to the other's `aliases`.

Text hygiene (`cartolex.collect.text`) happens at the boundary: NFC, lone
surrogates and control characters removed, JATS and HTML stripped (block tags
become paragraph breaks, a leading « Abstract » label goes), inverted-index
abstracts rebuilt in word order, and each part's language detected (`und`
when unsure).

## The demo services

`cartolex.demo.services` serves, on `127.0.0.1` and a free port, the subset of
each API cartolex uses, from a demo world:

```python
from cartolex.collect import HttpClient, local_settings
from cartolex.demo import generate
from cartolex.demo.services import DemoServices

with DemoServices(generate("S", 0)) as services:
    client = HttpClient(local_settings(services.endpoints()))
    services.faults.add("status", service="openalex", status=429, retry_after="2", times=1)
```

```bash
python -m cartolex.demo services --size S --seed 0 --people-list people.csv
```

The command serves until Ctrl-C and prints each service's base URL;
`--people-list` writes the world's people as a list to import, `--list-only`
exits after writing it.

| service | routes served |
| --- | --- |
| OpenAlex | `authors` (search, filters `id`, `orcid`, `affiliations.institution.id`, `last_known_institutions.id`, `display_name.search`; page or cursor paging), `authors/<id>`, `works` (filters `author.id`, `doi` with up to 100 values, `publication_year`, `from_publication_date`, `to_publication_date`, `type`, `openalex`), `works/<id>`, `institutions` (search, filters), `institutions/<id>` |
| ORCID | `v3.0/<orcid>/works`, `v3.0/<orcid>/record`; XML unless JSON is asked for; 404 for an unknown identifier |
| HAL | `search/` (one clause on `authIdHal_s`, `authFullName_t`, `halId_s`, `doiId_s`, `arxivId_s` or `docid`; an `fq` range on `producedDateY_i`; `fl`; `rows` up to 10,000; `cursorMark` with `sort=docid asc`), `ref/structure/` (`q=docid:(…)`, parents in `parentDocid_i`), `files/<halId>/document` (a PDF) |
| SciELO | `api/v1/article/identifiers/` (`collection`, `issn`, `from`, `until`, `offset`, `limit` up to 1,000), `api/v1/article/` (`code`, `collection`; JSON, or `format=xmlrsps`) |
| arXiv | `api/query?id_list=…` (Atom), `e-print/<id>` (one gzipped `.tex`, or a gzipped tar with an `\input` file and a figure), `pdf/<id>` |
| bioRxiv and medRxiv | `details/<server>/<DOI>/na/json`, `content/<DOI>v1.source.xml` (JATS) |
| Europe PMC | `search?query=DOI:"…"&resultType=core&format=json`, `<PMCID>/fullTextXML` (open-access articles only) |
| open-access files | `oa/<work>.pdf`, the copies OpenAlex's `best_oa_location` links to |

A service still to be written can be a `StubService` (every request answers
501); `register_service(name, factory)` replaces any service.

Search is literal on purpose: every word of the query must be a word of one of
the record's names (a one-letter word matches an initial), case ignored,
accents kept, so the resolver's name variants are exercised as in real use.

**The bibliographic layer** (`build_bibliography(world, seed)`) is derived from
the world with random streams of its own; the world never changes. It holds
institution records (one per institution, one lab-level record per group whose
parent is its institution, a few outside ones), author records, index works
(the world's works whose sources include the index, with their authorships as
the index states them: institutions, ORCIDs, outside co-authors), outside
works, the registry (declared works and employments) and the truth: for each
world person, the name an imported list shows and the records a careful person
confirms. Eight cohort people carry the special cases: `homonym`, `trap` (a
homonym whose record cites the person's own lab-level record), `split` (two
records, one work indexed twice, the copy without its DOI), `mixed` (one record
holding an outside homonym's works and the person's ORCID), `compound`,
`diacritics`, `moved` (an earlier outside institution before a given year) and
`none`. Identifiers stay in the demo blocks: `A999…`, `W999…`, `I999…`,
`T999…`, `S999…` (numbers beyond those in use), ORCIDs in `0000-0000-…`, DOIs
under `10.5555`.

**The sources layer** (`sources_layer(bibliography)`, built once per
bibliography) says what the archive, the journal platform and the full-text
services hold, again from random streams of its own:

- HAL: a deposit for every world work whose sources include `hal`, with its
  authors (idHAL when the person has one, some ORCIDs), their structures (a
  lab-level structure whose parent is its institution, sometimes the
  institution alone), titles and abstracts in the work's language and
  sometimes a translation, a PDF file for most; a fifth of the deposits of
  works with a DOI lack it, and half of those carry the deposit's year, one
  later; the **preprint** of some published articles, a year earlier, with an
  arXiv identifier; a few deposits of an outside homonym;
- SciELO, in a world written in Portuguese: the Portuguese articles and some
  English articles of the groups that write in Portuguese, with titles and
  abstracts in two or three languages (translations written anew from the
  same plan), author keywords, most authors' ORCIDs and, with bodies, the full
  text in each language;
- arXiv (world preprints deposited in HAL, and the preprints above, with the
  DOI of their published version), bioRxiv (the other preprints about the
  natural world), Europe PMC (half of the published articles about the natural
  world, most open, with JATS) and the open-access copies OpenAlex links to.

Its identifiers are in blocks nobody uses: HAL ids `hal-097…` to `hal-099…`,
document ids from 99,000,000, structures from 9,900,000, arXiv ids `99MM.NNNNN`
(a month new-style ids never had), PMIDs from 99,000,000, PMCIDs `PMC99…`,
journal ISSNs whose check character is wrong. JATS, LaTeX and PDF renderings
come from `cartolex.demo.services.render`. A link in an answer is written
`demo-base://<service>/…`, and the server replaces the prefix with its own
address.

**Faults** (`services.faults.add(kind, service=…, path=…, times=…, skip=…)`):
`status` (with `status` and `retry_after`), `hang` (answers after `delay`
seconds), `drop` (closes the connection), `malformed` (a body cut in half),
`cut_page` (half of a cursor page missing) and `early_end` (a null cursor too
early; for HAL and SciELO, whose pages have another shape, an empty page, each
service saying how with a `fault_page` method). `services.requests` lists every
request received, with its headers.

## Plug-in points for new finders

- declare the service in `SERVICES` (rate, lifetimes, policy with its date);
- fetch through `HttpClient.get_json` / `get_all`, with a `kind` and the
  `sends` of every request;
- store what was received with `RawWriter(layout, slot, "<kind>", header)` and
  add a reader for `<kind>` to `default_readers()`;
- serve the API subset from the bibliographic layer with a service object and
  `register_service`, replacing the stub.

## The HAL finder

`collect_hal(client, layout, slot, people, *, window, name_fallback=True)`
(`cartolex.collect.hal`) searches each person by **idHAL**
(`q=authIdHal_s:"…"`, `fq=producedDateY_i:[first TO last]`, `sort=docid asc`,
`cursorMark`, 200 rows a page). A person's idHAL is read from `ids["idhal"]`
in `people.parquet` (`people_refs(layout)` builds the list). Each deposit is
stored as received, with the person it was found for, in
`sources/<slot>/raw/hal/`; the structures the person stated on it, and their
parents level by level (up to four), are asked from the structure referential
and stored in the same run. A list cut short is asked once more; a person
whose search fails is reported and the job goes on; after three failures in a
row the job stops asking and keeps what it found.

A person **without an idHAL** is searched by name (`authFullName_t`), and the
author forms that match are only **proposed**: a `hal_candidates` run lists,
per person, each form (its idHAL when HAL has one, its name, its deposits,
structures and years). No row is built from it; confirming a form means giving
the person that idHAL.

`read_hal_runs` builds a text per deposit (key `hal:<halId>`, source `hal`):
the document type (`ART` article, `COMM` and `POSTER` communication, `OUV`
book, `COUV` chapter, `THESE` thesis, `REPORT` report, `UNDEFINED` and
`PREPRINT` preprint, others `other`; images, videos, sound, maps, software and
patents are skipped), the year (`producedDateY_i`), the **DOI link**
(`doiId_s`), the arXiv and PubMed identifiers, the number of authors; title
and abstract parts per language (`<lang>_title_s`, `<lang>_abstract_s`,
provider `hal`); an organisation per structure (key `hal-structure:<docid>`,
source `hal`, with its parents); and the person's authorship at their rank,
with the structures stated for them, and a `stated` affiliation for the
deposit's year.

## The SciELO finder

`collect_scielo(client, layout, slot, people, *, window, collection,
issns=(), codes=())` (`cartolex.collect.scielo`) uses the **ArticleMeta API**
rather than the OAI-PMH server: one request gives an article with its titles
and abstracts in every language (`v12`, `v83`, each with its language), its
authors with their ORCID when the journal gave it (`v10`), its DOI and
publication date, in JSON; the same endpoint gives the full text as SciELO PS
(JATS). OAI-PMH's Dublin Core carries no author identifier, tags languages
unevenly, and has no full text. Neither route searches by author, so the
finder lists the journals it is given (ISSNs, or the whole collection up to
5,000 articles), processed since the year before the window, reads each
article, keeps those of the window that show a person's ORCID, and proposes
the authors who only match a person's name (`scielo_candidates`). Author
keywords (`v85`) are never read.

`read_scielo_runs` builds a text per article (key `scielo:<collection>:<PID>`,
source `scielo`, `ids["scielo"] = "<collection>:<PID>"`), with title and
abstract parts in every language (provider `scielo`) and the authorships of
the people found by ORCID.

## Merging works across finders

When the tables are rebuilt, `merge_texts` (`cartolex.collect.merge`) merges,
within each slot, the texts that are one work, by three rules in this order:

| rule | when | listed as |
| --- | --- | --- |
| same DOI | two texts carry the same DOI (lower case, no `https://doi.org/`) | `doi`, or `hal_doi` when one side is a HAL deposit's DOI link |
| source link | the same identifier under the same scheme (`hal`, `arxiv`, `pmid`, `pmcid`, `scielo`, `openalex`) | `link:<scheme>` |
| title and year, per person | two texts with an author in common, the same normalised title (at least three words) and years at most one apart | `title_year` |

Every record of a text counts (its own DOI, title, year and people), not only
the values the text kept. A merge is refused, and listed with its reason, when
the two sides carry different DOIs or, for the third rule, types that cannot
be one work (a thesis and an article); records that reached one text through a
shared finder key are listed as `key`.

**Fields.** Each field of the merged text takes the value of the
highest-priority finder that has one: `import`, `folder`, `corpus`,
`openalex`, `scielo`, `hal`, `orcid`, then the others by name
(`rebuild_sources(..., finder_priority=…)` changes it; SciELO, a journal
platform, states the version of record, while a HAL date is sometimes the
deposit's). Ties go to the latest record. Identifiers are united. A value
another finder gives differently is kept as a **conflict**. The text keeps the
smallest id of the group; parts and authorships move to it (a clash keeps the
most recent part and the smallest rank, and is listed).

**Preprints.** A preprint and a published version (article, communication,
chapter or report) are never merged: when they meet by the third rule, or a
provider states the published DOI (arXiv's `arxiv:doi`, bioRxiv's
`published`), the preprint's `version_of` names the published text. A preprint
that meets several published texts stays unlinked. The build reads only the
published version (`corpus.assemble` skips a text whose `version_of` is in the
tables): it is the version of record, with its final title, year and DOI, and
reading both would count one work twice in its authors' texts. A published
text without an abstract gets its preprint's abstract parts.

Everything is written to `sources/merges.json` (`cartolex-merges/1`): merges
with their rule and evidence, version links, refusals, conflicts and filled
parts; `RebuildReport.merges` counts them. `merge_works(records)` is the pure
function behind it: the same records in any order give the same result, and
merging a merged result changes nothing (both are tested on random cases).

## Text providers

`improve_texts(client, layout, config, *, slots=None, text_ids=None,
providers=None, abstracts=True, full_text=False, work_dir=None)`
(`cartolex.collect.providers`) improves texts already in the tables:

- **a missing abstract**, from the first provider that has it, in the order
  SciELO, HAL, Europe PMC, bioRxiv/medRxiv, arXiv, OpenAlex (its inverted
  index);
- **a full text, on request only** (`full_text=True`): structured texts first,
  JATS from Europe PMC (open-access articles), bioRxiv/medRxiv and SciELO, then
  the LaTeX source from arXiv, then PDF files, HAL's main file and the
  open-access copy OpenAlex links to.

| provider | applies to | sends | full text |
| --- | --- | --- | --- |
| `arxiv` | an arXiv identifier | the identifier | the LaTeX source (`abstract`, `body`, format `latex`); a PDF when the e-print has no source |
| `biorxiv` | a preprint whose DOI has the servers' prefix | the DOI | the JATS the details link to (`abstract`, `body`, `jats`) |
| `europepmc` | a DOI (or a PMID, a PMCID) | the DOI, then the PMCID | `fullTextXML` of an open-access article (`jats`) |
| `hal` | a HAL identifier, else a DOI | the identifier or the DOI | the main file, a PDF (`full`, `plain`) |
| `scielo` | a SciELO identifier | the PID | SciELO PS: an `abstract` and a `body` part per language (`jats`) |
| `openalex` | an OpenAlex identifier, else a DOI | the identifier or the DOI, then the copy's address to its host | the PDF of `best_oa_location`, then of the other locations (`full`, `plain`) |

JATS is read with the sections kept as paragraphs (a section's title, its
paragraphs, figure and table captions; references, formulas and citation
markers dropped; translations in `sub-article` give parts in their language);
a document that declares entities is refused. LaTeX is read from one gzipped
file or a tar, `\input` and `\include` files inlined, accent macros and escapes
decoded, maths, labels, citations and comments dropped. A PDF goes through
`cartolex.lexicon.pdf_text`, each file on its own in a temporary file under
`cache/tmp/`: a file that cannot be read is reported for its text only. A
link a service gives is followed only when it is `http(s)` and, unless the
job's services are local, not a private address.

Providers only **store** parts, with their `provider` and `format`; which parts
feed the lexicon and their weights are build parameters. What each one found
goes to `sources/<slot>/raw/improve/` (with the links it stated), and
`read_improve_runs` adds it when the tables are rebuilt. `body` and `full`
parts are **private** (`PRIVATE_PARTS`, `shareable_parts` in
`cartolex.project.tables`): see {doc}`../format/sources`.

## Functions for the command line

The finders and providers of this page are plain functions; the command line
and the application call them with one `HttpClient` per job, which carries
the progress callback, the cancel check and the egress record:

| function | does | returns |
| --- | --- | --- |
| `people_refs(layout, person_ids=None)` | the people to look for, with their ORCID and idHAL | `list[PersonRef]` |
| `collect_hal(client, layout, slot, people, window=…, name_fallback=True)` | HAL deposits by idHAL; candidates by name | `FinderReport` (works per person, candidates, failures, skipped, runs) |
| `collect_scielo(client, layout, slot, people, window=…, collection=…, issns=(), codes=())` | SciELO articles by ORCID; candidates by name | `FinderReport` |
| `improve_texts(client, layout, config, full_text=False, providers=None, …)` | missing abstracts; full texts on request | `ImproveReport` (per provider: asked, improved, none, failed; one outcome per text) |
| `rebuild_sources(layout, config, finder_priority=None)` | the tables, merged | `RebuildReport` (rows, merges per rule) |
| `provider_egress(providers=None)` | what each provider sends, to which service | a list for the privacy summary |

A cancel raises `Cancelled` and leaves no partial run; the HTTP cache keeps
every complete answer, so collecting again is quick. `client.egress.summary()`
lists the hosts contacted and the kinds of data sent.
