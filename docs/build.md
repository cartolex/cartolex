# Building a project

A project's results (the keywords, the themes, the map, the changes over time,
the people placed on the map) are computed by a **build**, in stages. The
build knows what each stage read the last time it ran, so it runs only what
needs to run, and says why. A stage's new results replace the old ones only
once they are complete: stopping a build, or a crash, never leaves a
half-written result.

## The stages and their states

The Overview's box **The build** lists the stages: gather the texts, find
keyword candidates, AI clean-up, build the vocabulary, place keywords in a
common space, group keywords into topics and themes, apply your themes, draw
the map, change over time, place projected people. Each is in one of six
states:

| state | meaning |
| --- | --- |
| Never built | the stage has no results yet |
| Up to date | nothing it read has changed since it ran |
| Needs update | something it read has changed; the line says what (your decisions, a setting, an earlier stage, a new version of cartolex's way of doing it) |
| Running | a build is running it now |
| Failed | its last run failed or was stopped; its previous results are still there and usable |
| Skipped | it does not apply (the AI clean-up switched off, no projected people) |

For example, after you exclude keywords, *build the vocabulary* needs an
update (your keyword decisions changed), and so does every stage after it.

## Before building: the Build screen

**Build…** on the Overview opens the **Build** screen. Nothing runs yet: it
shows what the build will run, which stages are kept and why, the time and the
memory it should take, and what this computer has. A stage that would need
more memory than the computer has free is marked *Cannot run*, with the
numbers; so is a stage whose settings cannot work for this project (as many
groups of themes as keywords), with the reason. A screen whose results need
an update (the Map, for instance) offers to build only what it needs.

**AI help** asks, for each AI step, whether the build uses AI, and how:
**No AI**, **With your copilot** (the build stops at this step and waits for
the assistant's result, see {doc}`tutorial-keywords`) or **By API** (the
keyword clean-up only; needs a provider and a key in Settings › AI). The
choice is kept in the project. Accepting a copilot's keyword triage on the
Lexicon screen sets the keyword clean-up to **With your copilot** when it was
**No AI**.

## Building

Press **Build N stages**. The build runs in the background: the screen and the
**Activity** button at the top right show the stage running, how far it is and
the time left. You can keep working on other screens meanwhile. When it ends,
a summary says what was built, what was refused and why.

**Stages that reach the network or cost money** ask first. Today that is the
AI clean-up by API, which sends keyword strings (never texts or people), with
the project's field title and description, to the AI provider set in the
settings, and is billed by that provider. Answers already paid for are kept
in the project and reused. Without your permission, the stage and everything
that depends on it are left out, and the rest is built.

**Stopping.** **Stop** (on the Build screen or in the Activity) ends the build
at the next safe point: the stages already finished are kept, the one running
is dropped whole and keeps its previous results, and reads *cancelled*, not
*failed*. If the app itself stops during a build, the next time the project is
opened, anything left half done is completed or undone.

**A failure.** The Build screen says which stage failed and why, with what to
do; **Copy a diagnostic** gives what a report needs (versions, the error,
nothing of your data).

## Settings and map versions

Each step's settings are on the screen it shapes, in a **Tune** panel, folded
until you open it: **Tune the texts** (People › Texts), **Tune the keywords**,
**Tune the space and the grouping** (Themes), **Tune the map** (a button above
the map that opens the panel beside it, so the map and a layout's preview stay in
view; drag its edge to widen it). Its header says
« defaults » or « N changed »; each setting says its value and where it comes
from (a default, a rule computed from the project's size, or your choice),
and a value the stage cannot take is refused with the reason. After a change,
the panel offers **Rebuild from « *stage* »**.

The **Recipe** tab of the Build screen lists every setting of every step, with
its value, its origin and whether it differs from its default, links each to
its panel, and downloads them as Markdown or CSV: what a methods section
needs.

The map is drawn with its **pinned version**. The first build adds and pins
`v1`; a rebuild keeps drawing the pinned version, so the map people know does
not move. **Map versions** on the Map screen tries another layout (another
seed, UMAP, t-SNE or the theme tree) as a new version beside it; it is used
once pinned, and pinning the earlier one brings the earlier map back.

## On the command line

Everything above can be done without the app, for scripts and servers; the
results are the same.

```bash
cartolex status my-project                          # one line per stage, its state and why
cartolex build my-project --dry-run                 # what would run, time and memory, nothing changed
cartolex build my-project                           # build what needs it
cartolex build my-project --only themes.group       # this stage and what it needs
cartolex build my-project --force map.layout        # run it even if up to date
```

For example, after an edit of the excluded keywords, `cartolex status` prints:

```text
keywords.build (build the vocabulary): needs update; decisions/keywords.csv changed
themes.space (place keywords in a common space): needs update; keywords.build needs an update
```

The build prints « phase k of n » with the stage's name and how far it is, at
least every ten seconds, and ends with a summary. A stage that reaches the
network or costs money asks first; `--yes` answers yes. The AI key is read
from `MISTRAL_API_KEY`. Ctrl-C once stops at the next safe point and says
either « nothing changed » or « finished before the cancel » with the stages
that did; a second Ctrl-C stops at once.

```bash
cartolex params my-project
cartolex params my-project --set themes.group.top_groups=12
```

lists each stage's parameters, their values and where each value comes from:
a default, a rule computed from the project's size (the depth of the theme
tree, for instance), or `decisions/params.json`. `--set` writes
`decisions/params.json`, after checking the value; a value the stage cannot
take is refused with the reason. `--set themes.group.depth=3`, for example,
groups the keywords into three levels of themes (1 to 4), whatever the rule
says. When the vocabulary is too small for the depth asked (after a clean-up
that removed many keywords, for instance), the grouping takes the deepest
number of levels that grows from the top, and the build's result and the
Themes screen say so; the setting itself stays as you set it.

```bash
cartolex versions my-project
cartolex versions my-project --try-another --seed 7
cartolex versions my-project --pin v2
```

lists, adds and pins map versions. See {doc}`dev/command-line` for every
command, and {doc}`dev/build` for how the stages are declared.
