# The candidate extraction

The first stage of the keyword pipeline, `keywords.extract`
(`run_pipeline_stage_1`, `cartolex/lexicon/extract_raw.py`), turns the texts
of a corpus into a scored list of candidate terms per language. The
candidates are **noun phrases** found by part-of-speech patterns; their
scores are the TF-IDF scores the pipeline has always used, so the later
stages (AI triage, consolidation, the atlas) read the same tables as before.

## From texts to candidates

1. **People and languages.** Each person is one document: the texts of all
   their corpus documents, concatenated. Each paragraph is routed to a corpus
   language by language detection (`KeywordsConfig.corpus_languages`, any
   subset of English, French and Portuguese — another code is refused with a
   `SettingsError` when the settings are made); a paragraph in another
   language is dropped.
2. **Clean-up.** Words that PDF extraction split in two (`adh esion`) are
   rejoined, using the corpus as its own dictionary.
3. **Parsing.** Every paragraph is parsed by the language's model (see
   [the models](#the-models-and-their-licences)); a paragraph longer than
   10,000 characters is first cut at a line break or a sentence end.
4. **Word units and classes.** The tokens are grouped into word units — a
   word, or a compound joined by a hyphen or a slash without spaces
   (`sand-gravel`, `îles-barrières`) — and each unit gets a class: noun (`N`),
   proper noun (`R`), adjective or participle used as one (`A`), English gerund
   used as a noun (`G`), a preposition the pattern allows (`P`), a definite
   article after such a preposition (`D`), or anything else (`X`), which
   breaks a phrase. Punctuation, line breaks, numbers, web addresses,
   one-letter words and the language's function words (below) break a phrase.
   The parser's sentence boundaries are not used: inside a stretch without
   punctuation they are mostly errors (a boundary inside a hyphenated word).
   An elided word is a unit of its own, and the word after it starts a unit,
   as after a space (see [word boundaries](#word-boundaries-and-elision)).
5. **Patterns.** Every contiguous span of at most five units that fully
   matches the language's pattern is one occurrence of a candidate, nested
   spans included: `sediment transport model` also counts `sediment transport`,
   `transport model`, `sediment`, `transport` and `model`.

   | language | pattern | examples |
   | --- | --- | --- |
   | English | `(ADJ\|NOUN\|PROPN)* (NOUN\|PROPN\|gerund)` | `sea surface temperature`, `distributed systems`, `decision making` |
   | French | `NOUN ADJ* ((de\|du\|des\|d'\|à\|au\|aux) DET? (NOUN\|PROPN) ADJ*)?` | `trait de côte`, `masse d'eau`, `zone à risque`, `variabilité interannuelle du niveau marin` |
   | Portuguese | the French shape, with `de` (and its elided `d'`), `em`, `por`, `para`, `com`, `a` and their contractions (`do`, `da`, `dos`, `das`, `no`, `na`, `nos`, `nas`, `pelo`, `pela`, `pelos`, `pelas`, `ao`, `aos`, `à`, `às`) | `linha de costa`, `nível do mar`, `transporte pela corrente`, `coluna d'água` |

   An English `of` complement (`degrees of freedom`) is a switch of the
   lexicon lab, off: most English `X of Y` spans are phrasing (`role of
   silicic acid uptake`, `context of storm events`), and they made the terms
   inside them look like fragments of a longer phrase ({doc}`lexicon-lab`).

   The Portuguese tokenizer keeps a contraction as one token tagged as a
   preposition (`do` is `de` + `o`), so `nível do mar` is `N P N`; an
   uncontracted article after a preposition (`para a costa`) is the optional
   `D`. An article that does not follow a preposition never joins two nouns.
6. **Grouping.** Occurrences are grouped by a key: each content unit becomes
   the *corpus lemma* of its words (the lemma the corpus most often gives that
   word, so a word the tagger hesitates on stays in one group), a preposition
   its base form (`du`, `des`, `d'` → `de`; `pela` → `por`; `aux` → `à`), and
   articles are left out. `le trait de côte` and `les traits de côte`, or
   `tide gauge` and `tide gauges`, are one candidate. The term shown is the
   key's most frequent surface form, in lower case except proper nouns and
   words with inner capitals (`ADCP`, `Atlantic`).

The code is `cartolex/lexicon/noun_phrases.py`; the patterns and classes are
tested on hand-built parses in `tests/test_noun_phrases.py`, without any model.

### Word boundaries and elision

French writes some words elided before a vowel, with an apostrophe (straight
or typographic): the article `l'`, the preposition `d'`, and the pronouns and
conjunctions `qu'`, `j'`, `n'`, `s'`, `c'`, `m'`, `t'`; Portuguese elides
`de` the same way (`d'água`). An elided word is a word unit of its own, and
the word after it starts a unit, exactly as after a space:

- The French model splits the elided word off (`l'` + `apprentissage`). A
  token a model leaves whole — the Portuguese model keeps `d'água` as one
  noun — is split by the extraction into the elided word and the rest, with
  the rest's lemma; without that, `coluna d'água` was a noun followed by an
  unknown noun `d'água`, never a candidate.
- `d'` is the preposition `de` (its key part is `de`), `l'` an article (left
  out of keys); the other elided words are pronouns or conjunctions and
  break a phrase.
- A word with an apostrophe inside it stays one word (`aujourd'hui`,
  `presqu'île`, the compound `olho-d'água`), and Portuguese contractions
  (`do`, `na`, `pelo`, `às`) are words of their own, prepositions of the
  pattern: nothing is split off them.
- The shown form writes no space after an elided word (`masse d'eau`), and
  `cartolex.lexicon.text_utils.term_words` cuts a shown term back into the
  same words (`systèmes`, `d'`, `information`, `géographique`).

What sees these boundaries:

- **The part-of rule** works on the units themselves: a phrase after `l'`
  nests in a longer one exactly as a phrase after `la ` does.
  `apprentissage profond` sits in `choix de l'apprentissage profond` as
  `méthode statistique` sits in `choix de la méthode statistique`, and each
  is set aside only when it is never seen outside that one longer phrase
  (`tests/test_scoring.py`).
- **The project's rejections**: a rejected word blocks the terms it is a word
  of, after an elision too (`information` blocks `systèmes d'information
  géographique`).
- Two text-level rules read the words between spaces, where an elided word
  goes with the next one: the length bonus (`masse d'eau` counts two words,
  `trait de côte` three) and the consolidation's nested filter (it does not
  see `information géographique` inside `systèmes d'information
  géographique`). Giving them the extraction's boundaries was measured and
  left out: it moves no band and loses field terms (see
  [the lexicon lab](lexicon-lab.md#word-boundaries-and-elision)).

### Function words

Each language has a short list of function words that break a phrase
(`cartolex/_data/stopwords/function_words.json`): determiners, quantifiers,
pronouns and citation abbreviations a tagger may mark as adjectives or nouns
(`other`, `such`, `several`; `autres`, `plusieurs`, `nombreuses`; `outros`,
`vários`, `cada`; `et al.`). Verbs, adverbs, articles and conjunctions are
already outside the patterns. The lists hold no content word.

## Scores

The scoring (`cartolex/lexicon/scoring.py`) works on the analysed texts, each
with its person, its organisation (the index's `unit`) and its parts:

1. **Window.** A candidate is kept when at least `min_df` people use it
   (default 3) and at most `max_df` of them (default 60 %); the window always
   counts people.
2. **Counting unit** (`KeywordsConfig.counting_unit`, the build's
   `keywords.extract.counting_unit`). What one TF-IDF document is:
   `person` (default: a person's texts together, so each person weighs the
   same), `text` (each text weighs the same; a text two people wrote counts
   once) or `organisation` (each organisation weighs the same; a text counts
   once for an organisation, however many of its members wrote it). Each
   document's TF-IDF vector is L2-normalised, and a candidate's `score` is the
   sum over documents.
3. **Vote.** Inside a document, each text contributes its number of
   occurrences of the candidate. (Presence votes, a logarithmic vote and
   weights per text part exist as switches of the lexicon lab, see
   {doc}`lexicon-lab`; they are not settings.)
4. **Length bonus.** `score_len = score × (1 + length_bonus_alpha × (L − 1))`,
   `L` being the number of words of the term, prepositions and articles
   included, as written between spaces (an elided `d'` or `l'` goes with the
   word after it: `masse d'eau` has two).

With the defaults this is exactly the historical scoring (one document per
person, raw counts).

### Bands

Each kept candidate falls in one of three bands, with a reason code the
interface turns into words:

| band | reason | rule |
| --- | --- | --- |
| `kept` | `multiword` | a phrase of two content words or more (prepositions and articles do not count) |
| `check` | `single-word` | one content word |
| `check` | `common-modifier: <word>` | the adjective at the phrase's edge (first in English, last in French and Portuguese) appears in the candidates of at least `generic_spread` of the people (`recent approach`); off by default |
| `check` | `below-threshold` | a multi-word phrase outside the best `keep_share` of the candidates (off: every one is kept) |
| `aside` | `part-of: <term>` | every occurrence sits inside one and the same longer candidate (`vector machine` in `support vector machine`) |
| `aside` | `low-score` | the least specific `drop_share` of the candidates, by `score_len` (off) |
| `aside` | `name: person\|place` | mostly inside a recognised name of a person or a place (only when names are recognised; the engine does not recognise them) |

The rules are checked in the order `part-of`, `name`, `low-score`, then
`single-word`, `common-modifier`, `below-threshold`, `multiword`. With the
defaults, a candidate is kept (a phrase of two content words or more), to
check (one content word) or set aside (a fragment of a longer candidate).
The AI clean-up judges the kept and to-check bands and never sees the
set-aside band; with its decisions, the consolidation keeps only accepted
terms, so a set-aside candidate reaches the lexicon only if the same concept
is accepted under another form. Without the AI clean-up, bands remove
nothing: the consolidation reads every candidate. The rules and their
defaults come from the lexicon lab ({doc}`lexicon-lab`); the rules marked
off are its switches, not settings.

### The raw keyword tables

`raw_keywords_<lang>.csv` (`EnginePaths.raw_terms_csv`) has the columns
`term`, `score`, `len`, `score_len`, `forms` (every surface form of the
candidate, most frequent first, separated by `|`), `people` (how many people
use it), `texts` (how many distinct texts), `band` and `reason`, sorted by
`score_len` (ties by term). The merged list keeps, for a term found in two
languages, its best-scored row, with its band and reason.

Besides the patterns, two filters apply: a candidate whose shown form, or one
of its words, is among the project's own rejections (`manual_blacklist.csv`,
the rejected pairs of `canonical_decisions.json`) is left out, as is a
malformed string (a web address, encoding garbage).

A corpus language without any text is skipped with a warning and its table is
written empty; so is a language whose candidates never reach the
document-frequency window (a language with too few people, for example). A
run fails only when no corpus language has any text.

## What the later stages do with the candidates

- **AI triage.** The triage reads the merged list through a safety net that
  drops numbers and malformed strings only; its deterministic prefilter is
  unchanged.
- **Consolidation.** The attribution counts each accepted term in the
  people's texts with its own vectorizer, which lower-cases and keeps words of
  two letters or more. Every surface form of a candidate (the `forms` column)
  is counted, written the way that vectorizer reads it (`masse d'eau` →
  `masse eau`, `zones à risque` → `zones risque`), and the vectorizer's n-gram
  range is widened to the longest form. Tables without a `forms` column are
  read as before.

## The parse cache

Parsing is the slow part, and a corpus mostly grows by adding documents. The
analysis of each parsed text — the runs of classified word units the patterns
work on, and the lemma counts of its content words — is kept in the parse
cache (`cartolex/lexicon/parse_cache.py`), keyed by

- the sha256 of the text (after the clean-up above),
- the identity of the model that parsed it (`name@version`), and
- the pattern version (`cartolex.lexicon.noun_phrases.PATTERN_VERSION`,
  raised with any change to what an analysis records).

A later run parses only the texts it has not seen with the same model and
patterns. The caller chooses the folder: `EnginePaths.parse_cache_dir`
(`automatic_data/parse_cache/` in the workspace layout; the project format
puts it under `cache/parse/`). Layout and format:

```text
<folder>/<model name>-<model version>/<pattern version>/part-<digest>.jsonl
```

Each part is UTF-8 JSON lines: a header line
`{"format": "cartolex-parse/1", "model": "<name@version>", "patterns": "<version>"}`,
then one line per text, `{"sha256": …, "runs": …, "lemmas": …}`. A run writes
its new analyses into new parts of at most 1,000 texts, each written to a
temporary file and renamed into place, never modified afterwards; a part's
name is the digest of its content. A part whose header does not match, or
that cannot be read, is skipped with a warning and its texts are parsed
again. Another model version or pattern version lives in another folder, is
never read and can be deleted.

## Parallel parsing and determinism

`KeywordsConfig.extraction_n_jobs` (capped by `RunContext.threads`) sets the
number of worker processes for the language split and for parsing. Parsing
workers are fresh interpreters (never forks of the running process), each
with its own copy of the model, and take fixed batches of texts. The analysis
of a text does not depend on the batch it is parsed in, and candidates are
counted in a fixed order, so the output is the same, byte for byte, whatever
the number of workers and whatever the cache holds
(`tests/test_extraction.py`).

## The models and their licences

`cartolex/lexicon/language_models.py` pins one spaCy model per language:

| language | model | version | licence | wheel |
| --- | --- | --- | --- | --- |
| English | `en_core_web_md` | 3.8.0 | MIT | 33 MB |
| French | `fr_core_news_md` | 3.8.0 | LGPL-LR | 46 MB |
| Portuguese | `pt_core_news_md` | 3.8.0 | CC BY-SA 4.0 | 42 MB |

spaCy itself (`spacy>=3.8,<3.9`, MIT) is a dependency of cartolex. The models
are separate installs from the spaCy models' release wheels, pinned by sha256
in `tools/requirements-models.txt`; they carry their own licences and are
never bundled with cartolex or modified. The check installs all three into
every test environment. A user installs a model with
`python -m pip install "<name> @ <wheel address>#sha256=<hash>"` (the exact
command is in the error below; a `cartolex models add <language>` command
will do it).

A language with text whose pinned model is missing, or installed at another
version, stops the run before anything is parsed, with
`LanguageModelMissing` naming the language, the model and the install
command. There is no fallback to another model or to another extraction
method. A language without text needs no model.

The named-entity recogniser is not loaded. A model holds a few hundred MB in
memory: one is loaded at a time and released when its language is done.

## What the old stop lists did, and why they went

Until this version the extraction took every sequence of one to four words
(scikit-learn's n-grams) and filtered the result with packaged stop-word
lists: a base blacklist, administrative words, geographic terms, acronyms and
person names (a term was dropped if *any* of its words was listed), a
blacklist of single words, function words that may not start or end a term,
administrative and junk patterns, and a rule dropping any term with a word of
one or two letters unless it was a known scientific abbreviation. The triage
re-applied the same filters as a safety net.

That design had three problems:

- **Short words.** The one-or-two-letter rule broke every French term built
  with `de`, `du`, `à` (`trait de côte`) and nearly every multi-word
  Portuguese term (`linha de costa`): they reached the atlas as pieces
  (`trait`, `côte`) or as adjective phrases.
- **Real terms blocked.** The lists named common words that are real terms in
  some fields (an administrative word such as `recrutement` is a research
  object in the social sciences), and any term containing one of them
  disappeared.
- **Noise.** About half of the n-grams were not noun phrases (verbs, broken
  spans, a function word at an edge), and inflection or article variants were
  separate candidates, each one a separate AI call.

Noun-phrase patterns replace the lists: the grammar decides what a candidate
is, the function-word lists are short and hold no content word, and the
lexicon stays emergent — what the corpus says, judged by the triage and the
project's own decisions. On public keyphrase benchmarks of scientific
abstracts (one English, one French), a prototype of this extraction halved the
number of terms sent to the AI triage while reaching more of the reference
keyphrases than the n-gram extraction.
