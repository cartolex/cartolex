# SPDX-License-Identifier: MIT
"""The lexicon lab: compare the open choices of the keyword extraction, write a report.

Usage::

    python tools/lexicon_lab/run.py --suite quick         # demo worlds of size S, minutes
    python tools/lexicon_lab/run.py --suite full          # also L and the public benchmarks
    python tools/lexicon_lab/run.py --fetch               # download the public benchmarks first

Each corpus is parsed once (with the engine's parse cache); every variant then
scores the same analyses (:mod:`cartolex.lexicon.scoring`) and is measured
against the corpus's gold (:mod:`measures`): precision and recall of the final
lexicon, the AI load, stability under the removal of 10 % of the texts, and,
on demo worlds, how well the engine's term groups and people's theme mixes
recover the world's truth (:mod:`pipeline`). The report (Markdown, every
table) goes to ``.cache/lexicon_lab/report-<suite>.md``; ``--json`` also
writes the raw numbers.

The benchmarks are read from ``.cache/datasets/`` and never committed. The lab
never calls a paid service: the AI triage is played by fake judges, and its
cost is estimated (:mod:`handoff`).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import resource
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import analyses as lab_analyses  # noqa: E402
import corpora  # noqa: E402
import handoff  # noqa: E402
import measures  # noqa: E402
import pipeline  # noqa: E402
import variants  # noqa: E402

from cartolex.lexicon.noun_phrases import lemma_table  # noqa: E402
from cartolex.lexicon.scoring import score_units  # noqa: E402

LOG = logging.getLogger("lexicon_lab")
MIN_PEOPLE = 3
MAX_SHARE = 0.6
DROP = 0.1  # share of texts removed in the stability runs
STABILITY_SEEDS = (1, 2)
#: Illustrative prices per million tokens (input, output), to be checked before use.
PRICES = (
    handoff.Price("small model, 0.10 / 0.30 per M tokens", 0.10, 0.30),
    handoff.Price("large model, 2 / 6 per M tokens", 2.0, 6.0),
)


@dataclass
class CorpusSpec:
    name: str
    make: object
    theme_recovery: bool = False
    stability: bool = True
    names: bool = True


SUITES = {
    # A few seconds, for the test suite: one tiny world, no engine pipeline.
    "smoke": [CorpusSpec("demo XS", lambda: corpora.demo_corpus("XS"), names=False)],
    "quick": [
        CorpusSpec("demo S", lambda: corpora.demo_corpus("S"), theme_recovery=True),
        CorpusSpec("demo S bodies", lambda: corpora.demo_corpus("S", bodies=True)),
        CorpusSpec(
            "demo S trilingual", lambda: corpora.demo_corpus("S", languages="en,fr,pt"), names=False
        ),
    ],
    "full": [
        CorpusSpec("demo S", lambda: corpora.demo_corpus("S"), theme_recovery=True),
        CorpusSpec(
            "demo S bodies", lambda: corpora.demo_corpus("S", bodies=True), theme_recovery=True
        ),
        CorpusSpec("demo L", lambda: corpora.demo_corpus("L"), theme_recovery=True),
        CorpusSpec(
            "demo L trilingual",
            lambda: corpora.demo_corpus("L", languages="en,fr,pt"),
            theme_recovery=True,
            names=False,
        ),
        CorpusSpec(
            "demo L bodies",
            lambda: corpora.demo_corpus("L", bodies=True),
            theme_recovery=True,
            stability=False,
            names=False,
        ),
        CorpusSpec("inspec", corpora.inspec),
        CorpusSpec("termith", corpora.termith),
        CorpusSpec("semeval", corpora.semeval),
        CorpusSpec("scielo", corpora.scielo),
    ],
}


@dataclass
class CorpusResult:
    spec: CorpusSpec
    info: dict
    rows: list[dict] = field(default_factory=list)
    bands: list[dict] = field(default_factory=list)
    triage: list[dict] = field(default_factory=list)
    reasons: list[dict] = field(default_factory=list)


def peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def golds_of(parsed) -> tuple[dict, dict]:
    """The matcher and the gold of each language of a parsed corpus."""
    corpus = parsed.corpus
    matchers, golds = {}, {}
    for lang, units in parsed.units.items():
        lemmas = lemma_table(lab_analyses.all_analyses(units))
        m = measures.Matcher(lang, lemmas, stem=corpus.stemmed_gold)
        terms = [g for g in corpus.gold if g.lang == lang and g.field]
        keys = {}
        themes = {}
        for g in terms:
            k = m.key(g.text)
            if k:
                keys.setdefault(k, g.canonical or g.text.lower())
                if g.themes:
                    themes[g.canonical or g.text.lower()] = g.themes
        by_person: dict[int, list[str]] = {}
        for u in units:
            by_person.setdefault(u.person, []).extend(
                parsed.paragraphs[id(a)] for _, part in u.parts for a in part
            )
        people = measures.people_with(set(keys), by_person, m)
        shared = {k for k, n in people.items() if n >= MIN_PEOPLE}
        matchers[lang] = m
        golds[lang] = measures.Gold(set(keys), shared, keys, themes)
    return matchers, golds


def score(parsed, variant, units=None) -> dict:
    units = units if units is not None else parsed.units
    return {
        lang: score_units(
            lang,
            lang_units,
            len(parsed.corpus.people),
            min_df=MIN_PEOPLE,
            max_df=MAX_SHARE,
            options=variant.options,
            names=parsed.names if variant.names else None,
        )
        for lang, lang_units in units.items()
    }


def measure(parsed, variant, matchers, golds, *, stability: bool, workspace) -> dict:
    t0 = time.perf_counter()
    scored = score(parsed, variant)
    seconds = time.perf_counter() - t0
    counts = measures.summed(
        measures.evaluate(scored[lang].table, matchers[lang], golds[lang]) for lang in scored
    )
    row = {"variant": variant.id, "family": variant.family, "label": variant.label}
    row.update(counts)
    row.update(measures.ratios(counts))
    flags, values = [], []
    for lang, sc in scored.items():
        m, g = matchers[lang], golds[lang]
        for term, value in zip(sc.table["term"], sc.table["score_len"], strict=True):
            k = m.key(term)
            flags.append(bool(k) and k in g.all)
            values.append(float(value))
    row.update(measures.ranking(values, flags))
    row["score_seconds"] = seconds
    if stability:
        full = {
            (lang, k): v
            for lang in scored
            for k, v in measures.final_keys(scored[lang].table, matchers[lang], golds[lang]).items()
        }
        jac, rho = [], []
        for seed in STABILITY_SEEDS:
            sub = score(parsed, variant, measures.subsample(parsed.units, DROP, seed))
            part = {
                (lang, k): v
                for lang in sub
                for k, v in measures.final_keys(
                    sub[lang].table, matchers[lang], golds[lang]
                ).items()
            }
            jac.append(measures.jaccard(set(full), set(part)))
            common = sorted(set(full) & set(part))
            rho.append(measures.spearman([full[k] for k in common], [part[k] for k in common]))
        row["jaccard"] = sum(jac) / len(jac)
        row["spearman"] = sum(rho) / len(rho)
    if workspace is not None:
        tables = {lang: sc.table for lang, sc in scored.items() if not sc.empty}
        row.update(
            pipeline.theme_recovery(
                workspace,
                tables,
                matchers,
                golds,
                length_bonus_alpha=variant.options.length_bonus_alpha,
            )
        )
    return row, scored


REASON_ORDER = [
    ("kept", "multiword"),
    ("check", "single-word"),
    ("check", "common-modifier"),
    ("check", "below-threshold"),
    ("aside", "part-of"),
    ("aside", "low-score"),
    ("aside", "name"),
]


def reasons(parsed, matchers, golds, corpus_name: str) -> list[dict]:
    """What each band rule catches (every rule on), and the gold share among it."""
    v = variants.diagnostic(names=parsed.names is not None)
    scored = score(parsed, v)
    cells: dict[tuple[str, str], list[int]] = {}
    for lang, sc in scored.items():
        for key, (n, g) in measures.by_reason(sc.table, matchers[lang], golds[lang]).items():
            cell = cells.setdefault(key, [0, 0])
            cell[0] += n
            cell[1] += g
    total = [sum(c[0] for c in cells.values()), sum(c[1] for c in cells.values())]
    order = [k for k in REASON_ORDER if k in cells] + sorted(set(cells) - set(REASON_ORDER))
    rows = []
    for (band, reason), (n, g) in [(k, cells[k]) for k in order] + [
        (("all", "every candidate"), total)
    ]:
        rows.append(
            {
                "corpus": corpus_name,
                "band": band,
                "reason": reason,
                "candidates": n,
                "gold": g,
                "gold_share": g / n if n else float("nan"),
            }
        )
    return rows


#: What the judge sees: the bands sent to it (the others keep their band).
SCOPES = (
    ("every candidate", ("kept", "check", "aside")),
    ("kept and to-check bands", ("kept", "check")),
    ("to-check band", ("check",)),
)


def _usage_tokens_per_item(parsed, items) -> float:
    """Tokens of two short usage lines per term, measured on a sample of *items*."""
    sample = items[:: max(1, len(items) // 200)][:200]
    if not sample:
        return 0.0
    lowered = [(p, p.lower()) for p in parsed.paragraphs.values()]
    extra = 0
    for it in sample:
        low = it.term.lower()
        hits = [p for p, pl in lowered if low in pl][:50]
        lines = handoff.usage_lines(hits, [it.term]).get(it.term, [])
        extra += sum(handoff.tokens("   « " + u + " »\n") for u in lines)
    return extra / len(sample)


def _judged(scored, verdicts, scope: tuple[str, ...], matchers, golds) -> dict:
    """Precision and recall when the judge decides the bands in *scope* (kept stays kept)."""
    counts = []
    for lang, sc in scored.items():
        table = sc.table.copy()
        table["band"] = [
            (
                ("kept" if verdicts.get(term, handoff.Verdict("F")).accept else "aside")
                if band in scope
                else ("kept" if band == "kept" else "aside")
            )
            for term, band in zip(table["term"], table["band"], strict=True)
        ]
        counts.append(measures.evaluate(table, matchers[lang], golds[lang]))
    return measures.ratios(measures.summed(counts))


def triage_costs(parsed, scored, matchers, golds, corpus_name: str) -> list[dict]:
    """What the AI triage would cost by route and scope, and what fake judges make of it.

    API routes send bare strings in batches, as the engine's triage does
    (today: every candidate); a handoff sends one bundle with each term's
    evidence, optionally with two usage lines per term.
    """
    from cartolex.lexicon.triage_typed import build_typed_prompt

    system, _ = build_typed_prompt(["x"], "Research field", domain_description="")
    rows = []

    def add(route: str, scope: str, cost: dict) -> None:
        row = {"corpus": corpus_name, "route": route, "scope": scope, **cost}
        for price in PRICES:
            row[price.name] = handoff.priced(cost, price)
        rows.append(row)

    bundles = {}
    for scope, bands in SCOPES:
        terms = [
            t
            for sc in scored.values()
            for t, b in zip(sc.table["term"], sc.table["band"], strict=True)
            if b in bands
        ]
        add("API", scope, handoff.api_cost(terms, system))
        if scope == "every candidate":
            continue
        b = handoff.bundle(scored, bands=bands, domain="Research field")
        bundles[scope] = (b, bands)
        add("handoff", scope, handoff.handoff_cost(b))
        per_item = _usage_tokens_per_item(parsed, b.items)
        add(
            "handoff, with usage lines",
            scope,
            handoff.handoff_cost(b, extra_input_tokens=int(per_item * len(b.items))),
        )

    def is_gold(item) -> bool:
        return matchers[item.lang].key(item.term) in golds[item.lang].all

    every = handoff.bundle(scored, bands=("kept", "check", "aside"), domain="Research field")
    for judge in (handoff.OracleJudge(is_gold), handoff.NoisyJudge(is_gold, 0.1, seed=7)):
        verdicts = judge.judge(every)  # one verdict per term, whatever the scope
        for scope, bands in SCOPES:
            r = _judged(scored, verdicts, bands, matchers, golds)
            rows.append(
                {
                    "corpus": corpus_name,
                    "route": f"judge: {judge.name}",
                    "scope": scope,
                    "terms": sum(1 for it in every.items if it.band in bands),
                    "precision": r["precision"],
                    "recall": r["recall"],
                    "f1": r["f1"],
                }
            )
    return rows


def run_corpus(spec: CorpusSpec, *, jobs: int, quick: bool) -> CorpusResult:
    t0 = time.perf_counter()
    corpus = spec.make()
    parsed = lab_analyses.parse(corpus, n_jobs=jobs, names=spec.names)
    words = sum(len(v.split()) for t in corpus.texts for v in t.parts.values())
    info = {
        "corpus": spec.name,
        "languages": ",".join(sorted(parsed.units)),
        "people": len(corpus.people),
        "texts": len({t.text_id for t in corpus.texts}),
        "words": words,
        "gold": len([g for g in corpus.gold if g.field]),
        "parse_seconds": parsed.parse_seconds,
        "names_seconds": parsed.names_seconds,
    }
    matchers, golds = golds_of(parsed)
    info["reachable_gold"] = sum(len(g.shared) for g in golds.values())
    result = CorpusResult(spec, info)
    workspace = pipeline.Workspace(parsed) if spec.theme_recovery else None
    try:
        todo = [variants.BASELINE]
        for family in variants.FAMILIES.values():
            for v in family[1:]:
                if v.needs_bodies and not corpus.has_bodies:
                    continue
                if v.names and parsed.names is None:
                    continue
                todo.append(v)
        rec = variants.recommended()
        triage_on = variants.BASELINE
        if rec.options != variants.BASE or rec.names:
            todo.append(rec)
            triage_on = rec
        for v in todo:
            LOG.info("%s: %s", spec.name, v.id)
            row, scored = measure(
                parsed, v, matchers, golds, stability=spec.stability, workspace=workspace
            )
            result.rows.append(row)
            if v is triage_on:
                result.triage += triage_costs(parsed, scored, matchers, golds, spec.name)
        for rules in variants.BAND_POINTS:
            v = variants.band_variant(rules, base=rec.options)
            row, _ = measure(parsed, v, matchers, golds, stability=False, workspace=None)
            result.bands.append(row)
        result.reasons = reasons(parsed, matchers, golds, spec.name)
    finally:
        if workspace is not None:
            workspace.close()
    info["seconds"] = time.perf_counter() - t0
    info["peak_mb"] = peak_mb()
    return result


# ── the report ──────────────────────────────────────────────────────────────


def _fmt(v, kind: str = "") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if kind == "pct":
        return f"{100 * v:.1f} %"
    if kind == "int":
        return f"{int(v):,}"
    if kind == "s":
        return f"{v:.1f}"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def table(rows: list[dict], columns: list[tuple[str, str, str]]) -> list[str]:
    out = ["| " + " | ".join(h for h, _, _ in columns) + " |", "|" + "---|" * len(columns)]
    for r in rows:
        out.append("| " + " | ".join(_fmt(r.get(k), kind) for _, k, kind in columns) + " |")
    return out


MAIN_COLUMNS = [
    ("variant", "label", ""),
    ("candidates", "candidates", "int"),
    ("kept", "kept", "int"),
    ("to check (AI load)", "check", "int"),
    ("set aside", "aside", "int"),
    ("precision, kept", "precision_kept", "pct"),
    ("precision, final", "precision", "pct"),
    ("recall", "recall", "pct"),
    ("recall ceiling", "recall_ceiling", "pct"),
    ("F1", "f1", "pct"),
    ("ranking AUC", "auc", ""),
    ("precision, best 10 %", "p_top", "pct"),
    ("gold set aside", "gold_set_aside", "int"),
    ("stability, Jaccard", "jaccard", ""),
    ("stability, rank ρ", "spearman", ""),
    ("ARI topics", "ari_topics", ""),
    ("ARI themes", "ari_themes", ""),
    ("person mix, cosine", "mix_cosine", ""),
    ("scoring s", "score_seconds", "s"),
]


SUMMARY_COLUMNS = [
    ("corpus", "corpus", ""),
    ("variant", "label", ""),
    ("AI load", "check", "int"),
    ("precision", "precision", "pct"),
    ("recall", "recall", "pct"),
    ("F1", "f1", "pct"),
    ("AUC", "auc", ""),
    ("best 10 %", "p_top", "pct"),
    ("gold aside", "gold_set_aside", "int"),
    ("Jaccard", "jaccard", ""),
    ("ARI themes", "ari_themes", ""),
    ("mix cos", "mix_cosine", ""),
]


def summary(results) -> list[str]:
    """One compact table per choice, every corpus: the tables the documentation quotes."""
    lines = ["## Summary by choice", ""]
    for family in variants.FAMILIES:
        rows = []
        for r in results:
            mine = [x for x in r.rows if x["family"] == family]
            if not mine:
                continue
            base = next(x for x in r.rows if x["family"] == "baseline")
            first = dict(base, label=variants.FAMILIES[family][0].label)
            for x in [first, *mine]:
                rows.append(dict(x, corpus=r.info["corpus"]))
        if rows:
            cols = [c for c in SUMMARY_COLUMNS if any(c[1] in x for x in rows) or c[1] == "corpus"]
            lines += [f"### {family.capitalize()}", ""] + table(rows, cols) + [""]
    return lines


def report(results, suite: str, seconds: float, peak: float | None = None) -> str:
    lines = [
        f"# Lexicon lab report — suite {suite}",
        "",
        f"Generated by `tools/lexicon_lab/run.py --suite {suite}` in {seconds / 60:.1f} min, "
        f"peak memory {peak if peak is not None else peak_mb():.0f} MB. Window: at least "
        f"{MIN_PEOPLE} people, at most "
        f"{MAX_SHARE:.0%} of them. Judge: the oracle (it accepts exactly the gold). "
        "Stability: the same corpus without 10 % of its texts (two draws).",
        "",
        "## Corpora",
        "",
    ]
    lines += table(
        [r.info for r in results],
        [
            ("corpus", "corpus", ""),
            ("languages", "languages", ""),
            ("people", "people", "int"),
            ("texts", "texts", "int"),
            ("words", "words", "int"),
            ("gold terms", "gold", "int"),
            ("reachable gold", "reachable_gold", "int"),
            ("parse s", "parse_seconds", "s"),
            ("names s", "names_seconds", "s"),
            ("lab s", "seconds", "s"),
        ],
    )
    lines += [""] + summary(results)
    families = ["baseline", *variants.FAMILIES, "recommended"]
    for family in families:
        if not any(x["family"] == family for r in results for x in r.rows):
            continue
        lines += ["", f"## {family.capitalize()}", ""]
        for r in results:
            rows = [x for x in r.rows if x["family"] in (family, "baseline")]
            if family != "baseline":
                rows = [x for x in rows if x["family"] == family]
            if not rows:
                continue
            lines += [f"**{r.info['corpus']}**", ""]
            cols = [c for c in MAIN_COLUMNS if any(c[1] in x for x in rows)]
            lines += table(rows, cols) + [""]
    lines += [
        "",
        "## Bands: operating points",
        "",
        "On the recommended scoring. Keep: the share of the best-scored candidates in which a "
        "multi-word phrase is kept; low score: the least specific share set aside; fragments: "
        "the part-of rule (the share of a candidate's occurrences inside one longer candidate "
        "that makes it a fragment); common modifiers: the rule that sends a phrase with a "
        "widespread edge adjective to check.",
        "",
    ]
    for r in results:
        lines += [f"**{r.info['corpus']}**", ""]
        lines += table(
            r.bands,
            [
                c
                for c in MAIN_COLUMNS
                if c[1] not in ("jaccard", "spearman", "ari_topics", "ari_themes", "mix_cosine")
            ],
        ) + [""]
    lines += [
        "",
        "## Bands: what each rule catches",
        "",
        "Every rule on (the least specific tenth set aside, names when they are recognised); "
        "a candidate takes the first rule that fires. A rule that sets aside or sends to check "
        "is useful when the gold share of what it catches is well below that of every candidate.",
        "",
    ]
    lines += table(
        [x for r in results for x in r.reasons],
        [
            ("corpus", "corpus", ""),
            ("band", "band", ""),
            ("reason", "reason", ""),
            ("candidates", "candidates", "int"),
            ("gold", "gold", "int"),
            ("gold share", "gold_share", "pct"),
        ],
    )
    lines += [
        "",
        "## AI triage: routes and what they would cost",
        "",
        "On the recommended set. Tokens are estimated at four characters each; the prices are "
        "illustrative. The API routes send bare strings in batches of 150, as the engine's "
        "triage does; the handoff sends one bundle with the evidence of each term.",
        "",
    ]
    rows = [t for r in results for t in r.triage if "input_tokens" in t]
    cols = [
        ("corpus", "corpus", ""),
        ("route", "route", ""),
        ("what the judge sees", "scope", ""),
        ("terms", "terms", "int"),
        ("calls", "calls", "int"),
        ("input tokens", "input_tokens", "int"),
        ("output tokens", "output_tokens", "int"),
    ] + [(p.name, p.name, "") for p in PRICES]
    lines += table(rows, cols)
    lines += [
        "",
        "Fake judges (the final lexicon: the terms the judge accepts, and the kept band when "
        "the judge does not see it). The oracle accepts exactly the gold; the noisy judge "
        "gives 10 % of its answers wrong.",
        "",
    ]
    rows = [t for r in results for t in r.triage if "precision" in t]
    lines += table(
        rows,
        [
            ("corpus", "corpus", ""),
            ("judge", "route", ""),
            ("what the judge sees", "scope", ""),
            ("terms judged", "terms", "int"),
            ("precision", "precision", "pct"),
            ("recall", "recall", "pct"),
            ("F1", "f1", "pct"),
        ],
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare the lexicon's open choices.")
    parser.add_argument("--suite", choices=sorted(SUITES), default="quick")
    parser.add_argument("--corpora", help="comma-separated corpus names (default: the suite's)")
    parser.add_argument("--jobs", type=int, default=1, help="parsing worker processes")
    parser.add_argument("--out", type=Path, help="report file (default .cache/lexicon_lab/)")
    parser.add_argument("--json", type=Path, help="also write the raw numbers here")
    parser.add_argument("--fetch", action="store_true", help="download the public benchmarks")
    parser.add_argument(
        "--cache", type=Path, help="folder of the lab's parse cache (default .cache/lexicon_lab/)"
    )
    parser.add_argument(
        "--from-json", type=Path, help="write the report of numbers saved with --json, run nothing"
    )
    args = parser.parse_args(argv)
    if args.from_json is not None:
        saved = json.loads(args.from_json.read_text(encoding="utf-8"))
        results = [
            CorpusResult(None, d["info"], d["rows"], d["bands"], d["triage"], d.get("reasons", []))
            for d in saved["corpora"]
        ]
        text = report(results, saved["suite"], saved["seconds"], saved.get("peak_mb"))
        out = args.out or args.from_json.with_suffix(".md")
        out.write_text(text, encoding="utf-8")
        print(f"report: {out}")
        return 0
    if args.cache is not None:
        lab_analyses.CACHE = args.cache
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for noisy in ("cartolex", "numba", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if args.fetch:
        for name in corpora.SOURCES:
            print("fetching", name, corpora.fetch(name))
        corpora.prepare_scielo()
    specs = SUITES[args.suite]
    if args.corpora:
        wanted = [c.strip() for c in args.corpora.split(",")]
        specs = [s for s in SUITES["full"] if s.name in wanted]
    skipped = []
    for s in list(specs):
        if s.name in corpora.BENCHMARKS and not corpora.available(s.name):
            skipped.append(s.name)
            specs.remove(s)
    t0 = time.perf_counter()
    results = []
    for spec in specs:
        LOG.info("corpus %s", spec.name)
        results.append(run_corpus(spec, jobs=args.jobs, quick=args.suite == "quick"))
    seconds = time.perf_counter() - t0
    text = report(results, args.suite, seconds)
    if skipped:
        text += f"\nSkipped (not in the cache; run with --fetch): {', '.join(skipped)}\n"
    out = args.out or lab_analyses.CACHE / f"report-{args.suite}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if args.json:
        saved = {
            "suite": args.suite,
            "seconds": seconds,
            "peak_mb": peak_mb(),
            "corpora": [
                {
                    "info": r.info,
                    "rows": r.rows,
                    "bands": r.bands,
                    "triage": r.triage,
                    "reasons": r.reasons,
                }
                for r in results
            ],
        }
        args.json.write_text(json.dumps(saved, indent=1, default=str), encoding="utf-8")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
