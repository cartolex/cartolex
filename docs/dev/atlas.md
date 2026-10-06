# The atlas: one interface for the app and the offline site

The atlas (the treemap of the themes, the map and the card of links) is **one
piece of code** that both the app's `/map` screen and the offline site mount.
Neither host has a copy of its own: a host gives the atlas a **data source**
(where the numbers come from) and its **capabilities** (what it can do beyond
showing them), and the atlas draws everything else. A feature of the atlas is
written once, in `cartolex/app/static/atlas/`, and reaches both hosts.

```text
cartolex/app/static/atlas/
  atlas.js     mountAtlas(root, {source, host}): puts the parts together
  dom.js       h() and svg(): elements without a library; listeners released at once
  data.js      the bundle indexed: theme tree, people, organisations, keywords, members
  state.js     the atlas's state, its history (Back) and its address
  schemes.js   the colour schemes (one colour per theme, one scale), dark and bright
  scene.js     the map's scene: layers, fading, organisation tiles, arcs, labels, world view
  treemap.js   the treemap of the themes, explorable down to keywords
  card.js      the card of links of what is in focus
  parts.js     the card's pieces: links, bars, sections, lists, the rings as lists
  compare.js   Compare: two people or two organisations side by side
  rings.js     the network around the focus (co-authors, partner organisations)
  layers.js    the small layers panel over the map
  filters.js   the folded « Filters » control (people's columns, period, categories)
  find.js      « Find »: a combobox over everything the atlas shows
  panes.js     the panes: resizable, hidable to rails, card right or below, full screen
  mapview.js   the map: canvas, controller, hover card, its own buttons, Save's menu
  save.js      « Save view »: PNG and SVG of the map as it is on screen
```

These modules use **no library** (no Preact, no htm): they build the DOM
with `dom.js` and draw the map with the library-free modules of
`components/map/` (see {doc}`ui`). They follow the rules that let a page opened
from `file://` load them as one classic script: they import only each other
and those modules, by relative path (`./x.js`, `../components/map/core.js`),
with named imports; they export only declarations (`export function`,
`export const`); top-level names are unique across every module of the script
(the build refuses a duplicate); they touch no global but the root and the
document they are given.

## Mounting

```js
// the app (an ES module)
import { mountAtlas } from '../../atlas/atlas.js';
// the offline site (the classic script sets window.CartolexAtlas)
const { mountAtlas } = window.CartolexAtlas;

const atlas = mountAtlas(root, { source, host });
atlas.select({ kind: 'person', id: 'p0001' }, { centre: true });
atlas.setScene(scene);        // a host's own scene in place of the map's (null: the map's)
atlas.slot('bar');            // an element in the bar for a host's own buttons
atlas.slot('map');            // an element over the map (a host's banner)
atlas.hold({cardOn: false});  // a part of the layout held for a while, never kept (null: let go)
atlas.refresh();              // read the bundle again (the map was rebuilt)
atlas.state(); atlas.set({net: 2});   // the state (see below), and a change of it
atlas.index(); atlas.colours();       // the indexed bundle, the themes' colours now
atlas.map();                  // the map's controller (fit, zoomBy, centreOn, view…)
atlas.destroy();              // every listener, observer, frame and request released
```

`tests/browser/atlas_file.py` is a complete, small host: a page opened from
`file://`, a synthetic bundle, a translator over the catalogue's `atlas.*` keys,
the rings computed by `ringsOf` from a list of links.

`root` is an empty element; the atlas fills it and takes its whole size (the
host gives it a height). `mountAtlas` returns at once; the atlas shows
« Loading » until the bundle is read. Its style sheet is
`static/css/atlas.css`, which reads the `--cx-*` tokens of `css/tokens.css`
(the site ships both).

## The data source

Every method answers a promise. Only `bundle` is required; without an optional
method the part that needs it is not offered (no network control without
`coauthors`, for example), never shown broken. A method may reject or answer
`{error: {code, message}}`: the part says « could not be read » and the rest
of the atlas keeps working.

