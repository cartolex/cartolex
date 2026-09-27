# SPDX-License-Identifier: MIT
"""The workspace override file: the domain title and the stop-word overrides."""

from __future__ import annotations

import json
from collections.abc import Iterable, MutableMapping, MutableSequence, MutableSet
from pathlib import Path


def _normalize_tokens(values: Iterable[str]) -> set[str]:
    return {str(v).strip().lower() for v in values if isinstance(v, str) and str(v).strip()}


def _normalize_patterns(values: Iterable[str]) -> list[str]:
    return [str(v).strip() for v in values if isinstance(v, str) and str(v).strip()]


def load_overrides(path: Path) -> dict:
    """
    Load a workspace's override file: the domain title and the stop-word
    additions and removals of its runs (``{}`` when the file is absent or
    unreadable).

    Expected JSON format::

        {
          "domain_title": "...",
          "add": {
            "basic_blacklist": ["..."],
            "admin_tokens": ["..."],
            "admin_patterns": ["..."],
            "junk_patterns": ["..."],
            "geo_terms": ["..."],
            "org_acronyms": ["..."],
            "person_names": ["..."],
            "midwords": ["..."],
            "single_blacklist": ["..."],
            "merge_map": {"variant": "canonical"}
          },
          "remove": {
            "basic_blacklist": ["..."],
            "admin_tokens": ["..."],
            "admin_patterns": ["..."],
            "junk_patterns": ["..."],
            "geo_terms": ["..."],
            "org_acronyms": ["..."],
            "person_names": ["..."],
            "midwords": ["..."],
            "single_blacklist": ["..."],
            "merge_map": ["variant_to_remove"]
          }
        }
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _apply_token_overrides(
    base: MutableSet[str],
    add: Iterable[str] | None,
    remove: Iterable[str] | None,
) -> None:
    if add:
        base |= _normalize_tokens(add)
    if remove:
        base -= _normalize_tokens(remove)


def _apply_pattern_overrides(
    base: MutableSequence[str],
    add: Iterable[str] | None,
    remove: Iterable[str] | None,
) -> None:
    if add:
        base.extend(_normalize_patterns(add))
    if remove:
        remove_set = {str(v).strip() for v in remove if isinstance(v, str) and str(v).strip()}
        if remove_set:
            base[:] = [p for p in base if p not in remove_set]


def _apply_merge_map_overrides(
    merge_map: MutableMapping[str, str],
    add: dict[str, str] | None,
    remove: Iterable[str] | None,
) -> None:
    if add:
        for k, v in add.items():
            key = str(k).strip().lower()
            val = str(v).strip().lower()
            if key and val:
                merge_map[key] = val
    if remove:
        for k in remove:
            key = str(k).strip().lower()
            if key in merge_map:
                del merge_map[key]


def apply_overrides(
    *,
    overrides: dict,
    midwords: MutableSet[str],
    basic_blacklist: MutableSet[str],
    admin_tokens: MutableSet[str],
    admin_patterns: MutableSequence[str],
    junk_patterns: MutableSequence[str],
    geo_terms: MutableSet[str],
    org_acronyms: MutableSet[str],
    person_names: MutableSet[str],
    merge_map: MutableMapping[str, str],
    single_blacklist: MutableSet[str],
) -> None:
    """Apply the ``add`` / ``remove`` blocks of *overrides* to the stop-word sets, in place."""
    add = overrides.get("add", {}) if isinstance(overrides.get("add", {}), dict) else {}
    remove = overrides.get("remove", {}) if isinstance(overrides.get("remove", {}), dict) else {}

    _apply_token_overrides(midwords, add.get("midwords"), remove.get("midwords"))
    _apply_token_overrides(
        basic_blacklist, add.get("basic_blacklist"), remove.get("basic_blacklist")
    )
    _apply_token_overrides(admin_tokens, add.get("admin_tokens"), remove.get("admin_tokens"))
    _apply_token_overrides(geo_terms, add.get("geo_terms"), remove.get("geo_terms"))
    _apply_token_overrides(org_acronyms, add.get("org_acronyms"), remove.get("org_acronyms"))
    _apply_token_overrides(person_names, add.get("person_names"), remove.get("person_names"))
    _apply_token_overrides(
        single_blacklist, add.get("single_blacklist"), remove.get("single_blacklist")
    )

    _apply_pattern_overrides(
        admin_patterns, add.get("admin_patterns"), remove.get("admin_patterns")
    )
    _apply_pattern_overrides(junk_patterns, add.get("junk_patterns"), remove.get("junk_patterns"))

    _apply_merge_map_overrides(merge_map, add.get("merge_map"), remove.get("merge_map"))
