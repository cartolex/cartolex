# The offline site and the Share screen

`cartolex.site` builds the **offline site** of a project: a folder that opens
in any browser from the computer (`file://`), with no server, no network and no
web font, to send to the people the map is about. The Share screen (`/share`)
builds it, lists the builds, and makes figures, tables and files.

## A build

`build_site(project, SiteOptions(names, texts, title, language, versions))` writes
`outputs/sites/<date>_<time>/` (a second build in the same second gets `-2`):

```text
index.html     the one page: no inline script or style; it first shows
               « unzip the whole folder first », which the scripts remove
README.txt     starts with « UNZIP THE WHOLE FOLDER FIRST », then what the site holds
site.json      the build's record: options, counts, sizes, the inputs' fingerprint
assets/        tokens.css (the app's, copied), atlas.css and atlas.js (the app's atlas),
               site.css, site.js, i18n.js (en, fr, pt-BR), world.js (when organisations
               have an address), cloud-light.svg and cloud-dark.svg (the lexicon's word
               cloud, when the project has a lexicon)
data/          core.js (every page), orgs.js, links.js (who writes with whom),
               people/<n>.js, keywords/<n>.js and texts/<n>.js (only when texts are
               asked for), layout-<id>.js (each other map version carried)
```

**Map versions.** A site carries the built map versions `SiteOptions.versions`
names (every built one by default, the pinned first; one that is not built: 404
`map_version_not_built` from the API). `data/core.js` is placed on the first, as
a site always was, and adds `dimensions` (2, or 3 for a map in space: every
place then has a `z`) and `versions` (the versions carried:
`[{id, dimensions, method, note, pinned}]`). Each other version is
`data/layout-<id>.js` (`window.CX_SITE["layout-<id>"]`): its places only, as
columns in the core's rows, `{id, dimensions, people, keywords, orgs, projected,
nodes, bounds}`, each of `people`…`nodes` `{x, y[, z]}`. `site.json` lists
`versions`; the plan's summary lists them too, and `site_bytes.layouts`
estimates what the layouts add (8 bytes a coordinate; 7.2 measured on the large demo world).

A person's details and texts are in the part `(number − 1) mod n` of their
site id (`s<number>`), a keyword's users in the part `index mod n` of its place
among the core's keywords, *n* in `core.shards` (`people`, `keywords`, `texts`),
chosen so that a part holds about 2 MB (`SHARD_BYTES`): a page loads the part
of what it shows, so a national site's gigabytes of titles never load at once.
The parts are written one at a time. `site.json`'s format is `cartolex-site/3`.

The build is written under a hidden name and renamed when complete, so it is
never half-written and never replaces another; `outputs/sites/latest` names
the newest. `site.json` keeps a **fingerprint** of what the build read (the
runs of the stages, the tables, every decision file and `project.json`); a
build whose fingerprint differs from the project's now is **stale**, and the
share area of the project state is then « needs update ».

A page opened from `file://` cannot load ES modules or read a JSON file, so
everything is a classic script: the data files set `window.CX_SITE[<part>]`,
and `assets/atlas.js` is the app's atlas (`cartolex/app/static/atlas/` and the
map's modules, `ATLAS_MODULES`) turned into one script by
`cartolex.app.static_files.classic_script` (`window.CartolexAtlas`), with its
style sheet `assets/atlas.css` and the `atlas.*` messages of the app's
catalogues in `assets/i18n.js`. The site's own scripts
(`cartolex/site/assets/*.js`, joined into `assets/site.js`) put what they share
on `window.CxSite`.

## One atlas for the app and the site

The site has no map of its own: its Atlas page mounts the app's atlas
(`mountAtlas`, see {doc}`atlas`) with the site as its data source and its host.

- **The source** (`assets/source.js`) answers from the site's files in the
  shapes of the app's API: `bundle({version})` (`cartolex-atlas/3`, made in the
  browser from `data/core.js`'s columns, with `dimensions`, `versions` and `z` on
  a map in space; another version carried: the core's bundle with the places of
  `data/layout-<id>.js`, loaded the first time it is shown, about 1 MB for 15,000
  people in 3D, and kept; with more than one version the source says `layouts`,
  so the atlas offers its « Layout » select; a version the site does not carry
  answers an error and the atlas goes back to the first), `keywordUsers` (`data/keywords/<n>.js`),
  `coauthors` (the atlas's `ringsOf` over the sparse lists of `data/links.js`,
  read the first time the network is asked for), `compare` (the cosine of the
  two vectors, the top-level themes in common, the texts written together),
  `keywordsOf` and `land`. The site has no texts on the map and no time
  windows: those methods are left out, and the atlas does not offer them.
