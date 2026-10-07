# Explore the map

**Goal**: read the map of a built project: find a person, see what they work
on and who is closest, follow the links to the other screens, compare two
people or organisations, export distances and save a view as an image.

**You need**: a built project (the demo project of {doc}`first-map` will
do).

```{image} images/map-person.png
:alt: The Map screen with a person selected: the themes on the left, the map in the middle, the person's themes, keywords and nearest people in the panel on the right.
```

## Read the picture

Each point is a person, in the colour of the theme that weighs most in their
texts; two people are close when they use the same keywords in the same
proportions. Under **Show**, add the keywords, the organisations (at the
average place of their mapped people), the texts, the projected people and
the time windows (a person's texts over a few years, each a point).
**Filters** come from your lists' own columns, and **Period** keeps the texts
of some years only. Drag to move, the wheel or + and − to zoom, 0 to fit.

Once the texts are shown, **Texts: all · of the focus · with the network**
chooses which: every text (a sample of a very large corpus), or only those of
what is selected (a person's texts, all of them however large the corpus; an
organisation's members'; a theme's, the texts with most of their keywords in
it; a keyword's, the texts that use it), and with the network the texts of
the co-authors the network's rings reach as well. With nothing selected,
every text is drawn.

The themes beside the map show each theme's share of all use: select one to
light its people and keywords.

**Trajectory**, in the same panel, joins the time windows of the selected
person in time order, the first and last years named: where their work went
over the years. It is off until you turn it on, and stays on (in the page's
address) while you select other people.

## Map versions

**Map versions**, at the top of the map, lists how the map was drawn: each
version's method, seed, note, and whether it is flat (**2D**) or in space
(**3D**). The **pinned** version is the reference: every build of the map
draws it, and the distances, the exports and the themes' places come from it.

- **Nearest kept** says how faithful a built version is: how many of each
  person's nearest people stay nearest on the map (its trustworthiness; 100 %
  would keep them all).
- **Build it too** has a version drawn at each build of the map, beside the
  pinned one (which is always built), so the atlas and the offline site can
  show it. The map is built again to draw the change: **Build the map now**,
  or at the next build. A built version has **Show**.
- **Try another layout** draws a new version: another method, another seed,
  **Flat (2D)** or **In space (3D)** (a UMAP layout only), then **Pin it and
  redraw the map** or **Build it beside the pinned one** (the default for a
  map in space, which leaves the reference flat).

## A map in three dimensions

A map version can be laid out in three dimensions (**Map versions**, **In
space (3D)**): its places keep more of the distances than a flat
map can, and groups that overlap on the flat map may stand apart. Everything
of the flat map works on it: the layers, the selection and what it lights,
the co-authors' lines, the names, Find, the filters, the colours, Save the
view (the picture as it is turned on screen).

- Drag to **turn** it; Shift and drag (or drag with the right button) to
  **move** it; the wheel zooms toward the pointer; a click selects the point
  in front.
- With the keyboard: the arrows turn, Shift and the arrows move, + and − zoom,
  0 fits, the space bar starts or stops **Turn** (turning on its own).
- **Front** shows it from the front, as a flat map: x to the right, y up.
- The **World** view stays flat; so do base maps, shown on a flat version
  only, and the layout preview of **Tune the map** (a 3D layout is built,
  then viewed).

When several versions are built, **Layout** at the top of the map shows one
or another (flat or 3D), each named by its version, method and note; the
page's address keeps the choice.

## Find and follow

- **Find on the map** (type a name, choose with the arrows and Enter) selects
  a person, an organisation, a keyword or a theme; so does a click.
- The panel shows what is selected: a person's organisations, themes and most
  used keywords; an organisation's people; a keyword's users; a theme's
  keywords and the people who weigh most on it.
- Its links open the same thing elsewhere: **Open in People** (the person's
  sheet and texts), **Open in Organisations**, **Open in the lexicon**, **Open in
  Themes**. Those screens lead back to the map, centred on it.
- The address of the page keeps the selection: copy it to come back to the
  same view.

## Co-authors

A person's **co-authors** in the project can be drawn as lines on the map,
from the person to each co-author, and are listed in the panel with the texts
they wrote together. Collaborators collected for the project but not on the
map (people in the *context* role) are listed faintly, since they have no
place to draw. An option adds the **second circle**: the co-authors of the
co-authors.

## Compare and measure

- **Compare with…** puts another person or organisation beside the
  selection: their **similarity**, from 0 (nothing in common) to 1 (the
  same), the keywords both use, the themes both weigh on, the texts they
  wrote together. It is measured from their texts, not on the picture: quote
  this number, not a distance on the map.
- **Distances** exports, as a file, the nearest of each person or
  organisation, every pair (the full matrix), or the vectors, with names or
  pseudonyms. The file is listed on the **Share** screen when it is written,
  with a small `.meta.json` beside it that says how it was measured; a very
  large matrix asks first.

**Tune the map › Distances** chooses the similarity that Compare puts first,
that the exports write and that the nearest follow (no rebuild needed):

| similarity | what it means |
| --- | --- |
| **Meaning in the map's space** (the default) | the cosine of their vectors in the space the map is drawn from: what its reduction keeps of their whole vocabulary, so that keywords used together count as close |
| **Shared vocabulary** | the cosine of their keyword profiles (each keyword's share of their use): the same keywords in the same proportions, word for word |
| **Keywords in common** | the share of their keywords that both use, among those either uses (the Jaccard index), however much each uses them |
| **Shared themes** | how much of their themes they share: the sum, over the top-level themes, of the smaller of their two shares |

The first sees two people who write about the same things in different words
as close; the second and third need the same words; the last only looks at
the themes, so it ignores what distinguishes two people within a theme.

## Save a view

**Save the view** downloads what is on screen as a PNG or SVG image, with or
without its legend, for a slide or a report. The map can also go full screen,
and its side columns fold away. **Map versions** keeps the map stable: the
pinned version is the one every build redraws; try another layout there
without losing the one people know.

## If something is not right

- **Someone is missing**: only *mapped* people with enough texts are on the
  map; projected people appear when **Projected people** is shown. A
  person's sheet (Open in People) says why they have no texts.
- **The map is out of date**: a note says so after your changes; build the map
  again from the overview.

More: {doc}`introduction` (what the map shows), {doc}`tutorial-share`.
