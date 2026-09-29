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
from cartolex.lexicon.scoring import BandRules, ScoringOptions, TextUnit, score_units  # noqa: E402


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
    # « data », used once by everyone, would be evenly spread: the rule is off here.
    rules = BandRules(even_spread=None)
    return {
        "en": score_units("en", units, 3, min_df=1, max_df=1.0, options=ScoringOptions(bands=rules))
    }


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
    assert "| demo XS | API | every candidate |" in text


# ── the browser handoff: files and answers ──────────────────────────────────


def _items(n: int = 5):
    words = ["tide gauge", "trait de côte", "data", "sea level", "recent decades", "wave"]
    return [
        handoff.BundleItem(
            term=words[i % len(words)] + ("" if i < len(words) else f" {i}"),
            lang="fr" if i % len(words) == 1 else "en",
            band="check",
            reason="single-word",
            people=3 + i,
            texts=1,
            specificity=0.5,
            forms=[],
            inside=["longer phrase"] if i == 0 else [],
        )
        for i in range(n)
    ]


def test_answers_are_read_in_every_form() -> None:
    items = _items(6)
    text = "\n".join(
        [
            "Here are my answers:",
            "```",
            "1 | C | tide gauge",
            "2 | O | Trait de cote | shoreline",  # case and accents may differ
            "| 3 | G | data |",  # a Markdown table row
            "99 | C | sea level | sea level",  # a wrong number, the term decides
            "5\tK\trecent decades",  # tab-separated
            "5 | G | recent decades",  # a second answer: the first counts
            "7 | C | nothing like it",  # fits no item
            "```",
            "M en wave=ocean wave",  # the triage's line format
        ]
    )
    parsed = handoff.parse_answer(text, items)
    v = parsed.verdicts
    assert v[0] == handoff.Verdict("C", "tide gauge")  # the English form defaults to the term
    assert v[1] == handoff.Verdict("O", "shoreline")
    assert v[2].code == "G" and not v[2].accept
    assert v[3] == handoff.Verdict("C", "sea level")
    assert v[4].code == "K"
    assert v[5] == handoff.Verdict("M", "ocean wave")
    assert (parsed.renumbered, parsed.duplicates, parsed.unmatched, parsed.ignored) == (1, 1, 1, 1)
    assert parsed.missing(len(items)) == 0
    # A number whose term is not in the list: the number decides, and the line is flagged.
    odd = handoff.parse_answer("2 | C | côte | coast", items)
    assert odd.verdicts == {1: handoff.Verdict("C", "coast")} and odd.term_mismatch == 1


def test_answer_lines_round_trip() -> None:
    items = _items(3)
    lines = [
        handoff.answer_line(1, items[0], "C", "tide gauge"),
        handoff.answer_line(2, items[1], "O", "shoreline"),
        handoff.answer_line(3, items[2], "G"),
    ]
    assert lines == ["1 | C | tide gauge", "2 | O | trait de côte | shoreline", "3 | G | data"]
    parsed = handoff.parse_answer("\n".join(lines), items)
    assert [parsed.verdicts[i].code for i in range(3)] == ["C", "O", "G"]


def test_parts_fit_and_carry_everything(tmp_path: Path) -> None:
    import json
    import zipfile

    items = _items(200)
    kw = {"domain": "Coastal systems", "description": "A test.", "n_people": 50, "n_texts": 80}
    parts = handoff.split_items(items, max_tokens=2_000, **kw)
    assert len(parts) > 1 and [it for p in parts for it in p] == items
    sizes = [
        handoff.write_part(
            tmp_path / f"p{k}", chunk, name=f"part-{k}", part=k, parts=len(parts), **kw
        )
        for k, chunk in enumerate(parts, 1)
    ]
    assert all(s["cautious_tokens"] <= 2_000 for s in sizes)
    assert max(s["items"] for s in sizes) - min(s["items"] for s in sizes) < 30  # balanced
    folder = tmp_path / "p1"
    prompt = (folder / "prompt.txt").read_text(encoding="utf-8")
    terms = (folder / "terms.txt").read_text(encoding="utf-8")
    assert "Coastal systems" in prompt and f"numbered 1 to {len(parts[0])}" in prompt
    # A process or property of an object is a keyword; F is only for broken pieces.
    assert "6 | C | repliement des protéines | protein folding" in prompt
    assert "8 | F | matter physics" in prompt and "F is only for" in prompt
    assert f"(part 1 of {len(parts)})" in terms
    assert "1. tide gauge [en] — 3 people, 1 text — in: longer phrase" in terms
    assert "single-word" not in terms and "check" not in terms  # no band in the judge's list
    with zipfile.ZipFile(folder / "part-1.zip") as zf:
        assert sorted(zf.namelist()) == [
            "part-1/expected-answer.txt",
            "part-1/prompt.txt",
            "part-1/terms.txt",
        ]
    record, back = handoff.load_part(folder)
    assert back == parts[0] and record["format"] == handoff.HANDOFF_FORMAT
    assert json.loads((folder / "bundle.json").read_text())["items"][0]["number"] == 1


