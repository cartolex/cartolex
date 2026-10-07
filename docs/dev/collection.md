# Collection internals

`cartolex.collect` brings people, organisations and texts into a project. This
page describes its machinery: the HTTP layer every finder goes through, the
source writers that turn what was collected into the tables of
{doc}`../format/sources`, and the demo services that let everything run
offline. The user's view is in {doc}`../collection`, and what leaves the
computer in {doc}`../privacy`.

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
that change the answer. A service on this computer (the demo services) has the
host `loopback` whatever its port, so its answers survive a restart on another
port; the demo services put the world they serve in the first segment of their
URLs, so answers of one demo world are never taken for another's. A paged list is cached whole, once complete, under its
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
| OpenAlex | `institution_search`, `institution`, `institution_units` | 30 days |
| OpenAlex | `works_by_institution` | 7 days |
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
| OpenAlex snapshot | 2026-09-28 (help pages « Snapshot » and « Snapshot data format », 2026-09-24) | CC0; the public snapshot is released quarterly (the second Wednesday of January, April, July and October), about 626 million records, 745 GB of gzip JSON lines (a Parquet copy beside it) in September 2026; `s3://openalex/data/jsonl/<entity>/updated_date=YYYY-MM-DD/part_NNNN.gz`, a record sitting in the partition of the date it last changed; a `manifest.json` per entity (`date`, `record_count`, `content_length`, `files` with `url` and `meta`) and one per format; `works/deleted_ids.csv.gz` (`work_id,deleted_date`, since the 2026-09-23 release); records in the API's shape, with `is_xpac` and `has_content`, without `content_urls`; downloaded with `aws s3 sync … --no-sign-request`, no account; snapshots before 2026 under `legacy-data/`, laid out `data/<entity>/…` | streamed part by part, line by line, a line parsed only when its bytes may match; deleted works dropped; the newest partition wins when a copy holds a record twice; both layouts read |
| open-access copies (`files`) | 2026-09-28 (OpenAlex help, pages of 2026-08-11 and 2026-09-18) | OpenAlex gives `best_oa_location.pdf_url` and each location's `pdf_url`, on the host that holds the copy; its own cached PDFs (content.openalex.org) need a key and are metered | one request a second per host; OpenAlex's cached PDFs are not used |

## The source writers

Collection never writes a table row directly. Each finder stores what it
received in the slot's raw folder, and `rebuild_sources` rebuilds the six
tables from every slot's raw records:

```text
sources/<slot>/raw/
  ids.parquet              the slot's id registry (cartolex-ids/2)
  <kind>/<run id>.jsonl.gz one run, gzip-compressed: a header line, then one record per line (cartolex-raw/1)
```

- **Runs.** `RawWriter(layout, slot, kind, header)` writes a run to a
  temporary file and moves it into place when closed: a failed or cancelled
  job leaves no partial run. Run ids are `<UTC time to the microsecond>-<6 hex>`,
  and a new run's id always sorts after the slot's earlier runs of its kind, even
  when the clock repeats itself or goes back; runs are read in that order.
  The records are compressed as they are written (a large harvest's works take
  about a tenth of their plain size), and `close` writes the header as a gzip
  member of its own before the records' member, copied as it is. `read_runs`
  and `open_run` read compressed runs and the plain `.jsonl` runs of earlier
  versions alike (`run_files` lists both).
- **Ids.** `IdRegistry` gives `t000001`, `p000001`, `o000001` from natural keys
  (a DOI, a service record, an imported row) and remembers every key forever,
  so an id is never given twice and a new collection never renumbers
  anything. Text keys are looked up in their slot's registry only (a text
  belongs to one slot); people and organisations are shared by all slots. A
  new number comes after every number any registry gave, skipping ids already
  in the tables. Its keys live in an SQLite table while it is open, each
  table's loaded when first needed (a registry of tens of millions of keys
  costs disk, not memory), and `save` writes `ids.parquet`.
