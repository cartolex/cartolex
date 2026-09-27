# SPDX-License-Identifier: MIT
"""Pure edit operations over the curated subfield→concept→term hierarchy.

Schema 1.1 adds two top-level sections to the curated document
(``manual_data/subfields.json``):

``stash``
    Items set aside to be re-attached later: ``{"concepts": [...], "terms": [...]}``.
``trash``
    Items removed from the hierarchy but restorable: ``{"subfields": [...],
    "concepts": [...], "terms": [...]}``.

Entries carry the full original objects so restore is lossless:

- concept entry: ``{"concept": {...}, "origin_subfield_id": int}``
- term entry: ``{"term_index": int, "term": str | None,
  "origin_concept_id": int, "merge_group": list[int] | None,
  "status": str | None}`` — the term's attribution status at detach time
  (see *Term statuses* below), restored through the carry rule
- subfield entry: ``{"subfield": {...}, "concepts": [{...}, ...]}``

``term_index`` is the SVD row position (row order of
``umap_terms_clustered.csv``) — the engine never reorders or rewrites that
file. A term that belongs to a ``term_merges`` group moves as one unit (the
canonical index plus its variants), so a restore re-attaches the group intact.

Trash is hierarchy-only and reversible: nothing here touches the manual
blacklist or any extraction output.

**Term statuses.** A concept may list, as term STRINGS, ``subfield_only_terms``
(broader than the concept, within its subfield: credited to the subfield share
only) and ``ride_along_terms`` (broader than the subfield: displayed, no share
anywhere); every other term is *defining*. :func:`set_term_status` edits those
lists by term index; the **carry rule** (:func:`carry_status`) says what a
status becomes when its term moves: ride-along is a property of the term's
scope and survives any move or restore; subfield-only is relative to a subfield
and survives only within it, resetting to defining across subfields.
"""

from __future__ import annotations

import copy
from typing import Any

from .subfields import (
    _OTHER_COLOR,
    SubfieldsDoc,
    _term_status_sets,
    concept_shade,
    subfield_color,
)

EDIT_SCHEMA_VERSION = "1.1"


# ── Section accessors ────────────────────────────────────────────────────────


def _stash(doc: SubfieldsDoc) -> dict[str, list]:
    doc.setdefault("stash", {})
    doc["stash"].setdefault("concepts", [])
    doc["stash"].setdefault("terms", [])
    doc["schema_version"] = EDIT_SCHEMA_VERSION
    return doc["stash"]


def _trash(doc: SubfieldsDoc) -> dict[str, list]:
    doc.setdefault("trash", {})
    doc["trash"].setdefault("subfields", [])
    doc["trash"].setdefault("concepts", [])
    doc["trash"].setdefault("terms", [])
    doc["schema_version"] = EDIT_SCHEMA_VERSION
    return doc["trash"]


def _concept(doc: SubfieldsDoc, concept_id: int) -> dict[str, Any]:
    for c in doc.get("concepts", []):
        if int(c.get("id", -1)) == int(concept_id):
            return c
    raise ValueError(f"Unknown concept id {concept_id}.")


def _subfield(doc: SubfieldsDoc, subfield_id: int) -> dict[str, Any]:
    for s in doc.get("subfields", []):
        if int(s.get("id", -1)) == int(subfield_id):
            return s
    raise ValueError(f"Unknown subfield id {subfield_id}.")


def _term_label(term_index: int, terms_by_idx: list[str] | None) -> str | None:
    if terms_by_idx is not None and 0 <= term_index < len(terms_by_idx):
        return terms_by_idx[term_index]
    return None


