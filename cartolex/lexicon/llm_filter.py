# SPDX-License-Identifier: MIT
"""Persistence + per-term caching for LLM keyword triage.

The triage itself lives in :mod:`cartolex.lexicon.triage_typed` (single-pass
typed classification). This module retains the shared helpers it depends on:

* :class:`TermDecisionCache` — thread-safe per-term decision cache.
* :func:`save_decisions` / :func:`load_decisions` — persist the combined
  decisions dict (the context's ``paths.triage_decisions_json``).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

# ── Per-term decision cache ───────────────────────────────────
#
# The batch-level cache in LLMCache hashes entire batches of terms.
# If even one term changes in a batch, the whole batch is re-sent.
# The per-term cache stores individual term→decision mappings so that
# re-runs after small term-list changes only classify new terms.
#
# Key format: "phase:model:domain_title:term" → decision dict (frozen: see
# tests/test_ai_cache_keys.py)
# This is separate from the batch-level HTTP cache.


class TermDecisionCache:
    """Per-term cache for LLM classification decisions (thread-safe)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._dirty = False
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def _key(self, phase: str, term: str, domain_title: str, model: str) -> str:
        return f"{phase}:{model}:{domain_title}:{term}"

    def get(self, phase: str, term: str, domain_title: str, model: str) -> dict | None:
        with self._lock:
            return self._data.get(self._key(phase, term, domain_title, model))

    def put(self, phase: str, term: str, domain_title: str, model: str, decision: dict) -> None:
        with self._lock:
            self._data[self._key(phase, term, domain_title, model)] = decision
            self._dirty = True

    def flush(self) -> None:
        with self._lock:
            if self._dirty:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    json.dumps(self._data, ensure_ascii=False),
                    encoding="utf-8",
                )
                self._dirty = False


def save_decisions(decisions: dict, path: Path) -> None:
    """Persist the combined LLM decisions dict to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decisions, indent=2, ensure_ascii=False), encoding="utf-8")


def load_decisions(path: Path) -> dict:
    """Load previously saved LLM decisions."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
