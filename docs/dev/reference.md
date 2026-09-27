# The numeric reference

The engine is being cleaned up and restructured in steps. Each step must leave
every output unchanged, or make each difference explicit. The *numeric
reference* is how that is proved: a stored run of the released engine on fixed
demo worlds, and a comparison that says, stage by stage, whether a new run is
**identical**, **within tolerance** or **different**.

## What is stored

`tests/reference/` holds three reference runs, made with the released engine
(the *baseline*):

| folder | world | what runs |
| --- | --- | --- |
| `S` | demo world, size S, seed 0 | every stage |
| `L` | demo world, size L, seed 0 | every stage |
| `merge` | size S, seeds 0 and 1 | both cohorts up to the bundle, then the map merge |

Each run is a folder in the `cartolex-reference/1` format: one folder per
stage, and a `manifest.json` that lists every artifact with the sha256 of its
canonical full-precision form, the stage list, the settings, the input worlds'
hashes and an environment record (Python, platform, library versions, engine
version and a fingerprint of the engine's source files, thread settings).

| stage | engine step | main artifacts |
| --- | --- | --- |
| `extract` | text extraction and scoring | candidate terms per language, merged list |
| `triage` | AI triage, answered by a deterministic fake model | decisions, the engine's two answer caches, call counts of a first and a cached second run |
| `build` | consolidation | refined terms, language pairs, per-person, per-group and domain terms, aliases, vectorizer vocabulary and IDF, run settings |
| `roster` | person roster | persons in roster order |
| `space` | SVD space | vocabulary and person order, the input matrix, singular values, person and term coordinates |
| `group` | term clustering | term labels, term scores, cluster table, proto-subfields |
| `layout` | 2-D layout | person and term coordinates, group aggregates, diagnostics |
| `draft` | subfield draft | the draft document |
| `apply` | the draft applied unchanged | applied document, person weights, lexicon weights |
| `trajectories` | per-period positions | points and windows |
| `projection` | new documents placed on the fitted map | per set: items, SVD coordinates, map coordinates, keywords and weights |
| `bundle` | portable bundle | meta, entity terms, entities, vocabulary, taxonomy, archive members, round-trip checks |
| `merge` | reconciliation, pooling, joint SVD, metrics | reconciliation tables, joint matrix, joint embedding, anchors, metrics |

The plots are drawn during every run to prove they do not fail; images are
never compared.

### Canonical forms

- **Tables** (CSV): full-precision floats (the shortest text that reads back
  to the same number), rows sorted by their key columns unless the row order
  is itself an output (the roster, for example); the manifest records the
  column types, the key columns and whether the order counts.
- **Documents** (JSON): keys sorted, list order kept.
- **Ordered lists** (JSON): for example the vocabulary, whose order fixes the
  columns of the SVD input matrix.
- **Arrays** (`.npy`): hashed as little-endian float64. Arrays of more than
  20 000 values are stored as float32 to keep the repository small; their hash
  is still the float64 one.
- **Labels** (JSON): group labels aligned with an ordered list.
- **Hash-only** entries: redundant copies of stored data (for example the CSV
  files that repeat the SVD coordinates). Only their hash is kept.

Timestamps, absolute paths and the installed engine's version string are
removed; names are neutral (`person`, `group`, `domain`). Stored outputs carry
no names at all: persons and groups appear as opaque identifiers, a hash of
their raw identity in the corpus index (`p…` for a person, `g…` for a group). Files larger than
32 KiB are stored gzip-compressed (`.csv.gz`, `.json.gz`, `.npy.gz`, written
without a timestamp so the bytes are reproducible).

## How a run is made

`tools/reference/run.py` runs every stage in process, on a copy of the
workspace:

- **Pinned environments.** `tools/reference/envs.py` builds two virtual
  environments from the same lock file (`tools/reference/requirements-ref.txt`)
  and the same uv-managed Python: `baseline` holds the engine wheel built from
  the released tag, `current` this working tree (editable). They differ only in
  the engine code.
- **Fixed settings.** One thread for every numeric library, a fixed hash seed,
  UTC, the `C.UTF-8` locale, and a pinned current year (2026) wherever the
  engine accepts one. The runner re-executes itself when a setting is missing.
- **No network.** Every connection is refused for the whole run.
- **A deterministic fake model** answers the AI triage in exactly the line
  format the triage prompt asks for. With the world's truth file it keeps the
  theme vocabulary (under its canonical English form) and drops everything
  else; without one it applies a fixed rule. The engine's own caches are
  therefore filled with real entries, and a second triage run must be served
  entirely from them.
- **The adapter.** Only the part of `run.py` between `ENGINE ADAPTER: BEGIN`
  and `ENGINE ADAPTER: END` knows the engine's API, file layout and column
  names. When a restructuring step changes the API, that part alone is
  edited; the serialisation, the neutral names and the comparison never
  change. It drives the engine of this tree only: every stage takes a run
  context whose year is pinned.

## Checking

The check runner calls the reference check (see [the checks](checks.md)):

```bash
python tools/check.py --quick      # includes the S and merge comparisons
python tools/check.py --full       # also L
python tools/reference/check_reference.py --size S    # this check alone
```

`check_reference.py` ensures the `current` environment, generates the demo
worlds with the demo generator, runs `run.py` in that environment, compares the
result with `tests/reference/` and prints the comparison. It fails when a stage
is *different*, or when the runs cannot be compared (another format, or other
input worlds — for example after a change of the demo generator). A merge run
also records the hashes of its two cohorts' bundles: they are engine outputs,
not input worlds, so when they differ the comparison goes on and lists them in
the notes (the single-run comparison's `bundle` stage shows why). Reports are
kept in `.cache/reference/reports/`, the runs' logs in `.cache/reference/logs/`
and the runs themselves in `.cache/reference/runs/`.

To compare two runs directly:

```bash
python tools/reference/compare.py --reference tests/reference/S --current .cache/reference/runs/S --report report.md
```

## Reading a comparison report

The report has one row per stage: its verdict, how many of its artifacts are
identical, and what differs. A second table lists every artifact that is not
identical, with its metrics. The notes list environment differences (Python,
library versions, engine version and source fingerprint); they explain a
difference, they are not one. The last line is a one-line summary.

| kind | metrics reported |
| --- | --- |
| table | columns added or removed, rows added or removed (by key), changed exact values, largest absolute and relative float difference |
| document | paths of structural or exact-value differences, largest float difference |
| ordered list | items added or removed, positions that moved |
| labels | adjusted Rand index; a pure renaming of the groups is within tolerance |
| array, values | largest absolute and relative difference |
| array, spectrum | same, for singular values |
| array, embedding | largest difference after aligning column signs; largest principal angle between the column spaces |
| array, layout | largest difference; Procrustes disparity; share of the 10 nearest neighbours kept |
| hash only | changed or not |

## Tolerances

*Within tolerance* means "the same numbers up to floating-point noise" — a
different summation order, an equivalent linear-algebra path. The limits are
set in `tools/reference/compare.py`:

| quantity | within tolerance when |
| --- | --- |
| a float in a table or a document | \|a − b\| ≤ 1e-9 + 1e-6 · \|reference\| |
| an array (values, spectrum) | \|a − b\| ≤ 1e-9 + 1e-6 · max \|reference\| |
| an embedding | the same bound after sign alignment, and the largest principal angle ≤ 1e-4 rad |
| a 2-D layout | the array bound, or a Procrustes disparity ≤ 1e-6 with ≥ 99 % of the 10 nearest neighbours kept |
| labels | the same partition (adjusted Rand index 1) |
| strings, integers, booleans, keys, vocabularies | exactly equal |

Anything else is *different*.

## The rule

A difference is either **removed** or **explained** before it is accepted.
An explanation goes into the change's description as a table:

| stage | artifact | change (metrics from the report) | cause | why it is correct |
| --- | --- | --- | --- | --- |

Never edit a reference file to make a comparison pass, and never regenerate the
reference to hide a difference. The reference is regenerated only when the
baseline itself changes (a new released engine adopted as the baseline, or a
change of the demo worlds or of the reference format), in a change of its own
that says why.

## Regenerating the reference

The stored reference is made by the released engine, run by the runner frozen
with the release that produced the reference: `run.py` in this tree follows
this tree's API and cannot drive an older engine. Regenerating therefore needs
both the `baseline` environment and that runner, given explicitly:

```bash
python tools/reference/envs.py baseline --rebuild          # when the lock or the tag changed
python tools/reference/check_reference.py --size S --regenerate --runner RUNNER.py   # S and merge
python tools/reference/check_reference.py --size L --regenerate --runner RUNNER.py
```

`RUNNER.py` is the runner frozen with the release that produced the reference
(for example, extracted from that release's tag). `--regenerate` refuses to
run without it. It runs the same worlds in the `baseline` environment and
writes `tests/reference/`. Run it twice and check that the manifests are
identical, then run the normal check: today the `current` environment must
reproduce the reference exactly. Only one run of size L at a time: it is the
memory-hungry one.

Comparing the current engine needs nothing of the sort: `check_reference.py`
without `--regenerate` always uses this tree's `run.py` in the `current`
environment.

## The AI cache keys

Existing workspaces hold paid AI answers keyed by batch and by term.
`tests/test_ai_cache_keys.py` checks that the current code reproduces every key
stored in `tests/fixtures/ai_cache_keys.json` (several models, domain titles,
accents, case, spacing, punctuation, empty and duplicate batches), and that the
triage writes exactly those keys. These keys never change: a changed key would
make users pay again for answers they already have.

## Explained differences

A change that moves an output on purpose — a bug fix, a format change — is
recorded in `tools/reference/explained.toml`, never by editing the stored
reference:

```toml
[[difference]]
reference = "S"                # S, L or merge
stage = "extract"
artifact = "terms_fr"
sha256 = "<the artifact's hash in the new run's manifest>"
reason = "why this difference is expected"
```

The comparison then reports that artifact as *explained*, and the check passes.
The entry pins the new hash: any further change to the same artifact is
*different* again, and an entry that no longer matches anything is listed in the
report's notes so that it can be removed.

A change early in the pipeline moves every later stage. Rather than pinning each
of their artifacts, an entry may explain a whole stage by an **upstream cause**:

```toml
[[difference]]
reference = "S"
stage = "space"
upstream = "build"             # an earlier stage of the same run
reason = "the SVD input is built from other candidate terms"
```

Every changed artifact of the stage is then *explained*, provided the upstream
stage changed too in the same comparison (for a merge run, `upstream =
"bundle"` names the cohorts' bundles). Such an entry pins nothing: the stored
baseline (below) is what catches a further, accidental change.
