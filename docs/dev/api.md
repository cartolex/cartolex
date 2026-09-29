# The app and its API

`cartolex.app` is the web app: the API the screens use and the server of the
interface's files. The same code serves one person on their own computer and a
hosted service. The engine packages and the packages below the app (the
project format, the build) never import it (`tests/test_layering.py`).

```python
from cartolex.app import AppSettings, create_app

app = create_app(AppSettings(project=folder))       # an ASGI app, e.g. for uvicorn
```

```bash
cartolex                         # the app on a free loopback port, in the browser
cartolex app my-project          # the same, with this project open
cartolex api my-project --host 0.0.0.0 --allowed-host maps.example.org   # hosting
cartolex api --projects-root /srv/projects --allowed-host maps.example.org
```

The OpenAPI description of every route is at `GET /api/openapi.json`.

## The modules

| module | what it holds |
| --- | --- |
| `app` | `create_app(settings, extensions)`: the app, its routes, its middlewares |
| `settings` | `AppSettings`: the mode, the project or the projects' folder, the app's own folder, host names, limits, and the services (job runner, collection, site builder, build registry, AI key) |
| `runtime` | `Runtime`: everything one app holds while it runs (`app.state.cartolex`): sessions, open projects, jobs, caches. Nothing is kept at module level: two apps in one process share nothing |
| `extensions` | `Extension` and its parts; checking and combining them ({doc}`extensions`) |
| `manifest`, `schemas` | the manifest the interface starts from ({doc}`app-manifest`) |
| `projects`, `deps` | the project context of each request; local and hosted project hosts; paging |
| `security`, `routing`, `auth` | sessions, the launch link, the host check, CSRF, headers; the guard on every route; `Principal` and `authorize` |
| `jobs` | `JobRunner`, `LocalJobRunner`, the job logs |
| `errors`, `etags` | the error shape; versions as `ETag` and `If-Match` |
| `collection`, `people_io` | the `CollectionService` protocol and its stand-ins; people tables and imports |
| `share`, `uploads`, `static_files` | the site builder protocol; receiving files; serving the interface |
| `logs`, `server` | JSON log lines; running the server |
| `routes/` | one module per screen |

## Per request: the project context

A route that works on a project takes the dependency `ProjectDep`
(`cartolex.app.deps`): a `ProjectContext` with the project (open for
writing), its lock and its paths. **Locally** one project is open at a time:
`POST /api/projects/open`, `/close`, `GET /api/projects/recent` change and
list it, and the app holds its lock until it closes it or stops. **Hosted**,
many projects live in one folder (`--projects-root`); a request names its
project by the route, `/api/projects/<id>/<the local path without /api>`
(`/api/projects/coast/keywords`), or its principal carries one (a host's
sign-in). Each is opened on first use. Without an open project, a project
route answers 409 `no_project` with the next action `open-project`.

## Security

- **The launch link.** Each app has a random launch token; `cartolex app`
  opens the browser at `/launch?token=…` and prints the link. The first visit
  exchanges it for a session (an `HttpOnly`, `SameSite=Strict` cookie) and
  goes to `/`; the link does not work a second time. A hosted service can
  sign people in its own way (`AppSettings.authenticate`), which opens a
  session the same way.
- **The host check.** A request whose `Host` is not a name the app answers
  to is refused (400) before anything else: loopback names locally, the
  `--allowed-host` names when hosted.
- **CSRF.** Every `POST`, `PUT`, `PATCH` and `DELETE` sends the session's
  token in `X-Cartolex-CSRF`; the interface reads it from the cookie the
  manifest names. A request with a cross-site `Origin` (or `null`) is refused.
