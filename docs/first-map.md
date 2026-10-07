# Your first map, with the demo project

**Goal**: build a complete map in a few minutes, on an invented field, and
learn the way every project follows: people and texts, a build, the keywords,
the themes, the map, sharing.

**You need**: cartolex installed ({doc}`install`) and open in your browser.
Nothing else: the demo project brings its own people and texts, and nothing
leaves your computer.

**It takes**: five minutes, one of which is the build.

The second half of this page, {ref}`the-checklist`, follows the overview's
checklist « Your first map » for your own projects: the overview's link
« Your first map, step by step » leads there.

## Steps

### 1. Create the demo project

When cartolex opens for the first time, it shows the **Projects** screen.
(Later, open it from the project's name at the top left, then « All
projects… ».) In the box **The demo project**, press **Create the demo
project**.

```{image} images/first-map-start.png
:alt: The Projects screen, with the boxes New project, The demo project and Recent projects.
```

The demo is an invented community of about forty people in coastal and marine
sciences, with texts in English and French (see {doc}`demo` for how it is
made). Its folder goes in `cartolex-projects/demo` in your home folder, as
the box says.

**You should see** the **Overview** of the project « Demo: coastal and marine
systems », marked *Never built*.

### 2. Read the overview

The Overview is the home of every project:

- **Next step** names the one thing to do now, with a button;
- **Your first map** is the checklist of the whole way, the current step in
  black;
- **The build** lists the ten stages that turn texts into a map, each with
  its state;
- **Health**, **The map** and **Shared builds** fill in as you go.

```{image} images/first-map-overview.png
:alt: The overview of the demo project before its first build: the next step, the checklist « Your first map » and the stages of the build.
```

In the demo, the next step says that one mapped person has no texts yet: the
demo has, on purpose, a person without any publication. Its texts are
otherwise all there, so **do not collect** here (the demo people are
invented, and a collection would look for them in the real bibliographic
services): go straight to the build.

### 3. Build

Press **Build…** in the box **The build**. The **Build** screen opens, and
nothing runs yet. It shows:

- **Before the build**: how many stages will run, the time and the memory
  they should take, and what this computer has;
- each stage, with *Will run* and the reason (*never built* the first time);
- **AI help**: how the keyword clean-up and the theme curation are done. Keep
  **No AI** for now; {doc}`tutorial-keywords` does it with an AI assistant.

```{image} images/first-map-build.png
:alt: The Build screen before the first build: the time, the memory, the stages that will run.
```

At the bottom, a note repeats that one mapped person has no texts: build
without them. Press **Build 9 stages**.

**You should see** the stages turn to *Done* one after the other, with a
progress bar and the time left. The **Activity** button at the top right
shows the build too: you can leave this screen and keep working, and stop the
build there. A first build of the demo takes about a minute.

### 4. Look at the map

When the build ends, open **Map** in the navigation.

```{image} images/first-map-map.png
:alt: The map of the demo project: a treemap of the themes on the left, the people and keywords in the middle, the panel on the right.
```

- Each **dot** is a person, in the colour of the theme that weighs most in
  their texts; each **diamond** is a keyword. Two people are close when they
  use the same keywords.
- On the left, the **treemap** shows the themes, each as large as its share
  of all keyword use.
- Click a dot, a theme or a legend entry: the **panel** on the right says what
  it is. **Find on the map** finds a person or a theme by its name.
- Drag to move, use the wheel or + and − to zoom, 0 to fit.

The themes have plain names taken from one of their keywords, and some are
French while others are English: without AI help, the keywords of each
language tend to form themes of their own. That is what the next tutorials
fix.

### 5. Go on

The overview's next step now says **Review the keywords**. From here:

- {doc}`tutorial-keywords`: clean the keywords with an AI assistant, so that
  English and French terms meet;
- {doc}`tutorial-themes`: name and shape the themes;
- {doc}`tutorial-map`: read the map, compare people, export distances;
- {doc}`tutorial-share`: make a website of the map.

