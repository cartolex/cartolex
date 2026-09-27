# SPDX-License-Identifier: MIT
"""Run the demo world through the current engine and print what comes out.

Generates a demo world, writes its corpus contract into a fresh workspace,
runs the engine offline (extraction, consolidation, roster, SVD, concept
clustering, UMAP) and prints the sizes that matter: people, works, words,
raw keywords per language, global keywords, atlas terms, concepts, run times
and peak memory.

Usage::

    python tools/demo_stats.py --size S
    python tools/demo_stats.py --size L --json stats-L.json

The engine settings are in ``ENGINE_SETTINGS`` below; a reference run reuses
them verbatim. Run one large size at a time: size L peaks at about 1 GB.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import platform
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cartolex.demo import generate  # noqa: E402
from cartolex.demo.vocabulary import DRIVERS, METHODS, SETTINGS, THEMES  # noqa: E402

# Engine settings for demo runs. Only departures from the engine's defaults
# are listed, so that a change of a default shows up in a reference
# comparison. The parameter names are the engine's own.
ENGINE_SETTINGS: dict[str, dict] = {
    # RunContext.for_workspace(<workspace>, KeywordsConfig(**ENGINE_SETTINGS["keywords"]))
    "keywords": {
        # The demo corpus sits in the engine's default corpus slot ("manual") alone.
        # Works span 2012-2026: use the whole history, not the last five years.
        "kw_recency_years": 0,
    },
    # cartolex.atlas.driver.run_svd(**...), run_clustering(**...), run_umap(**...)
    "svd": {},
    "clustering": {},
    "umap": {},
}


def _peak_mb() -> float | None:
    """Peak resident memory of this process so far, in MB (None where unavailable)."""
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if platform.system() == "Darwin" else peak / 1024


def _rows(path: Path) -> int:
    """Number of data rows in a CSV file (0 when it does not exist)."""
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8", newline="") as handle:
        return max(sum(1 for _ in csv.reader(handle)) - 1, 0)


def _column(path: Path, name: str) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [row[name] for row in csv.DictReader(handle)]


_TOKEN = re.compile(r"(?u)\b\w\w+\b")


def _tokens(text: str) -> str:
    """Lower-case words of two letters or more, as the engine's vectorizer sees them."""
    return " ".join(_TOKEN.findall(text.lower()))


def vocabulary_forms() -> tuple[set[str], set[str]]:
    """Tokenised forms of the theme terms and methods, and of the settings and drivers."""
    terms = set()
    for theme in THEMES:
        for term in theme.terms:
            terms |= {_tokens(term.en), _tokens(term.fr)}
    for method in METHODS:
        terms |= {_tokens(method.term.en), _tokens(method.term.fr)}
    context = {_tokens(s.en) for s in SETTINGS} | {_tokens(s.fr) for s in SETTINGS}
    context |= {_tokens(d.en) for d in DRIVERS} | {_tokens(d.fr) for d in DRIVERS}
    return terms, context


def classify_terms(atlas_terms: list[str]) -> dict[str, float]:
    """Share of atlas terms by origin in the generator's vocabulary.

    ``term``: a theme term or method, whole. ``part``: a piece of one, or one
    plus a neighbouring word (the engine keeps n-grams of 1 to 4 words).
    ``context``: a study setting or driver phrase, or a piece of one.
    ``generic``: anything else, i.e. the phrasing of the templates.
    """
    terms, context = vocabulary_forms()
    padded_terms = [f" {f} " for f in terms]
    padded_context = [f" {f} " for f in context]
    counts = {"term": 0, "part": 0, "context": 0, "generic": 0}
    for raw in atlas_terms:
        t = _tokens(raw)
        pt = f" {t} "
        if t in terms:
            counts["term"] += 1
        elif any(pt in f or f in pt for f in padded_terms):
            counts["part"] += 1
        elif any(pt in f or f in pt for f in padded_context):
            counts["context"] += 1
        else:
            counts["generic"] += 1
    n = max(len(atlas_terms), 1)
    return {k: round(v / n, 3) for k, v in counts.items()}


