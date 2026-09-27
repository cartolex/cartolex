# SPDX-License-Identifier: MIT
"""Whitelists of terms that must be preserved in keyword extraction.

Two workspace files, both optional and never shipped, each read from the
explicit path it is given (the context's ``paths.whitelist_json`` and
``paths.person_whitelist_csv``):

* the axis whitelist, a JSON object mapping an axis name to a list of terms
  (:func:`load_whitelist`, :func:`whitelist_terms`);
* the operator-curated person-name whitelist, a one-column CSV of person
  names force-accepted as research-object keywords by the triage
  (:func:`load_person_whitelist`).

An absent file yields an empty whitelist.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path


def load_person_whitelist(path: Path | None) -> set[str]:
    """Load the operator-curated person-name whitelist from the CSV file *path*.

    A single-column CSV whose ``name`` header row is optional. Tolerant by
    design: no path, a missing file or an unreadable/undecodable file yields
    an empty set. Names are returned as written (stripped); consumers match
    them case-insensitively.
    """
    if path is None:
        return set()
    path = Path(path)
    if not path.exists():
        return set()
    names: set[str] = set()
    try:
        # utf-8-sig transparently strips a BOM left by spreadsheet exports.
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for i, row in enumerate(csv.reader(fh)):
                if not row:
                    continue
                cell = (row[0] or "").strip()
                if not cell:
                    continue
                if i == 0 and cell.lower() == "name":
                    continue
                names.add(cell)
    except Exception:
        return set()
    return names


def build_whitelist_set(
    axis_terms: Iterable[str],
    person_names: Iterable[str] = (),
) -> set[str]:
    """Lowercased whitelist for consolidation/attribution.

    Union of the axis whitelist terms and the operator-curated person-name
    whitelist; blank entries are dropped. This is the ``whitelist_set`` that
    protects terms through restricted-vocabulary building and the top-N
    attribution cuts.
    """
    out = {t.strip().lower() for t in axis_terms if t and t.strip()}
    out |= {n.strip().lower() for n in person_names if n and n.strip()}
    return out


def load_whitelist(path: Path | None) -> dict[str, set[str]]:
    """Load the whitelist axes from the JSON file *path* (empty when absent or invalid).

    Returns a dict mapping axis names to sets of terms.
    """
    if path is None or not Path(path).exists():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: set(v) for k, v in data.items() if isinstance(v, list)}


def whitelist_terms(axes: dict[str, set[str]]) -> set[str]:
    """Union of every axis of a whitelist."""
    result: set[str] = set()
    for terms in axes.values():
        result |= terms
    return result
