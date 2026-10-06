# The offline site and the Share screen

`cartolex.site` builds the **offline site** of a project: a folder that opens
in any browser from the computer (`file://`), with no server, no network and no
web font, to send to the people the map is about. The Share screen (`/share`)
builds it, lists the builds, and makes figures, tables and files.

## A build

`build_site(project, SiteOptions(names, texts, title, language))` writes
`outputs/sites/<date>_<time>/` (a second build in the same second gets `-2`):

```text
index.html     the one page: no inline script or style; it first shows
               « unzip the whole folder first », which the scripts remove
README.txt     starts with « UNZIP THE WHOLE FOLDER FIRST », then what the site holds
site.json      the build's record: options, counts, sizes, the inputs' fingerprint
assets/        tokens.css (the app's, copied), site.css, site.js, map.js,
               i18n.js (en, fr, pt-BR), world.js (when organisations have an address)
data/          core.js (every page), details.js (organisation and theme pages,
               the keywords' people), people/<n>.js (the people's details) and
               texts/<n>.js (their texts, only when texts are asked for)
```

A person's details and texts are in the part `(number − 1) mod n` of their
site id (`s<number>`), *n* in `core.shards` (`people`, `texts`), chosen so that
a part holds about 2 MB (`SHARD_BYTES`): a page loads the part of the person it
shows, so a national site's gigabytes of titles never load at once. The parts
are written one at a time. `site.json`'s format is `cartolex-site/2`.

The build is written under a hidden name and renamed when complete, so it is
never half-written and never replaces another; `outputs/sites/latest` names
the newest. `site.json` keeps a **fingerprint** of what the build read (the
runs of the stages, the tables, every decision file and `project.json`); a
build whose fingerprint differs from the project's now is **stale**, and the
share area of the project state is then « needs update ».

A page opened from `file://` cannot load ES modules or read a JSON file, so
everything is a classic script: the data files set `window.CX_SITE[<part>]`,
and `assets/map.js` is the app's own map modules (`MAP_MODULES`) and the
treemap's layout (`components/treemap-layout.js`) turned into one script by
`cartolex.app.static_files.classic_script` (`window.CartolexMap`). The site's
own scripts (`cartolex/site/assets/*.js`, joined into `assets/site.js`) put
what they share on `window.CxSite`.

## What it holds

`cartolex.site.data.gather` reads the atlas bundle (`GET /api/atlas`'s
`build_bundle` and `map_extras`) and adds, per person, the themes of each level,
the keywords, the organisations and the **co-authors**; per organisation its
themes, keywords, members and the organisations of its level it writes with;
per theme its people and organisations (a share of at least a fifth) and
keywords.

- **Who writes with whom** comes from the app's co-author graph
  (`cartolex.app.coauthors`, see `GET /api/atlas/coauthors` in {doc}`api`):
  `cartolex.site.data.site_links` gives it over the site's own indexes as sparse
  arrays (`SiteData.links`: `people` and `orgs`, each `ptr`, `nbr`, `cnt` with
  the works together, the strongest first, and `hidden`, the partners in the
  project the site does not carry; the people's `outside`). Each page reads its
  own: a person's part of the people's details has `co` (flat pairs of a site
  index and the works together; an index past the people is a projected
  person), `co_hidden` and `co_outside`, an organisation's details `co`, a named
  projected person's `details.projected`. Projected people are in the links only
  when the site names them; under pseudonyms the indexes are the pseudonyms'
  order, so the links name nobody. Organisations without a level of the project
  are left out.

- **Names** are shown only when the build says so; a site of people asks at
  each build (the API refuses a build without the answer, 422
  `names_question`). With pseudonyms the site holds no name, and people get
  site ids (`s1`, `s2`…) in a shuffled order. Organisations are always named.
- **Projected people** (placed on the finished map, possibly a sensitive set
  such as applicants) have their own question, `names_projected`, asked only
  when the project has some: pseudonyms (shuffled `q1`, `q2`…) unless named
  explicitly, and then listed among the checks to look at.
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
- Never a project id, an identifier, or the extra columns of the people's lists.

`cartolex.site.checks.plan` gives the privacy summary and the checks before
publishing: `no_map` (blocks), `names_unanswered` (to answer), `names_shown`,
`projected_names_shown`, `abstracts_included`, `abstracts_large`, `titles_large`, `map_stale`, `themes_untranslated` (the same name in
every display language), `themes_technical`, `themes_empty`, `title_generic`
(to look at), `full_texts_kept` (good to know); each with the fix the screen
offers (build the map, open the themes, change a field).

## The pages

Home (search a person, an organisation, a keyword or a theme; arrows move
through the results), Map (the MapFrame's controller: permanent legend, one
symbol per kind, hover card, labels of the selection and with the zoom, lines
from a selected person to their co-authors (the thicker, the more works
together) and dashed from an organisation to those it writes with, the plain
caveat about distances, the world view over the Natural Earth outline), Themes (a
treemap drill-down with the sub-themes as a list too, a small map, keywords,
people, organisations), a page per person and per organisation (position,
themes, keywords, co-authors or the organisations it writes with, texts when
carried; « Print this page »),
Index (people, organisations and keywords as searchable, paginated lists) and
Method (what distances and co-authors mean, in plain words; what the site
holds). Routes are
in the fragment (`#/person/s3`); an unknown one says « Not found ».

The site speaks English, French and Portuguese (Brazil)
(`cartolex/site/i18n/`); the build chooses the one it opens in, and the reader
can switch. The theme follows the system unless the reader chooses light or
dark. It reflows down to 390 px, and prints without the navigation and the
controls, the site's notice heading every page and folded details open.

## Figures, tables and files

`cartolex.site.exports`: `map_figure` (the map as PNG or SVG at a size in
pixels, light or dark, drawn with matplotlib and the app's tokens; people are
never named), `theme_table` (the tree as CSV), `write_map_bundle` (the
portable map bundle, `map_bundle/3`) and `write_project_zip` (the project
folder without `cache/`, the staging area, the lock and earlier exports), the
last two as jobs writing dated files into `outputs/exports/`.

## Measures

Measured once on the L demo world (329 people on the map, 35 placed, 48
organisations, 2 955 keywords, 163 themes), Chromium, from `file://`:

| | without texts | with titles |
| --- | --- | --- |
| size | 0.79 MB | 1.32 MB |
| build | 2.5 s | 0.8 s (the bundle read) |
| home ready | 0.14 s | |
| opened on the map, first frame drawn | 0.3–0.4 s | |
| a person's page (details read) | 0.16 s | |

Since the co-authors replaced the nearest people, the L world's site builds in
the same time (1.2 s, the co-author graph made in it; 0.26 s once kept) and
weighs 0.73 MB. On a national sample (86,500 people on the map, 825,000 texts,
2.7 million authorships) the site's links take 18 s, the co-author graphs
included (the nearest people took 69 s), and add 18 MB spread over the
people's parts and the details.

`data/core.js` is 0.20 MB and the details 0.38 MB (since `cartolex-site/2`,
`data/details.js` and the people's parts); the map's and the
site's scripts together about 0.11 MB, the world outline 0.05 MB.

## Checks

`tests/test_site.py` (the XS world): a pseudonymous site holds no name and no
text; titles and abstracts never carry a private part; builds never overwrite
each other and go stale after a decision changes; the share routes; the
site's tokens equal the app's and its catalogues are complete.
`tests/browser/test_offline_site.py` opens a built site from `file://` in Chromium with
every request refused (and in Firefox when a build of it is installed), and
checks the message a page shows without its files.
