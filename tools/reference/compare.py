# SPDX-License-Identifier: MIT
"""Compare a reference run with another run of the same world, stage by stage.

Usage::

    python compare.py --reference DIR --current DIR [--report out.md] [--json out.json]

Both directories hold a ``cartolex-reference/1`` output (``run.py``). Every
artifact is first compared by the sha256 of its full-precision canonical form;
when the hashes differ, the stored data are compared with the metric that fits
the artifact's kind. Each stage gets a verdict:

``identical``
    every artifact has the same hash;
``within tolerance``
    some artifacts differ, but only by floating-point noise as defined by the
    tolerances below (or, for group labels, by a renaming of the groups);
``different``
    anything else, including an artifact that appeared or disappeared.

The Markdown table is printed (and written with ``--report``); the last line
printed is a one-line summary. Exit status: 0 when every stage is identical or
within tolerance, 1 when a stage is different, 2 when the two runs cannot be
compared (another format, or other input worlds).

Needs numpy only (plus the standard library).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

FORMAT = "cartolex-reference/1"

# ── Tolerances ───────────────────────────────────────────────────────────────
#
# "Within tolerance" means "the same numbers up to floating-point noise": a
# change of summation order, a different but equivalent BLAS path. Anything
# larger is a real difference that must be removed or explained.

#: Scalars in tables and documents: |a - b| <= ATOL + RTOL * |reference|.
RTOL = 1e-6
ATOL = 1e-9
#: Arrays: |a - b| <= ATOL + RTOL * max|reference| (scale of the whole array).
ARRAY_RTOL = 1e-6
#: Latent embeddings: largest principal angle between the column spaces (radians).
ANGLE_TOL = 1e-4
#: 2-D layouts: Procrustes disparity (0 = same shape) and neighbour preservation.
PROCRUSTES_TOL = 1e-6
KNN_K = 10
KNN_MIN = 0.99

#: *explained*: the artifact differs, but exactly as recorded, with a reason, in the
#: ledger of explained differences (``tools/reference/explained.toml``).
VERDICTS = ("identical", "within tolerance", "explained", "different")
_RANK = {v: i for i, v in enumerate(VERDICTS)}


def worst(verdicts: Iterable[str]) -> str:
    """The most severe of *verdicts* (``identical`` for none)."""
    return max(verdicts, key=_RANK.__getitem__, default="identical")


# ── Metrics ──────────────────────────────────────────────────────────────────


def adjusted_rand_index(a: Iterable[int], b: Iterable[int]) -> float:
    """Adjusted Rand index of two labelings of the same items (1.0 = same partition)."""
    a = np.asarray(list(a))
    b = np.asarray(list(b))
    if a.shape != b.shape:
        raise ValueError("labelings of different lengths")
    n = a.size
    if n < 2:
        return 1.0
    _, ai = np.unique(a, return_inverse=True)
    _, bi = np.unique(b, return_inverse=True)
    table = np.zeros((ai.max() + 1, bi.max() + 1), dtype=np.int64)
    np.add.at(table, (ai, bi), 1)

    def comb2(x: np.ndarray) -> float:
        x = x.astype(np.float64)
        return float((x * (x - 1) / 2).sum())

    sum_ij = comb2(table)
    sum_a = comb2(table.sum(axis=1))
    sum_b = comb2(table.sum(axis=0))
    total = n * (n - 1) / 2
    expected = sum_a * sum_b / total
    maximum = (sum_a + sum_b) / 2
    if maximum == expected:
        return 1.0
    return (sum_ij - expected) / (maximum - expected)


def procrustes_disparity(ref: np.ndarray, cur: np.ndarray) -> float:
    """Procrustes disparity of two point sets (translation, scale and rotation removed).

    Both sets are centred and scaled to unit Frobenius norm; the disparity is
    the sum of squared distances after the best rotation (0 = same shape, at
    most 1).
    """
    A = np.asarray(ref, dtype=np.float64)
    B = np.asarray(cur, dtype=np.float64)
    if A.shape != B.shape:
        raise ValueError("point sets of different shapes")
    A = A - A.mean(axis=0)
    B = B - B.mean(axis=0)
    na, nb = np.linalg.norm(A), np.linalg.norm(B)
    if na == 0 or nb == 0:
        return 0.0 if na == nb else 1.0
    A, B = A / na, B / nb
    u, s, vt = np.linalg.svd(A.T @ B)
    return float(max(0.0, 1.0 - s.sum() ** 2))


def knn_indices(X: np.ndarray, k: int, chunk: int = 512) -> np.ndarray:
    """Indices of each row's *k* nearest other rows (Euclidean; ties by index)."""
    X = np.asarray(X, dtype=np.float64)
    n = X.shape[0]
    k = max(0, min(k, n - 1))
    out = np.zeros((n, k), dtype=np.int64)
    if k == 0:
        return out
    sq = (X * X).sum(axis=1)
    for start in range(0, n, chunk):
        stop = min(n, start + chunk)
        d = sq[start:stop, None] + sq[None, :] - 2.0 * X[start:stop] @ X.T
        d[np.arange(stop - start), np.arange(start, stop)] = np.inf
        out[start:stop] = np.argsort(d, axis=1, kind="stable")[:, :k]
    return out


