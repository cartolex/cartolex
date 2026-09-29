# The web interface

The interface is a set of native ES modules under `cartolex/app/static/`,
written with [Preact](https://preactjs.com/), its hooks, signals and
[htm](https://github.com/developit/htm) templates. There is no build step, no
npm and no bundler: the files in the repository are the files the browser
loads, and an installation stays Python-only.

```text
cartolex/app/static/
  index.html        the shell document (no inline script, no inline style)
  core/             shell, router, registries, i18n, stores, API client
    main.js           entry point: boot() into #cx-app
    app.js            the start sequence
    shell.js          header, navigation, outlet, drawers, toasts
    router.js         history routes and the page lifecycle
    page.js           definePage(), usePage(), usePageTitle()
    registries.js     pages, slots, facets, layers
    extension-api.js  what an extension's register(api) receives
    i18n.js           catalogues, messages (ICU subset), Intl formatting
    api.js            the JSON client (CSRF, AbortSignal, ETag, errors)
    errors.js         the ErrorCard model and the diagnostic
    states.js         the build's six states
    stores/           project state, jobs (one poller), preferences
    dom.js            ids, focus trap, outside press, Escape, floating
    runtime.js        what components need from the running app
    preact.js         the one door to the vendored libraries
    boot-theme.js     a classic script: the stored theme before first paint
  components/       Button, Card, StatusDot, Table, Dialog… (index.js)
  pages/            the gallery, the overview, placeholders, not found
  i18n/             en.json, fr.json, pt-BR.json
  css/              tokens.css, base.css, components.css, gallery.css
  brand/            logo and icon
  vendor/           Preact, hooks, htm, signals (see VENDOR.md)
```

## The shell

`core/main.js` calls `boot()`, which starts the app in this order:

1. `GET /api/app/manifest` (format `cartolex-manifest/1`: app, branding,
   locales and their catalogue files, navigation entries, extension modules,
   capabilities, the open project, the CSRF header's name);
2. the catalogues of the interface language;
3. `import()` of every extension module, then its `register(api)`;
4. the shell (header, navigation, outlet);
5. the route: the page of the current address is mounted.

Then the project state is refreshed (its status dots already showed the
cached state) and the jobs poller starts. If the manifest cannot be read, the
shell shows an ErrorCard, in the browser's language, and nothing else.

## Routes and the page lifecycle

Routes are real paths (`/keywords`, `/gallery`), read with the History API.
The server answers every path that is not under `/api/` or `/static/` with
the shell document. Real paths keep the fragment free for anchors inside a
page, give addresses people can share, and let a hosted service apply its
access rules per path.

Each navigation gets a **token** and an **AbortController**. A page module
exports a page object whose `mount(ctx)` renders into `ctx.root` and returns
its teardown (or a promise of it). `definePage(Component, {styles})` makes one
from a Preact component:

```js
import { html } from '../core/preact.js';
import { definePage, usePage, usePageTitle } from '../core/page.js';

function Keywords() {
  const ctx = usePage();
  usePageTitle(t('nav.keywords'));
  useEffect(() => {
    ctx.api.get('/api/keywords').then((result) => { /* … */ });
  }, []);
  return html`<div class="cx-page"><h1 class="cx-page__title">…</h1></div>`;
}

export const page = definePage(Keywords);
```

`ctx` holds `api` (the API client bound to the navigation's signal), `signal`,
`params` (from `/people/:id`), `query`, `guard()`, `onLeave()`, `keep()`,
`isCurrent()`, `setTitle()`, `navigate()`, `deferReady()` and `ready()`.

**Leaving a page** aborts its signal, runs its teardowns in reverse order and
empties the outlet. Calls made through `ctx.api` (or wrapped in `ctx.keep()`)
return promises that **never settle** once the page is left: no callback of a
torn-down page runs, whatever arrives late. The router marks the page
**ready** when `mount` returns (or when the page calls the function
`ctx.deferReady()` gave it), records the time, moves the focus to the page's
`h1` and announces its title.

**Guards.** A page with unsaved edits registers
`ctx.guard({dirty: () => boolean})`; the guard goes away with the page.
Leaving while `dirty()` is true — a link, `navigate()`, the browser's back
button — asks « Leave without saving? » first (the back button is undone if
the person stays); closing the tab asks the browser's own question. A guard
may bring its own `confirm(to)`.

## Registries and extensions

An extension is an ES module the manifest lists in `modules`; the host
application serves it (for example under `/static/ext/<id>/`). The shell
imports it before the first render and calls its `register(api)`:

```js
export function register(api) {
  const { html, definePage } = api.ui;
  api.i18n.add('en', { 'ext.demo.title': 'Demo' });
  api.pages.add({ id: 'demo', route: '/demo', label: 'ext.demo.title', order: 90,
                  page: definePage(() => html`<h1>…</h1>`) });
  api.slots.add('overview.cards', { id: 'demo', order: 10,
                  component: () => html`<${api.components.Card} title="…">…<//>` });
}
```

| `api.` | what it holds |
| --- | --- |
| `pages.add(entry)` | a routed page `{id, route, label, page \| load \| module, order, placement, area}`; `placement` is `main` (in the navigation), `settings` or `hidden` |
| `slots.add(name, {id, order, component})` | a contribution to a named insertion point; known names: `overview.cards`, `corpus.sources`, `map.layers`, `share.cards`, `settings.sections`, `header.actions`, `<page id>.cards` |
| `facets.add(…)`, `layers.add(…)` | list filters and map layers (used by later screens) |
| `i18n.add(locale, messages)` | messages merged over the catalogues, now and at every language switch |
| `ui` | Preact, hooks, signals, `html`, `definePage`, `usePage`, `usePageTitle` — the app's own instances |
| `components` | the component library |
| `api`, `stores`, `navigate`, `toast` | the API client, the stores, navigation and toasts |

Every `add` returns a function that removes what it added. A contribution
that throws while rendering shows a compact ErrorCard in its place; the rest
of the page keeps working. An extension module that fails to load is reported
by an error toast. An extension's override catalogues are listed in the
manifest after the base catalogue of each locale and merged over it.

## Stores

Stores are signals: a component that reads `store.x.value` while rendering
re-renders when it changes.

- **Project state** (`core/stores/project.js`): the six-state validity of every
  stage (`never built`, `up to date`, `needs update`, `running`, `failed`,
  `skipped`), grouped by area, from `GET /api/project/state`. The last state
  read is kept in the browser per project, so the status dots render at once,
  then refresh.
- **Jobs** (`core/stores/jobs.js`): `GET /api/jobs`, read by **one poller for
  the whole app**: every second while a job is queued or running or the
  Activity drawer is open, every twenty seconds otherwise, paused while the
  tab is hidden. When a job ends the project state refreshes and a toast
  says so. A failed job stays listed until dismissed.
- **Preferences** (`core/stores/prefs.js`): theme and interface language, in
  the browser's local storage; `core/boot-theme.js` applies the theme before
  the first paint.

## The API client

`core/api.js` sends JSON with `credentials: 'same-origin'`, the interface
language as `Accept-Language`, and on every POST, PUT, PATCH and DELETE the
CSRF header the manifest names, with the session's token (the manifest's
`security.csrf_token`, else the `cartolex_csrf` cookie). A call never throws
for an HTTP or network failure; it resolves to a result:

| result | when |
| --- | --- |
| `{ok: true, status, data, etag}` | 2xx; `etag` is the resource's version |
| `{ok: false, kind: 'stale', status: 412, etag, error}` | a write sent with `ifMatch` and refused because the resource changed: `etag` is the current version, for « reload and merge » |
| `{ok: false, kind: 'http', status, error}` | any other 4xx or 5xx |
| `{ok: false, kind: 'network', error}` | no answer |
| `{ok: false, kind: 'aborted'}` | the signal was aborted |

`error` is the ErrorCard model built from the server's
`{"error": {"code", "message", "next": {"label", "action"}}}`. An ErrorCard
shows plain words (the catalogue's `error.<code>.*` when it knows the code,
else the server's words), a « What to do » button, the technical details
folded, and « Copy a diagnostic »: app and version, page, language, time,
error code, HTTP status, method, the request path without its query and with
every identifier-like segment replaced by « … », the request id. Never
project data.

The button runs the error's next action. The interface runs `retry` (the
page's `onRetry`, else a reload), `reload` (the page's `onReload`: read again
and merge; else `onRetry`), `settings`, `open-project` and `build` (their
pages), `wait` (the Activity drawer), `report` (unfolds the details and copies
the diagnostic), and an address (`open:<path>` or a path, for extensions).
`confirm` and `fix-input` get a button only when the page passes `onAction`;
`unlock`, `sign-in` and `none` are told in words, without a button. In a
language other than English the catalogue's words for the action
(`error.action.<action>`) replace the server's label.

## Interface language

The interface language is the person's preference, apart from a project's
keyword languages. The interface speaks English, French and Portuguese
(Brazil); Spanish is not an interface language. Catalogues are flat JSON files
(`"$format": "cartolex-i18n/1"`) of dotted keys; messages use the ICU subset
of `core/i18n.js`: `{name}`, `{n, number}`, `{n, number, percent}`,
`{d, date, short}`, `{items, list}`, `{n, plural, =0 {…} one {# row} other {# rows}}`,
`{n, selectordinal, …}`, `{kind, select, … other {…}}`. Numbers, dates,
percentages, durations and lists are formatted with `Intl` for the interface
language. Stage names come from `stage.<id>` when the catalogue has them, else
from the API.

## Design

Black and greys on white, or the reverse; one accent (`--cx-accent`) for the
active navigation item and links, used rarely. The theme follows the system
setting unless the person chose one. **State is coded by shape**: a full dot
(up to date), a half dot (needs update), an empty dot (never built), a ring
with a gap (running), a cross (failed), a dash (skipped). Only errors and
warnings carry a hue, and always with an icon and a word. The system font
stack only (no web font); tabular figures in tables and counts.

A host application may set its own accent, per theme, in the manifest's
`branding.accent` (`{"light": "#rrggbb", "dark": "#rrggbb"}`); the shell
passes it to the style sheet as `--cx-brand-accent-light` and
`--cx-brand-accent-dark`, which replace the accent and the focus ring.

Data, not state, has hues of its own: the twelve families `--cx-hue-1` to
`--cx-hue-12` colour the top-level themes in the treemap and on the map (3:1
on the page, checked with the other tokens); the accent marks the selection
only.

Every colour, size and duration is a token (`--cx-*`) of `css/tokens.css`.
The contrast check computes the WCAG ratio of every text token on every
background token in both themes (at least 4.5:1, muted text included) and of
every control boundary and the focus ring (at least 3:1); `--cx-rule` is a
decorative separator and exempt. Motion respects `prefers-reduced-motion`.

## Components

Every component is keyboard-operable, has a visible focus, the ARIA roles of
its pattern and every state (default, hover, focus, disabled, loading, error,
empty, long text); the gallery shows them all.

| component | notes |
| --- | --- |
| Button, IconButton | variants primary, secondary, ghost, danger; loading keeps its place and focus; an IconButton's label is its name and tooltip |
| Card | titled region, actions, footer, loading placeholder |
| StatusDot, StatusPill, StageTracker | the six states by shape; the tracker shows reasons, the last failed attempt and progress (« phase 4 of 7 ») |
| Stepper | done steps are buttons back; `aria-current="step"` |
| Tabs | one tab stop; arrows, Home, End; automatic or manual activation |
| Table | virtualised (10⁵ rows), sortable headers, arrows, PageUp/Down, Home/End, Shift for ranges, Space, Ctrl+A, Enter, Escape, context menu; rows keyed, so updates keep the scroll, the focus and the selection |
| Menu, MenuButton, ContextMenuArea | arrows, Home/End, type-ahead, radio and checkbox items, groups; Escape gives the focus back; right click, Shift+F10 or the Menu key |
| Dialog, Drawer, ConfirmDialog | native `<dialog>`, focus trap, Escape, focus returned |
| Toast | two live regions (polite, assertive); errors stay until dismissed |
| FormField, Input, Textarea, Select, Checkbox | label, help, error in words, « required » in words |
| EmptyState | always names the next action |
| ErrorCard | see the API client above |
| ProgressBar | never goes back; indeterminate without a value |
| Tooltip, Help | hover and focus, dismissible with Escape; Help opens an explanation |
| AiHandoffDialog | export a bundle of terms with its instructions (what it contains, what it never contains), paste the answer, review, import |
| ActivityIndicator, ActivityDrawer | « Building · keywords 45 % »; jobs with progress, Stop, results, errors |
| TreeView | a virtualised tree (thousands of rows): the tree pattern's keyboard, ranges, type-ahead, context menu, drag and drop, a caller's own keys |
| Treemap | squarified nested rectangles by weight, one hue family per top-level node, labels that fit or are cut, zoom, drop targets |
| MapFrame | points on a canvas behind a renderer interface: pan, zoom, fit, hit testing, highlight, resize, theme changes; 10⁴ points pan at 60 frames a second |

## Rules

- **No inline script, no inline handler, no `eval`.** The app's
  Content-Security-Policy is `default-src 'self'` with nothing inline and
  nothing evaluated. Styles are classes and CSS custom properties set through
  the CSSOM (`style=${{'--cx-x': …}}`), never a `style` attribute from a
  string. No `innerHTML` with anything but a constant, no
  `dangerouslySetInnerHTML`.
- **No literal text.** User-visible text in templates comes from the
  catalogues. The lint allows only text inside `<code>` (identifiers, token
  names) and text without a letter (punctuation, digits, symbols).
- **Teardown.** A page releases everything it took: listeners, timers,
  subscriptions, requests. Preact effects' cleanups run at teardown; anything
  else goes through `ctx.onLeave()`. Calls go through `ctx.api` or
  `ctx.keep()`, so their late answers are dropped.
- **No bare import** outside `vendor/`, and no import cycle in `core/`.
  Modules import the libraries through `core/preact.js` only.
- **Vendored files** change only through `tools/vendor_ui.py`, which checks
  each npm archive's integrity, rewrites bare specifiers to relative paths,
  removes the source-map comment and writes `VENDOR.md` with the SHA-256 of
  every file.

## Running it

The gallery (`/gallery`) shows every component in every state, with switchers
for the theme and the language. Without the app's server:

```bash
python tools/ui_fixture_server.py            # prints http://127.0.0.1:<port>/gallery
```

The fixture server stands in for the app: it serves `static/`, the test
extension at `/static/ext/demo/`, `GET /api/app/manifest`
(`tests/fixtures/ui/manifest.json`: the shape of the contract's copy
`tests/fixtures/manifest.example.json`, with the core pages and the test
extension), `GET /api/project/state`, `GET /api/jobs` and
`POST /api/jobs/<id>/cancel` (`tests/fixtures/ui/`), the shell for every other
path, and the app's Content-Security-Policy on every answer.

The core pages' modules (`pages/people.js`, `keywords.js`, `map.js`,
`share.js`) are placeholders until their screens are built;
pages placed in `settings` are listed in the header's settings menu. The
themes screen (`pages/themes.js`, {doc}`themes-editor`) is built, and so are
the settings (`pages/settings.js`: one module per section under
`pages/settings/`, the section in the address, `/settings?section=build`; each
section reads what it shows when it opens) and the start screen
(`pages/start.js` and `pages/start/`: recent projects, the demo project, a new
project; `/start`, and `/start?new=1` for the form). A screen
is split into modules of a few hundred lines each, under a folder named after
it, with a small entry module: `pages/themes.js` loads `pages/themes/editor.js`,
which puts together the outline (`outline.js`, `rows.js`, `review.js`), the
treemap and map panels (`treemap.js`, `map.js`, `centre.js`), the side panel
(`panel.js`), the actions and operations (`actions.js`, `operations.js`,
`dialogs.js`), the draft store (`store.js`), the versions (`versions.js`) and
the AI handoff (`handoff.js`), over the tree's model (`model.js`,
`labels.js`). Static modules cost nothing after the first load (they are
cached), so the budget of a navigation counts API calls only. The fixture
server answers the themes screen with a small tree
(`tests/fixtures/ui/themes.example.json`).

## Checks

```bash
python tools/ui_check.py                       # the static checks
python tools/check.py --only js browser         # both checks, as before a merge
python tools/check.py --quick --only browser    # without the slow browser tests
pytest tests/browser --ui-screenshots DIR       # also write the review screenshots
```

`js` (`tools/ui_check.py`) parses every module with Node's own parser (the
Node that comes with Playwright's Python package; skipped without Node),
resolves imports, runs the literal-text lint and the bans, checks the vendored
hashes, the three catalogues (same keys, valid messages, the same arguments in
every language, every literal key present) and the contrast of the tokens.

`browser` runs `tests/browser/` in headless Chromium through Playwright, with
an empty home folder and every request off the loopback interface refused; a
console error, an uncaught exception or a CSP violation fails a test:

- **axe-core** (vendored in `tests/browser/vendor/`) on the gallery in both
  themes and three languages, on the shell's pages and on open dialogs, the
  drawer and menus: no serious or critical violation;
- **keyboard scripts**: Tab reaches every interactive section in order, and
  each component is operated with the keyboard alone;
- **budgets**: every navigation ready in under 1 s with at most 5 API calls
  (static modules and style sheets, cached after the first load, and the jobs
  poller's own reads are not counted);
- **teardown and leaks**: ten round trips between the gallery and the
  overview; DOM nodes and event listeners within 2 %, the JS heap within 10 %,
  after a forced garbage collection;
- the shell's behaviour: start order, cached status dots, routing and focus,
  guards, late answers dropped, the extension API, the API client, the
  Activity drawer;
- the theme editor on the real app (`tests/browser/test_theme_editor.py`, with
  `app_harness.py`): the S demo world built as a project once per session,
  then each scenario on a fresh copy served by the app on a free loopback port
  (see {doc}`themes-editor`): the main flows, a keyboard script of the menu
  actions, axe, and one budget on the L world (marked `slow`). A test waits
  with a predicate that returns a boolean, never an element: an element handle
  would keep a page alive and read as a leak.

The browser tests need `tools/requirements-browser.txt` (Playwright, which
`tools/check.py` installs into the quick Python's environment) and a Chromium
build for it (`python -m playwright install chromium`); without either they
are skipped with the reason, and the rest of the suite runs. Firefox is not
run: no Firefox build for this Playwright version is installed on the
reference machine.
