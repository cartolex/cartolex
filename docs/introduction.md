# What cartolex does, in its own words

cartolex turns the publications of a group of people into a map of their
field. This page is the vocabulary you need before your first project: each
word as the app uses it, what it means, and the screen where you meet it. Ten
minutes of reading; the {doc}`tutorials <first-map>` then put each word to
work.

## A project

A **project** is one map and everything it is made from, kept in a folder of
your computer: the people, their texts, your decisions, the results. You
create, open and switch projects from the **Projects** screen (the project's
name at the top left of every screen, then « All projects… »). The **demo
project** is an invented community of about forty people in coastal and
marine sciences, with texts in English and French: try anything on it, nothing
can break.

When you create a project you give it a **name**, **the field's title** (what
it maps, in a few words, as a specialist would say it), a short
**description** (read by the AI, if you use one, to judge what belongs to the
field), the **languages of the texts** and a **reference language**: keywords
of every language are gathered under their name in that language.

The **Overview** is the project's home: its **Next step** always names the one
thing to do now, and the checklist « Your first map » shows the whole way
(see {doc}`first-map`).

## People

The map is made of **people** and of what they write. The **People** screen
lists them, with three things for each.

- **Role**: what the person is to the map.
  - *Mapped*: their texts draw the map, and they are on it. Most people are.
  - *Context*: their texts help find the keywords of the field, but they are
    not on the map (the co-authors of your mapped people, for instance).
  - *Projected*: placed on the finished map by their texts, without moving
    it (newcomers, applicants, another team you want to compare).
  - *Excluded*: left out; *Undecided*: not chosen yet.
- **Identity**: whether cartolex knows who the person is in the bibliographic
  services (OpenAlex, the ORCID registry, HAL). *To check*: candidate records
  wait for you on the **Identities** tab; *Confirmed*; *Accepted
  automatically* (a single clear match, still marked for review); *No record*.
  Several people with the same name are common: nothing is collected for a
  person until their record is confirmed.
- **Coverage**: whether enough texts were found. *Good* (three texts with an
  abstract or more), *Thin*, *Failed* (a service failed: retry it) or *No
  data*. The **Coverage** tab counts them and says why.

People come in by **Import** (a list of names, a folder of documents, a
prepared corpus) or by **Collect** (the people of an institution, the
co-authors of your people). Two rows that may be one person are proposed on
the **Duplicates** tab; a merge can always be undone. A person's **sheet**
(Enter on a row, or « Open the sheet ») says what was found for them and why.

## Texts

A **text** is a publication: its title, its abstract when there is one, and
sometimes its full text, in any of the project's languages. The **Texts** tab
lists them. The **harvest** (Collect › Harvest texts) gathers the works of the
people whose identity is confirmed; the same work found twice (the same DOI,
or the same title, author and year) counts once. Documents you import (PDF or
text files) are texts too.

**Collecting** is the only step that sends something over the network, and
only to open bibliographic services. Before it starts, a notice says what
leaves the computer (names, identifiers), to which service, and what never
does; it starts only when you have read it ({doc}`privacy`).

## Organisations

**Organisations** are where people work: a team, a laboratory, a department,
an institution. They come with the people's affiliations (each with its
years) or from a column of your list. They sit on **levels** (a lab inside an
institution), can have several parents (a joint unit), and can be renamed or
merged on the **Organisations** tab. On the map, an organisation sits at the
average place of its mapped people.

## Keywords

cartolex finds the keywords in the texts themselves; nothing comes from an
outside list of terms.

- A **candidate** is a phrase built like a technical term (`sediment
  transport`, `trait de côte`), used by at least three people and by at most
  60 % of them.
- Each candidate falls in a **band**, with the reason shown beside it:
  *Kept* (a phrase of several words), *To check* (a single word, often too
  general), *Set aside* (a fragment of a longer phrase, a grammatical word, a
  word everyone uses), or *Rejected automatically* (a phrase that is never a
  keyword in any field).
- Your **decisions**: *Keep*, *Exclude*, *Merge* (two spellings, or a term and
  its translation, become one keyword). Every decision can be undone from the
  **History**.
