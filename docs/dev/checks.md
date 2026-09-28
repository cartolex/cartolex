# Checks

One command runs every check that must pass before a merge:

```bash
python tools/check.py            # everything
python tools/check.py --quick    # lint, vocabulary, tests on one Python, small reference,
                                 # the browser checks without the slow ones
python tools/check.py --full     # also the large reference comparison
python tools/check.py --only tests --pythons 3.10,3.14   # other versions
```

It prints one line per check (`PASS`, `FAIL` or `SKIP`, the time taken and a
summary) and keeps each check's full output in `.cache/check/`. It needs
[uv](https://docs.astral.sh/uv/) and Python 3.11 or later to run itself;
settings are in `tools/check.toml`.

## The checks

| Check | What it runs | Fails when |
| --- | --- | --- |
| `lint` | `ruff check` and `ruff format --check`, with the ruff pinned in the `dev` extra | any finding |
| `vocab` | `tools/vocab_scan.py` over the tree and the commit messages listed in `tools/check.toml` | a banned term appears outside a stated exception |
| `js` | `tools/ui_check.py`: the web interface's modules parsed by Node, imports, literal text, bans (eval, HTML from strings, inline handlers), vendored hashes, catalogues, token contrast (see {doc}`ui`) | any problem |
| `tests` | `pytest` on every Python in `tools/check.toml`, in parallel, without the browser tests | any test fails on any version |
| `browser` | `pytest tests/browser` in headless Chromium on the quick Python: axe, keyboard scripts, budgets, teardown and leaks, the shell (see {doc}`ui`); `--quick` leaves out the tests marked `slow` | any test fails; skipped, with the reason, without Playwright or a Chromium build |
| `reference` | `tools/reference/check_reference.py`: the demo world run through the engine and compared with the stored baseline and the stored reference; then the same world written as a project and built with `cartolex.build`, compared the same way | a stage is *different* from the baseline, or differs from the reference without an explanation, or the project build is not identical to the baseline in every artifact |
| `docs` | a strict Sphinx build of `docs/` | any warning |

## Virtual environments

Each Python gets `.venvs/py<version>`, created with uv and refreshed when
`pyproject.toml` or the pinned language models (`tools/requirements-models.txt`)
change; the three language models of the extraction are installed into each
from their pinned wheels. Tests that parse texts are marked `models`: without
the models they are skipped, but the check runs pytest with
`--require-models`, which makes them fail instead. Supported versions span two generations of the
scientific libraries (older numpy and pandas releases on the oldest Python). The
check tests the oldest version, where code breaks first, and the quick check the
one in `quick`; every other version is tested by CI before a release, or with
`--pythons`.

## The vocabulary scan

The scan keeps project-specific terms out of the repository. The list of terms
is not part of the repository: the scanner reads it from `--list`, from
`$CARTOLEX_DENYLIST`, or from `~/.config/cartolex-dev/deny-list.txt`, and
skips with a notice when there is none. A match is reported by the term's
number and its place, never by its text.

The check runs the scan in strict mode, which ignores inline exceptions: they may
explain a line to a reader, never hide a term from the check. Two kinds of exception
exist, each with a reason:

- a line containing `vocab-allow: <reason>`, or the line just after it;
- an `[[allow]]` entry in `tools/vocab-allow.toml` naming a file pattern and
  the numbers it may contain.

Commit and tag messages have no exceptions.

## The network

The test suite blocks every outbound connection except loopback
(`tests/conftest.py`). A test that needs data from a web service uses a
synthetic fixture or a local fake server.
