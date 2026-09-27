# SPDX-License-Identifier: MIT
"""Unified credential resolution for external API keys.

Resolution order (first non-empty wins):
1. Environment variable  (e.g. ``MISTRAL_API_KEY``)
2. JSON config file      (the context's ``paths.api_key_json``)
3. Interactive prompt     (with optional save-to-file)
"""

from __future__ import annotations

import getpass
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _read_json_key(path: Path, key_name: str) -> str:
    """Read a single key from a JSON object file, returning '' on failure."""
    if not path.exists():
        return ""
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    value = str(data.get(key_name, "")).strip()
    if not value or value.upper() == "YOUR_KEY_HERE":
        return ""
    return value


def _save_json_key(path: Path, key_name: str, value: str) -> None:
    """Save (or update) a single key in a JSON object file."""
    data: dict[str, str] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
        except Exception:
            pass
    data[key_name] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_api_key(
    *,
    env_var: str,
    config_path: Path | None = None,
    config_key: str,
    service_name: str = "",
    interactive: bool = True,
) -> str:
    """Resolve an API key using the env → file → prompt chain.

    Parameters
    ----------
    env_var:
        Environment variable name (e.g. ``"MISTRAL_API_KEY"``).
    config_path:
        Path to a JSON file that may contain the key.
    config_key:
        Key name inside the JSON file (e.g. ``"mistral_api_key"``).
    service_name:
        Human-readable service name shown in the interactive prompt.
    interactive:
        If ``True`` and no key is found, prompt the user at the terminal.
        When running inside a GUI, callers should set this
        to ``False`` and handle the missing-key case in the UI instead.

    Returns
    -------
    str
        The resolved API key, or ``""`` if none could be obtained.
    """
    # 1. Environment variable
    key = os.environ.get(env_var, "").strip()
    if key:
        return key

    # 2. JSON config file
    if config_path is not None:
        key = _read_json_key(config_path, config_key)
        if key:
            return key

    # 3. Interactive prompt (CLI only)
    if interactive:
        label = service_name or env_var
        try:
            key = getpass.getpass(f"Enter your {label} API key (or set {env_var}): ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""
        if key:
            # Offer to persist
            if config_path is not None:
                try:
                    save = input(f"Save key to {config_path}? [y/N] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    save = "n"
                if save == "y":
                    _save_json_key(config_path, config_key, key)
                    logger.info("Key saved to %s", config_path)
            return key

    return ""


# ── Convenience helpers for the two services used by this project ────────


def resolve_mistral_key(
    config_path: Path | None = None,
    *,
    interactive: bool = True,
) -> str:
    """Resolve the Mistral AI API key (env var → the JSON file *config_path* → prompt)."""
    return resolve_api_key(
        env_var="MISTRAL_API_KEY",
        config_path=config_path,
        config_key="mistral_api_key",
        service_name="Mistral AI",
        interactive=interactive,
    )
