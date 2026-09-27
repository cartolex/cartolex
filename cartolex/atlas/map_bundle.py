# SPDX-License-Identifier: MIT
"""Portable (de)serializer for the multi-cohort merge inputs (``map_bundle/2``).

Sits at the same altitude as :mod:`cartolex.atlas.reconcile` and
:mod:`cartolex.atlas.map_merge`: file formats and structural validation only.
A "cohort bundle" is a directory (or ``.zip`` archive) that carries one
cohort's anonymized inputs — the plain-TF sparse matrix, the vocabulary, and
optionally a curated taxonomy and an adjudication-decisions cache — so any
consumer application can write one from its own data and any other consumer
can read it straight into
:func:`~cartolex.atlas.map_merge.assemble_joint_matrix`.

This module has **no workspace knowledge, no PII vocabulary, no id-scheme
policing, and no tier concept** — those are consumer policy. The
``bundle_meta.json`` manifest carries an opaque ``profile`` object the engine
never interprets; consumers wrap their own envelope (id schemes, anonymity
policy, extra facet files) around this core, and unknown files inside a
bundle are ignored by the reader for forward compatibility.

This module is also the canonical home of the ``map_taxonomy/1`` schema
constant and its structural validator (:func:`validate_taxonomy`): the
taxonomy document itself is produced by a consumer application (the curated
concept hierarchy re-exported as labels + term strings), but
:func:`~cartolex.atlas.map_merge.anchor_concepts` is the engine function
that consumes it, so the constant and its shape contract belong here.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .map_merge import CohortInput
from .reconcile import load_decisions, save_decisions
from .types import to_dense

logger = logging.getLogger(__name__)

__all__ = [
    "BUNDLE_SCHEMA",
    "TAXONOMY_SCHEMA",
    "CohortBundle",
    "build_bundle",
    "write_bundle",
    "read_bundle",
    "validate_bundle",
    "validate_taxonomy",
]

BUNDLE_SCHEMA = "map_bundle/2"
TAXONOMY_SCHEMA = "map_taxonomy/1"

_BUNDLE_SCHEMA_PREFIX = "map_bundle/"
_BUNDLE_SCHEMA_MAJOR = 2

_ENTITY_TERMS_COLUMNS = ("entity_id", "term", "tf", "score")
_VOCABULARY_COLUMNS = ("term", "n_entities", "tf_total", "score_total")
_REQUIRED_FILES = ("bundle_meta.json", "entity_terms.csv", "entities.csv", "vocabulary.csv")
_ZIP_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def _check_schema(schema: Any) -> None:
    """Validate a ``map_bundle/<major>`` schema tag (single point of truth).

    Raises :class:`ValueError` naming both the found and the supported schema
    when the tag is malformed or its major version is not the one this engine
    build supports — same discipline as ``reconcile.ReconciliationTable``.
    """
    text = str(schema)
    if not text.startswith(_BUNDLE_SCHEMA_PREFIX):
        raise ValueError(f"Not a {BUNDLE_SCHEMA} bundle (schema={text!r})")
    suffix = text[len(_BUNDLE_SCHEMA_PREFIX) :]
    if not suffix.isdigit():
        raise ValueError(f"Not a {BUNDLE_SCHEMA} bundle (schema={text!r})")
    if int(suffix) != _BUNDLE_SCHEMA_MAJOR:
        raise ValueError(
            f"Unsupported bundle schema major version: found {text!r}, "
            f"this engine supports {BUNDLE_SCHEMA!r}"
        )


def _engine_version() -> str:
    try:
        return importlib.metadata.version("cartolex")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def validate_taxonomy(doc: Mapping[str, Any]) -> None:
    """Structural check for a ``map_taxonomy/1`` document.

    Only checks the shape :func:`~cartolex.atlas.map_merge.anchor_concepts`
    actually consumes: the schema tag and a top-level ``concepts`` list whose
    entries carry a ``terms`` list of strings. Everything else the format may
    carry (``subfields``, ``weights_basis``, ``clustering_signature``,
    per-concept ``label``/``subfield_id``, …) is consumer metadata and is
    passed through uninterpreted.
    """
    if doc.get("schema") != TAXONOMY_SCHEMA:
        raise ValueError(f"Not a {TAXONOMY_SCHEMA} document (schema={doc.get('schema')!r})")
    concepts = doc.get("concepts")
    if not isinstance(concepts, list):
        raise ValueError(f"{TAXONOMY_SCHEMA} document must have a 'concepts' list")
    for i, concept in enumerate(concepts):
        if not isinstance(concept, Mapping):
            raise ValueError(f"concepts[{i}] is not an object")
        terms = concept.get("terms")
        if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
            raise ValueError(f"concepts[{i}].terms must be a list of strings")
    subfields = doc.get("subfields")
    if subfields is not None and not isinstance(subfields, list):
        raise ValueError(f"{TAXONOMY_SCHEMA} document 'subfields' must be a list when present")


@dataclass
class CohortBundle:
    """One cohort's inputs to the multi-cohort merge, held in memory.

    ``meta`` is the full parsed ``bundle_meta.json`` (unknown keys preserved
    verbatim — round-trip safe). ``entities`` carries ``entity_id``, ``unit``
    and any extra consumer facet columns untouched. ``taxonomy`` and
    ``decisions`` are ``None`` when the corresponding optional file is absent.
    """

    meta: dict[str, Any]
    entities: pd.DataFrame
    terms_long: pd.DataFrame
    vocabulary: pd.DataFrame
    taxonomy: dict[str, Any] | None
    decisions: dict[str, str] | None

    @property
    def cohort_id(self) -> str:
        """The cohort identifier stamped in ``bundle_meta.json``."""
        return str(self.meta["cohort_id"])

    def to_cohort_input(self) -> CohortInput:
        """Pivot ``terms_long`` back into a dense, deterministically ordered
        :class:`~cartolex.atlas.map_merge.CohortInput`.

        Entity rows are sorted by ``entity_id`` and terms are sorted (drawn
        from ``vocabulary``, so zero-usage terms are never lost); a
        reconciliation built from a re-read bundle therefore reproduces
        exactly what the original in-memory inputs produce. Units come from
        ``entities.unit`` (``""`` when the column or a value is missing/NaN).
        """
        entities_sorted = self.entities.sort_values("entity_id", kind="stable").reset_index(
            drop=True
        )
        entity_ids = [str(e) for e in entities_sorted["entity_id"]]
        row_of_entity = {e: i for i, e in enumerate(entity_ids)}
        if "unit" in entities_sorted.columns:
            units = ["" if pd.isna(u) else str(u) for u in entities_sorted["unit"]]
        else:
            units = ["" for _ in entity_ids]

        terms_sorted = sorted(str(t) for t in self.vocabulary["term"])
        col_of_term = {t: j for j, t in enumerate(terms_sorted)}

        X_tf = np.zeros((len(entity_ids), len(terms_sorted)))
        for row in self.terms_long.itertuples(index=False):
            i = row_of_entity.get(str(row.entity_id))
            j = col_of_term.get(str(row.term))
            if i is not None and j is not None:
                X_tf[i, j] = float(row.tf)

        return CohortInput(
            cohort_id=self.cohort_id,
            terms=terms_sorted,
            X_tf=X_tf,
            researcher_ids=entity_ids,
            units=units,
        )


def build_bundle(
    cohort_input: CohortInput,
    *,
    scores: np.ndarray | None = None,
    taxonomy: Mapping[str, Any] | None = None,
    decisions: Mapping[str, str] | None = None,
    profile: Mapping[str, Any] | None = None,
    build_date: str = "",
    producer: str = "",
) -> CohortBundle:
    """Build a :class:`CohortBundle` from one cohort's in-memory inputs.

    *scores* (optional) is a boosted-score matrix with the same shape as
    ``cohort_input.X_tf`` (e.g. TF-IDF); when omitted, the ``score`` /
    ``score_total`` tracks are empty (``NaN``). The ``vocabulary`` table and
    the ``counters`` are computed here from *cohort_input*, so zero-usage
    terms are preserved. *profile* is an opaque consumer object stored
    verbatim in ``meta``; when omitted the key is absent entirely.
    ``engine_version`` is stamped from the installed ``cartolex`` package
    (``"unknown"`` when not installed).
    """
    X_tf = to_dense(cohort_input.X_tf)
    S: np.ndarray | None = None
    if scores is not None:
        S = np.asarray(scores, dtype=float)
        if S.shape != X_tf.shape:
            raise ValueError(
                f"scores shape {S.shape} does not match X_tf shape {X_tf.shape} "
                f"for cohort {cohort_input.cohort_id!r}"
            )

    terms = [str(t) for t in cohort_input.terms]
    entity_ids = [str(e) for e in cohort_input.researcher_ids]
    units = (
        [str(u) for u in cohort_input.units]
        if cohort_input.units is not None
        else ["" for _ in entity_ids]
    )

    term_rows: list[dict[str, Any]] = []
    for i, eid in enumerate(entity_ids):
        for j, term in enumerate(terms):
            tf = X_tf[i, j]
            if tf == 0:
                continue
            score = float(S[i, j]) if S is not None else float("nan")
            term_rows.append({"entity_id": eid, "term": term, "tf": float(tf), "score": score})
    terms_long = pd.DataFrame(term_rows, columns=list(_ENTITY_TERMS_COLUMNS))

    entities = pd.DataFrame({"entity_id": entity_ids, "unit": units})

    vocab_rows: list[dict[str, Any]] = []
    for j, term in enumerate(terms):
        col = X_tf[:, j]
        present = col > 0
        if S is not None and present.any():
            vals = S[present, j]
            score_total = float(np.nansum(vals)) if np.any(~np.isnan(vals)) else float("nan")
        else:
            score_total = float("nan")
        vocab_rows.append(
            {
                "term": term,
                "n_entities": int(present.sum()),
                "tf_total": float(col.sum()),
                "score_total": score_total,
            }
        )
    vocabulary = pd.DataFrame(vocab_rows, columns=list(_VOCABULARY_COLUMNS))

    meta: dict[str, Any] = {
        "schema": BUNDLE_SCHEMA,
        "cohort_id": str(cohort_input.cohort_id),
        "build_date": str(build_date),
        "counters": {
            "n_entities": len(entity_ids),
            "n_terms": len(terms),
            "n_term_rows": len(terms_long),
        },
        "producer": str(producer),
        "engine_version": _engine_version(),
    }
    if profile is not None:
        meta["profile"] = dict(profile)

    return CohortBundle(
        meta=meta,
        entities=entities,
        terms_long=terms_long,
        vocabulary=vocabulary,
        taxonomy=dict(taxonomy) if taxonomy is not None else None,
        decisions=dict(decisions) if decisions is not None else None,
    )


def _write_csv_sorted(df: pd.DataFrame, columns: list[str], sort_by: list[str], path: Path) -> None:
    """Write *df* with the fixed *columns* first (extras pass through), rows sorted."""
    ordered = list(columns) + [c for c in df.columns if c not in columns]
    out = df.reindex(columns=ordered).sort_values(sort_by, kind="stable").reset_index(drop=True)
    out.to_csv(path, index=False)


def _write_bundle_dir(bundle: CohortBundle, dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "bundle_meta.json").write_text(
        json.dumps(bundle.meta, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    _write_csv_sorted(
        bundle.terms_long,
        list(_ENTITY_TERMS_COLUMNS),
        ["entity_id", "term"],
        dir_path / "entity_terms.csv",
    )
    _write_csv_sorted(
        bundle.entities, ["entity_id", "unit"], ["entity_id"], dir_path / "entities.csv"
    )
    _write_csv_sorted(
        bundle.vocabulary, list(_VOCABULARY_COLUMNS), ["term"], dir_path / "vocabulary.csv"
    )
    if bundle.taxonomy is not None:
        (dir_path / "taxonomy.json").write_text(
            json.dumps(bundle.taxonomy, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if bundle.decisions is not None:
        save_decisions(bundle.decisions, dir_path / "decisions.json")


def _zip_dir(src_dir: Path, dest_zip: Path) -> None:
    """Zip *src_dir*'s files with fixed timestamps → byte-identical archives."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in src_dir.rglob("*") if p.is_file()):
            info = zipfile.ZipInfo(
                path.relative_to(src_dir).as_posix(), date_time=_ZIP_FIXED_DATE_TIME
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())


