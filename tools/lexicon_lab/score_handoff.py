# SPDX-License-Identifier: MIT
"""Score the answers of a browser-handoff test against the demo world's truth.

Usage::

    python tools/lexicon_lab/score_handoff.py ~/cartolex-work/handoff-test
    python tools/lexicon_lab/score_handoff.py ~/cartolex-work/handoff-test --answers "blind*.txt"

The test folder is written by :mod:`handoff_bundles`; an answer is saved next
to each bundle part as ``answer.txt`` (or ``answer-1.txt``, ``answer-2.txt`` …:
every file matching ``--answers`` is read, in name order). The truth is
computed here, in memory, from the demo world (the lab's gold: the world's
field terms, matched through loose keys), and nothing is written into the
test folder: the report goes to ``.cache/lexicon_lab/handoff-score.md``.

For each bundle (``tocheck``, ``kept-tocheck``) and each judge — the answers,
the lab's oracle (it accepts exactly the gold, under its true English form)
and a noisy oracle (10 % of its answers wrong) — the report gives:

- how the answers were read (lines, ignored lines, missing items …);
- on the judged terms: precision and recall of the accepted terms against the
  field terms, and the share of terms where the judge and the truth agree;
- the final lexicon, as the lab measures it: the terms accepted, plus the
  kept band when the judge did not see it; recall against the reachable gold;
- the agreement on English forms: for the field terms accepted, whether the
  judge's English form is the truth's (same words in the same order, or in
  any order), French and English terms apart.

Parts without an answer are left out of the judged terms; in the final
lexicon their terms keep their band (kept in, to check out).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import handoff  # noqa: E402
import measures  # noqa: E402
import pandas as pd  # noqa: E402

LOG = logging.getLogger("score_handoff")
DEFAULT_REPORT = ROOT / ".cache" / "lexicon_lab" / "handoff-score.md"
NOISE = 0.1
EXAMPLES = 30


@dataclass
class Part:
    """One bundle part: its folder, items, and the answer given (if any)."""

    folder: Path
    record: dict
    items: list[handoff.BundleItem]
    answer_files: list[Path] = field(default_factory=list)
    parsed: handoff.ParsedAnswer | None = None
    note: str = ""

    @property
    def answered(self) -> bool:
        return bool(self.answer_files)


def load_test(folder: Path, pattern: str = "answer*.txt") -> tuple[dict, dict[str, list[Part]]]:
    """The manifest and the parts of each bundle, with their answers read."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    bundles: dict[str, list[Part]] = {}
    for scope in manifest["bundles"]:
        parts = []
        for bundle_json in sorted((folder / scope).rglob("bundle.json")):
            record, items = handoff.load_part(bundle_json.parent)
            part = Part(bundle_json.parent, record, items)
            part.answer_files = sorted(bundle_json.parent.glob(pattern))
            if part.answer_files:
                text = "\n".join(p.read_text(encoding="utf-8") for p in part.answer_files)
                part.parsed = handoff.parse_answer(text, items)
            judge = bundle_json.parent / "judge.txt"
            if judge.exists():
                part.note = judge.read_text(encoding="utf-8").strip()
            parts.append(part)
        bundles[scope] = sorted(parts, key=lambda p: p.record.get("part", 1))
    return manifest, bundles


@dataclass
class Truth:
    """The lab's gold of the demo world, and the build's bands of every candidate."""

    matchers: dict
    golds: dict
    tables: dict[str, pd.DataFrame]

    def key(self, item: handoff.BundleItem) -> tuple[str, ...]:
        return self.matchers[item.lang].key(item.term)

    def is_gold(self, item: handoff.BundleItem) -> bool:
        k = self.key(item)
        return bool(k) and k in self.golds[item.lang].all

    def canonical(self, item: handoff.BundleItem) -> str:
        return self.golds[item.lang].canonical.get(self.key(item), "")

    def english_key(self, text: str) -> tuple[str, ...]:
        return self.matchers["en"].key(text) if "en" in self.matchers else tuple(text.split())


def load_truth(manifest: dict) -> Truth:
    """The world's gold (computed now, in memory) and the build's raw tables."""
    import analyses as lab_analyses
    import corpora
    import handoff_bundles
    import run

    world = manifest["world"]
    languages = ",".join(sorted(world["languages"], key=lambda x: (x != "en", x)))
    corpus = corpora.demo_corpus(world["size"], world["seed"], languages=languages)
    parsed = lab_analyses.parse(corpus, n_jobs=1, names=False)
    matchers, golds = run.golds_of(parsed)
    project = Path(manifest["build"]["project"])
    handoff_bundles.build_project(world["size"], world["seed"], project)
    tables = {
        lang: pd.read_csv(
            project / "derived" / "keywords.extract" / f"raw_keywords_{lang}.csv",
            keep_default_na=False,
        )
        for lang in world["languages"]
    }
    return Truth(matchers, golds, tables)


