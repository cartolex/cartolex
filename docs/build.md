# Building a project

A project's results (the keywords, the themes, the map, the changes over time,
the people placed on the map) are computed by a **build**, in stages. The build
knows what each stage read the last time it ran, so it runs only what needs to
run, and says why. A stage's new results replace the old ones only once they
are complete: stopping a build, or a crash, never leaves a half-written result.

## What needs to be built

```bash
cartolex status my-project
```

prints one line per stage, in one of six states:

| state | meaning |
| --- | --- |
| never built | the stage has no results yet |
| up to date | nothing it read has changed since it ran |
| needs update | something it read has changed; the line says what (a decision file, a parameter, an earlier stage, a new version of cartolex's way of doing it) |
| running | a build is running it now |
| failed | its last run failed or was stopped; its previous results are still there and usable |
| skipped | it does not apply (the AI clean-up switched off, no projected set) |

For example, after an edit of the excluded keywords:

```text
keywords.build (build the vocabulary): needs update; decisions/keywords.csv changed
themes.space (place keywords in a common space): needs update; keywords.build needs an update
```

## Before building: the dry run

```bash
cartolex build my-project --dry-run
```

says which stages will run, which are kept and why, with an estimate of the
time and memory of each. A stage that would need more memory than the machine
has free is marked « cannot run », with the numbers; so is a stage whose
parameters cannot work for this project (as many topic groups as keywords), with
the reason. Nothing is changed.

## Building

```bash
cartolex build my-project
cartolex build my-project --only themes.group     # this stage and what it needs
cartolex build my-project --force map.layout      # run it even if up to date
```

The build prints « phase k of n » with the stage's name and how far it is, at
least every ten seconds. It ends with a summary: what was built, what was
refused and why.

**Stages that reach the network or cost money** ask first. Today that is the
AI clean-up (`keywords.triage`), which sends keyword strings (never texts or
people), with the project's field title and description, to the AI provider
named in `project.json`, and is billed by that provider. Answers already paid
for are kept in `cache/ai/` and reused. The build asks before anything runs;
`--yes` answers yes. The AI key is read from `MISTRAL_API_KEY`. Without an
answer, the stage and everything that depends on it are left out, and the rest
is built.

**Stopping.** Press Ctrl-C once: the build stops at the next safe point, and
says either « nothing changed » or « finished before the cancel » with the
stages that did. The stage that was running keeps its previous results. A
second Ctrl-C stops at once; the next time the project is opened, anything left
half done is completed or undone.

## Parameters and map versions

```bash
cartolex params my-project
cartolex params my-project --set themes.group.top_groups=12
```

lists each stage's parameters, their values and where each value comes from:
a default, a rule computed from the project's size (the depth of the theme
tree, for instance), or `decisions/params.json`. `--set` writes
`decisions/params.json`, after checking the value; a value the stage cannot
take is refused with the reason.

```bash
cartolex versions my-project
cartolex versions my-project --try-another --seed 7
cartolex versions my-project --pin v2
```

The map is drawn with its **pinned version**. The first build adds and pins
`v1`; a rebuild keeps drawing the pinned version, so the map people know does
not move. Trying another layout adds a version beside it; it is used once
pinned.
