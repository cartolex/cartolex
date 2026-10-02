# SPDX-License-Identifier: MIT
"""Keys kept on this computer, never in a project: the AI provider's and OpenAlex's.

A key is personal and belongs to a machine: a project folder is shared, synced
and backed up, so no key is ever written there. The app keeps them in its own
folder (``<data_dir>/keys.json``, readable by its owner only), or in memory
when it has no folder. An environment variable given at launch
(``MISTRAL_API_KEY``, ``OPENALEX_API_KEY``) wins over a key saved here. The
AI clean-up and the collection read the saved key each time they start, so a
key saved while the app runs serves the next one. A hosted service has its
keys set by whoever runs it: none are saved from the interface.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from cartolex.project.files import replace_path

__all__ = ["KEY_SERVICES", "MachineKeys"]

#: The services a key can be saved for, and the environment variable that wins over it.
KEY_SERVICES = {"mistral": "MISTRAL_API_KEY", "openalex": "OPENALEX_API_KEY"}


class MachineKeys:
    """The keys of this computer: read, saved, removed; never shown back whole."""

    def __init__(self, folder: Path | None) -> None:
        self.path = Path(folder) / "keys.json" if folder is not None else None
        self._memory: dict[str, str] = {}
        self._lock = threading.Lock()

    def _saved(self) -> dict[str, str]:
        if self.path is None:
            return dict(self._memory)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: str(v) for k, v in data.items() if k in KEY_SERVICES and str(v).strip()}

    def get(self, service: str) -> str | None:
        """The key in use for *service*: the environment's, else the one saved here."""
        env = os.environ.get(KEY_SERVICES[service], "").strip()
        return env or self._saved().get(service) or None

    def status(self, service: str) -> dict[str, Any]:
        """Whether a key is set, where it comes from, and its last four characters."""
        env = os.environ.get(KEY_SERVICES[service], "").strip()
        saved = self._saved().get(service)
        key = env or saved
        return {
            "set": bool(key),
            "source": "environment" if env else "saved" if saved else None,
            "env_var": KEY_SERVICES[service],
            "saved": bool(saved),
            "ends": key[-4:] if key and len(key) >= 8 else None,
        }

    def save(self, service: str, key: str | None) -> None:
        """Save *key* for *service* on this computer (``None`` removes it)."""
        with self._lock:
            data = self._saved()
            if key:
                data[service] = key.strip()
            else:
                data.pop(service, None)
            if self.path is None:
                self._memory = data
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".part")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            replace_path(tmp, self.path)
