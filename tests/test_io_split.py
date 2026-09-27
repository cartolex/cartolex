# SPDX-License-Identifier: MIT
"""N-stream corpus ingestion (io_helpers.load_documents_split_by_language).

The split now returns a ``{lang: [per-entity text]}`` dict keyed by the
configured corpus languages, so a third language (Portuguese) is routed to its
own stream instead of being silently dropped.
"""

from __future__ import annotations

from pathlib import Path

from cartolex.lexicon.io_helpers import load_documents_split_by_language


def test_split_returns_dict_keyed_by_corpus_language(workspace: Path, synthetic_corpus) -> None:
    reports_index, manual_index = synthetic_corpus
    docs_by_lang, meta = load_documents_split_by_language(
        [("reports", reports_index, None), ("manual", manual_index, None)],
        corpus_languages=("fr", "en"),
    )
    assert set(docs_by_lang) == {"fr", "en"}
    assert any(docs_by_lang["fr"])  # FR report docs routed to the fr stream
    assert any(docs_by_lang["en"])  # EN manual docs routed to the en stream
    # per-entity alignment preserved: one slot per merged entity in each stream
    assert len(docs_by_lang["fr"]) == len(docs_by_lang["en"]) == len(meta)


def test_portuguese_paragraphs_routed_not_dropped(workspace: Path) -> None:
    corpus = workspace / "automatic_data" / "corpus_manual"
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "pt_001.txt").write_text(
        "aprendizagem automática e redes neuronais para a classificação supervisionada "
        "de imagens digitais em grandes conjuntos de dados",
        encoding="utf-8",
    )
    idx = workspace / "manual_index.csv"
    idx.write_text(
        "last_name,first_name,unit,txt_path\n"
        "SynthP,ResearcherP,LAB_SYNTH,automatic_data/corpus_manual/pt_001.txt\n",
        encoding="utf-8",
    )
    docs_by_lang, _meta = load_documents_split_by_language(
        [("manual", idx, None)],
        corpus_languages=("pt", "en"),
    )
    assert "pt" in docs_by_lang
    assert any("aprendizagem" in d for d in docs_by_lang["pt"])
