# SPDX-License-Identifier: MIT
"""Public API for the keyword extraction engine.

Callers should import from this package rather than from individual submodules:

    from cartolex.lexicon import KeywordsConfig, run_pipeline_stage_1, run_pipeline_stage_3

The names are loaded on first use: importing one light module of the package
(the theme tree, the fit measures) does not load the extraction and its
libraries.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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

_WHERE = {
    "CorpusError": ("io_helpers", "CorpusError"),
    "CorpusSlot": ("config", "CorpusSlot"),
    "KeywordsConfig": ("config", "KeywordsConfig"),
    "SettingsError": ("config", "SettingsError"),
    "run_pipeline_stage_1": ("extract_raw", "run_pipeline_stage_1"),
    "run_pipeline_stage_3": ("consolidation", "run_pipeline"),
}


def __getattr__(name: str) -> Any:
    if name in _WHERE:
        module, attr = _WHERE[name]
        value = getattr(import_module(f".{module}", __name__), attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
