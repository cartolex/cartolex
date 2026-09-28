# Privacy and personal data

A cartolex project holds personal data: the names of the people it maps, their
identifiers, their affiliations and their texts. This page says what leaves
your computer while cartolex collects, what never does, what is kept and where,
and how to delete it. It describes how the software behaves; it is not legal
advice.

## What leaves your computer

Only collection from bibliographic services reaches the network (and, if you
switch it on, the AI clean-up of keywords, which sends keyword strings only;
see {doc}`build`). Importing a list, a folder of documents or a corpus sends
nothing.

| step | host | why | what is sent |
| --- | --- | --- | --- |
| resolution | `api.openalex.org` | find each person's author records | the person's name and its variants, the names of the institutions the list states, the ORCID or OpenAlex id the list gives |
| resolution | `pub.orcid.org` | count the works a person declared, to tell records apart | ORCID iDs |
| harvest | `api.openalex.org` | fetch the works of the confirmed records | author identifiers, DOIs |
| harvest | `pub.orcid.org` | read the works and employments a person declared | ORCID iDs |

With every request go, as with any web request, your computer's network
address, and the program's name (`cartolex`). If you give a contact address
(`--contact`), it goes to the services in the request headers and, for
OpenAlex, as a parameter; if you give an API key, it goes to its service only.

**Before every collection**, cartolex prints this summary for the planned
work: the hosts, why, the kinds of data, the number of requests and, where a
service charges, the estimated cost (`cartolex collect resolve … --dry-run`,
`cartolex collect harvest … --dry-run`; in Python,
`cartolex.collect.privacy.plan_collection`). After a collection, the job's
record in `logs/jobs/` names every host contacted and the kinds of data sent,
never a name or an identifier.

## What never leaves your computer

- your texts, titles and abstracts, imported or collected;
- your decisions: roles, identities, merges, keywords, themes;
- the columns of your list other than the names, identifiers and institution
  names that are searched;
- e-mail addresses: an e-mail column is refused at import and never stored.

## What is kept, and where

Everything stays in the project folder:

| where | what |
| --- | --- |
| `sources/tables/` | people, organisations, affiliations, texts, their parts, authorships |
| `sources/<slot>/raw/` | the imported list (without e-mail addresses), the documents' text, the service records as received, and the id registry; the tables are rebuilt from them |
| `cache/http/` | the services' answers, each with its lifetime (a person search a few days, a work by DOI three months) |
| `decisions/` | what people decided, with every earlier version in `decisions/history/` |
| `logs/jobs/` | what each job did: counts, times, hosts and kinds of data |

Nothing is kept anywhere else: cartolex has no server and sends no usage data.

## Deleting

- **The services' answers**: delete `cache/http/`. The next collection fetches
  again.
- **Everything collected**: delete `sources/<slot>/raw/` for the slot and the
  tables in `sources/tables/`; the project keeps its decisions.
- **The whole project**: delete its folder. Copies you made (backups, shared
  bundles, exported sites) are yours to delete too.
- **One person**: set their role to `excluded` so that nothing uses them. Erasing
  every trace of one person means removing them from the list you import,
  deleting the raw runs and the cache, and collecting again; cartolex 1.0 has no
  single command for it yet.

## The legal frame

cartolex is a tool you run. Whoever decides to map a field with it — you, or
the organisation you work for — decides why and how people's data are used,
and is responsible for it under the law that applies, such as the European
Union's General Data Protection Regulation (GDPR) or Brazil's Lei Geral de
Proteção de Dados (LGPD). In particular:

- **Lawful basis.** Choosing and documenting the lawful basis of the processing
  (for example a task in the public interest, or a legitimate interest weighed
  against the people's interests) is yours. cartolex does not choose it.
- **Public data, processed locally.** cartolex reads bibliographic records that
  the services publish openly, and processes them on your computer.
- **Data minimisation.** Only what the mapping needs is sent and kept: names,
  identifiers and institution names for the searches; e-mail addresses are
  refused; logs hold no names.
- **Transparency and rights.** People mapped have rights (to be informed, to
  access, to rectify, to object, to erasure). Everything cartolex holds about a
  person is in readable files of the project folder, so you can answer them.
- **Retention.** Data are kept as long as the project folder is; deleting it
  deletes them.
- **The services.** OpenAlex and ORCID receive the requests listed above and
  apply their own terms and privacy policies, possibly outside your country.
