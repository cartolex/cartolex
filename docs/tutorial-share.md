# Share a site

**Goal**: build a website of your map that anyone can open in a browser,
without a server, an internet connection or cartolex, and send it or put it
online; take away the map as an image and the theme table.

**You need**: a built project, its themes named ({doc}`tutorial-themes`),
and a decision: will the people on the map be named, or shown under
pseudonyms?

**It takes**: a minute for a small project.

## Steps

### 1. Open Share and answer the questions

Open **Share** in the navigation. The box **Build the offline site** asks:

- **Title**: what the site is called (the project's field by default);
- **Language the site opens in**: English, French or Portuguese;
- **People's names**: *Pseudonyms* (« Person 12 »: no name is in the site;
  organisations stay named) or *Names* (anyone who receives the site sees who
  is where). It is asked at each build, never remembered for you;
- **Names of the placed people**: the projected people (newcomers,
  applicants) may be a sensitive set: pseudonyms unless you choose otherwise;
- **Texts**: none (keywords and places only), the titles, or the titles and
  abstracts, each with the size it adds to the site;
- **Map layouts**, when more than one map version is built (**Map versions**,
  {doc}`tutorial-map`): the layouts the site carries, flat or in three
  dimensions, every one by default, the pinned one first. The site opens on
  the first one ticked; a reader switches with **Layout** at the top of its
  map, and each other layout is read only when it is shown.

```{image} images/share-site.png
:alt: The Share screen: the questions of the offline site (title, language, names or pseudonyms, texts) and the figures, tables and files.
```

**What the site will hold** sums it up, and what it never holds: a full text,
an identifier, the extra columns of your lists.

### 2. Read the checks

**Checks before publishing** lists what *blocks* the build (no map yet),
what is *to answer* (names or pseudonyms), what is *to look at* (people shown
by name, themes whose names look untranslated or technical, a map not up to
date, a site that would be very large) and what is *good to know*. Each has a
button to the screen where it is fixed.

### 3. Build the site

Press **Build the site**. The build runs in the background; when it ends, the
site is listed under **Site builds**, with its size and whether it shows names
or pseudonyms.

```{image} images/share-built.png
:alt: The list of site builds: the newest marked latest, with its size, Open and Download as a zip.
```

- **Open** shows it in your browser, as a reader will see it: the map, the
  themes, a page per person and per organisation, the method.
- **Download as a zip** gives the file to send. The reader unzips it and
  opens `index.html`.

Every build is kept in its own dated folder (in the project's `outputs/`);
nothing is ever written over an earlier one. A build made before your latest
changes is marked *Stale*.

### 4. Figures, tables and files

Below, **Figures, tables and files** gives:

- **The map as an image**, PNG or SVG, at the size and in the colours you
  choose (people are never named on a figure);
- the **Theme table (CSV)**;
- **Write the map bundle**, which lets another project place itself on this
  map, and **Write the project as one file**;
- **Distances…**, the export described in {doc}`tutorial-map`.

## Putting it online

The site is a folder of plain files: any web hosting serves it (a university
web space, a static site service). With names, publish it only where the
people on the map would agree to be shown; {doc}`privacy` says what a project
holds about people.

## If something is not right

- **Build the site stays greyed**: answer the questions marked *to answer*.
- **The site is large**: the abstracts weigh most; choose the titles only, or
  no texts.
- **Theme names look untranslated**: rename the themes in each language in
  the theme editor ({doc}`tutorial-themes`).
