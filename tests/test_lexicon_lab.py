# SPDX-License-Identifier: MIT
"""The lexicon lab's measures, handoff bundle and judges (``tools/lexicon_lab``), model-free."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

LAB = Path(__file__).resolve().parent.parent / "tools" / "lexicon_lab"
sys.path.insert(0, str(LAB))
measures = importlib.import_module("measures")
handoff = importlib.import_module("handoff")
variants = importlib.import_module("variants")

from cartolex.lexicon.noun_phrases import TextAnalysis  # noqa: E402
from cartolex.lexicon.scoring import TextUnit, score_units  # noqa: E402


def test_loose_keys_ignore_articles_accents_and_inflection() -> None:
    fr = measures.Matcher("fr", {"traits": "trait", "côtes": "côte"})
    assert fr.key("Le trait de côte") == fr.key("les traits de côtes") == ("trait", "cote")
    en = measures.Matcher("en", {"gauges": "gauge"})
    assert en.key("tide gauges") == ("tide", "gauge")
    pt = measures.Matcher("pt", {})
    assert pt.key("linha da costa") == pt.key("a linha de costa") == ("linha", "costa")


def test_stemmed_gold_matches_by_stems() -> None:
    pytest.importorskip("snowballstemmer")
    m = measures.Matcher("en", {}, stem=True)
    assert m.key("enhanced web search") == m.key("enhanc web search")


def test_people_with_counts_people_not_occurrences() -> None:
    m = measures.Matcher("en", {})
    keys = {("tide", "gauge"), ("wave",)}
    found = measures.people_with(
        keys,
        {0: ["A tide gauge and a tide gauge."], 1: ["The wave.", "Tide gauge records."], 2: []},
        m,
    )
    assert found == {("tide", "gauge"): 2, ("wave",): 1}


def _table(rows):
    return pd.DataFrame(rows, columns=["term", "band", "score_len"])


def test_evaluate_and_ratios() -> None:
    m = measures.Matcher("en", {})
    gold = measures.Gold(
        all={("tide", "gauge"), ("sea", "level"), ("wave",)},
        shared={("tide", "gauge"), ("sea", "level")},
        canonical={},
        themes={},
    )
    table = _table(
        [
            ("tide gauge", "kept", 3.0),  # gold, kept
            ("recent approach", "kept", 2.0),  # not gold, kept
            ("sea level", "check", 1.0),  # gold, accepted by the oracle
            ("data", "check", 0.5),  # not gold, rejected
            ("wave", "aside", 0.1),  # gold, set aside
        ]
    )
    counts = measures.evaluate(table, m, gold)
    assert (counts["kept"], counts["check"], counts["aside"]) == (2, 2, 1)
    assert counts["gold_set_aside"] == 1 and counts["final"] == 3 and counts["found"] == 2
    r = measures.ratios(counts)
    assert r["precision"] == pytest.approx(2 / 3)
    assert r["precision_kept"] == pytest.approx(1 / 2)
    assert r["recall"] == pytest.approx(1.0)
    assert r["ai_load"] == 2
    both = measures.ratios(measures.summed([counts, counts]))
    assert both["precision"] == pytest.approx(2 / 3)
    assert set(measures.final_keys(table, m, gold)) == {
        ("tide", "gauge"),
        ("recent", "approach"),
        ("sea", "level"),
    }


def test_stability_helpers() -> None:
    assert measures.jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3)
    assert measures.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    units = {"en": [TextUnit(i, "g", f"t{i}", ()) for i in range(20)]}
    sub = measures.subsample(units, 0.1, seed=1)
    assert len(sub["en"]) == 18
    assert measures.subsample(units, 0.1, seed=1) == sub


def _scored():
    a = TextAnalysis(runs=((("tide", "N"), ("gauge", "N")), (("data", "N"),)), lemmas=())
    units = [TextUnit(i, "g", f"t{i}", (("full", (a,)),)) for i in range(3)]
    return {"en": score_units("en", units, 3, min_df=1, max_df=1.0)}


def test_bundle_items_carry_their_evidence() -> None:
    scored = _scored()
    b = handoff.bundle(scored, bands=("check", "kept"), domain="Coastal systems")
    by_term = {it.term: it for it in b.items}
    assert set(by_term) == {"tide gauge", "data", "tide", "gauge"} - {
        t for t in ("tide", "gauge") if scored["en"].candidates[t].band == "aside"
    }
    item = by_term["data"]
    assert (item.lang, item.band, item.reason, item.people, item.texts) == (
        "en",
        "check",
        "single-word",
        3,
        3,
    )
    text = b.to_text()
    assert "Coastal systems" in text and "1. " in text and "data [en]" in text
    assert '"format": "cartolex-handoff/0"' in b.to_json()


def test_judges() -> None:
    b = handoff.bundle(_scored(), bands=("check", "kept"), domain="x")

    def is_gold(it):
        return it.term == "tide gauge"

    oracle = handoff.OracleJudge(is_gold).judge(b)
    assert oracle["tide gauge"].accept and not oracle["data"].accept
    noisy = handoff.NoisyJudge(is_gold, error=1.0).judge(b)
    assert not noisy["tide gauge"].accept and noisy["data"].accept
    replay = handoff.ReplayJudge("1. C en tide gauge=tide gauge\nG data\nnoise line").judge(b)
    assert replay["tide gauge"] == handoff.Verdict("C", "tide gauge")
    assert replay["data"].code == "G"
    assert all(v.code == "F" for t, v in replay.items() if t not in ("tide gauge", "data"))


def test_costs() -> None:
    api = handoff.api_cost(["tide gauge"] * 301, "x" * 400, batch_size=150)
    assert api["calls"] == 3
    assert api["input_tokens"] == 3 * 100 + sum(
        handoff.tokens(s)
        for s in ('["tide gauge"' + ', "tide gauge"' * (n - 1) + "]" for n in (150, 150, 1))
    )
    b = handoff.bundle(_scored(), bands=("check", "kept"), domain="x")
    cost = handoff.handoff_cost(b, extra_input_tokens=10)
    assert cost["calls"] == 1 and cost["input_tokens"] == handoff.tokens(b.to_text()) + 10
    price = handoff.Price("p", 1.0, 2.0)
    assert handoff.priced({"input_tokens": 1_000_000, "output_tokens": 500_000}, price) == 2.0


def test_usage_lines() -> None:
    lines = handoff.usage_lines(
        ["Nothing here.", "The tide gauge records show a trend over the period."],
        ["tide gauge"],
        width=10,
    )
    assert lines["tide gauge"] == ["The tide gauge records s…"]


def test_reasons_count_what_each_rule_catches() -> None:
    m = measures.Matcher("en", {})
    gold = measures.Gold(all={("tide", "gauge"), ("wave",)}, shared=set(), canonical={}, themes={})
    table = pd.DataFrame(
        [
            ("tide gauge", "kept", "multiword"),
            ("recent approach", "check", "common-modifier: recent"),
            ("wave", "check", "single-word"),
            ("gauge", "aside", "part-of: tide gauge"),
            ("vector machine", "aside", "part-of: support vector machine"),
        ],
        columns=["term", "band", "reason"],
    )
    assert measures.by_reason(table, m, gold) == {
        ("kept", "multiword"): [1, 1],
        ("check", "common-modifier"): [1, 0],
        ("check", "single-word"): [1, 1],
        ("aside", "part-of"): [2, 0],
    }


def test_variant_families_start_with_the_default() -> None:
    for family, options in variants.FAMILIES.items():
        assert "default" in options[0].label, family
        assert options[0].options == variants.BASE
    labels = [variants.band_variant(r).label for r in variants.BAND_POINTS]
    assert len(labels) == len(set(labels)) == 14


@pytest.mark.models("en", "fr")
def test_the_lab_runs_end_to_end(tmp_path: Path) -> None:
    run = importlib.import_module("run")
    out = tmp_path / "report.md"
    argv = ["--suite", "smoke", "--out", str(out), "--cache", str(tmp_path / "cache")]
    assert run.main(argv) == 0
    text = out.read_text(encoding="utf-8")
    for heading in (
        "## Corpora",
        "## Of complement",
        "## Bands: operating points",
        "## Bands: what each rule catches",
        "## AI triage",
    ):
        assert heading in text
    assert "demo XS" in text and "API, every candidate (today)" in text
