# The lexicon lab

The lexicon lab (`tools/lexicon_lab/`) compares the open choices of the
keyword extraction on the demo worlds and on public benchmarks, so that the
defaults can be fixed on evidence. The rule it serves: **as few tuned
parameters as possible, defaults from simple rules, stability and simplicity
first** — a choice that does not clearly win is dropped, not kept as a knob.

```bash
python tools/lexicon_lab/run.py --suite quick            # demo worlds of size S, about 4 min
python tools/lexicon_lab/run.py --fetch --suite full     # L and the benchmarks, about an hour
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
around the current defaults (a *family*). The window is the engine's default:
a candidate is used by at least 3 people and at most 60 % of them.

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
| Jaccard, rank ρ | stability: the final lexicon, and the order of its scores, when 10 % of the texts are removed (mean of two draws) |
| ARI topics, ARI themes | on demo worlds, the engine's term groups (topics: concept clusters; themes: proto-subfields) against the true themes of the kept terms that have one |
| mix cosine | on demo worlds, each person's theme mix read from the lexical matrix, against their true mix |

The last two run the engine as a project does: consolidation (with the
judge's answers as triage decisions), SVD and term clustering.

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
