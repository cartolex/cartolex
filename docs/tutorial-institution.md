# Map a laboratory or an institution

**Goal**: start from the name of a laboratory, a department or a whole
institution, take the people who publish there, collect their texts and build
their map.

**You need**: cartolex installed, an internet connection (the people and their
texts come from OpenAlex, an open index of scholarly works), and the name of
the institution, or its ROR or OpenAlex identifier. A free OpenAlex key helps
for large institutions (step 6).

**It takes**: a few minutes for a laboratory; for a large university, the
reading of its works can take hours (see the end of this page).

## Steps

### 1. Create the project

On the **Projects** screen, press **Create a project**. Under **Start from**,
choose **Institutions**. Give the project a **Name**, **the field's title**
(what the map covers, in a few words) and a short **Description** (it is the
context an AI reads, if you use one later), tick the **Languages of the
texts**, and give a **Folder**: a new or empty folder, by its full path. Press
**Create the project**.

**You should see** the **People** screen, on its **Organisations** tab, still
empty, with the box **People of institutions** below the table.

### 2. Find the institution

In **People of institutions**, type the institution's name (or paste its
OpenAlex id, its ROR id or its link) and press **Find**.

The list shows every institution that bears the name, with its type, its
country, its number of works, its ROR id and what it is part of: names are
often shared, so choose those you mean. Tick one or several, then press
**Read their people**.

```{image} images/institution-proposal.png
:alt: The box People of institutions: an institution found by its name, ticked, and the authors proposed below it.
```

### 3. Read their people

A dialog asks for the **First year** and **Last year** of the works to read
(empty: the project's years) and how many **Works there, at least** a person
needs to be proposed (two by default). Press **Continue**.

Before anything is sent, cartolex shows **What leaves the computer**: which
service receives what (here, the institution's identifiers), about how many
requests, and what never leaves. Tick « I have read what leaves the computer »
and press **Start**. (You can choose not to see this notice again for this
kind of collection; Settings › Privacy shows it in full again.)

The reading runs in the background: the **Activity** button at the top right
shows its progress, and you can keep working. cartolex reads the institution,
every unit below it (labs, departments), and every work signed there in the
years.

**You should see** the proposal: « *institution*: *N* authors with 2 works or
more (*M* works read) », and a table of the people proposed, with their
works, their years there, their units, and whether they are in the project
already. A person who left keeps only the years they were there.

### 4. Take the people

Search the list by name, ORCID or record if you want only some of them, tick
them and press **Take the N selected**, or press **Take everyone… (N)**.

```{image} images/institution-take.png
:alt: The dialog Take everyone proposed: the role of the people taken and the level of each type of institution.
```

The dialog asks:

- their **Role**: *Mapped* (on the map, the usual choice), *Context* or
  *Projected* ({doc}`introduction`);
- **The level of each type of institution**: the index's types
  (education, facility…) placed on the project's levels, so that a lab sits
  inside its university on the map.

When two records of the index look like one person (the same ORCID, the same
name, or names that agree at the same unit), the clear pairs are taken as one
person and the others are listed for you to tick. Records you know to be one
person can also be selected in the list and taken with **Take as one person**.
Press **Take all**.

**You should see** the people on the **People** tab, mapped, with their
identity *Confirmed*: they were found through their records, so there is no
identity to check. The **Organisations** tab now lists the institution and
its units, with their people.

### 5. Harvest their texts

Press **Collect**, then **Harvest texts**. The dialog offers the years, **Search
HAL too** (an open archive, by name) and **Fill the missing abstracts from the
text providers**. Press **What leaves the computer**, read the notice (author
identifiers and DOIs go to OpenAlex), tick « I have read what leaves the
computer » and press **Start**.

**You should see**, when the harvest ends, « *N* people, *M* texts received »
in the Activity, with **Go to the build**. The **Texts** tab lists the texts;
**Coverage** says who has enough of them (*Good*: three texts with an
abstract or more).

### 6. Build and look

Press **Go to the build** (or **Build…** on the Overview), choose the AI help
(keep **No AI** the first time) and start the build, as in {doc}`first-map`.
On the **Map**, tick **Organisations** under **Show** and choose the level:
each lab sits at the average place of its people. Click one: the panel lists
its people, and **Open in Organisations** leads back to its sheet.

## If something is not right

- **Too many institutions bear the name**: give its ROR or OpenAlex id
  instead (on ror.org or openalex.org), or tick only the one whose country
  and type fit.
- **A large institution**: its works are read 100 at a time, one request per
  100 works. Above 100,000 works, cartolex stops after the first page and asks
  for your confirmation, with the requests, the time and the days of OpenAlex's
  daily budget it needs. Narrow the years, choose units below the institution,
  save a free OpenAlex key in Settings › Data sources, or read a downloaded
  copy of OpenAlex ({doc}`large-projects`). A reading you stop (or that meets
  a failing service) is *paused*: **Resume** in the Activity goes on from
  where it was.
- **People you do not want**: on the People tab, select them and **Set the
  role** to *Excluded*, or to *Context* to keep their texts for the keywords
  only.
- **Two organisations that are one**: « Organisations that may be one » on the
  Organisations tab lists them; the same ROR or OpenAlex id is merged in one
  step.
- **Someone without texts**: their sheet (Enter on their row) gives the first
  blocking cause, and offers **Retry**, **Add documents** or **Exclude**.

More: {doc}`collection` (every way in, in detail) and {doc}`privacy`.
