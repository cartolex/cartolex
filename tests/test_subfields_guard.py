# SPDX-License-Identifier: MIT
"""Subfield steps must fail clearly when the lexical models are missing (SVD, clustering)."""

from __future__ import annotations

import json

import pytest

from cartolex.context import RunContext
from cartolex.lexicon.config import KeywordsConfig


def test_draft_subfields_missing_lexical_models_raises_clear_error(tmp_path) -> None:
    from cartolex.lexicon.subfields import draft_subfields

    ctx = RunContext.for_workspace(tmp_path, KeywordsConfig(domain_title="Domain X"))
    with pytest.raises(FileNotFoundError, match=r"SVD"):
        draft_subfields(ctx)


def test_apply_subfields_missing_lexical_models_raises_clear_error(tmp_path) -> None:
    from cartolex.lexicon.subfields import apply_subfields

    ctx = RunContext.for_workspace(tmp_path, KeywordsConfig())
    curated = ctx.paths.subfields_curated_json
    curated.parent.mkdir(parents=True)
    curated.write_text(
        json.dumps({"subfields": [{"id": 0, "label": "x", "seed_terms": ["a"], "keep": True}]}),
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match=r"SVD"):
        apply_subfields(ctx)
