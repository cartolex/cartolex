# Contributing to cartolex

Thank you for helping. Read `AGENTS.md` first: its rules apply to every change.

## Set up

cartolex needs Python 3.10 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv venv .venvs/py3.12 --python 3.12
uv pip install -p .venvs/py3.12 -e ".[dev]"
```

## Check a change

```bash
python tools/check.py --quick   # lint, vocabulary, tests on one Python, small reference
python tools/check.py           # everything, before a merge
```

`docs/dev/checks.md` describes each check and how to read a failure.

## Data

Use the demo generator for anything that needs a corpus:

```bash
python -m cartolex.demo create --size S --seed 0 --out /tmp/demo
```

Never add real names, profiles or texts to the repository, even in a test.

## Report a problem

Open an issue with the version, the operating system, what you did, what you
expected and what happened. Attach a diagnostic export when the app offers
one; it holds versions and logs, never your data.
