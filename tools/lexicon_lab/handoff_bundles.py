# SPDX-License-Identifier: MIT
"""Write the browser-handoff test: the bundles a person uploads to a chat assistant.

Usage::

    python tools/lexicon_lab/handoff_bundles.py --out ~/cartolex-work/handoff-test
    python tools/lexicon_lab/score_handoff.py ~/cartolex-work/handoff-test   # once answered

A demo world is written as a project and its keywords are extracted by a
project build (``keywords.extract``, the defaults; every year of texts, as the
reference runs do). The candidates of two scopes become handoff bundles
(:mod:`handoff`):

- ``tocheck/``: the to-check band, one part;
- ``kept-tocheck/``: the kept and to-check bands, cut into parts that each fit
  one assistant conversation (``--max-tokens``, counted cautiously).

Each part holds ``prompt.txt`` (to paste), ``terms.txt`` (to attach),
``expected-answer.txt``, their zip, and ``bundle.json`` (to read the answer
back). The bundles carry only what a real handoff would: the terms, their
evidence (people and texts counts, other spellings, longer phrases), the
field's title and description. Nothing of the world's truth, no person's name
or identifier: the tool checks that no name or identifier of the world
appears in them. The project itself is built under ``.cache/handoff/``, away
from the bundles.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import handoff  # noqa: E402
import pandas as pd  # noqa: E402
import variants  # noqa: E402

LOG = logging.getLogger("handoff_bundles")
YEAR = 2026
#: The two scopes: folder name and the bands the judge sees.
SCOPES = {"tocheck": ("check",), "kept-tocheck": ("kept", "check")}


def build_project(size: str, seed: int, root: Path, *, rebuild: bool = False) -> Path:
    """Write the demo world as a project in *root* and build its keyword candidates."""
    from cartolex.build import build
    from cartolex.demo import generate
    from cartolex.demo.project import write_project

    done = root / "derived" / "keywords.extract" / "raw_keywords_en.csv"
    if done.exists() and not rebuild:
        return root
    if root.exists():
        shutil.rmtree(root)
    project = write_project(generate(size, seed), root)
    try:
        params, fp = project.read_params()
        stages = {"corpus.assemble": {"recency_years": 0}}  # every year, as the reference runs
        project.save_params(
            params.model_copy(update={"stages": stages}), expected=fp, action="handoff test"
        )
        result = build(project, ["keywords.extract"], year=YEAR, budget_mb=1e12)
        if result.outcome != "succeeded" or result.refused:
            raise SystemExit(f"handoff: the build failed: {result.summary()}")
    finally:
        project.close()
    return root


def project_domain(root: Path) -> tuple[str, str, tuple[str, ...]]:
    from cartolex.project import Project

    project = Project.open(root)
    try:
        identity = project.config.identity
        return (
            identity.domain_title,
            identity.domain_description,
            tuple(project.config.languages.corpus),
        )
    finally:
        project.close()


def build_candidates(root: Path, scratch: Path) -> tuple[dict, int]:
    """The candidates of the project's build, with their evidence, and the number of texts.

    The build keeps only its tables; the bundles also need each candidate's
    longer phrases. The engine's own loader and scoring are run again on the
    built project, as the stage runs them (its settings, its texts split by
    detected language, its parse cache), and the result must equal the build's
    raw tables exactly.
    """
    from cartolex.build.engine import keywords_settings
    from cartolex.build.enginefiles import results_paths
    from cartolex.context import RunContext
    from cartolex.lexicon import language_models
    from cartolex.lexicon.extract_raw import language_units, score_language
    from cartolex.lexicon.io_helpers import load_texts_split_by_language, slot_indexes
    from cartolex.lexicon.lexicon_store import (
        load_canonical_decision_blacklist,
        load_manual_blacklist,
    )
    from cartolex.project import Project

    project = Project.open(root)
    try:
        cfg = keywords_settings(project.config, recency_years=0)
    finally:
        project.close()
    paths = results_paths(root / "derived", scratch, root)
    ctx = RunContext(paths=paths, settings=cfg, now_year=YEAR)
    people, _ = load_texts_split_by_language(
        slot_indexes(ctx), corpus_languages=cfg.corpus_languages, now_year=YEAR
    )
    blacklist = load_manual_blacklist(paths.manual_blacklist_csv) | (
        load_canonical_decision_blacklist(paths.canonical_decisions_json)
    )
    scored = {}
    for lang in cfg.corpus_languages:
        try:
            units = language_units(lang, people, cache_dir=paths.parse_cache_dir)
        finally:
            language_models.release(lang)
        sc = score_language(lang, units, len(people), cfg, blacklist=blacklist)
        built = pd.read_csv(paths.raw_terms_csv(lang), keep_default_na=False)
        mine = sc.table.reset_index(drop=True)
        same = (
            len(built) == len(mine)
            and list(built["term"]) == list(mine["term"])
            and all(
                list(built[c].astype(str)) == list(mine[c].astype(str))
                for c in ("band", "reason", "people", "texts", "forms")
            )
            and float((built["score_len"] - mine["score_len"]).abs().max()) < 1e-9
        )
        if not same:
            raise SystemExit(f"handoff: the {lang} candidates differ from the build's raw table")
        scored[lang] = sc
    n_texts = len({text_id for person in people for text_id, _ in person.texts})
    return scored, n_texts


def name_leaks(texts: list[str], world) -> list[str]:
    """Names and identifiers of the world's people found in *texts* (whole words)."""
    words = set()
    for p in world.people:
        for name in (p.first_name, p.last_name):
            words.update(w for w in re.split(r"[\s-]+", name or "") if len(w) >= 3)
    ids = {
        x
        for p in world.people
        for x in (p.person_id, p.orcid, p.idhal, p.openalex_id)
        if x and len(x) >= 4
    }
    joined = "\n".join(texts)
    found = [w for w in sorted(words) if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", joined)]
    return found + [x for x in sorted(ids) if x in joined]


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def write_handoff_test(
    out: Path,
    *,
    size: str,
    seed: int,
    project: Path,
    max_tokens: int,
    rebuild: bool = False,
    split_tocheck: bool = False,
) -> dict:
    """Build, check, and write both scopes' bundles, the manifest and the README in *out*."""
    answered = sorted(out.rglob("answer*.txt")) if out.exists() else []
    if answered:
        raise SystemExit(f"handoff: {out} holds answers ({answered[0]} …); move them first")
    from cartolex.demo import generate

    build_project(size, seed, project, rebuild=rebuild)
    domain, description, languages = project_domain(project)
    scored, n_texts = build_candidates(project, project.parent / f"{project.name}-scratch")
    n_people = max(sc.n_people for sc in scored.values())
    world = generate(size, seed)
    for scope in SCOPES:
        if (out / scope).exists():
            shutil.rmtree(out / scope)
    out.mkdir(parents=True, exist_ok=True)

    manifest = {
        "format": "cartolex-handoff-test/1",
        "world": {"size": size, "seed": seed, "languages": list(languages)},
        "build": {
            "project": str(project),
            "stages": ["corpus.assemble", "keywords.extract"],
            "params": {"corpus.assemble": {"recency_years": 0}},
            "year": YEAR,
            "engine_commit": _git_commit(),
            "scoring": repr(variants.BASE),
        },
        "domain": domain,
        "description": description,
        "people": n_people,
        "texts": n_texts,
        "max_tokens": max_tokens,
        "tokens": "estimated: characters / 4; cautious: characters / 3 (used to cut the parts)",
        "bundles": {},
    }
    leaks: list[str] = []
    for scope, bands in SCOPES.items():
        b = handoff.bundle(scored, bands=bands, domain=domain, description=description)
        if scope == "tocheck" and not split_tocheck:
            chunks = [b.items]
        else:
            chunks = handoff.split_items(
                b.items,
                max_tokens=max_tokens,
                domain=domain,
                description=description,
                n_people=n_people,
                n_texts=n_texts,
            )
        parts = []
        for k, chunk in enumerate(chunks, 1):
            folder = out / scope if len(chunks) == 1 else out / scope / f"part-{k:02d}"
            name = f"handoff-{scope}" + (f"-part-{k:02d}" if len(chunks) > 1 else "")
            sizes = handoff.write_part(
                folder,
                chunk,
                name=name,
                domain=domain,
                description=description,
                n_people=n_people,
                n_texts=n_texts,
                part=k,
                parts=len(chunks),
                meta={"scope": scope, "bands": list(bands)},
            )
            sizes["folder"] = str(folder.relative_to(out))
            parts.append(sizes)
            leaks += name_leaks(
                [(folder / f).read_text(encoding="utf-8") for f in ("prompt.txt", "terms.txt")],
                world,
            )
        manifest["bundles"][scope] = {
            "bands": list(bands),
            "items": len(b.items),
            "by_language": {
                lang: sum(1 for it in b.items if it.lang == lang) for lang in sorted(scored)
            },
            "parts": parts,
        }
    if leaks:
        raise SystemExit(f"handoff: names or identifiers of people in the bundles: {leaks[:10]}")
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (out / "README.md").write_text(readme(manifest), encoding="utf-8")
    return manifest


def _size_rows(manifest: dict) -> list[str]:
    rows = [
        "| bundle | folder | terms | tokens (estimated) | tokens (cautious) | answer tokens |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for scope, info in manifest["bundles"].items():
        for p in info["parts"]:
            label = scope if len(info["parts"]) == 1 else f"{scope}, part {p['part']}"
            rows.append(
                f"| {label} | `{p['folder']}/` | {p['items']:,} | {p['tokens']:,} | "
                f"{p['cautious_tokens']:,} | {p['answer_tokens']:,} |"
            )
    return rows


def readme(manifest: dict) -> str:
    """The owner's guide to the test folder, in plain words."""
    kt = manifest["bundles"]["kept-tocheck"]
    tc = manifest["bundles"]["tocheck"]
    world = manifest["world"]
    limit = manifest["max_tokens"]
    big = [
        p
        for info in manifest["bundles"].values()
        for p in info["parts"]
        if p["cautious_tokens"] > limit
    ]
    tc_parts = (
        "in one bundle: one conversation"
        if len(tc["parts"]) == 1
        else f"in {len(tc['parts'])} parts: one conversation each"
    )
    size_note = f"Every part of `kept-tocheck/` stays under {limit:,} cautious tokens. " + (
        f"The `tocheck/` bundle is one piece, as a person would upload it: "
        f"{tc['parts'][0]['tokens']:,} tokens ({tc['parts'][0]['cautious_tokens']:,} "
        "cautious), well within what a conversation holds (about 200,000 tokens in "
        "claude.ai), but above that target."
        if big
        else "So does the `tocheck/` bundle."
    )
    return f"""# Handoff test: judging keywords in a chat assistant

Instead of sending the keyword candidates to a paid AI service, cartolex could
give them to you as a file for the chat assistant you already use (claude.ai,
for example), and read its answers back. This folder holds such files, made
from the demo world {world["size"]} (seed {world["seed"]}), so that we can
measure how well an assistant judges them.

Everything here is invented: the demo field («{manifest["domain"]}»), its
texts and its people. The files hold only what a real handoff would carry: the
candidate terms, how many people and texts use each one, its other spellings,
the longer phrases it appears in, and the field's title and description. No
names of people, no identifiers, and nothing of the world's answer key.

## What is here

- `tocheck/`: the {tc["items"]:,} candidates *to check* (single words, and
  phrases starting or ending with a very common adjective), English and French,
  {tc_parts}.
- `kept-tocheck/`: the {kt["items"]:,} candidates *kept* or *to check*, in
  {len(kt["parts"])} parts (`part-01` to `part-{len(kt["parts"]):02d}`): one
  conversation each.

Each bundle (the `tocheck` folder, or one `part-NN` folder) holds:

- a zip with the three files below, the way cartolex would hand them to you;
- `prompt.txt`: the message to paste;
- `terms.txt`: the numbered list of terms with their evidence, to attach;
- `expected-answer.txt`: the answer expected, and where to save it;
- `bundle.json`: the same list for the computer that reads the answer back
  (not for the assistant).

Sizes, in tokens (a token is about four characters; the cautious count
assumes three). The last column is the length of a complete answer, roughly:
an assistant stops after a few thousand tokens and waits for `continue`.

{chr(10).join(_size_rows(manifest))}

{size_note}

## Try one bundle by hand in claude.ai

1. Choose a bundle: `tocheck/`, or one `kept-tocheck/part-NN/`.
2. Open its zip (a double-click unpacks it), or use the three files next to
   it.
3. In claude.ai, start a **new conversation** (not in a project, with no other
   files). Attach `terms.txt` with the paperclip, or drag it in. Attach only
   that file: not the zip, not `bundle.json`.
4. Open `prompt.txt`, copy all its text, paste it as your message, and send.
5. The assistant answers with numbered lines such as
   `12 | O | levure bourgeonnante | budding yeast`. When it stops before the
   last number, type `continue` and send; repeat until the last number is
   there.
6. Copy the answer (the copy button of each code block) into a plain-text file
   named `answer.txt` **in the bundle's folder**: `tocheck/answer.txt`, or
   `kept-tocheck/part-03/answer.txt`. When the answer came in several pieces,
   paste them one after the other in `answer.txt`, or save them as
   `answer-1.txt`, `answer-2.txt` … If the assistant offered a file
   `answer.txt` to download, put that file there instead.
7. Write the model's name and the date in a file `judge.txt` next to the
   answer (for example `Claude Opus, claude.ai, 2026-09-28`).

The answers are scored afterwards against the demo world's answer key, from a
checkout of the repository (the branch `step2-handoff`, or main once it is
merged):

```bash
python tools/lexicon_lab/score_handoff.py ~/cartolex-work/handoff-test
```

It prints, for each bundle answered: how many terms were answered, the
precision and recall of the accepted terms, the agreement on English forms,
and the same measures for a perfect judge and for one wrong one time in ten.
The full report goes to `.cache/lexicon_lab/handoff-score.md` in the
repository, never into this folder, so that the folder stays blind.

## Good to know

- Use a fresh conversation for each bundle: an assistant that has seen one
  part judges the next one differently.
- The prompt asks the assistant not to write a program to classify the terms.
  If it does anyway (when code execution is on), say so in `judge.txt`.
- A partial answer is fine: the score counts the lines given and says how
  many are missing.
- For an automated blind judge: give it `prompt.txt` as the message and
  `terms.txt` as the attachment, and nothing else from this folder.

Made by `tools/lexicon_lab/handoff_bundles.py` (commit
{manifest["build"]["engine_commit"] or "?"}), from a project build of
`keywords.extract` with its default settings, on every year of texts.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the browser-handoff test bundles.")
    parser.add_argument(
        "--out", type=Path, required=True, help="the test folder (outside the repo)"
    )
    parser.add_argument("--size", default="L")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--project", type=Path, help="where to build (default .cache/handoff/)")
    parser.add_argument("--max-tokens", type=int, default=45_000, help="per part, cautious count")
    parser.add_argument("--rebuild", action="store_true", help="build the project again")
    parser.add_argument(
        "--split-tocheck", action="store_true", help="cut the to-check bundle into parts too"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for noisy in ("cartolex", "numba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    out = args.out.expanduser().resolve()
    if ROOT in out.parents or out == ROOT:
        raise SystemExit("handoff: write the test folder outside the repository")
    project = args.project or ROOT / ".cache" / "handoff" / f"project-{args.size}-{args.seed}"
    manifest = write_handoff_test(
        out,
        size=args.size,
        seed=args.seed,
        project=project,
        max_tokens=args.max_tokens,
        rebuild=args.rebuild,
        split_tocheck=args.split_tocheck,
    )
    print("\n".join(_size_rows(manifest)))
    print(f"handoff test: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
