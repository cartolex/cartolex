# SPDX-License-Identifier: MIT
"""What reaches the lexicon: the extraction's bands in the consolidation.

Without AI decisions, the set-aside band does not reach the vocabulary; the
to-check and kept bands do, and an explicitly kept set-aside term does. With
AI decisions, the acceptance gate alone decides, as before: the bands change
nothing. A raw table without a band column (an older run) is read whole.

All texts and terms are fabricated.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from cartolex.atlas.model_files import load_vectorizer
from cartolex.build.engine import _keyword_decisions
from cartolex.build.enginefiles import engine_paths
from cartolex.context import RunContext
from cartolex.lexicon import consolidation, run_pipeline_stage_3
from cartolex.lexicon.config import KeywordsConfig
from cartolex.lexicon.llm_triage import _load_global_terms
from cartolex.lexicon.scoring import BANDS, LEXICON_BANDS
from cartolex.project.tables import write_decision_csv

TEXTS = {
    "a.txt": "support vector machine and photons; the noise floor and a spectral band",
    "b.txt": "photons near the noise floor; support vector machine; spectral band again",
    "c.txt": "a spectral band of photons, a support vector machine, a noise floor",
}

#: (term, score_len, band, reason): one row per band, and a second set-aside term.
RAW = [
    ("support vector machine", 9.0, "kept", "multiword"),
    ("photons", 4.0, "check", "single-word"),
    ("noise floor", 5.0, "aside", "part-of: noise floor estimate"),
    ("spectral band", 3.0, "aside", "part-of: spectral band shift"),
]


def _workspace(ws: Path, *, bands: bool = True) -> RunContext:
    """A workspace ready for consolidation: three texts and an English raw table."""
    corpus = ws / "automatic_data" / "corpus_manual"
    corpus.mkdir(parents=True)
    rows = ["last_name,first_name,unit,txt_path"]
    for i, (name, text) in enumerate(TEXTS.items()):
        (corpus / name).write_text(text, encoding="utf-8")
        rows.append(f"Person{i},First{i},GROUP_A,automatic_data/corpus_manual/{name}")
    (ws / "manual_index.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    settings = KeywordsConfig(kw_recency_years=0, corpus_languages=("en",))
    ctx = RunContext.for_workspace(ws, settings, now_year=2026)
    table = pd.DataFrame(
        [
            {"term": t, "score": s, "len": len(t.split()), "score_len": s, "band": b, "reason": r}
            for t, s, b, r in RAW
        ]
    )
    if not bands:
        table = table.drop(columns=["band", "reason"])
    table.to_csv(ctx.paths.raw_terms_csv("en"), index=False)
    return ctx


def _keep(ctx: RunContext, *terms: str) -> None:
    ctx.paths.manual_keep_csv.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.manual_keep_csv.write_text("term\n" + "".join(f"{t}\n" for t in terms))


def _decide(ctx: RunContext, accepted: list[str]) -> None:
    others = sorted({t for t, *_ in RAW} - set(accepted))
    decisions = {"accepted": accepted, "rejected": others, "canonical_map": {}}
    ctx.paths.triage_decisions_json.write_text(json.dumps(decisions), encoding="utf-8")


def _run(ctx: RunContext) -> tuple[set[str], set[str], list[str]]:
    """The refined concepts, the vectorizer's vocabulary and the progress messages."""
    messages: list[str] = []
    run_pipeline_stage_3(ctx, progress_callback=lambda _pct, msg: messages.append(msg))
    concepts = set(pd.read_csv(ctx.paths.refined_terms_csv)["concept"].astype(str))
    vocabulary = set(load_vectorizer(ctx.paths.vectorizer_json).vocabulary_)
    return concepts, vocabulary, messages


def test_the_lexicon_bands_are_the_bands_the_triage_judges() -> None:
    assert LEXICON_BANDS == ("kept", "check")
    assert set(LEXICON_BANDS) < set(BANDS)
    assert _load_global_terms.__defaults__[-1] is LEXICON_BANDS


