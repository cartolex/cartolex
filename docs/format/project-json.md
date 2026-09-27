# `project.json`

`project.json` sits at the project's root and says what the project is. It is the
marker a tool looks for: a folder is a cartolex project when it holds a
`project.json` whose `format` starts with `cartolex-project/`.

```json
{
  "format": "cartolex-project/1",
  "name": "Coastal systems map",
  "identity": {
    "domain_title": "Coastal and marine systems",
    "domain_description": "Research on coastal and marine systems: physical processes, ecology, hazards and management. Out of scope: deep-ocean geology.",
    "frozen": true,
    "ai": {"provider": "mistral", "model": "mistral-small-latest"},
    "language_models": {"en": "en_core_web_md@3.8.0", "fr": "fr_core_news_md@3.8.0"}
  },
  "languages": {"corpus": ["en", "fr"], "reference": "en", "display": ["en", "fr"]},
  "levels": [
    {"id": "lab", "names": {"en": "Lab", "fr": "Laboratoire", "pt": "Laboratório"}},
    {"id": "university", "names": {"en": "University", "fr": "Université", "pt": "Universidade"}}
  ],
  "slots": [
    {"id": "collected", "kind": "collection", "fit": true, "trajectory": true},
    {"id": "reports", "kind": "folder", "fit": true, "trajectory": false, "doc_types": ["report"]}
  ],
  "overlays": [
    {"id": "applicants", "root": "../applicants-2027", "trajectory": false}
  ],
  "bases": [],
  "created": {"at": "2026-09-28T10:00:00Z", "by": "cartolex 1.0.0"},
  "app": {"id": "cartolex", "version": "1.0.0"}
}
```

## Keys

| key | what it holds |
| --- | --- |
| `format` | `cartolex-project/1` |
| `name` | the project's name, shown everywhere |
| `identity` | what AI answers and parse caches are keyed on (below) |
| `languages.corpus` | the languages extracted from texts, any of `en`, `fr`, `pt`; a text in another language is left out, and counted |
| `languages.reference` | the language that merges the forms of one keyword across languages (English by default) |
| `languages.display` | the languages keywords and themes are shown in |
| `levels` | the organisation levels, from the smallest to the largest, each with a name per interface language |
| `slots` | the corpus slots, in order (below) |
| `overlays` | the projected sets: people placed on the finished map, never shaping it |
| `bases` | other projects' maps this project can be placed on (below) |
| `created`, `app` | who made the project, and which application last wrote this file |

## Identity, and when it freezes

`identity` holds the strings that caches depend on:

- `domain_title` and `domain_description` are the only context the AI receives
  besides the terms themselves. The interface says so where you type them.
- `ai` is the provider and model used for AI filtering by API.
- `language_models` names each language's spaCy model and version; parsed texts
  are cached under that name.

When the first texts are processed, `frozen` becomes `true`. Changing a frozen
value afterwards is possible, as an explicit action that says what it costs: a
new domain title or AI model means cached AI answers are no longer reused, and a
new language model means texts are parsed again.

## Slots

A slot is one way texts enter the project: a collection from bibliographic
services, a folder of documents, an imported corpus. Slots are **ordered**, and
the order is an input: it fixes the order in which a person's texts are read,
hence the rows of every matrix. Slots are declared, never discovered.

| key | meaning |
| --- | --- |
| `id` | the slot's name, also the name of its folder in `sources/` |
| `kind` | `collection`, `folder` or `corpus` |
| `fit` | whether its texts build the lexicon and the map |
| `trajectory` | whether its texts are used for changes over time |
| `doc_types` | optional: only these document types are read |

## Overlays

An overlay is a projected set: people placed on the finished map by their texts,
who never shape it. Its `root` is a folder, relative to the project or
absolute, laid out like `sources/`; it may live outside the project, where a host
application keeps it. An overlay is never a fit slot.

## Bases

A base is another project's map this project can be placed on, instead of, or
beside, a map of its own: a small team placed on an institution's map. It names
the other project's map bundle and the map version it was taken from:

```json
{"id": "institute", "bundle": "../institute/outputs/bundles/v3.cartolex-bundle.zip", "map_version": "v3"}
```

The bundle is copied into `sources/bases/<id>/` when the base is added, so the
project stays complete when moved.