def write_bundle(bundle: CohortBundle, dest: Path) -> Path:
    """Write *bundle* to *dest* — a directory, or a ``.zip`` archive if the suffix is ``.zip``.

    Deterministic: CSV rows sorted, JSON keys sorted
    (``ensure_ascii=False``), fixed column order, fixed zip timestamps —
    writing the same bundle twice yields byte-identical files.
    """
    dest = Path(dest)
    if dest.suffix == ".zip":
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp) / "bundle"
            _write_bundle_dir(bundle, tmp_dir)
            _zip_dir(tmp_dir, dest)
    else:
        _write_bundle_dir(bundle, dest)
    logger.info("Wrote %s bundle for cohort %s to %s", BUNDLE_SCHEMA, bundle.cohort_id, dest)
    return dest


def _read_bundle_dir(dir_path: Path) -> CohortBundle:
    meta_path = dir_path / "bundle_meta.json"
    if not meta_path.exists():
        raise ValueError(f"Bundle {dir_path} is missing required file(s): ['bundle_meta.json']")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    # Check the schema major before the required-files list below: a future
    # major version may restructure that list entirely, so a schema mismatch
    # must surface as "unsupported schema major", not a misleading "missing
    # required file(s)" computed against *this* engine's file list.
    _check_schema(meta.get("schema"))

    missing = [name for name in _REQUIRED_FILES if not (dir_path / name).exists()]
    if missing:
        raise ValueError(f"Bundle {dir_path} is missing required file(s): {missing}")

    terms_long = pd.read_csv(dir_path / "entity_terms.csv", dtype={"entity_id": str, "term": str})
    entities = pd.read_csv(dir_path / "entities.csv", dtype={"entity_id": str})
    vocabulary = pd.read_csv(dir_path / "vocabulary.csv", dtype={"term": str})
    # A no-score bundle round-trips with an all-empty score column; make sure
    # it comes back float NaN (not an object column of empty strings).
    for frame, col in ((terms_long, "score"), (vocabulary, "score_total")):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    taxonomy_path = dir_path / "taxonomy.json"
    taxonomy = (
        json.loads(taxonomy_path.read_text(encoding="utf-8")) if taxonomy_path.exists() else None
    )

    decisions_path = dir_path / "decisions.json"
    decisions = load_decisions(decisions_path) if decisions_path.exists() else None

    return CohortBundle(
        meta=meta,
        entities=entities,
        terms_long=terms_long,
        vocabulary=vocabulary,
        taxonomy=taxonomy,
        decisions=decisions,
    )


