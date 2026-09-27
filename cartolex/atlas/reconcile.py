# SPDX-License-Identifier: MIT
"""Cross-cohort vocabulary reconciliation into a merged *sense* vocabulary.

Per-cohort vocabularies cannot simply be unioned by string: the same surface can
carry different meanings in different cohorts ("membrane" in soft matter vs
electrochemistry — merging them fabricates bridges on the joint map), and
different surfaces can name the same concept ("tumour"/"tumor"). This module
builds the merged sense vocabulary V* in four steps:

- **R0** — deterministic surface normalization (:func:`normalize_surface`):
  NFKC + lowercase + hyphen/space folding + UK→US spelling + singular folding.
- **R1** — sense contexts (:func:`build_cohort_senses`): each (cohort, surface)
  gets the top co-used surfaces by PPMI over that cohort's researchers; a sense
  IS its co-occurrence context.
- **R2** — same-surface merge/split test across cohorts: weighted context
  Jaccard + (optional) embedding cosine of contextualized sense strings.
- **R3** — cross-surface near-synonym merge (stricter; requires an embedder).

Decisions compose over N ≥ 3 cohorts by union-find with **conflict detection**:
a chain a~b, b~c with an explicit a≁c flags the component and breaks it
back to singletons for re-adjudication instead of silently merging.

The loss is asymmetric by design: a **false merge fabricates
interdisciplinarity/continuity** on the map, while a false split only loses
statistical power — thresholds are conservative and gray zones become *pending*
adjudications (cached in a decisions JSON, same discipline as
``llm_decisions.json``) that default to **split** until adjudicated.

Everything here is pure logic: inputs are in-memory matrices/vocabularies (from
map bundles or in memory), the embedding engine is an
injected callable, and the output :class:`ReconciliationTable` is a versioned,
JSON-serializable record that makes any downstream merge reproducible.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from cartolex.lexicon.canonicalization import canonical_singular

__all__ = [
    "SCHEMA_VERSION",
    "DECISIONS_SCHEMA",
    "Thresholds",
    "CohortSense",
    "ReconciliationTable",
    "normalize_surface",
    "build_cohort_senses",
    "sense_text",
    "pair_key",
    "reconcile",
    "build_naive_table",
    "load_decisions",
    "save_decisions",
]

SCHEMA_VERSION = "map_reconcile/2"
DECISIONS_SCHEMA = "map_reconcile_decisions/1"

Embedder = Callable[[list[str]], np.ndarray]

_WS_RE = re.compile(r"\s+")

# UK→US token map. Deliberately dict-based for the irregular families
# (-our, -re, -lling) where a blind suffix rule would corrupt valid words
# ("controlled", "genre"); the regular -isation/-yse families use suffix rules.
_UK_US_TOKEN_MAP: dict[str, str] = {
    "behaviour": "behavior",
    "behavioural": "behavioral",
    "colour": "color",
    "coloured": "colored",
    "colouring": "coloring",
    "flavour": "flavor",
    "vapour": "vapor",
    "tumour": "tumor",
    "neighbour": "neighbor",
    "neighbouring": "neighboring",
    "neighbourhood": "neighborhood",
    "labour": "labor",
    "harbour": "harbor",
    "endeavour": "endeavor",
    "centre": "center",
    "centred": "centered",
    "fibre": "fiber",
    "fibres": "fibers",
    "litre": "liter",
    "metre": "meter",
    "micrometre": "micrometer",
    "nanometre": "nanometer",
    "spectre": "specter",
    # canonical_singular strips plural "-es", leaving these stems for the
    # UK "-res"/"-ues"/"-mes" families ("centres"→"centr"): map the stems too.
    "centr": "center",
    "fibr": "fiber",
    "litr": "liter",
    "metr": "meter",
    "micrometr": "micrometer",
    "nanometr": "nanometer",
    "spectr": "specter",
    "programm": "program",
    "catalogu": "catalog",
    "analogu": "analog",
    "modelling": "modeling",
    "modelled": "modeled",
    "labelling": "labeling",
    "labelled": "labeled",
    "signalling": "signaling",
    "signalled": "signaled",
    "channelling": "channeling",
    "tunnelling": "tunneling",
    "levelling": "leveling",
    "cancelled": "canceled",
    "aluminium": "aluminum",
    "sulphur": "sulfur",
    "sulphide": "sulfide",
    "sulphate": "sulfate",
    "artefact": "artifact",
    "artefacts": "artifacts",
    "ageing": "aging",
    "grey": "gray",
    "programme": "program",
    "programmes": "programs",
    "catalogue": "catalog",
    "defence": "defense",
    "licence": "license",
    "analogue": "analog",
    "haemoglobin": "hemoglobin",
    "haematology": "hematology",
    "oestrogen": "estrogen",
    "anaemia": "anemia",
}

# Regular UK→US suffix families, safe at these lengths (applied per token,
# longest suffix first).
_UK_US_SUFFIX_RULES: tuple[tuple[str, str], ...] = (
    ("isations", "izations"),
    ("isation", "ization"),
    ("ising", "izing"),
    ("isers", "izers"),
    ("ised", "ized"),
    ("iser", "izer"),
    ("ysing", "yzing"),
    ("ysed", "yzed"),
    ("yse", "yze"),
)
# Tokens where the -is/-ys suffix is part of the stem, not a UK spelling.
_UK_US_SUFFIX_BLOCKLIST: frozenset[str] = frozenset({"promised", "raised", "praised", "arised"})


def _uk_to_us(token: str) -> str:
    mapped = _UK_US_TOKEN_MAP.get(token)
    if mapped is not None:
        return mapped
    if token in _UK_US_SUFFIX_BLOCKLIST:
        return token
    for uk, us in _UK_US_SUFFIX_RULES:
        if token.endswith(uk) and len(token) > len(uk) + 2:
            return token[: -len(uk)] + us
    return token


def normalize_surface(term: str) -> str:
    """R0: fold a raw canonical term to its cross-cohort comparison surface.

    NFKC-normalize, lowercase, fold hyphens to spaces, collapse whitespace,
    map UK→US spellings token-wise, then fold the trailing plural via
    :func:`cartolex.lexicon.canonicalization.canonical_singular`. Deterministic
    and idempotent; accents are preserved (vocabularies are canonical-English
    with occasional French terms — stripping accents would over-merge).
    """
    s = unicodedata.normalize("NFKC", str(term or "")).lower()
    s = s.replace("-", " ").replace("’", "'")
    s = _WS_RE.sub(" ", s).strip()
    if not s:
        return ""
    # Singular first so the UK→US map sees base forms ("centres"→"centre"→"center").
    s = canonical_singular(s)
    return " ".join(_uk_to_us(t) for t in s.split(" "))


@dataclass
class CohortSense:
    """One (cohort, normalized surface) with its co-occurrence context.

    ``context`` holds ``(surface, ppmi)`` pairs sorted by (ppmi desc, surface
    asc); ``source_terms`` are the cohort's raw vocabulary terms that fold onto
    this surface (≥ 1; > 1 when R0 merges within-cohort variants).
    """

    cohort_id: str
    surface: str
    source_terms: list[str]
    df: int
    tf_total: float
    context: list[tuple[str, float]] = field(default_factory=list)
    concept_label: str = ""

    @property
    def key(self) -> str:
        return f"{self.surface}@{self.cohort_id}"


def build_cohort_senses(
    cohort_id: str,
    terms: Sequence[str],
    X_tf: np.ndarray,
    *,
    concept_of_term: Mapping[str, str] | None = None,
    top_co: int = 15,
) -> list[CohortSense]:
    """R1: fold a cohort's vocabulary to senses and attach PPMI contexts.

    *X_tf* is the cohort's plain-TF researcher×term matrix (a bundle's ``X_tf``);
    only its non-zero pattern matters here. Raw terms folding to the same R0
    surface are summed into one sense. PPMI is computed over researchers:
    ``ppmi(t, u) = max(0, log(co · N / (df_t · df_u)))``.
    """
    X = np.asarray(X_tf, dtype=float)
    if X.ndim != 2 or X.shape[1] != len(terms):
        raise ValueError(
            f"X_tf shape {X.shape} does not match vocabulary size {len(terms)} "
            f"for cohort {cohort_id!r}"
        )

    surface_terms: dict[str, list[str]] = {}
    for t in terms:
        surf = normalize_surface(t)
        if surf:
            surface_terms.setdefault(surf, []).append(str(t))
    surfaces = sorted(surface_terms)
    col_of_term = {str(t): j for j, t in enumerate(terms)}

    # Fold columns: binary usage + total TF per surface.
    n_res = X.shape[0]
    B = np.zeros((n_res, len(surfaces)))
    tf_tot = np.zeros(len(surfaces))
    for j, surf in enumerate(surfaces):
        cols = [col_of_term[t] for t in surface_terms[surf]]
        block = X[:, cols]
        B[:, j] = (block > 0).any(axis=1)
        tf_tot[j] = float(block.sum())
    df = B.sum(axis=0)

    # PPMI over researchers (co-usage counts).
    co = B.T @ B
    with np.errstate(divide="ignore", invalid="ignore"):
        pmi = np.log(co * max(n_res, 1) / np.outer(df, df))
    ppmi = np.where(np.isfinite(pmi), np.maximum(pmi, 0.0), 0.0)
    np.fill_diagonal(ppmi, 0.0)
    ppmi[co < 1] = 0.0

    concept_of_term = dict(concept_of_term or {})
    senses: list[CohortSense] = []
    for j, surf in enumerate(surfaces):
        weights = ppmi[j]
        order = sorted(
            (k for k in np.nonzero(weights > 0)[0]),
            key=lambda k: (-weights[k], surfaces[k]),
        )[:top_co]
        context = [(surfaces[k], round(float(weights[k]), 6)) for k in order]
        labels = sorted({concept_of_term.get(t, "") for t in surface_terms[surf]} - {""})
        senses.append(
            CohortSense(
                cohort_id=str(cohort_id),
                surface=surf,
                source_terms=sorted(surface_terms[surf]),
                df=int(df[j]),
                tf_total=float(tf_tot[j]),
                context=context,
                concept_label=labels[0] if labels else "",
            )
        )
    return senses


def sense_text(sense: CohortSense, *, max_co: int = 8) -> str:
    """The contextualized string an embedder scores for this sense."""
    parts = [sense.surface]
    if sense.concept_label:
        parts.append(f"concept: {sense.concept_label}")
    if sense.context:
        parts.append("related: " + ", ".join(s for s, _ in sense.context[:max_co]))
    return "; ".join(parts)


@dataclass(frozen=True)
class Thresholds:
    """Merge/split thresholds (conservative by design — see module docstring)."""

    tau_merge: float = 0.60  # same-surface embedding cosine ⇒ merge
    tau_split: float = 0.40  # same-surface embedding cosine ⇒ split (with low Jaccard)
    jaccard_merge: float = 0.15  # weighted context Jaccard ⇒ merge
    jaccard_split: float = 0.05  # below this (and cos ≤ tau_split) ⇒ split
    cross_cos_auto: float = 0.90  # cross-surface cosine ⇒ merge (with context support)
    cross_cos_floor: float = 0.85  # cross-surface cosine ⇒ worth adjudicating
    cross_knn: int = 8  # cross-surface candidate neighbours per sense

    def to_dict(self) -> dict[str, float]:
        return {
            "tau_merge": self.tau_merge,
            "tau_split": self.tau_split,
            "jaccard_merge": self.jaccard_merge,
            "jaccard_split": self.jaccard_split,
            "cross_cos_auto": self.cross_cos_auto,
            "cross_cos_floor": self.cross_cos_floor,
            "cross_knn": float(self.cross_knn),
        }


def weighted_jaccard(a: Sequence[tuple[str, float]], b: Sequence[tuple[str, float]]) -> float:
    """Weighted Jaccard of two ``(surface, weight)`` contexts (Σmin/Σmax)."""
    wa = {s: max(w, 0.0) for s, w in a}
    wb = {s: max(w, 0.0) for s, w in b}
    keys = set(wa) | set(wb)
    if not keys:
        return 0.0
    num = sum(min(wa.get(k, 0.0), wb.get(k, 0.0)) for k in keys)
    den = sum(max(wa.get(k, 0.0), wb.get(k, 0.0)) for k in keys)
    return float(num / den) if den > 0 else 0.0


def pair_key(kind: str, a: CohortSense, b: CohortSense) -> str:
    """Stable adjudication-cache key for a scored sense pair."""
    if kind == "ss":
        lo, hi = sorted((a.cohort_id, b.cohort_id))
        return f"ss::{a.surface}::{lo}::{hi}"
    lo, hi = sorted((a.key, b.key))
    return f"xs::{lo}::{hi}"


@dataclass
class ReconciliationTable:
    """Versioned output of :func:`reconcile` — the merged sense vocabulary V*.

    ``groups`` maps each final sense id to its member senses; ``mapping`` sends
    every (cohort, raw term) to its sense id; ``pending`` is the adjudication
    sheet (gray-zone pairs, defaulted to split until decided); ``conflicts``
    records union-find components broken by contradictory decisions.
    """

    schema_version: str
    thresholds: dict[str, float]
    embedder_fingerprint: str
    groups: dict[str, list[dict[str, Any]]]
    mapping: dict[str, dict[str, str]]  # cohort_id → {raw_term → sense_id}
    pending: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    decisions_applied: dict[str, str]
    stats: dict[str, Any]

    @property
    def sense_ids(self) -> list[str]:
        return sorted(self.groups)

    def sense_of(self, cohort_id: str, raw_term: str) -> str | None:
        return self.mapping.get(str(cohort_id), {}).get(str(raw_term))

    def sense_cohorts(self) -> dict[str, list[str]]:
        """Sense id → sorted list of contributing cohort ids."""
        return {
            sid: sorted({m["cohort_id"] for m in members}) for sid, members in self.groups.items()
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema_version,
            "thresholds": self.thresholds,
            "embedder_fingerprint": self.embedder_fingerprint,
            "groups": self.groups,
            "mapping": self.mapping,
            "pending": self.pending,
            "conflicts": self.conflicts,
            "decisions_applied": self.decisions_applied,
            "stats": self.stats,
        }

    def write_json(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    @classmethod
    def from_json_dict(cls, doc: Mapping[str, Any]) -> ReconciliationTable:
        if doc.get("schema") != SCHEMA_VERSION:
            raise ValueError(f"Not a {SCHEMA_VERSION} table (schema={doc.get('schema')!r})")
        return cls(
            schema_version=str(doc["schema"]),
            thresholds=dict(doc.get("thresholds", {})),
            embedder_fingerprint=str(doc.get("embedder_fingerprint", "")),
            groups={k: list(v) for k, v in dict(doc.get("groups", {})).items()},
            mapping={k: dict(v) for k, v in dict(doc.get("mapping", {})).items()},
            pending=list(doc.get("pending", [])),
            conflicts=list(doc.get("conflicts", [])),
            decisions_applied=dict(doc.get("decisions_applied", {})),
            stats=dict(doc.get("stats", {})),
        )


def load_decisions(path: Path) -> dict[str, str]:
    """Read a cached adjudication file ({pair_key: "merge"|"split"})."""
    path = Path(path)
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != DECISIONS_SCHEMA:
        raise ValueError(f"Not a {DECISIONS_SCHEMA} file: {path}")
    out: dict[str, str] = {}
    for k, v in dict(doc.get("decisions", {})).items():
        if v not in ("merge", "split"):
            raise ValueError(f"Invalid decision {v!r} for {k!r} in {path}")
        out[str(k)] = str(v)
    return out


def save_decisions(decisions: Mapping[str, str], path: Path) -> Path:
    """Write an adjudication cache next to the reconciliation table."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema": DECISIONS_SCHEMA, "decisions": dict(sorted(decisions.items()))},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


