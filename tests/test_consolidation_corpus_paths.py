# SPDX-License-Identifier: MIT
"""Consolidation must load the per-document corpus INDEXES (``<slot>_index.csv``
with a txt_path column), not a PDF manifest: a manifest lacks ``txt_path`` and
would silently skip a whole corpus. Consolidation reads the index of each fit
slot the run context names (``paths.corpus_index_csv(<slot id>)``).
"""

from __future__ import annotations

from pathlib import Path

from cartolex.context import EnginePaths, RunContext
from cartolex.lexicon.config import KeywordsConfig
from cartolex.lexicon.io_helpers import slot_indexes


def test_consolidation_uses_per_document_corpus_indexes() -> None:
    paths = EnginePaths.for_workspace(Path("/ws"))
    # The corpus index (with txt_path), NOT a PDF manifest.
    assert paths.corpus_index_csv("manual") == Path("/ws/manual_index.csv")
    ctx = RunContext.for_workspace(
        Path("/ws"), KeywordsConfig(corpus_slots=("a", "b"), domain_title="D"), paths=paths
    )
    assert [p for _, p, _ in slot_indexes(ctx)] == [
        Path("/ws/a_index.csv"),
        Path("/ws/b_index.csv"),
    ]
