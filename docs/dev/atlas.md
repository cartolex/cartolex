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
  texts.js     the texts drawn: every one, or those of the focus (and its network)
  layers.js    the small layers panel over the map
  filters.js   the folded « Filters » control (people's columns, period, categories)
  find.js      « Find »: a combobox over everything the atlas shows
  panes.js     the panes: resizable, hidable to rails, card right or below, full screen
  mapview.js   the map: canvas, controller (2D or 3D), hover card, its own buttons, Save's menu
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
the rings computed by `ringsOf` from a list of links; and its variants: a map
version in three dimensions, two built versions (flat and 3D) read by
`version`, every person's time windows, the French catalogue, and a large 3D
bundle made in the browser for the frame budget (`tests/browser/test_atlas_3d.py`).

`root` is an empty element; the atlas fills it and takes its whole size. The
host gives it a **definite height** (a `height`, or a flex item's share of a box
that has one), never only a `min-height`: the atlas's `height: 100%` would then
follow its content, the card's lists making it taller than the window and a
hidden card shrinking it to the map's least (the offline site's page is the
window's height for this reason). Narrower than 860 px the panes stack, each
with its own height, and the page scrolls. `mountAtlas` returns at once; the atlas shows
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
| `bundle({version})` | **required.** The atlas bundle, the shape of `GET /api/atlas` (`cartolex-atlas/3`, {doc}`api`): `levels`, `nodes`, `people`, `keywords`, `organisations`, `organisation_levels`, `people_extra`, `columns`, `years`, `overlays`, `windows`, `window_years`, `bounds`, `map_version`, `pinned_version`, `versions`, `dimensions` (below). Of these, `nodes`, `people`, `keywords` and `bounds` are required; the others may be left out (empty). *version* is the map version to show (absent: the pinned one, or the first) |
| `keywordUsers(term, {limit})` | the people who use a keyword: `{known, count, items: [{id, name, share}], at: [indexes in people]}` (`GET /api/atlas/keyword-people`) |
| `coauthors({kind, id, circle, pages})` | who writes with a person or an organisation, `circle` rings (1 to 3), the shape of `GET /api/atlas/coauthors` (below); `pages` is `[[offset, limit]…]` per ring |
| `compare(a, b)` | two people or organisations (`{kind, id}`): `{space, keywords: {cosine, jaccard, common, shared}, themes: {overlap, shared}, texts: {shared, items}}`, any part may be missing (`GET /api/atlas/compare`) |
| `keywordsOf(kind, ids)` | the keywords of people or organisations: `{id: [term…]}`, most used first (`GET /api/atlas/regions`) |
| `texts({version})` | the texts placed on the map, columnar (`GET /api/atlas/texts`) |
| `textsOf({kind, id, net, limit, version})` | the texts of a person, a projected person or an organisation (`net` rings of its network too), from every text of the project, in the shape of `texts()` with `total` and `sampled` (`GET /api/atlas/texts?focus=`); without it, « of the focus » draws a theme's and a keyword's texts among those of `texts()` only |
| `windows({person, version})` | the time windows, columnar, every one or one person's (`GET /api/atlas/windows`) |
| `land()` | the outline of the land for the world view: rings of `[lon0, lat0, lon1, lat1…]` |

The reads of places (`bundle`, `texts`, `textsOf`, `windows`) are given the
`version` the atlas shows when it is not the pinned one; a source with a single
version may ignore it. A version that cannot be read (no longer built) sends
the atlas back to the pinned one.

Names: a person's `name` may be `null` (a site built with pseudonyms, a
projected person): the atlas then shows the host's label for them
(`host.label`), else `t('atlas.person.unnamed', {id})`. The atlas never asks a
source for a name the bundle left out.

### Map versions and maps in space: the data

A project may build several map versions (`decisions/maps.json`: the pinned one
and those marked `built`), flat or in space ({doc}`../format/decisions`). The
bundle says which one it is placed on and which others there are:

| field | what it holds |
| --- | --- |
| `map_version` | the version shown (the pinned one unless `?version=` asked another) |
| `pinned_version` | the pinned version, the reference (distances, exports, themes) |
| `versions` | the built versions, the pinned first: `[{id, dimensions, method, note, pinned}]` |
| `dimensions` | `2` (a flat map) or `3` (a map in space) |
| `bounds` | `xmin`, `xmax`, `ymin`, `ymax`, and `zmin`, `zmax` in space |

