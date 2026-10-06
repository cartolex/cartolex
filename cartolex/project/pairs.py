# SPDX-License-Identifier: MIT
"""The memory of pairs looked at: ``decisions/people_pairs.csv`` and
``decisions/organisation_pairs.csv``.

Two people (or two organisations) proposed as one and judged otherwise are
remembered, so the proposal does not come back: ``distinct`` (they are two)
never comes back; ``later`` (not decided yet) stays in the list of pairs to
review, and out of every automatic merge. A pair is written with its smaller
id first; a merge is not a pair decision (it is ``merged_into``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from .files import StaleWrite, fingerprint, write_decision
from .layout import ProjectLayout
from .tables import decision_csv_bytes, read_decision_csv

__all__ = ["PAIR_DECISIONS", "forget_pairs", "pair_key", "read_pairs", "remember_pairs"]

#: What a pair can be decided as.
PAIR_DECISIONS = ("distinct", "later")


def pair_key(a: str, b: str) -> tuple[str, str]:
    """The pair's key: the smaller id first."""
    return (a, b) if a <= b else (b, a)


def _file(layout: ProjectLayout, kind: str) -> tuple[Path, str]:
    if kind == "people":
        return layout.people_pairs_csv, "people_pairs"
    if kind == "organisations":
        return layout.organisation_pairs_csv, "organisation_pairs"
    raise ValueError(f"no pairs of {kind!r}: people or organisations")


def read_pairs(
    layout: ProjectLayout, kind: str = "people"
) -> dict[tuple[str, str], dict[str, str]]:
    """The pairs decided, by key (empty when the file does not exist)."""
    path, name = _file(layout, kind)
    return {pair_key(r["a"], r["b"]): r for r in read_decision_csv(path, name)}


def _write(
    layout: ProjectLayout,
    kind: str,
    change: Mapping[tuple[str, str], dict[str, str] | None],
    action: str,
    now: datetime | None,
) -> int:
    path, name = _file(layout, kind)
    for attempt in (1, 2):
        expected = fingerprint(path)
        rows = read_pairs(layout, kind)
        changed = 0
        for key, row in change.items():
            if row is None:
                changed += rows.pop(key, None) is not None
            elif rows.get(key, {}).get("decision") != row["decision"] or key not in rows:
                rows[key] = row
                changed += 1
        if not changed:
            return 0
        try:
            write_decision(
                layout,
                path,
                decision_csv_bytes(name, list(rows.values())),
                expected=expected,
                action=action,
                now=now,
            )
            return changed
        except StaleWrite:
            if attempt == 2:
                raise
    return 0  # pragma: no cover - the loop returns or raises


def remember_pairs(
    layout: ProjectLayout,
    pairs: Iterable[tuple[str, str]],
    decision: str,
    *,
    note: str = "",
    kind: str = "people",
    now: datetime | None = None,
) -> int:
    """Record *pairs* as *decision* (``distinct`` or ``later``); returns the pairs changed.
    The write is guarded like every decision (the version read, the history)."""
    if decision not in PAIR_DECISIONS:
        raise ValueError(f"{decision!r} is not a pair decision: {', '.join(PAIR_DECISIONS)}")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    change: dict[tuple[str, str], dict[str, str] | None] = {}
    for a, b in pairs:
        if a == b:
            continue
        key = pair_key(a, b)
        change[key] = {"a": key[0], "b": key[1], "decision": decision, "note": note,
                       "decided_at": stamp}  # fmt: skip
    if not change:
        return 0
    return _write(layout, kind, change, f"{decision} {len(change)} pairs", now)


def forget_pairs(
    layout: ProjectLayout,
    pairs: Iterable[tuple[str, str]],
    *,
    kind: str = "people",
    now: datetime | None = None,
) -> int:
    """Take *pairs* out of the memory (they may be proposed again); returns how many."""
    change: dict[tuple[str, str], dict[str, str] | None] = {pair_key(a, b): None for a, b in pairs}
    if not change:
        return 0
    return _write(layout, kind, change, f"forget {len(change)} pairs", now)
