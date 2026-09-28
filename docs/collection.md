# Collecting people and texts

A project maps the people of a field from their texts. Collection brings them
in: a list of people, then the records a bibliographic service holds for each
of them, then their works. Documents you already have come in without any
service. Everything collected lands in the project's source tables
({doc}`format/sources`); what you decide along the way (who is who, who is
the same person) lands in `decisions/people.csv`.

Before anything leaves your computer, cartolex says what will be sent, to
which host and why: see {doc}`privacy`.

## Ways in

### A list of people

```bash
cartolex collect people my-project people.csv --dry-run   # show how the columns are read
cartolex collect people my-project people.csv
cat names.txt | cartolex collect people my-project -       # a pasted list
```

A list is a CSV file or pasted text, separated by tabs, semicolons or commas,
with or without a header. cartolex proposes how to read each column:

| column | read as | header words it recognises (English, French, Portuguese, Spanish) |
| --- | --- | --- |
| a full name | the name, split at a comma (`Last, First`), at the words in capitals (`TAVELIN Ada`), or before the last word and its particles | `name`, `full name`, `nom complet`, `nome completo` |
| last and first names | the name | `last name`, `surname`, `family name`, `nom` (with `prénom`), `sobrenome`, `apellido`; `first name`, `given name`, `prénom`, `nombre` |
| identifiers | the person's ORCID (checked), OpenAlex id, HAL id; also found from the values alone | `orcid`, `openalex`, `idhal` |
| e-mail | **refused**: an address is never an identifier and is never stored | any header with `mail`, or a column of addresses |
| role | `mapped`, `context`, `projected` or `excluded` | `role` |
| projected set | the set a projected person belongs to | `set`, `overlay` |
| organisations | one level each: by the project's level ids and names, else lab-like words (`lab`, `team`, `unit`, `department`…) go to the smallest level and institution-like words (`institution`, `university`, `affiliation`…) to the largest | |
| anything else | a **filter**: kept as a person attribute, to colour or select people later | |

A list without a header is read from its values: one text column is a full
name, two are the last then the first name. `--dry-run` prints the proposal
and the mapping as JSON; edit it and give it back with `--mapping`.

Each new person gets a row in `decisions/people.csv` with the role the list
gives (`mapped` by default) and the identity `pending`. Importing the same list
again changes nothing; a person already decided keeps their decisions.

**Duplicates** are proposed, never merged: two rows that share an identifier,
whose names are the same once case, accents, hyphens and particles are set
aside, or whose first names agree (one may be an initial) with one surname
containing the other.

```bash
cartolex collect duplicates my-project
cartolex collect merge my-project p000012 p000031   # p000031 is p000012
```

A merge is recorded in `decisions/people.csv` (`merged_into`) and the other
name goes to the person's aliases, which searches try too.

### A folder of documents

```bash
cartolex collect folder my-project reports/ [--create-people]
```

PDF and text files, matched to people by a sub-folder per person
(`reports/Ada Tavelin/…`) or a name in the file name
(`tavelin-2021-report.pdf`). Each file is read on its own: a file that cannot
be read, that holds no text (a scan without a text layer), or that names nobody
or several people is reported with its reason, and the others come in. With
`--create-people`, a sub-folder that names nobody creates a person. A year in
the file name dates the text.

### An existing corpus

```bash
cartolex collect corpus my-project manual_index.csv
```

A corpus in the engine's contract (an index with `last_name`, `first_name`,
`unit`, `txt_path` and optionally `doc_year`, `doc_type`, and text files) comes
in whole: one person per name and unit, the unit as an organisation of the
project's first level, other columns as person attributes, and a text file
listed for several people as one text with several authors.

Folders and corpora bring whole documents: the build reads them when its
`corpus.assemble.parts` include `full`, which the import sets when you have
not set the parts yourself.

## Who is who: resolution

```bash
cartolex collect resolve my-project --dry-run    # what would be sent
cartolex collect resolve my-project --auto
```

For each person whose identity is pending, cartolex looks for their author
records in OpenAlex:

- every **variant of the name** is searched: as written, without accents, each
  half of a compound surname, without particles, with the first name as an
  initial;
- a search **restricted to the stated institutions** is added, on the full name
  only. The institution only **ranks** candidates, it never filters them: a
  lab-level affiliation often resolves to an institution record people never
  cite, and a filter would drop the right person and keep a homonym;
- an ORCID or OpenAlex id given in the list is looked up directly.

Each candidate is shown with its evidence: the name as recorded, its
institutions with years, its number of works, first and last year, top topics,
its ORCID, and how each piece of evidence counted in its score (the name up to
0.6, the same ORCID 0.4 or another one −0.5, an id given in the list 0.4, the
stated institution 0.25, an institution it belongs to 0.2, a similar name
0.15).

With `--auto`, a person is accepted automatically only when **a single
candidate** scores 0.8 or more (`--threshold`) and nothing contradicts it. The
identity is then `auto`: it stays marked for review. Everyone else waits
(`pending`) with their candidates.

**The registry separates people an index merged.** An index can merge two
people into one record and copy one person's ORCID onto every work of that
record, so filtering the index by ORCID does not separate them. When the
person or a likely record carries an ORCID, cartolex reads the works declared
in the ORCID registry and compares them with the record's, by DOI. When they
disagree (many of the record's works are not declared, or many declared works
are not on the record), both are shown with their counts and you decide.

Confirming records:

```bash
cartolex collect confirm my-project p000007 openalex:A123 openalex:A456   # two records
cartolex collect confirm my-project p000007 https://orcid.org/0000-…      # the registry
cartolex collect confirm my-project p000009 --none                        # no record exists
```

A person can have several records: list them all. « None » is a valid answer.
An OpenAlex id, an ORCID, or a URL holding one can be pasted.

## Collecting the works: the harvest

```bash
cartolex collect harvest my-project --dry-run
cartolex collect harvest my-project [--years 2012-2026] [--refresh | --cache-only]
```

For each confirmed person, the harvest collects every work of their OpenAlex
records and every work they declared in the registry (fetched from OpenAlex by
DOI), unites them, and removes duplicates by DOI, then by title and year. Each
work becomes a text with its title and abstract, the language of each detected;
its authorships name every project person on it at their rank, with the
institutions it states for them, which date their affiliations. Employments
declared in the registry date affiliations too.

Harvesting a person again replaces what the earlier harvest brought for them;
texts keep their ids. `--years` keeps a window of years (by default, every
year). Answers are cached in the project: `--refresh` fetches again,
`--cache-only` works offline from what was fetched before and says what is
missing. Ctrl-C stops after the current request: the people harvested before
it are kept.

## Trying it offline

The demo services answer like OpenAlex and the ORCID registry, on your own
computer, for a demo world ({doc}`demo`):

```bash
python -m cartolex.demo services --size S --people-list people.csv --list-only
cartolex init demo-project --name "Demo" --field "Coastal systems" --languages en,fr
cartolex collect people demo-project people.csv
cartolex collect resolve demo-project --services demo --world S:0 --auto
cartolex collect harvest demo-project --services demo --world S:0
cartolex build demo-project
```

`python -m cartolex.demo services` without `--list-only` keeps them running
and prints their address, for `--services http://127.0.0.1:PORT/…`.
