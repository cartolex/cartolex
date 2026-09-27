# SPDX-License-Identifier: MIT
"""Public API for the keyword extraction engine.

Callers should import from this package rather than from individual submodules:

    from cartolex.lexicon import KeywordsConfig, run_pipeline_stage_1, run_pipeline_stage_3
"""

from __future__ import annotations

from .config import CorpusSlot, KeywordsConfig, SettingsError
from .consolidation import run_pipeline as run_pipeline_stage_3
from .extract_raw import run_pipeline_stage_1
from .io_helpers import CorpusError

__all__ = [
    "CorpusError",
    "CorpusSlot",
    "KeywordsConfig",
    "SettingsError",
    "run_pipeline_stage_1",
    "run_pipeline_stage_3",
]
