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
cartolex                         # the app on a loopback port, in the browser (the last project)
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
  than 16 MB (uploads: 50 MB; a copilot's result: 256 MB) is refused before it is read. Uploads are
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
`fix-input`, `open-project`, `sign-in`, `wait`, `build`, `settings`,
`report`, `none`). The server does not translate: the interface
shows the text of the code from its catalogues (English, French, Portuguese),
filled with the params, and falls back on `message`. Every code is declared
once, with its English text, in `cartolex.app.errors.ERRORS` (listed below);
a test fails when a route raises a code that is not there or leaves out a
param its text names. Some errors add fields (`current` for a stale write,
`job` for a busy project).

```json
{"error": {"code": "stale", "params": {"file": "themes.json"},
           "message": "this changed elsewhere since it was read; reload it and apply the change again",
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
same interface. A build of cartolex's stages runs in a process of its own
(`cartolex.app.build_run`): the child writes under the app's project lock (as
its *holder*), sends its progress back and stops on a cancel or when the app is
gone, and its memory goes back to the computer when it ends; a child the
computer stops (most often for want of memory) fails the job with that reason.
A registry or an AI access given in code, or `AppSettings(build_in_child=False)`,
keeps the build in the job's thread. One job runs per project at a time: a second build or
collection is refused with 409 `busy`, naming the running job. A job's states
are `queued`, `running`, `cancelling`, `succeeded`, `waiting` (a build that
ended at an AI step whose route is a copilot, waiting for its result: not a
failure), `paused` (a long job stopped with its state saved, to be resumed: not
a failure), `failed`, `cancelled` and `interrupted`. A cancel stops at the next safe point, and the result says
« nothing changed » or « finished before the cancel »; a job that keeps
checkpoints pauses instead.

A failed job says why. Its `error` is
`{code, params, message, exception, detail, step, progress, traceback}`: the cause's
code (`collect_budget_spent`, `collect_service_unavailable`,
`collect_incomplete`, `collect_malformed`, `collect_refused`,
`collect_cache_miss`, else `job_failed`) with its params and English words,
the exception's class and a short message (300 characters at most), the stage
it was in, its last progress, and the traceback (of the failed stage, or of the
build's process; at most 8,000 characters, its end, with the home folder written
`~`; also in the job's log). The interface's error card shows the code's words,
and « Copy a diagnostic » carries the job's id, the time it failed, the system,
the class, the message, the step and the traceback.
A spent daily budget (`collect_budget_spent`, `keyed` among its params) names
its next action: without a key, a free one (`open:/settings?section=sources`);
with one, waiting for the next day's budget (`none`).

A paused job's `result` is `{outcome: "paused", pause}`; `pause` is
`{code, params, message, checkpoint, progress, cause}`: why it paused
(`collect_size_confirm`, `collect_stopped`, `collect_paused`, `collect_budget_paused`), how far it got,
the checkpoint to resume from, and the error that stopped it (shaped as a
failed job's `error`), if any. The work of a job raises
`cartolex.project.checkpoints.JobPaused` after saving a `Checkpoint`; a new
job given the checkpoint goes on from it (for a collection,
`POST /api/collection/start {action, resume: <checkpoint>, consent: true}`).
The institutions' proposal is the first job built this way; the people import's
identification, the harvest, the text providers' improvement and the
collaborators' rounds are long collections that should use it next.

Every job writes `logs/jobs/<job id>.jsonl` in its project: a `job` line (its
kind, the process, a digest of the machine's name and its boot), the build's own events (phases,
stage ends with counts and times) and a `job-end` line (its state, and the
`error` of a failed job or the `pause` of a paused one) — never a name or a
text; a collection adds one `egress` line per host whatever its end. After a restart, a job whose log has no end and whose process is gone is
`interrupted`, never `running`.

## Logs and the diagnostic

The app logs JSON lines through `logging` (`cartolex.app`): each request with
its id, method, route **template** (`/api/jobs/{job_id}`, never the values or
the query), status and time. The server installs the formatter
(`cartolex.app.logs.configure_logging`); creating an app changes no logging
setting. `GET /api/diagnostic` gives the versions (Python, cartolex and its build, the key
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
| `POST /api/presence {page, bye}` | a page of the interface says it is open, or (`bye`) closing; a local app started with a browser stops once none is open and no job runs (manifest capability `idle_stop`, `cartolex app --idle-stop`) |
| `GET /api/app/about` | what the About page shows: `version`, `build` (`{commit, date}` or `null`), `authors`, `licence`, `source` (the repository's address) and `citation` (how to cite cartolex), from the package's metadata |
| `GET /api/diagnostic` | versions and build, machine, recent job events |
| `GET /api/openapi.json` | this API's description |
| `GET /launch?token=…` | the launch link |

**Projects**: `GET /api/projects/current`, `POST /api/projects/open {path, force}`
(`force`: override a lock held elsewhere, after a warning),
`POST /api/projects/close`, `GET /api/projects/recent` (locally);
`POST /api/projects/forget {path}` takes a project out of the recent list (its folder
stays; answers the list); `GET /api/projects/removal?path=` says what deleting a recent
project's folder would remove (`path`, `name`, `bytes`, `files`, `kept`: the entries
cartolex did not write, which stay with the folder, `open`: the project open here, `held`:
the application that holds it elsewhere) and `POST /api/projects/delete {path, confirm}`
deletes it (locally, `cartolex.app.removal`): the open project is closed first, then,
under the project's lock, what cartolex writes at a project's root (`project.json` last),
links removed as links and never followed, and the folder when nothing else is left in it
(`deleted`, `folder_removed`, `kept`, `closed`). Refused: a folder not in the recent list
(`project_not_listed`), not a cartolex project, a link, the home folder or one holding it
or the app's own folder (`project_delete_refused`, `reason`: `not_a_project`, `link`,
`missing`, `protected`), without `confirm` (`project_delete_confirm`), while another
application holds the project (`project_delete_held`, never overridden) or a job runs on it
(`busy`), and on a hosted service (`project_delete_hosted`); a file the system refuses
stops it (`project_delete_failed`, the project still opens);
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
| `GET /api/project/state` | every stage's state (the six states, as keys: `up_to_date`, `needs_update`, `never_built`, `running`, `failed`, `skipped`) with its reasons, last run and last failed attempt, grouped in areas (corpus, keywords, themes, map, share, and the extensions'), each area summing up its stages; derived from the run records alone (a cancelled attempt is listed, `outcome: cancelled`, without making the stage failed; an attempt carries its `finished_at`); the AI clean-up's row (`keywords.triage`) adds `ai`, the route and the copilot's work (`route`, `extraction`, `api_verdicts`, `decisions` and `last`: the copilot's decisions accepted since the current extraction, `total`, `ai`: every accepted AI answer, `reviewed`: decisions of any source since the extraction, `pending`: copilot results imported and not accepted), and, when it is skipped, says `stage_copilot_done` (shown up to date), `stage_copilot_waiting`, `stage_copilot_earlier` (no AI route: the copilot's decisions predate the extraction and still apply) or `stage_ai_none`, with the word to show as `ai_state` (`copilot_done`, `copilot_waiting`, `copilot_earlier`, `none`); `changed_params`: how many values differ from their defaults, per stage id, `build` (the seed and the pinned year) and `layout` (the pinned map version), for the pages' « Tune » panels |
| `POST /api/build {scope, options: {force, allow_over_budget}, dry_run, consent, continue}` | `scope`: stage ids or areas, everything by default. The dry run (the default) answers the plan: each stage's action, reasons, estimate, whether it asks consent, the consent requests, the route of each AI step (`ai`: `routes`, `choices`, `api_ready`, `version`, the version of `params.json`, and `triage`, the AI clean-up's status as the project state gives it) and where the build pauses for a copilot (`pause`: `step`, `after`, `resumes`, `page`, the copilot's page, and `held`, the stages it holds back; `null` when it does not). A build pauses at an AI step whose route is a copilot when the stage after it runs and either the stage before it runs in the same build or no copilot result was accepted since that stage's run: it runs what comes before and ends `waiting`, its result's `waiting` naming the pause; `continue` lists the steps (`keywords.triage`, `themes.curation`) it goes past. `dry_run: false` starts a job (202); a stage that asks consent runs only when listed in `consent`; without it, an opt-in stage (the AI clean-up; its request says `skipped_without: true`) is skipped as if switched off and the stages after it run, any other is refused with those after it. The dry run's `notes` are messages with a next action: `preflight_no_texts` (mapped people whose texts were never collected, when the texts are gathered again), `preflight_copilot_pending`, `preflight_api_dropped` (the API's verdicts exist and another route is chosen: they no longer gate the vocabulary), `preflight_copilot_new` (the extraction runs again with no AI route after a copilot's triage: the new candidates enter by their bands). The AI clean-up's item, skipped, has `ai`: the row as the project state will show it once the build ends (`state`, `ai_state`, `skip`) |
| `PUT /api/build/ai {keywords.triage, themes.curation}` | the route of each AI step, kept in `params.json` (`ai`): `none`, `copilot` or, for the keyword clean-up only, `api` (which switches `keywords.triage` on; the theme curation has no API route); a step left out keeps its route; `If-Match` with the version of `params.json`; answers as the dry run's `ai` |
| `GET /api/build` | the tracker: the running or last build job (its progress: phase, stage, fractions, ETA, message) and each of its stages, done, running or waiting, with counts and times, and `ai`, the AI clean-up as the project state shows it (as the pre-flight's item); its result says what changed. A failed stage's result carries its code, params and `next` (the settings for a missing language model or a refused stage, a diagnostic otherwise); a consent request of a paid stage gives `ai_calls_max`, the most AI calls it makes when the candidates are known, and `ai_tokens` (`in`, `out`, `calls`: the tokens sent and received at most, answers already paid for left out; `null` when the extraction runs first). A build job's result has `ai_usage` (`tokens_in`, `tokens_out`) when a stage that ran called an AI provider: the tokens it reported; the stage's run counts keep them too |
| `GET /api/overview` | what the overview adds to the state: `project` (id, name, the state of the whole), `next` (the one most useful next step: a running job (a build to follow, any other in the Activity drawer), a build waiting for a copilot, people to add, a model to install, a failure only while it is the last thing tried, before the texts are gathered the roles, the identities and the harvest, a first build, a stale map, an update, the keyword review when no decision was made since the extraction, the themes, the map, sharing), `health[]` (a stale map, a missing language model, a stage too large for this machine, several languages without the AI clean-up (no API verdict and no AI answer accepted, once the candidates are found), a proposal of collaborators cut at its cap, a copilot result imported and not accepted, and once the texts are gathered the people whose identity waits or whose texts were never collected), `quiet_failures` (the failed stages a later job came after: stage id → the attempt's time), `steps` (« Your first map »: `project`, `people`, `identities`, `texts`, `build`, `review`, `themes`, `map`, `share`, each `done` or `todo`, with `n` where its words count people), `checklist` (`key`, the person's preference that hides it, and `hidden`), `people` (the counts the guidance reads, from the people's view: `people`, `mapped`, `with_texts`, `without_texts`, `identities`, `to_harvest`), `preview` (an even sample of at most 1 500 of the map's people, `[x, y, top-level theme index]`, with the top-level themes and the bounds; `null` without a map), `lexicon` (`{run}`, the vocabulary build the word cloud is drawn from; `null` before one) and `shares` (the three latest site builds). Each item is a message (`code`, `params`, `message`), a `level` (`info`, `warning`) and a `next` action; a build action may carry `scope`, the areas the build covers |
| `GET /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events?after=n`, `POST /api/jobs/{id}/cancel` | jobs; a job's `title` comes with `title_code` and `title_params` (`job.title.<code>`), a result's `summary` with `summary_code` and `summary_params` (`job.summary.<code>`) |

**Parameters and map versions**

| route | what it does |
| --- | --- |
| `GET /api/params` | per stage, each parameter's effective value, origin (`default`, `rule` with the rule, `params.json`), limits, the value it would have without `params.json` (`default_value`, and `differs`), the value of its last run and whether it changed since; its `tier` (`essential`, `intermediate`, `advanced`), its control (`widget`: `switch`, `choice`, `slider`, `number`, `text`, `chips`, `order`, `range`, `levels`, `grid`), the `keys` of a list given per slot kind and the `suggestions` of an open list; the validation messages; the sizes the rules use; `global`: the `seed`, the `pinned_year` and the `similarity` (`value`, `default`, `choices`) |
| `PUT /api/params {seed, pinned_year, stages}` | replace `decisions/params.json` (`If-Match`); refused values: 422 with every reason |
| `PUT /api/params/similarity {measure}` | how people and organisations are compared (`params.json`'s `similarity`, `If-Match`): `space` (the cosine of their vectors in the space of the themes, the default), `keywords` (the cosine of their keyword profiles), `jaccard` (their keywords in common) or `themes` (Σ min of their top-level theme shares), each from 0 to 1 (`cartolex.app.similarity`); it drives Compare's headline, the nearest and the distances' exports, and needs no rebuild; answers as `GET /api/params` |
| `GET /api/map/versions` | the versions, newest first, and the pinned one |
| `POST /api/map/versions {action: pin | try | discard, version, seed, method, params, note, build}` | pin a version, try another layout (a new version beside the pinned one, with another seed or `method`: `umap`, `tsne` when openTSNE is installed, `tree`, and layout `params` set over the pinned version's for the same method, else over the method's defaults; a parameter the method does not take: 422 `layout_param_unknown`), discard a version nobody pinned; `build: true` also redraws the map. `GET` gives `methods` (always `umap`, `tsne`, `tree`), `unavailable` (a method this installation cannot draw, with the error `layout_method_unavailable`: `code`, `params`, `message`, `next`; `try` with it is refused with that error) and the default rule (`default_method`: t-SNE from `tsne_from_people` mapped people) |
| `GET /api/recipe` | the recipe of the build, read-only: `rows` in pipeline order (the build's seed and pinned year, `group` `build`; each stage's parameters, `group` the stage id; the pinned map version's method, seed and layout parameters, `group` `layout`), each with `value`, `default_value`, `from` (`default`, `rule` with `rule` and `rule_description`, `params.json`, `version`), `differs`, `tier` and `panel` (the page that edits it: `texts`, `keywords`, `themes`, `map`); `changed`, `valid`, `problems`; `ai_usage`, the tokens the AI clean-up by API spent (`null` before any: `last`, the current run's `run`, `at`, `tokens_in`, `tokens_out`; `total`, every run of the project from `cache/ai/usage.json`, with `updated_at`); `ETag` of `params.json` |
| `GET /api/recipe/export?format=md\|csv&language=` | the recipe as a download: Markdown (one table per stage, changed values marked `*`) or CSV (`step, parameter, label, value, default, origin, differs, tier`; lists and objects as JSON), the labels in an interface language (English otherwise) |
| `GET /api/method/{step}` | the diagnostic of a step (shown in its page's « Tune » panel), read from its stages' outputs and cached by their runs (`step`, `stages`, `runs`, and `empty` before the stage ran): `texts` (people, texts, characters, mapped people), `keywords` (the candidates by band, reason and language, a histogram of their scores by band on a log scale, the vocabulary against `max_keywords` (`kept_keywords`, and `scored`: the scored list the cap cuts, so the cap is reached when `scored` exceeds it), the scoring's settings this version fixes, `fixed`), `space` (the variance each dimension explains, and the share of each person's 10 nearest people by TF-IDF kept by the first dimensions, on a sample of at most 800; the space's `unit`; `notes`: `health_space_languages` when a space of texts has 10 % or more of its keywords outside the reference language), `grouping` (per level: groups, keywords, keywords per group; too broad; the outline of the top two levels; Ward's `dendrogram` of the top-level themes; the comb's `calibration`: for each θ of its grid the keywords per level and per group, the too broad and the balance error, and the θ kept), `layout` (the pinned version, the nearest people the map keeps, the layout's measures, the parameters each method offers, the previews computed on this space) |
| `GET /api/method/keywords/preview?min_people=&min_texts=&max_share=&max_keywords=` | what these thresholds would keep of the last build's candidates and vocabulary, read from the stored candidates and nothing saved: `built` (the last build's values) and `used`, the candidates `before`/`after` and by band, the `vocabulary` counted as `GET /api/method/keywords` counts it (`kept_keywords`, the keywords someone's row of the space holds): `before`, and `after` when exact, else `null` with `after_low`/`after_high` (a term new to the scored list enters when someone uses it, which only a rebuild says; with a number of keywords per person, a row that loses a term takes its next one, and `after_low` is `null` when terms new to the scored list could push kept ones out), the strongest candidates that would leave (`leaving`, each with its `cause`), the kept keywords that would leave (`vocabulary_leaving`) and the scored terms nobody lists yet that could enter (`vocabulary_entering`). Only a stricter window can be previewed (a larger `min_people` or `min_texts`, a smaller `max_share`; `max_keywords` both ways, on the full scored list the build keeps): a looser value is named in `needs` (`preview_needs_extraction`) and the last build's value used instead. Before the extraction: `empty` (`empty_no_keywords`) |
| `POST /api/method/layout/preview {method, seed, params}` | a layout of a sample of the people (at most 800) with a method and its parameters, beside the map on the same people, each with the nearest people it keeps: 200 `{preview}` when it was computed on the current space, else 202 `{job}` (a job of the group `preview`, beside builds); send the same request once the job ends. `tsne` without openTSNE: 422 `layout_method_unavailable` (`GET /api/method/layout` lists every method and the `unavailable` ones with that error, and gives each layout parameter its `tier`); before the space (or, for `tree`, the applied themes): 409 `preview_needs_build` |
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
| `POST /api/people/merge {target, sources, override, note}` | rows that are one person (`If-Match`): each source's `merged_into`, nothing else of its row changes (its records, identity and role stay, for an unmerge); a person stands for the rows merged into them (their records for the collection, their texts in the corpus, the coverage and the sheet). Two sides with different ORCIDs: 409 `merge_orcid_conflict` unless `override` |
| `POST /api/people/unmerge {person_ids, remember}` | undo merges (`If-Match`): each row named, or merged into a person named, stands on its own again; `remember` (`distinct` or `later`) records each pair undone in `people_pairs.csv` |
| `GET /api/people/duplicates?show=open\|clear\|later\|all` | pairs of people who may be one person (`cartolex.collect.duplicates`), the most likely first, paged: each with its `evidence` (`code`, `params`, English `text`, `points`), `score`, `clear` (the automatic merge takes it), `conflict` (two different ORCIDs), the `decision` remembered (`later`) and both `people` in brief; `distinct` pairs never come back; counts `open`, `clear`, `later`, `distinct`; `last_auto`, the latest automatic merge still in place (`at`, `person_ids`); each row weighed on its own and computed once per version of the tables, the records decided and the decisions on organisations, so a merge or an unmerge in a review never computes them again (a pair whose side is merged is left out) |
| `GET /api/people/duplicates/compare?a=&b=` | two people side by side from the project's data only: names and aliases, ORCID, records, identity, role, affiliations with their years, texts (count, years, the most recent titles), co-authors in the project; what they share (texts, organisations, co-authors); the pair's evidence |
| `POST /api/people/duplicates/decide {a, b, decision, keep, override}` | `merge` (the other side into `keep`), `distinct` or `later` (`people_pairs.csv`); `If-Match` of the people, answered with their version for the next decision |
| `POST /api/people/duplicates/auto {apply, min_score}` | the automatic merge of the clear pairs, or with `min_score` (0 to 1) of every pair not decided whose score is at least that (never two different ORCIDs; never a group that would join the two people of a decided pair): a preview (`groups`, `merged`, `examples`, each with `score`, the lowest of its pairs; above a score, the nearest to it first), or with `apply` (`If-Match`) one write of `people.csv` whose rows carry the note `merged automatically <time>`; undone in one step by `POST /api/people/unmerge` on its `person_ids` |
| `GET /api/people/{id}/sheet` | why a profile is what it is: the coverage and its first blocking cause, the sources used and discarded (each discarded one with `code` and `params` beside its English `what` and `why`), each finder's latest attempt, the texts, the affiliations with their years, the decision |
| `POST /api/people/import` | a CSV file (a form's `file`) or `{"text": …}` (a pasted list, one person per line): kept outside the project until confirmed, and a mapping proposal, one field per column: `last_name`, `first_name`, `name` (a full name), `orcid`, `openalex`, `idhal`, `role`, `set`, `org:<level>` (an organisation at that level; a new level is added), `column` (kept as a filter), `ignore`; e-mail columns are refused (never stored) |
| `POST /api/people/import/{id}/confirm {mapping, role, set}`, `DELETE /api/people/import/{id}` | add the people (`If-Match` of the people): the answer counts them and lists the possible duplicates; or forget the import |
| `POST /api/people/import/documents` | a form: `file` (a zip, or one document), `kind` (`folder`: documents matched to people by their names or folders, or every one to `person_id`; `corpus`: a zip holding its index CSV and the files), `create_people`; a job of kind `import` |
| `GET /api/organisations` | organisations as people decided them (`organisations.csv`, `affiliations.csv`) with their level, parents, units below, people now and ever, identifiers, the organisations merged into each (`merged_from`) and what was decided (`decided`); filters `level`, `parent`, `q` (a name, an acronym, a ROR or OpenAlex id); counts per level; `ETag`: the version of `organisations.csv` |
| `GET /api/organisations/{id}` | one organisation: parents, units, each person's affiliation with its years and source (those of the organisations merged into it too), `merged_from`, `decided` and the sources' values (`source_values`); a merged one answers its `merged_into` |
| `PATCH /api/organisations/{id} {name, level, parents, note}` | rename an organisation, change its level or parents (`If-Match` of `organisations.csv`; an empty value gives the source's back) |
| `POST /api/organisations/merge {target, sources}`, `POST /api/organisations/unmerge {org_ids, remember}` | organisations that are one (`merged_into`; their affiliations become the target's), and undoing it (`remember`: `distinct` or `later`, in `organisation_pairs.csv`) |
| `GET /api/organisations/pairs?show=`, `GET /api/organisations/compare?a=&b=`, `POST /api/organisations/decide {a, b, decision, keep}`, `POST /api/organisations/auto {apply}` | pairs that may be one organisation: the same ROR or OpenAlex id (`clear`: the automatic merge takes them, each group into the organisation with the most people) or the same name and parents (proposed); two side by side (names, identifiers, level, parents, units, people and the people they share); a pair decided (`merge`, `distinct`, `later`) |
| `POST /api/affiliations {changes: [{person_id, org_id, start_year, end_year, action}]}`, `GET /api/affiliations/version` | add or remove an affiliation, or `forget` such a decision (`If-Match` of `affiliations.csv`, whose version and rows the `GET` answers) |
| `GET /api/texts` | texts with their providers, languages, richest content (`title`, `abstract`, `full`) and number of people (the parts themselves: `GET /api/texts/{id}`); sorts `year`, `title`, `source`, `people`, `content`; filters `slot`, `year`, `language`, `content`, `provider`, `person`, `q` (title or DOI); counts per content and provider, and `duplicates`: the copies of a work the corpus reads once (same normalised title, years at most one apart, a shared author; each copy's `copy_of` names the text read) |
| `GET /api/texts/{id}` | one text: each part by provider with a preview, its people, the records merged into it (`sources/merges.json`), its versions (preprints) and the conflicts between finders |
| `GET /api/collection/plan?action=`, `POST /api/collection/plan {action, …}` | what an action would do and **what leaves the computer**: each host with its purpose, what it is sent, the requests and their cost at the service's prices; what never leaves; what is kept and where; notes (`code`, `params`, `message`; beyond the OpenAlex daily budget, `note_openalex_budget` without a key, with `usd`, `days` and `key_days`, or `note_openalex_budget_key` with `usd` and `days`; above about 10 euros at OpenAlex's prices, `note_openalex_snapshot` with `usd`; OpenAlex read from a snapshot, `note_snapshot`, or `note_snapshot_harvests` for a retry whose identities are still searched online); the estimate; `budget` (`null` on this computer's demo services or when OpenAlex is asked nothing that costs): OpenAlex's cost against the daily budget of the current tier (`cost_usd`, `keyed`, `daily_usd`, `keyed_daily_usd`, `fits`, `state`: `fits`, `needs_key` or `days`, `days`, `days_with_key`, `snapshot_advised` above `SNAPSHOT_ADVICE_USD` of `cartolex.collect.privacy`, about 10 euros); `notice`, how much of the notice to show this person (`cartolex.app.notices`): `level` (`none`: nothing personal is sent and the work fits the free budget; `brief`: names or identifiers are sent and this kind was acknowledged with the same content; `full`: the first time, a content changed or beyond the free budget), `kind` (the action), `digest` (the content: version, hosts and kinds of data), `personal`, `reasons` (`nothing_sent`, `not_personal`, `acknowledged`, `first_time`, `changed`, `over_budget`) and `acknowledged_at`; `consent_needed`, true unless the level is `none`; `openalex`, for a `harvest`, `institutions`, `collaborators` or `retry` that asks OpenAlex anything while a snapshot folder is saved on this computer (else `null`): `api` (`requests`, `seconds` at the service's rate, `days` the daily budget spreads them over when more than one, `cost_usd`, `at_least` for an institutions' floor), `snapshot` (`folder`, `state`, `release`, `bytes` a job reads, `seconds` at the speed this computer last read it, `rate_measured`, `indexed`: the estimate is the members the job's keys take through the index, at most a whole reading), `preselected` (the snapshot when it is ready and faster) and `chosen` (the request's `openalex`, `api` or `snapshot`, else `preselected`); asking `openalex: snapshot` of a snapshot not ready is 409 `snapshot_unavailable` |
| `POST /api/collection/start {action, …, openalex, consent, remember, resume}`, `GET /api/collection`, `POST /api/collection/cancel` | the collection job: `identify`, `harvest`, `institutions`, `collaborators` or `retry` (`collect` for a stand-in); when the plan asks consent (its notice is `brief` or `full`), the start carries `consent: true` (else 409 `consent_needed`, with the plan); with `remember: true` too (« Don't show this again »), the notice of this kind is `brief` from now on for this person, until its content changes. The job reads OpenAlex the way its plan chose (`openalex`); read from the snapshot, in worker processes, its result has `snapshot` (`release`, `bytes`, `seconds`, `passes`, `members` read through the index), its log a `snapshot` event, and the speed it read at serves the next estimates. `resume` names the checkpoint of a paused job (its `result.pause.checkpoint`): the job goes on from it with the options it was started with (404 `checkpoint_not_found` when there is none). The harvest reports `code: harvest_people` progress (`params`: `n` people done, `total`, `texts` received, `requests` sent; `eta_s` from the pace so far). The institutions' job reports `code: institution_works` progress (`params`: `works`, `total`, `pages`, `authors`, `rate` in works a second over the last pages; `eta_s`, re-estimated from that rate as the run goes) and pauses after its first page when more than 100,000 works are announced (`collect_size_confirm`: `total`, `requests`, `seconds`, `cost_usd`, `days` of the daily budget, `keyed`, and with a snapshot ready on this computer `snapshot_release` and `snapshot_seconds`), until resumed (or resumed with `openalex: snapshot`: the snapshot is read instead, from its own checkpoint, with no size to confirm) (its `seconds` is a rough estimate from the first page); a 429 saying the daily budget is spent (`X-RateLimit-Remaining` at 0 or below, or a body naming the budget) pauses it (`collect_budget_paused`: `resets_at`, the UTC time from `X-RateLimit-Reset`, else the next midnight UTC, and `keyed`), never restarted automatically, while a 429 that keeps failing without that is rate limiting and pauses it as any failing page (`collect_paused`, cause `collect_service_unavailable`); the cancel pauses it too. A harvest writes a run every 2,000 people or 10 minutes and pauses the same way (`harvest_stopped` when stopped, `harvest_paused` after failures in a row, its `cause` the last; `n` people kept of `total`); resumed, it skips the people already written |
| `GET /api/collection/identities?state=pending&clear=&finder=&person=` | the identity queue (`person`: that person only; with `state=all`, a person's sheet changes an identity already decided): each person with every finder's candidate records (OpenAlex with the ORCID registry as evidence, HAL, SciELO), their score, evidence (each line `text` and `points`, with `code` and `params` when the record has them) and detail (with `detail_code` and `detail_params`); the single clear match flagged (`clear`); counts of clear, unclear and without candidate |
| `POST /api/collection/identities/{person_id} {decision: accept \| none \| id, record}`, `POST /api/collection/identities/accept {person_ids}` | decide one identity, or accept the single clear match of many (the others are `left`) |
| `GET /api/collection/coverage` | coverage per class and role; the four states and their first blocking causes; states by organisation; texts by year (with an abstract, titles only) and by language; the slots' summary |
| `GET /api/collection/collaborators`, `POST /api/collection/collaborators/decide {decisions}` | collaborators round by round with joint texts, fit and path, and the cap, rounds and cut of the latest run; decisions `mapped`, `context`, `projected`, `no`, `later` (`If-Match` of the people) |
| `GET /api/collection/institutions`, `POST /api/collection/institutions/take {take, role, join, levels}` | the latest search of institutions (each with its ROR, acronym, city, country and parents) and the latest proposal of their people (paged), with the levels and the suggested merges, each with `clear` (the same ORCID, names that agree), the details of each record (`people`: name, ORCID, works, years, units, also below the minimum) and the people already `taken`; take `all` or records (`A1+A2`: one person with two records); with `all`, `join` lists the groups of records taken as one person each, even below the minimum of works (default: the clear merges), and `levels` sets the level of each type of institution |
| `GET /api/collection/institutions/people` | the latest proposal's people, paged and searched (`q`: a name, an ORCID or a record) on the server |
| `GET /api/sources`, `GET /api/sources/{slot}/files`, `POST /api/sources/{slot}/files` | a folder or corpus slot's files; upload a document or a zip archive into `sources/<slot>/` |

**Keywords, themes, atlas**

| route | what it does |
| --- | --- |
| `GET /api/keywords` | the candidates of the current extraction in their bands (`kept`, `check`, `aside`, and `rejected`: rejected automatically by the rejection lists, reason `rejected-list` or `rejected-earlier`), `term` (one keyword of the vocabulary, case ignored: its row, its forms and the candidates merged into it by a decision, an AI's English form or an alias of the keywords stage, in every band; `matched_bands` counts them by band) with the reason and the `category` of each (`concept`, `method`, `object`, `place`, `field`, `never`, `here`; a decision's, else the AI's by API), your decisions and the AI's verdicts by API (`keywords.triage`) applied, and the `route` that decided each (`person`, `ai-handoff`, `ai-copilot`, `ai-api`, `extraction`); filters `band`, `lang`, `category` (`none`: no category), `decision`, `route`, `q` (the term or one of its forms); counts per band, category, route and language; the counting unit of the extraction; `warning` when several corpus languages have no AI filtering yet (`health_languages_split`); decisions whose keyword disappeared (listed, never dropped); `gate`: the gate the next vocabulary passes (`mode`: `api`, `copilot` once a copilot's triage is accepted for this extraction, else `bands`) and, for `copilot`, `unjudged`, the candidates of the kept and to-check bands nobody judged that it keeps out (`unjudged=true` lists them, and `POST /api/keywords/decisions/where` takes it as a filter) |
| `GET /api/keywords/lexicon?language=&category=&theme=` | the keywords of the last vocabulary build that someone's row of the space holds (the whole lexicon the themes use), paged: `concept`, `rank` and `score`, `terms` (one per display language, twins merged), `language`, `people` (whose row holds it), `texts` (the texts of its candidates, summed), `category`, `node` and `theme` (its node's name in `language`, under its top-level node's) with `hue` (the top-level node's hue family), `forms` and `candidates` (`{term, language}`: what a keyword decision names); sorts `rank`, `term`, `people`, `texts`, `category`, `theme`; filters `q` (a term or a form), `category` (`none`: no category), `theme` (a top-level node); `languages`, `keywords`, `themes` (the top-level nodes); cached by the runs it reads |
| `GET /api/keywords/lexicon/export?language=` | the same, every row, as CSV (`rank`, `term_<language>`…, `language`, `score`, `people`, `texts`, `category`, `theme`, `forms`) |
| `GET /api/keywords/lexicon/cloud?by=score\|people&colour=theme\|category&theme=light\|dark&language=&hues=` | the word cloud of the lexicon's 200 most important keywords (by score or by people) as SVG: the `wordcloud` package's layout in the vendored Lato typeface (`cartolex/_data/fonts`, SIL Open Font License), its glyphs embedded, mostly horizontal, each word in its top-level theme's hue (the theme editor's families) or its category's, the interface's tokens for a light or a dark page, or `hues`, the twelve colours of the person's colour scheme (`rrggbb`, separated by commas); cached by the runs it reads and the options |
| `GET /api/keywords/decisions` | the decisions of `keywords.csv`, the latest first (the history, each one restorable); filters `decision`, `source`, `q` |
| `POST /api/keywords/decisions/where {where, decision, reason}` | keep or exclude every keyword the list's filters keep (`If-Match`; `where.unjudged`: the candidates nobody judged, « keep them anyway ») |
| `POST /api/keywords/decisions {decisions: [{term, language, decision, target, reason, category}]}` | keep, exclude or merge, one or many (`If-Match`); the first decision freezes the identity; a keep or a merge takes the term out of this computer's rejection cache (a *put back* from the `rejected` band); `category` (optional) is an accepted one for a keep or a merge, `never` or `here` for an exclusion (`keyword_category_mismatch`) |
| `POST /api/keywords/restore {keywords}` | undo decisions (an exclusion restored) |
| `GET /api/themes` | the saved tree, or the draft of the last grouping, with the keywords the tree lacks or holds too many |
| `POST /api/themes/ops {tree, ops}` | apply operations of `cartolex.project.themes` (`rename_node`, `rename_level`, `move_keywords`, `move_node`, `merge_nodes`, `split_node`, `create_node`, `delete_node`, `set_aside`, `put_back`, `set_review`, `set_attribution`, `prune_empty`, `insert_level`, `remove_level`) to a tree; answers the new tree and each step's description (the names of the undo list). Nothing is saved |
| `PUT /api/themes {tree, action}` | save a new version (`If-Match`); empty nodes are removed and named in the action |
| `GET /api/themes/versions`, `GET /api/themes/versions/{id}`, `POST /api/themes/versions/{id}/restore` | versions |
| `POST /api/themes/apply` | a build job of the themes and the map |
| `GET /api/atlas` | what the map draws at any depth of the theme tree (levels, nodes, people, keywords, units, the number of time windows and their years, projected people, bounds), `cartolex-atlas/3` (described below, with the theme editor's routes), cached by its lineage (the runs it is made from), with an `ETag` |
| `GET /api/atlas/windows?person=&base=` | the people's time windows as columns, `cartolex-atlas-windows/1`: `person` (an index in the bundle's `people`), `start`, `end`, `texts`, `x`, `y`, `top` (the window's largest top-level node, or `null`); every one, or one `person`'s; none on a `base`'s map; cached by the lineage, with an `ETag` |
| `GET /api/atlas/texts` | the texts placed on the map, columnar (`cartolex-atlas-texts/1`: `id`, `title`, `year`, `x`, `y`, `by`, `terms`, `people`, `unplaced`, `total`, `sampled`): every one, or above 100,000 a uniform sample of 100,000, the same each time while the texts are the same; `base` places them on a base map. `focus=person:<id>` (or `projected:`, `organisation:`) gives the texts of a focus instead, from every text of the tables (`cartolex.app.focus_texts`): a person's (and those merged into them), an organisation's current members' on the map, and with `net=1…3` those of the people (or of the organisations' members) its network's rings reach; `focus`, `net`, `people_count`, `total` (the focus's texts), at most `limit` (default 5,000, at most 20,000) placed, a sample the same each time beyond it (`sampled`); 404 `unknown_people`, `organisation_not_found` |
| `GET /api/atlas/regions?kind=person\|organisation&ids=a,b` | the keywords a region spans, by id (at most 500 ids): a person's most used keywords (at most 40), or those of an organisation's current members; every person's keywords are read once per run of the keywords and of the corpus |
| `GET /api/atlas/neighbours?kind=person\|organisation\|projected&id=&k=&measure=` | (not shown by the atlas, whose panel lists co-authors; for scripts and extensions) the `k` nearest (at most 100, default 10) by the project's measure of similarity (`similarity` of `params.json`), or `measure` (`space`, `keywords`, `jaccard`, `themes`): people for a person or a projected person (their stored `z`, measured in the space only), organisations of the same level for an organisation (the mean of its current members); `items` of `{id, name, similarity}`, the nearest first, and the `measure` used; only the people on the map are candidates; 404 `atlas_item_not_found`, 409 `no_space` |
| `GET /api/atlas/coauthors?kind=person\|organisation&id=&circle=1\|2\|3` | who writes with a person (the people of the project who signed a work with them) or an organisation (the organisations of its level whose people signed works with its people), from the authorships (`cartolex.app.coauthors`): `texts` (their works counted), `large` (works left out, more than `max_authors` authors in all: `collect.snowball.max_authors`, 25 by default), `outside` (the authors of their works outside the project, a person once per work: counted, never listed); the first ring `count`, `placed`, `items` (`id`, `name`, `role`, `mapped`, `place`: `map`, `projected` or `null`, `texts`: the works together; organisations: `name`, `acronym`), `lines` (`[id, works]` for each partner drawn on the map, the strongest first, at most 2,000) and `partial`; `circle=2` adds `second`, `circle=3` also `third`: the partners of the ring before that are in no earlier ring, ranked by `paths` (how many of the ring before lead to them), then by the works along those links, each item with `paths`, `texts` and `via` (at most three of the ring before, the strongest link first), `lines` as `[from, to, works]` between people drawn on the map; each ring paged (`offset`, `limit`, `offset2`, `limit2`, `offset3`, `limit3`; at most 500). A projected person is never named (`name: null`). A work counts once (the copies of a work, a preprint and its published version, are one), a merged person as the person they are merged into. An organisation's works are those its people signed, by the organisations stated on each work, else the affiliations of its year, else the current ones; any level. The graphs are kept in the project's cache beside the texts' view. 404 `unknown_people`, `organisation_not_found` |
| `GET /api/atlas/compare?a=person:<id>&b=organisation:<id>` | two people or organisations side by side: `measure` (the project's) and `similarity` (its value, the headline; one of those after), `space` (the cosine of their vectors), `keywords` (`cosine` and `jaccard` of their keyword use, `common`, the `shared` keywords that weigh most for both with each side's share), `themes` (`overlap`: Σ min of their top-level shares, and the `shared` themes) and `texts` (`shared`: the texts with an author on each side, the first twenty `items` with their title and year) |
| `GET /api/atlas/keyword-people?term=&limit=` | the people who use a keyword (or a form merged into it), ranked by the share of their keyword use it holds (`X_tf`, else `X`, of the space stage): `count`, the first `limit` (at most 500, default 50) as `{id, name, share}`, and `at`, their indexes in the bundle's `people` (those on the map, at most 50,000); `known` is false (and nobody listed) when the space has no such keyword |

**Sharing, settings, the AI proposals**

| route | what it does |
| --- | --- |
| `GET /api/share` | the site builds in `outputs/sites/` (paged: `offset`, `limit`), the newest first, each with `latest`, `stale` (what it was built from changed since: the runs it reads, the tables, a decision file), its options and counts, `zip_size` and `disk` (its files and its zip, in bytes); `exports` (the files in `outputs/exports/`); `disk`: what sharing takes (`sites`, `zips`, `exports`, `leftovers`, `total`, and `older`: the `count` and `bytes` « delete older builds » frees); `available`. A deletion is refused (`busy`) while a site build or an export runs |
| `GET /api/share/plan?names=&texts=&title=&language=` | the privacy summary of a build with these options (people named or pseudonymised, organisations, keywords, themes, texts carried, full texts kept out, `text_bytes`: what the titles, and the titles and abstracts, would add to the site, in bytes, the abstracts estimated; `site_bytes`: what the atlas's data would weigh, estimated, `core`, `links`, `parts`, `atlas`) and the checks before publishing, each `{code, level, params, fix}`: `blocker` (`no_map`), `question` (`names_unanswered`), `warning` (`names_shown`, `projected_names_shown`, `abstracts_included`, `abstracts_large` and `titles_large` (the texts would add more than 500 MB: `size`, and `titles`, in bytes), `site_large` (the atlas would read more than 50 MB at once: `size`, and `total` with the parts, in bytes), `map_stale`, `themes_untranslated`, `themes_technical`, `themes_empty`, `title_generic`), `info` (`full_texts_kept`); `ready` |
| `POST /api/share/builds {names, texts, title, language}` | build the offline site (a job, 202): `names` (`names` or `pseudonyms`) is required for a people atlas (422 `names_question`); `names_projected` (the projected people's, `pseudonyms` by default) is asked apart; `texts` is `none` (the default), `titles` or `abstracts` |
| `GET /api/share/builds/<id>/site/<path>`, `GET /api/share/builds/<id>/zip` | a build's files (to open it in the browser), and the build as one zip whose README comes first (written once, file by file, to `outputs/sites/.zips/<id>.zip`, and served from there) |
| `GET /api/share/figures/map?format=png\|svg&width=&height=&theme=` | the map as an image of the size asked, light or dark (people never named) |
| `GET /api/share/tables/themes.csv` | the theme tree as CSV |
| `POST /api/share/exports {kind}`, `GET /api/share/exports/<name>` | write the map bundle (`map_bundle`) or the project as one zip without its caches (`project`) into `outputs/exports/` (a job, dated names), and download it. Distances in the space of the themes (`cartolex.app.distance_exports`): `neighbours` (the `k` nearest of each: `source`, `target`, `rank`, `similarity`; CSV up to a million rows, else Parquet), `similarity` (every pair's cosine, written by blocks of rows: CSV up to four million cells, else `.npz` with `similarity` as float32, `ids` and `names`; above ten million cells 409 `export_size_confirm` with its size until `confirm`) and `vectors` (CSV up to five million values, else Parquet), `of` the people on the map (every one, or those of `ids`: the people the map's filters keep) or the organisations of a `level`; the nearest and the matrix follow the project's measure of similarity (the plan's `measure`; none for the vectors), and `<file>.meta.json` (`cartolex-distances/1`: `measure`, `kind`, `of`, `level`, `count`, `k`, `names`, `space_run`, `map_version`, `made_at`) is written beside each file, also in a Parquet file's schema metadata (`cartolex`) and as an `.npz`'s member `meta.json`; people named or given pseudonyms (`s1`, `s2`… in a shuffled order, no name) as `names` says (422 `export_names_question`); `plan: true` answers what would be written (`count`, `cells`, `format`, `bytes`, `size`, `confirm`, and the organisations' `levels` with their names and counts) without writing |
| `DELETE /api/share/builds/<id>`, `DELETE /api/share/builds/<id>/zip` | delete a site build and its zip (`bytes` freed, `latest`: the build the marker names now, the newest left), or its zip only (written again when downloaded); `site_delete_elsewhere` for a build a host keeps outside `outputs/sites/` (`cartolex.site.cleanup`) |
| `POST /api/share/builds/prune {plan}` | delete every build but the latest, with their zips, and what interrupted work left (an unfinished build's hidden folder, a zip whose build is gone): `ids`, `count`, `leftovers`, `bytes`, `kept`; `plan: true` only says so |
| `DELETE /api/share/exports/<name>` | delete an exported file and the `<stem>.meta.json` beside it (`files`, `bytes`) |
| `GET /api/settings`, `PUT /api/settings` | languages, language models, the AI identity (with what changing a frozen one costs: 409 `identity_frozen` unless `confirm_identity_change`), slots, projected sets, levels, data sources |
| `GET /api/settings/rejects`, `PUT /api/settings/rejects {enabled}` | the candidates rejected automatically: whether the project uses the rejection lists (the `rejects` parameter of `keywords.extract`, ETag of `params.json`), the terms of cartolex's list per corpus language, this computer's cache (`<data dir>/rejects/`, none on a hosted service) |
| `GET /api/settings/rejects/terms?lang=`, `POST /api/settings/rejects/clear {language, terms}` | the cache's terms (one per term and language: how many projects gave it, the routes, the last day), paged; remove some terms, or empty the cache (of one language); `rejects_hosted` on a hosted service |
| `GET /api/settings/curation`, `PUT /api/settings/curation {notes, rules}` | the curator's notes for the AI copilot and the standing rules agreed with it (`decisions/curation-notes.md`, `If-Match`; `rules` left out: kept); every copilot bundle carries them |
| `GET /api/settings/stopwords`, `PUT /api/settings/stopwords {add, remove}` | the words added to and removed from the lists of words that are never keywords, per language (`decisions/stopwords.json`, `If-Match`); a word both added and removed: `stopword_both` |
| `GET /api/settings/prompts`, `PUT /api/settings/prompts/{name} {text}` | the prompts a project may replace (the packaged text, the project's own in `decisions/prompts/<name>.txt`, the placeholders); `text: null` goes back to the packaged one (the project's is kept in the history); a placeholder the packaged text lacks: `prompt_placeholder` (`If-Match`) |
| `GET /api/settings/backup` | a zip of `project.json` and `decisions/` with its history, and `backup.json` (`cartolex-backup/1`); texts, caches and built results are left out |
| `POST /api/settings/restore` | a backup (multipart `file`): its decision files replace the project's, each current version kept in its history first; `project.json` stays; `not_a_backup` otherwise |
| `POST /api/settings/reset {what: built}` | remove the built results (every stage is then never built); decisions, texts and caches stay; 409 `busy` while a job runs |
| `GET /api/machine`, `PUT /api/machine/keys {service, key}` | this computer: the keys saved on it (`mistral`, `openalex`; whether set, from the environment or saved, the last four characters; never shown whole, never in a project: `<data dir>/keys.json`, readable by its owner only; an environment variable wins), whether the AI clean-up can run by API, OpenAlex's daily budget with and without a key and its prices (from the one table of `cartolex.collect.privacy`, with `snapshot_above_usd`), the OpenAlex snapshot folder (`snapshot`, below), the processors, the memory available and the build's memory budget; `key: null` removes a key; refused on a hosted service (`keys_hosted`) |
| `PUT /api/machine/snapshot {folder}` | the folder of an OpenAlex snapshot downloaded to this computer, its full path (`<data dir>/snapshot.json`, never in a project; `folder: null` forgets it, its files are kept); refused when it is not a full path, not a folder or holds no snapshot (422 `snapshot_invalid`, `reason`: `relative`, `not_folder`, `no_snapshot`) and on a hosted service (`snapshot_hosted`). `GET /api/machine`'s `snapshot` is `null`, or `folder`, `state` (`ready`, `incomplete`: parts the manifests list missing or of another size, or an entity cartolex reads absent; `missing`: the folder is not there), `release`, `bytes` and `parts` by entity, `missing`, `absent`, `read_rate` in bytes a second and `rate_measured` (else an assumed speed), `measured_at`, `index` (`null`, or `state`: `complete` or `building`, `release`, `built_at`, `parts`, `done`) and `indexed` (a complete index of this release: the plans' estimates and the jobs read through it) |
| `PUT /api/machine/budget {memory_mb, workers, scratch}` | what the builds the app starts may use of this computer (`<data dir>/budget.json`, never in a project; `null` or missing: this computer's default, `cartolex.scale.Budget.for_machine`): the memory their stages size their work to, their worker processes, a folder on a fast local disk for their temporary files; refused below 1,024 MB or above the computer's memory, outside one to the computer's processors, for a folder that is not a full path, not a folder or not writable (422 `budget_invalid`, `field`, `reason`: `too_small`, `too_large`, `relative`, `not_folder`, `not_writable`) and on a hosted service (`budget_hosted`). `GET /api/machine`'s `build_budget`: `memory_mb`, `workers`, `scratch` (what the next build gets), `saved`, `default`, `total_memory_mb`, `cpus` |
| `GET /api/ai/proposals`, `GET /api/ai/proposals/{id}`, `POST /api/ai/proposals/{id}/accept {terms, all}` | the keyword proposals: a copilot's triage results ({doc}`copilot`), and the answers to a handoff (a prompt and a list pasted in a chat) an earlier version imported, still read and accepted (`cartolex.project.handoff` reads them); each answered term with its code, `category`, `reason`, and for a copilot's its `group` and `by` (`group` or `term`); accepted ones reach `keywords.csv` with the source `ai-copilot` (`ai-handoff` for an earlier answer) and their category; an accepted term whose English form is another term is merged into it; an accepted exclusion of category `never` whose `confidence` is `sure` enters this computer's rejection cache |
| `GET /api/keywords/ai` | the two routes of the AI filtering: with a copilot (`copilot.proposals`, the proposals so far) and by API (provider, whether a key is saved, what is sent, an estimate of the calls and tokens, the last run); the estimate counts every candidate but those rejected automatically (`terms`, `rejected`), those already answered in `cache/ai/` (`answered`) and the `new` ones, the only ones that cost calls; `usage`, the tokens spent so far, as the recipe's `ai_usage` |
| `POST /api/keywords/ai/run {consent}` | filter by API: switch `keywords.triage` on in `params.json` and start it as a build job (202); `ai_api_not_ready` without a key or a provider, `ai_consent_needed` without consent |

**The person**: `GET /api/me/preferences` and `PUT /api/me/preferences
{locale, theme, dismissed_jobs, other}`: the interface language, the theme, the
finished jobs dismissed from the Activity list (200 at most) and a few other
settings of the person signed in, kept in the app's own folder, so they outlive
the browser's storage (a hosted service: they follow a person from one browser
to another). The interface reads them at start and saves every change; the
browser keeps a copy to paint the theme before the first request.

**The collection notices**: `GET /api/me/notices` lists the kinds of
collection whose notice the person acknowledged (`acknowledged`: `kind`, `at`;
`version`, the notice's version), and `DELETE /api/me/notices` forgets them
all: every notice is shown in full again. They are kept in the app's own
folder, per person (`<data dir>/users/<digest>.notices.json`, never in a
project), in memory when the app has no folder.

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
| `stale` | 412 | this changed elsewhere since it was read; reload it and apply the change again | `file` | `reload` |
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
| `locked` | 409 | the project is open in {app} (process {pid} on {host}, since {since}); close it there, or open it anyway if that computer is off or the app is stuck | `app`, `pid`, `host`, `since` | `confirm` |
| `locked_here` | 409 | the project is open in another {app} on this computer (process {pid}, since {since}); close that one (its browser tab does not stop it: close the terminal it runs in), or open it anyway if it is stuck | `app`, `pid`, `since` | `confirm` |
| `lock_lost` | 409 | this app no longer holds the project: {app} (process {pid} on {host}, since {since}) opened it anyway; nothing more is saved here, open the project again | `app`, `pid`, `host`, `since` | `open-project` |
| `project_exists` | 409 | the folder already holds a project, or is not empty: {path} | `path` | `fix-input` |
| `project_folder_missing` | 422 | choose the folder of the new project | — | `fix-input` |
| `project_folder_relative` | 422 | the project's folder is a full path: {path} | `path` | `fix-input` |
| `project_id_missing` | 422 | a hosted project needs an id | — | `fix-input` |
| `project_not_listed` | 404 | {path} is not among the projects listed here | `path` | `reload` |
| `project_delete_hosted` | 409 | on a hosted service a project's files are deleted by whoever runs it | — | `none` |
| `project_delete_refused` | 409 | {path} cannot be deleted from here ({reason}) | `path`, `reason` | `none` |
| `project_delete_confirm` | 422 | deleting the folder {path} cannot be undone: confirm it to delete it | `path` | `confirm` |
| `project_delete_held` | 409 | the project is open in {app} (process {pid} on {host}, since {since}); close it there before deleting its folder | `app`, `pid`, `host`, `since` | `none` |
| `project_delete_failed` | 409 | the folder {path} was not deleted entirely ({error}); what is left can still be opened or deleted again | `path`, `error` | `retry` |
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
| `snapshot_hosted` | 409 | on a hosted service there is no OpenAlex snapshot folder of this computer | — | `none` |
| `snapshot_invalid` | 422 | {folder} cannot be the OpenAlex snapshot ({reason}): give the full path of a folder holding data/jsonl/works, authors and institutions | `folder`, `reason` | `fix-input` |
| `budget_hosted` | 409 | on a hosted service the builds' budget is set by whoever runs it | — | `none` |
| `budget_invalid` | 422 | {field} cannot be {value}: {reason} | `field`, `value`, `reason` | `fix-input` |
| `rejects_hosted` | 409 | on a hosted service there is no rejection cache of this computer | — | `none` |
| `stopword_both` | 422 | a word is both added and removed: {words} | `words` | `fix-input` |
| `prompt_not_found` | 404 | there is no prompt {name} to change | `name` | `reload` |
| `prompt_invalid` | 422 | the prompt cannot be read: {detail} | `detail` | `fix-input` |
| `prompt_placeholder` | 422 | the prompt uses placeholders the AI clean-up does not fill: {unknown} | `unknown`, `allowed` | `fix-input` |
| `not_a_backup` | 422 | this file is not a backup of a cartolex project | — | `fix-input` |
| `map_version_not_found` | 404 | there is no map version {version} | `version` | `reload` |
| `map_version_missing` | 422 | name the version to {action} | `action` | `fix-input` |
| `map_version_pinned` | 409 | {version} is pinned: pin another version before discarding it | `version` | `fix-input` |
| `no_pinned_version` | 409 | there is no pinned map version to start from: build the map first | — | `build` |
| `layout_param_unknown` | 422 | the {method} layout takes no parameter {param}; it takes: {known} | `method`, `param`, `known` | `fix-input` |
| `layout_method_unavailable` | 422 | the {method} layout needs the optional {package} package, which is not installed on this computer: run the installer again, or {command} | `method`, `package`, `command` | `fix-input` |
| `preview_needs_build` | 409 | this preview needs {stage} built first | `stage` | `build` |
| `base_not_found` | 404 | there is no base map {base} | `base` | `reload` |
| `atlas_item_not_found` | 404 | the {kind} {id} has no place in the space of this map | `kind`, `id` | `reload` |
| `base_no_map` | 422 | {path} holds no project with a map: build its map there first | `path` | `fix-input` |
| `base_same_project` | 422 | a project's own map cannot be its base: choose another project | — | `fix-input` |
| `base_in_use` | 409 | a map version is placed on {base}: discard it before removing the base | `base` | `fix-input` |
| `bases_hosted` | 409 | on a hosted service a base map is added by whoever runs it | — | `none` |
| `no_versions` | 404 | {file} has no versions; files with versions: {files} | `file`, `files` | `none` |
| `file_not_written` | 404 | {file} does not exist yet | `file` | `none` |
| `version_not_found` | 404 | there is no version {version} of {file} | `version`, `file` | `reload` |
| `version_unreadable` | 409 | version {version} of {file} cannot be rebuilt from the history: {reason} | `version`, `file`, `reason` | `none` |
| `already_current` | 409 | this version is already the current one | — | `none` |
| `unknown_people` | 404 | unknown person id(s): {ids} | `ids` | `reload` |
| `unknown_set` | 422 | there is no projected set {set}; sets: {sets} | `set`, `sets` | `fix-input` |
| `set_needed` | 422 | a projected person belongs to a projected set: add one in the settings first | — | `settings` |
| `self_merge` | 422 | a person cannot be merged into themselves | — | `fix-input` |
| `merged_target` | 409 | {target} is itself merged into {into}: merge into that person | `target`, `into` | `fix-input` |
| `merge_orcid_conflict` | 409 | {target} and {source} have different ORCIDs ({orcids}): two different iDs are two people; merge them anyway only if you know they are one person | `target`, `source`, `orcids` | `confirm` |
| `file_missing` | 422 | send the file in a form, as 'file' | — | `fix-input` |
| `list_body` | 422 | send the list in a form (as 'file'), or as {"text": …} | — | `fix-input` |
| `empty_list` | 422 | the list holds nobody | — | `fix-input` |
| `import_not_found` | 404 | this import is not waiting any more | — | `reload` |
| `mapping_unknown_fields` | 422 | unknown field(s) {fields}; the fields are {known} | `fields`, `known` | `fix-input` |
| `mapping_unknown_columns` | 422 | the list has no column(s) {columns} | `columns` | `fix-input` |
| `mapping_no_name` | 422 | map a column to the last name, or to the full name | — | `fix-input` |
| `unknown_role` | 422 | {role} is not a role | `role` | `fix-input` |
| `collection_unavailable` | 409 | collecting texts is not available in this version | — | `none` |
| `snapshot_unavailable` | 409 | the OpenAlex snapshot of this computer is not ready to be read ({state}): plug in its disk, finish its download, or read OpenAlex from the API | `state` | `none` |
| `no_slot` | 409 | the project has no slot to collect into: add one in the settings | — | `settings` |
| `collection_not_running` | 409 | no collection is running | — | `none` |
| `checkpoint_not_found` | 404 | there is no paused collection {checkpoint} to resume (it ended, or started again) | `checkpoint` | `reload` |
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
| `keyword_category_mismatch` | 422 | {term}: a {decision} takes a category among {allowed} | `term`, `decision`, `allowed` | `fix-input` |
| `no_decision` | 404 | none of these keywords has a decision | — | `reload` |
| `invalid_tree` | 422 | the tree is not valid: {detail} | `detail` | `reload` |
| `theme_refused` | 422 | the change was refused: {detail} | `detail` | `fix-input` |
| `theme_step_refused` | 422 | step {step} ({op}) was refused: {detail} | `step`, `op`, `detail` | `fix-input` |
| `no_keywords` | 409 | build the keywords first | — | `build` |
| `triage_empty` | 404 | no term to send in this band | — | `none` |
| `proposal_not_found` | 404 | there is no proposal {proposal} | `proposal` | `reload` |
| `nothing_chosen` | 422 | choose the terms to accept | — | `fix-input` |
| `not_available` | 501 | building the offline site is not available in this version | — | `none` |
| `no_map_to_share` | 409 | there is no map to share yet: build the map first | — | `build` |
| `names_question` | 422 | say whether the site shows people's names or pseudonyms | — | `fix-input` |
| `site_not_found` | 404 | there is no site build {build} | `build` | `reload` |
| `export_not_found` | 404 | there is no exported file {name} | `name` | `reload` |
| `site_delete_elsewhere` | 409 | the build {build} is kept outside the project's outputs; it is not deleted here | `build` | `none` |
| `export_names_question` | 422 | say whether the file names people or gives them pseudonyms | — | `fix-input` |
| `export_size_confirm` | 409 | this matrix has {cells} cells, about {size}: confirm to write it | `cells`, `size` | `confirm` |

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
| `empty_no_texts` | the grouping did not read the texts: build the themes with the comb | — | `none` |
| `empty_no_levels` | every keyword sits on the level its texts support, or you kept it there | — | `none` |
| `empty_no_corpus` | the texts are not gathered yet: build the corpus | — | `build` |
| `empty_no_space` | the keywords are not placed in a space yet: build the themes | — | `build` |
| `empty_no_grouping` | the keywords are not grouped yet: build the themes | — | `build` |
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
| `empty_no_duplicates` | no two people look like one person | — | `none` |
| `empty_no_identity_in_state` | nobody is in this state | — | `none` |
| `empty_triage` | no term to send in this band | — | `none` |
| `empty_no_proposals` | no AI answers imported yet | — | `none` |
| `empty_no_rejects` | no term rejected by an AI on this computer yet | — | `none` |
| `empty_no_decisions` | no decision yet: keep, exclude or merge keywords in the list | — | `none` |
| `collection_unavailable` | collecting texts from bibliographic services is not available in this version; import texts into a folder or corpus slot instead | — | — |
| `stage_switched_off` | switched off | `stage` | — |
| `stage_copilot_done` | done with your copilot ({n} decisions, {date}) | `n`, `date` | — |
| `stage_copilot_waiting` | waiting for your copilot: give it the candidates, then import and accept its result ({total} earlier decisions still apply) | `total` | — |
| `stage_copilot_earlier` | no AI clean-up of the candidates found since your copilot's triage: its {n} decisions still apply to the ones it judged, the new ones enter by their bands; choose a copilot or the API on the build page to have them judged | `n` | — |
| `stage_ai_none` | no AI clean-up: choose a copilot or the API on the build page to have one | — | — |
| `stage_no_overlay` | the project has no overlay | — | — |
| `stage_not_applicable` | {reason} | `reason` | — |
| `stage_cancelled` | the stage was cancelled; its previous results are kept | — | — |
| `stage_refused` | the stage could not run: {detail} | `detail` | — |
| `stage_no_texts` | no mapped person has texts yet: collect their texts first | — | `open:/people?collect=harvest` |
| `stage_no_mapped` | nobody is mapped yet: on the People page, choose the people whose texts make the map | — | `open:/people` |
| `stage_ai_not_set` | the AI clean-up by API needs an AI provider: choose one in the settings, or another route for the clean-up on the build page | — | `settings` |
| `stage_ai_no_key` | no AI key is saved on this computer: add one in the settings, or choose another route for the clean-up on the build page | — | `settings` |
| `stage_themes_rebase` | your themes could not be carried over to the new vocabulary: open the themes and save them again | — | `open:/themes` |
| `stage_no_pinned_map` | no map version is chosen: choose one in the map's settings | — | `open:/map?tune=1` |
| `stage_layout_missing` | this computer cannot draw the map's method ({method}): choose another method in the map's settings | `method` | `open:/map?tune=1` |
| `language_model_missing` | a language model is missing: {detail} | `detail` | — |
| `stage_failed` | the stage failed ({error_type}): {detail} | `error_type`, `detail` | — |
| `job_failed` | the job failed ({error_type}): {detail} | `error_type`, `detail` | — |
| `collect_budget_spent` | {host} refused more requests (status {status}): its daily budget is spent; set a free API key, or wait for the next day's budget | `host`, `status`, `what`, `wait_s`, `keyed` | a free key without one (`open:/settings?section=sources`), else `none` |
| `collect_service_unavailable` | {host} gave no usable answer after every attempt ({what}); try again later | `host`, `status`, `what` | — |
| `collect_incomplete` | {host} cut a page of a list short; collect again | `host`, `status`, `what` | — |
| `collect_malformed` | {host} gave an answer that could not be read ({what}) | `host`, `status`, `what` | — |
| `collect_refused` | {host} refused a request (status {status}); copy a diagnostic and report it | `host`, `status`, `what` | — |
| `collect_cache_miss` | an answer is not in the cache: collect without cache-only | — | — |
| `collect_size_confirm` | {total} works are signed there: reading them takes about {requests} requests and {seconds} s; confirm to go on, or narrow the years or the units, or read the OpenAlex snapshot instead | `works`, `total`, `pages`, `requests`, `seconds`, `cost_usd`, `days`, `keyed` | — |
| `collect_stopped` | stopped after {works} of {total} works; resume to go on | `works`, `total`, `pages` | — |
| `collect_paused` | a page still failed after its retries; {works} of {total} works are kept: resume to go on | `works`, `total`, `pages` | — |
| `collect_budget_paused` | the daily budget is spent; {works} of {total} works are kept: resume once it comes back ({resets_at}) | `works`, `total`, `pages`, `resets_at`, `keyed` | — |
| `health_map_stale` | the map was drawn from inputs that changed since; building the map restores it | — | `build` |
| `health_model_missing` | the language model {model} for {language} is not installed; the keyword extraction needs it | `model`, `language` | `settings` |
| `health_too_large` | {stage} needs about {need_mb} MB of memory and this machine has about {budget_mb} MB | `stage`, `need_mb`, `budget_mb` | `settings` |
| `health_languages_split` | the texts are in {languages}: without the AI clean-up, keywords of each language may form themes of their own | `languages` | `settings` |
| `health_space_languages` | {share}% of the keywords are not in {language}: in a space of texts, themes may split by language; the people's space may suit this corpus better | `share`, `language` | `open:/method` |
| `health_snowball_cap` | the last proposal of collaborators in {slot} stopped at the cap of {cap} people | `slot`, `cap` | `settings` |
| `health_copilot_pending` | a copilot's result was imported and is not accepted yet: review it | `proposal` | `open:/keywords?copilot=1&proposal=<id>` |
| `health_identities_pending` | {n} mapped people without texts wait for an identity check | `n` | `open:/people?tab=identities` |
| `health_not_harvested` | {n} mapped people have no texts: their texts were never collected | `n` | `open:/people?collect=harvest` |
| `preflight_no_texts` | {n} mapped people have no texts: collect them first, or build without them | `n` | `open:/people?collect=harvest` |
| `preflight_copilot_pending` | a copilot's result was imported and is not accepted yet: the build does not use it until you accept it | `proposal` | `open:/keywords?copilot=1&proposal=<id>` |
| `preflight_copilot_new` | the candidates are found again: your copilot's {n} decisions still apply to the ones it judged, but new candidates enter the vocabulary by their bands, without its triage; choose the copilot below to have it judge them first | `n` | — |
| `preflight_api_dropped` | the AI clean-up by API judged the candidates before; with this route its verdicts no longer decide which candidates the vocabulary keeps | — | — |
| `copilot_many_terms` | {terms} candidates: several conversations or agents may be needed; the kit cuts them into {parts} parts | `terms`, `parts` | — |
| `preview_needs_extraction` | {param} at {value} reaches past the last build's {built}: the candidates outside its window were never kept, a new extraction shows them | `param`, `value`, `built` | — |
| `next_watch_build` | a build is running | — | `open:/build` |
| `next_job_running` | a {kind} job is running | `kind` | `wait` |
| `next_set_roles` | nobody is mapped yet: choose the people whose texts make the map | — | `open:/people` |
| `next_check_identities` | {n} people wait for an identity check before their texts can be collected | `n` | `open:/people?tab=identities` |
| `next_collect_texts` | {n} mapped people have no texts yet: collect their texts | `n` | `open:/people?collect=harvest` |
| `next_review_keywords` | look at the keywords the build found: keep, set aside or merge them | — | `open:/keywords` |
| `next_build_map` | draw the map | — | `build` |
| `next_share` | share the map: build a site you can send or publish | — | `open:/share` |
| `next_copilot_waiting` | the build waits for your copilot ({step}) | `step` | `open:/build` |
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

### `GET /api/atlas`: `cartolex-atlas/3`

The atlas reads only the theme files of any depth (`themes_applied.json`,
`theme_keywords.csv`, `theme_people.parquet`, `theme_organisations.parquet`,
the `levels` of `positions.json`; see
{doc}`../format/derived`), never the two-level files that exist at depth 2
only, so a project of depth 1, 3 or 4 gets its map like one of depth 2.

```json
{
  "format": "cartolex-atlas/3", "available": true, "lineage": {"map.layout": "…"},
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
  "windows": 52, "window_years": {"min": 2006, "max": 2026},
  "overlays": [{"set": "applicants", "person_id": "p0041", "x": 0, "y": 0, "shares": [{}, {}]}],
  "bounds": {"xmin": -6, "xmax": 6, "ymin": -5, "ymax": 7}
}
```

- `levels` and `nodes` come from `themes_applied.json` of the map (nodes in
  tree order, with their map position, the mean of their people's).
- `shares` is one `{node id: share}` per level, from the top: the **usage
  share** of the person (the organisation, the projected person) that counts
  toward each node of that level; each level sums to 1 where there is usage. A
  keyword's `node` is `null` when it is set aside.
- `windows` counts the people's time windows placed on the map, and
  `window_years` gives their first and last years: a map of a hundred thousand
  people has millions, so they come apart, as columns, from
  `GET /api/atlas/windows` (every one when they are shown, the selected
  person's otherwise), each with its largest top-level node
  (`trajectory_themes.parquet`).
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
  `terms` indexes `keywords` of the bundle. A corpus of more than 100,000 texts
  (`total`) is drawn by a sample of 100,000 (`sampled`; the map says so, and a
  person's panel then leaves out their count of texts); only the sampled texts'
  parts are read.

### The theme tree

| route | what it does |
| --- | --- |
| `GET /api/themes` | the saved tree (`source: saved`), else the grouping's proposal read from `themes.group/themes_draft.json` (`source: draft`), else none; beside it `based_on_current`, the vocabulary's gaps, `space_run`, and `proposal`: `{run, pending, same_vocabulary}` |
| `GET /api/themes/draft` | the grouping's latest proposal, whatever tree is saved: `{run, tree}` (`no_proposal` before the first grouping) |
| `GET /api/themes/usage` | each keyword of the current vocabulary: `{term: [people, weight]}` (how many people use it; the sum of its share of each person's usage), `people` counted; `ETag` by the space's run |
| `POST /api/themes/ops` | `{tree, ops, lenient}`: the operations applied in order; each step is `{op, description}`, or with `lenient` a refused step is skipped and reported as `{op, refused}` |
| `POST /api/themes/borderline` | `{tree, level, reviewed, offset, limit, sort, q}`: the placed keywords of the tree sent, paged, smallest margin first: `keyword`, `node` (its node at the level compared: `level`, else its own node's), `other` (the nearest other node of that level), `own` and `near` (cosines to the two nodes' centroids in the space, the keyword left out of its own) and `margin` (`own − near`; negative: nearer the other node); `negative` counts those, and `alone` the keywords alone in their node there (no margin without them, so not listed). Keywords marked `kept` (« keep here » in this list) are left out unless `reviewed`; a `reviewed` from the queue of a rebase does not hide a keyword never judged here. `no_space` before the space is built; the measure is in `cartolex.lexicon.theme_fit` |
| `POST /api/themes/levels` | `{tree, reviewed, offset, limit, sort, q}`: the comb read on the tree sent: the placed keywords whose texts support a higher node, paged: `keyword`, `node` (where the tree puts it), `to` (the ancestor to move it up to; `null`: too broad for any theme), `share` (the share of its texts' use that node holds, above what any keyword gives it; too broad: the best a top-level node holds) and `texts`; `theta` (the calibrated θ) and `too_broad`. Sorts: `suggested` (default: moves up by share, then too broad), `keyword`, `share`. Keywords marked `kept` are left out unless `reviewed`. Empty with `empty_no_texts` when the grouping did not read the texts (`themes.group.comb` off); the measure is `cartolex.lexicon.theme_comb.tree_levels` |
| `POST /api/themes/suggestions` | `{tree, keywords, top, scope}`: for each keyword (default: the set-aside ones and those « to check », `scope` `aside`, `check` or `both`), the `top` nodes (at most 10, default 3) holding keywords whose centroid is nearest: `{suggestions: {keyword: [{node, score}]}}`, *score* the cosine |
| `POST /api/themes/compare` | `{before, after, limit}`: every difference (`cartolex.project.themes.compare`), with `total` and `counts` by kind |
| `POST /api/themes/playground` | `{settings, tree}`: the grouping's proposal with other settings, nothing saved (the editor's « Playground »): `settings` per stage, laid over `params.json` (`themes.space`: `space_unit`; `themes.group`: `depth`, `top_groups`, `keywords_per_group`, `level_sizes`, `comb`, `comb_theta`; `null`: back to the default or the rule). 200 `{preview, against}` when computed from the current results, else 202 `{job}` (a job of the `preview` group, title code `preview_grouping`; send the same request once it ends). The build's own runners on the stored space (refitted first when the space's settings differ from it), into a scratch folder: `preview.tree` is what a build with these settings proposes (`based_on.run`: `preview`), with `settings` (each stage's effective values), `space_refit`, `theta` (the comb's θ, pinned or calibrated; `null` without the comb) and `seconds`; `against` (with `tree`): `split`, `merged`, `moved`, `placed`, `set_aside`, `put_back` at the top level (`cartolex.project.themes_carry.against`). Refused settings: 422 `invalid_parameters`; before the keywords or the space: 409 `preview_needs_build` |
| `POST /api/themes/carry` | `{tree, proposal}`: the proposal with the curator's names, set-asides, attributions and reviews of `tree` carried wherever a node continues (`cartolex.project.themes_carry`), `carried` (`names`, `set_aside`, `attributions`, `reviews`, `dropped`) and `against`. Nothing is saved |
| `POST /api/themes/rebase` | rebases the saved tree onto the current vocabulary now, as an apply does first (send `If-Match`): `{written, notes, version, to_check, tree}` |
| `POST /api/themes/proposal` | `{decision: adopt \| keep, run}` (send `If-Match`): agree once on a new grouping of the same vocabulary; `adopt` saves the proposal, `keep` records that the tree was kept over it (`based_on.run`); `proposal_changed` when a newer proposal replaced `run` |

A **clustering-only change** (the grouping ran again with other parameters, on
the same vocabulary) leaves the saved tree as it is. `proposal.pending` is true
until someone adopts the new proposal or keeps the tree over it: the tree
names the grouping it agreed with in `based_on.run` (a tree saved from a
proposal, adopted or kept), else the grouping the last apply read (after a
rebase).

### Theme answers imported by an earlier version

The AI curation of the themes is the copilot's ({doc}`copilot`). The answers
to a theme handoff (a prompt and the tree as text, pasted in a chat;
`cartolex-themes-handoff/1`, `cartolex.project.themes_handoff`) that an earlier
version imported stay readable:

| route | what it does |
| --- | --- |
| `GET /api/themes/ai/proposals` | the theme results imported so far, newest first: the copilot's (`<time>-copilot-themes`, read with `GET /api/themes/copilot/proposals/{id}`) and the earlier answers (`<time>-themes`) |
| `GET /api/themes/ai/proposals/{id}` | an earlier answer read into proposed operations (`items`: `number`, `verb`, `op` in the form of `POST /api/themes/ops`, `reason`, `refused` when it cannot apply to the tree sent) and the lines it could not read (`unreadable`: `line`, `text`, `problem`: `unknown_action`, `missing_fields`, `unknown_node`, `unknown_keyword`, `bad_levels`, `empty_name` or `same_node`) |

The editor shows a proposal as a list to accept or reject, previews the
accepted operations on the tree, and applies them through
`POST /api/themes/ops`.

### The AI copilot

The copilot's bundle is a zip an assistant able to run code works from on its
own; its result comes back as one file. Its format, the kit and the routes
(`/api/themes/copilot/…`, `/api/keywords/copilot/…`) are in {doc}`copilot`.
An imported themes result is reviewed as a list of changes; a triage result is
a keyword proposal (`GET /api/ai/proposals/{id}` and its `accept` take
the ids of both).

### Errors of the theme editor

| code | status | message | next |
| --- | --- | --- | --- |
| `no_proposal` | 404 | the grouping has proposed no tree yet: build the themes first | `build` |
| `proposal_changed` | 409 | a newer proposal ({run}) replaced the one you saw: look at it first | `reload` |
| `themes_empty` | 404 | the tree holds no keyword to send | — |
| `no_space` | 409 | the keywords have no space yet: build the themes first | `build` |
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