- **No CORS.** No `Access-Control-Allow-*` header is ever sent.
- **Headers.** Every response carries `Content-Security-Policy: default-src
  'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:;
  … object-src 'none'; base-uri 'none'; frame-ancestors 'none'` (no inline
  script, no `eval`), `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer`, and `X-Request-ID`. `.js` files are `text/javascript` on
  every platform (a fixed table, never the system's).
- **Inputs.** Bodies are pydantic models with bounded sizes; a request larger
  than 16 MB (uploads: 50 MB) is refused before it is read. Uploads are
  written only inside their folder under a clean name; an archive is checked
  member by member (no absolute path, no `..`, no link, no hidden name, at
  most so many members and bytes once unpacked) and refused whole.
- **Authorisation.** Every route calls `authorize(principal, action,
  resource)` through its guard (`cartolex.app.routing.Guard`; a test fails
  when a route has none). Locally there is one trusted principal, the person
  who opened the link. A refusal is 403 with a plain message; no session is
  401 with the next action `sign-in`.

## Errors and empty results

Every error has the same shape (V2-016): a stable `code`, its `params`, a
`message` in plain English, and `next`, what to do: a `label` and an `action`
key the interface maps to a route or a command (`reload`, `retry`, `confirm`,
`fix-input`, `open-project`, `sign-in`, `wait`, `build`, `unlock`,
`settings`, `report`, `none`). The server does not translate: the interface
shows the text of the code from its catalogues (English, French, Portuguese),
filled with the params, and falls back on `message`. Every code is declared
once, with its English text, in `cartolex.app.errors.ERRORS` (listed below);
a test fails when a route raises a code that is not there or leaves out a
param its text names. Some errors add fields (`current` for a stale write,
`job` for a busy project).

```json
{"error": {"code": "stale", "params": {"file": "themes.json"},
           "message": "themes.json changed since it was read; reload it and apply the change again",
           "next": {"label": "Reload", "action": "reload"}, "current": "sha256:…"}}
```

A list or a result with nothing in it says what to do next (V2-013), the same
way: `"empty": {"code": "empty_no_keywords", "params": {}, "message": "no
keywords yet: build the keywords first", "next": {"label": "Build the
keywords", "action": "build"}}`. The state of a stage carries its skip reason
(`skip`), its last failed attempt (`attempt`) and the reasons of an update
(`reasons[]`) with codes too (`cartolex.app.messages.MESSAGES`); the English
`skip_reason` and `attempt.error` stay beside them.

## Versions: `ETag` and `If-Match`

Every read of a decision file answers with its version in `ETag`: the
fingerprint the guarded writer checks (`sha256:…`), or `none` for a file not
written yet. Every write sends the version it read in `If-Match` (without it:
428). When the file changed in between, the write is refused with **412**, the
current version in `ETag` and in `error.current`, and nothing is written: the
interface reloads and merges. The atlas bundle has an `ETag` too, its lineage;
`If-None-Match` gives 304 when it did not change.

## Lists

Lists are paged, sorted and filtered on the server: `offset`, `limit` (at most
500), `sort` (a field, `-field` for descending), `q` (a text filter) and each
list's own filters. The answer: `items`, `total`, `offset`, `limit`, `sort`,
`sorts` (the fields it can sort by), `filters`, `empty`, and each list's
counts.

## Jobs

Builds, collections and site builds run as **jobs**, outside the request,
through a `JobRunner` (submit, status, progress, cancel, list, events).
`LocalJobRunner` runs each in a thread of the app; a queue can stand behind the
same interface. One job runs per project at a time: a second build or
collection is refused with 409 `busy`, naming the running job. A job's states
are `queued`, `running`, `cancelling`, `succeeded`, `failed`, `cancelled` and
`interrupted`. A cancel stops at the next safe point, and the result says
« nothing changed » or « finished before the cancel ».

Every job writes `logs/jobs/<job id>.jsonl` in its project: a `job` line (its
kind, the process, a digest of the machine's name and its boot), the build's own events (phases,
stage ends with counts and times) and a `job-end` line — never a name or a
text. After a restart, a job whose log has no end and whose process is gone is
`interrupted`, never `running`.

## Logs and the diagnostic

The app logs JSON lines through `logging` (`cartolex.app`): each request with
its id, method, route **template** (`/api/jobs/{job_id}`, never the values or
the query), status and time. The server installs the formatter
(`cartolex.app.logs.configure_logging`); creating an app changes no logging
setting. `GET /api/diagnostic` gives the versions (Python, cartolex, the key
libraries, the language models), the machine's size and the recent job events
— never a project's name, path, people or texts — so it can be pasted into a
report as it is.

## The routes

**The app**

| route | what it does |
| --- | --- |
| `GET /api/health` | `{"status": "ok"}`, without a session (a container's health check) |
| `GET /api/app/manifest`, `GET /api/app/manifest/schema` | the manifest and its schema ({doc}`app-manifest`) |
| `GET /api/session`, `DELETE /api/session` | who the session acts for; sign out |
| `GET /api/diagnostic` | versions, machine, recent job events |
| `GET /api/openapi.json` | this API's description |
| `GET /launch?token=…` | the launch link |

**Projects**: `GET /api/projects/current`, `POST /api/projects/open {path}`,
`POST /api/projects/close`, `GET /api/projects/recent` (locally);
`GET /api/projects` (hosted: the projects the principal may open);
`POST /api/projects {folder | id, name, domain_title, domain_description,
languages, reference, display, start}` creates a project, with the extensions' slots,
projected sets and identity, and opens it; `display` defaults to the reference
and the corpus languages; `start` (`people`, `institutions`, `collaborators`,
`folder`, `corpus`) is where it starts from: a folder or a corpus adds a slot of
that kind, and the answer's `next` is the page to go to.
`GET /api/projects/defaults` gives the suggested folder (`~/cartolex-projects`,
locally), the starting points and the languages; `POST /api/projects/demo
{folder}` (locally) creates the demo project (the S demo world: an invented
community of coastal and marine sciences, in English and French) in an empty or
new folder (default `~/cartolex-projects/demo`) and opens it; it still needs a
build.

**State and building**

| route | what it does |
| --- | --- |
| `GET /api/project/state` | every stage's state (the six states, as keys: `up_to_date`, `needs_update`, `never_built`, `running`, `failed`, `skipped`) with its reasons, last run and last failed attempt, grouped in areas (corpus, keywords, themes, map, share, and the extensions'), each area summing up its stages; derived from the run records alone |
| `POST /api/build {scope, options: {force, allow_over_budget}, dry_run, consent}` | `scope`: stage ids or areas, everything by default. The dry run (the default) answers the plan: each stage's action, reasons, estimate, whether it asks consent, and the consent requests. `dry_run: false` starts a job (202); a stage that asks consent runs only when listed in `consent`; without it, an opt-in stage (the AI clean-up; its request says `skipped_without: true`) is skipped as if switched off and the stages after it run, any other is refused with those after it |
| `GET /api/build` | the tracker: the running or last build job (its progress: phase, stage, fractions, ETA, message) and each of its stages, done, running or waiting, with counts and times; its result says what changed. A failed stage's result carries its code, params and `next` (the settings for a missing language model or a refused stage, a diagnostic otherwise); a consent request of a paid stage gives `ai_calls_max`, the most AI calls it makes when the candidates are known |
| `GET /api/overview` | what the overview adds to the state: `project` (id, name, the state of the whole), `next` (the one most useful next step), `health[]` (a stale map, a missing language model, a stage too large for this machine, several languages without the AI clean-up, a proposal of collaborators cut at its cap), `preview` (an even sample of at most 1 500 of the map's people, `[x, y, top-level theme index]`, with the top-level themes and the bounds; `null` without a map) and `shares` (the three latest site builds). Each item is a message (`code`, `params`, `message`), a `level` (`info`, `warning`) and a `next` action; a build action may carry `scope`, the areas the build covers |
| `GET /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events?after=n`, `POST /api/jobs/{id}/cancel` | jobs; a job's `title` comes with `title_code` and `title_params` (`job.title.<code>`), a result's `summary` with `summary_code` and `summary_params` (`job.summary.<code>`) |

**Parameters and map versions**

| route | what it does |
| --- | --- |
| `GET /api/params` | per stage, each parameter's effective value, origin (`default`, `rule` with the rule, `params.json`), limits, the value of its last run and whether it changed since; the validation messages; the sizes the rules use |
| `PUT /api/params {seed, pinned_year, stages}` | replace `decisions/params.json` (`If-Match`); refused values: 422 with every reason |
| `GET /api/map/versions` | the versions, newest first, and the pinned one |
| `POST /api/map/versions {action: pin | try | discard, version, seed, method, note, build}` | pin a version, try another layout (a new version beside the pinned one, with another seed or `method`: `umap`, `tsne` when openTSNE is installed, `tree`), discard a version nobody pinned; `build: true` also redraws the map. `GET` gives `methods` and the default rule (`default_method`: t-SNE from `tsne_from_people` mapped people) |
| `GET /api/map/bases` | the base maps: other projects' maps copied into this one (`sources/bases/<id>/base_map.json`: the places of their keywords and people, without names, and their top-level themes) |
| `POST /api/map/bases {folder, id}`, `DELETE /api/map/bases/{id}` | copy another project's map (locally; `If-Match` of `project.json`) and add it to `project.json`'s `bases`, or remove one; refused: `base_no_map`, `base_same_project`, `base_in_use`, `bases_hosted` |

**Snapshots**: `GET /api/snapshots` (each decision file's versions and each
stage's current and previous generation), `GET /api/snapshots?file=themes.json`,
`GET /api/snapshots/{file}/{version}` (its content), and
`POST /api/snapshots/{file}/{version}/restore` (`If-Match`; a restore is a new
version, so it can be undone too).

**People and collection**

| route | what it does |
| --- | --- |
| `GET /api/people` | people with their role, set, identity state, records, unit, extra columns, coverage (texts, texts with an abstract, years, a class: good, thin, none) and coverage `state` (good, thin, failed, no_data) with its first blocking `cause`; filters `role`, `identity`, `set`, `coverage` (a class or a state), `source`, `col=<column>:<value>` (repeatable), `q`; counts per role, identity, class and state; `facets`: each extra column's values and counts (filters built from the people's own columns) |
| `PATCH /api/people {person_ids \| where, role, set, note}` | roles, sets, notes (`If-Match`); `where` (the list's filters) changes every person it keeps, for « all N matching » |
| `POST /api/people/merge {target, sources}` | rows that are one person |
| `GET /api/people/duplicates` | pairs of people who may be one person, with the reason (`cartolex.collect.people_import.find_duplicates`); none is merged |
| `GET /api/people/{id}/sheet` | why a profile is what it is: the coverage and its first blocking cause, the sources used and discarded (each discarded one with `code` and `params` beside its English `what` and `why`), each finder's latest attempt, the texts, the affiliations with their years, the decision |
| `POST /api/people/import` | a CSV file (a form's `file`) or `{"text": …}` (a pasted list, one person per line): kept outside the project until confirmed, and a mapping proposal, one field per column: `last_name`, `first_name`, `name` (a full name), `orcid`, `openalex`, `idhal`, `role`, `set`, `org:<level>` (an organisation at that level; a new level is added), `column` (kept as a filter), `ignore`; e-mail columns are refused (never stored) |
| `POST /api/people/import/{id}/confirm {mapping, role, set}`, `DELETE /api/people/import/{id}` | add the people (`If-Match` of the people): the answer counts them and lists the possible duplicates; or forget the import |
| `POST /api/people/import/documents` | a form: `file` (a zip, or one document), `kind` (`folder`: documents matched to people by their names or folders, or every one to `person_id`; `corpus`: a zip holding its index CSV and the files), `create_people`; a job of kind `import` |
| `GET /api/organisations` | organisations with their level, parents, units below, people now and ever; filters `level`, `parent`, `q`; counts per level |
| `GET /api/organisations/{id}` | one organisation: parents, units, each person's affiliation with its years and source |
| `GET /api/texts` | texts with their parts (part, language, provider), richest content (`title`, `abstract`, `full`), people; filters `slot`, `year`, `language`, `content`, `provider`, `person`, `q` (title or DOI); counts per content and provider |
| `GET /api/texts/{id}` | one text: each part by provider with a preview, its people, the records merged into it (`sources/merges.json`), its versions (preprints) and the conflicts between finders |
| `GET /api/collection/plan?action=`, `POST /api/collection/plan {action, …}` | what an action would do and **what leaves the computer**: each host with its purpose, what it is sent, the requests and their cost at the service's prices; what never leaves; what is kept and where; notes (`code`, `params`, `message`; the OpenAlex daily budget is `note_openalex_budget` or `note_openalex_budget_key` with `usd` and `days`); the estimate; `consent_needed` |
| `POST /api/collection/start {action, …, consent}`, `GET /api/collection`, `POST /api/collection/cancel` | the collection job: `identify`, `harvest`, `institutions`, `collaborators` or `retry` (`collect` for a stand-in); when the plan asks consent, the start carries `consent: true` (else 409 `consent_needed`, with the plan) |
| `GET /api/collection/identities?state=pending&clear=&finder=` | the identity queue: each person with every finder's candidate records (OpenAlex with the ORCID registry as evidence, HAL, SciELO), their score, evidence (each line `text` and `points`, with `code` and `params` when the record has them) and detail (with `detail_code` and `detail_params`); the single clear match flagged (`clear`); counts of clear, unclear and without candidate |
| `POST /api/collection/identities/{person_id} {decision: accept \| none \| id, record}`, `POST /api/collection/identities/accept {person_ids}` | decide one identity, or accept the single clear match of many (the others are `left`) |
| `GET /api/collection/coverage` | coverage per class and role; the four states and their first blocking causes; states by organisation; texts by year (with an abstract, titles only) and by language; the slots' summary |
| `GET /api/collection/collaborators`, `POST /api/collection/collaborators/decide {decisions}` | collaborators round by round with joint texts, fit and path, and the cap, rounds and cut of the latest run; decisions `mapped`, `context`, `projected`, `no`, `later` (`If-Match` of the people) |
| `GET /api/collection/institutions`, `POST /api/collection/institutions/take {take, role}` | the latest search of institutions and the latest proposal of their people (paged), with suggested merges and levels; take `all` or records (`A1+A2`: one person with two records) |
| `GET /api/sources`, `GET /api/sources/{slot}/files`, `POST /api/sources/{slot}/files` | a folder or corpus slot's files; upload a document or a zip archive into `sources/<slot>/` |

**Keywords, themes, atlas**

| route | what it does |
| --- | --- |
| `GET /api/keywords` | the candidates of the current extraction in their three bands (`kept`, `check`, `aside`) with the reason of each, your decisions and the AI's verdicts by API (`keywords.triage`) applied, and the `route` that decided each (`person`, `ai-handoff`, `ai-copilot`, `ai-api`, `extraction`); filters `band`, `lang`, `decision`, `route`, `q` (the term or one of its forms); counts per band, route and language; the counting unit of the extraction; `warning` when several corpus languages have no AI filtering yet (`health_languages_split`); decisions whose keyword disappeared (listed, never dropped) |
| `GET /api/keywords/decisions` | the decisions of `keywords.csv`, the latest first (the history, each one restorable); filters `decision`, `source`, `q` |
| `POST /api/keywords/decisions/where {where, decision, reason}` | keep or exclude every keyword the list's filters keep (`If-Match`) |
| `POST /api/keywords/decisions {decisions: [{term, language, decision, target, reason}]}` | keep, exclude or merge, one or many (`If-Match`); the first decision freezes the identity |
| `POST /api/keywords/restore {keywords}` | undo decisions (an exclusion restored) |
| `GET /api/themes` | the saved tree, or the draft of the last grouping, with the keywords the tree lacks or holds too many |
| `POST /api/themes/ops {tree, ops}` | apply operations of `cartolex.project.themes` (`rename_node`, `rename_level`, `move_keywords`, `move_node`, `merge_nodes`, `split_node`, `create_node`, `delete_node`, `set_aside`, `put_back`, `set_review`, `set_attribution`, `prune_empty`, `insert_level`, `remove_level`) to a tree; answers the new tree and each step's description (the names of the undo list). Nothing is saved |
| `PUT /api/themes {tree, action}` | save a new version (`If-Match`); empty nodes are removed and named in the action |
| `GET /api/themes/versions`, `GET /api/themes/versions/{id}`, `POST /api/themes/versions/{id}/restore` | versions |
| `POST /api/themes/apply` | a build job of the themes and the map |
| `GET /api/atlas` | what the map draws at any depth of the theme tree (levels, nodes, people, keywords, units, trajectories, projected people, bounds), `cartolex-atlas/2` (described below, with the theme editor's routes), cached by its lineage (the runs it is made from), with an `ETag` |
| `GET /api/atlas/texts` | every text placed on the map, columnar (`cartolex-atlas-texts/1`: `id`, `title`, `year`, `x`, `y`, `by`, `terms`, `people`, `unplaced`); `base` places them on a base map |
| `GET /api/atlas/regions?kind=person\|organisation&ids=a,b` | the keywords a region spans, by id (at most 500 ids): a person's most used keywords (at most 40), or those of an organisation's current members |

**Sharing, settings, the AI handoff**

| route | what it does |
| --- | --- |
| `GET /api/share` | the site builds in `outputs/sites/` (paged: `offset`, `limit`), the newest first, each with `latest`, `stale` (what it was built from changed since: the runs it reads, the tables, a decision file), its options and counts; `exports` (the files in `outputs/exports/`); `available` |
| `GET /api/share/plan?names=&texts=&title=&language=` | the privacy summary of a build with these options (people named or pseudonymised, organisations, keywords, themes, texts carried, full texts kept out) and the checks before publishing, each `{code, level, params, fix}`: `blocker` (`no_map`), `question` (`names_unanswered`), `warning` (`names_shown`, `projected_names_shown`, `abstracts_included`, `map_stale`, `themes_untranslated`, `themes_technical`, `themes_empty`, `title_generic`), `info` (`full_texts_kept`); `ready` |
| `POST /api/share/builds {names, texts, title, language}` | build the offline site (a job, 202): `names` (`names` or `pseudonyms`) is required for a people atlas (422 `names_question`); `names_projected` (the projected people's, `pseudonyms` by default) is asked apart; `texts` is `none` (the default), `titles` or `abstracts` |
| `GET /api/share/builds/<id>/site/<path>`, `GET /api/share/builds/<id>/zip` | a build's files (to open it in the browser), and the build as one zip whose README comes first |
| `GET /api/share/figures/map?format=png\|svg&width=&height=&theme=` | the map as an image of the size asked, light or dark (people never named) |
| `GET /api/share/tables/themes.csv` | the theme tree as CSV |
| `POST /api/share/exports {kind}`, `GET /api/share/exports/<name>` | write the map bundle (`map_bundle`) or the project as one zip without its caches (`project`) into `outputs/exports/` (a job, dated names), and download it |
| `GET /api/settings`, `PUT /api/settings` | languages, language models, the AI identity (with what changing a frozen one costs: 409 `identity_frozen` unless `confirm_identity_change`), slots, projected sets, levels, data sources |
| `GET /api/settings/stopwords`, `PUT /api/settings/stopwords {add, remove}` | the words added to and removed from the lists of words that are never keywords, per language (`decisions/stopwords.json`, `If-Match`); a word both added and removed: `stopword_both` |
| `GET /api/settings/prompts`, `PUT /api/settings/prompts/{name} {text}` | the prompts a project may replace (the packaged text, the project's own in `decisions/prompts/<name>.txt`, the placeholders); `text: null` goes back to the packaged one (the project's is kept in the history); a placeholder the packaged text lacks: `prompt_placeholder` (`If-Match`) |
| `GET /api/settings/backup` | a zip of `project.json` and `decisions/` with its history, and `backup.json` (`cartolex-backup/1`); texts, caches and built results are left out |
| `POST /api/settings/restore` | a backup (multipart `file`): its decision files replace the project's, each current version kept in its history first; `project.json` stays; `not_a_backup` otherwise |
| `POST /api/settings/reset {what: built}` | remove the built results (every stage is then never built); decisions, texts and caches stay; 409 `busy` while a job runs |
| `GET /api/machine`, `PUT /api/machine/keys {service, key}` | this computer: the keys saved on it (`mistral`, `openalex`; whether set, from the environment or saved, the last four characters; never shown whole, never in a project: `<data dir>/keys.json`, readable by its owner only; an environment variable wins), whether the AI clean-up can run by API, OpenAlex's daily budget with and without a key, the processors, the memory available and the build's memory budget; `key: null` removes a key; refused on a hosted service (`keys_hosted`) |
| `POST /api/handoff/export {band, bands, terms, lang, limit, max_tokens, group}` | the parts of a handoff (`cartolex.project.handoff`): for each, the prompt to paste, the terms to attach and the answer's format, its `bundle.json` (`cartolex-handoff/1`, sent back with the answer), and what they contain and never contain; parts stay under `max_tokens` (a chat assistant reads a limited amount at once); with `group` (the default) the terms the same people use share a part (`group_items`, from the extraction's `term_people.npz`), so a term and its translation are judged together |
| `POST /api/handoff/export.zip` | the same parts as a zip, one folder per part |
| `POST /api/handoff/import {bundle, answer}` | keep the answer as it came in `decisions/history/ai/` (with the part it answers) and propose a decision per answered term, with what could not be read (lines ignored, renumbered, unmatched); the first answers freeze the identity |
| `GET /api/handoff/proposals`, `GET /api/handoff/proposals/{id}`, `POST /api/handoff/proposals/{id}/accept {terms, all}` | proposals; accepted ones reach `keywords.csv` with the source `ai-handoff` (`ai-copilot` for a copilot's result); an accepted term whose English form is another term is merged into it |
| `GET /api/keywords/ai` | the two routes of the AI filtering: by handoff (the proposals so far) and by API (provider, whether a key is saved, what is sent, an estimate of the calls and tokens, the last run) |
| `POST /api/keywords/ai/run {consent}` | filter by API: switch `keywords.triage` on in `params.json` and start it as a build job (202); `ai_api_not_ready` without a key or a provider, `ai_consent_needed` without consent |

**The person**: `GET /api/me/preferences` and `PUT /api/me/preferences
{locale, theme, other}`: the interface language, the theme and a few other
settings of the person signed in, kept in the app's own folder (a hosted
service: they follow a person from one browser to another). Locally the
interface keeps them in the browser.

**The interface**: `/static/…` (the interface's files: `.js` as
`text/javascript; charset=utf-8`, `.css` as `text/css`, `.json` as
`application/json`, `.svg` as `image/svg+xml`), `/static/ext/<id>/…` (an
extension's), and every other address that is not under `/api` or `/static`
answered with the shell (`index.html`): the interface routes in the browser.
A page whose module is missing is listed in the manifest with
`/static/pages/placeholder.js`, and a warning is logged once.

## Collection behind a protocol

Importing people and collecting texts go through a `CollectionService`
(`cartolex.app.collection`): `describe()`, `propose_import()`,
`confirm_import()`, `plan(action, options)`, `collect(control, action, options)`
(in a job) and `candidates()`. A list is imported by
`cartolex.collect.people_import` for every service (`cartolex.app.importing`
turns the interface's one field per column into its mapping).

- `ServiceCollection(settings)` (`cartolex.app.collect_service`, the default of
  the command line) runs the finders of `cartolex.collect`: `identify`
  (`resolve`, HAL by name, SciELO with a collection), `harvest` (OpenAlex and
  ORCID, HAL by idHAL, missing abstracts on request), `institutions` (a search,
  or the people of the institutions chosen), `collaborators` (`snowball`) and
  `retry` (`retry_failed`). Each phase has its own `HttpClient`, whose progress
  and cancel are the job's; the job's log gets one `egress` line per host (the
  kinds of data sent, never the values). Its plan comes from
  `cartolex.collect.privacy.plan_collection`, with HAL, SciELO and the text
  providers added, and every message carries a code for the catalogues.
  `cartolex app --services demo --world S:0` runs it against the demo services
  of a demo world, on this computer; the tests do the same.
- `UnavailableCollection` (the default of `AppSettings`) imports lists but
  collects nothing.
- `DemoCollection(world)` answers from a demo world without leaving the
  computer or reaching a service (action `collect` only).

The reading routes (coverage, organisations, texts, a person's sheet,
collaborators, the institutions' proposal) read the project's tables and raw
records (`cartolex.app.corpus_view`), whatever the service; each list is
computed once per version of what it reads and kept in the app's cache.

The site builder is a `SiteBuilder` protocol (`cartolex.app.share`):
`builds`, `folder`, `build`. The default is cartolex's own
(`cartolex.site.OfflineSiteBuilder`, {doc}`site`); `StubSiteBuilder` lists
earlier builds and refuses new ones, for a host that shares elsewhere.

## Error codes

Every code the API answers with, its status, the English text (the fallback of
`message`, with `{param}` for each param) and its next action. The interface's
catalogues give each code its text in every interface language.

| code | status | English text | params | next |
| --- | --- | --- | --- | --- |
| `host_refused` | 400 | this address is not one cartolex answers to; open it from the address the cartolex command gives | — | `sign-in` |
| `cross_origin` | 403 | a change was asked from another site; cartolex takes changes from its own pages only | — | `none` |
| `request_too_large` | 413 | the request is larger than the limit ({limit_kb} KB) | `limit_kb` | `fix-input` |
| `invalid_length` | 400 | the request's length is not a number | — | `none` |
| `sign_in` | 401 | sign in first: open the app from the cartolex command (its launch link) | — | `sign-in` |
| `csrf` | 403 | the change was refused: the {header} header is missing or wrong (reload the page) | `header` | `reload` |
| `forbidden` | 403 | {reason} | `reason` | `none` |
| `version_required` | 428 | this change needs the version you read: send it in If-Match (the ETag of the read) | — | `reload` |
| `version_ambiguous` | 400 | If-Match names one version | — | `reload` |
| `stale` | 412 | {file} changed since it was read; reload it and apply the change again | `file` | `reload` |
| `invalid` | 422 | the request is not valid: {problems} | `problems` | `fix-input` |
| `no_route` | 404 | no such address in this app | — | `none` |
| `method_not_allowed` | 405 | this address does not take this method | — | `none` |
| `http_error` | 400 | the request was refused ({status}) | `status` | `none` |
| `internal` | 500 | something went wrong inside cartolex ({error_type}) | `error_type` | `report` |
| `static_missing` | 404 | no such file in the interface | — | `reload` |
| `unknown_locale` | 422 | {locale} is not an interface language; choose among {locales} | `locale`, `locales` | `fix-input` |
| `invalid_sort` | 422 | cannot sort by {sort}; sort by one of {sorts} | `sort`, `sorts` | `fix-input` |
| `no_project` | 409 | no project is open: open one, or create one | — | `open-project` |
| `project_not_named` | 400 | name the project in the address: /api/projects/<id>/… | — | `open-project` |
| `invalid_project_id` | 400 | {id} is not a project id | `id` | `fix-input` |
| `project_not_found` | 404 | there is no project {id} | `id` | `open-project` |
| `hosted_projects` | 404 | a hosted app names its project in the address | — | `none` |
| `not_a_project` | 404 | {path} holds no cartolex project | `path` | `open-project` |
| `unsupported_format` | 409 | the project is in format {found}; this cartolex reads {expected} | `found`, `expected` | `open-project` |
| `locked` | 409 | the project is open in {app} (process {pid} on {host}, since {since}) | `app`, `pid`, `host`, `since` | `open-project` |
| `stale_lock` | 409 | the project's lock is stale: {app} (process {pid}, since {since}) no longer runs on this computer; remove it if no other window has the project open | `app`, `pid`, `since` | `unlock` |
| `project_exists` | 409 | the folder already holds a project, or is not empty: {path} | `path` | `fix-input` |
| `project_folder_missing` | 422 | choose the folder of the new project | — | `fix-input` |
| `project_folder_relative` | 422 | the project's folder is a full path: {path} | `path` | `fix-input` |
| `project_id_missing` | 422 | a hosted project needs an id | — | `fix-input` |
| `field_title_missing` | 422 | name the field the map covers (its title): the AI receives it with the terms | — | `fix-input` |
| `no_language_pack` | 422 | cartolex has no language pack for {languages}; choose among {available} | `languages`, `available` | `fix-input` |
| `identity_frozen` | 409 | the project's identity is frozen: changing its {changed} means cached AI answers are not reused (they are paid for again) or texts are parsed again; confirm the change to make it anyway | `changed` | `confirm` |
| `invalid_file` | 422 | a file of the project is not valid: {detail} | `detail` | `report` |
| `nothing_to_change` | 422 | nothing to change | — | `fix-input` |
| `busy` | 409 | a {kind} job ({job}) is already running on this project; wait for it or cancel it | `kind`, `job` | `wait` |
| `stages_running` | 409 | a job is running {stages}; wait for it or cancel it | `stages` | `wait` |
| `unknown_scope` | 422 | {item} is neither a stage nor an area; stages: {stages} | `item`, `stages` | `fix-input` |
| `invalid_parameters` | 422 | the parameters were refused: {problems} | `problems` | `fix-input` |
| `job_not_found` | 404 | there is no job {job} | `job` | `reload` |
| `job_ended` | 409 | the job has already ended ({state}) | `state` | `none` |
| `job_elsewhere` | 409 | this job runs in another process; stop it there | — | `none` |
| `keys_hosted` | 409 | on a hosted service the keys are set by whoever runs it | — | `none` |
| `stopword_both` | 422 | a word is both added and removed: {words} | `words` | `fix-input` |
| `prompt_not_found` | 404 | there is no prompt {name} to change | `name` | `reload` |
| `prompt_invalid` | 422 | the prompt cannot be read: {detail} | `detail` | `fix-input` |
| `prompt_placeholder` | 422 | the prompt uses placeholders the AI clean-up does not fill: {unknown} | `unknown`, `allowed` | `fix-input` |
| `not_a_backup` | 422 | this file is not a backup of a cartolex project | — | `fix-input` |
| `map_version_not_found` | 404 | there is no map version {version} | `version` | `reload` |
| `map_version_missing` | 422 | name the version to {action} | `action` | `fix-input` |
| `map_version_pinned` | 409 | {version} is pinned: pin another version before discarding it | `version` | `fix-input` |
| `no_pinned_version` | 409 | there is no pinned map version to start from: build the map first | — | `build` |
| `base_not_found` | 404 | there is no base map {base} | `base` | `reload` |
| `base_no_map` | 422 | {path} holds no project with a map: build its map there first | `path` | `fix-input` |
| `base_same_project` | 422 | a project's own map cannot be its base: choose another project | — | `fix-input` |
| `base_in_use` | 409 | a map version is placed on {base}: discard it before removing the base | `base` | `fix-input` |
| `bases_hosted` | 409 | on a hosted service a base map is added by whoever runs it | — | `none` |
| `no_versions` | 404 | {file} has no versions; files with versions: {files} | `file`, `files` | `none` |
| `file_not_written` | 404 | {file} does not exist yet | `file` | `none` |
| `version_not_found` | 404 | there is no version {version} of {file} | `version`, `file` | `reload` |
| `already_current` | 409 | this version is already the current one | — | `none` |
| `unknown_people` | 404 | unknown person id(s): {ids} | `ids` | `reload` |
| `unknown_set` | 422 | there is no projected set {set}; sets: {sets} | `set`, `sets` | `fix-input` |
| `set_needed` | 422 | a projected person belongs to a projected set: add one in the settings first | — | `settings` |
| `self_merge` | 422 | a person cannot be merged into themselves | — | `fix-input` |
| `merged_target` | 409 | {target} is itself merged into {into}: merge into that person | `target`, `into` | `fix-input` |
| `file_missing` | 422 | send the file in a form, as 'file' | — | `fix-input` |
| `list_body` | 422 | send the list in a form (as 'file'), or as {"text": …} | — | `fix-input` |
| `empty_list` | 422 | the list holds nobody | — | `fix-input` |
| `import_not_found` | 404 | this import is not waiting any more | — | `reload` |
| `mapping_unknown_fields` | 422 | unknown field(s) {fields}; the fields are {known} | `fields`, `known` | `fix-input` |
| `mapping_unknown_columns` | 422 | the list has no column(s) {columns} | `columns` | `fix-input` |
| `mapping_no_name` | 422 | map a column to last_name, or to name (a full name) | — | `fix-input` |
| `unknown_role` | 422 | {role} is not a role | `role` | `fix-input` |
| `collection_unavailable` | 409 | collecting texts is not available in this version | — | `none` |
| `no_slot` | 409 | the project has no slot to collect into: add one in the settings | — | `settings` |
| `collection_not_running` | 409 | no collection is running | — | `none` |
| `person_not_found` | 404 | there is no person {person} | `person` | `reload` |
| `invalid_record` | 422 | a record is scheme:id (orcid:0000-0002-1825-0097, openalex:A123…) or an ORCID iD | — | `fix-input` |
| `not_a_candidate` | 409 | this record is not a candidate of this person; paste an id instead | — | `fix-input` |
| `no_clear_match` | 409 | none of these people has a single clear match; decide them one by one | — | `none` |
| `unknown_collection_action` | 422 | {action} is not a collection action; actions: {actions} | `action`, `actions` | `fix-input` |
| `institutions_missing` | 422 | search institutions by a name, or choose the institutions to read | — | `fix-input` |
| `consent_needed` | 409 | this collection sends data to {hosts}: read what leaves the computer, then confirm | `hosts` | `confirm` |
| `too_many_decisions` | 422 | {n} keywords at once is more than {max}: narrow the filters | `n`, `max` | `fix-input` |
| `ai_api_not_ready` | 409 | the AI filtering by API needs a key (Settings, AI) and a provider in the project | — | `settings` |
| `ai_consent_needed` | 409 | the AI filtering by API sends keywords to {provider}: confirm first | `provider` | `confirm` |
| `organisation_not_found` | 404 | there is no organisation {org} | `org` | `reload` |
| `text_not_found` | 404 | there is no text {text} | `text` | `reload` |
| `no_institution_proposal` | 404 | nobody was proposed from institutions yet: read institutions first | — | `none` |
| `import_refused` | 422 | the import was refused: {detail} | `detail` | `fix-input` |
| `documents_missing` | 422 | no document (PDF, text) in what was sent | — | `fix-input` |
| `corpus_index_missing` | 422 | the archive holds no index (a CSV file at its top) | — | `fix-input` |
| `slot_not_found` | 404 | the project has no slot {slot} | `slot` | `none` |
| `slot_collected` | 409 | slot {slot} is filled by collection, not by uploads | `slot` | `none` |
| `file_too_large` | 413 | the file is larger than the limit ({limit_mb} MB) | `limit_mb` | `fix-input` |
| `unsafe_name` | 422 | the name {name} leaves its folder | `name` | `fix-input` |
| `file_exists` | 409 | {name} is already there; nothing is replaced (rename the file to add it) | `name` | `fix-input` |
| `not_an_archive` | 422 | the file is not a zip archive | — | `fix-input` |
| `archive_too_many_files` | 413 | the archive holds more than {max_members} files | `max_members` | `fix-input` |
| `archive_too_large` | 413 | the archive unpacks to more than {limit_mb} MB | `limit_mb` | `fix-input` |
| `unsafe_archive_member` | 422 | the archive was refused: a member is not allowed ({problem}); nothing was written | `problem` | `fix-input` |
| `archive_replaces` | 409 | the archive would replace {name}; nothing was written | `name` | `fix-input` |
| `archive_corrupt` | 422 | a member of the archive is larger than it says | — | `fix-input` |
| `not_a_corpus_language` | 422 | {language} is not a corpus language | `language` | `fix-input` |
| `merge_target_missing` | 422 | merge {term} into another keyword | `term` | `fix-input` |
| `no_decision` | 404 | none of these keywords has a decision | — | `reload` |
| `invalid_tree` | 422 | the tree is not valid: {detail} | `detail` | `reload` |
| `theme_refused` | 422 | the change was refused: {detail} | `detail` | `fix-input` |
| `theme_step_refused` | 422 | step {step} ({op}) was refused: {detail} | `step`, `op`, `detail` | `fix-input` |
| `no_keywords` | 409 | build the keywords first | — | `build` |
| `handoff_empty` | 404 | no term to send in this band | — | `none` |
| `invalid_bundle` | 422 | the bundle is not valid: {detail} | `detail` | `fix-input` |
| `proposal_not_found` | 404 | there is no proposal {proposal} | `proposal` | `reload` |
| `nothing_chosen` | 422 | choose the terms to accept | — | `fix-input` |
| `not_available` | 501 | building the offline site is not available in this version | — | `none` |
| `no_map_to_share` | 409 | there is no map to share yet: build the map first | — | `build` |
| `names_question` | 422 | say whether the site shows people's names or pseudonyms | — | `fix-input` |
| `site_not_found` | 404 | there is no site build {build} | `build` | `reload` |
| `export_not_found` | 404 | there is no exported file {name} | `name` | `reload` |

## Message codes

Empty results (`empty`), skipped stages (`skip`), failed attempts (`attempt`), the
reasons of an update (`reasons[]`) and the collection plan carry `code`, `params` and
the English `message` the same way; an empty result also names its next action.

| code | English text | params | next |
| --- | --- | --- | --- |
| `empty_no_people` | no people yet: import a list of names | — | `import-people` |
| `empty_no_match` | nothing matches these filters | — | `none` |
| `empty_no_keywords` | no keywords yet: build the keywords first | — | `build` |
| `empty_no_themes` | no themes yet: build the themes to get a first draft | — | `build` |
| `empty_tree_never_saved` | the tree was never saved | — | `none` |
| `empty_no_borderline` | no keyword sits near the border between two nodes | — | `none` |
| `empty_no_map` | no map yet: build the map | — | `build` |
| `empty_no_map_versions` | no map yet: the first build draws one and pins it | — | `build` |
| `empty_up_to_date` | everything is up to date | — | `none` |
| `empty_nothing_built` | nothing was built yet | — | `build` |
| `empty_no_jobs` | no job has run yet | — | `build` |
| `empty_no_recent` | no project opened yet | — | `open-project` |
| `empty_file_never_written` | {file} was never written | `file` | `none` |
| `empty_no_site` | no site built yet | — | `none` |
| `empty_no_site_unavailable` | no site built yet; building a site comes in a later version | — | `none` |
| `empty_no_collection` | no collection has run | — | `collect` |
| `empty_no_identity_to_check` | nobody waits for a check | — | `none` |
| `empty_no_identity_in_state` | nobody is in this state | — | `none` |
| `empty_handoff` | no term to send in this band | — | `none` |
| `empty_no_proposals` | no AI answers imported yet | — | `none` |
| `empty_no_decisions` | no decision yet: keep, exclude or merge keywords in the list | — | `none` |
| `collection_unavailable` | collecting texts from bibliographic services is not available in this version; import texts into a folder or corpus slot instead | — | — |
| `stage_switched_off` | switched off (set {stage}.enabled in decisions/params.json to run it) | `stage` | — |
| `stage_no_overlay` | the project has no overlay | — | — |
| `stage_not_applicable` | {reason} | `reason` | — |
| `stage_cancelled` | the stage was cancelled; its previous results are kept | — | — |
| `stage_refused` | the stage could not run: {detail} | `detail` | — |
| `language_model_missing` | a language model is missing: {detail} | `detail` | — |
| `stage_failed` | the stage failed ({error_type}): {detail} | `error_type`, `detail` | — |
| `health_map_stale` | the map was drawn from inputs that changed since; building the map restores it | — | `build` |
| `health_model_missing` | the language model {model} for {language} is not installed; the keyword extraction needs it | `model`, `language` | `settings` |
| `health_too_large` | {stage} needs about {need_mb} MB of memory and this machine has about {budget_mb} MB | `stage`, `need_mb`, `budget_mb` | `settings` |
| `health_languages_split` | the texts are in {languages}: without the AI clean-up, keywords of each language may form themes of their own | `languages` | `settings` |
| `health_snowball_cap` | the last proposal of collaborators in {slot} stopped at the cap of {cap} people | `slot`, `cap` | `settings` |
| `next_watch_build` | a build is running | — | `open:/build` |
| `next_import_people` | start with the people whose texts make the map | — | `open:/people` |
| `next_install_model` | install the language model the keyword extraction needs | — | `settings` |
| `next_see_failure` | {stage} failed: see why and build again | `stage` | `build` |
| `next_first_build` | build the keywords, the themes and the map | — | `build` |
| `next_restore_map` | the map is out of date: building it restores it | — | `build` |
| `next_update` | some results need an update | — | `build` |
| `next_curate_themes` | check the themes the grouping proposed and name them | — | `open:/themes` |
| `next_open_map` | everything is up to date | — | `open:/map` |
| `reason_code` | {detail} | `detail` | — |
| `reason_input` | {detail} | `detail` | — |
| `reason_parameter` | {detail} | `detail` | — |
| `reason_project` | {detail} | `detail` | — |
| `reason_upstream` | {detail} | `detail` | — |

## The atlas and the theme editor

### `GET /api/atlas`: `cartolex-atlas/2`

The atlas reads only the theme files of any depth (`themes_applied.json`,
`theme_keywords.csv`, `theme_people.parquet`, `theme_organisations.parquet`,
`trajectory_themes.parquet`, the `levels` of `positions.json`; see
{doc}`../format/derived`), never the two-level files that exist at depth 2
only, so a project of depth 1, 3 or 4 gets its map like one of depth 2.

```json
{
  "format": "cartolex-atlas/2", "available": true, "lineage": {"map.layout": "…"},
  "map_version": "v1", "depth": 2, "source": "decisions", "weights_basis": "tf",
  "people_counted": 38,
  "levels": [{"level": 1, "names": {"en": "Theme"}}, {"level": 2, "names": {"en": "Topic"}}],
  "nodes": [{"id": "s1", "parent": null, "level": 1, "order": 1, "names": {"en": "…"},
             "color": "#d22d3c", "weight": 3.6, "share": 0.097, "keywords": 2,
             "keywords_counted": 42, "top_keywords": ["…"], "x": -2.3, "y": 3.6}],
  "people": [{"person_id": "p0001", "name": "…", "unit": "…", "x": -3.4, "y": 4.0,
              "shares": [{"s1": 0.25, "s8": 0.75}, {"c3": 0.25, "c9": 0.75}]}],
  "keywords": [{"term": "tide gauge", "x": -4.3, "y": 3.3, "node": "c3", "level": 2,
                "counts_to": 2, "weight": 0.07, "share": 0.002}],
  "units": [{"unit": "…", "x": 0, "y": 0, "size": 7, "ellipse": {"sx": 0.5, "sy": 1.0, "rho": -0.1},
             "shares": [{"s1": 0.5}, {"c3": 0.5}]}],
  "trajectories": [{"person_id": "p0001", "start": 2021, "end": 2023, "texts": 2, "x": 0, "y": 0,
                    "shares": [{"s1": 1.0}, {"c3": 1.0}]}],
  "overlays": [{"set": "applicants", "person_id": "p0041", "x": 0, "y": 0, "shares": [{}, {}]}],
  "bounds": {"xmin": -6, "xmax": 6, "ymin": -5, "ymax": 7}
}
```

- `levels` and `nodes` come from `themes_applied.json` of the map (nodes in
  tree order, with their map position, the mean of their people's).
- `shares` is one `{node id: share}` per level, from the top: the **usage
  share** of the person (the organisation, the time window, the projected
  person) that counts toward each node of that level; each level sums to 1
  where there is usage. A keyword's `node` is `null` when it is set aside.
- The `ETag` depends on the format and the lineage; `If-None-Match` gives 304.

The atlas page (`/map`) reads, beside the engine's results, what the tables
say (`cartolex.app.atlas_layers`), so the `ETag` also follows the tables:

- `organisations`: every organisation (`id`, `name`, `acronym`, `level`,
  `parents`, `location` `{lat, lon}` or null), placed (`x`, `y`) at the mean of
  the **mapped people affiliated to it now**, directly or through an
  organisation below it; `members` counts them, `members_ever` counts the
  people of the corpus ever affiliated to it (the same way, past affiliations
  included). `organisation_levels`: the project's levels, smallest first, with
  their names and how many organisations each has.
- `columns`: each extra column of the people on the map with its values and
  counts (a column with one value, or more than 200, is left out);
  `people_extra`: person id → `role`, `columns`, `orgs` (current
  organisations); `years`: the first and last year of the texts; `bases`: the
  project's base maps.
- `?base=<id>` answers the bundle placed on a base map: each keyword the base
  has at its place there; each person at the mean of their keywords' places
  there, weighted by use; keywords the base lacks, people without a shared
  keyword, units, time windows and projected people are not placed; `base`
  holds the base's name, map version, the shared keywords' count, its people's
  places and its top-level themes, and `bounds` are the base's.
- `GET /api/atlas/texts` places each text at the mean of the map's keywords
  found in its title and abstract (runs of up to six words, keyword aliases
  included; `by: 0`), else at the mean of its authors on the map (`by: 1`);
  `terms` indexes `keywords` of the bundle.

### The theme tree

| route | what it does |
| --- | --- |
| `GET /api/themes` | the saved tree (`source: saved`), else the grouping's proposal read from `themes.group/themes_draft.json` (`source: draft`), else none; beside it `based_on_current`, the vocabulary's gaps, `space_run`, and `proposal`: `{run, pending, same_vocabulary}` |
| `GET /api/themes/draft` | the grouping's latest proposal, whatever tree is saved: `{run, tree}` (`no_proposal` before the first grouping) |
| `GET /api/themes/usage` | each keyword of the current vocabulary: `{term: [people, weight]}` (how many people use it; the sum of its share of each person's usage), `people` counted; `ETag` by the space's run |
| `POST /api/themes/ops` | `{tree, ops, lenient}`: the operations applied in order; each step is `{op, description}`, or with `lenient` a refused step is skipped and reported as `{op, refused}` |
| `POST /api/themes/borderline` | `{tree, level, reviewed, offset, limit, sort, q}`: the placed keywords of the tree sent, paged, smallest margin first: `keyword`, `node` (its node at the level compared: `level`, else its own node's), `other` (the nearest other node of that level), `own` and `near` (cosines to the two nodes' centroids in the space, the keyword left out of its own) and `margin` (`own − near`; negative: nearer the other node); `negative` counts those. Keywords marked `kept` (« keep here » in this list) are left out unless `reviewed`; a `reviewed` from the queue of a rebase does not hide a keyword never judged here. `no_space` before the space is built; the measure is in `cartolex.lexicon.theme_fit` |
| `POST /api/themes/suggestions` | `{tree, keywords, top, scope}`: for each keyword (default: the set-aside ones and those « to check », `scope` `aside`, `check` or `both`), the `top` nodes (at most 10, default 3) holding keywords whose centroid is nearest: `{suggestions: {keyword: [{node, score}]}}`, *score* the cosine |
| `POST /api/themes/compare` | `{before, after, limit}`: every difference (`cartolex.project.themes.compare`), with `total` and `counts` by kind |
| `POST /api/themes/rebase` | rebases the saved tree onto the current vocabulary now, as an apply does first (send `If-Match`): `{written, notes, version, to_check, tree}` |
| `POST /api/themes/proposal` | `{decision: adopt \| keep, run}` (send `If-Match`): agree once on a new grouping of the same vocabulary; `adopt` saves the proposal, `keep` records that the tree was kept over it (`based_on.run`); `proposal_changed` when a newer proposal replaced `run` |

A **clustering-only change** (the grouping ran again with other parameters, on
the same vocabulary) leaves the saved tree as it is. `proposal.pending` is true
until someone adopts the new proposal or keeps the tree over it: the tree
names the grouping it agreed with in `based_on.run` (a tree saved from a
proposal, adopted or kept), else the grouping the last apply read (after a
rebase).

### The theme handoff

The theme curation by handoff follows the keyword handoff
(`cartolex.project.themes_handoff`, format `cartolex-themes-handoff/1`): each
part is `prompt.txt` (to paste), `tree.txt` (to attach), `expected-answer.txt`
and `bundle.json` (the tree as it was sent). `tree.txt` holds the tree (node
ids, names, levels), each node's most used keywords with how many people use
each, the set-aside tray, and the project's description labelled as the
assistant's context; never texts, people or their names, or keys. A tree too
large for `max_tokens` is cut by top-level nodes: every part holds the whole
outline and the keywords of some top-level nodes. The answer is one operation
per line:

```text
1 | RENAME | s3 | Coastal hazards | its keywords are floods, surges and erosion
2 | MOVE | tide gauge | s5 | an instrument of sea-level observation
3 | MERGE | s7 | s2 | both hold harbour management keywords
4 | SPLIT | s4 | Salt marshes | salt marsh; marsh accretion | a distinct group
5 | SET ASIDE | further work | not a keyword of the field
6 | ATTRIBUTION | ocean | 0 | too broad to count toward one theme
```

| route | what it does |
| --- | --- |
| `POST /api/themes/handoff/export` | `{tree, top, max_tokens}` (default: the saved tree, else the proposal): the parts, what they contain and never contain |
| `POST /api/themes/handoff/export.zip` | the same parts as a zip, one folder per part with its `bundle.json` |
| `POST /api/themes/handoff/import` | `{bundle, answer}`: keeps the answer in `decisions/history/ai/<time>-themes.txt` (and the bundle beside it), reads it into proposed operations (`items`: `number`, `verb`, `op` in the form of `POST /api/themes/ops`, `reason`, `refused` when it cannot apply to the tree sent) and the lines it could not read (`unreadable`: `line`, `text`, `problem`); nothing changes in the tree. The first AI answers freeze the identity |
| `GET /api/themes/handoff/proposals` | the imported answers, newest first |
| `GET /api/themes/handoff/proposals/{id}` | one of them, read again |

The editor shows a proposal as a list to accept or reject, previews the
accepted operations on the tree, and applies them through
`POST /api/themes/ops`, so they are undone like any other edit. An answer's
unreadable lines have a `problem`: `unknown_action`, `missing_fields`,
`unknown_node`, `unknown_keyword`, `bad_levels`, `empty_name` or `same_node`.

### The AI copilot

The copilot's bundle is a zip an assistant able to run code works from on its
own; its result comes back as one file. Its format, the kit and the routes
(`/api/themes/copilot/…`, `/api/keywords/copilot/…`) are in {doc}`copilot`.
An imported themes result is reviewed like a theme handoff's answer; a triage
result is a keyword proposal (`GET /api/handoff/proposals/{id}` and its
`accept` take the ids of both).

### Errors of the theme editor

| code | status | message | next |
| --- | --- | --- | --- |
| `no_proposal` | 404 | the grouping has proposed no tree yet: build the themes first | `build` |
| `proposal_changed` | 409 | a newer proposal ({run}) replaced the one you saw: look at it first | `reload` |
| `theme_handoff_empty` | 404 | the tree holds no keyword to send | — |
| `no_space` | 409 | the keywords have no space yet: build the themes first | `build` |
| `invalid_theme_bundle` | 422 | this is not a theme bundle of cartolex: {detail} | `fix-input` |
| `invalid_copilot_result` | 422 | this is not a copilot result of cartolex: {detail} | `fix-input` |

**Keeping over an unanswered proposal, and the versions' names.** When a
new grouping of the same vocabulary waits for an answer
(`GET /api/themes` → `proposal.pending`), `POST /api/themes/apply` first keeps
the saved tree over it, as a new version whose action is « keep the curated
tree over the proposal `<run id>` at an apply », and its answer adds
`kept: {run, action, version}`. `GET /api/themes/versions` gives each version
`names` and `names_after`: the names of the nodes its action names, in the
version before it and in the one it made (a node merged away is named by the
version that still had it).
