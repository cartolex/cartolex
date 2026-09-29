# SPDX-License-Identifier: MIT
"""Subfield hierarchy: deterministic draft → curated → applied.

Two-stage workflow (the paths are the run context's)::

    draft_subfields(ctx)     → ctx.paths.subfields_draft_json
    # the operator reviews the draft in an editor (rename, move, merge, term statuses)
    apply_subfields(ctx)     → ctx.paths.subfields_json

``subfields_draft.json`` is the **deterministic** taxonomy: the term clusters become
concepts, a Ward cut over the concept centroids groups them into subfields, and every node
is labelled by its dominant keyword (see :func:`cartolex.atlas.hierarchy.build_hierarchy`).
No language model is involved; the draft is a *starting point* whose names and placements
the operator settles by hand. The curated document (``ctx.paths.subfields_curated_json``) is
the source of truth, which is then "applied" to produce the final document with
per-researcher membership and the lexicon weights (three-status term model, see
:func:`compute_lexicon_weights`).

Until 0.7.0 the draft went through a two-pass LLM curation (concept labelling + subfield
naming). It was retired: its users redid the work by hand anyway, and the deterministic
hierarchy is a better starting point than weak machine names.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_distances

if TYPE_CHECKING:
    from cartolex.context import RunContext

logger = logging.getLogger(__name__)

# ── JSON schema version ────────────────────────────────────────────────────────

SUBFIELDS_SCHEMA_VERSION = "1.0"

# ── Type aliases ──────────────────────────────────────────────────────────────

SubfieldEntry = dict[str, Any]
SubfieldsDoc = dict[str, Any]

# ── Clustering / synthesis defaults ──────────────────────────────────────────


# ── Subfield palette (shared by every drawing of the map) ─────────────────────

# Canonical, stable colour palette assigned to subfields by order — the single
# colour source: persisted into the hierarchy by _assign_colors, then consumed
# by interactive maps, offline sites and the static PNGs.
#
# 40 maximally-distinct colours, selected by a greedy max-min search in CIELAB
# (mid-lightness L∈[38,76], saturated but non-neon C∈[32,92]); minimum pairwise
# ΔE ≈ 23, versus ≈ 6 for the former 20-colour set — so even adjacent subfields
# stay easy to tell apart. Greedy order keeps consecutive indices far apart too.
SUBFIELD_PALETTE = (
    "#d22d3c", "#1ed23c", "#0069ff", "#0096a5", "#e1b400", "#ff3cd2", "#e1a5d2",
    "#4b691e", "#d29669", "#3ca5ff", "#ff780f", "#4bd296", "#873c96", "#964b5a",
    "#a5c34b", "#e10078", "#c369ff", "#4b5a96", "#964b00", "#ff8796", "#00870f",
    "#b496ff", "#ff78c3", "#00785a", "#a5b478", "#967800", "#ff875a", "#0069d2",
    "#e12d00", "#a500a5", "#69c3f0", "#78c300", "#693cc3", "#b43c78", "#9696d2",
    "#1ec3b4", "#f0a54b", "#ff87ff", "#0f78a5", "#965a3c",
)  # fmt: skip


def subfield_color(index: int) -> str:
    """Stable colour for the subfield at *index* (wraps around the palette)."""
    return SUBFIELD_PALETTE[index % len(SUBFIELD_PALETTE)]


def concept_shade(base_hex: str, index: int, count: int) -> str:
    """A distinct lightened shade of a subfield's base colour for its concept ``#index``.

    Concept 0 keeps the subfield's base colour; later concepts mix progressively toward white
    (up to ~55%), so every concept in a subfield is a recognisable tint of the same hue. Used
    everywhere a concept is drawn (maps, previews, curation views) so colours stay consistent.
    """
    base = base_hex.lstrip("#")
    r, g, b = int(base[0:2], 16), int(base[2:4], 16), int(base[4:6], 16)
    t = 0.0 if count <= 1 else 0.55 * (index / (count - 1))

    def mix(c: int) -> int:
        return round(c + (255 - c) * t)

    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def term_cluster_subfield_map(subfields: list[SubfieldEntry]) -> dict[int, int]:
    """Map term-cluster id → subfield id from each subfield's ``member_cluster_refs``.

    Synthesis records member clusters as ``"T{n}"`` (term clusters) and
    ``"R{n}"`` (researcher clusters); only the term clusters carry a UMAP term
    position, so only those are mapped — letting the term map be coloured and
    labelled by subfield. Subfields lacking an ``id`` or term members are skipped.
    """
    mapping: dict[int, int] = {}
    for sf in subfields:
        sid = sf.get("id")
        if sid is None:
            continue
        for ref in sf.get("member_cluster_refs", []):
            if isinstance(ref, str) and ref[:1] == "T" and ref[1:].isdigit():
                mapping[int(ref[1:])] = int(sid)
    return mapping


def compute_lexicon_weights(
    doc: SubfieldsDoc, X: np.ndarray, terms: list[str], *, chunk_bytes: int | None = None
) -> pd.DataFrame:
    """Researcher-equal lexicon weights over the curated hierarchy (single-track).

    Accepts a dense array or a scipy-sparse matrix, never made dense as a
    whole: the totals stay sparse and the term weights are summed by chunks of
    terms (:func:`cartolex.lexicon.theme_tree.keyword_weights`), with exactly
    the numbers of the whole matrix made dense (*chunk_bytes*: the memory of a
    chunk, default :data:`cartolex.lexicon.theme_tree.CHUNK_BYTES`).

    Each researcher's row of *X* is L1-normalised over the curated term columns
    (kept concepts' ``term_indices``); term weight = the column sum of those
    shares, so every contributing researcher carries exactly one unit of mass.
    ``term_merges`` fold variant weights into the canonical term, which is the
    only one kept. Returns one row per canonical curated term with columns
    ``term, term_index, concept_id, subfield_id, weight, share``; mutates *doc*
    in place, stamping ``weight``/``share`` on every concept and subfield
    (concept = Σ terms, subfield = Σ concepts, share = weight ÷ Σ researcher
    units).

    Term statuses (optional per-concept ``subfield_only_terms`` /
    ``ride_along_terms`` string lists, see :func:`_term_status_sets`): a
    subfield-only term is excluded from its concept's weight/share but credited
    directly to the host subfield's; a ride-along term contributes to neither.
    The returned per-term display rows keep every term with its weight
    regardless of status. Absent lists = every term defining (legacy).
    """
    from .theme_tree import CHUNK_BYTES, held_usage, keyword_weights, row_totals

    kept_sf_ids = {int(s["id"]) for s in doc["subfields"]}
    concepts = [c for c in doc.get("concepts", []) if int(c.get("subfield_id", -1)) in kept_sf_ids]
    curated: list[int] = sorted({int(ti) for c in concepts for ti in c["term_indices"]})
    col_of = {ti: j for j, ti in enumerate(curated)}
    H = held_usage(X, curated)
    totals = row_totals(H)
    term_w = keyword_weights(H, totals, chunk_bytes=chunk_bytes or CHUNK_BYTES)
    total = float((totals > 0).sum()) or 1.0

    # True merge: fold variant weights into the canonical index, drop variants.
    canon_of: dict[int, int] = {}
    for c in concepts:
        for group in c.get("term_merges") or []:
            for ti in group[1:]:
                canon_of[int(ti)] = int(group[0])
    folded: dict[int, float] = {}
    for ti in curated:
        tgt = canon_of.get(ti, ti)
        folded[tgt] = folded.get(tgt, 0.0) + float(term_w[col_of[ti]])

    rows: list[dict[str, Any]] = []
    sf_direct: dict[int, float] = {}  # subfield-only term weights, credited past the concept
    for c in concepts:
        kept_tis = [int(ti) for ti in c["term_indices"] if int(ti) not in canon_of]
        sf_only, ride = _term_status_sets(c)
        c_weight = 0.0
        for ti in kept_tis:
            t = terms[ti].strip().lower()
            if t in ride:
                continue  # display only: no share at either level
            if t in sf_only:
                sid = int(c["subfield_id"])
                sf_direct[sid] = sf_direct.get(sid, 0.0) + folded.get(ti, 0.0)
            else:
                c_weight += folded.get(ti, 0.0)
        c["weight"] = round(c_weight, 6)
        c["share"] = round(c_weight / total, 6)
        for ti in kept_tis:  # display term rows keep every status
            rows.append(
                {
                    "term": terms[ti],
                    "term_index": ti,
                    "concept_id": int(c["id"]),
                    "subfield_id": int(c["subfield_id"]),
                    "weight": round(folded.get(ti, 0.0), 6),
                    "share": round(folded.get(ti, 0.0) / total, 6),
                }
            )
    by_sf: dict[int, float] = dict(sf_direct)
    for c in concepts:
        by_sf[int(c["subfield_id"])] = by_sf.get(int(c["subfield_id"]), 0.0) + float(c["weight"])
    for s in doc["subfields"]:
        w = by_sf.get(int(s["id"]), 0.0)
        s["weight"] = round(w, 6)
        s["share"] = round(w / total, 6)
    cols = ["term", "term_index", "concept_id", "subfield_id", "weight", "share"]
    return pd.DataFrame(rows, columns=pd.Index(cols))


def curated_term_maps(
    doc: SubfieldsDoc | None, terms_by_idx: list[str]
) -> tuple[dict[str, str], set[str]]:
    """Return (lower variant → lower canonical term map, curated canonical term set).

    Built from the applied hierarchy: the curated set is every kept concept's
    ``term_indices`` resolved through *terms_by_idx* (row order = SVD term index),
    minus merge variants; the map folds each ``term_merges`` variant string onto
    its canonical term string. Everything is lowercased for case-insensitive
    joins. Both are empty when *doc* predates the concepts schema.
    """
    if not isinstance(doc, dict) or not terms_by_idx:
        return {}, set()
    kept_sf_ids = {
        int(sf.get("id", i))
        for i, sf in enumerate(doc.get("subfields", []))
        if sf.get("keep", True)
    }
    canon: dict[str, str] = {}
    curated: set[str] = set()
    n = len(terms_by_idx)

    def _t(ti: int) -> str | None:
        return terms_by_idx[ti].strip().lower() if 0 <= ti < n else None

    for c in doc.get("concepts", []):
        if int(c.get("subfield_id", -1)) not in kept_sf_ids:
            continue
        variants: set[int] = set()
        for group in c.get("term_merges") or []:
            tgt = _t(int(group[0]))
            if tgt is None:
                continue
            for ti in group[1:]:
                src = _t(int(ti))
                if src is not None:
                    canon[src] = tgt
                    variants.add(int(ti))
        for ti in c.get("term_indices", []):
            if int(ti) in variants:
                continue
            t = _t(int(ti))
            if t is not None:
                curated.add(t)
    return canon, curated


def concept_term_index(
    doc: SubfieldsDoc | None,
) -> tuple[dict[int, int], dict[int, dict[str, Any]]]:
    """Return (SVD-term-index → concept id, concept id → {label, label_fr, color, subfield_id}).

    Reads the persisted hierarchy (``concepts`` with ``term_indices``) so every surface
    (interactive maps, offline sites, static plots) colours keywords by concept consistently.
    Colours prefer the persisted concept colour and fall back to a ``concept_shade`` of
    the subfield colour. Concepts of dropped subfields are skipped. Returns empty maps
    when *doc* is not a dict or predates the concepts/term_indices schema.
    """
    if not isinstance(doc, dict):
        return {}, {}
    concepts = doc.get("concepts") or []
    subfields = doc.get("subfields") or []
    if not concepts or not any("term_indices" in c for c in concepts):
        return {}, {}
    sf_color_by_id: dict[int, str] = {}
    for i, sf in enumerate(subfields):
        if not sf.get("keep", True):
            continue
        sid = int(sf.get("id", i))
        sf_color_by_id[sid] = sf.get("color") or subfield_color(sid)
    # Position of each concept within its subfield drives the fallback shade.
    by_sid: dict[int, list[int]] = {}
    for c in concepts:
        if "id" in c:
            by_sid.setdefault(int(c.get("subfield_id", -1)), []).append(int(c["id"]))
    pos_in_sf = {cid: (j, len(cids)) for cids in by_sid.values() for j, cid in enumerate(cids)}
    term_to_cid: dict[int, int] = {}
    info: dict[int, dict[str, Any]] = {}
    for c in concepts:
        if "id" not in c:
            continue
        cid = int(c["id"])
        sid = int(c.get("subfield_id", -1))
        if sid not in sf_color_by_id:
            continue
        j, n = pos_in_sf.get(cid, (0, 1))
        info[cid] = {
            "label": c.get("label", f"#{cid}"),
            "label_fr": c.get("label_fr") or c.get("label", f"#{cid}"),
            "color": c.get("color") or concept_shade(sf_color_by_id[sid], j, n),
            "subfield_id": sid,
        }
        for ti in c.get("term_indices", []):
            term_to_cid[int(ti)] = cid
    return term_to_cid, info


def _term_status_sets(concept: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Normalised (subfield-only, ride-along) term-string sets declared on *concept*.

    The optional ``subfield_only_terms`` / ``ride_along_terms`` lists hold term
    STRINGS matched case-insensitively and whitespace-stripped against the
    concept's own terms. Absent/empty lists = every term is defining.
    """

    def _norm(values: Any) -> set[str]:
        return {str(t).strip().lower() for t in (values or []) if str(t).strip()}

    return _norm(concept.get("subfield_only_terms")), _norm(concept.get("ride_along_terms"))


def _group_maps(
    doc: SubfieldsDoc | None,
    terms_by_idx: list[str],
) -> tuple[dict[str, int], dict[int, int], dict[int, str], dict[int, str], dict[str, int]]:
    """Shared builder behind :func:`term_to_group_maps` / :func:`term_to_subfield_direct`.

    Returns ``(term_to_concept, concept_to_subfield, concept_labels,
    subfield_labels, term_to_subfield_direct)``. Terms a concept declares
    ``subfield_only_terms`` or ``ride_along_terms`` (see
    :func:`_term_status_sets`) are excluded from ``term_to_concept``;
    subfield-only terms land in ``term_to_subfield_direct`` (term → the host
    concept's subfield id) instead, ride-alongs in neither.
    """
    if not isinstance(doc, dict):
        return {}, {}, {}, {}, {}
    n = len(terms_by_idx)

    def _t(ti: int) -> str | None:
        return terms_by_idx[ti].strip().lower() if 0 <= ti < n else None

    subfield_labels: dict[int, str] = {}
    kept_sids: set[int] = set()
    for i, sf in enumerate(doc.get("subfields", [])):
        if not sf.get("keep", True):
            continue
        sid = int(sf.get("id", i))
        kept_sids.add(sid)
        subfield_labels[sid] = sf.get("label_fr") or sf.get("label") or f"#{sid}"

    term_to_concept: dict[str, int] = {}
    concept_to_subfield: dict[int, int] = {}
    concept_labels: dict[int, str] = {}
    term_to_sf_direct: dict[str, int] = {}
    kept_concepts: list[tuple[dict[str, Any], int, int, set[str], set[str]]] = []
    for c in doc.get("concepts", []):
        if "id" not in c:
            continue
        sid = int(c.get("subfield_id", -1))
        if kept_sids and sid not in kept_sids:
            continue
        cid = int(c["id"])
        concept_to_subfield[cid] = sid
        concept_labels[cid] = c.get("label_fr") or c.get("label") or f"#{cid}"
        sf_only, ride = _term_status_sets(c)
        kept_concepts.append((c, cid, sid, sf_only, ride))

    def _add(t: str | None, cid: int, sid: int, sf_only: set[str], ride: set[str]) -> None:
        if t is None:
            return
        if t in ride:
            return
        if t in sf_only:
            term_to_sf_direct.setdefault(t, sid)
            return
        term_to_concept.setdefault(t, cid)

    # Two claiming tiers: index-anchored membership (term_indices + merges)
    # binds a term to its concept BEFORE any concept's representative
    # ``top_terms`` may fill in — a stale ``top_terms`` entry left behind by a
    # curation move must never steal a term whose index lives elsewhere.
    for c, cid, sid, sf_only, ride in kept_concepts:
        for ti in c.get("term_indices", []):
            _add(_t(int(ti)), cid, sid, sf_only, ride)
        for group in c.get("term_merges") or []:
            for ti in group:
                _add(_t(int(ti)), cid, sid, sf_only, ride)
    for c, cid, sid, sf_only, ride in kept_concepts:
        for term in c.get("top_terms", []):
            _add(str(term).strip().lower(), cid, sid, sf_only, ride)
    return term_to_concept, concept_to_subfield, concept_labels, subfield_labels, term_to_sf_direct


def term_to_group_maps(
    doc: SubfieldsDoc | None,
    terms_by_idx: list[str],
) -> tuple[dict[str, int], dict[int, int], dict[int, str], dict[int, str]]:
    """Resolve the applied hierarchy into string-keyed lookup maps.

    Returns ``(term_to_concept, concept_to_subfield, concept_labels,
    subfield_labels)``, all lowercased term keys.  ``term_to_concept`` prefers
    each concept's ``term_indices`` (resolved through *terms_by_idx*, whose row
    order is the SVD term index) and also folds declared ``term_merges`` variant
    strings and any representative ``top_terms`` onto their concept — so a
    researcher who used a merge variant still gets credited.  Concepts of dropped
    subfields are skipped.  Empty maps when *doc* predates the concepts schema.

    Terms a concept declares ``subfield_only_terms`` or ``ride_along_terms``
    are NOT defining and are excluded from ``term_to_concept``; the
    subfield-only ones are exposed separately by
    :func:`term_to_subfield_direct`.

    This is the single source of the attribution maps, shared by the fitted
    persons and by any projected set (persons placed on a fitted map).
    """
    term_to_concept, concept_to_subfield, concept_labels, subfield_labels, _direct = _group_maps(
        doc, terms_by_idx
    )
    return term_to_concept, concept_to_subfield, concept_labels, subfield_labels


def term_to_subfield_direct(
    doc: SubfieldsDoc | None,
    terms_by_idx: list[str],
) -> dict[str, int]:
    """Term → host concept's subfield id for declared ``subfield_only_terms``.

    Sibling of :func:`term_to_group_maps` (same doc/terms resolution, same
    lowercased term keys): a subfield-only term is broader than its concept but
    within the subfield's scope, so it contributes to the SUBFIELD share only —
    pass this map to :func:`researcher_group_weights` as
    ``term_to_subfield_direct``. Empty for docs without status lists.
    """
    return _group_maps(doc, terms_by_idx)[4]


def researcher_group_weights(
    scored_terms: Any,
    *,
    term_to_concept: dict[str, int],
    concept_to_subfield: dict[int, int],
    term_to_subfield_direct: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate a researcher's *real* scored terms into (subfield, concept) weights.

    Evidence-based by construction: a term contributes to a concept (and that
    concept's subfield) only when it is one of that concept's curated terms — the
    same notion of attribution used for the fitted persons.  A projected person
    therefore never receives a subfield/concept they have no term evidence for
    (contrast the SVD-centroid proximity this replaces).

    *scored_terms* is an iterable of ``(term, score)`` pairs or ``{"term","score"}``
    dicts (matched case-insensitively, whitespace-stripped).  Build the maps with
    :func:`term_to_group_maps`.  Concept weights come from defining terms only
    (``term_to_concept`` excludes a concept's declared subfield-only and
    ride-along terms); when the optional *term_to_subfield_direct* map (see
    :func:`term_to_subfield_direct`) is given, subfield weights are the concept
    rollup PLUS the direct subfield-only contributions.  Weights are
    L1-normalised to sum 1 within each level over the contributing terms,
    returned as ``[{"id", "weight"}]`` sorted by descending weight; empty when
    nothing matches.
    """
    direct = term_to_subfield_direct or {}
    if not term_to_concept and not direct:
        return [], []
    concept_score: dict[int, float] = {}
    subfield_direct: dict[int, float] = {}
    for item in scored_terms:
        if isinstance(item, dict):
            term, score = item.get("term", ""), item.get("score", 0.0)
        else:
            term, score = item
        key = str(term).strip().lower()
        cid = term_to_concept.get(key)
        if cid is not None:
            concept_score[cid] = concept_score.get(cid, 0.0) + float(score)
            continue
        sid = direct.get(key)
        if sid is not None:
            subfield_direct[sid] = subfield_direct.get(sid, 0.0) + float(score)

    if not concept_score and not subfield_direct:
        return [], []

    subfield_score: dict[int, float] = dict(subfield_direct)
    for cid, sc in concept_score.items():
        sid = concept_to_subfield.get(cid)
        if sid is None:
            continue
        subfield_score[sid] = subfield_score.get(sid, 0.0) + sc

    def _normalise(scores: dict[int, float]) -> list[dict[str, Any]]:
        total = sum(scores.values())
        if total <= 0:
            return []
        out = [{"id": int(k), "weight": round(v / total, 4)} for k, v in scores.items() if v > 0]
        out.sort(key=lambda w: w["weight"], reverse=True)
        return out

    return _normalise(subfield_score), _normalise(concept_score)


def concept_centroids_from_coords(
    doc: SubfieldsDoc | None,
    xs: Sequence[float],
    ys: Sequence[float],
) -> list[dict[str, Any]]:
    """Concept label anchors: mean UMAP position of each concept's member terms.

    *xs*/*ys* are the term coordinates indexed by SVD term index (row order of
    ``umap_terms_clustered.csv`` — the same invariant the atlas relies on).
    Out-of-range indices and NaN coordinates are skipped; concepts with no
    resolvable coordinate are omitted. Each entry is
    ``{id, label, label_fr, color, subfield_id, x, y, n}``.
    """
    term_to_cid, info = concept_term_index(doc)
    if not info:
        return []
    limit = min(len(xs), len(ys))
    sums: dict[int, list[float]] = {}
    for ti, cid in term_to_cid.items():
        if not 0 <= ti < limit:
            continue
        x, y = float(xs[ti]), float(ys[ti])
        if math.isnan(x) or math.isnan(y):
            continue
        acc = sums.setdefault(cid, [0.0, 0.0, 0.0])
        acc[0] += x
        acc[1] += y
        acc[2] += 1
    return [
        {"id": cid, **info[cid], "x": sx / n, "y": sy / n, "n": int(n)}
        for cid, (sx, sy, n) in sorted(sums.items())
        if n > 0
    ]


# ── Internal helpers ──────────────────────────────────────────────────────────


def _researcher_id_series(meta_ind: pd.DataFrame) -> pd.Series:
    """Canonical researcher id per row of ``meta_ind``.

    The lexical ``meta_ind`` stores the canonical ``make_researcher_id`` hash in
    an ``id`` column. Earlier code looked only for ``researcher_id`` and silently
    fell back to positional indices ("0", "1", …) — which do not match the ids
    every other output uses, so researcher↔subfield attribution (member lists,
    weight CSV, per-person charts) joined to nothing.
    """
    for col in ("researcher_id", "id"):
        if col in meta_ind.columns:
            return meta_ind[col].astype(str)
    return pd.Series([str(i) for i in range(len(meta_ind))], index=meta_ind.index)


def _llm_cache_key(
    system_prompt: str, user_content: str, model: str, schema: dict[str, Any], temperature: float
) -> str:
    """Stable hash of everything that determines an LLM response (the exact prompt)."""
    blob = json.dumps(
        {
            "system": system_prompt,
            "user": user_content,
            "model": model,
            "schema": schema,
            "temperature": temperature,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cached_chat_json(
    client: Any,
    system_prompt: str,
    user_content: str,
    *,
    response_schema: dict[str, Any],
    model: str,
    temperature: float,
    cache_dir: Path | None,
    label: str = "",
) -> dict[str, Any]:
    """``client.chat_json`` with an on-disk cache keyed on the exact prompt.

    A cache hit returns the stored response without an API call — so re-running the proposal on
    unchanged cluster evidence is free, deterministic, and does not churn labels the operator may
    have started curating. Each *fresh* call also writes a dated record under ``<cache_dir>/audit/``
    (``<date>__<label>__<key>.json``) holding the exact input prompt, the parsed response, and the
    full LLM message (content plus any reasoning the model exposes — ``mistral-medium-3.5`` exposes
    none, but a reasoning model would land here) so the operator can inspect what was produced.
    """
    key = _llm_cache_key(system_prompt, user_content, model, response_schema, temperature)
    cache_file = (cache_dir / f"{key}.json") if cache_dir else None
    if cache_file and cache_file.exists():
        try:
            logger.info("LLM cache hit (%s…); skipping API call.", key[:12])
            return json.loads(cache_file.read_text(encoding="utf-8"))["response"]
        except (OSError, json.JSONDecodeError, KeyError):
            logger.warning("LLM cache file unreadable; recomputing.")

    result = client.chat_json(system_prompt, user_content, response_schema=response_schema)

    if cache_dir is not None:
        now = datetime.now()
        record = {
            "timestamp": now.isoformat(timespec="seconds"),
            "label": label,
            "model": model,
            "temperature": temperature,
            "system_prompt": system_prompt,
            "user_content": user_content,
            "response": result,
            # full LLM message incl. the model's reasoning/thinking (whatever the SDK exposes)
            "message": getattr(client, "last_message", None),
        }
        blob = json.dumps(record, indent=2, ensure_ascii=False)
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / f"{key}.json").write_text(blob, encoding="utf-8")
            audit_dir = cache_dir / "audit"
            audit_dir.mkdir(parents=True, exist_ok=True)
            safe = (label or "call").replace("/", "-")
            audit_file = audit_dir / f"{now:%Y-%m-%d_%H%M%S}__{safe}__{key[:10]}.json"
            audit_file.write_text(blob, encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write LLM cache/audit: %s", exc)
    return result


def _subfield_centroids(subfields: list[SubfieldEntry], *, data: Any, emb: Any) -> np.ndarray:
    """SVD-space centroid of each subfield's seed terms (zero vector if none match)."""
    term_to_idx = {t: i for i, t in enumerate(data.terms)}
    rows: list[np.ndarray] = []
    for sf in subfields:
        idxs = [term_to_idx[t] for t in sf.get("top_terms", []) if t in term_to_idx]
        if idxs:
            rows.append(emb.Z_terms[idxs].mean(axis=0))
        else:
            logger.warning(
                "Subfield '%s' has no matching terms; using zero centroid.", sf.get("label", "?")
            )
            rows.append(np.zeros(emb.Z_ind.shape[1]))
    return np.array(rows)


def _subfield_distance_matrix(subfields: list[SubfieldEntry], *, data: Any, emb: Any) -> np.ndarray:
    """Cosine distance from each researcher to each subfield centroid (n_res, n_sf)."""
    return cosine_distances(emb.Z_ind, _subfield_centroids(subfields, data=data, emb=emb))


def _researcher_counts(subfields: list[SubfieldEntry], *, data: Any, emb: Any) -> list[int]:
    """Number of researchers nearest to each subfield centroid (argmin assignment)."""
    if not subfields:
        return []
    dist = _subfield_distance_matrix(subfields, data=data, emb=emb)
    counts = np.bincount(dist.argmin(axis=1), minlength=len(subfields))
    return [int(c) for c in counts]


def subfield_label(sf: dict[str, Any], lang: str, reference_language: str = "en") -> str:
    """Display label for a subfield in ``lang``.

    ``label`` holds the *reference-language* form; every other display language
    lives in ``label_<lang>``. When ``lang`` is the reference language the
    ``label`` field is used; otherwise ``label_<lang>`` with a fallback to
    ``label``. With the default ``reference_language="en"`` this reproduces the
    legacy ``label`` (English) + ``label_fr`` behaviour.
    """
    if lang == reference_language:
        return str(sf.get("label") or sf.get(f"label_{lang}") or "")
    return str(sf.get(f"label_{lang}") or sf.get("label") or "")


# ── Public API ────────────────────────────────────────────────────────────────


def _other_display_languages(
    display_languages: tuple[str, ...], reference_language: str
) -> list[str]:
    """Display languages that need an explicit ``label_<lang>`` field — every
    configured display language except the reference (which lives in ``label``)."""
    return [lang for lang in display_languages if lang != reference_language]


def _enforce_label_distinctness(h: dict[str, Any]) -> None:
    """Make sibling labels distinct (in place): the subfields, and each subfield's concepts.

    In each language (``label`` and every ``label_<lang>``), a label that
    repeats a sibling's takes a distinguishing top term
    (``Complex Fluid Dynamics (granular)``), as
    :func:`cartolex.lexicon.labels.distinct_names` does for the theme tree. Idempotent.
    """
    from cartolex.lexicon.labels import distinct_names

    def dedupe(nodes: list[dict[str, Any]]) -> None:
        names = [{k: str(v) for k, v in d.items() if k.split("_")[0] == "label"} for d in nodes]
        distinct_names(names, [d.get("top_terms", []) for d in nodes])
        for d, own in zip(nodes, names, strict=True):
            d.update(own)

    by_cid = {c["id"]: c for c in h.get("concepts", [])}
    dedupe(h.get("subfields", []))
    for s in h.get("subfields", []):
        dedupe([by_cid[c] for c in s.get("concept_ids", []) if c in by_cid])


def _load_term_cluster_labels(term_clusters_csv: Path, terms: list[str]) -> np.ndarray:
    """Per-term cluster id of the clustering stage, aligned to *terms* (−1 where unknown).

    The hierarchy's concepts ARE the term clusters, so the draft requires the clustering to
    have run; a missing file is a clear, actionable error rather than an empty hierarchy.
    """
    if not Path(term_clusters_csv).exists():
        raise FileNotFoundError(
            f"{term_clusters_csv} not found — run the clustering stage before building the hierarchy."
        )
    df = pd.read_csv(term_clusters_csv)
    if "term" not in df.columns or "cluster" not in df.columns:
        raise ValueError(f"{term_clusters_csv} lacks 'term'/'cluster' columns.")
    by_term = dict(zip(df["term"].astype(str), df["cluster"].astype(int), strict=False))
    return np.array([int(by_term.get(str(t), -1)) for t in terms], dtype=int)


_OTHER_COLOR = "#9aa0b0"  # neutral grey for an `is_other` bag (legacy docs)


def _recompute_generality(h: dict[str, Any], Zn: np.ndarray) -> None:
    """Recompute concept + subfield ``generality`` (centroid·global) and flag one ``general``.

    Editing (moves, merges, drops) makes stored generalities stale, so they are recomputed
    from the current membership. An empty subfield is never flagged general.
    """
    from sklearn.preprocessing import normalize

    by_cid = {c["id"]: c for c in h["concepts"]}
    gc = normalize(Zn.mean(axis=0).reshape(1, -1))[0]

    def gen(idx: list[int]) -> float:
        cen = normalize(Zn[idx].mean(axis=0).reshape(1, -1))[0] if idx else gc
        return round(float(cen @ gc), 3)

    for c in h["concepts"]:
        c["generality"] = gen(c["term_indices"])
    for s in h["subfields"]:
        idx = [
            ti for cid in s["concept_ids"] if cid in by_cid for ti in by_cid[cid]["term_indices"]
        ]
        s["generality"] = gen(idx)
        s["general"] = False
    non_other = [s for s in h["subfields"] if not s.get("is_other") and s["concept_ids"]]
    if non_other:
        max(non_other, key=lambda s: (s["generality"], len(s["concept_ids"])))["general"] = True


def _assign_colors(h: dict[str, Any]) -> None:
    """Stamp a persisted colour on every subfield (base hue) and concept (shade), in place."""
    by_cid = {c["id"]: c for c in h["concepts"]}
    for s in h["subfields"]:
        base = _OTHER_COLOR if s.get("is_other") else subfield_color(int(s["id"]))
        s["color"] = base
        cids = s["concept_ids"]
        for j, cid in enumerate(cids):
            if cid in by_cid:
                by_cid[cid]["color"] = concept_shade(base, j, len(cids))


def _assemble_draft(
    h: dict[str, Any],
    data: Any,
    emb: Any,
    *,
    pairs_csv: Path | None,
    reference_language: str = "en",
    display_languages: tuple[str, ...] = ("fr", "en"),
) -> tuple[list[SubfieldEntry], list[dict[str, Any]]]:
    """Turn a :func:`build_hierarchy` result into the draft's (subfields, concepts) lists.

    Recomputes generality, names every node in each display language as the
    theme tree does (:func:`cartolex.lexicon.labels.node_names`, the forms from
    the consolidation pairs CSV): ``label`` in the reference language,
    ``label_<lang>`` in the others; disambiguates colliding sibling labels and
    stamps the persistent colour scheme (one hue per subfield, one shade per
    concept). Pure and deterministic — the same hierarchy always yields the
    same draft.
    """
    from sklearn.preprocessing import normalize

    from cartolex.lexicon.labels import keyword_forms, node_names

    if not h["concepts"]:
        return [], []
    terms = [str(t) for t in data.terms]
    scores = np.asarray(data.X.sum(axis=0)).ravel()
    others = _other_display_languages(display_languages, reference_language)
    langs = [reference_language, *others]
    forms = keyword_forms(pairs_csv, terms, langs, reference_language)
    by_cid = {c["id"]: c for c in h["concepts"]}

    def name(node: dict[str, Any], rows: list[int]) -> None:
        names = node_names(rows, terms, scores, forms, langs, reference_language)
        node["label"] = names.get(reference_language, node["label"])
        for lang in others:
            node[f"label_{lang}"] = names.get(lang) or node["label"]

    for c in h["concepts"]:
        name(c, c["term_indices"])
    for s in h["subfields"]:
        rows = [t for cid in s["concept_ids"] if cid in by_cid for t in by_cid[cid]["term_indices"]]
        name(s, rows)

    def _display_labels(d: dict[str, Any]) -> dict[str, str]:
        return {f"label_{lang}": d[f"label_{lang}"] for lang in others}

    Zn = normalize(np.asarray(emb.Z_terms, dtype=float))
    _recompute_generality(h, Zn)
    _enforce_label_distinctness(h)
    _assign_colors(h)

    by_cid = {c["id"]: c for c in h["concepts"]}
    candidates: list[SubfieldEntry] = []
    for s in h["subfields"]:
        candidates.append(
            {
                "source": "hierarchy",
                # The id the concepts' `subfield_id` references point at. Without it, any
                # consumer joining concepts to subfields (a curation tool, validate_doc)
                # sees dangling references.
                "id": s["id"],
                "label": s["label"],
                **_display_labels(s),
                "description": "",
                "member_cluster_refs": [],
                "top_terms": s.get("top_terms", []),
                "size": 0,
                "centroid_umap_x": None,
                "centroid_umap_y": None,
                "member_researcher_ids": [],
                "keep": True,
                "generality": s.get("generality"),
                "general": s.get("general", False),
                "is_other": False,
                "color": s.get("color"),
                "concept_labels": [by_cid[c]["label"] for c in s["concept_ids"] if c in by_cid],
            }
        )
    counts = _researcher_counts(candidates, data=data, emb=emb)
    for sf, n in zip(candidates, counts, strict=True):
        sf["size"] = int(n)
    concepts = [
        {
            "id": c["id"],
            "label": c["label"],
            **_display_labels(c),
            "subfield_id": c["subfield_id"],
            "generality": c.get("generality"),
            "term_indices": c["term_indices"],
            "term_merges": c.get("term_merges", []),
            "top_terms": c["top_terms"],
            "color": c.get("color"),
        }
        for c in h["concepts"]
    ]
    return candidates, concepts


def _require_lexical_models(lexical_data_json: Path, embeddings_json: Path) -> None:
    """Fail with an actionable message when the lexical models are missing.

    The subfield steps are SVD-based (term vectors from the stored embeddings); the
    UMAP layout is *not* a prerequisite (it runs last). Point the operator at the SVD
    and clustering stages when the SVD stage's files are absent (and name a model file
    of an earlier release, which is never read).
    """
    from cartolex.atlas.model_files import reject_legacy

    reject_legacy(lexical_data_json, embeddings_json, stage="SVD")
    missing = [p.name for p in (lexical_data_json, embeddings_json) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"Lexical model(s) not found: {', '.join(missing)}. "
            "Run the SVD and clustering stages first."
        )


def draft_subfields(
    ctx: RunContext,
    *,
    n_subfields: int = 12,
    clustering_signature: str | None = None,
) -> list[SubfieldEntry]:
    """Subfield draft stage: write ``ctx.paths.subfields_draft_json``; return its subfields.

    Concepts ARE the term clusters (per-term assignment in
    ``ctx.paths.terms_clustered_csv``); subfields are the Ward cut of the
    concept centroids at exactly *n_subfields*; labels are the dominant
    keyword of each node, French sides from the consolidation pairs
    (``ctx.paths.refined_pairs_csv``). The domain label and languages come
    from ``ctx.settings``. No key, no network, deterministic. The draft carries
    ``"curation": "deterministic"`` and ``status: "draft"``; the operator's
    review turns it into the curated document.
    """
    paths, settings = ctx.paths, ctx.settings
    with ctx.threads.applied():
        return write_subfield_draft(
            lexical_data_json=paths.lexical_data_json,
            embeddings_json=paths.embeddings_json,
            term_clusters_csv=paths.terms_clustered_csv,
            pairs_csv=paths.refined_pairs_csv,
            draft_json_out=paths.subfields_draft_json,
            domain_title=settings.domain_title,
            clustering_signature=clustering_signature,
            n_subfields=n_subfields,
            reference_language=settings.reference_language,
            display_languages=settings.display_languages,
        )


def write_subfield_draft(
    *,
    lexical_data_json: Path,
    embeddings_json: Path,
    term_clusters_csv: Path,
    pairs_csv: Path | None,
    draft_json_out: Path,
    domain_title: str = "",
    clustering_signature: str | None = None,
    n_subfields: int = 12,
    reference_language: str = "en",
    display_languages: tuple[str, ...] = ("fr", "en"),
) -> list[SubfieldEntry]:
    """Write the deterministic subfield draft from explicit files (see :func:`draft_subfields`)."""
    _require_lexical_models(lexical_data_json, embeddings_json)
    from cartolex.atlas.hierarchy import build_hierarchy
    from cartolex.atlas.model_files import load_embeddings, load_lexical_data

    logger.info("Loading lexical data from %s", lexical_data_json)
    data = load_lexical_data(lexical_data_json)
    emb = load_embeddings(embeddings_json)
    terms = list(data.terms)
    cluster_labels = _load_term_cluster_labels(term_clusters_csv, terms)
    gscore = np.asarray(data.X.sum(axis=0)).ravel()
    gscore = np.asarray(getattr(gscore, "A1", gscore)).ravel()
    h = build_hierarchy(
        emb.Z_terms, terms, gscore, cluster_labels=cluster_labels, target_subfields=n_subfields
    )
    candidates, concepts = _assemble_draft(
        h,
        data,
        emb,
        pairs_csv=pairs_csv,
        reference_language=reference_language,
        display_languages=display_languages,
    )
    doc = {
        "schema_version": SUBFIELDS_SCHEMA_VERSION,
        "clustering_signature": clustering_signature,
        "domain_title": domain_title,
        "status": "draft",
        "curation": "deterministic",
        "instructions": (
            "Deterministic hierarchy from the term clustering: concepts are the term clusters, "
            "subfields a Ward cut over their centroids, labels the dominant keyword. Review "
            "it (rename, move, merge, set term statuses), then apply."
        ),
        "subfields": candidates,
        "concepts": concepts,
        "n_dropped_terms": len(h["dropped_term_indices"]),
    }
    draft_json_out.parent.mkdir(parents=True, exist_ok=True)
    draft_json_out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Wrote %d subfields / %d concepts (deterministic draft) to %s",
        len(candidates),
        len(concepts),
        draft_json_out,
    )
    return candidates


def apply_subfields(
    ctx: RunContext,
    *,
    clustering_signature: str | None = None,
) -> SubfieldsDoc:
    """Subfield apply stage: apply the curated hierarchy of a run and write the final artifact.

    Reads the curated document (``ctx.paths.subfields_curated_json``), or the
    draft (``ctx.paths.subfields_draft_json``) when there is none; writes the
    applied document (``ctx.paths.subfields_json``), the per-person subfield
    weights (``ctx.paths.subfield_weights_csv``) and the lexicon weights
    (``ctx.paths.lexicon_weights_csv``), with ``ctx.settings.weights_basis``.
    See :func:`apply_subfield_files` for the details.
    """
    paths = ctx.paths
    with ctx.threads.applied():
        return apply_subfield_files(
            curated_json=paths.subfields_curated_json,
            draft_json=paths.subfields_draft_json,
            lexical_data_json=paths.lexical_data_json,
            embeddings_json=paths.embeddings_json,
            final_json_out=paths.subfields_json,
            weights_csv_out=paths.subfield_weights_csv,
            lexicon_weights_csv_out=paths.lexicon_weights_csv,
            clustering_signature=clustering_signature,
            weights_basis=ctx.settings.weights_basis,
        )


def apply_subfield_files(
    *,
    curated_json: Path,
    lexical_data_json: Path,
    embeddings_json: Path,
    final_json_out: Path,
    weights_csv_out: Path | None = None,
    lexicon_weights_csv_out: Path | None = None,
    draft_json: Path | None = None,
    clustering_signature: str | None = None,
    weights_basis: str = "tf",
) -> SubfieldsDoc:
    """Project researchers into curated subfields and write the final artifact.

    Reads ``curated_json`` (the curated document), keeps only entries
    with ``keep=true``, then for each remaining subfield:

    - Computes the centroid of the subfield's seed terms in SVD space.
    - Assigns each researcher to their nearest subfield (argmin cosine distance).
    - Records ``member_researcher_ids`` and ``membership_score`` per researcher.

    Writes the result to ``final_json_out`` (the applied document).
    When ``weights_csv_out`` is given, also writes a per-(researcher, subfield)
    soft-weight distribution CSV (columns ``researcher_id, subfield_id, weight``),
    for example for per-person subfield charts.

    Parameters
    ----------
    curated_json:
        User-curated subfield JSON (the source of truth).
    lexical_data_json / embeddings_json:
        The stored lexical data and embeddings (model descriptors, see
        :mod:`cartolex.atlas.model_files`).
    final_json_out:
        Where to write the applied artifact.
    draft_json:
        Optional deterministic draft (the draft stage's output).
        When ``curated_json`` does not exist, the draft is applied as-is so the
        apply step works straight after the clustering step. Manual curation
        still wins when the curated file is present.
    clustering_signature:
        Opaque hash of the current term-cluster partition (from
        the consuming application's clustering-signature helper). Stamped into the
        applied artifact so downstream consumers can detect staleness.
        ``None`` when not yet clustered.
    weights_basis:
        Matrix the lexicon weights are computed from: ``"tf"`` (default) uses
        the plain term-frequency track (``LexicalData.X_tf`` — shares read as
        "fraction of activity"), ``"tfidf"`` uses the length-boosted TF-IDF
        matrix ``X`` (legacy behaviour). When ``"tf"`` is requested but the
        persisted lexical data predates the TF track, falls back to ``X``
        with a warning (re-run the consolidation to populate ``score_tf``).
    """
    source_json = curated_json
    if not source_json.exists():
        if draft_json is not None and draft_json.exists():
            logger.info(
                "Curated %s not found; falling back to the draft %s.",
                curated_json,
                draft_json,
            )
            source_json = draft_json
        else:
            raise FileNotFoundError(
                f"{curated_json} not found. "
                "Curate the draft (subfields_draft.json) into it, or apply the draft as it is."
            )

    doc: SubfieldsDoc = json.loads(source_json.read_text(encoding="utf-8"))
    # Drafts written before 2026-06 carry no subfield ids (the id used to be
    # assigned here, post-filter). Assign positional ids on the FULL list first:
    # the draft numbering is contiguous and in order, so position == id,
    # and the concepts' `subfield_id` references resolve — for validation, for
    # the lexicon weights, and regardless of any keep=false filtering below.
    for i, sf in enumerate(doc["subfields"]):
        if "id" not in sf:
            sf["id"] = i
    subfields = [sf for sf in doc["subfields"] if sf.get("keep", True)]
    if not subfields:
        raise ValueError("No subfields with keep=true found in curated JSON.")

    _require_lexical_models(lexical_data_json, embeddings_json)
    from cartolex.atlas.model_files import load_embeddings, load_lexical_data

    logger.info("Loading embeddings for subfield application.")
    data = load_lexical_data(lexical_data_json)
    emb = load_embeddings(embeddings_json)

    # ── Schema 1.1 (edited documents): validate the partition invariants, refresh
    #    the subfield seed terms, and re-derive colors after edits. Stash and
    #    trash stay in the curated doc — the applied artifact never carries them.
    if doc.get("stash") or doc.get("trash") or str(doc.get("schema_version")) == "1.1":
        from .subfields_edit import refresh_subfield_top_terms, restamp_colors, validate_doc

        problems = validate_doc(doc, n_terms=len(data.terms))
        if problems:
            raise ValueError(
                "The edited hierarchy is inconsistent — fix the curated document: "
                + " ; ".join(problems)
            )
        refresh_subfield_top_terms(doc)
        restamp_colors(doc)

    # Schema-1.0 docs written without subfield seeds (e.g. by an external curation tool)
    # would otherwise get all-zero centroids and a degenerate researcher
    # assignment — derive the missing seeds from each subfield's concepts.
    if doc.get("concepts") and any(not sf.get("top_terms") for sf in subfields):
        from .subfields_edit import refresh_subfield_top_terms as _refresh_seeds

        logger.info("Deriving missing subfield seed top_terms from member concepts.")
        _refresh_seeds(doc, only_missing=True)

    meta_ind: pd.DataFrame = data.meta_ind

    # ── Assign each researcher to nearest subfield centroid ──────────────────
    dist_matrix = _subfield_distance_matrix(subfields, data=data, emb=emb)  # (n_res, n_sf)
    assignments = dist_matrix.argmin(axis=1)
    scores = 1.0 - dist_matrix.min(axis=1)

    researcher_ids: list[str] = _researcher_id_series(meta_ind).tolist()

    # Populate member lists
    for sf in subfields:
        sf["member_researcher_ids"] = []

    for res_idx, (sf_idx, score) in enumerate(zip(assignments, scores, strict=True)):
        subfields[sf_idx]["member_researcher_ids"].append(
            {"researcher_id": researcher_ids[res_idx], "membership_score": float(score)}
        )

    # Compute UMAP centroids from researcher positions
    for sf_idx, sf in enumerate(subfields):
        member_res_indices = [i for i, a in enumerate(assignments) if a == sf_idx]
        if member_res_indices and emb.umap_ind is not None:
            coords = emb.umap_ind[member_res_indices]
            sf["centroid_umap_x"] = float(coords[:, 0].mean())
            sf["centroid_umap_y"] = float(coords[:, 1].mean())

    # ── Per-researcher soft subfield weights (distribution for pie charts) ────
    # Turn cosine distances into a per-researcher probability-like distribution
    # over subfields: similarity = max(0, 1 - distance), normalised to sum 1.
    # Researchers with no positive similarity get a uniform distribution.
    if weights_csv_out is not None:
        similarities = np.clip(1.0 - dist_matrix, a_min=0.0, a_max=None)  # (n_res, n_sf)
        row_sums = similarities.sum(axis=1, keepdims=True)
        n_sf = similarities.shape[1]
        with np.errstate(invalid="ignore", divide="ignore"):
            weights = np.where(row_sums > 0, similarities / row_sums, 1.0 / n_sf)
        subfield_ids = [sf["id"] for sf in subfields]
        rows: list[dict[str, object]] = []
        for res_idx, rid in enumerate(researcher_ids):
            for col, sf_id in enumerate(subfield_ids):
                w = float(weights[res_idx, col])
                if w <= 0.0:
                    continue
                rows.append({"researcher_id": rid, "subfield_id": sf_id, "weight": round(w, 5)})
        weights_csv_out.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=["researcher_id", "subfield_id", "weight"]).to_csv(
            weights_csv_out, index=False
        )
        logger.info("Wrote %d researcher-subfield weight rows → %s", len(rows), weights_csv_out)

    # ── Single-track lexicon weights (researcher-equal, curated terms only) ───
    # Stamped in place on the kept subfields + concepts, so the applied doc is
    # the single source of truth every consumer (maps, sites, word clouds) reads.
    effective_basis = "tfidf"
    if doc.get("concepts"):
        X_weights = data.X
        if weights_basis == "tf":
            # Lexical data built from a keyword table without score_tf has no TF track.
            X_tf = getattr(data, "X_tf", None)
            if X_tf is not None:
                X_weights = X_tf
                effective_basis = "tf"
            else:
                logger.warning(
                    "weights_basis='tf' requested but the lexical data has no TF track "
                    "(score_tf) — falling back to the TF-IDF matrix. Re-run the consolidation and SVD stages."
                )
        lex_df = compute_lexicon_weights(
            {"subfields": subfields, "concepts": doc.get("concepts", [])},
            X_weights,
            list(data.terms),
        )
        if lexicon_weights_csv_out is not None:
            lexicon_weights_csv_out.parent.mkdir(parents=True, exist_ok=True)
            lex_df.to_csv(lexicon_weights_csv_out, index=False)
            logger.info("Wrote %d lexicon weight rows → %s", len(lex_df), lexicon_weights_csv_out)

    applied_doc: SubfieldsDoc = {
        "schema_version": SUBFIELDS_SCHEMA_VERSION,
        "clustering_signature": clustering_signature,
        "domain_title": doc.get("domain_title", ""),
        "status": "applied",
        # Basis the weight/share fields were actually computed from — consumers
        # (sites, word clouds) pick the matching per-researcher score column.
        "weights_basis": effective_basis,
        "subfields": subfields,
    }

    # ── Hierarchy passthrough: carry the concept layer through to the applied doc ──
    if "concepts" in doc:
        applied_doc["concepts"] = doc.get("concepts", [])

    final_json_out.parent.mkdir(parents=True, exist_ok=True)
    final_json_out.write_text(
        json.dumps(applied_doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Applied %d subfields to %d researchers → %s",
        len(subfields),
        len(researcher_ids),
        final_json_out,
    )
    return applied_doc


def load_subfields(subfields_json: Path) -> SubfieldsDoc | None:
    """Load the applied subfields artifact, or return None if not yet generated."""
    if not subfields_json.exists():
        return None
    try:
        return json.loads(subfields_json.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not load %s: %s", subfields_json, exc)
        return None