- The **AI clean-up** (optional) judges the candidates and gives each
  accepted one a **category** (a concept, a method, an object of study, a
  place, a field) and its name in the reference language, so that a term and
  its translation become one keyword.
- The **lexicon** is what the last build kept: the keywords the themes and the
  map use, on the **Final lexicon** tab of the Lexicon screen, with a word cloud.

{doc}`keywords` explains each rule in plain words.

## Themes

The keywords are grouped into **themes**: keywords that the same people use
together end up in the same theme. The themes form a **tree** of one to four
**levels** (themes, then topics inside them, for instance). The grouping only
proposes a tree: on the **Themes** screen you rename, move, merge, split,
create and set aside, with undo and redo, then **save** (a new version, which
you can restore later) and **apply** (the map is drawn again with your
themes). A theme's **share** is the part of all keyword use that counts
toward it.

## The map

The **Map** screen places on one picture:

- the **people**: two people are close when they use the same keywords in the
  same proportions. Each point takes the colour of the theme that weighs most
  in their texts;
- the **keywords**, near the people who use them;
- the **organisations**, at the average place of their mapped people;
- the **texts**, at the average place of the keywords found in them;
- the **projected people**, and the **time windows** (a person's texts over a
  few years, joined by a line in time order).

The treemap beside it shows the themes and their shares; the panel shows what
is selected. The distances on the map are a picture: the **similarity**
shown in the panel (from 0, nothing in common, to 1, the same use of the
vocabulary) is measured in the space the themes are drawn from, and is the
number to quote. A **map version** records how the map was drawn: the pinned
one is the one every build redraws, so the map people know does not move.

## The build

Every result is computed by the **build**, in **stages**: gather the texts,
find keyword candidates, AI clean-up, build the vocabulary, place keywords in
a common space, group keywords into topics and themes, apply your themes,
draw the map, change over time, place projected people. Each stage is *Never
built*, *Up to date*, *Needs update* (something it read changed: the line says
what), *Running*, *Failed* (its previous results are kept) or *Skipped*. A
build runs only what needs it.

**Build…** on the Overview opens the **Build** screen: before anything runs,
it says what will run and why, the time and memory it should take, and asks
how the AI steps are done. A build runs in the background: the **Activity**
button at the top right follows it and every collection, with a Stop button.
Each screen has a **Tune** panel (« Tune the keywords », « Tune the map »…)
with the settings of its step; the **Recipe** tab of the Build screen lists
them all, with what differs from the defaults. {doc}`build` says more.

## The AI steps

Two steps can use an AI, and both work without one. For each, the Build
screen asks how, and the project remembers:

- **No AI**: the keywords are kept by their statistics, the themes by the
  grouping; you review them by hand.
- **With your copilot**: cartolex gives you a **bundle** (one zip file) for an
  AI assistant that can run code. It holds the candidate keywords with
  counts, and people as numbers in a random order: never a text, a name, an
  identifier or an organisation. The assistant judges with cartolex's own
  tools and gives back a `result.json`; you import it and accept what you
  want. No key, and cartolex itself sends nothing ({doc}`tutorial-keywords`).
- **By API** (keyword clean-up only): cartolex sends the keyword strings, with
  the field's title and description, to the AI provider set in the settings,
  with your key. It asks first, and the provider bills the calls; an answer
  already paid for is never asked again.

## Sharing

The **Share** screen builds an **offline site**: a folder that opens in any
browser, without a server or an internet connection, with the map, the
themes, a page per person and per organisation, and the method. At each build
you choose **names or pseudonyms** for the people, and whether it carries the
titles or the abstracts of the texts; checks list what to look at before
sending it. The same screen gives the map as an image, the theme table and
other files ({doc}`tutorial-share`).

## Where things are kept

Everything stays in the project's folder on your computer: what was collected
(`sources/`), what you decided (`decisions/`, with every earlier version),
what was computed (`derived/`), and what was shared (`outputs/`). Nothing is
kept elsewhere except the app's own settings and keys, on this computer
({doc}`format/index` for the details).

## Next

- {doc}`install`, if cartolex is not installed yet;
- {doc}`first-map`, the first tutorial;
- {doc}`about`: the method step by step, with its scientific references.
