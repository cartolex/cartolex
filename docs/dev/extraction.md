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

### Text in another language, and stop words

A paragraph reaches one language's stream by language detection, which a
mixed paragraph or a title in capitals can fool; the stream's tagger then
takes the other language's articles and prepositions for nouns (`des`,
`la`, `LE`, `UN` as English nouns). Three rules of the scoring deal with it
(`noun_phrases.spans` and `scoring.BandRules.stop_words`); none changes what
a parse records, so the parse cache stays valid:

- **Closed words.** `cartolex/_data/stopwords/closed_words.json` lists, for
  English, French, Portuguese and Spanish, the articles, prepositions,
  conjunctions, pronouns and forms of *to be* and *to have*. A stream's
  *foreign words* (`noun_phrases.foreign_words`) are the closed words of
  every other language, less its own closed words, function words and
  pattern prepositions and articles (`de` is French, Portuguese and
  Spanish). Only a word written in lower case or in capitals counts
  (`noun_phrases.closed_form`): a capitalised one begins a name (`La Niña`,
  `El Niño`); a word the tokenizer left whole after an elision counts as the
  elided word (`qu'une` is `qu'`). The lists leave out words that are
  content words or chemical symbols in another of the languages (`car`,
  `son`, `os`, `an`, `au`, `ni`, `se`, `el`, `sem`, `tem`).
- **Foreign reading.** A paragraph whose word units of phrases hold at least
  `FOREIGN_READING` (2) different foreign words is read as another
  language's text: each foreign word cuts the phrase it is in, and is an
  occurrence of its own, of class `F`, when it alone matches the pattern.
  One foreign word is not enough: `de Vries`, `La Niña` or `El Niño` in an
  English paragraph change nothing.
- **Bands.** A single-word candidate is set aside as `stop-word` when it is
  mostly of class `F`, or when its key or shown form is among the stream
  language's stop words (`noun_phrases.stop_words`: spaCy's list for the
  language, with the function and closed words). spaCy's lists hold words
  that are content words inside a phrase (`nível do mar`, `bottom water`,
  `front de mer`), so they only judge single words, and only the stream's
  own language (the French list holds `car` and `bat`). A phrase that
  starts or ends with a foreign word is set aside as `stop-word-edge:
  <word>`, and a phrase set aside so never makes another a fragment
  (`part-of`). The stream's own closed words are not edges: its tagger
  decides them (`croissance des vers`).

### Evenly spread single words

