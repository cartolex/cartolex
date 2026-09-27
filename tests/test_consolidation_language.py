# SPDX-License-Identifier: MIT
"""N-language raw-keyword merge in consolidation.

``load_and_merge_raw_keywords`` reads one ``raw_keywords_<lang>.csv`` per
configured corpus language and tags each row's ``lang_source`` accordingly.
"""

from __future__ import annotations

from cartolex.context import RunContext
from cartolex.lexicon import consolidation
from cartolex.lexicon.config import KeywordsConfig


def test_load_and_merge_reads_all_corpus_languages(tmp_path) -> None:
    settings = KeywordsConfig(corpus_languages=("pt", "en"))
    ctx = RunContext.for_workspace(tmp_path, settings)
    ctx.paths.automatic_dir.mkdir(parents=True)
    ctx.paths.raw_terms_csv("pt").write_text(
        "term,score,score_len\nmatéria ativa,2.0,2.2\n", encoding="utf-8"
    )
    ctx.paths.raw_terms_csv("en").write_text(
        "term,score,score_len\nactive matter,1.0,1.1\n", encoding="utf-8"
    )

    df = consolidation.load_and_merge_raw_keywords(ctx)

    assert set(df["lang_source"]) == {"pt", "en"}
    assert "matéria ativa" in df["term"].tolist()
    assert "active matter" in df["term"].tolist()
