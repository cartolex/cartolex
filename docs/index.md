# cartolex

**cartolex maps a body of texts by the words of their authors.** Give it the
people of a field (a list, a laboratory, an institution): it collects their
texts from open services, finds the vocabulary they actually use, places every
person, organisation and text in a lexical space, groups the vocabulary into
themes, and draws it all as an atlas you explore and share as a website that
opens offline.

**Who talks the same.** Distances in that space measure how much people, teams
or institutions talk the same about their subject, whether or not they work
together; the co-authorship network, drawn on the same map, shows who does.
The map shows what people write about, never how good their work is. It was
made for research fields; any texts whose authors are known will do, the
articles of journalists for instance.

No programming is needed: everything happens in the app, in your web browser,
on your own computer, and your data stays there ({doc}`privacy`). These pages
come with the app, for the version you have installed (the question mark at
the top of every screen).

**To install it: [download the installer kit](https://github.com/cartolex/cartolex/releases/latest/download/cartolex-installer.zip)**, unzip it and start the
launcher of your system ({doc}`install`).

## How a map is made

<figure class="cx-docs-figure">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 178" width="100%" role="img" aria-labelledby="cx-flow-title cx-flow-desc" font-family="inherit" font-size="13" fill="currentColor">
<title id="cx-flow-title">From texts to a shared map</title>
<desc id="cx-flow-desc">Five steps from left to right: people and texts, keywords, themes, the map, sharing. Under each, what cartolex does and what you decide.</desc>
<g fill="none" stroke="currentColor" stroke-width="1.5">
<rect x="4" y="8" width="132" height="56" rx="8"/>
<rect x="160" y="8" width="132" height="56" rx="8"/>
<rect x="316" y="8" width="132" height="56" rx="8"/>
<rect x="472" y="8" width="132" height="56" rx="8"/>
<rect x="628" y="8" width="128" height="56" rx="8"/>
<path d="M138 36h18M294 36h18M450 36h18M606 36h18"/>
<path d="M150 31l6 5-6 5M306 31l6 5-6 5M462 31l6 5-6 5M618 31l6 5-6 5"/>
</g>
<g font-weight="600" font-size="15" text-anchor="middle">
<text x="70" y="41">People and texts</text>
<text x="226" y="41">Keywords</text>
<text x="382" y="41">Themes</text>
<text x="538" y="41">Map</text>
<text x="692" y="41">Share</text>
</g>
<g font-size="12" opacity="0.85">
<text x="4" y="88">Collected from open</text>
<text x="4" y="104">services, or your files.</text>
<text x="4" y="128">You: who is who.</text>
<text x="160" y="88">The phrases of the field,</text>
<text x="160" y="104">found in the texts.</text>
<text x="160" y="128">You (or an AI):</text>
<text x="160" y="144">keep, set aside, merge.</text>
<text x="316" y="88">Keywords grouped into</text>
<text x="316" y="104">a tree of themes.</text>
<text x="316" y="128">You: name, move,</text>
<text x="316" y="144">merge, split.</text>
<text x="472" y="88">Everyone placed near</text>
<text x="472" y="104">what they write about.</text>
<text x="472" y="128">You: explore, compare,</text>
<text x="472" y="144">save views.</text>
<text x="628" y="88">An offline site,</text>
<text x="628" y="104">figures and tables.</text>
<text x="628" y="128">You: names or</text>
<text x="628" y="144">pseudonyms.</text>
</g>
</svg>
</figure>

Each step is a stage of the **build**, which reruns only what your changes
affect. {doc}`introduction` explains the words the app uses; {doc}`about`
gives the method in one page, and {doc}`references` the works and software it
is built on.

## Where to start

**Start here.** {doc}`introduction` (ten minutes of reading), then
{doc}`install`, then {doc}`first-map`: a map of an invented field, built in a
few minutes, to try every screen without risk.

**Tutorials**, step by step in the app, each with what you need and what you
should see:

- {doc}`first-map`: from the start screen to a map, on the demo project;
- {doc}`tutorial-institution`: take the people of a laboratory or an
  institution, collect their texts, build;
- {doc}`tutorial-names`: import a list of people, check who is who, merge the
  duplicates, collect;
- {doc}`tutorial-keywords`: clean the keywords with an AI assistant, without
  sending any text;
- {doc}`tutorial-themes`: rename, move, merge and split the themes, add a
  level, apply;
- {doc}`tutorial-map`: find people, follow links, compare, export distances,
  save a view;
- {doc}`tutorial-share`: build a website of the map, with names or
  pseudonyms.

**Guides** explain one subject in depth: {doc}`collection`, {doc}`keywords`,
{doc}`build`, {doc}`privacy`, {doc}`large-projects`.

**Reference**: {doc}`references`, {doc}`cite`, {doc}`format/index`, {doc}`sizes`, {doc}`hosting`.

**For developers**: the command line, the programming interface, the engine
and the checks, in the last part of the menu ({doc}`dev/command-line` first).

```{toctree}
:caption: Start here
:maxdepth: 1
:hidden:

introduction
install
about
references
cite
```

```{toctree}
:caption: Tutorials
:maxdepth: 1
:hidden:

first-map
tutorial-institution
tutorial-names
tutorial-keywords
tutorial-themes
tutorial-map
tutorial-share
```

```{toctree}
:caption: Guides
:maxdepth: 1
:hidden:

collection
keywords
build
privacy
large-projects
```

```{toctree}
:caption: Reference
:maxdepth: 2
:hidden:

format/index
sizes
hosting
```

```{toctree}
:caption: For developers
:maxdepth: 1
:hidden:

dev/command-line
demo
dev/api
dev/extensions
dev/app-manifest
dev/engine
dev/extraction
dev/lexicon-lab
dev/build
dev/params-tiers
dev/themes
dev/themes-engine
dev/checks
dev/reference
dev/placement
dev/layouts
dev/ui
dev/atlas
dev/site
dev/themes-editor
dev/copilot
dev/usability-g3
dev/collection
```
