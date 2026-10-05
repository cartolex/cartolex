# SPDX-License-Identifier: MIT
"""Make a copilot bundle of a project, and read a copilot result back.

The bundle's format and the kit are in :mod:`cartolex.copilot`; this module
assembles the zip from what the application has read (the tree, the vectors,
the candidates) and turns a result into proposals the curator reviews.

**What never leaves.** People are rows of the matrices in a random order; the
roster's names, identifiers, organisations and texts are never written. A
keyword or a candidate that holds a person's full name is left out. Usage
lines (triage, only when asked for) are cut from the texts around the term,
with every roster name, e-mail address, web address and identifier replaced
by a mark; the privacy summary then says so.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import secrets
import time
import unicodedata
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "THEMES_CONTAINS",
    "THEMES_NEVER",
    "TRIAGE_CONTAINS",
    "TRIAGE_NEVER",
    "NameMask",
    "kit_wheel",
    "themes_bundle",
    "triage_bundle",
    "usage_lines",
]

#: What a themes bundle holds, and never holds (shown before the export, and in ``PRIVACY.md``).
THEMES_CONTAINS = (
    "the theme tree (node ids, names, levels), the set-aside keywords and why, and the grouping's proposal",
    "the keywords, how many people use each, and their vectors in the keywords' space",
    "the comb's suggestions: the keywords whose texts support a higher node, or no theme",
    "which keywords each text uses, texts as numbered rows in a random order (never a text)",
    "each person's usage of the keywords and vector, as numbered rows in a random order",
    "the field's title and description, your curation notes and standing rules, as the assistant's context",
    "the cartolex kit (a wheel) and its guide",
)
THEMES_NEVER = (
    "texts or excerpts of texts",
    "people's names, identifiers or organisations",
    "a keyword that holds a person's name",
    "keys or settings",
)
TRIAGE_CONTAINS = (
    "the candidate keywords of the chosen bands, their language, band and why",
    "for each: how many people and texts use it, its other spellings, the longer phrases it sits in",
    "who uses which candidate, people as numbered columns in a random order",
    "your decisions so far on these candidates",
    "the field's title and description, your curation notes and standing rules, as the assistant's context",
    "the cartolex kit (a wheel) and its guide",
)
TRIAGE_USAGE = "a few short lines of text around each candidate, names and identifiers masked"
TRIAGE_NEVER = (
    "whole texts",
    "people's names, identifiers or organisations",
    "a candidate that holds a person's name",
    "keys or settings",
)
#: The packages of the kit's wheel (the engine the kit calls, and the kit).
KIT_PACKAGES = ("copilot", "atlas", "lexicon")
_DATE = (2026, 1, 1, 0, 0, 0)


# ── names ────────────────────────────────────────────────────────────────────


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


class NameMask:
    """The roster's names: which terms hold a full name, and a text with every name masked."""

    def __init__(self, people: Iterable[tuple[str, str]]) -> None:
        full: set[str] = set()
        words: set[str] = set()
        for first, last in people:
            f, la = _fold(first).strip(), _fold(last).strip()
            if f and la:
                full.add(f"{f} {la}")
                full.add(f"{la} {f}")
            for w in re.split(r"[\s\-']+", f"{f} {la}"):
                if len(w) >= 2:
                    words.add(w)
        self._full = (
            re.compile(
                r"(?<!\w)(?:"
                + "|".join(re.escape(x) for x in sorted(full, key=len, reverse=True))
                + r")(?!\w)"
            )
            if full
            else None
        )
        self._words = words
        self._pattern = (
            re.compile(
                r"(?<!\w)("
                + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
                + r")(?!\w)"
            )
            if words
            else None
        )

    def holds_name(self, term: str) -> bool:
        """Whether *term* holds a person's full name (first and last, in either order)."""
        folded = re.sub(r"[\s\-]+", " ", _fold(term))
        return self._full is not None and self._full.search(folded) is not None

    def mask(self, text: str) -> str:
        """*text* with e-mail and web addresses, identifiers and every roster name masked."""
        text = re.sub(r"\S+@\S+", "[address]", text)
        text = re.sub(r"(?i)\b(?:https?://|www\.)\S+", "[address]", text)
        text = re.sub(r"(?i)\b10\.\d{4,9}/\S+", "[identifier]", text)
        text = re.sub(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b", "[identifier]", text)
        if self._pattern is None:
            return text
        folded = _fold(text)
        if len(folded) != len(text):  # a character that folds to several: mask word by word
            return " ".join(
                "[name]" if _fold(w).strip(".,;:()") in self._words else w for w in text.split(" ")
            )
        out, last = [], 0
        for m in self._pattern.finditer(folded):
            out.append(text[last : m.start()])
            out.append("[name]")
            last = m.end()
        out.append(text[last:])
        return "".join(out)


def usage_lines(
    texts: Iterable[str],
    terms: Sequence[str],
    mask: NameMask,
    *,
    per_term: int = 2,
    width: int = 60,
) -> dict[str, list[str]]:
    """Up to *per_term* short lines showing each term in a text, names masked.

    Each text is read once: its word sequences (as long as the longest term)
    are looked up among the terms still short of lines, case aside.
    """
    word = re.compile(r"\w+")
    wanted: dict[tuple[str, ...], str] = {}
    for t in terms:
        key = tuple(w.casefold() for w in word.findall(t))
        if key:
            wanted.setdefault(key, t)
    out: dict[str, list[str]] = {t: [] for t in terms}
    lengths = sorted({len(k) for k in wanted})
    for text in texts:
        if not wanted:
            break
        spans = [(m.start(), m.end(), m.group().casefold()) for m in word.finditer(text)]
        seen: set[tuple[str, ...]] = set()
        for i in range(len(spans)):
            for n in lengths:
                if i + n > len(spans):
                    break
                key = tuple(s[2] for s in spans[i : i + n])
                term = wanted.get(key)
                if term is None or key in seen:
                    continue
                seen.add(key)
                a = max(spans[i][0] - width, 0)
                b = min(spans[i + n - 1][1] + width, len(text))
                line = " ".join(text[a:b].split())
                out[term].append(
                    ("…" if a else "") + mask.mask(line) + ("…" if b < len(text) else "")
                )
                if len(out[term]) >= per_term:
                    del wanted[key]
    return out


# ── the kit ──────────────────────────────────────────────────────────────────


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("cartolex")
    except PackageNotFoundError:  # pragma: no cover - a source tree without metadata
        return "0.0.0"


@lru_cache(maxsize=2)
def _wheel(package_dir: str, version: str) -> tuple[str, bytes]:
    root = Path(package_dir)
    files: dict[str, bytes] = {"cartolex/__init__.py": (root / "__init__.py").read_bytes()}
    for pkg in KIT_PACKAGES:
        for path in sorted((root / pkg).rglob("*.py")):
            files[f"cartolex/{path.relative_to(root).as_posix()}"] = path.read_bytes()
    files["cartolex/_data/__init__.py"] = (root / "_data" / "__init__.py").read_bytes()
    info = f"cartolex-{version}.dist-info"
    files[f"{info}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: cartolex\nVersion: {version}\n"
        "Summary: cartolex copilot kit (the engine modules the kit calls)\nLicense: MIT\n"
    ).encode()
    files[f"{info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: cartolex\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    record = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(data)}")
    record.append(f"{info}/RECORD,,")
    files[f"{info}/RECORD"] = ("\n".join(record) + "\n").encode()
    return f"cartolex-{version}-py3-none-any.whl", _zip(files)


def kit_wheel() -> tuple[str, bytes]:
    """The kit's wheel: its file name and bytes (the engine packages and the kit, Python only)."""
    import cartolex

    return _wheel(str(Path(cartolex.__file__).parent), _version())


# ── assembling ───────────────────────────────────────────────────────────────


def _zip(files: Mapping[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


def _json(doc: Any) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def _npz(**arrays: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


def _csr_arrays(name: str, M: Any) -> dict[str, np.ndarray]:
    from scipy import sparse

    M = sparse.csr_matrix(M)
    M.sort_indices()
    return {
        f"{name}_data": M.data.astype(np.float32),
        f"{name}_indices": M.indices.astype(np.int32),
        f"{name}_indptr": M.indptr.astype(np.int64),
        f"{name}_shape": np.asarray(M.shape, dtype=np.int64),
    }


def _finish(
    task: str,
    data: dict[str, bytes],
    *,
    curator_language: str,
    context: Mapping[str, Any],
    counts: Mapping[str, Any],
    contains: Sequence[str],
    never: Sequence[str],
    parts: int = 1,
) -> tuple[bytes, dict[str, Any]]:
    from cartolex.copilot import guide
    from cartolex.copilot.bundle import FORMAT, file_hash

    wheel_name, wheel = kit_wheel()
    files = dict(data)
    files[f"setup/{wheel_name}"] = wheel
    files["setup/bootstrap.py"] = (
        Path(__file__).parent.parent / "copilot" / "bootstrap.py"
    ).read_bytes()
    files["result/README.txt"] = b"The assistant writes result.json here (see README_FIRST.md).\n"
    manifest = {
        "format": FORMAT,
        "task": task,
        "parts": int(parts),
        "id": secrets.token_hex(8),
        "made_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cartolex": _version(),
        "curator_language": curator_language,
        "reference_language": context.get("reference_language", "en"),
        "counts": dict(counts),
        "contains": list(contains),
        "never": list(never),
    }
    files["README_FIRST.md"] = guide.readme(task, manifest, context).encode("utf-8")
    files["GUIDE.md"] = guide.guide(task, manifest, context).encode("utf-8")
    if task == "triage":
        files["TRIAGE_RULES.md"] = guide.triage_rules(context).encode("utf-8")
    files["PRIVACY.md"] = guide.privacy(contains, never).encode("utf-8")
    manifest["files"] = {name: file_hash(b) for name, b in sorted(files.items())}
    files["bundle.json"] = _json(manifest)
    order = ["README_FIRST.md", "GUIDE.md", "TRIAGE_RULES.md", "PRIVACY.md", "bundle.json"]
    order = [k for k in order if k in files]
    ordered = {k: files[k] for k in order} | {
        k: v for k, v in sorted(files.items()) if k not in order
    }
    return _zip(ordered), manifest


def _without(doc: Mapping[str, Any], drop: set[str]) -> dict[str, Any]:
    out = json.loads(json.dumps(doc))
    for key in ("keywords", "attribution", "set_aside", "review"):
        out[key] = {k: v for k, v in (out.get(key) or {}).items() if k not in drop}
    out["saved"] = None
    return out


def themes_bundle(
    *,
    tree: Mapping[str, Any],
    draft: Mapping[str, Any] | None,
    terms: Sequence[str],
    X: Any,
    U: Any,
    Z_terms: np.ndarray,
    Z_people: np.ndarray,
    context: Mapping[str, Any],
    curator_language: str,
    mask: NameMask,
    levels: tuple[float, Sequence[Any]] | None = None,
    texts: Any = None,
) -> tuple[bytes, dict[str, Any]]:
    """The bundle of a theme tree: its zip and manifest.

    *terms* are the space's keywords in the columns of *X* (the lexical
    matrix) and *U* (the usage), in the rows of *Z_terms*; the rows of *X*,
    *U* and *Z_people* are the people, shuffled here. *levels* (θ and the
    :class:`cartolex.lexicon.theme_comb.LevelSuggestion` of the tree) go into
    ``baseline/levels.json``: the comb read on the tree. *texts* (texts ×
    *terms*, which keywords each text uses) goes into ``data/text_keywords.npz``,
    its rows shuffled and binary: the kit reads the comb again on the tree it
    changes; no text, no identifier.
    """
    from scipy import sparse

    from cartolex.copilot import measures

    drop = {t for t in terms if mask.holds_name(t)}
    keep = np.array([i for i, t in enumerate(terms) if t not in drop], dtype=np.int64)
    kept_terms = [terms[i] for i in keep]
    X = sparse.csr_matrix(X)[:, keep]
    U = sparse.csr_matrix(U)[:, keep]
    Zt = np.asarray(Z_terms, dtype=float)[keep]
    order = np.random.default_rng(secrets.randbits(64)).permutation(X.shape[0])
    X, U, Zp = X[order], U[order], np.asarray(Z_people, dtype=float)[order]
    people = np.asarray((U > 0).sum(axis=0)).ravel()
    totals = np.asarray(U.sum(axis=1)).ravel()
    scale = np.divide(1.0, totals, out=np.zeros_like(totals, dtype=float), where=totals > 0)
    weight = np.asarray(U.multiply(scale[:, None]).sum(axis=0)).ravel()
    usage = [[int(n), round(float(w), 4)] for n, w in zip(people, weight, strict=True)]
    vocab = set(kept_terms)

    def outside(doc: Mapping[str, Any]) -> set[str]:
        # A keyword of the tree the space does not hold (a tree not yet rebased) stays out.
        return (set(doc.get("keywords") or {}) | set(doc.get("set_aside") or {})) - vocab

    work = _without(tree, outside(tree))
    proposal = _without(draft, outside(draft)) if draft is not None else None
    ctx = dict(context)
    data = {
        "data/context.json": _json(ctx),
        "data/keywords.json": _json({"terms": kept_terms, "usage": usage}),
        "data/vectors.npz": _npz(
            Z_terms=Zt.astype(np.float32),
            Z_people=Zp.astype(np.float32),
            **_csr_arrays("X", X),
            **_csr_arrays("U", U),
        ),
        "data/tree.json": _json(work),
    }
    if proposal is not None:
        data["data/draft.json"] = _json(proposal)
    if texts is not None:
        D = sparse.csr_matrix(texts)[:, keep]
        D = D[np.random.default_rng(secrets.randbits(64)).permutation(D.shape[0])]
        D = (D > 0).astype(np.float32)
        data["data/text_keywords.npz"] = _npz(**_csr_arrays("D", D))
    Zt32 = Zt.astype(np.float32).astype(float)
    base = {"tree": measures.summary(work, kept_terms, Zt32)}
    if proposal is not None:
        base["draft"] = measures.summary(proposal, kept_terms, Zt32)
    data["baseline/measures.json"] = _json(base)
    if levels is not None:
        theta, found = levels
        placed = set(work["keywords"])
        data["baseline/levels.json"] = _json(
            {
                "measure": "the comb: a keyword's texts placed by their other keywords; the lowest "
                "node holding a share theta of its use (above what any keyword gives the node)",
                "theta": theta,
                "items": [
                    {
                        "keyword": x.keyword,
                        "node": x.node,
                        "to": x.to,
                        "share": x.share,
                        "texts": x.texts,
                    }
                    for x in found
                    if x.keyword in placed
                ],
            }
        )
    counts = {
        "levels": int(work["depth"]),
        "nodes": len(work["nodes"]),
        "keywords": len(work["keywords"]),
        "set_aside": len(work.get("set_aside") or {}),
        "people": int(X.shape[0]),
        "left_out": len(drop),
    }
    return _finish(
        "themes",
        data,
        curator_language=curator_language,
        context=ctx,
        counts=counts,
        contains=THEMES_CONTAINS,
        never=THEMES_NEVER,
    )


def triage_bundle(
    *,
    items: Sequence[Mapping[str, Any]],
    users: Mapping[tuple[str, str], Any],
    n_people: int,
    context: Mapping[str, Any],
    curator_language: str,
    mask: NameMask,
    usage: Mapping[tuple[str, str], Sequence[str]] | None = None,
    parts: int = 1,
) -> tuple[bytes, dict[str, Any]]:
    """The bundle of candidate keywords: its zip and manifest.

    *items* carry ``term``, ``lang``, ``band``, ``reason``, ``people``,
    ``texts``, ``specificity``, ``forms``, ``inside`` and ``current`` (the
    decision so far); *users* maps (term, language) to the indices of the people
    who use it (the extraction's ``term_people.npz``), shuffled here. *usage*
    (optional, already masked) gives each candidate's usage lines. *parts*: how many
    parts the kit cuts the candidates into, by theme (one conversation each). The
    curator's standing rules travel in *context* (``standing_rules``).
    """
    from scipy import sparse

    kept = [dict(it) for it in items if not mask.holds_name(it["term"])]
    for it in kept:
        it["forms"] = [f for f in it.get("forms") or [] if not mask.holds_name(f)]
        it["inside"] = [f for f in it.get("inside") or [] if not mask.holds_name(f)]
        it["usage"] = list((usage or {}).get((it["term"], it["lang"]), []))
    order = np.random.default_rng(secrets.randbits(64)).permutation(max(int(n_people), 1))
    rows, cols = [], []
    for i, it in enumerate(kept):
        idx = users.get((it["term"], it["lang"]))
        if idx is not None and len(idx):
            rows.extend([i] * len(idx))
            cols.extend(order[np.asarray(idx, dtype=np.int64)].tolist())
    P = sparse.csr_matrix(
        (np.ones(len(cols), dtype=np.float32), (rows, cols)), shape=(len(kept), len(order))
    )
    data = {
        "data/context.json": _json(dict(context)),
        "data/terms.json": _json({"items": [{"id": i, **it} for i, it in enumerate(kept)]}),
        "data/term_people.npz": _npz(**_csr_arrays("P", P)),
    }
    bands: dict[str, int] = {}
    decided: dict[str, int] = {}
    for it in kept:
        bands[it["band"]] = bands.get(it["band"], 0) + 1
        if it.get("current"):
            decided[it["current"]] = decided.get(it["current"], 0) + 1
    data["baseline/measures.json"] = _json({"bands": bands, "decided": decided})
    counts = {
        "terms": len(kept),
        "people": len(order),
        "left_out": len(items) - len(kept),
        "usage_lines": bool(usage),
    }
    contains = TRIAGE_CONTAINS + ((TRIAGE_USAGE,) if usage else ())
    never = TRIAGE_NEVER if usage else ("texts or excerpts of texts", *TRIAGE_NEVER[1:])
    return _finish(
        "triage",
        data,
        curator_language=curator_language,
        context=context,
        counts=counts,
        contains=contains,
        never=never,
        parts=max(1, int(parts)),
    )


def roster_names(folder: Path) -> list[tuple[str, str]]:
    """The first and last names of every ``people.csv`` and ``index.csv`` under an assembled corpus."""
    out: set[tuple[str, str]] = set()
    for name in ("people.csv", "index.csv"):
        for path in sorted(Path(folder).rglob(name)):
            with open(path, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    first, last = (
                        (r.get("first_name") or "").strip(),
                        (r.get("last_name") or "").strip(),
                    )
                    if first or last:
                        out.add((first, last))
    return sorted(out)


def corpus_texts(folder: Path) -> Iterable[str]:
    """The texts of an assembled corpus (every slot's ``texts.parquet``, or ``texts/*.txt``
    for a corpus written one file per text), a block at a time."""
    import pyarrow.parquet as pq

    for slot in sorted(p for p in Path(folder).iterdir() if p.is_dir()):
        packed = slot / "texts.parquet"
        if packed.exists():
            pf = pq.ParquetFile(packed)
            try:
                for batch in pf.iter_batches(batch_size=2000, columns=["text"]):
                    yield from (t or "" for t in batch.column(0).to_pylist())
            finally:
                pf.close()
            continue
        for path in sorted(slot.glob("texts/*.txt")):
            yield path.read_text(encoding="utf-8", errors="replace")