def knn_preservation(ref: np.ndarray, cur: np.ndarray, k: int = KNN_K) -> float:
    """Mean fraction of each point's *k* nearest neighbours kept (1.0 = all kept)."""
    a = knn_indices(ref, k)
    b = knn_indices(cur, k)
    if a.shape[1] == 0:
        return 1.0
    kept = [len(set(x) & set(y)) / a.shape[1] for x, y in zip(a.tolist(), b.tolist(), strict=True)]
    return float(np.mean(kept))


def principal_angles(ref: np.ndarray, cur: np.ndarray) -> np.ndarray:
    """Principal angles (radians, ascending) between the column spaces of two matrices."""
    qa, _ = np.linalg.qr(np.asarray(ref, dtype=np.float64))
    qb, _ = np.linalg.qr(np.asarray(cur, dtype=np.float64))
    s = np.linalg.svd(qa.T @ qb, compute_uv=False)
    return np.sort(np.arccos(np.clip(s, -1.0, 1.0)))


def sign_aligned(ref: np.ndarray, cur: np.ndarray) -> np.ndarray:
    """*cur* with each column's sign flipped to best match *ref* (SVD signs are arbitrary)."""
    cur = np.array(cur, dtype=np.float64, copy=True)
    for j in range(cur.shape[1]):
        if float(ref[:, j] @ cur[:, j]) < 0:
            cur[:, j] = -cur[:, j]
    return cur


@dataclass
class FloatDiff:
    """Running maximum of absolute and relative differences between float values."""

    max_abs: float = 0.0
    max_rel: float = 0.0
    count: int = 0
    beyond: int = 0  # values outside the tolerance
    nan_mismatch: int = 0

    def add(self, ref: float, cur: float, *, scale: float | None = None) -> None:
        self.count += 1
        if math.isnan(ref) or math.isnan(cur):
            if math.isnan(ref) != math.isnan(cur):
                self.nan_mismatch += 1
            return
        if ref == cur:
            return
        if math.isinf(ref) or math.isinf(cur):
            self.beyond += 1
            self.max_abs = math.inf
            return
        diff = abs(ref - cur)
        rel = diff / abs(ref) if ref != 0 else math.inf
        self.max_abs = max(self.max_abs, diff)
        self.max_rel = max(self.max_rel, rel)
        bound = ATOL + RTOL * (abs(ref) if scale is None else scale)
        if diff > bound:
            self.beyond += 1

    @property
    def ok(self) -> bool:
        return self.beyond == 0 and self.nan_mismatch == 0

    def text(self) -> str:
        out = f"max abs {self.max_abs:.3g}, max rel {self.max_rel:.3g}"
        if self.beyond:
            out += f", {self.beyond} beyond tolerance"
        if self.nan_mismatch:
            out += f", {self.nan_mismatch} NaN mismatch(es)"
        return out


# ── Reading stored artifacts ─────────────────────────────────────────────────


def _read_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == ".gz" else data


def read_json(path: Path) -> Any:
    """A stored JSON artifact (plain or gzip-compressed)."""
    return json.loads(_read_bytes(path).decode("utf-8"))


