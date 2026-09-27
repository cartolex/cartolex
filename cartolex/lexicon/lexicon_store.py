# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def load_json(path: Path) -> dict:
    """Load a JSON file, returning an empty structure when it is missing."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_manual_blacklist(path: Path) -> set[str]:
    """Load the manual blacklist of terms."""
    if not path.exists():
        return set()
    df = pd.read_csv(path)
    if "term" not in df.columns:
        return set()
    return set(df["term"].astype(str).str.strip().str.lower())


def load_canonical_decision_blacklist(path: Path) -> set[str]:
    """Load the blacklist derived from canonical LLM decisions."""
    decisions = load_json(path)
    banned: set[str] = set()
    for key, val in decisions.items():
        if val != "s":
            continue
        try:
            t1, t2 = key.split("|||")
        except ValueError:
            continue
        banned.add(t1.strip().lower())
        banned.add(t2.strip().lower())
    return banned


def load_translation_map(path: Path) -> dict[str, str]:
    """Load the FR<->EN term translation map."""
    data = load_json(path)
    merged: dict[str, str] = {}
    for key, value in data.items():
        k = str(key).strip().lower()
        v = str(value).strip().lower()
        if k and v:
            merged[k] = v
    return merged
