# SPDX-License-Identifier: MIT
"""The corpus slot registry and the keyword windows (recency, document types).

All names and text are fabricated.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from cartolex.context import EnginePaths, RunContext
from cartolex.lexicon.config import CorpusSlot, KeywordsConfig, SettingsError
from cartolex.lexicon.io_helpers import (
    load_documents_selected,
    load_documents_split_by_language,
    slot_indexes,
)


def test_defaults_one_manual_slot_and_a_recency_window() -> None:
    cfg = KeywordsConfig()
    assert cfg.kw_recency_years == 5
    assert cfg.corpus_slots == (CorpusSlot("manual"),)
    assert cfg.fit_slots == cfg.trajectory_slots == (CorpusSlot("manual"),)


def test_slots_are_coerced_and_checked() -> None:
    cfg = KeywordsConfig(
        corpus_slots=(
            "reports",
            {"id": "papers", "trajectory": False, "doc_types": ["Article", " review "]},
            CorpusSlot("history", fit=False),
        )
    )
    assert [s.id for s in cfg.corpus_slots] == ["reports", "papers", "history"]
    assert cfg.corpus_slots[1].doc_types == ("article", "review")
    # An empty type list is no filter at all.
    assert CorpusSlot("x", doc_types=()).doc_types is None
    assert CorpusSlot("x", doc_types=[" "]).doc_types is None
    assert [s.id for s in cfg.fit_slots] == ["reports", "papers"]
    assert [s.id for s in cfg.trajectory_slots] == ["reports", "history"]
    assert cfg.corpus_slots[1].as_dict() == {
        "id": "papers",
        "fit": True,
        "trajectory": False,
        "doc_types": ["article", "review"],
    }


@pytest.mark.parametrize(
    "slots, message",
    [
        ((), "at least one slot with fit=True"),
        ((CorpusSlot("history", fit=False),), "at least one slot with fit=True"),
        (("a", "a"), "repeats the slot id"),
        ("manual", "sequence of corpus slots"),
        ((42,), "Not a corpus slot"),
    ],
)
def test_unusable_slot_registries_are_refused(slots, message: str) -> None:
    with pytest.raises(SettingsError, match=message):
        KeywordsConfig(corpus_slots=slots)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"id": "../up"}, "must start with a letter or digit"),
        ({"id": ""}, "must start with a letter or digit"),
        ({"id": "idle", "fit": False, "trajectory": False}, "read by no stage"),
        ({"id": "x", "doc_types": "article"}, "not a string"),
    ],
)
def test_unusable_slots_are_refused(kwargs: dict, message: str) -> None:
    with pytest.raises(SettingsError, match=message):
        CorpusSlot(**kwargs)


def test_the_workspace_layout_derives_slot_files_from_the_id(tmp_path: Path) -> None:
    paths = EnginePaths.for_workspace(tmp_path)
    assert paths.corpus_index_csv("papers") == tmp_path / "papers_index.csv"
    assert paths.corpus_text_dir("papers") == tmp_path / "automatic_data" / "corpus_papers"
    settings = KeywordsConfig(
        corpus_slots=(CorpusSlot("papers"), CorpusSlot("history", fit=False)), domain_title="D"
    )
    ctx = RunContext.for_workspace(tmp_path, settings)
    assert slot_indexes(ctx) == [("papers", tmp_path / "papers_index.csv", None)]
    assert [s for s, _, _ in slot_indexes(ctx, trajectory=True)] == ["papers", "history"]


def _write_index(path: Path, corpus_dir: Path, rows: list[dict], source: str) -> None:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for i, r in enumerate(rows):
        fname = f"{source}_{i}.txt"
        (corpus_dir / fname).write_text(r["text"], encoding="utf-8")
        records.append(
            {
                "last_name": r.get("last_name", "P"),
                "first_name": r.get("first_name", "Q"),
                "unit": "LAB",
                "doc_year": r.get("doc_year", ""),
                "doc_type": r.get("doc_type", ""),
                "source": source,
                "txt_path": str(corpus_dir.relative_to(path.parent) / fname),
            }
        )
    pd.DataFrame(records).to_csv(path, index=False)


def _two_slots(workspace: Path) -> tuple[Path, Path]:
    auto = workspace / "automatic_data"
    reports, papers = workspace / "reports_index.csv", workspace / "papers_index.csv"
    _write_index(
        reports,
        auto / "corpus_reports",
        [
            {"doc_year": 2024, "doc_type": "annual", "text": "annualterm"},
            {"doc_year": 2024, "doc_type": "admin", "text": "adminterm"},
            {"doc_year": 2024, "doc_type": "", "text": "untypedterm"},
            {"doc_year": 2010, "doc_type": "annual", "text": "oldterm"},
        ],
        "reports",
    )
    _write_index(
        papers,
        auto / "corpus_papers",
        [{"doc_year": 2024, "doc_type": "thesis", "text": "thesisterm"}],
        "papers",
    )
    return reports, papers


def test_doc_types_filter_their_own_slot_only(workspace: Path) -> None:
    """A slot's document types never filter another slot; untyped documents pass."""
    reports, papers = _two_slots(workspace)
    docs, _meta = load_documents_selected(
        [("reports", reports, ("annual",)), ("papers", papers, None)],
        now_year=2026,
        recency_years=5,
    )
    blob = "\n".join(docs)
    assert "annualterm" in blob and "untypedterm" in blob
    assert "adminterm" not in blob  # filtered by its slot's types
    assert "thesisterm" in blob  # the other slot is not type-filtered
    assert "oldterm" not in blob  # the recency window applies to every slot


def test_slot_order_is_document_order(workspace: Path) -> None:
    reports, papers = _two_slots(workspace)
    first, _ = load_documents_selected([("reports", reports, None), ("papers", papers, None)])
    second, _ = load_documents_selected([("papers", papers, None), ("reports", reports, None)])
    assert first[0].index("annualterm") < first[0].index("thesisterm")
    assert second[0].index("thesisterm") < second[0].index("annualterm")


def test_doc_types_filter_in_the_split_loader(workspace: Path) -> None:
    """The same per-slot filter through the extraction loader."""
    auto = workspace / "automatic_data"
    notes = workspace / "notes_index.csv"
    _write_index(
        notes,
        auto / "corpus_notes",
        [
            {
                "doc_year": 2024,
                "doc_type": "thesis",
                "text": "aprendizagem de estruturas em redes para dados",
            },
            {"doc_year": 2024, "doc_type": "memo", "text": "memorando interno sem valor"},
        ],
        "notes",
    )
    docs_by_lang, _meta = load_documents_split_by_language(
        [("notes", notes, ("thesis",)), ("absent", workspace / "absent_index.csv", None)],
        corpus_languages=("pt", "en"),
        now_year=2026,
        recency_years=5,
    )
    assert any("aprendizagem" in d for d in docs_by_lang["pt"])
    assert not any("memorando" in d for d in docs_by_lang["pt"])