On a map in space every place has a `z` beside `x` and `y`: `people`,
`keywords`, `units`, `overlays`, `organisations` (the mean of their current
members, `z` included), `nodes`; the columns of `GET /api/atlas/texts` and
`GET /api/atlas/windows` gain a `z` column. A flat map's bundle has no `z` at
all. People, keywords, units and projected people come in the same order on
every version (the pinned map's), so an index means the same item whichever
version is shown; a unit's `ellipse` is empty in space. The routes that return
places take `version` (`/api/atlas`, `/api/atlas/texts`, `/api/atlas/windows`;
404 `map_version_not_built` when it is not built), and their ETags and caches
are per version. A base places a flat map only: `base` with a map in space is
refused (409 `base_needs_2d`). What does not depend on the places (distances,
nearest people, co-authors, regions, who uses a keyword) is the same on every
version.

The offline site carries the versions it was built with (`SiteOptions.versions`,
every built one by default, the pinned first): `data/core.js` is placed on the
first and gains `dimensions` and `versions`; each other is
`data/layout-<id>.js` ({doc}`site`).

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
| `tx` | the texts drawn: every one (left out), `focus` (those of the focus: a person's or an organisation's own, read with `textsOf`; a theme's, the drawn texts with most of their keywords under it; a keyword's, those that use it) or `network` (with the people the network's rings reach); with nothing in focus, every one |
| `f`, `from`, `to`, `kc`, `kcol` | the people's filters, the period, the keywords' categories and colour |
| `view`, `base`, `as` | the world view, a base map, points or regions |
| `map` | the map version shown (its id); absent: the pinned one, or the first of `versions` |
| `traj` | `1`: the focused person's trajectory, their time windows joined in time order (read only then); absent: off |

The layout (pane sizes, hidden panes, the card's place) and the colour scheme
belong to the person, not to the view: `host.prefs` keeps them under
`atlas.tree`, `atlas.card`, `atlas.below`, `atlas.tree_on`, `atlas.card_on`,
`atlas.card_at`, `colour_scheme` and `colour_order` (the themes' order along a
scale, so the app's other screens order them as the map does).

The atlas's words are the `atlas.*` keys of the app's catalogues (en, fr,
pt-BR); a host passes them to `t`. Its style sheet's classes start with
`cx-atlas`; the map's box carries `cxMap` (the controller) and `cxScene()` (what
is drawn) for the browser tests.

## Map versions and three dimensions

A bundle may list the built map versions, the pinned first:
`versions: [{id, dimensions, method, note, pinned}]`, with `map_version` the one
it holds and `dimensions` (2 or 3) its own. With more than one, the bar shows
« Layout » (`id · 2D/3D · method · note`); choosing one sets `map` and reads the
bundle again through the source (the focus is kept). On a version in three
dimensions every place has a `z` (people, keywords, organisations, projected
people, theme nodes; the `z` column of the texts and the windows) and
`bounds` has `zmin`, `zmax`.

The scene follows (`components/map/core.js`): a scene in three dimensions says
`dimensions: 3`; its layers carry `z` (Float32Array), its lines `z` pairs, its
labels `z`; its regions carry `members` (`{x, y, z}`, the member points, the
farthest from their mean left out) in place of a `polygon`, hulled on screen at
each frame. The map's view swaps its controller by the scene's dimensions
(`mapview.js`, on a canvas of its own): `controller3d.js` (an orbit camera,
`space.js`), drawn by `webgl3d.js` (depth test; the faded traces write no
depth; regions hulled on screen) or `canvas3d.js` (back to front; the fallback,
and the renderer of saved PNGs). The 3D controller answers what the 2D one
answers, `centreOn(x, y, zoom, z)` with a `z`, plus `turn(on)`, `turning()`,
`turnBy(dyaw, dpitch)`, `front()` and `project(x, y, z)` (both controllers
answer `dimensions` and `project`). One tab stop: the arrows turn, Shift and the
arrows pan, + and − zoom, 0 fits, the space bar turns; a drag turns, Shift (or
the right button) pans, the wheel zooms toward the pointer, a click picks the
front-most point.

What stays flat: the world view (its scene has no `dimensions`, so the map
swaps back to 2D and again when it closes), base maps (a base is placed on a
flat version only: the app shows one on the pinned version, leaving `map`, and
offers no base when the pinned version is in 3D; the « Layout » select offers
no 3D version while a base is shown), and a host's own scene (the app's layout
preview).

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
