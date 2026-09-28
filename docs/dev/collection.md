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
| arXiv | 2026-09-28 | no more than one request every three seconds, one connection at a time | one request every 3 s |
| HAL, SciELO, bioRxiv/medRxiv, Europe PMC | not yet | — | a conservative rate until their finders are written |

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
| HAL, SciELO, arXiv, bioRxiv/medRxiv, Europe PMC | stubs answering 501; `register_service(name, factory)` replaces one |

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

**Faults** (`services.faults.add(kind, service=…, path=…, times=…, skip=…)`):
`status` (with `status` and `retry_after`), `hang` (answers after `delay`
seconds), `drop` (closes the connection), `malformed` (a body cut in half),
`cut_page` (half of a cursor page missing) and `early_end` (a null cursor too
early). `services.requests` lists every request received, with its headers.

## Plug-in points for new finders

- declare the service in `SERVICES` (rate, lifetimes, policy with its date);
- fetch through `HttpClient.get_json` / `get_all`, with a `kind` and the
  `sends` of every request;
- store what was received with `RawWriter(layout, slot, "<kind>", header)` and
  add a reader for `<kind>` to `default_readers()`;
- serve the API subset from the bibliographic layer with a service object and
  `register_service`, replacing the stub.
