# SPDX-License-Identifier: MIT
"""Collection's changes to ``decisions/people.csv``, through the guarded writer.

Every change is a merge into the rows as they are on disk: a row is changed
only in the columns given, rows nobody mentions are kept, and a write refused
because the file changed in between (:class:`~cartolex.project.StaleWrite`)
is retried once on the new version.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from cartolex.project.files import StaleWrite, fingerprint, write_decision
from cartolex.project.layout import ProjectLayout
from cartolex.project.tables import DECISION_TABLES, decision_csv_bytes, read_decision_csv

__all__ = ["decided_now", "read_people", "update_people"]


def decided_now(now: datetime | None = None) -> str:
    """The ``decided_at`` stamp: UTC, to the second."""
    return (
        (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def read_people(layout: ProjectLayout) -> dict[str, dict[str, str]]:
    """The rows of ``decisions/people.csv`` by person id (empty when there is none)."""
    return {r["person_id"]: r for r in read_decision_csv(layout.people_csv, "people")}


def update_people(
    layout: ProjectLayout,
    changes: Mapping[str, Mapping[str, str]],
    *,
    action: str,
    only_new: bool = False,
    now: datetime | None = None,
) -> int:
    """Apply *changes* (person id → columns) to ``decisions/people.csv``; returns rows changed.

    With *only_new*, people who already have a row are left as they are (an
    import never overrides a decision). Each changed row gets a new
    ``decided_at``.
    """
    columns = DECISION_TABLES["people"].columns
    stamp = decided_now(now)
    for attempt in (1, 2):
        expected = fingerprint(layout.people_csv)
        rows = read_people(layout)
        changed = 0
        for pid, values in changes.items():
            if only_new and pid in rows:
                continue
            row = dict(rows.get(pid) or {c: "" for c in columns})
            row["person_id"] = pid
            new = {k: "" if v is None else str(v) for k, v in values.items()}
            if all(row.get(k, "") == v for k, v in new.items()) and pid in rows:
                continue
            row.update(new)
            row["decided_at"] = stamp
            rows[pid] = row
            changed += 1
        if not changed:
            return 0
        try:
            write_decision(
                layout,
                layout.people_csv,
                decision_csv_bytes("people", list(rows.values())),
                expected=expected,
                action=action,
                now=now,
            )
            return changed
        except StaleWrite:
            if attempt == 2:
                raise
    return 0  # pragma: no cover - the loop returns or raises
