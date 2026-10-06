# Your first map, step by step

This page follows the checklist « Your first map » of the app's overview,
from an empty project to a map you can share. Each step says where it is done
in the app and how you know it is done. The overview's **Next step** always
names the one thing to do now; the checklist shows the whole way.

The checklist can be hidden with « Hide this list », project by project, and
shown again with « Show « Your first map » » at the top of the overview.

## 1. Project

On the start screen (**Projects**, in the settings menu), create a project: a
folder on this computer, a name, a short description of the field, and the
languages of the texts. « Create the demo project » makes one from an
invented community of coastal and marine sciences, in English and French,
ready to build.

Done when a project is open.

## 2. People

The map is made from the texts of people. On the **People** page, the
**Import** menu adds them:

- **a list of names** (a CSV file or a pasted list, one person per line); its
  extra columns become filters, an organisation column gives their
  organisations;
- **a folder of documents**, matched to people by their names or folders;
- **a corpus**, an index and its files.

With collection available, the **Collect** menu also finds people from
institutions (« People of institutions ») or from collaborators. Each person has a **role**: *mapped* (their
texts make the map), *context* or *projected* (placed on the map without
shaping it). Imported people are mapped by default; change a role in the list.

Done when the project has people.

## 3. Identities

To collect someone's texts, cartolex first needs their record in the
bibliographic services (OpenAlex, HAL, SciELO). **Collect → Find identities**
searches them; the **Identities** tab then lists the people whose record
waits for a check, with each candidate and why it was found. The keyboard does
it all: ↑ ↓ a person, 1–9 a candidate, N none of these, ⏎ confirm. « Accept the
clear matches » confirms at once the people with one strong candidate.

Before anything leaves the computer, the app shows what is sent, to whom, and
asks you to agree (see {doc}`privacy`).

Done when no mapped person without texts waits for a check (once the texts
are gathered by a build, the people still waiting are listed as a note on the
overview instead).

## 4. Texts

**Collect → Harvest texts** reads the works of the people whose identity is
confirmed. It runs as a job: the **Activity** drawer, at the top of every
screen, follows it, and you can keep working meanwhile. When it ends, its
result offers to go to the build. The **Texts** tab lists what was collected,
and **Coverage** shows who has enough texts.

People imported with their documents have their texts already.

Done when mapped people have texts and none waits for a collection.

## 5. Build

From the overview, **Build…** opens the pre-flight sheet: what will run and
why, the time and memory it should take, and the notes to read first (people
whose texts were never collected, an AI result not accepted yet). The build
finds the keyword candidates, builds the vocabulary, groups the keywords into
themes and draws the map.

The **AI clean-up** of the candidates is chosen on the same sheet:

- **No AI**: the candidates are kept by their statistics only;
- **With your copilot**: the build stops after the candidates, you give them to an AI
  assistant (a file to download), import its result and accept it, then
  continue the build. Once accepted, the AI clean-up reads « Done with your
  copilot » with the number of decisions;
- **By API**: the candidates are sent to the AI provider set in the settings,
  with a key saved on this computer; it asks your consent and is billed.

A build that stops on a problem says why and what to do, with a button: for
instance, no mapped person has texts yet (collect them), or nobody is mapped
(choose people on the People page). A build you cancel keeps every result it
had; the stages it did not finish show « cancelled », not « failed ».

Done when the vocabulary is built.

## 6. Keyword review

The **Keywords** page shows the candidates in three bands: *kept*, *to
check* and *set aside*. Keep, set aside or merge keywords (one or many, with
the filters), then build again to apply your decisions. {doc}`keywords` says
how a phrase becomes a keyword.

Done when you have made at least one decision since the last extraction.

## 7. Themes

The **Themes** page shows the keywords grouped into topics and themes. Rename
them, move keywords and topics, merge or split; save, then apply the themes
(a build of the themes and the map).

Done when the themes are saved.

## 8. Map

The **Map** page draws the people and the organisations on the map of the
themes; the period, the filters from your list's columns and « Find on the
map » help read it.

Done when the map is drawn.

## 9. Share

The **Share** page builds an offline site of the map, to send as a zip or to
publish: it asks whether people are named, which texts it carries, and checks
what it would show before building.

Done when a site was built.

## When something is not right

- The overview's **Next step** names a failed stage only while it is the
  last thing tried: once another job has run since, the stage reads « failed
  on <date> » in the list of stages, and the next step moves on.
- Any job running (a collection, an import, a site) is said on the overview
  with a button to the Activity drawer.
- A message never asks you to edit a file: each says what to do, with a
  button to the page where it is done.
