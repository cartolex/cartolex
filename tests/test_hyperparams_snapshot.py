# SPDX-License-Identifier: MIT
"""Stage-3 write site (``consolidation.py``): ``keywords_hyperparams.json`` must
snapshot the values from the RUN's ``KeywordsConfig`` — the recency window,
n-gram/df/consolidation cutoffs, and LLM-triage settings — not module defaults.
This lets an analyst reconstruct how an anomalous corpus was built years later
from a contribution-bundle manifest alone.

All corpus text and researcher identities below are fabricated.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from cartolex.context import RunContext
from cartolex.lexicon import consolidation
from cartolex.lexicon.config import KeywordsConfig


def _write_raw_keywords(auto_dir: Path) -> None:
    """Hand-craft Stage-1 output (raw_keywords_{fr,en}.csv) so Stage 3 can run
    without invoking the real extraction. Columns match extract_raw.py's
    actual output shape: term, score, len, score_len.
    """
    fr_rows = [
        {"term": "photons", "score": 4.0, "len": 7, "score_len": 8.0},
        {"term": "proteines", "score": 3.0, "len": 9, "score_len": 6.0},
    ]
    en_rows = [
        {"term": "classification", "score": 5.0, "len": 14, "score_len": 10.0},
        {"term": "proteins", "score": 2.0, "len": 8, "score_len": 4.0},
    ]
    pd.DataFrame(fr_rows).to_csv(auto_dir / "raw_keywords_fr.csv", index=False)
    pd.DataFrame(en_rows).to_csv(auto_dir / "raw_keywords_en.csv", index=False)


def test_hyperparams_snapshot_carries_run_config_values(workspace: Path, synthetic_corpus) -> None:
    """The persisted snapshot must reflect the run's KeywordsConfig, not fresh
    defaults re-read elsewhere — set a batch of non-default values and assert
    they round-trip through the write site into keywords_hyperparams.json.
    """
    _write_raw_keywords(workspace / "automatic_data")

    cfg = KeywordsConfig(
        corpus_slots=(
            {"id": "reports", "doc_types": ("annual", "review")},
            {"id": "manual", "trajectory": False},
        ),
        kw_recency_years=3,
        ngram_range=(1, 2),
        min_df=1,
        max_df=0.95,
        max_features=500,
        weights_basis="tfidf",
        length_bonus_alpha=1.5,
        global_top_n=42,
        refined_top_n=7,
        nested_threshold=1.1,
        top_n_researcher=5,
        top_n_unit=10,
        top_n_domain=20,
        use_llm=False,
        llm_model="test-triage-model",
        llm_min_score=0.25,
    )

    # Sanity check: every value above must actually differ from the
    # KeywordsConfig() defaults, otherwise this test could pass even if the
    # write site silently fell back to fresh defaults.
    defaults = KeywordsConfig()
    assert cfg.kw_recency_years != defaults.kw_recency_years
    assert cfg.corpus_slots != defaults.corpus_slots
    assert cfg.ngram_range != defaults.ngram_range
    assert cfg.min_df != defaults.min_df
    assert cfg.max_df != defaults.max_df
    assert cfg.max_features != defaults.max_features
    assert cfg.weights_basis != defaults.weights_basis
    assert cfg.length_bonus_alpha != defaults.length_bonus_alpha
    assert cfg.global_top_n != defaults.global_top_n
    # Distinct from BOTH the default and cfg.global_top_n — the legacy
    # "refined_top_n" snapshot key actually carries global_top_n (pre-existing
    # mislabel), so only a third value proves the true cutoff is persisted.
    assert cfg.refined_top_n != defaults.refined_top_n
    assert cfg.refined_top_n != cfg.global_top_n
    assert cfg.nested_threshold != defaults.nested_threshold
    assert cfg.top_n_researcher != defaults.top_n_researcher
    assert cfg.top_n_unit != defaults.top_n_unit
    assert cfg.top_n_domain != defaults.top_n_domain
    assert cfg.use_llm != defaults.use_llm
    assert cfg.llm_model != defaults.llm_model
    assert cfg.llm_min_score != defaults.llm_min_score

    consolidation.run_pipeline(RunContext.for_workspace(workspace, cfg))

    hyperparams_path = workspace / "automatic_data" / "keywords_hyperparams.json"
    data = json.loads(hyperparams_path.read_text(encoding="utf-8"))

    # The corpus slot registry, in order.
    assert data["corpus_slots"] == [
        {"id": "reports", "fit": True, "trajectory": True, "doc_types": ["annual", "review"]},
        {"id": "manual", "fit": True, "trajectory": False, "doc_types": None},
    ]
    assert data["refined_top_n"] == 42

    # The other snapshot keys, carrying the RUN's config values.
    assert data["snapshot_version"] == 3
    assert data["kw_recency_years"] == 3
    assert data["ngram_range"] == [1, 2]
    assert data["min_df"] == 1
    assert data["max_df"] == 0.95
    assert data["max_features"] == 500
    assert data["weights_basis"] == "tfidf"
    assert data["length_bonus_alpha"] == 1.5
    assert data["global_top_n"] == 42
    # The TRUE post-triage keep-top-N cutoff (cfg.refined_top_n), under an
    # honest name — the legacy "refined_top_n" key above carries global_top_n.
    assert data["triage_refined_top_n"] == 7
    assert data["nested_threshold"] == 1.1
    assert data["top_n_researcher"] == 5
    assert data["top_n_unit"] == 10
    assert data["top_n_domain"] == 20
    assert data["use_llm"] is False
    assert data["llm_model"] == "test-triage-model"
    assert data["llm_min_score"] == 0.25

    # snapshot_date: ISO calendar date of the run, so downstream window
    # reconstruction doesn't have to guess the run date from export date
    # (which is wrong across a year boundary).
    assert "snapshot_date" in data
    assert date.fromisoformat(data["snapshot_date"]) == date.today()

    # Plain JSON: scalars and lists of scalars, but for the slot registry (a
    # list of plain slot objects).
    for key, value in data.items():
        assert isinstance(value, (str, int, float, bool, list))
        if isinstance(value, list) and key != "corpus_slots":
            assert all(isinstance(v, (str, int, float, bool)) for v in value)