## If something is not right

- **The Build screen says « cannot run » for a stage**: the line says why (a
  stage that needs more memory than the computer has free, for instance).
- **The build stops on a language model**: run the command it shows, or see
  {doc}`install` (the installer kit installs them).
- **A stage failed**: the Build screen says which, keeps every earlier result,
  and offers **Copy a diagnostic** for a report. The overview names it until
  another job runs.
- **You stopped the build**: the stages that finished are kept; the others
  read *cancelled*, not *failed*. Build again to finish.
- **The demo project already exists** (a second try): open it from **Recent
  projects** on the Projects screen, or give another folder.

(the-checklist)=
## The checklist « Your first map »

For your own projects, the Overview's checklist follows the way from an empty
project to a map you can share. Each step says where it is done in the app
and how you know it is done. The checklist can be hidden with « Hide this
list », project by project, and shown again with « Show « Your first map » »
at the top of the overview.

1. **Project.** On the **Projects** screen, **Create a project**: a name, the
   field's title, a short description of the field, the languages of the
   texts and a folder; then say where it starts from (a list of people,
   institutions, collaborators, a folder of texts, a corpus). *Done when a
   project is open.*
2. **People.** On the **People** screen, the **Import** menu adds a list of
   names (a CSV file or a pasted list, one person per line; its extra columns
   become filters, an organisation column gives their organisations), a
   folder of documents, or a corpus. With collection available, the
   **Collect** menu also takes the people of institutions or the
   collaborators of your people. Each person has a **role**: *mapped*,
   *context* or *projected* ({doc}`introduction`). *Done when the project has
   people.* Tutorials: {doc}`tutorial-names`, {doc}`tutorial-institution`.
3. **Identities.** **Collect › Find identities** looks for each person's
   records in the bibliographic services; the **Identities** tab lists the
   people whose record waits for a check, with each candidate and why it was
   found. ↑ ↓ choose a person, 1–9 a candidate, N none of these, Enter
   confirms; a person's sheet changes an identity already decided
   (**Change identity…**). *Done when no mapped person without texts waits for a check.*
4. **Texts.** **Collect › Harvest texts** gathers the works of the people
   whose identity is confirmed. It runs in the background (the **Activity**
   button follows it); its result offers to go to the build. The **Texts** tab
   lists what was collected, **Coverage** who has enough. People imported with
   their documents have their texts already. *Done when mapped people have
   texts and none waits for a collection.*
5. **Build.** **Build…** on the overview opens the Build screen (step 3
   above), where the AI help is chosen. A build that cannot start says why and
   what to do, with a button: no mapped person has texts yet (collect them),
   or nobody is mapped (choose people on the People screen). *Done when the
   vocabulary is built.*
6. **Keyword review.** The **Lexicon** screen shows the candidates in their
   bands, *kept*, *to check* and *set aside*: keep, exclude or merge them,
   one or many, then build again to apply your decisions ({doc}`keywords`,
   {doc}`tutorial-keywords`). *Done when you have made at least one decision
   since the last extraction.*
7. **Themes.** The **Themes** screen shows the keywords grouped into themes:
   rename, move, merge, split, then save and apply ({doc}`tutorial-themes`).
   *Done when the themes are saved.*
8. **Map.** The **Map** screen draws the people and the organisations on the
   map of the themes ({doc}`tutorial-map`). *Done when the map is drawn.*
9. **Share.** The **Share** screen builds an offline site of the map, to send
   or to publish ({doc}`tutorial-share`). *Done when a site was built.*

**When something is not right.** The overview's **Next step** names a failed
stage only while it is the last thing tried: once another job has run since,
the stage reads « failed on <date> » in the list of stages, and the next step
moves on. Any job running (a collection, an import, a site) is said on the
overview, with a button to the Activity. A message never asks you to edit a
file: each says what to do, with a button to the screen where it is done.