def _detach_term_unit(
    concept: dict[str, Any], term_index: int, terms_by_idx: list[str] | None = None
) -> tuple[int, list[int] | None, str]:
    """Remove *term_index* (and its merge group, if any) from *concept*.

    Returns ``(canonical_index, merge_group, status)`` — the group is the full
    ``[canonical, variant...]`` list when the term belonged to one, else None;
    the status is the term's attribution status, erased from the concept's
    lists on the way out. Statuses are keyed by term string, so a concept that
    carries statuses needs *terms_by_idx* to name the term — detaching from
    such a concept without it raises rather than leave a stale string behind.
    """
    ti = int(term_index)
    group: list[int] | None = None
    merges = concept.get("term_merges") or []
    for g in merges:
        if ti in [int(x) for x in g]:
            group = [int(x) for x in g]
            break
    moved = set(group) if group else {ti}
    indices = [int(x) for x in concept.get("term_indices", [])]
    if not moved.issubset(indices):
        missing = sorted(moved.difference(indices))
        raise ValueError(f"Term indices {missing} not in concept {concept.get('id')}.")
    concept["term_indices"] = [x for x in indices if x not in moved]
    if group:
        concept["term_merges"] = [g for g in merges if int(g[0]) != group[0]]
    canonical = group[0] if group else ti
    status = "defining"
    if _has_statuses(concept):
        term = _term_label(canonical, terms_by_idx)
        if term is None:
            raise ValueError(
                f"Concept {concept.get('id')} carries term statuses: terms_by_idx is required "
                "to detach a term from it."
            )
        status = term_status(concept, term)
        _set_status_on(concept, term, "defining")
    return canonical, group, status


def _attach_term_unit(
    concept: dict[str, Any],
    canonical: int,
    merge_group: list[int] | None,
    *,
    status: str | None = None,
    term: str | None = None,
) -> None:
    indices = [int(x) for x in concept.get("term_indices", [])]
    new = merge_group if merge_group else [canonical]
    concept["term_indices"] = indices + [x for x in new if x not in indices]
    if merge_group:
        concept.setdefault("term_merges", []).append(list(merge_group))
    if status and status != "defining" and term:
        _set_status_on(concept, term, status)


# ── Term statuses (three-status attribution model) ──────────────────────────

TERM_STATUSES = ("defining", "subfield_only", "ride_along")
_STATUS_LIST = {"subfield_only": "subfield_only_terms", "ride_along": "ride_along_terms"}


def _norm_term(value: Any) -> str:
    return str(value).strip().lower()


def _has_statuses(concept: dict[str, Any]) -> bool:
    return bool(concept.get("subfield_only_terms") or concept.get("ride_along_terms"))


def term_status(concept: dict[str, Any], term: str) -> str:
    """Attribution status of the term string *term* in *concept*."""
    sf_only, ride = _term_status_sets(concept)
    t = _norm_term(term)
    if t in ride:
        return "ride_along"
    if t in sf_only:
        return "subfield_only"
    return "defining"


def _set_status_on(concept: dict[str, Any], term: str, status: str) -> None:
    """Record *status* for *term* on *concept* — the two lists stay mutually exclusive."""
    if status not in TERM_STATUSES:
        raise ValueError(f"Unknown term status {status!r} (expected one of {TERM_STATUSES}).")
    t = _norm_term(term)
    for key in _STATUS_LIST.values():
        kept = [x for x in (concept.get(key) or []) if _norm_term(x) != t]
        if kept:
            concept[key] = kept
        else:
            concept.pop(key, None)
    key = _STATUS_LIST.get(status)
    if key:
        concept.setdefault(key, []).append(term)


def _canonical_of(concept: dict[str, Any], term_index: int) -> int:
    ti = int(term_index)
    for g in concept.get("term_merges") or []:
        if ti in [int(x) for x in g]:
            return int(g[0])
    return ti


def canonical_indices(concept: dict[str, Any]) -> list[int]:
    """The concept's term indices minus merge variants — the terms statuses apply to."""
    variants = {int(x) for g in (concept.get("term_merges") or []) for x in g[1:]}
    return [int(x) for x in concept.get("term_indices", []) if int(x) not in variants]


def set_term_status(
    doc: SubfieldsDoc, term_index: int, status: str, terms_by_idx: list[str]
) -> SubfieldsDoc:
    """Set the attribution status of the term unit owning *term_index*.

    The status is written on the canonical term of the merge group (variants
    follow their canonical term everywhere else too). ``"defining"`` erases the
    term from both lists.
    """
    c = _owning_concept(doc, term_index)
    term = _term_label(_canonical_of(c, term_index), terms_by_idx)
    if term is None:
        raise ValueError("terms_by_idx must cover the term index to name the term.")
    _set_status_on(c, term, status)
    doc["schema_version"] = EDIT_SCHEMA_VERSION
    return doc


