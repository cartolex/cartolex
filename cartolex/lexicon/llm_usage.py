# SPDX-License-Identifier: MIT
"""Mistral token usage: a recorder owned by the run, and the persisted workspace total.

A :class:`UsageRecorder` tallies the token counts reported by live Mistral API
calls. Each run gets its own (the context's ``usage``), so two runs in one
process never mix their counts; a caller that wants a process-wide total
shares one recorder between the contexts it builds. An optional *active
scope* (:meth:`UsageRecorder.begin_scope` / :meth:`UsageRecorder.end_scope`)
captures the tokens spent during one logical operation — e.g. a triage job —
*including* calls made from worker threads (triage fans batches out across a
thread pool). Cached responses make no API call and therefore add nothing.

The workspace-level cumulative total is persisted to a JSON file (the
context's ``paths.ai_usage_json``) so it survives server restarts.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class TokenUsage:
    """A simple (prompt, completion) token tally."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Sum of prompt and completion tokens."""
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, int]:
        """Serialise to a plain dict with the three token counts."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


class UsageRecorder:
    """Token usage of live API calls: a cumulative tally and an optional active scope.

    Thread-safe; one recorder per run (or one shared on purpose by several).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cumulative = TokenUsage()
        self._active: TokenUsage | None = None

    def record(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Tally one live API call's token usage (cumulative + active scope)."""
        with self._lock:
            self._cumulative.prompt_tokens += prompt_tokens
            self._cumulative.completion_tokens += completion_tokens
            if self._active is not None:
                self._active.prompt_tokens += prompt_tokens
                self._active.completion_tokens += completion_tokens

    def begin_scope(self) -> None:
        """Start a fresh accumulation scope (replaces any current one)."""
        with self._lock:
            self._active = TokenUsage()

    def end_scope(self) -> TokenUsage:
        """Close the active scope and return its accumulated usage."""
        with self._lock:
            used = self._active or TokenUsage()
            self._active = None
        return TokenUsage(used.prompt_tokens, used.completion_tokens)

    def cumulative(self) -> TokenUsage:
        """Tokens recorded since this recorder was created."""
        with self._lock:
            return TokenUsage(self._cumulative.prompt_tokens, self._cumulative.completion_tokens)


#: Serialises read-modify-write cycles of persisted totals within this process.
_persist_lock = threading.Lock()


def read_persisted(path: Path) -> TokenUsage:
    """Read the persisted workspace cumulative; empty when the file is absent."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return TokenUsage()
    return TokenUsage(
        int(doc.get("prompt_tokens", 0) or 0),
        int(doc.get("completion_tokens", 0) or 0),
    )


def add_to_persisted(path: Path, usage: TokenUsage) -> TokenUsage:
    """Add *usage* to the persisted workspace cumulative and return the new total."""
    path = Path(path)
    with _persist_lock:
        total = read_persisted(path)
        total.prompt_tokens += usage.prompt_tokens
        total.completion_tokens += usage.completion_tokens
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = total.as_dict()
        doc["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        return total