- **The host** (`assets/atlas.js`): the site's catalogues, its light or dark
  look, the fragment's query as the atlas's address (`#/map?sel=person:s3`),
  the browser's storage for the layout and the colour scheme, the pseudonyms
  (`label`), and links to a person's or an organisation's page (no editing).

## What it holds

`cartolex.site.data.gather` reads the atlas bundle (`GET /api/atlas`'s
`build_bundle` and `map_extras`), the space of the themes
(`cartolex.app.space_index`) and the co-authorship graph
(`cartolex.app.coauthors`), and writes:

- `core`: the bundle as columns: the theme tree; the people (place, the
  theme shares of each level as `[node, thousandths…]`, at most 10 per level
  and none under 0.5 %,
  their organisations); the keywords; the organisations; the projected people;
  the years; what the site can answer (`has`);
- `people` and `orgs`: each one's keywords (15, the most used first) and vector
  in the space of the themes (int8, base64), for « Compare »;
- `keywords`: each keyword's users (`[count, person, thousandths, …]`, the 100
  whose use it holds the largest share of);
- `links` (`site_links`): who writes with whom over the site's own indexes,
  as CSR lists (`ptr`, `nbr`, `cnt` texts together, the strongest first, and
  `hidden`: partners in the project the site does not carry, only counted):
  `people` (the people on the map in the site's order, then the projected
  people the site names; `outside` counts co-authors outside the project) and
  `orgs` (pairs of organisations of one level). Under pseudonyms the indexes
  follow the pseudonyms, so the links name nobody; the rings are found in the
  browser.

The real nearest neighbours are no longer computed: the atlas shows real
links (co-authors), and similarity only in « Compare ».

- **Names** are shown only when the build says so; a site of people asks at
  each build (the API refuses a build without the answer, 422
  `names_question`). With pseudonyms the site holds no name, and people get
  site ids (`s1`, `s2`…) in a shuffled order. Organisations are always named.
- **Projected people** (placed on the finished map, possibly a sensitive set
  such as applicants) have their own question, `names_projected`, asked only
  when the project has some: pseudonyms (shuffled `q1`, `q2`…) unless named
  explicitly, and then listed among the checks to look at. Unless named they
  are left out of the site's links (a pseudonym beside a named co-author would
  say who it is), and the name of their set is never carried.
- **Texts**: none by default; `titles`, or `abstracts` (titles and abstracts),
  read through `shareable_parts()`, so a full text never goes in. A text is an
  entry per mapped author (`cartolex.site.data.SiteTexts`: two arrays over the
  app's texts view); the abstracts are read once into a scratch file and each
  part `data/texts/<n>.js` is made from its people's entries alone, so the
  memory stays small whatever the texts (on 2.5 million entries of a national
  sample: 2.5 GB of site, 4.3 GB of memory at most, against 5.6 GB when every
  text was held). The plan says what the texts would add (`text_bytes`: the
  titles counted, the abstracts estimated at 1,120 bytes each, the average of
  that sample) and warns when it passes 500 MB (`abstracts_large`,
  `titles_large`): on the national project the titles add 1.1 GB, the
  abstracts 8.7 GB (estimated).
- Never a project id, an identifier, a role, or the extra columns of the people's lists.

`cartolex.site.checks.plan` gives the privacy summary and the checks before
publishing: `no_map` (blocks), `names_unanswered` (to answer), `names_shown`,
`projected_names_shown`, `abstracts_included`, `abstracts_large`, `titles_large`,
`site_large`, `map_stale`, `themes_untranslated` (the same name in
every display language), `themes_technical`, `themes_empty`, `title_generic`
(to look at), `full_texts_kept` (good to know); each with the fix the screen
offers (build the map, open the themes, change a field).

## The pages

Home (the lexicon's word cloud, a link to the atlas; search a person, an
organisation, a keyword or a theme, arrows moving through the results; the
themes), Atlas (the app's atlas: the treemap of the themes, the
map, the card of links; its own Find, Back, Home, panes and full screen; the
page is the window's height and the atlas fills what the header and the footer
leave, whatever its card holds), a
page per person and per organisation (themes per level, keywords,
organisations or members, who they write with, texts when carried; « Show on
the atlas », « Print this page »), Index (people, organisations and keywords
as searchable, paginated lists) and Method (what distances mean, who writes
with whom, what the site holds). Routes are in the fragment (`#/person/s3`);
the themes' pages of earlier sites (`#/themes/<id>`) open the theme in the
atlas; an unknown route says « Not found ».

The site speaks English, French and Portuguese (Brazil)
(`cartolex/site/i18n/`); the build chooses the one it opens in, and the reader
can switch. The theme follows the system unless the reader chooses light or
dark. It reflows down to 390 px, and prints without the navigation and the
controls, the site's notice heading every page and folded details open.

The plan also estimates what the atlas's data would weigh (`site_bytes`, from
the counts and the project's co-author pairs, `cartolex.site.data.estimate_bytes`,
by sizes per item measured on the large sample below): `core`, `links`, the
`parts` read one at a time, and `atlas` (`core` and `links`, what the atlas
reads at most at once). Past 50 MB (`LARGE_ATLAS_BYTES`, a project of about
170,000 people) it warns (`site_large`, `size` and `total` in bytes): the site
may be slow to open on an ordinary computer.