def oracle_verdicts(items, truth: Truth, *, error: float = 0.0, seed: int = 7) -> dict:
    """The oracle's verdicts by index (with *error*, a noisy oracle's)."""
    rng = random.Random(seed)
    out = {}
    for i, it in enumerate(items):
        gold = truth.is_gold(it)
        if error and rng.random() < error:
            gold = not gold
        english = truth.canonical(it) or it.term
        out[i] = handoff.Verdict("C", english) if gold else handoff.Verdict("G")
    return out


def oracle_answer(items, truth: Truth) -> str:
    """The oracle's answer as text, in the handoff format (for tests of the scoring)."""
    verdicts = oracle_verdicts(items, truth)
    return "\n".join(
        handoff.answer_line(i + 1, it, verdicts[i].code, verdicts[i].canonical)
        for i, it in enumerate(items)
    )


def _ratio(a: float, b: float) -> float:
    return a / b if b else float("nan")


def score(parts: list[Part], judged: list[dict | None], truth: Truth) -> dict:
    """The measures of one judge on one bundle; *judged* gives each part's verdicts or None."""
    n = accepted = gold = tp = agree = 0
    codes: Counter[str] = Counter()
    canon = {lang: [0, 0, 0] for lang in truth.matchers}  # [accepted gold, exact, same words]
    wrong_accepts, missed_gold, canon_misses = [], [], []
    decided: dict[tuple[str, str], bool] = {}  # (lang, term) -> accepted, for judged parts
    for part, verdicts in zip(parts, judged, strict=True):
        if verdicts is None:
            continue
        for i, it in enumerate(part.items):
            v = verdicts.get(i)
            is_gold = truth.is_gold(it)
            ok = v is not None and v.accept
            n += 1
            codes[v.code if v is not None else "missing"] += 1
            accepted += ok
            gold += is_gold
            tp += ok and is_gold
            agree += ok == is_gold
            decided[(it.lang, it.term)] = ok
            if ok and not is_gold and len(wrong_accepts) < EXAMPLES:
                wrong_accepts.append((it.term, it.lang, v.code, v.canonical))
            if is_gold and not ok and len(missed_gold) < EXAMPLES:
                missed_gold.append((it.term, it.lang, v.code if v else "missing"))
            if ok and is_gold:
                cell = canon[it.lang]
                cell[0] += 1
                mine, true = truth.english_key(v.canonical), truth.english_key(truth.canonical(it))
                cell[1] += mine == true
                cell[2] += sorted(mine) == sorted(true)
                if mine != true and len(canon_misses) < EXAMPLES:
                    canon_misses.append((it.term, it.lang, v.canonical, truth.canonical(it)))
    precision, recall = _ratio(tp, accepted), _ratio(tp, gold)
    counts = []
    for lang, table in truth.tables.items():
        t = table.copy()
        t["band"] = [
            ("kept" if decided[(lang, term)] else "aside")
            if (lang, term) in decided
            else ("kept" if band == "kept" else "aside")
            for term, band in zip(t["term"], t["band"], strict=True)
        ]
        counts.append(measures.evaluate(t, truth.matchers[lang], truth.golds[lang]))
    final = measures.ratios(measures.summed(counts))
    return {
        "judged": n,
        "answered": n - codes["missing"],
        "accepted": accepted,
        "gold": gold,
        "precision": precision,
        "recall": recall,
        "f1": _ratio(2 * precision * recall, precision + recall),
        "agreement": _ratio(agree, n),
        "codes": dict(codes),
        "final_precision": final["precision"],
        "final_recall": final["recall"],
        "final_f1": final["f1"],
        "final_terms": sum(c["final"] for c in counts),
        "canonical": {
            lang: {
                "accepted_gold": c[0],
                "same_form": _ratio(c[1], c[0]),
                "same_words": _ratio(c[2], c[0]),
            }
            for lang, c in canon.items()
        },
        "examples": {
            "accepted, not field terms": wrong_accepts,
            "field terms not accepted": missed_gold,
            "English form differs from the truth": canon_misses,
        },
    }


def score_test(folder: Path, pattern: str = "answer*.txt", truth: Truth | None = None) -> dict:
    """Every bundle of the test folder, scored for the answers, the oracle and a noisy oracle."""
    manifest, bundles = load_test(folder, pattern)
    truth = truth or load_truth(manifest)
    out: dict = {"folder": str(folder), "answers": pattern, "bundles": {}}
    for scope, parts in bundles.items():
        answered = [p for p in parts if p.answered]
        pool = answered or parts  # nothing answered yet: the oracles judge every part
        judges = {
            "answers": [p.parsed.verdicts if p.answered else None for p in parts],
            "oracle": [oracle_verdicts(p.items, truth) if p in pool else None for p in parts],
            f"noisy oracle {NOISE:.0%}": [
                oracle_verdicts(p.items, truth, error=NOISE, seed=7 + k) if p in pool else None
                for k, p in enumerate(parts)
            ],
        }
        results = {
            name: score(parts, v, truth) if any(x is not None for x in v) else None
            for name, v in judges.items()
        }
        reading = [
            {
                "part": p.record.get("part", 1),
                "folder": str(p.folder.relative_to(folder)),
                "items": len(p.items),
                "files": [f.name for f in p.answer_files],
                "judge": p.note,
                **(
                    {
                        "lines": p.parsed.lines,
                        "ignored": p.parsed.ignored,
                        "unmatched": p.parsed.unmatched,
                        "renumbered": p.parsed.renumbered,
                        "term_mismatch": p.parsed.term_mismatch,
                        "duplicates": p.parsed.duplicates,
                        "missing": p.parsed.missing(len(p.items)),
                    }
                    if p.parsed is not None
                    else {}
                ),
            }
            for p in parts
        ]
        out["bundles"][scope] = {
            "parts": len(parts),
            "answered_parts": len(answered),
            "items": sum(len(p.items) for p in parts),
            "reading": reading,
            "judges": results,
        }
    return out