| method | answers |
| --- | --- |
| `bundle()` | **required.** The atlas bundle, the shape of `GET /api/atlas` (`cartolex-atlas/3`, {doc}`api`): `levels`, `nodes`, `people`, `keywords`, `organisations`, `organisation_levels`, `people_extra`, `columns`, `years`, `overlays`, `windows`, `window_years`, `bounds`, `map_version`. Of these, `nodes`, `people`, `keywords` and `bounds` are required; the others may be left out (empty) |
| `keywordUsers(term, {limit})` | the people who use a keyword: `{known, count, items: [{id, name, share}], at: [indexes in people]}` (`GET /api/atlas/keyword-people`) |
| `coauthors({kind, id, circle, pages})` | who writes with a person or an organisation, `circle` rings (1 to 3), the shape of `GET /api/atlas/coauthors` (below); `pages` is `[[offset, limit]…]` per ring |
| `compare(a, b)` | two people or organisations (`{kind, id}`): `{space, keywords: {cosine, jaccard, common, shared}, themes: {overlap, shared}, texts: {shared, items}}`, any part may be missing (`GET /api/atlas/compare`) |
| `keywordsOf(kind, ids)` | the keywords of people or organisations: `{id: [term…]}`, most used first (`GET /api/atlas/regions`) |
| `texts()` | the texts placed on the map, columnar (`GET /api/atlas/texts`) |
| `windows({person})` | the time windows, columnar, every one or one person's (`GET /api/atlas/windows`) |
| `land()` | the outline of the land for the world view: rings of `[lon0, lat0, lon1, lat1…]` |

Names: a person's `name` may be `null` (a site built with pseudonyms, a
projected person): the atlas then shows the host's label for them
(`host.label`), else `t('atlas.person.unnamed', {id})`. The atlas never asks a
source for a name the bundle left out.

### The network rings

`coauthors` answers, for `{kind: 'person', id, circle: 2}`:

```json
{"id": "p0001", "circle": 2, "texts": 31, "outside": 4, "max_authors": 30,
 "count": 7, "placed": 6, "partial": false, "offset": 0, "limit": 50,
 "items": [{"id": "p0007", "name": "…", "role": "mapped", "mapped": true,
            "place": "map", "texts": 5}],
 "lines": [["p0007", 5]],
 "second": {"count": 12, "placed": 10, "partial": false, "offset": 0, "limit": 50,
            "items": [{"id": "p0020", "name": "…", "place": "projected", "texts": 2,
                       "paths": 1, "via": ["p0007"]}],
            "lines": [["p0007", "p0020", 2]]}}
```

`items` of the first ring and `second`, `third` for the others, each paged;
`place` is `map` (drawn among the people), `projected` (drawn as a projected
person: a collaborator collected but not mapped) or `null` (not drawn);
`lines` are the links to draw, the first ring's from the focus. For
`kind: 'organisation'` the items are organisations of the focus's level
(`{id, name, acronym, place, texts}`) and the answer names the `level`.
`outside` counts the co-authors who are not in the project (never listed);
`hidden`, when a host gives it, counts partners in the project it does not show
(a site that leaves some people out): the card says both as counts.

A host whose data is a sparse list of links (the offline site) does not write
its own search: `ringsOf(graph, id, depth, pages)` of `atlas/rings.js` answers
this shape from `graph = {neighbours(id) → [[id, texts]…], describe(ids) →
items, placed(id) → place}`.

## The host's capabilities

`host` is an object; only `t` and `locale` are required.