def test_without_ai_the_set_aside_band_does_not_reach_the_vocabulary(tmp_path) -> None:
    ctx = _workspace(tmp_path)
    concepts, vocabulary, messages = _run(ctx)

    assert {"support vector machine", "photons"} <= concepts
    assert {"support vector machine", "photons"} <= vocabulary
    assert "noise floor" not in concepts and "noise floor" not in vocabulary
    assert "spectral band" not in concepts and "spectral band" not in vocabulary
    assert "Band gate removed 2 set-aside terms (no LLM decisions)" in messages
    # The set-aside candidates stay in the raw table, with their band and reason.
    raw = pd.read_csv(ctx.paths.raw_terms_csv("en"))
    aside = raw[raw["band"] == "aside"]
    assert set(aside["term"]) == {"noise floor", "spectral band"}
    assert aside["reason"].str.startswith("part-of: ").all()


@pytest.mark.parametrize("written", ["spectral band", "Spectral Band "])
def test_an_explicit_keep_wins_over_the_set_aside_band(tmp_path, written) -> None:
    ctx = _workspace(tmp_path)
    _keep(ctx, written)
    concepts, vocabulary, messages = _run(ctx)

    assert "spectral band" in concepts and "spectral band" in vocabulary
    assert "noise floor" not in concepts and "noise floor" not in vocabulary
    assert "Band gate removed 1 set-aside terms (no LLM decisions)" in messages


def test_a_keep_in_the_project_keyword_decisions_wins_over_the_set_aside_band(tmp_path) -> None:
    # The build turns decisions/keywords.csv into the engine's keep list, which
    # the consolidation of keywords.build reads.
    project, out = tmp_path / "project", tmp_path / "project" / "staging"
    keywords_csv = project / "decisions" / "keywords.csv"
    keywords_csv.parent.mkdir(parents=True)
    write_decision_csv(
        keywords_csv,
        "keywords",
        [{"term": "Noise floor", "language": "en", "decision": "keep", "source": "person"}],
    )
    _keyword_decisions(SimpleNamespace(layout=SimpleNamespace(keywords_csv=keywords_csv), out=out))
    keep_csv = engine_paths("keywords.build", {"keywords.build": out}, project).manual_keep_csv
    ctx = _workspace(tmp_path / "ws")
    ctx = ctx.replace(paths=dataclasses.replace(ctx.paths, manual_keep_csv=keep_csv))
    concepts, vocabulary, _ = _run(ctx)

    assert "noise floor" in concepts and "noise floor" in vocabulary
    assert "spectral band" not in concepts and "spectral band" not in vocabulary


def test_an_older_table_without_bands_is_read_whole(tmp_path) -> None:
    ctx = _workspace(tmp_path, bands=False)
    concepts, vocabulary, messages = _run(ctx)

    assert {t for t, *_ in RAW} <= concepts
    assert {t for t, *_ in RAW} <= vocabulary
    assert not any(m.startswith("Band gate") for m in messages)


def test_with_ai_decisions_the_bands_change_nothing(tmp_path) -> None:
    accepted = ["support vector machine", "photons", "noise floor"]
    with_bands = _workspace(tmp_path / "bands")
    without_bands = _workspace(tmp_path / "plain", bands=False)
    for ctx in (with_bands, without_bands):
        _decide(ctx, accepted)
        _keep(ctx, "spectral band")

    concepts, vocabulary, messages = _run(with_bands)

    # The acceptance gate alone decides, set-aside or not: what the AI accepted
    # and what is kept explicitly, exactly as from a table without bands.
    assert concepts == set(accepted) | {"spectral band"}
    assert (concepts, vocabulary) == _run(without_bands)[:2]
    assert not any(m.startswith("Band gate") for m in messages)


def test_the_allowed_concepts_follow_every_raw_term_of_a_concept() -> None:
    raw = pd.DataFrame(
        {
            "term": ["tide gauge", "tide gauges", "gauge", "mixed layer", "Swash Zone"],
            "concept": ["tide gauge", "tide gauge", "gauge", "mixed layer", "swash zone"],
            "band": ["aside", "check", "aside", None, "aside"],
        }
    )
    # A concept passes when one of its raw terms may (tide gauges), when a row
    # has no band (an older table's), or when a raw term is kept explicitly.
    assert consolidation.band_allowed_concepts(raw, {"swash zone"}) == {
        "tide gauge",
        "mixed layer",
        "swash zone",
    }
    assert consolidation.band_allowed_concepts(raw.drop(columns=["band"]), set()) is None
