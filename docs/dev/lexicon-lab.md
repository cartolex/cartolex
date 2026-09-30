# The lexicon lab

The lexicon lab (`tools/lexicon_lab/`) compares the open choices of the
keyword extraction on the demo worlds and on public benchmarks, so that the
defaults can be fixed on evidence. The rule it serves: **as few tuned
parameters as possible, defaults from simple rules, stability and simplicity
first** — a choice that does not clearly win is dropped, not kept as a knob.

```bash
python tools/lexicon_lab/run.py --suite quick            # demo worlds of size S, about 3 min
python tools/lexicon_lab/run.py --fetch --suite full     # L and the benchmarks, about 45 min
python tools/lexicon_lab/run.py --from-json saved.json   # re-render a report from saved numbers
```

The report (Markdown, every table) is written to
`.cache/lexicon_lab/report-<suite>.md`; `--json` keeps the numbers. The lab
needs the language models and `snowballstemmer` (installed with the `docs`
extra) for the stemmed benchmark.

## Method

Each corpus is parsed once, with the engine's own code and parse cache; every
variant then scores **the same analyses** with
`cartolex.lexicon.scoring.score_units`, changing **one choice at a time**
around the defaults (a *family*). The window is the engine's default: a
candidate is used by at least 3 people and at most 60 % of them, and occurs
in at least 3 distinct texts. The choices
are switches of `ScoringOptions` and `BandRules`; only the counting unit is a
setting (`KeywordsConfig.counting_unit`, the build's
`keywords.extract.counting_unit`).

**Gold.** On a demo world, the field terms of its truth: theme terms and
methods — not study settings, drivers of change or the phrasing of the
templates, which are generic filler (`lexicon_truth`). On a benchmark, the
reference keyphrases. Terms and gold are compared through loose keys: lower
case, no accents, each word replaced by its corpus lemma (or by its Porter
stem when the gold is stemmed), articles and prepositions dropped.
*Reachable gold* is the gold that occurs in the texts of at least 3 people:
what a lexicon with that floor can find.

**Final lexicon.** The kept band, plus the terms to check that the judge
accepts. The lab's judge is the oracle: it accepts exactly the gold, so the
final precision is the precision of what is kept without asking, and the
recall is the recall of the whole procedure. A noisy judge (10 % of its
answers wrong) shows how much the result depends on a perfect judge.

**Measures.**

| measure | what it says |
| --- | --- |
| AI load | the terms to check: what a judge (the AI, or a person) must look at |
| precision | the share of the final lexicon that is gold |
| recall | the share of the reachable gold the final lexicon holds |
| F1 | their harmonic mean |
| AUC, best 10 % | how well the score puts the gold first: the probability that a gold candidate outscores another one, and the gold share of the best-scored tenth |
| gold aside | gold candidates the bands set aside (the cost of the set-aside rules) |
| Jaccard, rank ρ | stability: the final lexicon, and the order of its scores, when 10 % of the texts are removed (mean of two draws); read backwards, when 10 % are added |
| ARI topics, ARI themes | on demo worlds, the engine's term groups (topics: concept clusters; themes: proto-subfields) against the true themes of the kept terms that have one |
| mix cosine | on demo worlds, each person's theme mix read from the lexical matrix, against their true mix |

The last two run the engine as a project does: consolidation (with the
judge's answers as triage decisions), SVD and term clustering.

**Band rules.** Besides the operating points (the keep share, the low-score
share, the part-of rule, the common-modifier rule), the lab turns every rule
on and counts what each one catches and the gold share of it: a rule that
sends to check or sets aside is worth its place when that share is well
below the share of gold among all candidates.

**AI triage.** The harness (`handoff.py`) builds what a judge would receive —
the API's bare strings in batches of 150 (the engine's triage today), or one
*handoff bundle* with each term's evidence: people and texts, specificity,
forms, language, the longer phrases it sits in, its band and reason, and
optionally two usage lines — and estimates tokens (four characters each) and
cost at illustrative prices. Three scopes are compared: the judge sees every
candidate (today), the kept and to-check bands, or the to-check band only;
the bands it does not see keep their fate (kept stays kept, set aside stays
out). Fake judges answer: the oracle, and a noisy judge wrong 10 % of the
time. No paid service is called and no key is read.

## Data

| corpus | language | people | texts | kind |
| --- | --- | --- | --- | --- |
| demo S, L | en, fr | 39, 343 | 226, 2,137 | titles and abstracts |
| demo S, L trilingual | en, fr, pt | 39, 343 | 226, 2,137 | titles and abstracts |
| demo S, L bodies | en, fr | 39, 343 | 226, 2,137 | titles, abstracts and long bodies |
| `inspec` | en | 200 | 1,000 | abstracts, indexer keyphrases |
| `termith` | fr | 80 | 399 | abstracts, indexer keyphrases |
| `semeval` | en | 122 | 244 | full-text articles, author and reader keyphrases (stemmed) |
| `scielo` | pt | 200 | 1,000 | abstracts, author keywords |

As the lab counts them (the words of each text once; the gold of a demo world
is its field terms in every language, reachable when at least 3 people use
it):

| corpus | languages | people | texts | words | gold terms | reachable gold |
|---|---|---|---|---|---|---|
| demo S | en,fr | 39 | 226 | 46,036 | 11,444 | 1,208 |
| demo S bodies | en,fr | 39 | 226 | 211,917 | 11,444 | 2,949 |
| demo L | en,fr | 343 | 2,137 | 435,872 | 11,444 | 6,574 |
| demo L trilingual | en,fr,pt | 343 | 2,137 | 438,880 | 17,166 | 7,683 |
| demo L bodies | en,fr | 343 | 2,137 | 1,965,440 | 11,444 | 9,214 |
| inspec | en | 200 | 1,000 | 120,631 | 8,625 | 692 |
| termith | fr | 80 | 399 | 53,817 | 2,438 | 439 |
| semeval | en | 122 | 244 | 1,694,500 | 3,034 | 967 |
| scielo | pt | 200 | 1,000 | 204,362 | 3,196 | 818 |

The trilingual worlds of the stop-word measures below read one French text
in ten as English, its title in capitals (`demo_corpus(..., misdetected=True)`,
the corpora `demo S|L trilingual misdetected`): language detection sends
such text to the English stream of a real corpus (a mixed paragraph, a
shouted title), and none of its terms is English gold. The tables measured
before that change name the plain trilingual worlds.

Benchmarks have no people: consecutive texts form pseudo-people (5 abstracts,
or 2 articles), and pseudo-people pseudo-organisations of 5. They are read
from `.cache/datasets/` for evaluation only and never committed; `--fetch`
downloads them from public copies on a dataset hub.

The Portuguese set is made from a public dump of SciELO records with their
author keywords: the lab keeps the first 1,000 agronomy abstracts (by record
id) whose licence is CC BY 4.0, with a Portuguese title, an abstract of more
than 400 characters and at least three keywords. It was chosen because
SciELO is the main open collection of Portuguese-language science, its
records carry author keywords (a gold made independently of any extraction),
and filtering on the per-record licence keeps only openly licensed texts.
Author keywords are fewer and more often absent from the text than indexer
keyphrases, so recall against them is low for every method; what matters is
the comparison between variants.

## Results in brief

The numbers below come from `--suite full`, after the first default changes
it led to (the first variant of each family is the default at that time). At
gate G2 the owner approved the recommendations, putting quality first: the
common-modifier rule is now off and the AI judges the kept and to-check
bands. The tables were measured before that decision, with the
common-modifier rule on; the *recommended* rows and the common-modifier
table show the effect of turning it off. Every table of the report is
reproduced in this page; the report itself also has one detailed table per
corpus and family.

| choice | options compared | recommendation | status |
| --- | --- | --- | --- |
| English `of` complement | on, off | **off** | default changed |
| counting unit | person, text, organisation | **person** (People) | unchanged |
| text vote | raw frequency, presence, 1 + ln n | **raw frequency** | unchanged; the others stay lab switches |
| text-part weights | equal, body × 0.5, body × 0.25 | **equal** | unchanged; lab switch |
| length bonus | α = 2, 1, 0 | **α = 2** | unchanged (the setting `length_bonus_alpha`) |
| name recognition | off, people and places set aside | **off** | unchanged; lab switch |
| band: part of a longer phrase | 90 %, 100 %, off | **100 %** (never seen outside it) | default changed |
| band: low score | 0, 10 %, 20 % | **none** | default changed |
| band: keep share | 100 %, 50 % | **100 %** (every multi-word phrase kept) | unchanged |
| band: common modifiers | 20 %, off | **off** | default changed at G2; lab switch |
| what the AI judges | every candidate, kept and to check, to check only | **kept and to check** | decided at G2: the triage reads the kept and to-check bands only |

With the recommended set, the three bands are: **kept**, a phrase of two
words or more; **to check**, a single word; **set aside**, a phrase never
seen outside one and the same longer phrase. Nothing depends on a tuned
share any more, and the band of a candidate no longer depends on its score:
the score only orders the candidates. That is why, below, the counting unit,
the text vote, the part weights and the length bonus leave the lexicon
itself unchanged (same AI load, precision and recall) and move only the
ranking measures and, slightly, the theme recovery.

| corpus | variant | AI load | precision | recall | F1 | AUC | best 10 % | gold aside | Jaccard | ARI themes | mix cos |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demo S | defaults | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.525 | 25.1 % | 29 | 0.916 | 0.323 | 0.851 |
| demo S | recommended | 652 | 52.0 % | 84.6 % | 64.4 % | 0.525 | 25.1 % | 29 | 0.921 | 0.310 | 0.839 |
| demo S bodies | defaults | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.499 | 31.9 % | 54 | 0.924 | 0.330 | 0.884 |
| demo S bodies | recommended | 875 | 50.7 % | 83.9 % | 63.2 % | 0.499 | 31.9 % | 54 | 0.928 | 0.331 | 0.887 |
| demo L | defaults | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.516 | 28.0 % | 18 | 0.943 | 0.198 | 0.863 |
| demo L | recommended | 1,287 | 59.0 % | 85.6 % | 69.9 % | 0.516 | 28.0 % | 18 | 0.945 | 0.268 | 0.863 |
| demo L trilingual | defaults | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.520 | 24.1 % | 70 | 0.938 | 0.183 | 0.855 |
| demo L trilingual | recommended | 1,874 | 50.0 % | 80.4 % | 61.7 % | 0.520 | 24.1 % | 70 | 0.939 | 0.192 | 0.856 |
| demo L bodies | defaults | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.645 | 35.6 % | 13 | – | 0.298 | 0.873 |
| demo L bodies | recommended | 1,369 | 44.0 % | 81.8 % | 57.2 % | 0.645 | 35.6 % | 13 | – | 0.311 | 0.875 |
| inspec | defaults | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.645 | 44.7 % | 1 | 0.896 | – | – |
| inspec | recommended | 1,627 | 76.4 % | 90.8 % | 83.0 % | 0.645 | 44.7 % | 1 | 0.900 | – | – |
| termith | defaults | 925 | 88.5 % | 86.1 % | 87.3 % | 0.612 | 52.5 % | 0 | 0.914 | – | – |
| termith | recommended | 909 | 87.3 % | 86.1 % | 86.7 % | 0.612 | 52.5 % | 0 | 0.919 | – | – |
| semeval | defaults | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.745 | 36.7 % | 0 | 0.861 | – | – |
| semeval | recommended | 3,911 | 14.7 % | 82.9 % | 24.9 % | 0.745 | 36.7 % | 0 | 0.870 | – | – |
| scielo | defaults | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.593 | 34.1 % | 2 | 0.878 | – | – |
| scielo | recommended | 1,898 | 39.4 % | 81.5 % | 53.1 % | 0.593 | 34.1 % | 2 | 0.888 | – | – |

