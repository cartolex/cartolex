# How keywords emerge

cartolex builds its keywords from the texts of the people it maps: nothing
comes from an outside list of terms. This page says, in plain words, how a
phrase in a text becomes a keyword of the map: what you see on the
**Keywords** screen (the bands and their reasons), and what the AI clean-up
changes ({doc}`tutorial-keywords` does it step by step). The rules were chosen
by measuring them on test corpora ({doc}`dev/lexicon-lab`), quality first:
everything that reaches the lexicon is checked.

## 1. Candidates: the phrases people write

Every text is read in its language (English, French, Portuguese, Spanish,
German or Italian, the project's **languages of the texts**) by a language
model that knows which word is a noun, an adjective or a preposition; a text
in another language is left out. A **candidate** is a phrase built like a
technical term:

- in English, nouns and adjectives ending on a noun: `sediment transport`,
  `sea surface temperature`, `distributed systems`. A phrase stops at `of`:
  in `the role of silicic acid uptake` the candidate is `silicic acid
  uptake`;
- in French, Portuguese, Spanish and Italian, a noun with its adjectives,
  and at most one complement: `transport sédimentaire`, `trait de côte`,
  `masse d'eau`, `linha de costa`, `nível do mar`, `nivel del mar`,
  `lesión por presión`, `qualità dell'acqua`, `presa in carico`;
- in German, adjectives ending on a noun: `künstliche Intelligenz`,
  `psychische Gesundheit`. German writes most terms as one word
  (`Meeresspiegelanstieg`), and two nouns in a row belong to two phrases. A
  phrase stops at a genitive, as English stops at `of`: in `Ziel der Arbeit`
  the candidates are `Ziel` and `Arbeit`. Nouns keep their capital.

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

## 4. Four bands, each with its reason

Every candidate falls in one of four bands. The reason is shown next to it,
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
- *a stop word*: a single word that is a grammatical word of its language
  (`relação`, `fait`), or a grammatical word of another language: when a
  French paragraph lands among the English texts (a mixed paragraph, a title
  in capitals), the English model takes `des`, `la` or `LE` for nouns. Such a
  paragraph is recognised by holding two different French grammatical words
  inside its phrases; there these words cut the phrases they are in.
- *starts or ends with a word of another language*: `LE LITTORAL`,
  `qu'une attention` among the English candidates. A capitalised word
  begins a name and does not count (`La Niña`, `El Niño`).
- *a word used evenly by many people*: a single word used by at least a
  fifth of the people, and by about as many people as it would reach if its
  occurrences were scattered at random over the texts (`study`, `approach`,
  `résultats`, `objetivo`). A word of the field gathers in the texts of the
  people who work on its subject and stays to check.

**Rejected automatically** — never judged again, restorable in one click (*put
back*).

- *rejected by cartolex's list*: a phrase that is never a keyword, in any
  field (`further work`, `et al`), on the list cartolex ships;
- *rejected by your earlier projects*: an AI answered, in another project on
  this computer, that the phrase is never a keyword in any field.

A project's own answers never reject its own candidates, and a candidate you
decided on is never rejected in your project. Putting a candidate back keeps it
and takes it out of this computer's list. The settings (Words) show both lists,
empty this computer's, and switch them off for a project.

**What reaches the lexicon.** Without the AI clean-up, the kept and to-check
candidates reach the lexicon, and those set aside or rejected automatically do
not. With it, only the candidates the AI accepts do (see below): it never sees
those rejected automatically, so they do not reach the lexicon either. In both cases a candidate someone keeps
by hand (a *keep* in `decisions/keywords.csv`) reaches the lexicon whatever
its band, and one someone excludes does not; every candidate, set aside or
not, stays in the candidate tables with its band and its reason.

## 5. What the AI sees

The AI clean-up is optional. It judges every candidate but those rejected
automatically: the **kept**, **to-check** and **set-aside** ones, so that a
keyword a rule set aside can be rescued. Its answers are kept: at each run only
new candidates cost a call. When it runs, only the terms it accepts reach the
lexicon.

It sees the candidate phrases, in batches, with the title of the field and
the short description the project's owner wrote — no text, and nothing about
who uses a phrase. It answers, for each candidate, what it names — a
**concept** (a process, a phenomenon, a property, a theory), a **method** (a
technique, an instrument, a model, a data source), an **object** of study, a
**place** or setting (a kind of environment, not a named place) or a **field**
(a discipline's name) — or why it is not a keyword: **never** a keyword, in any
field (a function word, a broken piece, boilerplate, a generic word of academic
writing: given only when the AI is sure), or not informative **here**, in this
field only (a name, a term too common among the field's researchers). This
category is kept with each decision: the Keywords list shows and filters it,
the map can colour and filter its keywords by it, and a theme's name prefers a
concept or an object when two keywords are used as much. A *never* answer
enters this computer's list of rejections, for your next projects. It gives
each accepted term its English form so that the languages meet on the map:
`évolution du trait de côte` and `shoreline evolution` get the same English
form, and become one keyword. A phrase that joins a process or a property to
an object of the field (`sediment transport`, `régime alimentaire des
amphipodes`) is a keyword; a single everyday word (`water`, `growth`) is
not, unless the field uses it as a term of art. Its answers are kept: the
same candidate is never paid for twice.

**Triage with AI** (the Keywords screen's primary button) offers two routes:
**by API**, the batches above, with a key; or **with an AI copilot**, without a
key and without cartolex sending anything. The copilot is a zip you give to an
assistant that can run code: the candidates with their evidence (how many
people and texts use each one, its other spellings, the longer phrases it sits
in, its band and reason, and, if you ask, a line or two where it appears, names
masked), sorted into groups by cartolex — junk flagged by patterns, families
that share a head word, the rest by who uses them — and cartolex's own kit.
The assistant judges a whole group in a line, and term by term only where a
group is mixed; it keeps its progress on disk, so a large field can be judged
in several conversations (the bundle can be cut into parts). It asks you twice:
after its first 200 decisions (does this look right? any standing rules, such
as « research discourse is always excluded »?) and before handing back. Its
result comes back as `result.json` (one per part): import it, see what the
assistant truly read and what it did not, and accept all of it or some. Your
standing rules are kept in the project, and every later bundle carries them.