def carry_status(status: str | None, origin_subfield_id: Any, dest_subfield_id: Any) -> str:
    """What a term's status becomes when it lands in a concept of *dest_subfield_id*.

    Ride-along is a property of the term's scope: it survives any move or
    restore. Subfield-only is relative to a subfield: it survives within the
    same subfield and resets to defining across subfields (or when the origin
    is unknown).
    """
    if status == "ride_along":
        return "ride_along"
    if (
        status == "subfield_only"
        and origin_subfield_id is not None
        and dest_subfield_id is not None
        and int(origin_subfield_id) == int(dest_subfield_id)
    ):
        return "subfield_only"
    return "defining"


def _subfield_of_concept_id(doc: SubfieldsDoc, concept_id: Any) -> int | None:
    """Subfield id of an active concept, or of one held in the stash/trash; None if gone."""
    if concept_id is None:
        return None
    for c in doc.get("concepts", []):
        if int(c.get("id", -1)) == int(concept_id):
            return int(c.get("subfield_id", -1))
    for section in (doc.get("stash") or {}, doc.get("trash") or {}):
        for e in section.get("concepts", []):
            if int(e.get("concept", {}).get("id", -1)) == int(concept_id):
                return int(e.get("origin_subfield_id", -1))
    return None


def _pop_entry(entries: list[dict], key: str, value: int, what: str) -> dict:
    for i, e in enumerate(entries):
        candidate = e.get(key)
        if isinstance(candidate, dict):
            candidate = candidate.get("id")
        if candidate is not None and int(candidate) == int(value):
            return entries.pop(i)
    raise ValueError(f"No {what} with id {value} found.")


# ── Concept stash / trash ────────────────────────────────────────────────────


def _remove_concept(doc: SubfieldsDoc, concept_id: int) -> dict[str, Any]:
    c = _concept(doc, concept_id)
    doc["concepts"] = [x for x in doc["concepts"] if int(x.get("id", -1)) != int(concept_id)]
    return c


def stash_concept(doc: SubfieldsDoc, concept_id: int) -> SubfieldsDoc:
    """Move a concept (with all its terms) from its subfield to the stash."""
    c = _remove_concept(doc, concept_id)
    _stash(doc)["concepts"].append(
        {"concept": c, "origin_subfield_id": int(c.get("subfield_id", -1))}
    )
    return doc


def restore_concept(doc: SubfieldsDoc, concept_id: int, subfield_id: int) -> SubfieldsDoc:
    """Re-attach a stashed concept under *subfield_id* (may differ from its origin)."""
    _subfield(doc, subfield_id)
    entry = _pop_entry(_stash(doc)["concepts"], "concept", concept_id, "stashed concept")
    c = entry["concept"]
    c["subfield_id"] = int(subfield_id)
    doc["concepts"].append(c)
    return doc


def trash_concept(doc: SubfieldsDoc, concept_id: int) -> SubfieldsDoc:
    """Move a concept (with all its terms) to the trash — reversible."""
    c = _remove_concept(doc, concept_id)
    _trash(doc)["concepts"].append(
        {"concept": c, "origin_subfield_id": int(c.get("subfield_id", -1))}
    )
    return doc


def untrash_concept(doc: SubfieldsDoc, concept_id: int, subfield_id: int) -> SubfieldsDoc:
    """Restore a trashed concept under *subfield_id*."""
    _subfield(doc, subfield_id)
    entry = _pop_entry(_trash(doc)["concepts"], "concept", concept_id, "trashed concept")
    c = entry["concept"]
    c["subfield_id"] = int(subfield_id)
    doc["concepts"].append(c)
    return doc


# ── Term stash / trash ───────────────────────────────────────────────────────


def _owning_concept(doc: SubfieldsDoc, term_index: int) -> dict[str, Any]:
    ti = int(term_index)
    for c in doc.get("concepts", []):
        if ti in [int(x) for x in c.get("term_indices", [])]:
            return c
    raise ValueError(f"Term index {term_index} not found in any active concept.")


def stash_term(
    doc: SubfieldsDoc, term_index: int, terms_by_idx: list[str] | None = None
) -> SubfieldsDoc:
    """Move a term (and its merge variants) from its concept to the stash."""
    c = _owning_concept(doc, term_index)
    canonical, group, status = _detach_term_unit(c, term_index, terms_by_idx)
    _stash(doc)["terms"].append(
        {
            "term_index": canonical,
            "term": _term_label(canonical, terms_by_idx),
            "origin_concept_id": int(c.get("id", -1)),
            "merge_group": group,
            "status": status,
        }
    )
    return doc


