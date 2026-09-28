---
orphan: true
---

# Open archives, journals and full texts

Besides the bibliographic index, cartolex can collect from an open archive
(HAL) and a journal platform (SciELO), and improve the texts it found with
open-access services. This page says what each brings, what it needs from
you, and what it sends.

## Who is found, and how

| service | a person is found by | a name alone |
| --- | --- | --- |
| HAL | their **idHAL** (a column of the people list) | proposes the author forms HAL has, with their idHAL when there is one |
| SciELO | their **ORCID**, when the journal printed it on the article | proposes the articles where the name appears |

A name never adds a text by itself: a proposal waits for you to confirm it
(give the person the idHAL, or the ORCID). Both services are read over the
project's window of years. SciELO cannot be searched by author: give it the
journals to read (their ISSNs), or it reads a whole collection, up to 5,000
articles.

From HAL, each deposit brings its titles and abstracts in every language it
has, its DOI, its type and year, and the structures (teams, labs,
institutions, with the ones above them) its authors stated. From SciELO, each
article brings its titles and abstracts in every language (often Portuguese,
Spanish, English, French); the keywords its authors chose are not read,
since the keywords come from the texts themselves.

## One work, one text

The same work often comes from several services. cartolex joins them into one
text when they share a DOI, a service's identifier, or, for the same person,
the same title with years at most one apart. When two services disagree (a
year, a title), the value of the more reliable one is kept and the other is
listed; nothing is replaced silently. A **preprint** and the article it
became stay two texts, linked: the map reads the article only, so a work
counts once. Every join is listed, with the rule that made it, in
`sources/merges.json`.

## Better texts

**Missing abstracts** are looked for, in this order, in SciELO, HAL, Europe
PMC, bioRxiv and medRxiv, arXiv and OpenAlex.

**Full texts** are fetched only when you ask for them: first a structured text
(the JATS of Europe PMC, bioRxiv, medRxiv and SciELO, the LaTeX source of
arXiv), then a PDF (HAL's file, or the open-access copy OpenAlex knows). A file
that cannot be read is skipped on its own. By default the keywords are built
from titles and abstracts; whether full texts count, and how much, is a
setting of the build.

Full texts **stay on your computer**: they are never part of a shared map, a
site, an export or anything cartolex sends.

## What is sent, to whom

| service | receives | pace |
| --- | --- | --- |
| HAL | idHALs, names (for proposals), HAL identifiers, DOIs | 2 requests a second |
| SciELO | journal ISSNs, article identifiers | 1 a second |
| Europe PMC | DOIs, then Europe PMC identifiers | 1 a second |
| bioRxiv, medRxiv | DOIs of preprints | 1 a second |
| arXiv | arXiv identifiers | 1 every 3 seconds, as arXiv asks |
| OpenAlex, open-access hosts | OpenAlex identifiers or DOIs, then the address of the open copy | as for OpenAlex; 1 a second per host |

No text leaves the computer: only identifiers, DOIs, journal ISSNs and, for
proposals, people's names. The contact address you give goes with each
request, as the services ask. Every host contacted and the kinds of data sent
are recorded for the privacy summary.