def read_bundle(src: Path) -> CohortBundle:
    """Read a bundle from a directory or ``.zip`` archive; validates before returning.

    Forward-compatible by design: unknown files are ignored, missing optional
    files (``taxonomy.json``, ``decisions.json``) yield ``None``, and unknown
    ``bundle_meta.json`` keys are preserved in ``meta``.
    """
    src = Path(src)
    if src.suffix == ".zip":
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp) / "bundle"
            with zipfile.ZipFile(src) as zf:
                zf.extractall(tmp_dir)
            bundle = _read_bundle_dir(tmp_dir)
    else:
        bundle = _read_bundle_dir(src)
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle: CohortBundle) -> None:
    """Structural validation of a :class:`CohortBundle` — schema/shape only.

    Checks: a supported ``map_bundle/2`` schema tag; every
    ``terms_long.entity_id`` present in ``entities``; ``tf`` finite and
    non-negative; no duplicate ``(entity_id, term)`` pair; every
    ``terms_long`` term present in ``vocabulary``; and the taxonomy validates
    when present.

    Deliberately **not** checked here: id-format regexes, k-anonymity
    thresholds, or any notion of "tier" — those are consumer policy, not
    engine structure.
    """
    _check_schema(bundle.meta.get("schema"))

    cohort_id = bundle.meta.get("cohort_id")
    if not isinstance(cohort_id, str) or not cohort_id:
        raise ValueError(
            f"bundle_meta.json must have a non-empty string 'cohort_id' (found {cohort_id!r})"
        )

    if "entity_id" not in bundle.entities.columns:
        raise ValueError("entities is missing an 'entity_id' column")

    entity_dup_mask = bundle.entities.duplicated(subset=["entity_id"], keep=False)
    if entity_dup_mask.any():
        dups = sorted(set(bundle.entities.loc[entity_dup_mask, "entity_id"].astype(str)))
        raise ValueError(f"entities has {len(dups)} duplicate entity_id value(s): {dups[:5]}")

    entity_ids = set(bundle.entities["entity_id"].astype(str))

    terms_long = bundle.terms_long
    for col in ("entity_id", "term", "tf"):
        if col not in terms_long.columns:
            raise ValueError(f"terms_long is missing a {col!r} column")

    phantom = set(terms_long["entity_id"].astype(str)) - entity_ids
    if phantom:
        raise ValueError(
            f"terms_long references {len(phantom)} entity id(s) absent from entities: "
            f"{sorted(phantom)[:5]}"
        )

    tf = terms_long["tf"].to_numpy(dtype=float)
    if not np.all(np.isfinite(tf)):
        raise ValueError("terms_long.tf contains non-finite value(s)")
    if np.any(tf < 0):
        raise ValueError("terms_long.tf contains negative value(s)")

    dup_mask = terms_long.duplicated(subset=["entity_id", "term"], keep=False)
    if dup_mask.any():
        dups = terms_long.loc[dup_mask, ["entity_id", "term"]].drop_duplicates()
        raise ValueError(
            f"terms_long has {len(dups)} duplicate (entity_id, term) pair(s): "
            f"{list(dups.itertuples(index=False, name=None))[:5]}"
        )

    if "term" not in bundle.vocabulary.columns:
        raise ValueError("vocabulary is missing a 'term' column")

    vocab_dup_mask = bundle.vocabulary.duplicated(subset=["term"], keep=False)
    if vocab_dup_mask.any():
        dups = sorted(set(bundle.vocabulary.loc[vocab_dup_mask, "term"].astype(str)))
        raise ValueError(f"vocabulary has {len(dups)} duplicate term value(s): {dups[:5]}")

    vocab_terms = set(bundle.vocabulary["term"].astype(str))
    unknown = set(terms_long["term"].astype(str)) - vocab_terms
    if unknown:
        raise ValueError(
            f"terms_long references {len(unknown)} term(s) absent from the vocabulary: "
            f"{sorted(unknown)[:5]}"
        )

    if bundle.taxonomy is not None:
        validate_taxonomy(bundle.taxonomy)
