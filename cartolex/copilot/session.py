# SPDX-License-Identifier: MIT
"""What both copilot sessions share: the bundle's folder, the changes' log, the result."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .bundle import RESULT_FILE, RESULT_FORMAT, check_result, read_manifest

__all__ = ["CheckpointNeeded", "Session"]


class CheckpointNeeded(RuntimeError):
    """A step the curator has to agree to first (the guide's two checkpoints)."""


class Session:
    """A bundle opened in its folder: its manifest, its context, timings of each step."""

    task = ""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.manifest = read_manifest(self.root)
        if self.manifest["task"] != self.task:
            raise ValueError(f"this bundle is for {self.manifest['task']}, not {self.task}")
        self.context: dict[str, Any] = self.json("data/context.json")
        self.language = str(self.context.get("reference_language") or "en")
        self.curator_language = str(self.manifest.get("curator_language") or "en")
        self.timings: list[tuple[str, float]] = []

    # ── files ──
    def path(self, rel: str) -> Path:
        return self.root / rel

    def json(self, rel: str) -> Any:
        return json.loads(self.path(rel).read_text(encoding="utf-8"))

    def _timed(self, step: str, started: float) -> None:
        self.timings.append((step, round(time.perf_counter() - started, 3)))

    # ── the result ──
    def _result(self, body: Mapping[str, Any], notes: str) -> dict[str, Any]:
        return {
            "format": RESULT_FORMAT,
            "task": self.task,
            "bundle": self.manifest["id"],
            "made_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "kit": self.manifest.get("cartolex", ""),
            **dict(body),
            "notes": str(notes or ""),
        }

    def _write(self, doc: Mapping[str, Any], curator_agreed: bool) -> Path:
        if not curator_agreed:
            raise CheckpointNeeded(
                "Checkpoint 2: show the curator what you change and why (the summary above), "
                "in their language, and ask whether to hand it back. Then call "
                "write_result(..., curator_agreed=True)."
            )
        problems = check_result(doc, task=self.task)
        if problems:
            raise ValueError("the result is not valid: " + "; ".join(problems))
        out = self.path(RESULT_FILE)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return out