Generic words of scientific writing (`study`, `approach`, `étude`,
`objectif`, `resultados`) are used by many people and spread over them like
any word; a word of the field gathers in the texts of the people who work
on its subject. The scoring measures it (`scoring._spread`): a single word
with `n` occurrences scattered at random over the texts would reach person
`i` with probability `1 − exp(−n·vᵢ)`, `vᵢ` being the person's share of all
candidate occurrences of the language. Its *spread* is the number of people
who use it over the sum of these probabilities: about 1 for a word used
like any other, well below for a gathered word. A single word used by at
least `even_people` (a fifth) of the people who have texts in the language,
with a spread of at least `even_spread` (0.9), is set aside as
`even-spread`. The floor keeps out rare words, whose spread says nothing: a
word used once by each of a few people is spread like a random one. The
thresholds come from the lexicon lab ({doc}`lexicon-lab`).

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
| `aside` | `stop-word` | one word: a stop word of the language, or a closed word of another language in text of that language (see [above](#text-in-another-language-and-stop-words)) |
| `aside` | `stop-word-edge: <word>` | a phrase that starts or ends with a closed word of another language (`LE LITTORAL` in English) |
| `aside` | `part-of: <term>` | every occurrence sits inside one and the same longer candidate (`vector machine` in `support vector machine`) |
| `aside` | `low-score` | the least specific `drop_share` of the candidates, by `score_len` (off) |
| `aside` | `name: person\|place` | mostly inside a recognised name of a person or a place (only when names are recognised; the engine does not recognise them) |
| `aside` | `even-spread` | one word used by at least a fifth of the people, spread over them like a random word (see [above](#evenly-spread-single-words)) |

The rules are checked in the order `stop-word`, `stop-word-edge`,
`part-of`, `name`, `low-score`, then `even-spread`, `single-word`,
`common-modifier`, `below-threshold`, `multiword`. With the defaults, a
candidate is kept (a phrase of two content words or more), to check (one
content word) or set aside (a stop word, a phrase with another language's
word at an edge, a fragment of a longer candidate, an evenly spread word).
After scoring, the candidates of the rejection snapshot
(`EnginePaths.rejects_json`, `cartolex.lexicon.rejects`: cartolex's list and
the machine's cache, see {doc}`../format/decisions`) go to a fourth band,
`rejected`, with the reason `rejected-list` or `rejected-earlier`; a candidate
matches when its shown form or one of its other forms is listed.
Only the kept and to-check bands can reach the lexicon without the AI
(`scoring.LEXICON_BANDS`); the set-aside and rejected bands stay in the raw
tables with their reason. The AI clean-up judges every band but the rejected
one (`scoring.AI_BANDS`), so that a candidate a rule set aside can be rescued;
with its decisions, the consolidation keeps only accepted terms (its
acceptance gate), so a rejected candidate reaches the lexicon only if the same
concept is accepted under another form. Once a copilot's triage is accepted
for the current extraction (a decision of `keywords.csv` from the copilot made
after the extraction ran), the same acceptance gate applies: only the terms
with an accepting decision (a keep, the AI's or a person's, and a merge's
target; `cartolex.build.engine.copilot_gate`, written as
`keywords.build/decisions/accepted.csv`) enter, and the keywords screen counts
the candidates nobody judged that it keeps out (« send them to the AI », « keep
them anyway »). Without the AI clean-up, the consolidation's
band gate (`consolidation.band_allowed_concepts`) keeps a concept only when
one of its raw terms is in the kept or to-check band, so the set-aside band
does not reach the lexicon either. Either way an explicit keep wins: the
manual keep list (`manual_keep.csv`; in a project, the `keep` rows of
`decisions/keywords.csv`). A raw table without a `band` column, from an
older run, is read whole. Both gates report what they remove in the run's
progress. The rules and their defaults come from the lexicon lab
({doc}`lexicon-lab`); the rules marked off are its switches, not settings.

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
<folder>/<model name>-<model version>/<pattern version>/analyses.sqlite
```

One SQLite table, `analyses (key TEXT PRIMARY KEY, data BLOB)`: the text's
sha256 and its analysis as compressed JSON (`{"runs": …, "lemmas": …}`). One
process writes at a time and any number read: the parsing workers look their
texts up while the run stores the analyses they parse. An analysis is stored
once and never changed. Another model version or pattern version lives in
another folder, is never read and can be deleted. The parts an earlier version
wrote (`part-<digest>.jsonl`: a header line `{"format": "cartolex-parse/1",
"model": …, "patterns": …}`, then one line per text) are read into the database
the first time it is opened for writing; a part whose header does not match,
or that cannot be read, is skipped with a warning.

## A text at a time, in worker processes

The extraction never holds the corpus (`cartolex/lexicon/extract_stream.py`).
It reads the window's pairs as compact arrays
(`cartolex.lexicon.corpus_store.load_corpus`), and each text once, however many
of its authors are in the project: a count the scoring weighs by person and
text counts the text once per author (its *weight*, the pairs that name it).
Four passes, each over blocks of texts (`TASK_TEXTS`) given to worker processes
(`cartolex.scale.ordered_map`, results given back in order):

1. **languages**: each text's paragraphs by language, and each language's words
   counted with the texts' weights (the healing's dictionary);
2. **parsing**, a language at a time: each text healed
   (`text_utils.heal_text`, the corpus's real words sent once to every worker),
   cut, and its pieces analysed from the parse cache or parsed; the words' lemmas
   counted with the weights (the corpus lemma table). Each parsing worker holds
   its language's model (about 0.9 GB, `PARSE_WORKER_MB`): their number is capped
   by the run's memory (`ThreadLimits.workers_within`);
3. **keys**: the candidates each text holds, a 64-bit hash each, summed with the
   texts' weights; a candidate the texts of fewer than `min_df` people could hold
   cannot enter the window and is not counted further (dropping it changes
   nothing);
4. **counts**: the other candidates' occurrences per text, their surface forms,
   classes and containers, gathered into `scoring.Aggregates`.

The analyses wait between passes in a scratch folder (`RunContext.scratch`, else
the stage's own folder), read front to back. `scoring.score_aggregates` scores the
counts: the same window, TF-IDF, evidence and bands as `score_units`, which keeps
its interface (the lexicon lab's texts in memory) on top of the same function.

## Parallel parsing and determinism

`KeywordsConfig.extraction_n_jobs` (capped by `RunContext.threads`; a project's
build sets both from the computer's budget, `cartolex.scale.Budget`) sets the
number of worker processes. Workers are fresh interpreters (never forks of the
running process), started only when there are several blocks of texts. The
analysis of a text does not depend on the block it is parsed in, and every count
is made in the texts' order, so the output is the same, byte for byte, whatever
the number of workers and whatever the cache holds (`tests/test_extraction.py`;
every stage of a project: `tests/test_build_workers.py`).

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
