# JSON Schemas

Each JSON file of a project has a JSON Schema (draft 2020-12). The schemas are
generated from the models in `cartolex.project.models`, shipped with the
package in `cartolex/project/schemas/`, and never edited by hand:

```text
python -m cartolex.project.schemas --write   # regenerate after a model change
python -m cartolex.project.schemas --check   # fail when a stored schema is out of date
```

| file | schema | format id |
| --- | --- | --- |
| `project.json` | `project.schema.json` | `cartolex-project/1` |
| `decisions/params.json` | `params.schema.json` | `cartolex-params/1` |
| `derived/<stage>/run.json` | `run.schema.json` | `cartolex-run/1` |
| `decisions/themes.json` | `themes.schema.json` | `cartolex-themes/1` |
| `decisions/maps.json` | `maps.schema.json` | `cartolex-maps/1` |
| `decisions/stopwords.json` | `stopwords.schema.json` | `cartolex-stopwords/1` |

A schema checks a file's shape. Some rules need the whole file, and cartolex
checks them when it reads it: a theme tree has no cycle and places keywords
only under its deepest level, a pinned map version exists, ids are unique.

The tables are described in {doc}`sources` (Parquet) and {doc}`decisions` (CSV);
their column types are fixed in `cartolex.project.tables`.

## `project.json`

```{literalinclude} ../../cartolex/project/schemas/project.schema.json
:language: json
```

## `run.json`

```{literalinclude} ../../cartolex/project/schemas/run.schema.json
:language: json
```

## `themes.json`

```{literalinclude} ../../cartolex/project/schemas/themes.schema.json
:language: json
```
