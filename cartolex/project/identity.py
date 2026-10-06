# SPDX-License-Identifier: MIT
"""Who is who: the merges people decided, read the same way everywhere.

A merge is a decision, and a reversible one: ``merged_into`` in
``decisions/people.csv`` says that a row is the same person as another. The
merged row keeps everything it had (its records, its identity, its role), so
that undoing the merge gives it back as it was; while it is merged, the person
it is merged into stands for both. A person's **effective** records are their
own and those of every row merged into them; their texts, coverage and
affiliations likewise (the collection, the coverage, the person's sheet and the
corpus all read them so).

``merged_into`` may name a row that is itself merged (an older file, a hand
edit): :func:`merge_roots` follows the chain to the person that remains, and a
cycle leaves every row in it on its own.
"""

from __future__ import annotations

import functools
import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

from .tables import read_decision_csv

__all__ = [
    "AUTO_MERGE_NOTE",
    "IDENTITY_RANK",
    "MergeRefused",
    "effective_identity",
    "effective_orcids",
    "effective_records",
    "merge_roots",
    "merge_changes",
    "merged_groups",
    "merges_digest",
    "orcid_conflict",
    "unmerge_changes",
]

#: Which identity state stands for a merged person, the strongest first.
IDENTITY_RANK = ("confirmed", "auto", "none", "pending", "")


def merge_roots(
    rows: Mapping[str, Mapping[str, str]] | Iterable[Mapping[str, str]],
) -> dict[str, str]:
    """Each merged row → the person that remains (the end of its ``merged_into`` chain).

    *rows* are ``people.csv``'s rows, by person id or as a list. A row that is not
    merged is absent; a chain that loops, or ends on a row nobody has, leaves its
    rows out (they stand on their own).
    """
    by_id = rows if isinstance(rows, Mapping) else {r["person_id"]: r for r in rows}
    into = {pid: (r.get("merged_into") or "") for pid, r in by_id.items()}
    out: dict[str, str] = {}
    for pid, target in into.items():
        if not target:
            continue
        seen = {pid}
        current = target
        while into.get(current):
            if current in seen:
                current = ""
                break
            seen.add(current)
            current = into[current]
        if current and current not in seen and current in by_id:
            out[pid] = current
    return out


def merged_groups(roots: Mapping[str, str]) -> dict[str, list[str]]:
    """Each person that remains → the rows merged into them, sorted."""
    groups: dict[str, list[str]] = defaultdict(list)
    for pid, root in roots.items():
        groups[root].append(pid)
    return {root: sorted(pids) for root, pids in sorted(groups.items())}


def _records(row: Mapping[str, str] | None) -> list[str]:
    return [r for r in ((row or {}).get("records") or "").split(";") if r]


def effective_records(
    rows: Mapping[str, Mapping[str, str]], roots: Mapping[str, str] | None = None
) -> dict[str, list[str]]:
    """Each person that remains → their records and those of the rows merged into them
    (their own first, then each merged row's in id order; each record once). A row
    merged into another has none of its own here: the person it is merged into has them.
    Rows without a record are absent."""
    roots = merge_roots(rows) if roots is None else roots
    out: dict[str, list[str]] = {}
    for pid in sorted(rows):
        if pid in roots:
            continue
        own = _records(rows[pid])
        if own:
            out[pid] = own
    for root, merged in merged_groups(roots).items():
        found = list(out.get(root, []))
        for pid in merged:
            found.extend(r for r in _records(rows.get(pid)) if r not in found)
        if found:
            out[root] = found
    return out


def effective_identity(
    rows: Mapping[str, Mapping[str, str]], pid: str, merged: Iterable[str] = ()
) -> str:
    """The identity state that stands for *pid* and the rows *merged* into them: the
    strongest of theirs (``confirmed``, then ``auto``, ``none``, ``pending``)."""
    states = [(rows.get(p) or {}).get("identity") or "" for p in (pid, *merged)]
    return min(states, key=lambda s: IDENTITY_RANK.index(s) if s in IDENTITY_RANK else 9)


def effective_orcids(
    person_ids: Iterable[str],
    rows: Mapping[str, Mapping[str, str]],
    table_orcids: Mapping[str, str | None],
) -> set[str]:
    """The ORCIDs of some rows taken together: the table's column and the ``orcid:``
    records decided for them."""
    out: set[str] = set()
    for pid in person_ids:
        if table_orcids.get(pid):
            out.add(str(table_orcids[pid]))
        out.update(r.split(":", 1)[1] for r in _records(rows.get(pid)) if r.startswith("orcid:"))
    return out