def read_table(path: Path, dtypes: dict[str, str]) -> tuple[list[str], list[list[Any]]]:
    """A stored table: column names and typed rows."""
    text = _read_bytes(path).decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, [])
    rows = []
    for raw in reader:
        row: list[Any] = []
        for col, value in zip(header, raw, strict=True):
            kind = dtypes.get(col, "str")
            if kind == "float":
                row.append(float(value))
            elif kind == "int":
                row.append(int(value))
            else:
                row.append(value)
        rows.append(row)
    return header, rows


def read_array(path: Path) -> np.ndarray:
    """A stored ``.npy`` array (plain or gzip-compressed)."""
    return np.load(io.BytesIO(_read_bytes(path)), allow_pickle=False)


# ── Per-kind comparisons ─────────────────────────────────────────────────────


@dataclass
class ArtifactResult:
    """Outcome of one artifact's comparison."""

    stage: str
    name: str
    kind: str
    verdict: str
    detail: str = ""


def compare_tables(
    ref: tuple[list[str], list[list[Any]]],
    cur: tuple[list[str], list[list[Any]]],
    *,
    keys: list[str],
    ordered: bool,
    dtypes: dict[str, str],
) -> tuple[str, str]:
    """Compare two tables; return ``(verdict, detail)``."""
    (hr, rr), (hc, rc) = ref, cur
    if hr != hc:
        missing = [c for c in hr if c not in hc]
        extra = [c for c in hc if c not in hr]
        parts = []
        if missing:
            parts.append(f"columns missing {missing}")
        if extra:
            parts.append(f"columns added {extra}")
        if not parts:
            parts.append("column order changed")
        return "different", "; ".join(parts)
    key_idx = [hr.index(k) for k in keys]
    notes: list[str] = []
    if key_idx:
        kr = [tuple(r[i] for i in key_idx) for r in rr]
        kc = [tuple(r[i] for i in key_idx) for r in rc]
        if len(set(kr)) == len(kr) and len(set(kc)) == len(kc):
            only_r = sorted(set(kr) - set(kc), key=str)
            only_c = sorted(set(kc) - set(kr), key=str)
            if only_r or only_c:
                examples = ", ".join(str(list(k)) for k in (only_r + only_c)[:3])
                return (
                    "different",
                    f"rows: {len(rr)} → {len(rc)}; {len(only_r)} removed, "
                    f"{len(only_c)} added (e.g. {examples})",
                )
            if ordered and kr != kc:
                notes.append("row order changed")
            pos = {k: i for i, k in enumerate(kc)}
            pairs = [(r, rc[pos[k]]) for r, k in zip(rr, kr, strict=True)]
        else:
            pairs = None
    else:
        pairs = None
    if pairs is None:
        if len(rr) != len(rc):
            return "different", f"rows: {len(rr)} → {len(rc)}"
        pairs = list(zip(rr, rc, strict=True))
    floats = FloatDiff()
    exact_mismatch: dict[str, int] = {}
    for a, b in pairs:
        for j, col in enumerate(hr):
            if dtypes.get(col) == "float":
                floats.add(a[j], b[j])
            elif a[j] != b[j]:
                exact_mismatch[col] = exact_mismatch.get(col, 0) + 1
    if exact_mismatch:
        notes.append(
            "values differ in "
            + ", ".join(f"{c} ({n} row(s))" for c, n in sorted(exact_mismatch.items()))
        )
    if floats.count:
        notes.append(floats.text())
    verdict = (
        "different"
        if (exact_mismatch or not floats.ok or "row order changed" in notes)
        else "within tolerance"
    )
    return verdict, "; ".join(notes) or "same values, different encoding"


def compare_documents(ref: Any, cur: Any) -> tuple[str, str]:
    """Compare two JSON documents structurally, floats within tolerance."""
    floats = FloatDiff()
    problems: list[str] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if k not in b:
                    problems.append(f"{path}/{k} removed")
                elif k not in a:
                    problems.append(f"{path}/{k} added")
                else:
                    walk(a[k], b[k], f"{path}/{k}")
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                problems.append(f"{path} length {len(a)} → {len(b)}")
                return
            for i, (x, y) in enumerate(zip(a, b, strict=True)):
                walk(x, y, f"{path}[{i}]")
        elif (
            isinstance(a, int | float)
            and isinstance(b, int | float)
            and not isinstance(a, bool)
            and not isinstance(b, bool)
            and (isinstance(a, float) or isinstance(b, float))
        ):
            before = floats.beyond + floats.nan_mismatch
            floats.add(float(a), float(b))
            if floats.beyond + floats.nan_mismatch > before:
                problems.append(f"{path}: {a!r} → {b!r}")
        elif a != b or type(a) is not type(b):
            problems.append(f"{path}: {_short(a)} → {_short(b)}")

    walk(ref, cur, "")
    if problems:
        shown = "; ".join(problems[:3])
        more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
        return "different", f"{len(problems)} difference(s): {shown}{more}"
    return "within tolerance", floats.text() if floats.count else "same values, different encoding"