class _UnionFind:
    def __init__(self, keys: Sequence[str]) -> None:
        self._parent = {k: k for k in keys}

    def find(self, k: str) -> str:
        root = k
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[k] != root:
            self._parent[k], k = root, self._parent[k]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Deterministic: smaller key becomes the root.
            lo, hi = sorted((ra, rb))
            self._parent[hi] = lo

    def components(self) -> dict[str, list[str]]:
        comps: dict[str, list[str]] = {}
        for k in self._parent:
            comps.setdefault(self.find(k), []).append(k)
        return {r: sorted(m) for r, m in comps.items()}


def _pending_entry(
    kind: str, key: str, a: CohortSense, b: CohortSense, scores: dict[str, float | None]
) -> dict[str, Any]:
    return {
        "key": key,
        "kind": kind,
        "a": {
            "surface": a.surface,
            "cohort_id": a.cohort_id,
            "df": a.df,
            "concept": a.concept_label,
            "context": [s for s, _ in a.context[:10]],
        },
        "b": {
            "surface": b.surface,
            "cohort_id": b.cohort_id,
            "df": b.df,
            "concept": b.concept_label,
            "context": [s for s, _ in b.context[:10]],
        },
        "scores": {k: (None if v is None else round(float(v), 4)) for k, v in scores.items()},
    }


