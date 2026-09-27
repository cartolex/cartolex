# SPDX-License-Identifier: MIT
"""Engine errors are specific exceptions with an English message, never ``SystemExit``.

A consuming application catches them and tells the operator what to run; an
exit from inside a library call would stop its process instead.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from cartolex.context import RunContext
from cartolex.lexicon.config import KeywordsConfig
from cartolex.lexicon.consolidation import run_pipeline
from cartolex.lexicon.io_helpers import CorpusError, load_documents_selected, write_roster
from cartolex.lexicon.labels import fill_missing_label_sides
from cartolex.lexicon.llm_triage import _load_global_terms
from cartolex.lexicon.pdf_corpus import build_pdf_corpus, load_pdf_index


def test_consolidation_without_extraction(tmp_path: Path) -> None:
    ctx = RunContext.for_workspace(tmp_path, KeywordsConfig(domain_title="D"))
    with pytest.raises(FileNotFoundError, match="Run the extraction stage first"):
        run_pipeline(ctx)


def test_triage_without_global_terms(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="run the extraction stage first"):
        _load_global_terms(tmp_path / "keywords_global.csv")
    table = tmp_path / "wrong.csv"
    table.write_text("word,score_len\nx,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No 'term' column"):
        _load_global_terms(table)


def test_pdf_corpus_inputs_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="PDF index CSV not found"):
        load_pdf_index(tmp_path / "pdfs.csv")
    with pytest.raises(FileNotFoundError, match="PDF directory not found"):
        build_pdf_corpus(
            pdf_dir=tmp_path / "absent",
            pdf_index_csv=tmp_path / "pdfs.csv",
            out_dir=tmp_path / "out",
            index_csv=tmp_path / "index.csv",
        )


def test_label_translation_needs_a_pairs_table(tmp_path: Path) -> None:
    pairs = tmp_path / "pairs.csv"
    pd.DataFrame({"term": ["x"]}).to_csv(pairs, index=False)
    with pytest.raises(ValueError, match="no 'concept' column"):
        fill_missing_label_sides(pairs, languages=("fr",), client=object())


def test_an_empty_corpus_is_a_corpus_error(tmp_path: Path) -> None:
    empty = tmp_path / "manual_index.csv"
    empty.write_text("last_name,first_name,unit,txt_path\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="No valid corpus index files found"):
        load_documents_selected([("manual", empty, None), ("absent", tmp_path / "x.csv", None)])
    with pytest.raises(CorpusError, match="No corpus slot to read"):
        load_documents_selected([])
    with pytest.raises(CorpusError, match="cannot build roster.csv"):
        write_roster(index_csvs=[empty], out_csv=tmp_path / "roster.csv")