The recommended set differs from the defaults by the common-modifier rule
only. With the oracle, dropping it lowers the precision (the phrases it
sends to check are no longer judged), which is the reason the AI should
judge the kept band too (see [the AI triage](#ai-triage)).

## The English `of` complement

| corpus | variant | AI load | precision | recall | F1 | AUC | best 10 % | gold aside | Jaccard | ARI themes | mix cos |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demo S | off (default) | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.525 | 25.1 % | 29 | 0.916 | 0.323 | 0.851 |
| demo S | on | 1,204 | 39.0 % | 72.8 % | 50.8 % | 0.531 | 24.0 % | 172 | 0.911 | 0.297 | 0.844 |
| demo S bodies | off (default) | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.499 | 31.9 % | 54 | 0.924 | 0.330 | 0.884 |
| demo S bodies | on | 3,012 | 36.4 % | 78.4 % | 49.7 % | 0.573 | 30.7 % | 218 | 0.916 | 0.319 | 0.885 |
| demo L | off (default) | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.516 | 28.0 % | 18 | 0.943 | 0.198 | 0.863 |
| demo L | on | 3,842 | 32.8 % | 84.1 % | 47.2 % | 0.653 | 32.2 % | 122 | 0.924 | 0.203 | 0.864 |
| demo L trilingual | off (default) | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.520 | 24.1 % | 70 | 0.938 | 0.183 | 0.855 |
| demo L trilingual | on | 4,335 | 33.1 % | 78.8 % | 46.6 % | 0.618 | 27.0 % | 195 | 0.924 | 0.194 | 0.855 |
| demo L bodies | off (default) | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.645 | 35.6 % | 13 | – | 0.298 | 0.873 |
| demo L bodies | on | 12,280 | 20.2 % | 81.7 % | 32.3 % | 0.809 | 40.0 % | 21 | – | 0.255 | 0.873 |
| inspec | off (default) | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.645 | 44.7 % | 1 | 0.896 | – | – |
| inspec | on | 1,705 | 80.5 % | 91.0 % | 85.4 % | 0.644 | 44.5 % | 1 | 0.896 | – | – |
| termith | off (default) | 925 | 88.5 % | 86.1 % | 87.3 % | 0.612 | 52.5 % | 0 | 0.914 | – | – |
| termith | on | 925 | 88.5 % | 86.1 % | 87.3 % | 0.612 | 52.5 % | 0 | 0.914 | – | – |
| semeval | off (default) | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.745 | 36.7 % | 0 | 0.861 | – | – |
| semeval | on | 6,906 | 24.8 % | 82.9 % | 38.2 % | 0.736 | 34.4 % | 0 | 0.859 | – | – |
| scielo | off (default) | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.593 | 34.1 % | 2 | 0.878 | – | – |
| scielo | on | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.593 | 34.1 % | 2 | 0.878 | – | – |

The complement was the clearest result of the lab. On the demo worlds, the
English templates are full of `the X of Y` phrasing (`role of silicic acid
uptake`, `context of storm events`); with the complement, each such span is
a candidate used by many people, and the terms inside it become its
fragments and are set aside: the gold set aside grows from 29 terms to 172
on demo S. Without it, demo L goes from 32.8 % to 62.0 % precision and from
84.1 % to 85.6 % recall, with a quarter fewer terms to check (2,902 instead
of 3,842; with full texts 5,070 instead of 12,280), and the lexicon is more
stable (Jaccard 0.943 instead of 0.924). On the English benchmarks the
change is neutral (inspec, F1 85.8 % against 85.4 %) or a gain (semeval, F1
43.6 % against 38.2 %); the French and Portuguese corpora are unaffected.
The ranking AUC is lower without the complement on the demo worlds because
the `X of Y` spans were mostly low-scored non-gold candidates (more than
half the English candidates at size L), which made the gold look better
ranked; the best-scored tenth is also a smaller list. A true `X of Y` term
(`degrees of freedom`) is no longer a candidate: `degrees` and `freedom`
are.

## Counting unit

| corpus | variant | AI load | precision | recall | F1 | AUC | best 10 % | gold aside | Jaccard | ARI themes | mix cos |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demo S | person (default) | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.525 | 25.1 % | 29 | 0.916 | 0.323 | 0.851 |
| demo S | text | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.553 | 25.1 % | 29 | 0.916 | 0.318 | 0.851 |
| demo S | organisation | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.498 | 21.9 % | 29 | 0.916 | 0.321 | 0.843 |
| demo S bodies | person (default) | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.499 | 31.9 % | 54 | 0.924 | 0.330 | 0.884 |
| demo S bodies | text | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.564 | 37.4 % | 54 | 0.924 | 0.339 | 0.886 |
| demo S bodies | organisation | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.493 | 28.6 % | 54 | 0.924 | 0.350 | 0.885 |
| demo L | person (default) | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.516 | 28.0 % | 18 | 0.943 | 0.198 | 0.863 |
| demo L | text | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.596 | 35.1 % | 18 | 0.943 | 0.193 | 0.863 |
| demo L | organisation | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.494 | 22.1 % | 18 | 0.943 | 0.206 | 0.864 |
| demo L trilingual | person (default) | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.520 | 24.1 % | 70 | 0.938 | 0.183 | 0.855 |
| demo L trilingual | text | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.603 | 32.6 % | 70 | 0.938 | 0.190 | 0.855 |
| demo L trilingual | organisation | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.489 | 20.0 % | 70 | 0.938 | 0.185 | 0.854 |
| demo L bodies | person (default) | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.645 | 35.6 % | 13 | – | 0.298 | 0.873 |
| demo L bodies | text | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.700 | 45.3 % | 13 | – | 0.297 | 0.873 |
| demo L bodies | organisation | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.621 | 31.2 % | 13 | – | 0.289 | 0.873 |
| inspec | person (default) | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.645 | 44.7 % | 1 | 0.896 | – | – |
| inspec | text | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.644 | 44.2 % | 1 | 0.896 | – | – |
| inspec | organisation | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.653 | 49.0 % | 1 | 0.896 | – | – |
| termith | person (default) | 925 | 88.5 % | 86.1 % | 87.3 % | 0.612 | 52.5 % | 0 | 0.914 | – | – |
| termith | text | 925 | 88.5 % | 86.1 % | 87.3 % | 0.603 | 45.5 % | 0 | 0.914 | – | – |
| termith | organisation | 925 | 88.5 % | 86.1 % | 87.3 % | 0.620 | 52.5 % | 0 | 0.914 | – | – |
| semeval | person (default) | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.745 | 36.7 % | 0 | 0.861 | – | – |
| semeval | text | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.745 | 36.2 % | 0 | 0.861 | – | – |
| semeval | organisation | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.746 | 37.2 % | 0 | 0.861 | – | – |
| scielo | person (default) | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.593 | 34.1 % | 2 | 0.878 | – | – |
| scielo | text | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.591 | 32.2 % | 2 | 0.878 | – | – |
| scielo | organisation | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.588 | 33.1 % | 2 | 0.878 | – | – |

The three presets give the same lexicon on every corpus: the window always
counts people and the bands no longer read the score. They order it
differently. Counting texts ranks the gold better on every demo world (AUC
0.596 against 0.516 at size L, 0.700 against 0.645 with full texts; best
tenth 35.1 % against 28.0 %), counting organisations slightly worse; on the
benchmarks the three are within 0.01 of AUC (termith is the exception, text
worse in the best tenth: 45.5 % against 52.5 %). The benchmarks' people and
organisations are made up (consecutive texts), so they can hardly separate
the presets; the demo worlds can, but they are written from shared
templates. No preset
clearly wins: **People** stays the default, and whether the three presets
are worth offering — they say what should weigh the same, a choice of the
user rather than a gain of quality — is the owner's decision. Theme recovery
does not move (ARI themes within 0.02, mix cosine within 0.01).

## Text vote

| corpus | variant | AI load | precision | recall | F1 | AUC | best 10 % | gold aside | Jaccard | ARI themes | mix cos |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demo S | frequency (default) | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.525 | 25.1 % | 29 | 0.916 | 0.323 | 0.851 |
| demo S | presence | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.556 | 21.1 % | 29 | 0.916 | 0.308 | 0.851 |
| demo S | sublinear | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.539 | 23.6 % | 29 | 0.916 | 0.322 | 0.841 |
| demo S bodies | frequency (default) | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.499 | 31.9 % | 54 | 0.924 | 0.330 | 0.884 |
| demo S bodies | presence | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.513 | 22.8 % | 54 | 0.924 | 0.313 | 0.888 |
| demo S bodies | sublinear | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.503 | 28.7 % | 54 | 0.924 | 0.340 | 0.889 |
| demo L | frequency (default) | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.516 | 28.0 % | 18 | 0.943 | 0.198 | 0.863 |
| demo L | presence | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.516 | 26.3 % | 18 | 0.943 | 0.228 | 0.863 |
| demo L | sublinear | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.518 | 27.5 % | 18 | 0.943 | 0.208 | 0.863 |
| demo L trilingual | frequency (default) | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.520 | 24.1 % | 70 | 0.938 | 0.183 | 0.855 |
| demo L trilingual | presence | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.517 | 22.5 % | 70 | 0.938 | 0.191 | 0.854 |
| demo L trilingual | sublinear | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.522 | 23.5 % | 70 | 0.938 | 0.206 | 0.855 |
| demo L bodies | frequency (default) | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.645 | 35.6 % | 13 | – | 0.298 | 0.873 |
| demo L bodies | presence | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.609 | 34.4 % | 13 | – | 0.283 | 0.872 |
| demo L bodies | sublinear | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.631 | 33.4 % | 13 | – | 0.298 | 0.873 |
| inspec | frequency (default) | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.645 | 44.7 % | 1 | 0.896 | – | – |
| inspec | presence | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.624 | 40.9 % | 1 | 0.896 | – | – |
| inspec | sublinear | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.643 | 43.3 % | 1 | 0.896 | – | – |
| termith | frequency (default) | 925 | 88.5 % | 86.1 % | 87.3 % | 0.612 | 52.5 % | 0 | 0.914 | – | – |
| termith | presence | 925 | 88.5 % | 86.1 % | 87.3 % | 0.541 | 43.6 % | 0 | 0.914 | – | – |
| termith | sublinear | 925 | 88.5 % | 86.1 % | 87.3 % | 0.588 | 50.5 % | 0 | 0.914 | – | – |
| semeval | frequency (default) | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.745 | 36.7 % | 0 | 0.861 | – | – |
| semeval | presence | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.605 | 19.8 % | 0 | 0.861 | – | – |
| semeval | sublinear | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.695 | 28.5 % | 0 | 0.861 | – | – |
| scielo | frequency (default) | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.593 | 34.1 % | 2 | 0.878 | – | – |
| scielo | presence | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.519 | 24.4 % | 2 | 0.878 | – | – |
| scielo | sublinear | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.564 | 29.7 % | 2 | 0.878 | – | – |

Raw frequency ranks the gold best almost everywhere: presence (one vote per
text) loses most on the benchmarks (semeval AUC 0.605 against 0.745, best
tenth 19.8 % against 36.7 %; termith 0.541 against 0.612; scielo 0.519
against 0.593), and 1 + ln n lies between the two. Full texts do not change
the verdict (demo L with bodies: 0.645, 0.609, 0.631). Only on demo S, with
or without bodies, does presence have a higher AUC (0.556 against 0.525,
0.513 against 0.499), with a worse best tenth. **Raw frequency** stays;
presence and 1 + ln n stay lab switches.

## Text-part weights

| corpus | variant | AI load | precision | recall | F1 | AUC | best 10 % | gold aside | Jaccard | ARI themes | mix cos |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demo S bodies | equal (default) | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.499 | 31.9 % | 54 | 0.924 | 0.330 | 0.884 |
| demo S bodies | body 0.5 | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.494 | 31.9 % | 54 | 0.924 | 0.339 | 0.886 |
| demo S bodies | body 0.25 | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.493 | 30.5 % | 54 | 0.924 | 0.329 | 0.887 |
| demo L bodies | equal (default) | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.645 | 35.6 % | 13 | – | 0.298 | 0.873 |
| demo L bodies | body 0.5 | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.641 | 35.7 % | 13 | – | 0.291 | 0.874 |
| demo L bodies | body 0.25 | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.637 | 35.4 % | 13 | – | 0.282 | 0.874 |
| semeval | equal (default) | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.745 | 36.7 % | 0 | 0.861 | – | – |
| semeval | body 0.5 | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.751 | 37.3 % | 0 | 0.861 | – | – |
| semeval | body 0.25 | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.760 | 39.0 % | 0 | 0.861 | – | – |

Weighing the body down (titles and abstracts count fully) slightly lowers
the ranking on the demo worlds with full texts (AUC 0.645 → 0.637 at size L)
and raises it a little on semeval (0.745 → 0.760 at 0.25). The gain is small
and not consistent, and the engine's corpus contract gives each text as one
piece today, so the parts would first have to be carried. **Equal weights**
stay; part weights stay a lab switch.

## Length bonus

| corpus | variant | AI load | precision | recall | F1 | AUC | best 10 % | gold aside | Jaccard | ARI themes | mix cos |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demo S | α = 2 (default) | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.525 | 25.1 % | 29 | 0.916 | 0.323 | 0.851 |
| demo S | α = 1 | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.453 | 20.1 % | 29 | 0.916 | 0.308 | 0.844 |
| demo S | α = 0 (none) | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.299 | 4.3 % | 29 | 0.916 | 0.324 | 0.851 |
| demo S bodies | α = 2 (default) | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.499 | 31.9 % | 54 | 0.924 | 0.330 | 0.884 |
| demo S bodies | α = 1 | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.464 | 26.5 % | 54 | 0.924 | 0.338 | 0.887 |
| demo S bodies | α = 0 (none) | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.394 | 11.3 % | 54 | 0.924 | 0.345 | 0.891 |
| demo L | α = 2 (default) | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.516 | 28.0 % | 18 | 0.943 | 0.198 | 0.863 |
| demo L | α = 1 | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.483 | 19.2 % | 18 | 0.943 | 0.243 | 0.864 |
| demo L | α = 0 (none) | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.426 | 3.7 % | 18 | 0.943 | 0.260 | 0.867 |
| demo L trilingual | α = 2 (default) | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.520 | 24.1 % | 70 | 0.938 | 0.183 | 0.855 |
| demo L trilingual | α = 1 | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.488 | 15.8 % | 70 | 0.938 | 0.186 | 0.855 |
| demo L trilingual | α = 0 (none) | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.432 | 3.2 % | 70 | 0.938 | 0.217 | 0.858 |
| demo L bodies | α = 2 (default) | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.645 | 35.6 % | 13 | – | 0.298 | 0.873 |
| demo L bodies | α = 1 | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.629 | 29.2 % | 13 | – | 0.296 | 0.874 |
| demo L bodies | α = 0 (none) | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.601 | 13.6 % | 13 | – | 0.297 | 0.879 |
| inspec | α = 2 (default) | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.645 | 44.7 % | 1 | 0.896 | – | – |
| inspec | α = 1 | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.615 | 39.4 % | 1 | 0.896 | – | – |
| inspec | α = 0 (none) | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.533 | 37.0 % | 1 | 0.896 | – | – |
| termith | α = 2 (default) | 925 | 88.5 % | 86.1 % | 87.3 % | 0.612 | 52.5 % | 0 | 0.914 | – | – |
| termith | α = 1 | 925 | 88.5 % | 86.1 % | 87.3 % | 0.621 | 50.5 % | 0 | 0.914 | – | – |
| termith | α = 0 (none) | 925 | 88.5 % | 86.1 % | 87.3 % | 0.623 | 48.5 % | 0 | 0.914 | – | – |
| semeval | α = 2 (default) | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.745 | 36.7 % | 0 | 0.861 | – | – |
| semeval | α = 1 | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.748 | 35.9 % | 0 | 0.861 | – | – |
| semeval | α = 0 (none) | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.737 | 31.8 % | 0 | 0.861 | – | – |
| scielo | α = 2 (default) | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.593 | 34.1 % | 2 | 0.878 | – | – |
| scielo | α = 1 | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.618 | 37.2 % | 2 | 0.878 | – | – |
| scielo | α = 0 (none) | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.653 | 38.8 % | 2 | 0.878 | – | – |

The bonus `1 + α (L − 1)` puts precise phrases first. With α = 2 the best
tenth holds the most gold on the demo worlds (28.0 % at size L, against
19.2 % with α = 1 and 3.7 % without a bonus), on inspec, termith and
semeval; only scielo prefers a smaller α (38.8 % without, 34.1 % with α =
2), and the AUC is mixed (higher with α = 2 on the demo worlds and inspec,
lower on termith and scielo). No other value clearly wins: **α = 2** stays,
as the existing setting `length_bonus_alpha`. The ARI themes rise on demo L
without the bonus (0.260 against 0.198), within the noise of that measure
(see [theme recovery](#theme-recovery-and-stability)).

## Name recognition

| corpus | variant | AI load | precision | recall | F1 | AUC | best 10 % | gold aside | Jaccard | ARI themes | mix cos |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demo S | not recognised (default) | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.525 | 25.1 % | 29 | 0.916 | 0.323 | 0.851 |
| demo S | people and places set aside | 1,141 | 59.3 % | 84.1 % | 69.5 % | 0.525 | 25.1 % | 35 | 0.916 | 0.340 | 0.844 |
| demo S bodies | not recognised (default) | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.499 | 31.9 % | 54 | 0.924 | 0.330 | 0.884 |
| demo S bodies | people and places set aside | 2,182 | 57.3 % | 83.7 % | 68.0 % | 0.499 | 31.9 % | 60 | 0.924 | 0.341 | 0.885 |
| demo L | not recognised (default) | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.516 | 28.0 % | 18 | 0.943 | 0.198 | 0.863 |
| demo L | people and places set aside | 2,876 | 62.0 % | 85.3 % | 71.8 % | 0.516 | 28.0 % | 39 | 0.943 | 0.201 | 0.863 |
| inspec | not recognised (default) | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.645 | 44.7 % | 1 | 0.896 | – | – |
| inspec | people and places set aside | 1,669 | 81.1 % | 88.7 % | 84.7 % | 0.645 | 44.7 % | 15 | 0.896 | – | – |
| termith | not recognised (default) | 925 | 88.5 % | 86.1 % | 87.3 % | 0.612 | 52.5 % | 0 | 0.914 | – | – |
| termith | people and places set aside | 910 | 88.4 % | 83.4 % | 85.8 % | 0.612 | 52.5 % | 12 | 0.913 | – | – |
| semeval | not recognised (default) | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.745 | 36.7 % | 0 | 0.861 | – | – |
| semeval | people and places set aside | 6,717 | 29.7 % | 82.8 % | 43.7 % | 0.745 | 36.7 % | 1 | 0.861 | – | – |
| scielo | not recognised (default) | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.593 | 34.1 % | 2 | 0.878 | – | – |
| scielo | people and places set aside | 2,065 | 43.6 % | 80.8 % | 56.6 % | 0.593 | 34.1 % | 9 | 0.878 | – | – |

Setting aside what spaCy recognises as people and places costs recall on
every benchmark (inspec 90.8 % → 88.7 %, termith 86.1 % → 83.4 %) and gains
almost nothing: on most corpora what the rule catches is gold about as often
as any candidate, or more (termith 75 % against 37.6 %, inspec 34 % against
30.5 %; see [what each rule catches](#what-each-band-rule-catches)), because
places and eponyms are field terms. Only semeval gains (0.6 % gold among the
names), for a to-check band 2 % smaller. It also costs time — on
semeval, name recognition (195 s) takes longer than the parsing (41 s).
**No name recognition**; it stays a lab switch.

## Bands

### Operating points

On the recommended rules, each combination of the keep share, the low-score
share and the part-of rule, then the part-of rule at 90 % and the
common-modifier rule on.

| corpus | rules | kept | to check | set aside | precision, kept | precision, final | recall | F1 | gold set aside |
|---|---|---|---|---|---|---|---|---|---|
| demo S | keep 100%, low score 0%, fragments 100%, common modifiers off | 1,961 | 652 | 1,370 | 51.9 % | 52.0 % | 84.6 % | 64.4 % | 29 |
| demo S | keep 100%, low score 10%, fragments 100%, common modifiers off | 1,950 | 532 | 1,501 | 51.7 % | 51.7 % | 83.4 % | 63.8 % | 43 |
| demo S | keep 100%, low score 20%, fragments 100%, common modifiers off | 1,859 | 434 | 1,690 | 50.3 % | 50.3 % | 77.4 % | 61.0 % | 116 |
| demo S | keep 50%, low score 0%, fragments 100%, common modifiers off | 1,321 | 1,292 | 1,370 | 45.3 % | 58.6 % | 84.6 % | 69.2 % | 29 |
| demo S | keep 50%, low score 10%, fragments 100%, common modifiers off | 1,321 | 1,161 | 1,501 | 45.3 % | 58.2 % | 83.4 % | 68.6 % | 43 |
| demo S | keep 50%, low score 20%, fragments 100%, common modifiers off | 1,321 | 972 | 1,690 | 45.3 % | 56.4 % | 77.4 % | 65.2 % | 116 |
| demo S | keep 100%, low score 0%, fragments off, common modifiers off | 2,864 | 1,119 | 0 | 36.5 % | 36.6 % | 87.0 % | 51.6 % | 0 |
| demo S | keep 100%, low score 10%, fragments off, common modifiers off | 2,807 | 777 | 399 | 36.7 % | 36.7 % | 85.3 % | 51.4 % | 20 |
| demo S | keep 100%, low score 20%, fragments off, common modifiers off | 2,590 | 594 | 799 | 36.8 % | 36.8 % | 78.9 % | 50.2 % | 98 |
| demo S | keep 50%, low score 0%, fragments off, common modifiers off | 1,724 | 2,259 | 0 | 35.2 % | 48.5 % | 87.0 % | 62.2 % | 0 |
| demo S | keep 50%, low score 10%, fragments off, common modifiers off | 1,724 | 1,860 | 399 | 35.2 % | 48.0 % | 85.3 % | 61.4 % | 20 |
| demo S | keep 50%, low score 20%, fragments off, common modifiers off | 1,724 | 1,460 | 799 | 35.2 % | 46.0 % | 78.9 % | 58.1 % | 98 |
| demo S | keep 100%, low score 0%, fragments 90%, common modifiers off | 1,960 | 638 | 1,385 | 51.9 % | 52.0 % | 84.6 % | 64.4 % | 29 |
| demo S | keep 100%, low score 0%, fragments 100%, common modifiers 20% | 1,459 | 1,154 | 1,370 | 51.5 % | 59.1 % | 84.6 % | 69.6 % | 29 |
| demo S bodies | keep 100%, low score 0%, fragments 100%, common modifiers off | 4,868 | 875 | 2,672 | 50.7 % | 50.7 % | 83.9 % | 63.2 % | 54 |
| demo S bodies | keep 100%, low score 10%, fragments 100%, common modifiers off | 4,703 | 732 | 2,980 | 49.6 % | 49.7 % | 79.4 % | 61.1 % | 186 |
| demo S bodies | keep 100%, low score 20%, fragments 100%, common modifiers off | 4,338 | 631 | 3,446 | 49.0 % | 49.0 % | 72.2 % | 58.4 % | 398 |
| demo S bodies | keep 50%, low score 0%, fragments 100%, common modifiers off | 2,835 | 2,908 | 2,672 | 47.5 % | 62.4 % | 83.9 % | 71.6 % | 54 |
| demo S bodies | keep 50%, low score 10%, fragments 100%, common modifiers off | 2,835 | 2,600 | 2,980 | 47.5 % | 61.1 % | 79.4 % | 69.1 % | 186 |
| demo S bodies | keep 50%, low score 20%, fragments 100%, common modifiers off | 2,835 | 2,134 | 3,446 | 47.5 % | 58.9 % | 72.2 % | 64.8 % | 398 |
| demo S bodies | keep 100%, low score 0%, fragments off, common modifiers off | 6,915 | 1,500 | 0 | 36.4 % | 36.5 % | 85.7 % | 51.2 % | 0 |
| demo S bodies | keep 100%, low score 10%, fragments off, common modifiers off | 6,521 | 1,051 | 843 | 36.4 % | 36.5 % | 80.8 % | 50.3 % | 145 |
| demo S bodies | keep 100%, low score 20%, fragments off, common modifiers off | 5,847 | 897 | 1,671 | 36.9 % | 36.9 % | 73.2 % | 49.1 % | 367 |
| demo S bodies | keep 50%, low score 0%, fragments off, common modifiers off | 3,646 | 4,769 | 0 | 37.3 % | 52.5 % | 85.7 % | 65.1 % | 0 |
| demo S bodies | keep 50%, low score 10%, fragments off, common modifiers off | 3,646 | 3,926 | 843 | 37.3 % | 51.0 % | 80.8 % | 62.5 % | 145 |
| demo S bodies | keep 50%, low score 20%, fragments off, common modifiers off | 3,646 | 3,098 | 1,671 | 37.3 % | 48.6 % | 73.2 % | 58.4 % | 367 |
| demo S bodies | keep 100%, low score 0%, fragments 90%, common modifiers off | 4,845 | 836 | 2,734 | 50.9 % | 50.9 % | 83.8 % | 63.3 % | 57 |
| demo S bodies | keep 100%, low score 0%, fragments 100%, common modifiers 20% | 3,542 | 2,201 | 2,672 | 47.8 % | 57.2 % | 83.9 % | 68.0 % | 54 |
| demo L | keep 100%, low score 0%, fragments 100%, common modifiers off | 9,527 | 1,287 | 4,482 | 59.0 % | 59.0 % | 85.6 % | 69.9 % | 18 |
| demo L | keep 100%, low score 10%, fragments 100%, common modifiers off | 9,014 | 1,115 | 5,167 | 58.5 % | 58.5 % | 80.3 % | 67.7 % | 368 |
| demo L | keep 100%, low score 20%, fragments 100%, common modifiers off | 8,170 | 1,031 | 6,095 | 58.5 % | 58.5 % | 72.8 % | 64.9 % | 862 |
| demo L | keep 50%, low score 0%, fragments 100%, common modifiers off | 5,369 | 5,445 | 4,482 | 57.1 % | 71.0 % | 85.6 % | 77.6 % | 18 |
| demo L | keep 50%, low score 10%, fragments 100%, common modifiers off | 5,369 | 4,760 | 5,167 | 57.1 % | 69.6 % | 80.3 % | 74.6 % | 368 |
| demo L | keep 50%, low score 20%, fragments 100%, common modifiers off | 5,369 | 3,832 | 6,095 | 57.1 % | 67.5 % | 72.8 % | 70.0 % | 862 |
| demo L | keep 100%, low score 0%, fragments off, common modifiers off | 13,178 | 2,118 | 0 | 42.8 % | 42.8 % | 85.9 % | 57.1 % | 0 |
| demo L | keep 100%, low score 10%, fragments off, common modifiers off | 12,203 | 1,562 | 1,531 | 43.3 % | 43.3 % | 80.4 % | 56.3 % | 361 |
| demo L | keep 100%, low score 20%, fragments off, common modifiers off | 10,857 | 1,378 | 3,061 | 44.1 % | 44.1 % | 72.8 % | 54.9 % | 858 |
| demo L | keep 50%, low score 0%, fragments off, common modifiers off | 6,734 | 8,562 | 0 | 45.6 % | 60.6 % | 85.9 % | 71.1 % | 0 |
| demo L | keep 50%, low score 10%, fragments off, common modifiers off | 6,734 | 7,031 | 1,531 | 45.6 % | 59.0 % | 80.4 % | 68.1 % | 361 |
| demo L | keep 50%, low score 20%, fragments off, common modifiers off | 6,734 | 5,501 | 3,061 | 45.6 % | 56.6 % | 72.8 % | 63.7 % | 858 |
| demo L | keep 100%, low score 0%, fragments 90%, common modifiers off | 9,500 | 1,235 | 4,561 | 59.1 % | 59.2 % | 85.6 % | 70.0 % | 18 |
| demo L | keep 100%, low score 0%, fragments 100%, common modifiers 20% | 7,912 | 2,902 | 4,482 | 56.4 % | 62.0 % | 85.6 % | 71.9 % | 18 |
| demo L trilingual | keep 100%, low score 0%, fragments 100%, common modifiers off | 12,349 | 1,874 | 5,300 | 50.0 % | 50.0 % | 80.4 % | 61.7 % | 70 |
| demo L trilingual | keep 100%, low score 10%, fragments 100%, common modifiers off | 11,735 | 1,588 | 6,200 | 49.5 % | 49.5 % | 75.7 % | 59.9 % | 434 |
| demo L trilingual | keep 100%, low score 20%, fragments 100%, common modifiers off | 10,613 | 1,438 | 7,472 | 49.5 % | 49.5 % | 68.4 % | 57.5 % | 992 |
| demo L trilingual | keep 50%, low score 0%, fragments 100%, common modifiers off | 6,906 | 7,317 | 5,300 | 48.7 % | 63.6 % | 80.4 % | 71.0 % | 70 |
| demo L trilingual | keep 50%, low score 10%, fragments 100%, common modifiers off | 6,906 | 6,417 | 6,200 | 48.7 % | 62.2 % | 75.7 % | 68.3 % | 434 |
| demo L trilingual | keep 50%, low score 20%, fragments 100%, common modifiers off | 6,906 | 5,145 | 7,472 | 48.7 % | 59.8 % | 68.4 % | 63.8 % | 992 |
| demo L trilingual | keep 100%, low score 0%, fragments off, common modifiers off | 16,524 | 2,999 | 0 | 37.7 % | 37.8 % | 81.3 % | 51.6 % | 0 |
| demo L trilingual | keep 100%, low score 10%, fragments off, common modifiers off | 15,399 | 2,174 | 1,950 | 38.0 % | 38.1 % | 76.3 % | 50.8 % | 385 |
| demo L trilingual | keep 100%, low score 20%, fragments off, common modifiers off | 13,735 | 1,883 | 3,905 | 38.5 % | 38.5 % | 68.9 % | 49.4 % | 956 |
| demo L trilingual | keep 50%, low score 0%, fragments off, common modifiers off | 8,529 | 10,994 | 0 | 39.5 % | 54.8 % | 81.3 % | 65.5 % | 0 |
| demo L trilingual | keep 50%, low score 10%, fragments off, common modifiers off | 8,529 | 9,044 | 1,950 | 39.5 % | 53.2 % | 76.3 % | 62.7 % | 385 |
| demo L trilingual | keep 50%, low score 20%, fragments off, common modifiers off | 8,529 | 7,089 | 3,905 | 39.5 % | 50.6 % | 68.9 % | 58.4 % | 956 |
| demo L trilingual | keep 100%, low score 0%, fragments 90%, common modifiers off | 12,325 | 1,814 | 5,384 | 50.1 % | 50.1 % | 80.4 % | 61.7 % | 70 |
| demo L trilingual | keep 100%, low score 0%, fragments 100%, common modifiers 20% | 10,674 | 3,549 | 5,300 | 47.5 % | 52.4 % | 80.4 % | 63.5 % | 70 |
| demo L bodies | keep 100%, low score 0%, fragments 100%, common modifiers off | 17,132 | 1,369 | 6,418 | 43.9 % | 44.0 % | 81.8 % | 57.2 % | 13 |
| demo L bodies | keep 100%, low score 10%, fragments 100%, common modifiers off | 15,749 | 1,265 | 7,905 | 46.1 % | 46.2 % | 79.0 % | 58.3 % | 273 |
| demo L bodies | keep 100%, low score 20%, fragments 100%, common modifiers off | 14,070 | 1,187 | 9,662 | 48.7 % | 48.7 % | 74.4 % | 58.9 % | 694 |
| demo L bodies | keep 50%, low score 0%, fragments 100%, common modifiers off | 9,110 | 9,391 | 6,418 | 52.5 % | 63.5 % | 81.8 % | 71.5 % | 13 |
| demo L bodies | keep 50%, low score 10%, fragments 100%, common modifiers off | 9,110 | 7,904 | 7,905 | 52.5 % | 62.7 % | 79.0 % | 69.9 % | 273 |
| demo L bodies | keep 50%, low score 20%, fragments 100%, common modifiers off | 9,110 | 6,147 | 9,662 | 52.5 % | 61.3 % | 74.4 % | 67.2 % | 694 |
| demo L bodies | keep 100%, low score 0%, fragments off, common modifiers off | 22,647 | 2,272 | 0 | 33.3 % | 33.3 % | 81.9 % | 47.4 % | 0 |
| demo L bodies | keep 100%, low score 10%, fragments off, common modifiers off | 20,507 | 1,925 | 2,487 | 35.5 % | 35.5 % | 79.1 % | 49.0 % | 265 |
| demo L bodies | keep 100%, low score 20%, fragments off, common modifiers off | 18,180 | 1,755 | 4,984 | 37.7 % | 37.7 % | 74.5 % | 50.1 % | 689 |
| demo L bodies | keep 50%, low score 0%, fragments off, common modifiers off | 11,285 | 13,634 | 0 | 42.4 % | 53.7 % | 81.9 % | 64.9 % | 0 |
| demo L bodies | keep 50%, low score 10%, fragments off, common modifiers off | 11,285 | 11,147 | 2,487 | 42.4 % | 52.8 % | 79.1 % | 63.3 % | 265 |
| demo L bodies | keep 50%, low score 20%, fragments off, common modifiers off | 11,285 | 8,650 | 4,984 | 42.4 % | 51.3 % | 74.5 % | 60.8 % | 689 |
| demo L bodies | keep 100%, low score 0%, fragments 90%, common modifiers off | 16,986 | 1,269 | 6,664 | 44.3 % | 44.3 % | 81.8 % | 57.5 % | 13 |
| demo L bodies | keep 100%, low score 0%, fragments 100%, common modifiers 20% | 13,431 | 5,070 | 6,418 | 40.6 % | 48.6 % | 81.8 % | 60.9 % | 13 |
| inspec | keep 100%, low score 0%, fragments 100%, common modifiers off | 431 | 1,627 | 18 | 55.0 % | 76.4 % | 90.8 % | 83.0 % | 1 |
| inspec | keep 100%, low score 10%, fragments 100%, common modifiers off | 431 | 1,421 | 224 | 55.0 % | 75.3 % | 85.5 % | 80.1 % | 38 |
| inspec | keep 100%, low score 20%, fragments 100%, common modifiers off | 431 | 1,215 | 430 | 55.0 % | 74.3 % | 80.9 % | 77.5 % | 70 |
| inspec | keep 50%, low score 0%, fragments 100%, common modifiers off | 346 | 1,712 | 18 | 57.2 % | 80.9 % | 90.8 % | 85.6 % | 1 |
| inspec | keep 50%, low score 10%, fragments 100%, common modifiers off | 346 | 1,506 | 224 | 57.2 % | 80.0 % | 85.5 % | 82.7 % | 38 |
| inspec | keep 50%, low score 20%, fragments 100%, common modifiers off | 346 | 1,300 | 430 | 57.2 % | 79.1 % | 80.9 % | 80.0 % | 70 |
| inspec | keep 100%, low score 0%, fragments off, common modifiers off | 443 | 1,633 | 0 | 53.7 % | 75.4 % | 90.9 % | 82.4 % | 0 |
| inspec | keep 100%, low score 10%, fragments off, common modifiers off | 443 | 1,425 | 208 | 53.7 % | 74.3 % | 85.7 % | 79.6 % | 37 |
| inspec | keep 100%, low score 20%, fragments off, common modifiers off | 443 | 1,218 | 415 | 53.7 % | 73.2 % | 81.1 % | 77.0 % | 69 |
| inspec | keep 50%, low score 0%, fragments off, common modifiers off | 356 | 1,720 | 0 | 55.9 % | 80.0 % | 90.9 % | 85.1 % | 0 |
| inspec | keep 50%, low score 10%, fragments off, common modifiers off | 356 | 1,512 | 208 | 55.9 % | 79.1 % | 85.7 % | 82.2 % | 37 |
| inspec | keep 50%, low score 20%, fragments off, common modifiers off | 356 | 1,305 | 415 | 55.9 % | 78.1 % | 81.1 % | 79.6 % | 69 |
| inspec | keep 100%, low score 0%, fragments 90%, common modifiers off | 431 | 1,626 | 19 | 55.0 % | 76.4 % | 90.8 % | 83.0 % | 1 |
| inspec | keep 100%, low score 0%, fragments 100%, common modifiers 20% | 352 | 1,706 | 18 | 59.1 % | 81.3 % | 90.8 % | 85.8 % | 1 |
| termith | keep 100%, low score 0%, fragments 100%, common modifiers off | 92 | 909 | 7 | 40.2 % | 87.3 % | 86.1 % | 86.7 % | 0 |
| termith | keep 100%, low score 10%, fragments 100%, common modifiers off | 92 | 808 | 108 | 40.2 % | 86.7 % | 81.8 % | 84.2 % | 19 |
| termith | keep 100%, low score 20%, fragments 100%, common modifiers off | 92 | 707 | 209 | 40.2 % | 85.8 % | 75.6 % | 80.4 % | 47 |
| termith | keep 50%, low score 0%, fragments 100%, common modifiers off | 91 | 910 | 7 | 40.7 % | 87.5 % | 86.1 % | 86.8 % | 0 |
| termith | keep 50%, low score 10%, fragments 100%, common modifiers off | 91 | 809 | 108 | 40.7 % | 86.9 % | 81.8 % | 84.3 % | 19 |
| termith | keep 50%, low score 20%, fragments 100%, common modifiers off | 91 | 708 | 209 | 40.7 % | 86.0 % | 75.6 % | 80.5 % | 47 |
| termith | keep 100%, low score 0%, fragments off, common modifiers off | 99 | 909 | 0 | 37.4 % | 85.9 % | 86.1 % | 86.0 % | 0 |
| termith | keep 100%, low score 10%, fragments off, common modifiers off | 99 | 808 | 101 | 37.4 % | 85.3 % | 81.8 % | 83.5 % | 19 |
| termith | keep 100%, low score 20%, fragments off, common modifiers off | 99 | 707 | 202 | 37.4 % | 84.3 % | 75.6 % | 79.7 % | 47 |
| termith | keep 50%, low score 0%, fragments off, common modifiers off | 97 | 911 | 0 | 38.1 % | 86.3 % | 86.1 % | 86.2 % | 0 |
| termith | keep 50%, low score 10%, fragments off, common modifiers off | 97 | 810 | 101 | 38.1 % | 85.7 % | 81.8 % | 83.7 % | 19 |
| termith | keep 50%, low score 20%, fragments off, common modifiers off | 97 | 709 | 202 | 38.1 % | 84.7 % | 75.6 % | 79.9 % | 47 |
| termith | keep 100%, low score 0%, fragments 90%, common modifiers off | 92 | 909 | 7 | 40.2 % | 87.3 % | 86.1 % | 86.7 % | 0 |
| termith | keep 100%, low score 0%, fragments 100%, common modifiers 20% | 76 | 925 | 7 | 35.5 % | 88.5 % | 86.1 % | 87.3 % | 0 |
| semeval | keep 100%, low score 0%, fragments 100%, common modifiers off | 5,259 | 3,911 | 125 | 9.6 % | 14.7 % | 82.9 % | 24.9 % | 0 |
| semeval | keep 100%, low score 10%, fragments 100%, common modifiers off | 5,242 | 3,012 | 1,041 | 9.7 % | 14.6 % | 82.1 % | 24.8 % | 45 |
| semeval | keep 100%, low score 20%, fragments 100%, common modifiers off | 4,729 | 2,609 | 1,957 | 10.6 % | 15.9 % | 81.0 % | 26.5 % | 83 |
| semeval | keep 50%, low score 0%, fragments 100%, common modifiers off | 2,813 | 6,357 | 125 | 15.9 % | 25.6 % | 82.9 % | 39.2 % | 0 |
| semeval | keep 50%, low score 10%, fragments 100%, common modifiers off | 2,813 | 5,441 | 1,041 | 15.9 % | 25.4 % | 82.1 % | 38.8 % | 45 |
| semeval | keep 50%, low score 20%, fragments 100%, common modifiers off | 2,813 | 4,525 | 1,957 | 15.9 % | 25.2 % | 81.0 % | 38.4 % | 83 |
| semeval | keep 100%, low score 0%, fragments off, common modifiers off | 5,357 | 3,938 | 0 | 9.4 % | 14.4 % | 82.9 % | 24.5 % | 0 |
| semeval | keep 100%, low score 10%, fragments off, common modifiers off | 5,340 | 3,025 | 930 | 9.5 % | 14.3 % | 82.1 % | 24.4 % | 45 |
| semeval | keep 100%, low score 20%, fragments off, common modifiers off | 4,818 | 2,618 | 1,859 | 10.4 % | 15.6 % | 81.0 % | 26.1 % | 83 |
| semeval | keep 50%, low score 0%, fragments off, common modifiers off | 2,861 | 6,434 | 0 | 15.6 % | 25.3 % | 82.9 % | 38.7 % | 0 |
| semeval | keep 50%, low score 10%, fragments off, common modifiers off | 2,861 | 5,504 | 930 | 15.6 % | 25.1 % | 82.1 % | 38.4 % | 45 |
| semeval | keep 50%, low score 20%, fragments off, common modifiers off | 2,861 | 4,575 | 1,859 | 15.6 % | 24.8 % | 81.0 % | 38.0 % | 83 |
| semeval | keep 100%, low score 0%, fragments 90%, common modifiers off | 5,248 | 3,898 | 149 | 9.6 % | 14.7 % | 82.9 % | 24.9 % | 2 |
| semeval | keep 100%, low score 0%, fragments 100%, common modifiers 20% | 2,296 | 6,874 | 125 | 15.6 % | 29.6 % | 82.9 % | 43.6 % | 0 |
| scielo | keep 100%, low score 0%, fragments 100%, common modifiers off | 1,261 | 1,898 | 39 | 18.0 % | 39.4 % | 81.5 % | 53.1 % | 2 |
| scielo | keep 100%, low score 10%, fragments 100%, common modifiers off | 1,261 | 1,583 | 354 | 18.0 % | 37.8 % | 76.4 % | 50.6 % | 46 |
| scielo | keep 100%, low score 20%, fragments 100%, common modifiers off | 1,261 | 1,268 | 669 | 18.0 % | 35.8 % | 69.9 % | 47.3 % | 100 |
| scielo | keep 50%, low score 0%, fragments 100%, common modifiers off | 950 | 2,209 | 39 | 20.6 % | 47.1 % | 81.5 % | 59.7 % | 2 |
| scielo | keep 50%, low score 10%, fragments 100%, common modifiers off | 950 | 1,894 | 354 | 20.6 % | 45.5 % | 76.4 % | 57.1 % | 46 |
| scielo | keep 50%, low score 20%, fragments 100%, common modifiers off | 950 | 1,579 | 669 | 20.6 % | 43.3 % | 69.9 % | 53.5 % | 100 |
| scielo | keep 100%, low score 0%, fragments off, common modifiers off | 1,289 | 1,909 | 0 | 17.7 % | 38.8 % | 81.8 % | 52.7 % | 0 |
| scielo | keep 100%, low score 10%, fragments off, common modifiers off | 1,289 | 1,589 | 320 | 17.7 % | 37.3 % | 76.7 % | 50.2 % | 44 |
| scielo | keep 100%, low score 20%, fragments off, common modifiers off | 1,289 | 1,269 | 640 | 17.7 % | 35.2 % | 70.0 % | 46.9 % | 99 |
| scielo | keep 50%, low score 0%, fragments off, common modifiers off | 968 | 2,230 | 0 | 20.3 % | 46.7 % | 81.8 % | 59.4 % | 0 |
| scielo | keep 50%, low score 10%, fragments off, common modifiers off | 968 | 1,910 | 320 | 20.3 % | 45.0 % | 76.7 % | 56.7 % | 44 |
| scielo | keep 50%, low score 20%, fragments off, common modifiers off | 968 | 1,590 | 640 | 20.3 % | 42.8 % | 70.0 % | 53.2 % | 99 |
| scielo | keep 100%, low score 0%, fragments 90%, common modifiers off | 1,260 | 1,894 | 44 | 18.0 % | 39.4 % | 81.5 % | 53.1 % | 2 |
| scielo | keep 100%, low score 0%, fragments 100%, common modifiers 20% | 1,069 | 2,090 | 39 | 18.6 % | 43.6 % | 81.5 % | 56.8 % | 2 |

- **Part of a longer phrase.** The rule sets aside a large share of the
  candidates on the demo worlds (4,482 of 15,296 at size L), almost none of
  them gold; without it the kept band's precision falls from 59.0 % to
  42.8 % at size L, and the terms to check grow by 65 %. At 90 % or 100 %
  (never seen outside the longer phrase) the results are the same within
  about a hundred terms and the same precision and recall: **100 %**, which
  needs no tuned share.
- **Low score.** Setting aside the least specific tenth loses gold (demo L:
  recall 85.6 % → 80.3 %, 368 gold terms set aside; inspec 90.8 % → 85.5 %)
  to save 11–18 % of the terms to check; at 20 % the loss at least doubles.
  **No low-score rule.**
- **Keep share.** Keeping only the best-scored half of the multi-word
  phrases sends the other half to check (demo L: 1,287 → 5,445 terms to
  check) for little gain in the kept band's precision on the demo worlds
  (59.0 % → 57.1 %, lower) and a real one on semeval (9.6 % → 15.9 %); the
  final precision rises only because the oracle judges what is sent to it.
  **Every multi-word phrase is kept.**
- **Common modifiers.** See below.

### What each band rule catches

| corpus | band | reason | candidates | gold | gold share |
|---|---|---|---|---|---|
| demo S | kept | multiword | 1,435 | 738 | 51.4 % |
| demo S | check | single-word | 523 | 0 | 0.0 % |
| demo S | check | common-modifier | 499 | 264 | 52.9 % |
| demo S | aside | part-of | 1,370 | 29 | 2.1 % |
| demo S | aside | low-score | 128 | 14 | 10.9 % |
| demo S | aside | name | 28 | 6 | 21.4 % |
| demo S | all | every candidate | 3,983 | 1,051 | 26.4 % |
| demo S bodies | kept | multiword | 3,389 | 1,574 | 46.4 % |
| demo S bodies | check | single-word | 723 | 6 | 0.8 % |
| demo S bodies | check | common-modifier | 1,300 | 758 | 58.3 % |
| demo S bodies | aside | part-of | 2,672 | 54 | 2.0 % |
| demo S bodies | aside | low-score | 297 | 129 | 43.4 % |
| demo S bodies | aside | name | 34 | 6 | 17.6 % |
| demo S bodies | all | every candidate | 8,415 | 2,527 | 30.0 % |
| demo L | kept | multiword | 7,404 | 4,123 | 55.7 % |
| demo L | check | single-word | 1,098 | 6 | 0.5 % |
| demo L | check | common-modifier | 1,582 | 1,131 | 71.5 % |
| demo L | aside | part-of | 4,482 | 18 | 0.4 % |
| demo L | aside | low-score | 677 | 347 | 51.3 % |
| demo L | aside | name | 53 | 21 | 39.6 % |
| demo L | all | every candidate | 15,296 | 5,646 | 36.9 % |
| demo L trilingual | kept | multiword | 10,091 | 4,740 | 47.0 % |
| demo L trilingual | check | single-word | 1,588 | 7 | 0.4 % |
| demo L trilingual | check | common-modifier | 1,644 | 1,069 | 65.0 % |
| demo L trilingual | aside | part-of | 5,300 | 70 | 1.3 % |
| demo L trilingual | aside | low-score | 900 | 364 | 40.4 % |
| demo L trilingual | all | every candidate | 19,523 | 6,250 | 32.0 % |
| demo L bodies | kept | multiword | 12,219 | 5,218 | 42.7 % |
| demo L bodies | check | single-word | 1,265 | 9 | 0.7 % |
| demo L bodies | check | common-modifier | 3,530 | 2,050 | 58.1 % |
| demo L bodies | aside | part-of | 6,418 | 13 | 0.2 % |
| demo L bodies | aside | low-score | 1,487 | 260 | 17.5 % |
| demo L bodies | all | every candidate | 24,919 | 7,550 | 30.3 % |
| inspec | kept | multiword | 348 | 205 | 58.9 % |
| inspec | check | single-word | 1,389 | 349 | 25.1 % |
| inspec | check | common-modifier | 79 | 29 | 36.7 % |
| inspec | aside | part-of | 18 | 1 | 5.6 % |
| inspec | aside | low-score | 201 | 35 | 17.4 % |
| inspec | aside | name | 41 | 14 | 34.1 % |
| inspec | all | every candidate | 2,076 | 633 | 30.5 % |
| termith | kept | multiword | 75 | 27 | 36.0 % |
| termith | check | single-word | 793 | 311 | 39.2 % |
| termith | check | common-modifier | 16 | 10 | 62.5 % |
| termith | aside | part-of | 7 | 0 | 0.0 % |
| termith | aside | low-score | 101 | 19 | 18.8 % |
| termith | aside | name | 16 | 12 | 75.0 % |
| termith | all | every candidate | 1,008 | 379 | 37.6 % |
| semeval | kept | multiword | 2,279 | 367 | 16.1 % |
| semeval | check | single-word | 2,914 | 406 | 13.9 % |
| semeval | check | common-modifier | 2,950 | 158 | 5.4 % |
| semeval | aside | part-of | 125 | 0 | 0.0 % |
| semeval | aside | low-score | 857 | 45 | 5.3 % |
| semeval | aside | name | 170 | 1 | 0.6 % |
| semeval | all | every candidate | 9,295 | 977 | 10.5 % |
| scielo | kept | multiword | 1,060 | 197 | 18.6 % |
| scielo | check | single-word | 1,564 | 397 | 25.4 % |
| scielo | check | common-modifier | 192 | 28 | 14.6 % |
| scielo | aside | part-of | 39 | 2 | 5.1 % |
| scielo | aside | low-score | 309 | 43 | 13.9 % |
| scielo | aside | name | 34 | 7 | 20.6 % |
| scielo | all | every candidate | 3,198 | 674 | 21.1 % |

Among all candidates, 10.5 % (semeval) to 37.6 % (termith) are gold. The
part-of rule catches almost none (0–2 % on the demo worlds, at most 5.6 % on
the benchmarks): it is the one set-aside rule worth its place. The low-score
tail holds gold at 5–51 %, the names at 1–75 %. Single words are gold 0–1 %
of the time on the demo worlds (their field terms are all multi-word) and
14–39 % on the benchmarks, against 16–59 % for the kept multi-word phrases:
hence a band of their own, checked rather than kept or set aside.

### Common modifiers

| corpus | variant | AI load | precision | recall | F1 | AUC | best 10 % | gold aside | Jaccard | ARI themes | mix cos |
|---|---|---|---|---|---|---|---|---|---|---|---|
| demo S | to check, 20 % (default) | 1,154 | 59.1 % | 84.6 % | 69.6 % | 0.525 | 25.1 % | 29 | 0.916 | 0.323 | 0.851 |
| demo S | off | 652 | 52.0 % | 84.6 % | 64.4 % | 0.525 | 25.1 % | 29 | 0.921 | 0.310 | 0.839 |
| demo S bodies | to check, 20 % (default) | 2,201 | 57.2 % | 83.9 % | 68.0 % | 0.499 | 31.9 % | 54 | 0.924 | 0.330 | 0.884 |
| demo S bodies | off | 875 | 50.7 % | 83.9 % | 63.2 % | 0.499 | 31.9 % | 54 | 0.928 | 0.331 | 0.887 |
| demo L | to check, 20 % (default) | 2,902 | 62.0 % | 85.6 % | 71.9 % | 0.516 | 28.0 % | 18 | 0.943 | 0.198 | 0.863 |
| demo L | off | 1,287 | 59.0 % | 85.6 % | 69.9 % | 0.516 | 28.0 % | 18 | 0.945 | 0.268 | 0.863 |
| demo L trilingual | to check, 20 % (default) | 3,549 | 52.4 % | 80.4 % | 63.5 % | 0.520 | 24.1 % | 70 | 0.938 | 0.183 | 0.855 |
| demo L trilingual | off | 1,874 | 50.0 % | 80.4 % | 61.7 % | 0.520 | 24.1 % | 70 | 0.939 | 0.192 | 0.856 |
| demo L bodies | to check, 20 % (default) | 5,070 | 48.6 % | 81.8 % | 60.9 % | 0.645 | 35.6 % | 13 | – | 0.298 | 0.873 |
| demo L bodies | off | 1,369 | 44.0 % | 81.8 % | 57.2 % | 0.645 | 35.6 % | 13 | – | 0.311 | 0.875 |
| inspec | to check, 20 % (default) | 1,706 | 81.3 % | 90.8 % | 85.8 % | 0.645 | 44.7 % | 1 | 0.896 | – | – |
| inspec | off | 1,627 | 76.4 % | 90.8 % | 83.0 % | 0.645 | 44.7 % | 1 | 0.900 | – | – |
| termith | to check, 20 % (default) | 925 | 88.5 % | 86.1 % | 87.3 % | 0.612 | 52.5 % | 0 | 0.914 | – | – |
| termith | off | 909 | 87.3 % | 86.1 % | 86.7 % | 0.612 | 52.5 % | 0 | 0.919 | – | – |
| semeval | to check, 20 % (default) | 6,874 | 29.6 % | 82.9 % | 43.6 % | 0.745 | 36.7 % | 0 | 0.861 | – | – |
| semeval | off | 3,911 | 14.7 % | 82.9 % | 24.9 % | 0.745 | 36.7 % | 0 | 0.870 | – | – |
| scielo | to check, 20 % (default) | 2,090 | 43.6 % | 81.5 % | 56.8 % | 0.593 | 34.1 % | 2 | 0.878 | – | – |
| scielo | off | 1,898 | 39.4 % | 81.5 % | 53.1 % | 0.593 | 34.1 % | 2 | 0.888 | – | – |

A phrase whose edge adjective appears in the candidates of at least 20 % of
the people is sent to check. What the rule catches is gold more often than
the kept band on every demo world (52.9–71.5 % against 42.7–55.7 %) and on
termith (62.5 % against 36.0 %) — widespread adjectives are the field's own
(`coastal`, `marine`) — but less often on inspec (36.7 % against 58.9 %),
semeval (5.4 % against 16.1 %) and scielo (14.6 % against 18.6 %), where
they are generic (`new`, `different`). It does not clearly win, so the lab
recommends dropping it; since it helps on real English and Portuguese texts
when the kept band goes unjudged, it stays the default until the owner
decides together with [what the AI judges](#ai-triage).

## Theme recovery and stability

Theme recovery barely depends on the lexicon choices: the ARI of term groups
against the true themes stays between 0.18 and 0.35 and the person-mix
cosine between 0.84 and 0.89 whatever the variant. The ARI moves by up to
0.07 with changes that only alter which phrases are kept (the
common-modifier rule on demo L: 0.198 → 0.268), so differences below that
are not read as evidence. With the defaults, the final lexicon keeps 86–94 %
of its terms (Jaccard) when a tenth of the texts is removed, and the order
of its scores keeps a rank correlation of 0.97–0.98. Dropping the `of`
complement raises the Jaccard (demo L 0.924 → 0.943); no other choice moves
it by more than 0.01.

## AI triage

### What the judge sees

| corpus | judge | what the judge sees | terms judged | precision | recall | F1 |
|---|---|---|---|---|---|---|
| demo S | judge: oracle | every candidate | 3,983 | 100.0 % | 87.0 % | 93.1 % |
| demo S | judge: oracle | kept and to-check bands | 2,613 | 100.0 % | 84.6 % | 91.7 % |
| demo S | judge: oracle | to-check band | 652 | 52.0 % | 84.6 % | 64.4 % |
| demo S | judge: noisy 10% | every candidate | 3,983 | 76.9 % | 77.0 % | 76.9 % |
| demo S | judge: noisy 10% | kept and to-check bands | 2,613 | 87.1 % | 75.0 % | 80.6 % |
| demo S | judge: noisy 10% | to-check band | 652 | 50.4 % | 84.6 % | 63.2 % |
| demo S bodies | judge: oracle | every candidate | 8,415 | 100.0 % | 85.7 % | 92.3 % |
| demo S bodies | judge: oracle | kept and to-check bands | 5,743 | 100.0 % | 83.9 % | 91.2 % |
| demo S bodies | judge: oracle | to-check band | 875 | 50.7 % | 83.9 % | 63.2 % |
| demo S bodies | judge: noisy 10% | every candidate | 8,415 | 79.5 % | 77.6 % | 78.5 % |
| demo S bodies | judge: noisy 10% | kept and to-check bands | 5,743 | 87.3 % | 75.9 % | 81.2 % |
| demo S bodies | judge: noisy 10% | to-check band | 875 | 49.8 % | 83.8 % | 62.5 % |
| demo L | judge: oracle | every candidate | 15,296 | 100.0 % | 85.9 % | 92.4 % |
| demo L | judge: oracle | kept and to-check bands | 10,814 | 100.0 % | 85.6 % | 92.2 % |
| demo L | judge: oracle | to-check band | 1,287 | 59.0 % | 85.6 % | 69.9 % |
| demo L | judge: noisy 10% | every candidate | 15,296 | 83.9 % | 78.0 % | 80.9 % |
| demo L | judge: noisy 10% | kept and to-check bands | 10,814 | 90.7 % | 77.7 % | 83.7 % |
| demo L | judge: noisy 10% | to-check band | 1,287 | 58.2 % | 85.6 % | 69.3 % |
| demo L trilingual | judge: oracle | every candidate | 19,523 | 100.0 % | 81.3 % | 89.7 % |
| demo L trilingual | judge: oracle | kept and to-check bands | 14,223 | 100.0 % | 80.4 % | 89.2 % |
| demo L trilingual | judge: oracle | to-check band | 1,874 | 50.0 % | 80.4 % | 61.7 % |
| demo L trilingual | judge: noisy 10% | every candidate | 19,523 | 81.5 % | 73.3 % | 77.2 % |
| demo L trilingual | judge: noisy 10% | kept and to-check bands | 14,223 | 87.8 % | 72.5 % | 79.4 % |
| demo L trilingual | judge: noisy 10% | to-check band | 1,874 | 49.2 % | 80.4 % | 61.1 % |
| demo L bodies | judge: oracle | every candidate | 24,919 | 100.0 % | 81.9 % | 90.1 % |
| demo L bodies | judge: oracle | kept and to-check bands | 18,501 | 100.0 % | 81.8 % | 90.0 % |
| demo L bodies | judge: oracle | to-check band | 1,369 | 44.0 % | 81.8 % | 57.2 % |
| demo L bodies | judge: noisy 10% | every candidate | 24,919 | 79.9 % | 73.6 % | 76.6 % |
| demo L bodies | judge: noisy 10% | kept and to-check bands | 18,501 | 85.7 % | 73.5 % | 79.1 % |
| demo L bodies | judge: noisy 10% | to-check band | 1,369 | 43.5 % | 81.8 % | 56.8 % |
| inspec | judge: oracle | every candidate | 2,076 | 100.0 % | 90.9 % | 95.2 % |
| inspec | judge: oracle | kept and to-check bands | 2,058 | 100.0 % | 90.8 % | 95.2 % |
| inspec | judge: oracle | to-check band | 1,627 | 76.4 % | 90.8 % | 83.0 % |
| inspec | judge: noisy 10% | every candidate | 2,076 | 79.6 % | 80.2 % | 79.9 % |
| inspec | judge: noisy 10% | kept and to-check bands | 2,058 | 79.9 % | 80.1 % | 80.0 % |
| inspec | judge: noisy 10% | to-check band | 1,627 | 64.8 % | 83.4 % | 72.9 % |
| termith | judge: oracle | every candidate | 1,008 | 100.0 % | 86.1 % | 92.5 % |
| termith | judge: oracle | kept and to-check bands | 1,001 | 100.0 % | 86.1 % | 92.5 % |
| termith | judge: oracle | to-check band | 909 | 87.3 % | 86.1 % | 86.7 % |
| termith | judge: noisy 10% | every candidate | 1,008 | 81.6 % | 79.7 % | 80.6 % |
| termith | judge: noisy 10% | kept and to-check bands | 1,001 | 81.8 % | 79.7 % | 80.7 % |
| termith | judge: noisy 10% | to-check band | 909 | 73.8 % | 80.6 % | 77.0 % |
| semeval | judge: oracle | every candidate | 9,295 | 100.0 % | 82.9 % | 90.7 % |
| semeval | judge: oracle | kept and to-check bands | 9,170 | 100.0 % | 82.9 % | 90.7 % |
| semeval | judge: oracle | to-check band | 3,911 | 14.7 % | 82.9 % | 24.9 % |
| semeval | judge: noisy 10% | every candidate | 9,295 | 47.8 % | 76.3 % | 58.8 % |
| semeval | judge: noisy 10% | kept and to-check bands | 9,170 | 48.2 % | 76.3 % | 59.1 % |
| semeval | judge: noisy 10% | to-check band | 3,911 | 13.6 % | 81.1 % | 23.2 % |
| scielo | judge: oracle | every candidate | 3,198 | 100.0 % | 81.8 % | 90.0 % |
| scielo | judge: oracle | kept and to-check bands | 3,159 | 100.0 % | 81.5 % | 89.8 % |
| scielo | judge: oracle | to-check band | 1,898 | 39.4 % | 81.5 % | 53.1 % |
| scielo | judge: noisy 10% | every candidate | 3,198 | 69.8 % | 74.1 % | 71.9 % |
| scielo | judge: noisy 10% | kept and to-check bands | 3,159 | 70.1 % | 73.8 % | 71.9 % |
| scielo | judge: noisy 10% | to-check band | 1,898 | 34.6 % | 76.3 % | 47.6 % |

If the judge sees only the to-check band, the kept band goes unjudged and
the final precision is the kept band's: 44–59 % on the demo worlds with the
recommended rules, where the rest is study settings and template phrasing.
If it sees the kept and to-check bands, a perfect judge reaches 100 %, and a
judge wrong one time in ten still reaches 86–91 % on the demo worlds and
70–82 % on the benchmarks (48 % on semeval, whose gold is sparse), and loses
6–11 points of recall to its mistakes. Leaving out the set-aside band loses
at most 2.4 points of recall against today's every-candidate triage (demo S;
1.8 with full texts, under 1 elsewhere) and saves 26–34 % of the terms on
the demo worlds (15,296 → 10,814 at size L), about 1 % on the benchmarks.
**The lab recommends that the AI judge the kept and to-check bands**; the
engine's triage still reads every candidate, and the change waits for the
owner.

### Routes and what they cost

| corpus | route | what the judge sees | terms | calls | input tokens | output tokens | small model, 0.10 / 0.30 per M tokens | large model, 2 / 6 per M tokens |
|---|---|---|---|---|---|---|---|---|
| demo S | API | every candidate | 3,983 | 27 | 56,095 | 45,374 | 0.019 | 0.384 |
| demo S | API | kept and to-check bands | 2,613 | 18 | 38,108 | 31,632 | 0.013 | 0.266 |
| demo S | handoff | kept and to-check bands | 2,613 | 1 | 67,704 | 31,632 | 0.016 | 0.325 |
| demo S | handoff, with usage lines | kept and to-check bands | 2,613 | 1 | 220,224 | 31,632 | 0.032 | 0.630 |
| demo S | API | to-check band | 652 | 5 | 8,447 | 4,459 | 0.002 | 0.044 |
| demo S | handoff | to-check band | 652 | 1 | 20,619 | 4,459 | 0.003 | 0.068 |
| demo S | handoff, with usage lines | to-check band | 652 | 1 | 62,633 | 4,459 | 0.008 | 0.152 |
| demo S bodies | API | every candidate | 8,415 | 57 | 123,709 | 106,378 | 0.044 | 0.886 |
| demo S bodies | API | kept and to-check bands | 5,743 | 39 | 86,962 | 77,403 | 0.032 | 0.638 |
| demo S bodies | handoff | kept and to-check bands | 5,743 | 1 | 148,818 | 77,403 | 0.038 | 0.762 |
| demo S bodies | handoff, with usage lines | kept and to-check bands | 5,743 | 1 | 501,151 | 77,403 | 0.073 | 1.467 |
| demo S bodies | API | to-check band | 875 | 6 | 10,451 | 6,055 | 0.003 | 0.057 |
| demo S bodies | handoff | to-check band | 875 | 1 | 29,711 | 6,055 | 0.005 | 0.096 |
| demo S bodies | handoff, with usage lines | to-check band | 875 | 1 | 85,470 | 6,055 | 0.010 | 0.207 |
| demo L | API | every candidate | 15,296 | 102 | 224,718 | 197,219 | 0.082 | 1.633 |
| demo L | API | kept and to-check bands | 10,814 | 73 | 163,923 | 147,247 | 0.061 | 1.211 |
| demo L | handoff | kept and to-check bands | 10,814 | 1 | 274,053 | 147,247 | 0.072 | 1.432 |
| demo L | handoff, with usage lines | kept and to-check bands | 10,814 | 1 | 962,958 | 147,247 | 0.140 | 2.809 |
| demo L | API | to-check band | 1,287 | 9 | 15,604 | 8,913 | 0.004 | 0.085 |
| demo L | handoff | to-check band | 1,287 | 1 | 45,082 | 8,913 | 0.007 | 0.144 |
| demo L | handoff, with usage lines | to-check band | 1,287 | 1 | 127,495 | 8,913 | 0.015 | 0.308 |
| demo L trilingual | API | every candidate | 19,523 | 131 | 289,485 | 255,012 | 0.105 | 2.109 |
| demo L trilingual | API | kept and to-check bands | 14,223 | 95 | 215,152 | 195,386 | 0.080 | 1.603 |
| demo L trilingual | handoff | kept and to-check bands | 14,223 | 1 | 362,080 | 195,386 | 0.095 | 1.896 |
| demo L trilingual | handoff, with usage lines | kept and to-check bands | 14,223 | 1 | 1,242,128 | 195,386 | 0.183 | 3.657 |
| demo L trilingual | API | to-check band | 1,874 | 13 | 22,643 | 13,103 | 0.006 | 0.124 |
| demo L trilingual | handoff | to-check band | 1,874 | 1 | 65,964 | 13,103 | 0.011 | 0.211 |
| demo L trilingual | handoff, with usage lines | to-check band | 1,874 | 1 | 186,621 | 13,103 | 0.023 | 0.452 |
| demo L bodies | API | every candidate | 24,919 | 167 | 379,601 | 346,215 | 0.142 | 2.836 |
| demo L bodies | API | kept and to-check bands | 18,501 | 124 | 288,784 | 270,898 | 0.110 | 2.203 |
| demo L bodies | handoff | kept and to-check bands | 18,501 | 1 | 488,078 | 270,898 | 0.130 | 2.602 |
| demo L bodies | handoff, with usage lines | kept and to-check bands | 18,501 | 1 | 1,676,952 | 270,898 | 0.249 | 4.979 |
| demo L bodies | API | to-check band | 1,369 | 10 | 17,166 | 9,514 | 0.005 | 0.091 |
| demo L bodies | handoff | to-check band | 1,369 | 1 | 49,317 | 9,514 | 0.008 | 0.156 |
| demo L bodies | handoff, with usage lines | to-check band | 1,369 | 1 | 137,165 | 9,514 | 0.017 | 0.331 |
| inspec | API | every candidate | 2,076 | 14 | 25,128 | 15,636 | 0.007 | 0.144 |
| inspec | API | kept and to-check bands | 2,058 | 14 | 25,054 | 15,473 | 0.007 | 0.143 |
| inspec | handoff | kept and to-check bands | 2,058 | 1 | 46,851 | 15,473 | 0.009 | 0.187 |
| inspec | handoff, with usage lines | kept and to-check bands | 2,058 | 1 | 175,764 | 15,473 | 0.022 | 0.444 |
| inspec | API | to-check band | 1,627 | 11 | 19,005 | 10,799 | 0.005 | 0.103 |
| inspec | handoff | to-check band | 1,627 | 1 | 36,676 | 10,799 | 0.007 | 0.138 |
| inspec | handoff, with usage lines | to-check band | 1,627 | 1 | 135,418 | 10,799 | 0.017 | 0.336 |
| termith | API | every candidate | 1,008 | 7 | 12,370 | 7,392 | 0.003 | 0.069 |
| termith | API | kept and to-check bands | 1,001 | 7 | 12,324 | 7,298 | 0.003 | 0.068 |
| termith | handoff | kept and to-check bands | 1,001 | 1 | 22,264 | 7,298 | 0.004 | 0.088 |
| termith | handoff, with usage lines | kept and to-check bands | 1,001 | 1 | 86,863 | 7,298 | 0.011 | 0.218 |
| termith | API | to-check band | 909 | 7 | 11,792 | 6,162 | 0.003 | 0.061 |
| termith | handoff | to-check band | 909 | 1 | 20,040 | 6,162 | 0.004 | 0.077 |
| termith | handoff, with usage lines | to-check band | 909 | 1 | 77,552 | 6,162 | 0.010 | 0.192 |
| semeval | API | every candidate | 9,295 | 62 | 117,357 | 81,546 | 0.036 | 0.724 |
| semeval | API | kept and to-check bands | 9,170 | 62 | 116,783 | 80,318 | 0.036 | 0.715 |
| semeval | handoff | kept and to-check bands | 9,170 | 1 | 229,009 | 80,318 | 0.047 | 0.940 |
| semeval | handoff, with usage lines | kept and to-check bands | 9,170 | 1 | 712,268 | 80,318 | 0.095 | 1.906 |
| semeval | API | to-check band | 3,911 | 27 | 45,520 | 24,293 | 0.012 | 0.237 |
| semeval | handoff | to-check band | 3,911 | 1 | 96,718 | 24,293 | 0.017 | 0.339 |
| semeval | handoff, with usage lines | to-check band | 3,911 | 1 | 286,147 | 24,293 | 0.036 | 0.718 |
| scielo | API | every candidate | 3,198 | 22 | 41,552 | 28,639 | 0.013 | 0.255 |
| scielo | API | kept and to-check bands | 3,159 | 22 | 41,369 | 28,250 | 0.013 | 0.252 |
| scielo | handoff | kept and to-check bands | 3,159 | 1 | 75,287 | 28,250 | 0.016 | 0.320 |
| scielo | handoff, with usage lines | kept and to-check bands | 3,159 | 1 | 288,898 | 28,250 | 0.037 | 0.747 |
| scielo | API | to-check band | 1,898 | 13 | 22,614 | 13,045 | 0.006 | 0.123 |
| scielo | handoff | to-check band | 1,898 | 1 | 44,826 | 13,045 | 0.008 | 0.168 |
| scielo | handoff, with usage lines | to-check band | 1,898 | 1 | 169,391 | 13,045 | 0.021 | 0.417 |

Tokens are estimated at four characters each; the prices are in US dollars
per million tokens and illustrative (to be checked before any use). An API
triage sends bare strings in batches of 150 with the same instructions each
time; a handoff bundle sends each term once with its evidence (people,
texts, specificity, forms, language, containing phrases, band and reason),
which multiplies the input by 1.7 to 3, and two usage lines per term by
about 3 more. Every route costs little in money — at size L, from under a
cent (small model, to-check band) to about 2.8 (large model, kept and
to-check bands with usage lines) — but a handoff of the kept and to-check
bands at size L (274,053 input tokens, 962,958 with usage lines) is too
large for one assistant conversation and would have to be split; the
to-check band alone (45,082 tokens) fits.

### The cost of the real comparison

Running every route, scope and corpus above with a real model would take
about 11.7 million input and 3.9 million output tokens: about 2.35 with a
small model and 47 with a large one at the illustrative prices. A focused
comparison — API against handoff with usage lines, for the kept-and-to-check
and to-check scopes, on demo L, demo L trilingual and the four benchmarks —
takes about 5.2 million input and 1.1 million output tokens: about 0.85
small, 17 large. Repeating it three times to see the judge's own variance
triples that. A browser handoff has no per-token price on a subscription,
but needs a person to paste the bundle in parts and bring the answers back.
The harness is ready: a real judge only has to implement `handoff.Judge`.

### The browser-handoff test (retired)

A blind test of the handoff (bundles written for a chat assistant, answers
pasted back and scored against the demo world's truth) guided the prompt's
versions 2 and 3: a phrase joining a process, a property or a measure to an
object of the field is a keyword and F is kept for broken pieces (with the
first prompt, a blind judge rejected most French « X des Y » terms as
fragments); a single everyday word is G unless it is a term of art; a French
or Portuguese term takes as its English form the English term of the list that
names the same thing. The application's route for an assistant is now the
copilot ({doc}`copilot`); its tools were removed with the handoff.

## Word boundaries and elision

French writes some words elided with an apostrophe (`l'`, `d'`, `qu'` …),
Portuguese `d'` (`d'água`). Without the AI clean-up, the consolidation's
nested filter drops most set-aside candidates anyway; the few it keeps are
French candidates written after an elided article or preposition (`eau
douce` in `apports d'eau douce`, `information géographique`, `apprentissage
profond`), which is what setting the whole band aside removes. The question
was whether the rules cut words at the elision the way the extraction does,
and what field terms it costs. The study used the lab's corpora, parse and
scoring, and the consolidation of the theme recovery, run with the oracle's
answers and without any answer (the route without the AI clean-up, which
reads every candidate); its variants were not kept as switches.

**What sees the elision.** The extraction's units split the elided word off
(the French model does it), and the part-of rule works on units: a phrase
after `l'` is set aside exactly when its twin after `la ` is
(`tests/test_scoring.py`). The field terms the bands set aside are, with or
without an elision, phrases only ever seen inside one longer phrase: the
complement of a phrase with a generic head (`ostréiculture` in `histoire de
l'ostréiculture`, like `justice environnementale` in `histoire de la justice
environnementale` and `malacocultura` in `acesso da malacocultura`), or a
compound without its first word (`beach resilience` in `pocket beach
resilience`). Few of them follow an elision:

| corpus | field terms set aside | French | after an elision |
|---|---|---|---|
| demo S | 29 | 9 | 1 (`ostréiculture`) |
| demo S trilingual | 31 | 8 | 1 (`interface science-politique`) |
| demo S bodies | 54 | 27 | 1 (`observation participante`) |
| demo L | 18 | 11 | 1 (`analyse spectrale`, in `aide de l'analyse spectrale`) |
| demo L trilingual | 70 | 22 | 2 (`analyse spectrale`, `ostréiculture`) |

Two rules read the words between spaces instead, where the elided word goes
with the next one: the consolidation's nested filter (for it,
`information géographique` is not inside `systèmes d'information
géographique`, while `méthode statistique` is inside `choix de la méthode
statistique`), and the length bonus (`masse d'eau` counts two words). That
is why only elided fragments survived the consolidation without the AI
clean-up. The Portuguese model, finally, keeps `d'água` as one noun, so
`coluna d'água` or `massas d'água` were never candidates; Portuguese
contractions (`do`, `na`, `pelo`) are single prepositions and behave.

**Measured.** *After* is the change adopted: an elided word the tokenizer
leaves attached is split off (`d'água`), and the project's rejections see the
elided word. *Same boundaries* also gives the nested filter and the length
bonus the extraction's words (not adopted). The measures are the lab's
(kept band, and the to-check terms the oracle accepts).

| corpus | variant | kept (gold) | to check (gold) | set aside (gold) | precision | recall | F1 | AUC | best 10 % |
|---|---|---|---|---|---|---|---|---|---|
| demo S | before, after | 1,961 (1,018) | 652 (4) | 1,370 (29) | 52.0 % | 84.6 % | 64.4 % | 0.525 | 25.1 % |
| demo S | same boundaries | 1,961 (1,018) | 652 (4) | 1,370 (29) | 52.0 % | 84.6 % | 64.4 % | 0.523 | 24.9 % |
| demo S trilingual | before, after | 2,218 (982) | 852 (6) | 1,591 (31) | 44.4 % | 80.5 % | 57.2 % | 0.555 | 25.3 % |
| demo S trilingual | same boundaries | 2,218 (982) | 852 (6) | 1,591 (31) | 44.4 % | 80.5 % | 57.2 % | 0.553 | 24.7 % |
| demo S bodies | before, after | 4,868 (2,467) | 875 (6) | 2,672 (54) | 50.7 % | 83.9 % | 63.2 % | 0.499 | 31.9 % |
| demo S bodies | same boundaries | 4,868 (2,467) | 875 (6) | 2,672 (54) | 50.7 % | 83.9 % | 63.2 % | 0.496 | 31.4 % |
| demo L | before, after | 9,527 (5,619) | 1,287 (9) | 4,482 (18) | 59.0 % | 85.6 % | 69.9 % | 0.516 | 28.0 % |
| demo L | same boundaries | 9,527 (5,619) | 1,287 (9) | 4,482 (18) | 59.0 % | 85.6 % | 69.9 % | 0.511 | 27.5 % |
| demo L trilingual | before | 12,349 (6,168) | 1,874 (12) | 5,300 (70) | 50.0 % | 80.4 % | 61.7 % | 0.520 | 24.1 % |
| demo L trilingual | after | 12,350 (6,168) | 1,874 (12) | 5,298 (70) | 50.0 % | 80.4 % | 61.7 % | 0.520 | 24.1 % |
| demo L trilingual | same boundaries | 12,350 (6,168) | 1,874 (12) | 5,298 (70) | 50.0 % | 80.4 % | 61.7 % | 0.518 | 24.2 % |

The consolidated lexicon, as the reference-language concepts it holds:

| corpus | variant | route | terms | precision | recall |
|---|---|---|---|---|---|
| demo S | before, after | oracle's answers | 1,631 | 57.8 % | 84.8 % |
| demo S | same boundaries | oracle's answers | 1,630 | 57.8 % | 84.8 % |
| demo S | before, after | no answers | 1,820 | 55.1 % | 85.1 % |
| demo S | same boundaries | no answers | 1,804 | 55.5 % | 85.1 % |
| demo S bodies | before, after | oracle's answers | 3,975 | 52.7 % | 88.0 % |
| demo S bodies | same boundaries | oracle's answers | 3,965 | 52.8 % | 87.9 % |
| demo S bodies | before, after | no answers | 4,436 | 54.5 % | 88.5 % |
| demo S bodies | same boundaries | no answers | 4,394 | 54.8 % | 88.4 % |
| demo L | before, after | oracle's answers | 7,632 | 58.5 % | 94.9 % |
| demo L | same boundaries | oracle's answers | 7,623 | 58.6 % | 94.8 % |
| demo L | before, after | no answers | 8,865 | 62.0 % | 95.0 % |
| demo L | same boundaries | no answers | 8,819 | 62.3 % | 95.0 % |
| demo L trilingual | before, after | oracle's answers | 9,613 | 45.0 % | 93.4 % |
| demo L trilingual | same boundaries | oracle's answers | 9,605 | 45.0 % | 93.3 % |
| demo L trilingual | before, after | no answers | 11,594 | 52.0 % | 93.9 % |
| demo L trilingual | same boundaries | no answers | 11,549 | 52.2 % | 93.9 % |

(Recall here is over the concepts of the reachable field terms, each
counted once whatever its language; without the nested filter, the oracle's
lexicon reaches 86.4 % on demo S and 95.9 % on demo L, at 50.4 % and
53.6 % precision.)

- **The change** moves no English or French candidate: the French model
  left no elided word attached in the demo texts. In Portuguese the demo
  writes `d'água` in one phrase only at size L (`massas d'água frias`, ten
  people): `d'água frias de fundo` becomes `água frias de fundo`, since the
  model tags `massas` as an adjective there, so no field term is gained on
  the demo worlds; hand-written sentences (`coluna d'água`, `lâmina d’água`,
  `níveis d'água extremos`) are tested with the real model.
- **Same boundaries** moves no band. The length bonus lowers the ranking
  measures (demo L: AUC 0.516 → 0.511, best tenth 28.0 % → 27.5 %), and the
  nested filter removes, without the AI clean-up, exactly those elided
  fragments (`eau douce`, `océan`, `éolien`, `information géographique` …),
  but also field terms (`apprentissage automatique`, `ostréiculture`,
  `analyse spectrale` at size L), and, with the oracle's answers, field terms
  accepted inside a longer accepted phrase (`apprentissage profond`,
  `hypoxie côtière`, `analyse documentaire` in demo S bodies): a few tenths
  of a point of precision for a loss of recall. Not adopted.

**A complement is not a fragment (an option).** What does set field terms
aside is the complement: a phrase that follows a preposition (and its
article, elided or not) in its only longer phrase. Sending such a phrase to
check instead of setting it aside was measured too; it is a change of the
band rules, left to the owner:

| corpus | variant | AI load | precision | recall | lexicon, oracle's answers | lexicon, no answers |
|---|---|---|---|---|---|---|
| demo S | today | 652 | 52.0 % | 84.6 % | 57.8 %, 84.8 % | 55.1 %, 85.1 % |
| demo S | complement to check | 754 | 52.2 % | 85.1 % | 57.7 %, 84.7 % | 55.1 %, 85.1 % |
| demo S | and in the nested filter | 754 | 52.2 % | 85.1 % | 56.9 %, 85.5 % | 51.0 %, 85.7 % |
| demo L | today | 1,287 | 59.0 % | 85.6 % | 58.5 %, 94.9 % | 62.0 %, 95.0 % |
| demo L | complement to check | 1,427 | 59.1 % | 85.7 % | 58.5 %, 94.9 % | 62.0 %, 95.0 % |
| demo L | and in the nested filter | 1,427 | 59.1 % | 85.7 % | 57.8 %, 95.0 % | 60.1 %, 95.1 % |
| demo L trilingual | today | 1,874 | 50.0 % | 80.4 % | 45.0 %, 93.4 % | 52.0 %, 93.9 % |
| demo L trilingual | complement to check | 2,182 | 50.2 % | 80.9 % | 45.0 %, 93.3 % | 52.0 %, 93.9 % |
| demo L trilingual | and in the nested filter | 2,182 | 50.2 % | 80.9 % | 44.3 %, 93.7 % | 49.7 %, 94.1 % |

(precision and recall of the consolidated lexicon in the last two columns).
The bands alone find a few more field terms (demo L trilingual: 36 fewer
field terms set aside, recall 80.4 % → 80.9 %) for 11–16 % more terms to
check, but the nested filter then removes them from the lexicon; letting it
keep a complement as well gains 0.1–0.7 points of recall for 0.7–0.8 points of
precision with the oracle's answers, and 2–4 points of precision without
the AI clean-up. Not adopted: a change of the band rules and of the nested
filter for the owner to weigh.

## Stop words and evenly spread words

A field report: with English and French texts and no AI clean-up, little
junk in English but much in French. Two mechanisms, both reproduced on the
trilingual worlds with misdetected French texts (above):

- French text in the English stream: the English tagger takes `des`, `de`,
  `la`, `LE`, `UN` for nouns, so they are single-word candidates to check
  (which reach the lexicon without AI), and French phrasing becomes kept
  English phrases (`dans les`, `ces résultats indiquent que la`). On demo L,
  1,047 kept or to-check English candidates held a French closed word.
- Generic words of scientific writing (`étude`, `objectif`, `résultats`,
  `approche`, `resultados`, `study`, `objective`) are single words to check,
  used by many people.

The rules (`docs/dev/extraction.md`, *Text in another language, and stop
words* and *Evenly spread single words*):

- **Stop words.** A paragraph whose phrases hold two different closed words
  of another language is read as that language: there those words cut
  phrases and are set aside (`stop-word`); a single word among the stream
  language's stop words (spaCy's list, the function and closed words) is set
  aside; a phrase starting or ending with another language's closed word is
  set aside (`stop-word-edge`). spaCy's lists judge single words of their own
  language only: they hold content words (`sistema`, `nível`, `front`,
  `bottom`, and in the French list `car`, `bat`).
- **Even spread.** Chosen over a hand list of generic words, which would be
  a keyword list in reverse and differ by field. The measure is the number
  of people who use a single word over the number its occurrences would
  reach if scattered at random over the texts, in proportion to each
  person's volume: about 1 for a word used like any other, well below 1 for
  a word gathered in some people's texts. Rare words are spread like random
  ones by construction, hence the floor on the people who use it. On the
  demo worlds, sorting the single words to check by kind (template phrasing
  of the texts, words of one or two themes' terms, other): at a fifth of
  the people and 90 % of random, the rule takes 86 of 161 template words on
  S and 94 of 175 on L, and no word of a theme's terms; the other words it
  takes are generic heads shared by many terms (`processes`, `dados`,
  `changements`, `observações`). The same measure over organisations
  instead of people separated the kinds less well. A person-level measure
  does not see words gathered in a group of people who each use them once:
  the floor is what protects them.

Measured on the trilingual worlds with misdetected texts (`--corpora "demo S
trilingual misdetected,demo L trilingual misdetected"`); *no AI* is the
lexicon the consolidation takes without the AI clean-up (kept and to-check
bands), *final* the lab's oracle judge on the same bands:

| corpus | rules | to check | no AI: lexicon | no AI: precision | no AI: recall | final precision | English candidates with a French closed word | gold lost |
|---|---|---|---|---|---|---|---|---|
| demo S | before | 932 | 3,257 | 29.8 % | 80.6 % | 41.7 % | 141 | – |
| demo S | stop words | 935 | 3,136 | 31.0 % | 80.6 % | 44.0 % | 4 | 0 |
| demo S | stop words and even spread (default) | 766 | 2,967 | 32.8 % | 80.6 % | 44.0 % | 2 | 0 |
| demo L | before | 2,152 | 15,403 | 39.6 % | 80.8 % | 45.9 % | 1,047 | – |
| demo L | stop words | 2,226 | 14,494 | 42.1 % | 80.8 % | 49.6 % | 18 | 0 |
| demo L | stop words and even spread (default) | 2,075 | 14,343 | 42.5 % | 80.8 % | 49.6 % | 18 | 0 |

No reachable gold term leaves the lexicon (recall unchanged); with every
band rule on, the stop-word and even-spread reasons catch 62 and 164
candidates on S, 118 and 151 on L, none of them gold. The even-spread
thresholds barely matter on L (80 % of random: 14,315 terms; as random:
14,408; a tenth of the people: 14,330, all at 80.8 % recall); on S a tenth
of the people loses 0.3 point of recall (three single-word gold terms:
`meta-analysis` in two languages, `télédétection`), so the floor stays at a
fifth. The theme ARI moves within its usual range between
variants (0.17–0.23 on L); the person mixes do not move.

## Time and memory

| corpus | words | parsing, first run (s) | name recognition (s) | scoring, one variant (s) | whole corpus in the lab (s) |
|---|---|---|---|---|---|
| demo S | 46,036 | (cached) | 0.4 | 0.4 | 73 |
| demo S bodies | 211,917 | (cached) | – | 1.6 | 224 |
| demo L | 435,872 | 19.7 | 47.5 | 2.9 | 414 |
| demo L trilingual | 438,880 | 21.6 | – | 3.0 | 482 |
| demo L bodies | 1,965,440 | 37.1 | – | 11.5 | 860 |
| inspec | 120,631 | 7.0 | 14.1 | 0.6 | 33 |
| termith | 53,817 | 8.8 | 9.0 | 0.2 | 13 |
| semeval | 1,694,500 | 40.5 | 195.4 | 6.3 | 341 |
| scielo | 204,362 | 7.8 | 21.7 | 1.0 | 53 |

The whole full suite takes 41.6 minutes with the parse cache filled (52.9
minutes the first time, with name recognition), with a peak of 1.2 GB (1.34
GB with name recognition). Scoring one variant takes under 12 seconds even
on 2 million words: the choices themselves cost nothing; parsing and, when
on, name recognition dominate. The quick suite takes about 3 minutes.

## Recommended defaults

1. **English phrases take no `of` complement** — precision and recall up on
   every demo world, neutral or better on the English benchmarks, a more
   stable lexicon (applied).
2. **People as the counting unit** — the presets give the same lexicon; none
   ranks clearly better on every corpus (unchanged).
3. **Raw frequency as the text vote** — the best ranking on the benchmarks
   (unchanged).
4. **Equal text-part weights** — no consistent gain from weighing the body
   down (unchanged).
5. **Length bonus α = 2** — the most gold among the best-scored candidates on
   eight corpora of nine (unchanged).
6. **No name recognition** — it costs recall and time, and what it catches
   is gold about as often as any candidate (unchanged).
7. **Three bands without a tuned share** — kept: a multi-word phrase; to
   check: a single word; set aside: a phrase never seen outside one longer
   phrase (applied; the common-modifier rule is off since G2), a stop word,
   another language's closed word at a phrase's edge, and an evenly spread
   single word (applied, see [stop words](#stop-words-and-evenly-spread-words)).
8. **The AI judges the kept and to-check bands** — left unjudged, the kept
   band is 41–56 % study settings and template phrasing on the demo worlds;
   the set-aside band holds almost no gold (applied at G2).

## Choices recommended to drop

These stay switches of `ScoringOptions` and `BandRules` for the lab, and
never become settings: the `of` complement; presence and sublinear votes;
text-part weights; name recognition; the low-score rule; a keep share below
100 %; a part-of share below 100 %; the common-modifier rule.

## Decided at gate G2

The owner's words: « Quality first. Check everything that reaches the
lexicon — kept and to check ok. » The recommendations are approved:

- the common-modifier rule is off by default (a lab switch only);
- the AI clean-up (`keywords.triage`, stage version 2) judges the kept and
  to-check bands, never the set-aside band; the handoff bundles hold the same
  two bands;
- the engine's triage prompt follows the handoff prompt (version 3): F only
  for broken pieces, a process, property or measure of an object is a
  keyword, a single everyday word is G unless it is a term of art, and a term
  of another language takes the English form of the English term that names
  the same thing;
- no paid API use for now: the handoff is tested with a blind judge instead
  (a test since retired with the handoff).

Decided later by the owner: without the AI clean-up too, the set-aside band
does not reach the lexicon (the consolidation's band gate, `keywords.build`
stage version 2); an explicit keep still wins. The lab's final lexicon
already left that band out, and the lab always consolidates with a judge's
answers, so none of its numbers move.