- **Readers.** A reader turns the runs of one kind of one slot into rows of a
  `SourceBuilder` (`person`, `organisation`, `affiliation`, `text`, `part`,
  `authorship`). `default_readers()` lists cartolex's; a finder adds its own
  kind there.
- **Merging.** A text reached through several keys keeps its first values and
  gains the ones it lacked; affiliations of the same person, organisation and
  source join into one span of years. A slot's texts are numbered (`position`)
  by year, then date, then id.
- **A process of its own.** Above 256 MB of raw records
  (`ISOLATE_BYTES`, with the default readers) a rebuild runs in a child process,
  which gives back its report or raises what it raised: the process that asked
  (the app, after a collection) keeps its memory as it was. `isolate=` forces
  either way.
- **Memory.** What grows with the records lives in a scratch database while
  the readers run (`cartolex.collect.workstore.WorkStore`, SQLite, in a folder
  of `rebuild_sources(..., scratch=…)`, by default the project's `cache/`,
  removed at the end; a killed rebuild's folder is removed by the next one): every record of each text, the texts' parts (compressed),
  the authorships, the affiliations, the registry's keys. A part or an
  authorship stated twice keeps its first statement (`INSERT OR IGNORE`), an
  affiliation's span widens (`ON CONFLICT … DO UPDATE`). People and
  organisations stay in memory. A harvest is read a person at a time, each work
  reduced as it is read to the project people on it and their institutions
  (`harvest._placed_work`: a work can have thousands of authors), and a heavy
  run (64 MB and more) is read by worker processes in blocks of lines, given
  back in order (`cartolex.scale.ordered_map`). The texts are then merged
  group by group (`merge.merge_texts`, below), and the tables written a row
  group at a time (`project.tables.SourceTableWriter`): memory holds the people,
  the organisations and one person's or one group's rows, whatever the number
  of records. Rebuilt this way, the tables are the same as when every row was
  held in memory (checked on samples of a real collection of 50,000 and 200,000
  records).
- **Kept rows.** Rows whose ids no registry gave (tables written by another
  tool, the demo project) are kept as they are; the others are rebuilt. The
  same raw records and the same kept rows give the same bytes.
- **Aliases.** A person merged into another (`merged_into` in
  `decisions/people.csv`) gives their name form to the other's `aliases`.

The kinds of raw runs cartolex writes, and what their readers build:

| kind | written by | header | records | rows built |
| --- | --- | --- | --- | --- |
| `people` | `import_people` | the source's name, the mapping, rows read | one per list row (no e-mail): names, identifiers, filters, organisations with levels and parents | people (`import`), organisations, affiliations (`import`, no years) |
| `folder` | `import_folder` | the folder's name (no path), files seen | one per file read: its path, digest, person, title, year, language, text | texts (`folder`), one `full` part, one authorship; people created from sub-folders (`folder`) |
| `corpus` | `import_corpus` | the index's name, the unit level | one per index row: names, unit, attributes, file, year, type, language, text | people (`import`), units as organisations, texts with a `full` part, authorships in row order |
| `openalex` | `harvest` | the year window; per person: records, names, ORCID, declared DOIs | author records and works as received, with the person and how each work was reached | texts, title and abstract parts, authorships, organisations, affiliations (`stated` per work, `openalex` from the records' years) |
| `orcid` | `harvest` | per person: ORCID iDs | the registry's works and records as received | affiliations (`orcid`) to organisations of the same name the person is already affiliated with |
| `resolve` | `resolve` | the threshold, whether acceptance was automatic | each person's candidates with their evidence and scores | none (kept for the record) |
| `institution_proposals` | `propose_people` | the institutions, the years, the least works, the levels, the source | the institution records of the tree, then every author signed there: record, name, ORCID, works with year and units | none (a proposal) |
| `institution` | `take_people` | the proposal's run, the levels | the units needed (the institutions above the stated ones included), then one per person taken: records, names, ORCID, aliases, units with years | organisations (`openalex`, levels, every parent), people (`institution`), affiliations (`stated`) |
| `snowball` | `snowball` | the seeds and their records, years, cap, the author threshold, the rounds, the cut, the fit measure, the source | one per collaborator: round, record, name, ORCID, parents with joint works, path, joint works with the institutions stated, fit | people (`collaborators`), organisations, affiliations (`stated`) |
| `failures` | any finder | the finder | one per person whose collection failed: finder, service, host, status, error, cause, time | none (the coverage report reads them) |

A harvest writes one `openalex` and one `orcid` run with the same run id. A
person's latest harvest replaces their earlier ones: the readers take, for each
person, the lines of the latest run that names them. Within one rebuild, the
first record of a text, a part or an authorship wins and later ones only fill
what it lacks; readers order their records so that this is the right one (a
work with a DOI before a copy without one; the newest folder or corpus run
first).

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
| OpenAlex | `authors` (search, filters `id`, `orcid`, `affiliations.institution.id`, `last_known_institutions.id`, `display_name.search`; page or cursor paging), `authors/<id>`, `works` (filters `author.id`, `doi` with up to 100 values, `publication_year`, `from_publication_date`, `to_publication_date`, `type`, `openalex`, `authorships.institutions.id`, `authorships.institutions.lineage`), `works/<id>`, `institutions` (search, filters `id`, `lineage` (an institution and every unit below it), `ror`, `type`, `display_name.search`), `institutions/<id>`, `institutions/ror:<ror>` |
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
institution records (one per institution, with a ROR id whose check number is
wrong, one lab-level record per group whose parent is its institution, one of
them a **joint unit** with a second parent, a few outside ones), author records,
index works (the world's works whose sources include the index, with their
authorships as the index states them: institutions, ORCIDs, outside
co-authors), outside works (homonyms, the outside co-authors' own works on other
themes, and a **large collaboration** of 30 authors, two of them cohort people),
the registry (declared works and employments) and the truth: for each
world person, the name an imported list shows and the records a careful person
confirms. Eight cohort people carry the special cases: `homonym`, `trap` (a
homonym whose record cites the person's own lab-level record), `split` (two
records, one work indexed twice, the copy without its DOI), `mixed` (one record
holding an outside homonym's works and the person's ORCID), `compound`,
`diacritics`, `moved` (an earlier outside institution before a given year) and
`none`. Identifiers stay in the demo blocks: `A999…`, `W999…`, `I999…`,
`T999…`, `S999…` (numbers beyond those in use), ORCIDs in `0000-0000-…`, DOIs
under `10.5555`, ROR ids `0zz…` with a wrong check number. `layer_strings(bib)`
lists every name the layer invents; `python -m cartolex.demo create --layer`
writes them for the vocabulary scan of `tools/check.py`.

**The mini snapshot** (`write_snapshot(bib, folder)`, or `python -m cartolex.demo
snapshot --size S --out DIR`) writes the works, authors and institutions the
demo OpenAlex serves in the snapshot's layout (`data/jsonl/<entity>/updated_date=…/part_NNNN.gz`,
manifests, the works' deletion log), record for record the API's JSON plus the
fields the snapshot adds (`is_xpac`, `has_content`), over two update dates; a
withdrawn work sits in the older partition and in the deletion log.

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

## Resolution and harvest

`cartolex.collect.names` gives the normal form of a name (folded, particles set
aside), the search variants of a person and the similarity of two names;
`cartolex.collect.openalex` and `cartolex.collect.orcid` the requests and how
their answers are read; `cartolex.collect.resolve` the candidates and their
scores (see {doc}`../collection` for the rules and the points); and
`cartolex.collect.harvest` the harvest and its readers. The linking rule of a
harvested work: a project person is on it at the rank where one of their
confirmed OpenAlex records appears, or, when the work's DOI is one they
declared in the registry, at the rank whose ORCID is theirs or whose name
matches theirs (similarity 0.75 or more). `last` is null when the list is
truncated or when four authors or more are in alphabetical order;
`corresponding` is null when no author is flagged.

`cartolex.collect.privacy.plan_collection` estimates the requests of a planned
resolution or harvest per host and the OpenAlex cost at its published prices,
and lists what is sent and what never is; `record_job` writes the job's record
(hosts, kinds of data, counts, and the phases a search of collaborators told of)
to `logs/jobs/collect-<action>-<run id>.jsonl`.

The command line (`cartolex.cli_collect`) reads the contact address and the
OpenAlex key from `--contact` / `$CARTOLEX_CONTACT` and `--openalex-key` /
`$OPENALEX_API_KEY`, else the key saved in the app's settings
(`cartolex.app.machine.MachineKeys` in `--data-dir` or the default folder), and
passes them in as settings; `--services demo` starts the demo services of
`--world SIZE:SEED` for the command. In the app, `ServiceCollection.settings`
adds the key saved on this computer each time it is read, for a service the
launch gave none (`use_saved_keys`, bound by the app's runtime unless it is
hosted).

## Sources of OpenAlex records: the API or the snapshot

The finders ask OpenAlex through an `OpenAlexSource`
(`cartolex.collect.openalex`): `author`, `works_by_authors`, `works_by_dois`,
`institution` (an OpenAlex id or `ror:<id>`), `search_institutions`,
`institution_units` (an institution and every record whose `lineage` holds it:
`institutions?filter=lineage:`), `works_by_institutions`
(`works?filter=authorships.institutions.lineage:`) and `works_of_authors` (the
works of many records, asked 50 records at a time). `OpenAlexApi(client)`
sends the requests; `SnapshotSource(Snapshot(folder))`
(`cartolex.collect.snapshot`) answers them from a downloaded snapshot. Its
`prefetch(author_ids, dois)` reads everything a harvest will ask in one pass
over the authors and one over the works; the institutions are read once;
other questions cost one pass each. `Snapshot.scan_into(query, store)` puts
what a pass finds in a `RecordStore` instead of memory: each record compressed
on its own and appended to a temporary file (the source's `spill` folder, the
project's `cache/snapshot/` from the command line), its place indexed by id, a
later copy replacing the earlier one. The source keeps indexes only: the works
by the authors and DOIs asked for (a person's question is a lookup, not a scan),
the institutions' names, ROR ids and lineages. A `Query` may keep only some
fields (`select`): the works with `WORK_FIELDS`, as the API is asked for them,
those of an institution's reading with the fields it folds. That reading comes
in pages of 100 from its own store, the cursor `snapshot:<works before>` resuming
after a new pass. In worker processes, each part's matches come back in date
order and are let go of once stored: memory holds the parts read ahead, never all
of them. `works_of_authors` (the collaborators' rounds) still keeps its works in
memory. A pass streams
each part (`gzip`, line by line), tests the raw bytes of each line against
what is wanted (author, institution or work ids, DOIs, RORs) and parses only
the lines that may match; the deletion log is streamed the same way, for the
works found only. `Snapshot.report` counts the bytes, lines, lines parsed and
seconds. `harvest(…, source=…)`, `propose_people(project, source, …)` and
`snowball(project, source, …)` take either source and write the same raw runs,
hence the same tables (the tests compare them on the demo world, from the mini
snapshot `write_snapshot` writes). From the API, the harvest asks for the
works of consecutive people's records together, up to `batch` records a list
(`AUTHOR_BATCH`, 50; `author_batches` packs them), and gives each person the
works their own records sign, in the list's order: the tables of a harvest
person by person (`batch=1`), in about one request per 100 works instead of
at least one per person. A batch whose list fails, or holds a work none of its
records signs (a record merged into another), is asked for person by person,
where a failure is recorded with the person. A list answer names a work's
first 100 authors only (`AUTHORS_SHOWN`): `complete_authors` replaces every
work that shows that many, or says its authors are cut, by its own record
(`work(client, id)`, free of charge), so that the people further down are found
on it; the demo services cut their lists the same way (`authors_shown`). The
works are asked for with `select=WORK_FIELDS`: the fields the tables and the
collaborators' rounds read (those `cartolex.collect.digests` keeps), and a few
small ones kept for later (other identifiers, retraction and paratext,
bibliographic details, where an open copy is, the references, the index's own
topic, kept to compare with and never read to build anything, the last update);
a record holds about half of a whole one.

## People from institutions

`cartolex.collect.institutions`: `find_institutions(source, name)` lists the
institutions of a name, with their type, ROR id, parents and works, for a
person to choose; `resolve_institutions(source, refs)` reads OpenAlex ids, ROR
ids or URLs holding one. `propose_people(project, source, institutions,
years=None, min_works=2, levels=None)` reads the institutions' units and the
works signed there in the window (the slot's by default), and counts, per
author record, the works where the authorship states a unit of the tree; the
authors with `min_works` or more are proposed with their works, first and last
years, units (works and years each), ORCID and the person of the project they
already are. `propose_levels(units, levels, given)` maps each type of
institution to a level: `given` first, then the largest level for
`LARGE_TYPES` (education, government, healthcare, company, nonprofit,
archive, funder) and the smallest for the others; a project without levels
gets `unit` and `institution` when people are taken. Suggested merges pair two
records with the same ORCID, or with the same surname, first names that agree
(one may be an initial), a unit in common, no work in common and not two
different ORCIDs, when one of them, or the two together, reach `min_works`.
The other parents of a joint unit are read too, so that it enters with every
parent.

`take_people(project, take="all" | ["A1", "A2+A3"], role="mapped",
levels=None)` takes people from the latest proposal (or `run_id`): a group
`A2+A3` is one person with both records. People already confirmed with one of
the records are reported, not taken again. Their decisions:
`identity = confirmed`, the records, the role.

## Collaborators

`cartolex.collect.snowball.snowball(project, source, rounds=1, seeds=None,
years=None, cap=None, max_authors=None)` proposes the next rounds: round 1
from the seeds (the people named, else every confirmed mapped person with an
OpenAlex record), a later round from the collaborators of the round before not
refused (`decisions/snowball.csv`). A round reads the works of the people it
starts from (`works_of_authors`), leaves out the works with more than
`max_authors` authors, and gathers every co-author who is not a person of the
project (by record, or by an ORCID a project person has), with the joint works,
the people they wrote with and how often; the round is taken only if the
people proposed stay within `cap`, else it is left out whole and named in
`report.cut`. The works are asked for with `ROUND_FIELDS` (the title, abstract,
dates and authors), and a work whose author list a list answer cut is asked for
whole only when it shows `max_authors` authors or fewer (`complete_authors(…,
max_authors=…)`): one that shows more is left out of the graph either way, and
with 100 authors shown and `max_authors` at 25 none is. The collaborators' own
works are then read for their fit (`fit_works`, `FIT_FIELDS`, no authors): the
works counts of their records first (`works_counts`, one list per 50 records),
then the works of those with 100 works or fewer 50 records a list, and for each
of the others their 100 most recent works in one request (`recent_works`,
`sort=publication_date:desc`): a prolific co-author costs one request, not one
per hundred works and one per large collaboration. A collaborator the next round
of the same call starts from is read whole instead (their co-authors are
needed), and that reading serves the next round. `FIT_WORKS` (100) bounds the
works a fit reads, from the API and the snapshot alike (`most_recent`). The fit is
`topical_fit(seeds, candidates)`: cosine similarity of `(1 + ln tf) × idf`
vectors over the words (three letters or more, folded, the packaged function
words left out) of titles and abstracts, idf over the round's texts
(`1 + ln((1 + N) / (1 + df))`), the seeds' profile the mean of their unit
vectors; a candidate is measured on their works other than the joint ones,
among their 100 most recent (the joint ones when there is no other). The path of a collaborator is the
path of the person of the round before they wrote most with (ties by id), and
themselves. `cap` and `max_authors` default to `params.json`'s `collect.snowball`
(200, 25). Collaborators become people (`collaborators`), `context` and
`confirmed` in `people.csv`, with a `context` row each in `snowball.csv`;
`decide_collaborators(project, {person: decision})` changes both (`no` →
`excluded`, `later` → `undecided`, `projected` → the set `collaborators`).
Nothing is written when a request fails: a round is whole or absent.

`snowball(…, progress=…, on_phase=…)` says how far it is (the codes
`collaborators_seeds`, `collaborators_parents`, `collaborators_round`,
`collaborators_collaborators` with the records done, their total and the works
read, `collaborators_fit`, `collaborators_tables`) and when each phase ends:
`seeds`, `parents`, `round` (the collaborators found, the large works, the cut),
`collaborators`, `fit` and `tables`, with their seconds and counts. The app's job
writes each as a `phase` line of `logs/jobs/<job id>.jsonl`, with the requests the
phase sent, read from the cache and retried. The plan's estimate
(`privacy.collaborator_requests`) counts the seeds' works from their texts in the
tables and up to `cap` collaborators like them, and its time takes each request's
answer into account (`Service.latency`, 0.6 s for OpenAlex), not only the rate:
a job sends its requests one after the other.

## Failures and coverage

A person whose collection fails in a harvest, a resolution or a HAL search
(`ServiceError`, or `CacheMiss` in `cache_only` mode) is recorded by
`cartolex.collect.outcomes.failure_record` and the job goes on; after
`MAX_FAILURES_IN_A_ROW` (3) failures in a row, or a wait longer than a job may
block, it stops, keeps what it collected and raises the last error. The
failures of a job are one `failures` run; `latest_outcomes(layout, config)`
gives, per person and finder, the latest attempt, a finder's run naming the
person (`people` in a harvest's header, `searched` in a HAL run's, the records
of a resolution) superseding an earlier failure.

`cartolex.collect.coverage`: `person_coverage(project, good=None)` gives each
person's state, counts (texts, with an abstract or a full text, titles only),
years and languages, and the first blocking cause (`service_failure`,
`no_record`, `not_collected`, `no_works_in_window`, `no_abstracts`,
`few_abstracts`); `coverage_report(project)` adds the states by organisation,
the texts by year and by language (each text once) and the slots' summary;
`person_sheet(project, person)` the sources used (finders with their texts,
providers), discarded (candidate records not confirmed, HAL and SciELO
proposals, preprints read through their published version) and the attempts;
`retry_failed(project, client)` runs again the harvest, resolution or HAL
search that failed, for those people only; `add_documents(project, person,
folder)` imports a folder for one person (`import_folder(…, person_id=…)`);
`exclude_person`.

## Digests: rebuilding the tables quickly

`rebuild_sources` reads a harvest's runs (`openalex`, `orcid`) through
`cartolex.collect.digests.DigestCache`: each run is digested once into
`cache/sources/<slot>/<kind>/<run id>.jsonl.gz`, the records its reader needs
with the costly work done (`harvest.work_text`: the title and abstract
stripped, the abstract rebuilt from the inverted index, their languages
detected; the registry's works lists left out). `cache/sources/index.json`
(`cartolex-digests/1`) holds, per run, the raw file's size and modification
time and the digester's version (when one differs, the run is digested again),
the records it holds and the people it names. Before the readers run, the runs
that are someone's latest harvest and have no fresh digest are digested, in up
to four worker processes when they weigh more than 32 MB; a run superseded for
everyone is never read. A digest gives the same rows as its raw run: a rebuild
gives the same bytes with digests, without them (`incremental=False`) or
after `cache/sources/` is deleted (tested). `tools/collect_scale.py` measures
it on a synthetic collection.

## Plug-in points for new finders

- a finder that reads OpenAlex takes an `OpenAlexSource`, so the snapshot can
  stand in for the API;
- record a person's failed collection with `failure_record` and
  `write_failures`, so the coverage report can say so;
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
structures and years, and `record`, `hal:<idHAL>` when it has one). No row is
built from it; a form is confirmed like any record, `resolve.confirm(project,
person, ["hal:<idHAL>"])`: `people_refs` reads the idHAL from the confirmed
records of `decisions/people.csv` at once, and the next rebuild writes it into
the person's `ids` in the people table, where the next HAL collection finds it. A SciELO
author proposed by name carries `orcid` and `record` (`orcid:…`) when the
article shows an ORCID, confirmed the same way. `resolve.identity_queue(project)`
lists the people whose identity waits with every finder's candidates, each with
the record that confirms it (the resolution's with their score and evidence).

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
the values the text kept. The texts are first put in **groups**: the texts some
candidate key could join (a DOI, a source link, a person and a normalised title,
a DOI a provider states for a preprint, a text a record names as its version),
found by sorting 64-bit hashes of the keys and taking the connected groups.
Each group is merged by `merge_works`, the pure function of the rules, so memory
holds one group at a time; a text alone whose records all come from one finder
(most texts) takes its fields directly (`_alone`, tested to give what
`merge_works` gives). A merge is refused, and listed with its reason, when
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

**Preprints.** A preprint and a published version (article, review,
communication, proceedings, chapter or report) are never merged: when they
meet by the third rule, or a provider states the published DOI (arXiv's
`arxiv:doi`, bioRxiv's `published`), the preprint's `version_of` names the
published text. A preprint that meets several published texts by the third
rule (an article and its conference version) names the version of record among
them (article, then review, chapter, conference version); it stays unlinked
when two of them rank the same, or when providers name several. The build reads only the
published version (`corpus.assemble` skips a text whose `version_of` is in the
tables): it is the version of record, with its final title, year and DOI, and
reading both would count one work twice in its authors' texts. A published
text without an abstract gets its preprint's abstract parts.

**Duplicates the merge keeps apart.** Texts the rules keep apart can still be
one work: the same article under two DOIs, a conference version with its own
DOI, a preprint nothing links. The tables keep them all; `corpus.assemble`
reads one text per work (same normalised title of at least 25 characters,
years at most one apart, an author in common), the version of record, and
counts the copies it read once (`duplicate_texts` in its `run.json`, and a
note on the Texts tab).

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
`cache/tmp/`, in the job's PDF worker (below): a file that cannot be read, or
takes too long, is reported for its text only.

**The PDF worker.** `cartolex.collect.pdfworker.PdfWorker(timeout)` reads each
PDF in a separate process (started with `spawn`, once per job, when the first
PDF comes) and waits at most `timeout` seconds (120 by default) for its text;
a file that takes longer is left out with the reason (`no text after 120 s`),
the process is stopped and the next file gets a new one. `import_folder` and
`improve_texts` open one for their job (`pdf_worker(timeout)`, their
`pdf_timeout` argument), and every PDF they read goes through it
(`extract_pdf`). Tests replace the reading function with
`extractor="module:function"`, imported in the worker. A
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
| `coverage(layout)` | per slot: texts, with a title, an abstract, a full text, parts per provider; the merges of `sources/merges.json` | a dict for the coverage report |
| `propose_people(project, source, institutions, years=…, min_works=2, levels=None)` | the authors of institutions and their units | `InstitutionProposal` (people with evidence, suggested merges, levels) |
| `take_people(project, take="all", role="mapped", levels=None)` | people taken from the latest proposal | `TakeReport` (taken, already known, refused) |
| `snowball(project, source, rounds=1, seeds=None, cap=None, max_authors=None)` | the next rounds of collaborators | `SnowballReport` (rounds, collaborators with evidence, the cut) |
| `decide_collaborators(project, {person: decision})` | decisions on collaborators | rows changed |
| `harvest(project, client, source=SnapshotSource(Snapshot(folder)))` | the harvest from a snapshot | `HarvestReport` |
| `coverage_report(project)`, `person_sheet(project, person)` | states, causes, aggregates; one person's sheet | dicts for the interface |
| `retry_failed(project, client)`, `add_documents(project, person, folder)`, `exclude_person(project, person)` | the coverage report's actions | the finders' reports |
| `identity_queue(project)` | the people whose identity waits, with every finder's candidates | a list for the interface |

A cancel raises `Cancelled` and leaves no partial run; the HTTP cache keeps
every complete answer, so collecting again is quick. `client.egress.summary()`
lists the hosts contacted and the kinds of data sent.