| member | what it does | app | site |
| --- | --- | --- | --- |
| `t(key, params)` | a message of the `atlas.*` keys (ICU subset of {doc}`ui`) | `core/i18n.js` | `createTranslator` of `core/messages.js` |
| `locale` | the interface language (numbers, percentages, lists) | yes | yes |
| `address` | `{read() → URLSearchParams, write(URLSearchParams)}`: where the state lives | the page's query | the fragment's query |
| `prefs` | `{get(key), set(key, value)}`: the layout and the colour scheme, per person | `/api/me/preferences` | the browser's storage |
| `look` | `{dark() → boolean, set(dark)?, subscribe(fn)?}`: Dark / Bright (without `subscribe`, the atlas watches the document's `data-theme` and the system); with `set`, the bar shows the switch | the theme store | the site's switch |
| `title` | the field's name, at the top of the home card | the project's name | the site's title |
| `onReady({index})`, `onScheme(id)` | told when the bundle is indexed, when the scheme changes | | |
| `links` | `{person(id), organisation(id), text(id), keyword(term), themesKeyword(term), themesNode(id)}` → an address, or `{href, label}` (the host's own words for the button), or null | People, Keywords, Themes | its own pages, if it has them |
| `navigate(href)` | follow one of those addresses | the router | — |
| `label(kind, id)` | the name of what the bundle left unnamed | — | the pseudonym |
| `fileStem()` | the start of a saved view's file name | the map version | the site's title |

Without `links`, the card's « Open in People ↗ », « Open in Keywords ↗ » and
« Edit in Themes ↗ » are not shown: editing happens only in the app's Themes
screen. A host adds its own buttons to `atlas.slot('bar')` (the app: map
versions, distances, the Tune panel's preview) and its own banner to
`atlas.slot('map')`. A host that shows a panel of its own beside the atlas (the
app's « Tune the map ») holds the card on its rail meanwhile (`atlas.hold`); the
person's layout is not changed, and their own choice of that part ends the hold.

## State and address

The state is in the address, so a view can be shared and survives a reload:

| key | what |
| --- | --- |
| `sel` | the focus, `kind:id`: `theme`, `keyword`, `person`, `organisation`, `projected`, `text` |
| `with` | the second side of a comparison (`kind:id`), beside `sel` |
| `open` | the node the treemap has opened (its children, or its keywords) |
| `show` | the kinds the map shows (`people`, `keywords`, `organisations`, `texts`, `projected`, `windows`) |
| `names` | the kinds whose names are written on the map |
| `org` | the organisations' level (the project's own level names) |
| `net` | the network's rings, 1 to 3 (0: none) |
| `f`, `from`, `to`, `kc`, `kcol` | the people's filters, the period, the keywords' categories and colour |
| `view`, `base`, `as` | the world view, a base map, points or regions |

The layout (pane sizes, hidden panes, the card's place) and the colour scheme
belong to the person, not to the view: `host.prefs` keeps them under
`atlas.tree`, `atlas.card`, `atlas.below`, `atlas.tree_on`, `atlas.card_on`,
`atlas.card_at`, `colour_scheme` and `colour_order` (the themes' order along a
scale, so the app's other screens order them as the map does).

The atlas's words are the `atlas.*` keys of the app's catalogues (en, fr,
pt-BR); a host passes them to `t`. Its style sheet's classes start with
`cx-atlas`; the map's box carries `cxMap` (the controller) and `cxScene()` (what
is drawn) for the browser tests.

## Colour schemes

`atlas/schemes.js` holds every scheme once: one colour per theme (Vivid,
Soft, Colour-blind safe (Tol muted), Okabe–Ito, Tol bright, Tableau 10,
Dark 2, Set 2, Paired) and one scale (Viridis, Cividis, Magma, Inferno,
Plasma, Yellow–green–blue, Yellow–orange–red, Greens, Blues, Purples, Oranges,
Reds, Greys; the themes are placed along the scale by their place on the map,
left to right). `themeColours(scheme, themes, {dark})` gives the colour of
each top-level theme for the page's dark or bright look; the map, the
treemap, the legends, saved views and the app's Themes screen use it, so a
theme has the same colour everywhere.

## How the offline site packages it

`cartolex.app.static_files.ATLAS_MODULES` lists the modules in dependency
order (the map's, the treemap's layout, `core/messages.js`, then `atlas/`);
`classic_script(ATLAS_MODULES, 'CartolexAtlas')` turns them into one classic
script, `assets/atlas.js`, that sets `window.CartolexAtlas` to everything they
export (`mountAtlas`, `ringsOf`, `createTranslator`, `SCHEMES`…). The site
ships `css/atlas.css` beside its tokens, and the `atlas.*` messages of the
app's three catalogues in its own `i18n.js`. The site's own code is its data
source (from its data files) and its host (no links, its own storage, its
pseudonyms).