## The word cloud

The home page shows the lexicon's word cloud, the one of the app's Lexicon
screen: the build draws it with the same function
(`cartolex.app.lexicon_view.word_cloud`, the `wordcloud` package, at most
`CLOUD_WORDS` = 200 keywords, the most important first, in the vendored Lato
embedded in the SVG), coloured by top-level theme in the interface's hues (the
atlas's default scheme, Vivid), once for each look: `assets/cloud-light.svg`
and `assets/cloud-dark.svg`, of which the style sheet shows the page's. It is in
the language the site opens in when the project displays it, else in the
project's first display language. A project without a lexicon (or a lexicon
without a keyword) gets no cloud (`core.has.cloud`). Keywords are not personal
data: the cloud carries no person.

## Figures, tables and files

`cartolex.site.exports`: `map_figure` (the map as PNG or SVG at a size in
pixels, light or dark, drawn with matplotlib and the app's tokens; people are
never named), `theme_table` (the tree as CSV), `write_map_bundle` (the
portable map bundle, `map_bundle/3`) and `write_project_zip` (the project
folder without `cache/`, the staging area, the lock and earlier exports), the
last two as jobs writing dated files into `outputs/exports/`.

## Measures

The L demo world (329 people on the map, 35 placed, 48 organisations, 2 777
keywords, 84 themes), built without names, before (`cartolex-site/2`, the
site's own map) and after (`cartolex-site/3`, the app's atlas):

| | before | after |
| --- | --- | --- |
| size, without texts | 0.71 MB | 1.13 MB |
| size, with titles | 1.24 MB | 1.67 MB |
| build, without texts | 1.8 s | 1.9 s |
| build, with titles | 1.4 s | 1.5 s |
| home ready (Chromium, `file://`) | 0.14 s | 0.14 s |
| the map drawn | 0.3–0.4 s | 0.5 s (the atlas, treemap and card) |

The word cloud adds two files of 20 KB and 2 s to the build (reading the
lexicon, 0.6 s on the L world, then two drawings of 1 s each; the drawing does
not grow with the project, at most 200 words; reading the lexicon does, as the
app's Lexicon screen does).

`data/core.js` is 0.24 MB (the theme shares of every person), the keywords'
users 0.28 MB, the atlas's script 0.23 MB, the people's parts 0.14 MB, the
links 0.03 MB.

A sample of 86 543 mapped people (1 million texts, 21 790 organisations,
9 938 keywords): gathering the data took 74 s and 4.2 GB at most (123 s and
3.4 GB before: the nearest neighbours are gone, the co-authors take 26 s);
`data/core.js` is 14 MB (5 MB before, when every person's themes were in the
details), the people's parts 35 MB, the keywords' users 7 MB, the links 18 MB
(946 000 pairs). In Chromium, from `file://`: the core read in 0.4 s, the atlas
bundle made from it in 0.3 s, the links read in 0.3 s; 180 MB of memory.

## Checks

`tests/test_site.py` (the XS world): a pseudonymous site holds no name and no
text; the home page's word cloud, for each look, and none without a lexicon;
the atlas's data (theme shares per level, organisations, keywords' users,
vectors); the links are the app's co-authorship graph over the site's
own indexes, organisations paired within a level, unnamed projected people
left out; titles and abstracts never carry a private part; builds never overwrite
each other and go stale after a decision changes; the share routes; the
site's tokens equal the app's and its catalogues are complete.
`tests/browser/test_offline_site.py` opens a built site from `file://` in Chromium with
every request refused (and in Firefox when a build of it is installed), walks
from the home page's word cloud and the search to a person's page and on to the atlas mounted over the
site's files, checks that the atlas fills the window and keeps its height as
its card fills, hides and comes back, and the message a page shows without its
files.