def _group_records(members: list[CohortSense]) -> list[dict[str, Any]]:
    return [
        {
            "cohort_id": s.cohort_id,
            "surface": s.surface,
            "source_terms": list(s.source_terms),
            "df": s.df,
            "tf_total": round(s.tf_total, 6),
            "concept": s.concept_label,
        }
        for s in sorted(members, key=lambda s: (s.cohort_id, s.surface))
    ]


def _assign_group_ids(
    components: dict[str, list[str]], sense_by_key: Mapping[str, CohortSense]
) -> dict[str, list[CohortSense]]:
    """Name each component: primary surface, '#'-disambiguated on collisions."""
    provisional: list[tuple[str, list[CohortSense]]] = []
    for _, keys in sorted(components.items()):
        members = [sense_by_key[k] for k in keys]
        by_df: dict[str, int] = {}
        for s in members:
            by_df[s.surface] = by_df.get(s.surface, 0) + s.df
        primary = sorted(by_df.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        provisional.append((primary, members))

    counts: dict[str, int] = {}
    for primary, _ in provisional:
        counts[primary] = counts.get(primary, 0) + 1

    groups: dict[str, list[CohortSense]] = {}
    for primary, members in provisional:
        if counts[primary] == 1:
            gid = primary
        else:
            joined = "+".join(sorted({s.cohort_id for s in members}))
            gid = f"{primary}#{joined}"
        # Extremely defensive: two split components with identical cohort sets
        # cannot share a surface (they would be one component), but guard anyway.
        while gid in groups:
            gid += "'"
        groups[gid] = members
    return groups


def reconcile(
    cohorts: Sequence[Sequence[CohortSense]],
    *,
    embedder: Embedder | None = None,
    embedder_fingerprint: str = "",
    decisions: Mapping[str, str] | None = None,
    thresholds: Thresholds | None = None,
) -> ReconciliationTable:
    """Run R2/R3 over per-cohort senses and return the merged sense table.

    *cohorts* is one :func:`build_cohort_senses` result per cohort.
    *embedder* (optional) maps a list of sense strings to unit-comparable
    vectors; without it, decisions fall back to context Jaccard alone and all
    cross-surface merges are skipped. *decisions* is the adjudication cache —
    entries override the automatic thresholds for their pair.
    """
    thresholds = thresholds or Thresholds()
    decisions = dict(decisions or {})
    all_senses: list[CohortSense] = [s for cohort in cohorts for s in cohort]
    sense_by_key = {s.key: s for s in all_senses}
    if len(sense_by_key) != len(all_senses):
        raise ValueError("Duplicate (surface, cohort) senses — fold per cohort first.")

    # Optional embeddings for every sense (unit-normalized for cosine-by-dot).
    emb: np.ndarray | None = None
    keys_sorted = sorted(sense_by_key)
    if embedder is not None and keys_sorted:
        emb = np.asarray(embedder([sense_text(sense_by_key[k]) for k in keys_sorted]), dtype=float)
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        emb = emb / np.maximum(norms, 1e-12)
    row_of_key = {k: i for i, k in enumerate(keys_sorted)}

    def _cos(a: CohortSense, b: CohortSense) -> float | None:
        if emb is None:
            return None
        return float(emb[row_of_key[a.key]] @ emb[row_of_key[b.key]])

    merge_edges: list[tuple[str, str, str]] = []  # (key_a, key_b, pair_key)
    split_pairs: dict[str, tuple[str, str]] = {}  # pair_key → (key_a, key_b)
    pending: list[dict[str, Any]] = []
    applied: dict[str, str] = {}
    stats = {
        "n_cohorts": len(cohorts),
        "n_senses_pre": len(all_senses),
        "same_surface": {"merged": 0, "split": 0, "pending": 0},
        "cross_surface": {"merged": 0, "rejected": 0, "pending": 0},
    }

    def _record(kind: str, a: CohortSense, b: CohortSense, outcome: str, key: str) -> None:
        bucket = stats["same_surface"] if kind == "ss" else stats["cross_surface"]
        if outcome == "merge":
            merge_edges.append((a.key, b.key, key))
            bucket["merged"] += 1
        elif outcome == "split":
            split_pairs[key] = (a.key, b.key)
            bucket["split" if kind == "ss" else "rejected"] += 1
        else:  # pending — conservative default is split, recorded for adjudication
            bucket["pending"] += 1

    # ── R2: same-surface pairs across cohorts ─────────────────────────────
    by_surface: dict[str, list[CohortSense]] = {}
    for s in all_senses:
        by_surface.setdefault(s.surface, []).append(s)
    for surface in sorted(by_surface):
        group = sorted(by_surface[surface], key=lambda s: s.cohort_id)
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                key = pair_key("ss", a, b)
                wj = weighted_jaccard(a.context, b.context)
                cos = _cos(a, b)
                if key in decisions:
                    outcome = decisions[key]
                    applied[key] = outcome
                elif cos is not None and (
                    cos >= thresholds.tau_merge or wj >= thresholds.jaccard_merge
                ):
                    outcome = "merge"
                elif cos is not None and (
                    cos <= thresholds.tau_split and wj < thresholds.jaccard_split
                ):
                    outcome = "split"
                elif cos is None and wj >= thresholds.jaccard_merge:
                    outcome = "merge"
                elif cos is None and wj < thresholds.jaccard_split:
                    outcome = "split"
                else:
                    outcome = "pending"
                    pending.append(_pending_entry("ss", key, a, b, {"jaccard": wj, "cos": cos}))
                _record("ss", a, b, outcome, key)

    # ── R3: cross-surface near-synonyms (embedder required) ────────────────
    if emb is not None and len(keys_sorted) > 1:
        from sklearn.neighbors import NearestNeighbors

        k = min(thresholds.cross_knn + 1, len(keys_sorted))
        nn = NearestNeighbors(n_neighbors=k, metric="cosine").fit(emb)
        dist, idx = nn.kneighbors(emb)
        seen: set[str] = set()
        for i, key_a in enumerate(keys_sorted):
            a = sense_by_key[key_a]
            for d, jn in zip(dist[i][1:], idx[i][1:], strict=False):
                b = sense_by_key[keys_sorted[int(jn)]]
                if a.surface == b.surface or a.cohort_id == b.cohort_id:
                    continue  # same-surface handled by R2; within-cohort kept as curated
                key = pair_key("xs", a, b)
                if key in seen:
                    continue
                seen.add(key)
                cos = 1.0 - float(d)
                if cos < thresholds.cross_cos_floor and key not in decisions:
                    continue
                wj = weighted_jaccard(a.context, b.context)
                if key in decisions:
                    outcome = decisions[key]
                    applied[key] = outcome
                elif cos >= thresholds.cross_cos_auto and wj >= thresholds.jaccard_merge:
                    outcome = "merge"
                else:
                    outcome = "pending"
                    pending.append(_pending_entry("xs", key, a, b, {"jaccard": wj, "cos": cos}))
                _record("xs", a, b, outcome, key)

    # ── Union-find with conflict detection ─────────────────────────────────
    uf = _UnionFind(sorted(sense_by_key))
    for key_a, key_b, _ in merge_edges:
        uf.union(key_a, key_b)
    components = uf.components()

    conflicts: list[dict[str, Any]] = []
    conflicted_roots: set[str] = set()
    for pkey, (key_a, key_b) in sorted(split_pairs.items()):
        if uf.find(key_a) == uf.find(key_b):
            root = uf.find(key_a)
            conflicted_roots.add(root)
            conflicts.append(
                {
                    "component": sorted(components[root]),
                    "split_pair": pkey,
                    "note": "merge chain contradicts an explicit split — broken to "
                    "singletons pending re-adjudication",
                }
            )
    if conflicted_roots:
        # Break every conflicted component back to singletons and queue its
        # merge edges for re-adjudication (deterministic, conservative).
        broken: dict[str, list[str]] = {}
        for root, members in components.items():
            if root in conflicted_roots:
                for m in members:
                    broken[m] = [m]
            else:
                broken[root] = members
        components = broken
        conflicted_keys = {m for c in conflicts for m in c["component"]}
        for key_a, key_b, pkey in merge_edges:
            if key_a in conflicted_keys and key_b in conflicted_keys:
                a, b = sense_by_key[key_a], sense_by_key[key_b]
                kind = "ss" if a.surface == b.surface else "xs"
                entry = _pending_entry(kind, pkey, a, b, {"jaccard": None, "cos": None})
                entry["scores"]["conflict"] = 1.0
                pending.append(entry)

    groups = _assign_group_ids(components, sense_by_key)

    mapping: dict[str, dict[str, str]] = {}
    gid_of_key = {s.key: gid for gid, members in groups.items() for s in members}
    for s in all_senses:
        gid = gid_of_key[s.key]
        cohort_map = mapping.setdefault(s.cohort_id, {})
        for raw in s.source_terms:
            cohort_map[raw] = gid

    stats["n_groups"] = len(groups)
    stats["n_conflicts"] = len(conflicts)
    stats["n_pending"] = len(pending)
    stats["n_multi_cohort_groups"] = sum(
        1 for members in groups.values() if len({m.cohort_id for m in members}) > 1
    )

    return ReconciliationTable(
        schema_version=SCHEMA_VERSION,
        thresholds=thresholds.to_dict(),
        embedder_fingerprint=str(embedder_fingerprint),
        groups={gid: _group_records(members) for gid, members in groups.items()},
        mapping=mapping,
        pending=sorted(pending, key=lambda p: p["key"]),
        conflicts=conflicts,
        decisions_applied=applied,
        stats=stats,
    )


def build_naive_table(cohorts: Sequence[Sequence[CohortSense]]) -> ReconciliationTable:
    """A-naive ablation: merge every same-surface sense, no splits, no synonyms.

    Prices what the reconciliation buys: compare maps built from this table with
    those built from the reconciled one.
    """
    all_senses = [s for cohort in cohorts for s in cohort]
    sense_by_key = {s.key: s for s in all_senses}
    uf = _UnionFind(sorted(sense_by_key))
    by_surface: dict[str, list[CohortSense]] = {}
    for s in all_senses:
        by_surface.setdefault(s.surface, []).append(s)
    for surface in sorted(by_surface):
        group = sorted(by_surface[surface], key=lambda s: s.cohort_id)
        for other in group[1:]:
            uf.union(group[0].key, other.key)
    groups = _assign_group_ids(uf.components(), sense_by_key)
    mapping: dict[str, dict[str, str]] = {}
    gid_of_key = {s.key: gid for gid, members in groups.items() for s in members}
    for s in all_senses:
        cohort_map = mapping.setdefault(s.cohort_id, {})
        for raw in s.source_terms:
            cohort_map[raw] = gid_of_key[s.key]
    return ReconciliationTable(
        schema_version=SCHEMA_VERSION,
        thresholds={},
        embedder_fingerprint="naive",
        groups={gid: _group_records(members) for gid, members in groups.items()},
        mapping=mapping,
        pending=[],
        conflicts=[],
        decisions_applied={},
        stats={
            "n_cohorts": len(cohorts),
            "n_senses_pre": len(all_senses),
            "n_groups": len(groups),
            "naive": True,
        },
    )