def restore_term(doc: SubfieldsDoc, term_index: int, concept_id: int) -> SubfieldsDoc:
    """Re-attach a stashed term unit to *concept_id*."""
    target = _concept(doc, concept_id)
    entry = _pop_entry(_stash(doc)["terms"], "term_index", term_index, "stashed term")
    _reattach(doc, target, entry)
    return doc


def trash_term(
    doc: SubfieldsDoc, term_index: int, terms_by_idx: list[str] | None = None
) -> SubfieldsDoc:
    """Move a term (and its merge variants) to the trash — reversible."""
    c = _owning_concept(doc, term_index)
    canonical, group, status = _detach_term_unit(c, term_index, terms_by_idx)
    _trash(doc)["terms"].append(
        {
            "term_index": canonical,
            "term": _term_label(canonical, terms_by_idx),
            "origin_concept_id": int(c.get("id", -1)),
            "merge_group": group,
            "status": status,
        }
    )
    return doc


def untrash_term(doc: SubfieldsDoc, term_index: int, concept_id: int) -> SubfieldsDoc:
    """Restore a trashed term unit to *concept_id*."""
    target = _concept(doc, concept_id)
    entry = _pop_entry(_trash(doc)["terms"], "term_index", term_index, "trashed term")
    _reattach(doc, target, entry)
    return doc


def _reattach(doc: SubfieldsDoc, target: dict[str, Any], entry: dict[str, Any]) -> None:
    status = carry_status(
        entry.get("status"),
        _subfield_of_concept_id(doc, entry.get("origin_concept_id")),
        target.get("subfield_id"),
    )
    _attach_term_unit(
        target,
        int(entry["term_index"]),
        entry.get("merge_group"),
        status=status,
        term=entry.get("term"),
    )


def move_term(
    doc: SubfieldsDoc, term_index: int, concept_id: int, terms_by_idx: list[str] | None = None
) -> SubfieldsDoc:
    """Move a term unit from its concept to *concept_id*, carrying its status.

    Ride-along survives the move; subfield-only survives only within the same
    subfield (see :func:`carry_status`).
    """
    src = _owning_concept(doc, term_index)
    dst = _concept(doc, concept_id)
    if src is dst:
        return doc
    canonical, group, status = _detach_term_unit(src, term_index, terms_by_idx)
    _attach_term_unit(
        dst,
        canonical,
        group,
        status=carry_status(status, src.get("subfield_id"), dst.get("subfield_id")),
        term=_term_label(canonical, terms_by_idx),
    )
    return doc


# ── Subfield trash ───────────────────────────────────────────────────────────


def trash_subfield(doc: SubfieldsDoc, subfield_id: int) -> SubfieldsDoc:
    """Move a subfield AND its concepts to the trash — reversible as a unit."""
    s = _subfield(doc, subfield_id)
    doc["subfields"] = [x for x in doc["subfields"] if int(x.get("id", -1)) != int(subfield_id)]
    mine = [c for c in doc.get("concepts", []) if int(c.get("subfield_id", -1)) == int(subfield_id)]
    doc["concepts"] = [c for c in doc.get("concepts", []) if c not in mine]
    _trash(doc)["subfields"].append({"subfield": s, "concepts": mine})
    return doc


def untrash_subfield(doc: SubfieldsDoc, subfield_id: int) -> SubfieldsDoc:
    """Restore a trashed subfield with the concepts it was trashed with."""
    entry = _pop_entry(_trash(doc)["subfields"], "subfield", subfield_id, "trashed subfield")
    doc["subfields"].append(entry["subfield"])
    doc.setdefault("concepts", []).extend(entry.get("concepts", []))
    return doc


# ── Merge / rename ───────────────────────────────────────────────────────────


def merge_subfields(doc: SubfieldsDoc, src_id: int, dst_id: int) -> SubfieldsDoc:
    """Move every concept of *src_id* under *dst_id*, then drop the source subfield."""
    if int(src_id) == int(dst_id):
        raise ValueError("Cannot merge a subfield into itself.")
    _subfield(doc, src_id)
    _subfield(doc, dst_id)
    for c in doc.get("concepts", []):
        if int(c.get("subfield_id", -1)) == int(src_id):
            c["subfield_id"] = int(dst_id)
    doc["subfields"] = [s for s in doc["subfields"] if int(s.get("id", -1)) != int(src_id)]
    return doc


