# Start from a list of names

**Goal**: import a list of people (a team, a network, the members of a
committee), make sure each one is the right person in the bibliographic
services, merge the people listed twice, and collect their texts.

**You need**: cartolex installed, an internet connection, and a list of names:
a spreadsheet saved as CSV, or names you can paste, one per line. Optional
columns help: an ORCID iD or an OpenAlex id (a person with one is found
directly), a lab or an institution (it becomes their organisation and ranks
the candidates), any other column (career stage, team, country) becomes a
filter of the map.

**It takes**: a few minutes, plus about a minute per twenty people to check
identities.

A list may look like this (comma, semicolon or tab separated, with or without
a header):

```text
Name,Lab,Career stage
"Durlelmais, Luca",BEG-MAR,phd
"Stordomaro, Ada",BEG-MAR,senior
"Vaifiendec, Xavi",MGT-BRE,senior
```

## Steps

### 1. Create the project

On the **Projects** screen, press **Create a project**, keep **A list of
people** under **Start from**, fill in the **Name**, **the field's title**,
the **Description**, the **Languages of the texts** and the **Folder**, then
press **Create the project**. The **People** screen opens.

### 2. Import the list

Press **Import**, then **A list of people**. Choose the **File**, or paste the
names in **Or paste the list** (« Last, First » or « First Last », one per
line), and press **Read the list**.

```{image} images/names-import.png
:alt: The Import dialog: each column of the list with its first values and how it is read (full name, organisation, filter).
```

cartolex proposes how to read each column, under **Read as**: a full name,
last and first names, an ORCID iD, an OpenAlex author id, an idHAL, a role,
an organisation (one per level of the project), **Keep as a filter**, or
**Ignore**. Change any that is wrong. E-mail addresses are never stored: a
column of them is refused. Choose the **Role of the people added** (*Mapped*
by default), then press **Import N rows**.

**You should see** « *N* people added », and the possible duplicates the
import found.

### 3. Merge the people listed twice

The same person often appears twice in a list (« Ada Stordomaro » and
« A. Stordomaro »). The **Duplicates** tab lists the pairs that may be one
person, each with its likelihood. Choose a pair: the panel puts the two side
by side (names, ORCID, records, organisations, texts, co-authors) and says why
they were proposed, *for* and *against*.

```{image} images/names-duplicates.png
:alt: The Duplicates tab: a pair of people compared side by side, with the reasons for and against, and the buttons to decide.
```

Decide with the buttons or the keyboard: **1** or **2** one person (keeping
the left or the right row), **D** two people, **L** later. A merge keeps the
texts and records of both rows, and can be undone from the person's sheet
(**Unmerge**). **Merge the clear pairs** merges, in one step you can undo,
the pairs that share an ORCID or a record.

Pairs come back here after each import or collection, with what the texts
then tell (a shared ORCID, texts at the same place).

### 4. Find their identities

Press **Collect**, then **Find identities**. For each person whose identity
waits, cartolex looks for candidate records in OpenAlex (with the ORCID
registry as evidence), and in HAL when **Search HAL too** is ticked. Press
**What leaves the computer**: the names, and the institutions your list states,
go to OpenAlex. Tick « I have read what leaves the computer », press
**Start**.

### 5. Check who is who

Open the **Identities** tab. The list holds everyone waiting for a check;
choose a person to see their candidates on the right, each with its
identifier, its works and years, its score and the evidence behind it (the
same name, the same ORCID, the stated institution).

```{image} images/names-identities.png
:alt: The Identities tab: the people waiting for a check on the left, the candidate records of one person on the right.
```

The keyboard does it all: **↑ ↓** a person, **1–9** a candidate, **N** none
of these, **Enter** confirms; each decision is saved at once and the next
person comes. A person can have several records: confirm each. If you know
the record, paste it under « Or paste a record » (`orcid:…`, `openalex:A…`,
`hal:…`) and press **Use this record**. **Accept the clear matches** confirms
in one go the people with a single strong candidate (shown under **Single
clear matches**).

Namesakes are common: when two candidates look alike, compare their
institutions, years and topics, and choose **None of these** rather than a
guess. A record found by the name only (a HAL author form without an
identifier) can be seen, not confirmed.

**You should see** « Nobody waits for a check ».

### 6. Harvest and build

Press **Collect**, then **Harvest texts**, read what leaves the computer,
tick, **Start**. When it ends, the **Coverage** tab tells who has enough
texts; **Retry the N that failed** asks again for the people whose collection
failed. Then **Go to the build**, as in {doc}`first-map`.

On the map, your list's extra columns are **Filters**: show only the senior
researchers, or one team.

## If something is not right

- **A column is read wrongly**: **Back** in the Import dialog, or import again
  with the right reading: a person already in the project keeps their
  decisions.
- **Nobody found for someone**: their sheet (Enter on their row in the People
  tab) gives the first blocking cause. Add their ORCID in a new import, paste
  a record on the Identities tab, or **Add documents** (their PDFs).
- **A wrong record was confirmed**: on the person's sheet, **Change
  identity…** opens the choice again: pick another candidate (1–9, Enter),
  paste their record or ORCID, or **None of these** (N). On the command line,
  `cartolex collect confirm FOLDER <person> <record>` does the same
  ({doc}`dev/command-line`). Harvest again: the new harvest replaces what the
  wrong record brought.
- **Two different ORCIDs on a pair**: cartolex refuses to merge them unless
  you insist (**Merge anyway**): two iDs are usually two people.

More: {doc}`collection` and {doc}`privacy`.
