# How keywords emerge

```{admonition} Decided
:class: note

This page describes the defaults the lexicon lab recommended
({doc}`dev/lexicon-lab`) and the project's owner approved, quality first:
everything that reaches the lexicon is checked.
```

cartolex builds its keywords from the texts of the people it maps: nothing
comes from an outside list of terms. This page says, in plain words, how a
phrase in a text becomes a keyword of the map.

## 1. Candidates: the phrases people write

Every text is read in its language (English, French or Portuguese) by a
language model that knows which word is a noun, an adjective or a
preposition. A **candidate** is a phrase built like a technical term:

- in English, nouns and adjectives ending on a noun: `sediment transport`,
  `sea surface temperature`, `distributed systems`. A phrase stops at `of`:
  in `the role of silicic acid uptake` the candidate is `silicic acid
  uptake`;
- in French and Portuguese, a noun with its adjectives, and at most one
  complement: `transport sédimentaire`, `trait de côte`, `masse d'eau`,
  `linha de costa`, `nível do mar`.

Shorter phrases inside a longer one count too: a text about `sea surface
temperature` also speaks of `surface temperature` and `temperature`. The
spellings of one phrase are grouped — `tide gauge` and `tide gauges`, `le
trait de côte` and `les traits de côte` are one candidate — and the
candidate is shown the way people most often write it.

A candidate must be used by **at least three people**, and by **at most 60 %
of them**: a phrase only one or two people use cannot place anyone on a
shared map, and a phrase almost everyone uses (`results`, `the present
study`) tells people apart no better than chance.

## 2. The counting unit: who weighs the same

The score of a candidate adds up how much each *unit* uses it. Three presets
choose the unit:

| preset | the unit | what it means |
| --- | --- | --- |
| **People** (default) | a person | each person weighs the same, however much they write |
| Literature | a text | each text weighs the same: prolific people weigh more, and a text two people wrote counts once |
| Organisations | an organisation | each lab or team weighs the same, however many people it has |

In the lab the three presets give the same keywords; they differ a little
in the order of the candidates, which is what you see first. Whatever the
preset, the map still places people.

## 3. The score: specific and used

Within each unit, a candidate weighs more when it is used often and when few
other units use it (the classic TF-IDF weighting). The score of a candidate
is the sum over units. Longer phrases get a bonus: a phrase of two words
scores three times its weight, a phrase of three words five times, because a
precise phrase names more than a single word. The score orders the
candidates; it does not decide their band.

## 4. Three bands, each with its reason

Every candidate falls in one of three bands. The reason is shown next to it,
and any candidate can be moved to another band by hand.

**Kept** — a keyword unless someone says otherwise.

- *multi-word term*: a phrase of two words or more (`coastal erosion`,
  `trait de côte`).

**To check** — a person, or the AI, decides.

- *single word*: one word alone (`erosion`, `plankton`) is often too general
  to be a keyword on its own, but not always.

The kept terms are checked too when the AI clean-up runs: a phrase of
several words is not always a keyword (`study area`, `recent decades`).

**Set aside** — not used, but kept visible and restorable in one click.

- *part of «…»*: the phrase is never seen outside the same longer phrase
  (`vector machine` is only ever part of `support vector machine`), so the
  longer phrase stands for it.

## 5. What the AI sees

The AI clean-up is optional. It judges the **kept** and **to-check**
candidates, never those set aside: a phrase never seen outside a longer one
adds nothing the longer one does not say. When it runs, only the terms it
accepts reach the lexicon.

It sees the candidate phrases, in batches, with the title of the field and
the short description the project's owner wrote — no text, and nothing about
who uses a phrase. It answers, for each candidate, whether it names a
concept, a method or an object of the field, or why it is not a keyword (a
name, an administrative phrase, a word too general, a broken piece of a
phrase), and gives its English form so that the languages meet on the map:
`évolution du trait de côte` and `shoreline evolution` get the same English
form, and become one keyword. A phrase that joins a process or a property to
an object of the field (`sediment transport`, `régime alimentaire des
amphipodes`) is a keyword; a single everyday word (`water`, `growth`) is
not, unless the field uses it as a term of art. Its answers are kept: the
same candidate is never paid for twice.

A second route is being studied: instead of calling a paid service, export
the candidates with their evidence — how many people and texts use each one,
its other spellings, the longer phrases it sits in, its band and reason, a
line or two where it appears — for a person or an AI assistant to judge, and
read the answers back.