def _short(value: Any, width: int = 40) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else repr(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def compare_strings(ref: list[str], cur: list[str]) -> tuple[str, str]:
    """Compare two ordered lists (a vocabulary): set first, then order."""
    only_r = sorted(set(ref) - set(cur))
    only_c = sorted(set(cur) - set(ref))
    if only_r or only_c:
        examples = ", ".join(repr(s) for s in (only_r + only_c)[:3])
        return (
            "different",
            f"set: {len(ref)} → {len(cur)}; {len(only_r)} removed, {len(only_c)} added "
            f"(e.g. {examples})",
        )
    if ref != cur:
        moved = sum(1 for a, b in zip(ref, cur, strict=True) if a != b)
        return "different", f"same set, order differs at {moved} position(s)"
    return "within tolerance", "same items, different encoding"


def compare_labels(ref: list[int], cur: list[int]) -> tuple[str, str]:
    """Compare two labelings: exact, else the adjusted Rand index."""
    if len(ref) != len(cur):
        return "different", f"length {len(ref)} → {len(cur)}"
    if ref == cur:
        return "within tolerance", "same labels, different encoding"
    ari = adjusted_rand_index(ref, cur)
    groups = f"{len(set(ref))} → {len(set(cur))} groups"
    if ari >= 1.0 - 1e-12:
        return "within tolerance", f"same partition, groups renamed (ARI 1.0; {groups})"
    moved = sum(1 for a, b in zip(ref, cur, strict=True) if a != b)
    return "different", f"ARI {ari:.4f}; {groups}; {moved} item(s) with another label"


def compare_arrays(ref: np.ndarray, cur: np.ndarray, role: str) -> tuple[str, str]:
    """Compare two numeric arrays according to their role."""
    if ref.shape != cur.shape:
        return "different", f"shape {list(ref.shape)} → {list(cur.shape)}"
    ref = ref.astype(np.float64)
    cur = cur.astype(np.float64)
    if ref.size == 0:
        return "within tolerance", "empty"
    if np.isnan(ref).any() or np.isnan(cur).any():
        if not np.array_equal(np.isnan(ref), np.isnan(cur)):
            return "different", "NaN positions differ"
        ref = np.nan_to_num(ref)
        cur = np.nan_to_num(cur)
    scale = float(np.max(np.abs(ref)))
    bound = ATOL + ARRAY_RTOL * scale
    if role == "embedding" and ref.ndim == 2:
        aligned = sign_aligned(ref, cur)
        diff = float(np.max(np.abs(aligned - ref)))
        angles = principal_angles(ref, cur) if ref.shape[1] <= ref.shape[0] else np.zeros(1)
        max_angle = float(angles.max()) if angles.size else 0.0
        detail = (
            f"max abs (signs aligned) {diff:.3g} (scale {scale:.3g}); "
            f"largest principal angle {max_angle:.3g} rad"
        )
        ok = diff <= bound and max_angle <= ANGLE_TOL
        return ("within tolerance" if ok else "different"), detail
    if role == "layout" and ref.ndim == 2:
        diff = float(np.max(np.abs(cur - ref)))
        disparity = procrustes_disparity(ref, cur)
        knn = knn_preservation(ref, cur)
        detail = (
            f"max abs {diff:.3g} (scale {scale:.3g}); Procrustes disparity {disparity:.3g}; "
            f"{KNN_K}-NN preserved {knn:.4f}"
        )
        ok = (diff <= bound) or (disparity <= PROCRUSTES_TOL and knn >= KNN_MIN)
        return ("within tolerance" if ok else "different"), detail
    diff = np.abs(cur - ref)
    max_abs = float(diff.max())
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(ref != 0, diff / np.abs(ref), np.where(diff > 0, np.inf, 0.0))
    detail = f"max abs {max_abs:.3g}, max rel {float(rel.max()):.3g} (scale {scale:.3g})"
    return ("within tolerance" if max_abs <= bound else "different"), detail


def compare_artifact(
    stage: str, name: str, ref_dir: Path, cur_dir: Path, ref: dict, cur: dict
) -> ArtifactResult:
    """Compare one artifact of two runs from their manifest entries and stored files."""
    kind = ref.get("kind", "?")
    if ref.get("sha256") == cur.get("sha256") and kind == cur.get("kind"):
        return ArtifactResult(stage, name, kind, "identical")
    if kind != cur.get("kind"):
        return ArtifactResult(stage, name, kind, "different", f"kind {kind} → {cur.get('kind')}")
    if kind == "hash":
        return ArtifactResult(
            stage, name, kind, "different", "redundant copy changed (hash only, no stored data)"
        )
    ref_path, cur_path = ref_dir / ref["file"], cur_dir / cur["file"]
    absent = [
        f"{side} {path.name}"
        for side, path in (("reference", ref_path), ("current", cur_path))
        if not path.is_file()
    ]
    if absent:
        return ArtifactResult(
            stage,
            name,
            kind,
            "different",
            f"hash changed; stored data missing ({', '.join(absent)}), no metrics",
        )
    if kind == "table":
        verdict, detail = compare_tables(
            read_table(ref_path, ref.get("dtypes", {})),
            read_table(cur_path, cur.get("dtypes", {})),
            keys=list(ref.get("keys", [])),
            ordered=bool(ref.get("ordered")),
            dtypes=ref.get("dtypes", {}),
        )
        if ref.get("dtypes") != cur.get("dtypes"):
            verdict, detail = "different", f"column types changed; {detail}"
    elif kind == "json":
        verdict, detail = compare_documents(read_json(ref_path), read_json(cur_path))
    elif kind == "strings":
        verdict, detail = compare_strings(read_json(ref_path), read_json(cur_path))
    elif kind == "labels":
        verdict, detail = compare_labels(read_json(ref_path), read_json(cur_path))
    elif kind == "array":
        verdict, detail = compare_arrays(
            read_array(ref_path), read_array(cur_path), ref.get("role", "values")
        )
    else:
        verdict, detail = "different", f"unknown kind {kind!r}"
    return ArtifactResult(stage, name, kind, verdict, detail)


# ── Whole runs ───────────────────────────────────────────────────────────────


@dataclass
class StageResult:
    """Verdict of one stage and the results of its artifacts."""

    stage: str
    verdict: str
    artifacts: list[ArtifactResult] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Short description of what differs (empty when identical)."""
        parts = [
            f"{a.name}: {a.verdict}" + (f" ({a.detail})" if a.detail else "")
            for a in self.artifacts
            if a.verdict != "identical"
        ]
        return "; ".join(parts)


@dataclass
class Report:
    """Result of comparing two runs."""

    stages: list[StageResult]
    notes: list[str] = field(default_factory=list)
    comparable: bool = True

    @property
    def verdict(self) -> str:
        return worst(s.verdict for s in self.stages) if self.comparable else "different"

    def counts(self) -> dict[str, int]:
        out = {v: 0 for v in VERDICTS}
        for s in self.stages:
            out[s.verdict] += 1
        return out

    def summary_line(self) -> str:
        """The one-line summary printed last."""
        if not self.comparable:
            return "reference: NOT COMPARABLE — " + "; ".join(self.notes[:1])
        c = self.counts()
        n = len(self.stages)
        text = f"{n} stage{'' if n == 1 else 's'}: {c['identical']} identical"
        if c["within tolerance"]:
            text += f", {c['within tolerance']} within tolerance"
        if c["explained"]:
            text += f", {c['explained']} explained"
        if c["different"]:
            names = ", ".join(s.stage for s in self.stages if s.verdict == "different")
            text += f", {c['different']} different ({names})"
        return text


def _environment_notes(ref: dict, cur: dict) -> list[str]:
    notes = []
    er, ec = ref.get("environment", {}), cur.get("environment", {})
    for key in ("python", "platform", "engine_version", "engine_fingerprint"):
        if er.get(key) != ec.get(key):
            notes.append(f"environment {key}: {er.get(key)} → {ec.get(key)}")
    libs_r, libs_c = er.get("libraries", {}), ec.get("libraries", {})
    for lib in sorted(set(libs_r) | set(libs_c)):
        if libs_r.get(lib) != libs_c.get(lib):
            notes.append(f"library {lib}: {libs_r.get(lib)} → {libs_c.get(lib)}")
    if er.get("settings") != ec.get("settings"):
        notes.append("run settings (threads, hash seed, locale) differ")
    return notes


@dataclass(frozen=True)
class Explained:
    """One recorded difference: this artifact now has this hash, for this reason."""

    stage: str
    artifact: str
    sha256: str  # the current artifact's hash, or "absent"
    reason: str


def load_explained(path: Path, reference: str) -> list[Explained]:
    """Read the ledger entries of *reference* (e.g. ``S``, ``L``, ``merge``)."""
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out = []
    for entry in data.get("difference", []):
        if entry.get("reference") != reference:
            continue
        if not entry.get("reason"):
            raise ValueError(f"{path}: every [[difference]] needs a reason")
        out.append(Explained(entry["stage"], entry["artifact"], entry["sha256"], entry["reason"]))
    return out


def _apply_explained(stages: list[StageResult], cur: dict, ledger: list[Explained]) -> list[str]:
    """Turn recorded differences into *explained*; return notes on unused entries."""
    by_key = {(e.stage, e.artifact): e for e in ledger}
    used: set[tuple[str, str]] = set()
    for s in stages:
        for a in s.artifacts:
            entry = by_key.get((s.stage, a.name))
            if entry is None or a.verdict != "different":
                continue
            current = cur.get("artifacts", {}).get(s.stage, {}).get(a.name)
            sha = current["sha256"] if current else "absent"
            if sha == entry.sha256:
                a.verdict = "explained"
                a.detail = f"{a.detail} — explained: {entry.reason}".lstrip(" —")
                used.add((s.stage, a.name))
        s.verdict = worst(a.verdict for a in s.artifacts)
    return [
        f"explained entry no longer matches: {e.stage}/{e.artifact}"
        for e in ledger
        if (e.stage, e.artifact) not in used
    ]


def _input_worlds(inputs: Any) -> Any:
    """The input worlds of a manifest's ``inputs``, without derived data.

    A merge run also records the hashes of its cohorts' bundles; they are
    engine outputs (compared by the single-run reference's bundle stage), not
    input worlds, so they do not decide whether two runs can be compared.
    """
    if not isinstance(inputs, dict) or not isinstance(inputs.get("cohorts"), list):
        return inputs
    cohorts = [
        {k: v for k, v in c.items() if k != "bundle"} if isinstance(c, dict) else c
        for c in inputs["cohorts"]
    ]
    return {**inputs, "cohorts": cohorts}


def _bundle_notes(ref: dict, cur: dict) -> list[str]:
    """Notes naming the cohort bundle artifacts whose hashes differ (merge runs)."""
    notes = []
    ref_cohorts = (ref.get("inputs") or {}).get("cohorts") or []
    cur_cohorts = (cur.get("inputs") or {}).get("cohorts") or []
    for rc, cc in zip(ref_cohorts, cur_cohorts, strict=False):
        rb, cb = rc.get("bundle") or {}, cc.get("bundle") or {}
        changed = sorted(k for k in set(rb) | set(cb) if rb.get(k) != cb.get(k))
        if changed:
            notes.append(
                f"cohort {rc.get('domain_id', '?')}: bundle artifacts changed upstream "
                f"({', '.join(changed)}); see the single-run comparison's bundle stage"
            )
    return notes


def compare_runs(ref_dir: Path, cur_dir: Path, explained: list[Explained] | None = None) -> Report:
    """Compare two run directories and return the report."""
    ref = json.loads((ref_dir / "manifest.json").read_text(encoding="utf-8"))
    cur = json.loads((cur_dir / "manifest.json").read_text(encoding="utf-8"))
    notes = _environment_notes(ref, cur)
    if ref.get("format") != FORMAT or cur.get("format") != FORMAT:
        return Report([], [f"format {ref.get('format')} vs {cur.get('format')}"], False)
    if _input_worlds(ref.get("inputs")) != _input_worlds(cur.get("inputs")):
        return Report(
            [],
            ["the two runs were made on different input worlds (inputs differ)", *notes],
            False,
        )
    notes = _bundle_notes(ref, cur) + notes
    if ref.get("settings") != cur.get("settings"):
        notes.insert(0, "run settings differ between the two manifests")
    stages: list[StageResult] = []
    order = list(ref.get("stages", []))
    order += [s for s in cur.get("stages", []) if s not in order]
    for stage in order:
        ra = ref.get("artifacts", {}).get(stage)
        ca = cur.get("artifacts", {}).get(stage)
        if ra is None or ca is None:
            which = "current" if ca is None else "reference"
            stages.append(
                StageResult(
                    stage,
                    "different",
                    [ArtifactResult(stage, "*", "-", "different", f"stage absent from {which}")],
                )
            )
            continue
        results = []
        for name in sorted(set(ra) | set(ca)):
            if name not in ca:
                results.append(
                    ArtifactResult(
                        stage, name, ra[name]["kind"], "different", "absent from current"
                    )
                )
            elif name not in ra:
                results.append(
                    ArtifactResult(stage, name, ca[name]["kind"], "different", "new in current")
                )
            else:
                results.append(compare_artifact(stage, name, ref_dir, cur_dir, ra[name], ca[name]))
        stages.append(StageResult(stage, worst(r.verdict for r in results), results))
    if explained:
        notes += _apply_explained(stages, cur, explained)
    return Report(stages, notes)


def render_markdown(report: Report, *, title: str = "Reference comparison") -> str:
    """The report as Markdown: one row per stage, then the non-identical artifacts."""
    lines = [f"# {title}", ""]
    if not report.comparable:
        lines += ["**Not comparable.**", ""] + [f"- {n}" for n in report.notes] + [""]
        return "\n".join(lines)
    lines += ["| stage | verdict | artifacts | differences |", "| --- | --- | --- | --- |"]
    for s in report.stages:
        n_same = sum(1 for a in s.artifacts if a.verdict == "identical")
        lines.append(
            f"| {s.stage} | {s.verdict} | {n_same}/{len(s.artifacts)} identical | "
            f"{_cell(s.summary) or '—'} |"
        )
    changed = [a for s in report.stages for a in s.artifacts if a.verdict != "identical"]
    if changed:
        lines += [
            "",
            "## Artifacts that differ",
            "",
            "| stage | artifact | kind | verdict | metrics |",
        ]
        lines.append("| --- | --- | --- | --- | --- |")
        for a in changed:
            lines.append(f"| {a.stage} | {a.name} | {a.kind} | {a.verdict} | {_cell(a.detail)} |")
    if report.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in report.notes]
    lines += ["", f"**{report.summary_line()}**", ""]
    return "\n".join(lines)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def report_json(report: Report) -> dict[str, Any]:
    """The report as plain data."""
    return {
        "verdict": report.verdict,
        "comparable": report.comparable,
        "notes": report.notes,
        "stages": [
            {
                "stage": s.stage,
                "verdict": s.verdict,
                "artifacts": [
                    {"name": a.name, "kind": a.kind, "verdict": a.verdict, "detail": a.detail}
                    for a in s.artifacts
                ],
            }
            for s in report.stages
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Compare a run with the stored reference.")
    parser.add_argument("--reference", required=True, type=Path, help="reference run directory")
    parser.add_argument("--current", required=True, type=Path, help="run directory to check")
    parser.add_argument("--report", type=Path, help="also write the Markdown report here")
    parser.add_argument("--json", type=Path, help="also write the report as JSON here")
    parser.add_argument("--title", default="Reference comparison", help="report title")
    parser.add_argument(
        "--explained",
        type=Path,
        help="ledger of explained differences (tools/reference/explained.toml)",
    )
    parser.add_argument("--name", help="this reference's name in the ledger (S, L, merge)")
    args = parser.parse_args(argv)
    ledger = load_explained(args.explained, args.name) if args.explained and args.name else None
    report = compare_runs(args.reference, args.current, ledger)
    text = render_markdown(report, title=args.title)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report_json(report), indent=1) + "\n", encoding="utf-8")
    print(text)
    print(report.summary_line())
    if not report.comparable:
        return 2
    return 1 if report.verdict == "different" else 0


if __name__ == "__main__":
    raise SystemExit(main())