def run(size: str, seed: int, workdir: Path) -> dict:
    """Generate, write and run the engine in *workdir*; return the measurements."""
    stats: dict = {"size": size, "seed": seed, "engine_settings": ENGINE_SETTINGS}
    times: dict[str, float] = {}

    t0 = time.perf_counter()
    world = generate(size=size, seed=seed)
    times["generate"] = time.perf_counter() - t0
    ws = workdir / "workspace"
    t0 = time.perf_counter()
    summary = world.write_corpus(ws)
    times["write_corpus"] = time.perf_counter() - t0

    counts = world.counts()
    manual = summary["manual"]
    corpus_ids = {Path(p).stem for p in _column(ws / "manual_index.csv", "txt_path")}
    works = {w.work_id: w for w in world.works}
    stats["world"] = {
        "people": counts["cohort"],
        "people_with_works": manual["people"],
        "applicants": counts["applicants"],
        "groups": counts["groups"],
        "works": manual["texts"],
        "works_fr": sum(1 for i in corpus_ids if works[i].language == "fr"),
        "index_rows": manual["rows"],
        "words": sum(works[i].words for i in corpus_ids),
    }

    from cartolex.atlas import driver
    from cartolex.context import RunContext
    from cartolex.lexicon import KeywordsConfig, run_pipeline_stage_1, run_pipeline_stage_3
    from cartolex.lexicon.io_helpers import build_researcher_index

    ctx = RunContext.for_workspace(ws, KeywordsConfig(**ENGINE_SETTINGS["keywords"]))
    stages = (
        ("extraction", lambda: run_pipeline_stage_1(ctx)),
        ("consolidation", lambda: run_pipeline_stage_3(ctx)),
        ("roster", lambda: build_researcher_index(ctx)),
        ("svd", lambda: driver.run_svd(ctx, **ENGINE_SETTINGS["svd"])),
        ("clustering", lambda: driver.run_clustering(ctx, **ENGINE_SETTINGS["clustering"])),
        ("umap", lambda: driver.run_umap(ctx, **ENGINE_SETTINGS["umap"])),
    )
    for name, call in stages:
        t0 = time.perf_counter()
        call()
        times[name] = time.perf_counter() - t0

    paths = ctx.paths
    atlas_terms = _column(paths.layout_terms_csv, "term")
    proto = json.loads(paths.proto_subfields_json.read_text(encoding="utf-8"))
    stats["engine"] = {
        "raw_keywords": {lang: _rows(paths.raw_terms_csv(lang)) for lang in ("en", "fr")},
        "global_keywords": _rows(paths.global_terms_csv),
        "refined_keywords": _rows(paths.refined_terms_csv),
        "person_keyword_rows": _rows(paths.person_terms_csv),
        "atlas_people": _rows(paths.layout_persons_csv),
        "atlas_terms": len(atlas_terms),
        "concepts": _rows(paths.clusters_csv),
        "proto_subfields": int(proto.get("n_subfields", 0)),
        "atlas_term_origin": classify_terms(atlas_terms),
    }
    stats["seconds"] = {k: round(v, 2) for k, v in times.items()}
    stats["seconds"]["engine_total"] = round(
        sum(v for k, v in times.items() if k not in ("generate", "write_corpus")), 2
    )
    stats["peak_memory_mb"] = None if _peak_mb() is None else round(_peak_mb(), 0)
    stats["python"] = platform.python_version()
    return stats


def render(stats: dict) -> str:
    """Human-readable report."""
    w, e, s = stats["world"], stats["engine"], stats["seconds"]
    q = e["atlas_term_origin"]
    lines = [
        f"demo world {stats['size']}/{stats['seed']} (Python {stats['python']})",
        f"  people            {w['people']} ({w['people_with_works']} with works), "
        f"{w['applicants']} in projected sets, {w['groups']} groups",
        f"  works             {w['works']} ({w['works_fr']} in French), "
        f"{w['index_rows']} index rows",
        f"  words             {w['words']}",
        f"  raw keywords      en {e['raw_keywords']['en']}, fr {e['raw_keywords']['fr']}",
        f"  global keywords   {e['global_keywords']} (refined {e['refined_keywords']})",
        f"  atlas             {e['atlas_people']} people, {e['atlas_terms']} terms, "
        f"{e['concepts']} concepts, {e['proto_subfields']} proto-subfields",
        f"  atlas terms       {q['term']:.0%} theme terms or methods, {q['part']:.0%} parts "
        f"of one, {q['context']:.0%} settings or drivers, {q['generic']:.0%} generic phrasing",
        "  seconds           "
        + ", ".join(f"{k} {v:.1f}" for k, v in s.items() if k != "engine_total")
        + f" (engine {s['engine_total']:.1f})",
        f"  peak memory       {stats['peak_memory_mb']} MB",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--size", default="S", type=str.upper)
    parser.add_argument("--seed", default=0, type=int)
    parser.add_argument("--workdir", type=Path, help="scratch folder (default: a temporary one)")
    parser.add_argument("--keep", action="store_true", help="keep the scratch folder")
    parser.add_argument("--json", type=Path, help="also write the measurements to this file")
    parser.add_argument("--verbose", action="store_true", help="show the engine's log")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    json_out = args.json.resolve() if args.json else None
    if args.workdir:
        workdir = args.workdir.resolve()
        if workdir.exists() and any(workdir.iterdir()):
            parser.error(f"{workdir} is not empty")
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = Path(tempfile.mkdtemp(prefix="cartolex-demo-stats-"))
    try:
        stats = run(args.size, args.seed, workdir)
    finally:
        if not args.keep and not args.workdir:
            shutil.rmtree(workdir, ignore_errors=True)
    print(render(stats))
    if json_out:
        json_out.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    if args.keep or args.workdir:
        print(f"workspace kept in {workdir / 'workspace'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