def merge_concepts(doc: SubfieldsDoc, src_id: int, dst_id: int) -> SubfieldsDoc:
    """Fold the source concept's terms (and merge groups) into the destination."""
    if int(src_id) == int(dst_id):
        raise ValueError("Cannot merge a concept into itself.")
    src = _concept(doc, src_id)
    dst = _concept(doc, dst_id)
    indices = [int(x) for x in dst.get("term_indices", [])]
    dst["term_indices"] = indices + [
        int(x) for x in src.get("term_indices", []) if int(x) not in indices
    ]
    if src.get("term_merges"):
        dst.setdefault("term_merges", []).extend(src["term_merges"])
    seen = {str(t).lower() for t in dst.get("top_terms", [])}
    for t in src.get("top_terms", []) or []:
        if str(t).lower() not in seen:
            dst.setdefault("top_terms", []).append(t)
    # Statuses travel with their terms: a ride-along stays ride-along in the merged
    # concept, a subfield-only term keeps its status (same or merged subfield — the
    # operator merged them on purpose).
    for key in _STATUS_LIST.values():
        have = {_norm_term(t) for t in dst.get(key) or []}
        for t in src.get(key) or []:
            if _norm_term(t) not in have:
                dst.setdefault(key, []).append(t)
                have.add(_norm_term(t))
    doc["concepts"] = [c for c in doc["concepts"] if int(c.get("id", -1)) != int(src_id)]
    return doc


def rename_subfield(
    doc: SubfieldsDoc,
    subfield_id: int,
    label: str | None = None,
    label_fr: str | None = None,
) -> SubfieldsDoc:
    """Set the EN/FR labels of a subfield (None leaves a side unchanged)."""
    s = _subfield(doc, subfield_id)
    if label is not None:
        s["label"] = label
    if label_fr is not None:
        s["label_fr"] = label_fr
    return doc


def rename_concept(
    doc: SubfieldsDoc,
    concept_id: int,
    label: str | None = None,
    label_fr: str | None = None,
) -> SubfieldsDoc:
    """Set the EN/FR labels of a concept (None leaves a side unchanged)."""
    c = _concept(doc, concept_id)
    if label is not None:
        c["label"] = label
    if label_fr is not None:
        c["label_fr"] = label_fr
    return doc


# ── Colors & validation ──────────────────────────────────────────────────────


def restamp_colors(doc: SubfieldsDoc) -> SubfieldsDoc:
    """Re-derive persisted colors after edits, in place.

    Subfield hues stay stable by id (``subfield_color``); concept shades are
    re-derived from their CURRENT subfield's hue (``concept_shade``), so a
    moved or merged concept picks up its new family color. Also rebuilds each
    subfield's ``concept_ids`` from the concepts' ``subfield_id`` (the source
    of truth after edits).

    A subfield may carry an explicit ``pinned_color`` (e.g. a field-consistent
    scheme set by the curator); it overrides the palette and survives re-apply
    and bundle import (the palette is only the fallback when no colour is pinned).
    """
    concepts = doc.get("concepts", [])
    by_sid: dict[int, list[dict[str, Any]]] = {}
    for c in concepts:
        by_sid.setdefault(int(c.get("subfield_id", -1)), []).append(c)
    for s in doc.get("subfields", []):
        sid = int(s.get("id", -1))
        if s.get("is_other"):
            base = _OTHER_COLOR
        else:
            base = s.get("pinned_color") or subfield_color(sid)
        s["color"] = base
        mine = by_sid.get(sid, [])
        s["concept_ids"] = [int(c["id"]) for c in mine if "id" in c]
        for j, c in enumerate(mine):
            c["color"] = concept_shade(base, j, len(mine))
    return doc


def refresh_subfield_top_terms(
    doc: SubfieldsDoc, max_terms: int = 20, *, only_missing: bool = False
) -> SubfieldsDoc:
    """Rebuild each subfield's seed ``top_terms`` from its CURRENT concepts.

    Researcher assignment at apply time computes subfield centroids from
    ``top_terms`` — after moves/merges/trashes those must reflect the edited
    content, not the original draft. With ``only_missing`` subfields that
    already carry seeds keep them (used at apply time to heal docs written
    without seeds, without overriding deliberate curation).

    A concept's declared ``ride_along_terms`` (display-only generics of the
    three-status attribution model) are excluded from the seeds so they never
    steer the researcher-assignment centroids; ``subfield_only_terms`` stay —
    they are within the subfield's scope.
    """
    by_sid: dict[int, list[str]] = {}
    for c in doc.get("concepts", []):
        sid = int(c.get("subfield_id", -1))
        _sf_only, ride = _term_status_sets(c)
        for t in c.get("top_terms", []) or []:
            if str(t).strip().lower() in ride:
                continue
            bucket = by_sid.setdefault(sid, [])
            if t not in bucket:
                bucket.append(t)
    for s in doc.get("subfields", []):
        if only_missing and s.get("top_terms"):
            continue
        terms = by_sid.get(int(s.get("id", -1)))
        if terms:
            s["top_terms"] = terms[:max_terms]
    return doc