@pytest.mark.models("en", "fr")
def test_the_handoff_test_is_written_and_scored(tmp_path: Path, monkeypatch) -> None:
    analyses = importlib.import_module("analyses")
    handoff_bundles = importlib.import_module("handoff_bundles")
    score_handoff = importlib.import_module("score_handoff")
    monkeypatch.setattr(analyses, "CACHE", tmp_path / "lab-cache")
    out = tmp_path / "handoff-test"
    manifest = handoff_bundles.write_handoff_test(
        out, size="XS", seed=0, project=tmp_path / "project", max_tokens=2_500
    )
    kept = manifest["bundles"]["kept-tocheck"]
    assert len(kept["parts"]) > 1 and all(p["cautious_tokens"] <= 2_500 for p in kept["parts"])
    assert len(manifest["bundles"]["tocheck"]["parts"]) == 1
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "claude.ai" in readme and "score_handoff.py" in readme

    first = score_handoff.score_test(out)
    assert first["bundles"]["tocheck"]["judges"]["answers"] is None
    oracle = first["bundles"]["tocheck"]["judges"]["oracle"]
    assert oracle["precision"] == 1.0 and oracle["recall"] == 1.0

    # The oracle's answer, saved where a person would save it, scores as the oracle.
    manifest_, bundles = score_handoff.load_test(out)
    truth = score_handoff.load_truth(manifest_)
    part = bundles["kept-tocheck"][0]
    (part.folder / "answer.txt").write_text(
        "```\n" + score_handoff.oracle_answer(part.items, truth) + "\n```\n", encoding="utf-8"
    )
    scores = score_handoff.score_test(out, truth=truth)["bundles"]["kept-tocheck"]
    answers, oracle = scores["judges"]["answers"], scores["judges"]["oracle"]
    assert scores["answered_parts"] == 1 and answers["judged"] == len(part.items)
    for key in ("precision", "recall", "final_precision", "final_recall"):
        assert answers[key] == pytest.approx(oracle[key])
    assert all(c["same_form"] == 1.0 for c in answers["canonical"].values() if c["accepted_gold"])
    assert "Handoff test: scores" in score_handoff.report(
        score_handoff.score_test(out, truth=truth)
    )

    # A second set under a suffix leaves the first one, and its answer, as they are.
    before = (part.folder / "answer.txt").read_bytes()
    again = handoff_bundles.write_handoff_test(
        out, size="XS", seed=0, project=tmp_path / "project", max_tokens=2_500, suffix="-v2"
    )
    assert set(again["bundles"]) == {"tocheck", "kept-tocheck", "tocheck-v2", "kept-tocheck-v2"}
    assert (part.folder / "answer.txt").read_bytes() == before
    assert again["bundles"]["tocheck-v2"]["prompt_version"] == handoff.PROMPT_VERSION
    both = score_handoff.score_test(out, truth=truth)
    assert set(both["bundles"]) == set(again["bundles"])
    only = score_handoff.score_test(out, truth=truth, only=["kept-tocheck-v2"])
    assert list(only["bundles"]) == ["kept-tocheck-v2"]
    assert only["bundles"]["kept-tocheck-v2"]["judges"]["answers"] is None
    with pytest.raises(SystemExit):  # the answered set is never overwritten
        handoff_bundles.write_handoff_test(
            out, size="XS", seed=0, project=tmp_path / "project", max_tokens=2_500
        )


def test_bundles_can_interleave_the_languages() -> None:
    en = _scored()["en"]
    scored = {"fr": en, "en": en}  # the same candidates twice, as two languages
    one_after = [it.lang for it in handoff.bundle(scored, bands=("kept", "check")).items]
    n = len(one_after) // 2
    assert one_after == ["fr"] * n + ["en"] * n
    mixed = handoff.bundle(scored, bands=("kept", "check"), interleave=True).items
    assert [it.lang for it in mixed] == ["fr", "en"] * n
    assert [it.term for it in mixed[::2]] == [it.term for it in mixed[1::2]]