# ── the report ──────────────────────────────────────────────────────────────


def _pct(x: float) -> str:
    return "–" if x != x else f"{100 * x:.1f} %"


def report(scores: dict) -> str:
    lines = [
        "# Handoff test: scores",
        "",
        f"Test folder `{scores['folder']}`, answers read from `{scores['answers']}`. "
        "Judged terms: the terms of the parts that have an answer (every part for the "
        "oracles when none has). Final lexicon: accepted terms, plus the kept band where "
        "the judge did not see it; recall against the field terms at least 3 people use.",
        "",
    ]
    for scope, b in scores["bundles"].items():
        lines += [
            f"## {scope}",
            "",
            f"{b['items']:,} terms in {b['parts']} part(s); {b['answered_parts']} answered.",
            "",
            "| judge | judged | answered | accepted | field terms | precision | recall | F1 | "
            "agreement | final precision | final recall | final F1 | English form, fr | "
            "same words, fr | English form, en |",
            "|" + "---|" * 15,
        ]
        for name, r in b["judges"].items():
            if r is None:
                lines.append(f"| {name} | no answer yet |" + " |" * 13)
                continue
            fr = r["canonical"].get("fr", {})
            en = r["canonical"].get("en", {})
            lines.append(
                f"| {name} | {r['judged']:,} | {r['answered']:,} | {r['accepted']:,} | "
                f"{r['gold']:,} | {_pct(r['precision'])} | {_pct(r['recall'])} | "
                f"{_pct(r['f1'])} | {_pct(r['agreement'])} | {_pct(r['final_precision'])} | "
                f"{_pct(r['final_recall'])} | {_pct(r['final_f1'])} | "
                f"{_pct(fr.get('same_form', float('nan')))} | "
                f"{_pct(fr.get('same_words', float('nan')))} | "
                f"{_pct(en.get('same_form', float('nan')))} |"
            )
        lines += [
            "",
            "Reading the answers:",
            "",
            "| part | terms | files | lines read | other lines | unmatched | renumbered | "
            "term differs | repeated | missing | judge |",
            "|" + "---|" * 11,
        ]
        for x in b["reading"]:
            if not x["files"]:
                lines.append(f"| {x['part']} | {x['items']:,} | none |" + " |" * 8)
                continue
            lines.append(
                f"| {x['part']} | {x['items']:,} | {', '.join(x['files'])} | {x['lines']:,} | "
                f"{x['ignored']:,} | {x['unmatched']:,} | {x['renumbered']:,} | "
                f"{x['term_mismatch']:,} | {x['duplicates']:,} | {x['missing']:,} | "
                f"{x['judge']} |"
            )
        answers = b["judges"].get("answers")
        if answers is not None:
            codes = ", ".join(f"{k} {v:,}" for k, v in sorted(answers["codes"].items()))
            lines += ["", f"Codes given: {codes}.", ""]
            for title, rows in answers["examples"].items():
                if not rows:
                    continue
                lines += [f"**{title.capitalize()}** (first {EXAMPLES}):", ""]
                lines += ["- " + " · ".join(str(c) for c in row if c != "") for row in rows]
                lines.append("")
        lines.append("")
    return "\n".join(lines)


def summary(scores: dict) -> str:
    rows = []
    for scope, b in scores["bundles"].items():
        for name, r in b["judges"].items():
            if r is None:
                rows.append(f"{scope:13s} {name:18s} no answer yet")
                continue
            fr = r["canonical"].get("fr", {}).get("same_form", float("nan"))
            rows.append(
                f"{scope:13s} {name:18s} answered {r['answered']:>6,}/{r['judged']:<6,} "
                f"P {_pct(r['precision']):>8s}  R {_pct(r['recall']):>8s}  "
                f"final P {_pct(r['final_precision']):>8s} R {_pct(r['final_recall']):>8s}  "
                f"English form (fr) {_pct(fr)}"
            )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the answers of a handoff test.")
    parser.add_argument("folder", type=Path, help="the test folder (with manifest.json)")
    parser.add_argument("--answers", default="answer*.txt", help="answer files, in each part")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT, help="the Markdown report")
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    folder = args.folder.expanduser().resolve()
    out = args.out.expanduser().resolve()
    if folder in out.parents:
        raise SystemExit("score: write the report outside the test folder, which stays blind")
    scores = score_test(folder, args.answers)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report(scores), encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(scores, ensure_ascii=False, indent=1), encoding="utf-8")
    print(summary(scores))
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
