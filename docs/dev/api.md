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
languages, reference}` creates a project, with the extensions' slots,
projected sets and identity, and opens it.

**State and building**

| route | what it does |
| --- | --- |
| `GET /api/project/state` | every stage's state (the six states, as keys: `up_to_date`, `needs_update`, `never_built`, `running`, `failed`, `skipped`) with its reasons, last run and last failed attempt, grouped in areas (corpus, keywords, themes, map, share, and the extensions'), each area summing up its stages; derived from the run records alone |
| `POST /api/build {scope, options: {force, allow_over_budget}, dry_run, consent}` | `scope`: stage ids or areas, everything by default. The dry run (the default) answers the plan: each stage's action, reasons, estimate, whether it asks consent, and the consent requests. `dry_run: false` starts a job (202); a stage that asks consent runs only when listed in `consent` |
| `GET /api/build` | the tracker: the running or last build job (its progress: phase, stage, fractions, ETA, message) and each of its stages, done, running or waiting, with counts and times; its result says what changed |
| `GET /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events?after=n`, `POST /api/jobs/{id}/cancel` | jobs |

**Parameters and map versions**

| route | what it does |
| --- | --- |
| `GET /api/params` | per stage, each parameter's effective value, origin (`default`, `rule` with the rule, `params.json`), limits, the value of its last run and whether it changed since; the validation messages; the sizes the rules use |
| `PUT /api/params {seed, pinned_year, stages}` | replace `decisions/params.json` (`If-Match`); refused values: 422 with every reason |
| `GET /api/map/versions` | the versions, newest first, and the pinned one |
| `POST /api/map/versions {action: pin | try | discard, version, seed, note, build}` | pin a version, try another layout (a new version beside the pinned one), discard a version nobody pinned; `build: true` also redraws the map |

**Snapshots**: `GET /api/snapshots` (each decision file's versions and each
stage's current and previous generation), `GET /api/snapshots?file=themes.json`,
`GET /api/snapshots/{file}/{version}` (its content), and
`POST /api/snapshots/{file}/{version}/restore` (`If-Match`; a restore is a new
version, so it can be undone too).

**People and collection**

| route | what it does |
| --- | --- |
| `GET /api/people` | people with their role, set, identity state, records, unit and coverage (texts, texts with an abstract, years, and a class: good, thin, none); filters `role`, `identity`, `set`, `coverage`, `q`; counts per role, identity and coverage |
| `PATCH /api/people {person_ids, role, set, note}` | roles, sets, notes (`If-Match`) |
| `POST /api/people/merge {target, sources}` | rows that are one person |
| `POST /api/people/import` | a CSV file (a form's `file`) or `{"text": …}` (a pasted list, one person per line): kept outside the project until confirmed, and a mapping proposal (column → `last_name`, `first_name`, `name`, `orcid`, `email`, `unit`, `column`, `ignore`) with a preview |
| `POST /api/people/import/{id}/confirm {mapping, role, set}`, `DELETE /api/people/import/{id}` | add the people (`If-Match` of the people), or forget the import |
| `GET /api/collection/plan` | what a collection would do and **what leaves the computer**, and what never does |
| `POST /api/collection/start`, `GET /api/collection`, `POST /api/collection/cancel` | the collection job |
| `GET /api/collection/identities?state=pending` | the identity queue: each person with candidate records and their evidence |
| `POST /api/collection/identities/{person_id} {decision: accept | none | id, record}`, `POST /api/collection/identities/accept {person_ids}` | decide one identity, or accept the best candidates of many |
| `GET /api/collection/coverage` | coverage per class and per role |
| `GET /api/sources`, `GET /api/sources/{slot}/files`, `POST /api/sources/{slot}/files` | a folder or corpus slot's files; upload a document or a zip archive into `sources/<slot>/` |

**Keywords, themes, atlas**

| route | what it does |
| --- | --- |
| `GET /api/keywords` | the candidates of the current extraction in their three bands (`kept`, `check`, `aside`) with the reason of each, your decisions applied; filters `band`, `lang`, `decision`, `q`; counts per band; decisions whose keyword disappeared (listed, never dropped) |
| `POST /api/keywords/decisions {decisions: [{term, language, decision, target, reason}]}` | keep, exclude or merge, one or many (`If-Match`); the first decision freezes the identity |
| `POST /api/keywords/restore {keywords}` | undo decisions (an exclusion restored) |
| `GET /api/themes` | the saved tree, or the draft of the last grouping, with the keywords the tree lacks or holds too many |
| `POST /api/themes/ops {tree, ops}` | apply operations of `cartolex.project.themes` (`rename_node`, `rename_level`, `move_keywords`, `move_node`, `merge_nodes`, `split_node`, `create_node`, `delete_node`, `set_aside`, `put_back`, `set_review`, `set_attribution`, `prune_empty`, `insert_level`, `remove_level`) to a tree; answers the new tree and each step's description (the names of the undo list). Nothing is saved |
| `PUT /api/themes {tree, action}` | save a new version (`If-Match`); empty nodes are removed and named in the action |
| `GET /api/themes/versions`, `GET /api/themes/versions/{id}`, `POST /api/themes/versions/{id}/restore` | versions |
| `POST /api/themes/apply` | a build job of the themes and the map |
| `GET /api/atlas` | what the map draws at any depth of the theme tree (levels, nodes, people, keywords, units, trajectories, projected people, bounds), `cartolex-atlas/2` (described below, with the theme editor's routes), cached by its lineage (the runs it is made from), with an `ETag` |

**Sharing, settings, the AI handoff**

| route | what it does |
| --- | --- |
| `GET /api/share`, `POST /api/share/builds` | the site builds in `outputs/sites/`; building a site comes in a later version (501 until then) |
| `GET /api/settings`, `PUT /api/settings` | languages, language models, the AI identity (with what changing a frozen one costs: 409 `identity_frozen` unless `confirm_identity_change`), slots, projected sets, levels, data sources |
| `POST /api/handoff/export {band, terms, lang, limit, max_tokens}` | the parts of a handoff (`cartolex.project.handoff`): for each, the prompt to paste, the terms to attach and the answer's format, its `bundle.json` (`cartolex-handoff/1`, sent back with the answer), and what they contain and never contain; parts stay under `max_tokens` (a chat assistant reads a limited amount at once) |
| `POST /api/handoff/export.zip` | the same parts as a zip, one folder per part |
| `POST /api/handoff/import {bundle, answer}` | keep the answer as it came in `decisions/history/ai/` (with the part it answers) and propose a decision per answered term, with what could not be read (lines ignored, renumbered, unmatched); the first answers freeze the identity |
| `GET /api/handoff/proposals`, `GET /api/handoff/proposals/{id}`, `POST /api/handoff/proposals/{id}/accept {terms, all}` | proposals; accepted ones reach `keywords.csv` with the source `ai-handoff` |

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
`confirm_import()`, `plan()`, `collect()` (in a job) and `candidates()`.
Until the collection services land, `UnavailableCollection` (the default)
imports lists but collects nothing, and `DemoCollection(world)` answers from a
demo world without leaving the computer — the tests and demonstrations use it.
The site builder is a `SiteBuilder` protocol (`cartolex.app.share`) with a
stand-in that lists earlier builds.

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
| `map_version_not_found` | 404 | there is no map version {version} | `version` | `reload` |
| `map_version_missing` | 422 | name the version to {action} | `action` | `fix-input` |
| `map_version_pinned` | 409 | {version} is pinned: pin another version before discarding it | `version` | `fix-input` |
| `no_pinned_version` | 409 | there is no pinned map version to start from: build the map first | — | `build` |
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
| `no_candidates` | 409 | none of these people has a candidate record | — | `none` |
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
| `collection_unavailable` | collecting texts from bibliographic services is not available in this version; import texts into a folder or corpus slot instead | — | — |
| `stage_switched_off` | switched off (set {stage}.enabled in decisions/params.json to run it) | `stage` | — |
| `stage_no_overlay` | the project has no overlay | — | — |
| `stage_not_applicable` | {reason} | `reason` | — |
| `stage_cancelled` | the stage was cancelled; its previous results are kept | — | — |
| `stage_refused` | the stage could not run: {detail} | `detail` | — |
| `language_model_missing` | a language model is missing: {detail} | `detail` | — |
| `stage_failed` | the stage failed ({error_type}): {detail} | `error_type`, `detail` | — |
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

### The theme tree

| route | what it does |
| --- | --- |
| `GET /api/themes` | the saved tree (`source: saved`), else the grouping's proposal read from `themes.group/themes_draft.json` (`source: draft`), else none; beside it `based_on_current`, the vocabulary's gaps, `space_run`, and `proposal`: `{run, pending, same_vocabulary}` |
| `GET /api/themes/draft` | the grouping's latest proposal, whatever tree is saved: `{run, tree}` (`no_proposal` before the first grouping) |
| `GET /api/themes/usage` | each keyword of the current vocabulary: `{term: [people, weight]}` (how many people use it; the sum of its share of each person's usage), `people` counted; `ETag` by the space's run |
| `POST /api/themes/ops` | `{tree, ops, lenient}`: the operations applied in order; each step is `{op, description}`, or with `lenient` a refused step is skipped and reported as `{op, refused}` |
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

### Errors of the theme editor

| code | status | message | next |
| --- | --- | --- | --- |
| `no_proposal` | 404 | the grouping has proposed no tree yet: build the themes first | `build` |
| `proposal_changed` | 409 | a newer proposal ({run}) replaced the one you saw: look at it first | `reload` |
| `theme_handoff_empty` | 404 | the tree holds no keyword to send | — |
| `invalid_theme_bundle` | 422 | this is not a theme bundle of cartolex: {detail} | `fix-input` |

**Keeping over an unanswered proposal, and the versions' names.** When a
new grouping of the same vocabulary waits for an answer
(`GET /api/themes` → `proposal.pending`), `POST /api/themes/apply` first keeps
the saved tree over it, as a new version whose action is « keep the curated
tree over the proposal `<run id>` at an apply », and its answer adds
`kept: {run, action, version}`. `GET /api/themes/versions` gives each version
`names` and `names_after`: the names of the nodes its action names, in the
version before it and in the one it made (a node merged away is named by the
version that still had it).