def _iter_unit_indices(entry: dict[str, Any]) -> list[int]:
    group = entry.get("merge_group")
    if group:
        return [int(x) for x in group]
    return [int(entry["term_index"])]


def validate_doc(
    doc: SubfieldsDoc, n_terms: int | None = None, terms_by_idx: list[str] | None = None
) -> list[str]:
    """Check the structural invariants of an edited curated doc.

    Returns a list of human-readable problems (empty = valid):

    - subfield / concept ids are unique (across active AND stash/trash);
    - every active concept's ``subfield_id`` resolves to an active subfield;
    - no term index appears in more than one place (active concepts,
      stashed terms, trashed terms — merge groups included);
    - term indices are within ``[0, n_terms)`` when *n_terms* is given;
    - no term is both subfield-only and ride-along, and — when *terms_by_idx*
      is given — every status string names one of the concept's own canonical
      terms (a stale string would silently do nothing at apply time).
    """
    errors: list[str] = []
    sf_ids = [int(s.get("id", -1)) for s in doc.get("subfields", [])]
    if len(sf_ids) != len(set(sf_ids)):
        errors.append("Duplicate subfield ids.")
    stash = doc.get("stash") or {}
    trash = doc.get("trash") or {}
    trashed_sf = [e.get("subfield", {}) for e in trash.get("subfields", [])]
    all_sf_ids = sf_ids + [int(s.get("id", -1)) for s in trashed_sf]
    if len(all_sf_ids) != len(set(all_sf_ids)):
        errors.append("A trashed subfield shares its id with an active one.")

    active_concepts = list(doc.get("concepts", []))
    held_concepts = (
        [e.get("concept", {}) for e in stash.get("concepts", [])]
        + [e.get("concept", {}) for e in trash.get("concepts", [])]
        + [c for e in trash.get("subfields", []) for c in e.get("concepts", [])]
    )
    cids = [int(c.get("id", -1)) for c in active_concepts + held_concepts]
    if len(cids) != len(set(cids)):
        errors.append("Duplicate concept ids (active + stash/trash).")

    sf_id_set = set(sf_ids)
    for c in active_concepts:
        if int(c.get("subfield_id", -1)) not in sf_id_set:
            errors.append(
                f"Concept {c.get('id')} points to unknown subfield {c.get('subfield_id')}."
            )

    seen: dict[int, str] = {}

    def _claim(indices: list[int], where: str) -> None:
        for ti in indices:
            if n_terms is not None and not (0 <= ti < n_terms):
                errors.append(f"Term index {ti} out of range [0, {n_terms}) in {where}.")
            if ti in seen:
                errors.append(f"Term index {ti} appears in both {seen[ti]} and {where}.")
            else:
                seen[ti] = where

    for c in active_concepts:
        _claim([int(x) for x in c.get("term_indices", [])], f"concept {c.get('id')}")
    for e in stash.get("terms", []):
        _claim(_iter_unit_indices(e), "stash")
    for e in trash.get("terms", []):
        _claim(_iter_unit_indices(e), "trash")

    for c in active_concepts:
        sf_only, ride = _term_status_sets(c)
        for t in sorted(sf_only & ride):
            errors.append(f"Concept {c.get('id')}: '{t}' is both subfield-only and ride-along.")
        if terms_by_idx is not None:
            own = {
                _norm_term(terms_by_idx[ti])
                for ti in canonical_indices(c)
                if 0 <= ti < len(terms_by_idx)
            }
            for t in sorted((sf_only | ride) - own):
                errors.append(f"Concept {c.get('id')}: status term '{t}' is not one of its terms.")
    return errors


def cloned(doc: SubfieldsDoc) -> SubfieldsDoc:
    """Deep-copy a doc — handy for previewing an edit without mutating the original."""
    return copy.deepcopy(doc)