def orcid_conflict(a: set[str], b: set[str]) -> bool:
    """Whether two people's ORCIDs say they are two people: both have one, none in common."""
    return bool(a) and bool(b) and not (a & b)


@functools.lru_cache(maxsize=16)
def _digest(path: str, size: int, mtime_ns: int) -> str | None:
    rows = read_decision_csv(Path(path), "people")
    roots = merge_roots(rows)
    if not roots:
        return None
    h = hashlib.sha256(b"cartolex-merges/1\0")
    for pid, root in sorted(roots.items()):
        h.update(f"{pid}\0{root}\n".encode())
    return "sha256:" + h.hexdigest()


def merges_digest(people_csv: Path) -> str | None:
    """A fingerprint of the merges ``people.csv`` holds (``None``: none, or no file).

    ``corpus.assemble`` records it as an input of its own, so a project with merges
    gathers its texts again when its merges change, and a project without any is not
    concerned by the way merges are read. Computed once per version of the file."""
    try:
        st = Path(people_csv).stat()
    except FileNotFoundError:
        return None
    return _digest(str(people_csv), st.st_size, st.st_mtime_ns)


# ── merging and unmerging ────────────────────────────────────────────────────


class MergeRefused(ValueError):
    """A merge that cannot be made as asked: a stable ``code`` and its ``params``."""

    def __init__(self, code: str, message: str, **params: object) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


def merge_changes(
    rows: Mapping[str, Mapping[str, str]],
    target: str,
    sources: Iterable[str],
    table_orcids: Mapping[str, str | None],
    *,
    override: bool = False,
    note: str = "",
) -> dict[str, dict[str, str]]:
    """The changes to ``people.csv`` that merge *sources* into *target*: each source's
    ``merged_into`` (and *note*, when given), and the rows already merged into a source,
    which follow it. Nothing else of a row changes, so :func:`unmerge_changes` gives it
    back as it was.

    Refused (:class:`MergeRefused`) when a source is the target (``self_merge``), the
    target is itself merged (``merged_target``), or the two sides carry different ORCIDs
    (``merge_orcid_conflict``) unless *override*: two different iDs are two people,
    unless someone who knows says otherwise.
    """
    sources = [s for s in dict.fromkeys(sources)]
    if target in sources:
        raise MergeRefused("self_merge", "a person cannot be merged into themselves")
    into = (rows.get(target) or {}).get("merged_into") or ""
    if into:
        raise MergeRefused(
            "merged_target", f"{target} is itself merged into {into}", target=target, into=into
        )
    roots = merge_roots(rows)
    groups = merged_groups(roots)
    if not override:
        mine = effective_orcids((target, *groups.get(target, ())), rows, table_orcids)
        for source in sources:
            theirs = effective_orcids((source, *groups.get(source, ())), rows, table_orcids)
            if orcid_conflict(mine, theirs):
                raise MergeRefused(
                    "merge_orcid_conflict",
                    f"{target} and {source} have different ORCIDs",
                    target=target,
                    source=source,
                    orcids=sorted(mine | theirs),
                )
    changes: dict[str, dict[str, str]] = {}
    for source in sources:
        change = {"merged_into": target}
        if note:
            change["note"] = note
        changes[source] = change
        for follower in groups.get(source, ()):
            if follower != target:
                changes.setdefault(follower, {"merged_into": target})
    for pid, row in rows.items():  # a row merged into a source follows it to the target
        if (row.get("merged_into") or "") in sources and pid != target:
            changes.setdefault(pid, {"merged_into": target})
    return changes


def unmerge_changes(
    rows: Mapping[str, Mapping[str, str]], person_ids: Iterable[str]
) -> dict[str, dict[str, str]]:
    """The changes that undo merges: each of *person_ids* that is merged stands on its own
    again (``merged_into`` emptied; the note of an automatic merge emptied too). A person
    others are merged into, named here, gets every one of them back."""
    roots = merge_roots(rows)
    groups = merged_groups(roots)
    changes: dict[str, dict[str, str]] = {}
    for pid in person_ids:
        for one in (pid, *groups.get(pid, ())):
            row = rows.get(one) or {}
            if not row.get("merged_into"):
                continue
            change = {"merged_into": ""}
            if (row.get("note") or "").startswith(AUTO_MERGE_NOTE):
                change["note"] = ""
            changes[one] = change
    return changes


#: The note an automatic merge leaves on each row it merges (followed by its time).
AUTO_MERGE_NOTE = "merged automatically"
