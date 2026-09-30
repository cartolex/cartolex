# SPDX-License-Identifier: MIT
"""What both copilot sessions share: the bundle's folder, the changes' log, the result."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .bundle import RESULT_FILE, RESULT_FORMAT, check_result, read_manifest

__all__ = ["ASK", "CheckpointNeeded", "Session"]


class CheckpointNeeded(RuntimeError):
    """A step the curator has to agree to first (the guide's two checkpoints)."""


#: How a checkpoint ends: a question to the curator, and nothing else until they answer.
ASK = (
    "End your message with that question, in the curator's language, and wait for "
    "their answer: take no other step before it."
)
SESSION_FORMAT = "cartolex-copilot-session/1"


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

    # ── saving the session between steps ──
    def _state_path(self) -> Path:
        part = getattr(self, "part", None)
        return self.path(f"result/session{'-part-' + str(part) if part else ''}.json")

    def state(self) -> dict[str, Any]:
        """What the session keeps between two processes (its changes and decisions are on
        disk already, as they are made)."""
        return {}

    def restore(self, state: Mapping[str, Any]) -> None:
        """Take back what :meth:`state` kept."""

    def save(self) -> Path:
        """Keep the session's state in ``result/`` (it is saved after every step too): an
        environment that starts a fresh process per step goes on with :meth:`load`."""
        doc = {"format": SESSION_FORMAT, "bundle": self.manifest.get("id"), **self.state()}
        out = self._state_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
        return out

    @classmethod
    def load(cls, folder: Path | str = ".", **options: Any) -> Session:
        """The session as the last process left it: the bundle opened again (with the same
        *options*, ``part=`` …), its changes or decisions taken up from ``result/``, and its
        state (what it showed you) back. For a fresh process per step within one
        conversation; a new conversation opens the bundle and calls ``resume()`` instead."""
        from . import open_bundle

        session = open_bundle(folder, **options)
        session.resume()  # type: ignore[attr-defined]
        path = session._state_path()
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("format") == SESSION_FORMAT and doc.get("bundle") == session.manifest.get(
                "id"
            ):
                session.restore(doc)
        return session

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

    def _write(
        self, doc: Mapping[str, Any], curator_agreed: bool, *, name: str = RESULT_FILE
    ) -> Path:
        if not curator_agreed:
            raise CheckpointNeeded(
                "Checkpoint 2: show the curator what you change and why (report()), in their "
                "language, and ask whether to hand it back. " + ASK + " Then call "
                "write_result(..., curator_agreed=True)."
            )
        problems = check_result(doc, task=self.task)
        if problems:
            raise ValueError("the result is not valid: " + "; ".join(problems))
        out = self.path(name)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return out
