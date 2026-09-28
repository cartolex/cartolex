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

Every error has the same shape (V2-016): a stable `code`, a `message` in
plain words, and `next`, what to do: a `label` and an `action` key the
interface maps to a route or a command (`reload`, `retry`, `confirm`,
`fix-input`, `open-project`, `sign-in`, `wait`, `build`, `unlock`,
`settings`, `report`, `none`). Some errors add fields (`current` for a stale
write, `problems` for refused values, `job` for a busy project).

```json
{"error": {"code": "stale", "message": "themes.json changed since it was read; reload it and apply the change again",
           "next": {"label": "Reload", "action": "reload"}, "current": "sha256:…"}}
```

A list or a result with nothing in it says what to do next (V2-013):
`"empty": {"message": "no keywords yet: build the keywords first", "next": {"label": "Build the keywords", "action": "build"}}`.

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
| `GET /api/atlas` | what the map draws (people, keywords, themes, topics, units, trajectories, projected people, bounds), `cartolex-atlas/1`, cached by its lineage (the runs it is made from), with an `ETag` |

**Sharing, settings, the AI handoff**

| route | what it does |
| --- | --- |
| `GET /api/share`, `POST /api/share/builds` | the site builds in `outputs/sites/`; building a site comes in a later version (501 until then) |
| `GET /api/settings`, `PUT /api/settings` | languages, language models, the AI identity (with what changing a frozen one costs: 409 `identity_frozen` unless `confirm_identity_change`), slots, projected sets, levels, data sources |
| `POST /api/handoff/export {band, terms, lang, limit}` | a bundle of terms with their evidence (`cartolex-handoff/1`, `cartolex.project.handoff`), its text with the instructions to paste, and what it contains and never contains |
| `POST /api/handoff/import {bundle, answer}` | keep the answers as they came in `decisions/history/ai/` and propose a decision per answered term; the first answers freeze the identity |
| `GET /api/handoff/proposals`, `GET /api/handoff/proposals/{id}`, `POST /api/handoff/proposals/{id}/accept {terms, all}` | proposals; accepted ones reach `keywords.csv` with the source `ai-handoff` |

**The interface**: `/static/…` (the interface's files), `/static/ext/<id>/…`
(an extension's), and any other page path answered with the shell
(`index.html`).

## Collection behind a protocol

Importing people and collecting texts go through a `CollectionService`
(`cartolex.app.collection`): `describe()`, `propose_import()`,
`confirm_import()`, `plan()`, `collect()` (in a job) and `candidates()`.
Until the collection services land, `UnavailableCollection` (the default)
imports lists but collects nothing, and `DemoCollection(world)` answers from a
demo world without leaving the computer — the tests and demonstrations use it.
The site builder is a `SiteBuilder` protocol (`cartolex.app.share`) with a
stand-in that lists earlier builds.
