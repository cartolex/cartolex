# Clean the keywords with an AI copilot

**Goal**: let an AI assistant judge the candidate keywords of your project
(keep the terms of the field, exclude generic phrasing, join a term and its
translation), review what it proposes, accept it, and see the cleaner
lexicon and themes that follow.

**You need**: a project built once (the demo project of {doc}`first-map` is
perfect), and an **AI assistant that can run code**: a chat assistant with a
code tool that accepts a zip file, or a coding assistant on your computer.
No key and no account with cartolex: cartolex itself sends nothing, you hand
the file over yourself.

**It takes**: a few minutes of your time; the assistant's work takes from a
few minutes (the demo) to an hour or more (tens of thousands of candidates,
cut into parts).

## Why

Without AI, the candidates are kept by their statistics: generic phrases
(« the present study », « recent decades ») slip in, and a term and its
translation (`sediment transport`, `transport sédimentaire`) stay two
keywords, so the themes split by language. An assistant reads each candidate
with its evidence and decides, the way a specialist would, much faster.

## What leaves your computer

Only the **bundle** you download and give to the assistant. It holds the
candidate keywords with their counts, other spellings and the longer phrases
they sit in, who uses which candidate (people as numbers in a random order),
your earlier decisions and notes, the field's title and description, and
cartolex's own kit. Never a text (unless you ask for a few masked lines around
each candidate), never a name, an identifier, an organisation, or your keys.
The dialog lists both, under **What it contains** and **What it never
contains**.

## Steps

### 1. Open the copilot

On the **Lexicon** screen, press **Triage with AI**, then **With an AI copilot
(runs code, no key)…**.

```{image} images/keywords-list.png
:alt: The Lexicon screen: the bands Kept, To check, Set aside, Rejected automatically and Final lexicon, and the list of candidates with their reason, people, texts and score.
```

(You can also choose, on the Build screen, **AI help › Keyword clean-up › With
your copilot**: the build then stops at this step, the Activity reads « Build
waiting for your copilot », and the overview's next step leads here. Once the
result is accepted, **Continue the build**.)

### 2. Export the bundle

The dialog **AI copilot of the keywords** has three steps: *Export*, *Import
the result*, *Review*.

```{image} images/keywords-copilot.png
:alt: The dialog AI copilot of the keywords, at its first step: which keywords, the parts, the curation notes, what the bundle contains and never contains.
```

- **Which keywords**: *Kept and to check* is the usual choice; *Kept, to check
  and set aside* lets the assistant rescue a keyword a rule set aside; *Not
  judged yet* sends only what no one has judged.
- **Add a few lines of text around each candidate**: helps the assistant judge
  a term in its context; every name, address and identifier in them is
  masked. Leave it off if your texts must not leave the computer at all.
- **Parts**: the dialog estimates the size of the work in tokens. A large
  project is cut into parts, each judged in a conversation of its own.
- **Curation notes for the assistant**: what the assistant should know (the
  teams of the field, terms that belong together or apart, « methods count as
  keywords »). Press **Save the notes**: they go with every later bundle.

Press **Download the bundle**: a zip named after the task, the project and
the time.

### 3. Give it to the assistant

Start a conversation with your assistant, attach the zip, and ask it to
follow the guide inside (« Unpack this bundle and follow its guide »). The
guide tells it everything: it unpacks cartolex's kit, reads the candidates
group by group (a family of terms sharing a word, junk flagged by patterns),
and keeps its progress on disk.

It stops twice to ask you:

1. after its first 200 decisions: does this look right? Any **standing
   rules** (« research discourse is always excluded », « place names are not
   keywords here »)? Your rules are kept in the project for the next bundles;
2. before handing back: its report, what it read and what it did not.

It then gives you a file named `result.json` (one per part).

### 4. Import and review

Back in cartolex, press **I have the result** and choose the `result.json`
(or several: one per part, or a result taken up again; they are merged).

```{image} images/keywords-review.png
:alt: The review step: what the kit counted, the assistant's notes, and the proposed decisions with what the AI says, the category and why.
```

The review shows:

- **The assistant's notes and measures**, and **What the kit counted**: how
  many candidates it decided by group, term by term, without reading them,
  read and left undecided, or never shown. Look here first: a result that
  skipped a band says so under **What this result does not cover**;
- the **Proposed decisions**, each with what **The AI says**, its
  **Category** (a concept, a method, an object of study, a place, a field) and
  **Why**.

Untick what you disagree with (**Choose all** and **Choose none** help), then
press **Accept N decisions**. Nothing changes before; every decision can be
undone afterwards from the **History**. A result of another bundle (another
project's, or an older one) is flagged before you accept.

### 5. Build again

Accepted decisions apply at the next build: press **Build…** on the overview
(or **Continue the build** if the build was waiting) and start it.

Accepting also sets the build's **AI help › Keyword clean-up** to **With your
copilot** when it was **No AI** (never when it is **By API**), and the toast
says so: the build will ask your copilot for the new keywords a later
extraction finds. You can switch it back on the Build screen.

From now on, **only the keywords someone accepted** (you or the assistant)
enter the vocabulary. New candidates nobody judged are counted on the
Lexicon screen, « N candidates not judged »: **Send them to the AI** makes a
bundle of them alone, **Keep them anyway** lets them in.

### 6. See the lexicon

Open the **Final lexicon** tab of the Lexicon screen: the keywords the last build
kept, the whole vocabulary the themes and the map use, with a **Word cloud**.
**Size by** *Score* or *People*, **Colour by** *Theme* or *Category*. The list
below gives each keyword's term in each language, its rank, people, texts,
category, theme and forms; **Download CSV** takes it away.

```{image} images/keywords-lexicon.png
:alt: The Lexicon tab: a word cloud of the most important keywords, coloured by theme.
```

**You should see** fewer, more specific keywords, and themes named by terms
of the field. Open the **Themes** screen next: {doc}`tutorial-themes`.

## By hand, or by API

- **By hand**: on any band of the Lexicon screen, select rows (click,
  Shift-click, or the filters, then « All N shown ») and choose **Keep**,
  **Exclude** or **Merge…**. Merging a French term into its English
  translation makes them one keyword on the map.
- **By API**: **Triage with AI › By API (with a key)…** sends the keyword
  strings, with the field's title and description, to the AI provider set in
  Settings › AI, with your key saved on this computer. The dialog shows what is
  sent and an estimate of the calls; the provider bills them. An answer
  already paid for is never asked again.

## If something is not right

- **The assistant cannot open the zip or run code**: it needs a code tool; a
  plain chat cannot do it.
- **It stopped halfway** (a conversation too long): import what it gave you
  (a partial result says so), then give it the bundle again: it takes up the
  work from its `result` folder. Or cut the bundle into more parts.
- **A decision is wrong**: undo it from **History**, or decide it yourself in
  the list; your decision wins.

More: {doc}`keywords` explains how a phrase becomes a candidate, its band and
its reason.
