# SPDX-License-Identifier: MIT
"""Reference runner: every engine stage on one workspace, normalised outputs.

Runs the whole engine in process on a copy of a corpus-contract workspace —
extraction, AI triage answered by a deterministic fake model, consolidation,
person roster, SVD space, term groups, 2-D layout, subfield draft and apply,
trajectories, plots (run only), projection of new documents and the portable
bundle — and writes the outputs in the ``cartolex-reference/1`` format: one
folder per stage, neutral names, canonical serialisation, and a
``manifest.json`` holding the sha256 of every artifact's full-precision
canonical form. With ``--merge-with`` it instead runs both workspaces up to the
bundle (each in its own process) and writes the map-merge stage.

Usage::

    python run.py --workspace WS [--truth TRUTH.json] [--project PROJECT] --out DIR
    python run.py --workspace WS --merge-with WS2 [--truth T] [--merge-truth T2]
                  [--project P --merge-project P2] --out DIR

With ``--project`` (the same world written as a cartolex project), the stages
run through a project build instead of being called on the workspace, and the
artifacts are read where the build put them: they must be the same.

Self-contained on purpose: it imports only the standard library, the installed
engine and the engine's dependencies, never this repository. Only the part
between ``ENGINE ADAPTER: BEGIN`` and ``ENGINE ADAPTER: END`` knows the
engine's API; when the API changes, that part alone is edited. It drives the
engine of this tree; the reference itself was made with the runner frozen with
the release that produced it (see ``docs/dev/reference.md``).
"""

from __future__ import annotations

import os
import sys

# ── Fixed run settings (applied before numpy/numba load) ─────────────────────

FIXED_ENV = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMBA_NUM_THREADS": "1",
    "PYTHONHASHSEED": "0",
    "TZ": "UTC",
    "LC_ALL": "C.UTF-8",
}


def _ensure_fixed_settings() -> None:
    """Re-execute this script once with the fixed settings when any is missing.

    ``PYTHONHASHSEED`` only takes effect at interpreter start and the thread
    counts only when the numeric libraries load, so they cannot be set from
    inside a running process: re-executing is the reliable way.
    """
    if all(os.environ.get(k) == v for k, v in FIXED_ENV.items()):
        return
    env = dict(os.environ)
    env.update(FIXED_ENV)
    env.setdefault("MPLBACKEND", "Agg")
    os.execve(sys.executable, [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]], env)


if __name__ == "__main__":
    _ensure_fixed_settings()

import argparse  # noqa: E402
import contextlib  # noqa: E402
import csv  # noqa: E402
import gzip  # noqa: E402
import hashlib  # noqa: E402
import importlib.metadata  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import math  # noqa: E402
import platform  # noqa: E402
import resource  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import unicodedata  # noqa: E402
import warnings  # noqa: E402
import zlib  # noqa: E402
from collections.abc import Callable  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

FORMAT = "cartolex-reference/1"
#: The "current year" every date-dependent stage is pinned to.
NOW_YEAR = 2026
#: Order of the stage folders in the manifest (and of a full run).
STAGES = (
    "extract",
    "triage",
    "build",
    "roster",
    "space",
    "group",
    "layout",
    "draft",
    "apply",
    "trajectories",
    "projection",
    "bundle",
)
MERGE_STAGES = ("merge",)
#: Above these sizes an artifact is stored gzip-compressed, and an array as
#: float32; the manifest hash is always of the full-precision form.
GZIP_ABOVE_BYTES = 32 * 1024
FLOAT32_ABOVE_ELEMENTS = 20_000

log = logging.getLogger("reference")


# ── Network: blocked for the whole run ───────────────────────────────────────


def block_network() -> None:
    """Make every network connection fail (Unix sockets stay usable)."""

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("network access is blocked during a reference run")

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _connect(self: socket.socket, address: Any) -> Any:
        if getattr(socket, "AF_UNIX", None) is not None and self.family == socket.AF_UNIX:
            return real_connect(self, address)
        return _refuse()

    def _connect_ex(self: socket.socket, address: Any) -> Any:
        if getattr(socket, "AF_UNIX", None) is not None and self.family == socket.AF_UNIX:
            return real_connect_ex(self, address)
        return _refuse()

    socket.socket.connect = _connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _connect_ex  # type: ignore[method-assign]
    socket.create_connection = _refuse  # type: ignore[assignment]
    socket.getaddrinfo = _refuse  # type: ignore[assignment]


# ── Canonical artifacts ──────────────────────────────────────────────────────
#
# Every stage output is one of these kinds. ``sha256`` is always computed over
# the canonical full-precision form; what is stored may be compressed or
# narrowed (float32) to keep the committed reference small.


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _float_text(x: float) -> str:
    """Shortest round-trip text of a float (``repr``), stable across platforms."""
    return repr(float(x))


def _canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=True
    ).encode("utf-8")


def _plain(obj: Any) -> Any:
    """Recursively turn numpy scalars/arrays and tuples into plain JSON values."""
    import numpy as np

    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_plain(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [_plain(v) for v in obj.tolist()]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, set | frozenset):
        return sorted(_plain(v) for v in obj)
    return obj


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _maybe_gzip(path_base: Path, suffix: str, data: bytes) -> tuple[Path, bool]:
    if len(data) > GZIP_ABOVE_BYTES:
        path = path_base.with_name(path_base.name + suffix + ".gz")
        _write_bytes(path, gzip.compress(data, compresslevel=9, mtime=0))
        return path, True
    path = path_base.with_name(path_base.name + suffix)
    _write_bytes(path, data)
    return path, False


@dataclass
class Table:
    """A table: named columns, rows either in meaningful order or canonically sorted.

    ``keys`` name the columns identifying a row (used to align rows when two
    runs are compared); ``ordered`` says whether row order is itself an output.
    """

    frame: Any  # pandas.DataFrame
    keys: list[str]
    ordered: bool = False

    kind = "table"

    def _prepared(self) -> tuple[Any, dict[str, str]]:
        import pandas as pd

        df = self.frame.copy().reset_index(drop=True)
        dtypes: dict[str, str] = {}
        for col in df.columns:
            s = df[col]
            if pd.api.types.is_bool_dtype(s):
                dtypes[col] = "bool"
            elif pd.api.types.is_integer_dtype(s):
                dtypes[col] = "int"
            elif pd.api.types.is_float_dtype(s):
                dtypes[col] = "float"
            else:
                dtypes[col] = "str"
        if not self.ordered and len(df):
            by = list(self.keys) + [c for c in df.columns if c not in self.keys]
            df = df.sort_values(by=by, kind="mergesort", na_position="last").reset_index(drop=True)
        return df, dtypes

    def canonical(self) -> tuple[bytes, dict[str, Any]]:
        """Canonical CSV text (full precision) and the manifest metadata."""
        import pandas as pd

        df, dtypes = self._prepared()
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(list(df.columns))
        cols = list(df.columns)
        for row in df.itertuples(index=False, name=None):
            out = []
            for col, v in zip(cols, row, strict=True):
                t = dtypes[col]
                if v is None or (not isinstance(v, str) and pd.isna(v)):
                    out.append("nan" if t == "float" else "")
                elif t == "float":
                    out.append(_float_text(v))
                elif t == "int":
                    out.append(str(int(v)))
                elif t == "bool":
                    out.append("true" if bool(v) else "false")
                else:
                    out.append(str(v))
            writer.writerow(out)
        meta = {
            "rows": int(len(df)),
            "columns": cols,
            "dtypes": dtypes,
            "keys": list(self.keys),
            "ordered": bool(self.ordered),
        }
        return buf.getvalue().encode("utf-8"), meta

    def store(self, path_base: Path) -> tuple[str, dict[str, Any]]:
        data, meta = self.canonical()
        path, _ = _maybe_gzip(path_base, ".csv", data)
        return path.name, {**meta, "sha256": _sha256(data)}


@dataclass
class Document:
    """A JSON document; dictionaries are key-sorted, list order is kept."""

    obj: Any
    kind = "json"

    def store(self, path_base: Path) -> tuple[str, dict[str, Any]]:
        plain = _plain(self.obj)
        canonical = _canonical_json(plain)
        pretty = (
            json.dumps(plain, sort_keys=True, ensure_ascii=False, indent=1, allow_nan=True) + "\n"
        ).encode("utf-8")
        path, _ = _maybe_gzip(path_base, ".json", pretty)
        return path.name, {"sha256": _sha256(canonical)}


@dataclass
class Strings:
    """An ordered list of strings (for example a vocabulary whose order matters)."""

    items: list[str]
    kind = "strings"

    def store(self, path_base: Path) -> tuple[str, dict[str, Any]]:
        items = [str(s) for s in self.items]
        canonical = _canonical_json(items)
        pretty = (json.dumps(items, ensure_ascii=False, indent=0) + "\n").encode("utf-8")
        path, _ = _maybe_gzip(path_base, ".json", pretty)
        return path.name, {"sha256": _sha256(canonical), "count": len(items)}


@dataclass
class Array:
    """A numeric array with a comparison role.

    ``role`` tells the comparison how to read it: ``values`` (element-wise),
    ``spectrum`` (singular values), ``embedding`` (rows embedded in a
    latent space, compared up to column signs and by subspace angles) or
    ``layout`` (2-D map coordinates, compared by Procrustes and neighbours).
    """

    values: Any  # numpy array
    role: str = "values"
    kind = "array"

    def store(self, path_base: Path) -> tuple[str, dict[str, Any]]:
        import numpy as np

        arr = np.ascontiguousarray(np.asarray(self.values, dtype=np.float64))
        header = f"float64|{list(arr.shape)}|".encode()
        digest = _sha256(header + arr.astype("<f8").tobytes())
        stored = arr.astype(np.float32) if arr.size > FLOAT32_ABOVE_ELEMENTS else arr
        buf = io.BytesIO()
        np.save(buf, stored, allow_pickle=False)
        path, _ = _maybe_gzip(path_base, ".npy", buf.getvalue())
        return path.name, {
            "sha256": digest,
            "shape": list(arr.shape),
            "role": self.role,
            "stored_dtype": str(stored.dtype),
        }


@dataclass
class Labels:
    """Integer group labels aligned with an ordered item list (compared by ARI)."""

    values: Any
    kind = "labels"

    def store(self, path_base: Path) -> tuple[str, dict[str, Any]]:
        labels = [int(v) for v in self.values]
        canonical = _canonical_json(labels)
        path = path_base.with_name(path_base.name + ".json")
        _write_bytes(path, (json.dumps(labels) + "\n").encode("utf-8"))
        return path.name, {"sha256": _sha256(canonical), "count": len(labels)}


@dataclass
class HashOnly:
    """An artifact recorded by hash only: a redundant copy of stored data.

    Its canonical form is the one of *inner*; nothing is stored, so a changed
    hash is reported without a detailed diff.
    """

    inner: Table | Document | Strings | Array | Labels
    kind = "hash"

    def store(self, path_base: Path) -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            _, meta = self.inner.store(Path(tmp) / "x")
        return "", {"sha256": meta["sha256"], "of": self.inner.kind}


Artifact = Table | Document | Strings | Array | Labels | HashOnly


def write_stage(out_dir: Path, stage: str, artifacts: dict[str, Artifact]) -> dict[str, Any]:
    """Write one stage's artifacts under ``out_dir/stage`` and return their manifest entries."""
    entries: dict[str, Any] = {}
    stage_dir = out_dir / stage
    for name in sorted(artifacts):
        art = artifacts[name]
        file_name, meta = art.store(stage_dir / name)
        entry = {"kind": art.kind, **meta}
        if file_name:
            entry["file"] = f"{stage}/{file_name}"
        entries[name] = entry
    return entries


# ── Neutral names ────────────────────────────────────────────────────────────


def rename_keys(obj: Any, mapping: dict[str, str]) -> Any:
    """Recursively rename dictionary keys through *mapping* (values untouched)."""
    if isinstance(obj, dict):
        return {mapping.get(k, k): rename_keys(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rename_keys(v, mapping) for v in obj]
    return obj


def opaque_id(key: str, prefix: str = "p") -> str:
    """Short stable opaque identifier of an internal key."""
    return f"{prefix}{hashlib.sha256(key.encode('utf-8')).hexdigest()[:10]}"


def person_id(last: Any, first: Any, group: Any) -> str:
    """Opaque person identifier from the raw identity fields of the corpus index.

    Stored outputs never carry names: invented names can still collide with
    words a repository must not contain, and identities stay comparable.
    """
    return opaque_id("\x1f".join(str(v).strip() for v in (last, first, group)), "p")


def group_id(group: Any) -> str:
    """Opaque group identifier from the raw group label of the corpus index."""
    return opaque_id(str(group).strip(), "g")


# ── Truth vocabulary and the fake model's judgement (engine independent) ────


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).lower().split())


@dataclass(frozen=True)
class Verdict:
    """The fake model's judgement of one term."""

    accept: bool
    lang: str
    canonical: str
    code: str  # an accept code (C/M/O) or a reject code (N/K/G/F)


_FRENCH_HINTS = frozenset(
    "le la les des du de et en au aux une un sur pour dans par avec sans entre".split()
)


def _stable_pick(text: str, options: str) -> str:
    return options[zlib.crc32(text.encode("utf-8")) % len(options)]


def _guess_lang(term: str) -> str:
    if any(ord(c) > 127 for c in term) or set(term.split()) & _FRENCH_HINTS:
        return "fr"
    return "en"


def load_truth(path: Path | None) -> dict[str, tuple[str, str]] | None:
    """Theme vocabulary of a truth file: folded surface form → (canonical English, language).

    Understood layouts: ``{"themes": {name: [{"en": ..., "fr": ...}, ...]}}``
    and ``{"themes": [{"terms": [{"canonical": ..., "forms": {lang: form}}]}]}``.
    Returns ``None`` without a truth file.
    """
    if path is None:
        return None
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    vocab: dict[str, tuple[str, str]] = {}

    def add(canonical: str, forms: dict[str, str]) -> None:
        for lang, form in sorted(forms.items()):
            if isinstance(form, str) and form.strip():
                vocab.setdefault(_fold(form), (_fold(canonical), lang))

    themes = doc.get("themes", {})
    groups = themes.values() if isinstance(themes, dict) else themes
    for group in groups:
        entries = group.get("terms", []) if isinstance(group, dict) else group
        for entry in entries:
            if "forms" in entry:
                add(entry.get("canonical") or entry["forms"].get("en", ""), entry["forms"])
            else:
                add(entry.get("en", ""), {k: v for k, v in entry.items() if len(k) == 2})
    return vocab


def make_judge(vocab: dict[str, tuple[str, str]] | None) -> Callable[[str], Verdict]:
    """The deterministic rule the fake model answers with.

    With a truth vocabulary: a term that is one of the theme forms is accepted
    with its canonical English form; anything else is rejected. Without one: a
    term of two or more words (or a long single word) is accepted as itself.
    Accept and reject codes are spread by a stable hash so every code occurs.
    """

    def judge(term: str) -> Verdict:
        folded = _fold(term)
        if vocab is not None:
            hit = vocab.get(folded)
            if hit is not None:
                canonical, lang = hit
                return Verdict(True, lang, canonical, _stable_pick(canonical, "CMO"))
            return Verdict(False, "en", folded, _stable_pick(folded, "GFKN"))
        words = folded.split()
        if (len(words) >= 2 or len(folded) >= 9) and not any(w.isdigit() for w in words):
            return Verdict(True, _guess_lang(folded), folded, _stable_pick(folded, "CMO"))
        return Verdict(False, "en", folded, _stable_pick(folded, "GF"))

    return judge


# ── A deterministic offline text embedder (for the merge reconciliation) ────


def hashing_embedder(texts: list[str]) -> Any:
    """Character-trigram hashing vectors (256 dims, L2-normalised): offline and deterministic."""
    import numpy as np

    dim = 256
    out = np.zeros((len(texts), dim))
    for i, text in enumerate(texts):
        s = f" {_fold(text)} "
        for j in range(len(s) - 2):
            out[i, zlib.crc32(s[j : j + 3].encode("utf-8")) % dim] += 1.0
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(norms, 1e-12)


HASHING_EMBEDDER_NAME = "char-trigram-crc32-256"


# ════════════════════════════════════════════════════════════════════════════
# ENGINE ADAPTER: BEGIN
#
# The only part of this file that knows the engine: its entry points, its
# settings object, its workspace file layout and its column names. Each stage
# function runs the engine and returns neutral artifacts. When the engine's API
# changes, edit this part and nothing else.
# ════════════════════════════════════════════════════════════════════════════

# Engine settings of the demo runs, copied verbatim from tools/demo_stats.py.
# Only departures from the engine's defaults are listed, so that a change of a
# default shows up in a reference comparison. The parameter names are the
# engine's own.
ENGINE_SETTINGS: dict[str, dict] = {
    # RunContext.for_workspace(<workspace>, KeywordsConfig(**ENGINE_SETTINGS["keywords"]))
    "keywords": {
        # The demo corpus sits in the engine's default corpus slot ("manual") alone.
        # Works span 2012-2026: use the whole history, not the last five years.
        "kw_recency_years": 0,
    },
    # the atlas driver's run_svd(**...), run_clustering(**...), run_umap(**...)
    "svd": {},
    "clustering": {},
    "umap": {},
}
#: The runner's own additions for the AI triage stage: one call at a time (files
#: written in a fixed order) and the domain label the prompts and cache keys use.
TRIAGE_SETTINGS: dict[str, Any] = {
    "llm_max_concurrent": 1,
    "domain_title": "Coastal and marine systems",
}
#: Engine output keys and columns → neutral names.
NEUTRAL_KEYS = {
    "domain_title": "domain_name",
    "cohort_id": "domain_id",
    "cohorts": "domains",
    "n_cohorts": "n_domains",
    "n_multi_cohort_groups": "n_multi_domain_groups",
    "cohort_a": "domain_a",
    "cohort_b": "domain_b",
    "member_researcher_ids": "member_person_ids",
    "researcher_id": "person",
    "researcher_ids": "person_ids",
    "unit": "group",
    "len": "n_tokens",
    "kw_recency_years": "recency_years",
    "top_n_researcher": "top_n_person",
    "top_n_unit": "top_n_group",
}


#: Package of each engine part.
_ROOTS = {"lexicon": "cartolex.lexicon", "atlas": "cartolex.atlas"}


def _mod(name: str) -> Any:
    """Import an engine module by its part-relative name (``"atlas.map_merge"``)."""
    import importlib

    part, _, rest = name.partition(".")
    return importlib.import_module(_ROOTS[part] + (f".{rest}" if rest else ""))


def engine_version() -> str:
    """Installed engine version."""
    try:
        return importlib.metadata.version("cartolex")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def engine_fingerprint() -> str:
    """sha256 over the installed engine's source files and shipped data files."""
    import importlib

    h = hashlib.sha256()
    for pkg in _ROOTS.values():
        root = Path(importlib.import_module(pkg).__file__).resolve().parent
        for path in sorted(root.rglob("*.py")):
            h.update(f"{pkg}/{path.relative_to(root).as_posix()}\0".encode())
            h.update(path.read_bytes())
    context = Path(importlib.import_module("cartolex.context").__file__)
    h.update(b"cartolex/context.py\0")
    h.update(context.read_bytes())
    root = Path(importlib.import_module("cartolex._data").__file__).resolve().parent
    folders = [
        (p.relative_to(root).as_posix(), p)
        for p in sorted(d for d in root.iterdir() if d.is_dir() and d.name != "__pycache__")
    ]
    for label, folder in folders:
        for path in sorted(p for p in Path(folder).glob("*") if p.is_file()):
            h.update(f"{label}/{path.name}\0".encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def _neutral_frame(df: Any) -> Any:
    return df.rename(columns={c: NEUTRAL_KEYS.get(c, c) for c in df.columns})


def _person_key(last: Any, first: Any, group: Any) -> str:
    make_key = _mod("lexicon.utils").make_researcher_id

    return make_key(str(last), str(first), str(group))


def _read_csv(path: Path, **kwargs: Any) -> Any:
    import pandas as pd

    return pd.read_csv(path, **kwargs)


class FakeModelClient:
    """Stands in for the provider SDK client: answers the typed triage prompt.

    It reads the JSON list of terms in the user message and answers one line
    per term in the format the typed triage prompt asks for (``C <lang>
    <term>=<canonical>`` for an accept, ``<code> <term>`` for a reject).
    """

    calls = 0

    def __init__(self, judge: Callable[[str], Verdict], **_: Any) -> None:
        self._judge = judge
        self.chat = self

    def complete(self, *, model: str, messages: list[dict], temperature: float, **_: Any) -> Any:
        from types import SimpleNamespace

        FakeModelClient.calls += 1
        terms = json.loads(messages[-1]["content"])
        lines = []
        for term in terms:
            v = self._judge(term)
            lines.append(
                f"{v.code} {v.lang} {term}={v.canonical}" if v.accept else f"{v.code} {term}"
            )
        content = "\n".join(lines)
        message = SimpleNamespace(content=content, model_dump=lambda: {"content": content})
        usage = SimpleNamespace(
            prompt_tokens=sum(len(m["content"]) for m in messages) // 4,
            completion_tokens=len(content) // 4,
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def install_fake_model(judge: Callable[[str], Verdict]) -> None:
    """Route the engine's provider SDK import to :class:`FakeModelClient`."""
    import types

    module = types.ModuleType("mistralai")

    def _client(**kwargs: Any) -> FakeModelClient:
        return FakeModelClient(judge, **kwargs)

    module.Mistral = _client  # type: ignore[attr-defined]
    sys.modules["mistralai"] = module


class Engine:
    """Runs the engine stages on one workspace copy and returns neutral artifacts.

    Every stage takes an explicit run context, whose current year is pinned
    (every date window uses it); the draft is applied unchanged (without a
    curated document, the apply stage reads the draft). ``self.files`` names
    the files read (the context's paths).
    """

    def __init__(self, ws: Path, *, judge: Callable[[str], Verdict], domain_id: str) -> None:
        self.ws = ws
        self.domain_id = domain_id
        self.judge = judge
        self.cfg: Any = None
        self.files: Any = None
        self._setup()

    # ── the engine's API ──

    def _setup(self) -> None:
        from cartolex.context import RunContext

        settings = _mod("lexicon").KeywordsConfig(**ENGINE_SETTINGS["keywords"], **TRIAGE_SETTINGS)
        self.ctx = RunContext.for_workspace(self.ws, settings, now_year=NOW_YEAR)
        self.files = self.ctx.paths
        self.cfg = self.ctx.settings

    def _run_extract(self) -> None:
        _mod("lexicon").run_pipeline_stage_1(self.ctx)

    def _run_triage(self) -> Any:
        stage = _mod("lexicon.llm_triage").run_pipeline_stage_2_llm
        return stage(self.ctx, api_key="reference-fake-key")

    def _run_build(self) -> None:
        _mod("lexicon").run_pipeline_stage_3(self.ctx)

    def _run_roster(self) -> int:
        return _mod("lexicon.io_helpers").build_researcher_index(self.ctx)

    def _run_space(self) -> None:
        _mod("atlas.driver").run_svd(self.ctx, **ENGINE_SETTINGS["svd"])

    def _run_group(self) -> None:
        _mod("atlas.driver").run_clustering(self.ctx, **ENGINE_SETTINGS["clustering"])

    def _run_layout(self) -> None:
        _mod("atlas.driver").run_umap(self.ctx, **ENGINE_SETTINGS["umap"])

    def _run_draft(self) -> None:
        _mod("lexicon.subfields").draft_subfields(self.ctx)

    def _run_apply(self) -> Any:
        return _mod("lexicon.subfields").apply_subfields(self.ctx)

    def _run_trajectories(self) -> None:
        _mod("atlas.driver").run_trajectories(self.ctx)

    def _run_plots(self) -> None:
        _mod("atlas.driver").run_lexical_plots(self.ctx)

    def _overlay_root(self) -> Path:
        """The folder of the projected sets: one folder per set, each with an index."""
        return self.ws / "overlay"

    def _positioning_models(self) -> tuple:
        return _mod("lexicon.positioning").load_positioning_models(self.ctx)

    # ── inputs ──

    def corpus_index(self) -> Path:
        """The corpus index of the workspace (corpus contract): its one corpus slot."""
        (slot,) = self.cfg.corpus_slots
        return self.files.corpus_index_csv(slot.id)

    def person_ids(self) -> dict[str, str]:
        """The engine's person key → opaque person id, for every person of the index."""
        df = _read_csv(self.corpus_index(), dtype=str, keep_default_na=False)
        return {
            _person_key(a, b, c): person_id(a, b, c)
            for a, b, c in zip(df["last_name"], df["first_name"], df["unit"], strict=True)
        }

    def neutral_persons(self, obj: Any) -> Any:
        """*obj* with every engine person key (as a key or a string value) made opaque."""
        ids = self.person_ids()

        def walk(x: Any) -> Any:
            if isinstance(x, dict):
                return {ids.get(k, k): walk(v) for k, v in x.items()}
            if isinstance(x, list):
                return [walk(v) for v in x]
            if isinstance(x, str):
                return ids.get(x, x)
            return x

        return walk(obj)

    # ── stages ──

    def extract(self) -> dict[str, Artifact]:
        f = self.files
        self._run_extract()
        out: dict[str, Artifact] = {}
        for lang in self.cfg.corpus_languages:
            df = _neutral_frame(_read_csv(f.raw_terms_csv(lang)))
            out[f"terms_{lang}"] = Table(df, keys=["term"])
        merged = _neutral_frame(_read_csv(f.global_terms_csv))
        out["terms_merged"] = Table(merged, keys=["term"])
        return out

    def triage(self) -> dict[str, Artifact]:
        f = self.files
        install_fake_model(self.judge)
        FakeModelClient.calls = 0
        first = self._run_triage()
        first_calls = FakeModelClient.calls
        # Second run: every answer must come from the engine's own caches.
        FakeModelClient.calls = 0
        second = self._run_triage()
        second_calls = FakeModelClient.calls
        decisions = json.loads(f.triage_decisions_json.read_text(encoding="utf-8"))
        batch_cache = json.loads(f.triage_batch_cache_json.read_text(encoding="utf-8"))
        term_cache = json.loads(f.triage_term_cache_json.read_text(encoding="utf-8"))
        translation = f.translation_cache_json
        calls = {
            "model_calls_first_run": first_calls,
            "model_calls_second_run": second_calls,
            "second_run_identical": _plain(first) == _plain(second),
            "batch_cache_entries": len(batch_cache),
            "term_cache_entries": len(term_cache),
        }
        return {
            "decisions": Document(decisions),
            "batch_cache": Document(batch_cache),
            "term_cache": Document(term_cache),
            "translations": Document(
                json.loads(translation.read_text(encoding="utf-8")) if translation.exists() else {}
            ),
            "calls": Document(calls),
        }

    def build(self) -> dict[str, Artifact]:
        f = self.files
        self._run_build()
        out: dict[str, Artifact] = {
            "refined": Table(
                _neutral_frame(_read_csv(f.refined_terms_csv)),
                keys=["concept", "term"],
            ),
            "pairs": Table(
                _neutral_frame(_read_csv(f.refined_pairs_csv)),
                keys=["concept"],
            ),
            "aliases": Table(_read_csv(f.term_aliases_csv), keys=["alias"]),
        }
        for lang in self.cfg.display_languages:
            df = _read_csv(f.refined_terms_lang_csv(lang))
            out[f"refined_{lang}"] = HashOnly(Table(df, keys=["concept", "term"]))
        persons = _read_csv(f.person_terms_csv, dtype={"unit": str})
        persons.insert(
            0,
            "person",
            [
                person_id(a, b, c)
                for a, b, c in zip(
                    persons["last_name"], persons["first_name"], persons["unit"], strict=True
                )
            ],
        )
        persons = persons.drop(columns=["last_name", "first_name", "unit"])
        out["person_terms"] = Table(_neutral_frame(persons), keys=["person", "term"])
        groups = _read_csv(f.group_terms_csv, dtype={"unit": str})
        groups["unit"] = [group_id(g) for g in groups["unit"]]
        out["group_terms"] = Table(_neutral_frame(groups), keys=["group", "term"])
        domain = _read_csv(f.domain_terms_csv)
        out["domain_terms"] = Table(_neutral_frame(domain), keys=["term"])
        import pandas as pd

        vec = _mod("atlas.model_files").load_vectorizer(f.vectorizer_json)
        names = [str(t) for t in vec.get_feature_names_out()]
        out["vectorizer"] = Table(
            pd.DataFrame({"term": names, "idf": [float(x) for x in vec.idf_]}), keys=["term"]
        )
        params = json.loads(f.run_settings_json.read_text(encoding="utf-8"))
        params.pop("snapshot_date", None)  # the run's calendar date
        out["settings"] = Document(rename_keys(params, NEUTRAL_KEYS))
        return out

    def roster(self) -> dict[str, Artifact]:
        roster_csv = self.files.roster_csv
        # Written once by consolidation; the roster call below rewrites it.
        before = roster_csv.read_bytes() if roster_csv.exists() else b""
        count = self._run_roster()
        df = _read_csv(roster_csv, dtype=str, keep_default_na=False)
        df.insert(
            0,
            "person",
            [
                person_id(a, b, c)
                for a, b, c in zip(df["last_name"], df["first_name"], df["unit"], strict=True)
            ],
        )
        df["unit"] = [group_id(g) for g in df["unit"]]
        df = df.drop(columns=["last_name", "first_name"])
        return {
            "persons": Table(_neutral_frame(df), keys=["person"], ordered=True),
            "summary": Document(
                {"count": int(count), "same_as_build_stage": before == roster_csv.read_bytes()}
            ),
        }

    def _lexical(self) -> tuple[Any, Any]:
        model_files = _mod("atlas.model_files")
        data = model_files.load_lexical_data(self.files.lexical_data_json)
        emb = model_files.load_embeddings(self.files.embeddings_json)
        return data, emb

    def space(self) -> dict[str, Artifact]:
        import numpy as np
        import pandas as pd

        f = self.files
        self._run_space()
        data, emb = self._lexical()
        svd = _mod("atlas.model_files").load_svd(f.svd_model_json)
        X = data.X.tocoo()
        X_tf = data.X_tf.tocsr() if getattr(data, "X_tf", None) is not None else None
        tf = np.asarray(X_tf[X.row, X.col]).ravel().astype(float) if X_tf is not None else None
        matrix = pd.DataFrame(
            {
                "row": X.row.astype(int),
                "col": X.col.astype(int),
                "score": X.data.astype(float),
                **({"tf": tf} if tf is not None else {}),
            }
        )
        pca_persons = _read_csv(f.pca_persons_csv, dtype={"unit": str})
        pca_terms = _read_csv(f.pca_terms_csv)
        return {
            "terms": Strings(list(data.terms)),
            "persons": Strings(self.neutral_persons([str(i) for i in data.individuals])),
            "matrix": Table(matrix, keys=["row", "col"]),
            "singular_values": Array(np.asarray(svd.singular_values_), role="spectrum"),
            "explained_variance_ratio": Array(np.asarray(svd.explained_variance_ratio_)),
            "person_coords": Array(emb.Z_ind, role="embedding"),
            "term_coords": Array(emb.Z_terms, role="embedding"),
            "person_table": HashOnly(Table(pca_persons, keys=[], ordered=True)),
            "term_table": HashOnly(Table(pca_terms, keys=[], ordered=True)),
        }

    def group(self) -> dict[str, Artifact]:
        import numpy as np

        f = self.files
        self._run_group()
        data, _ = self._lexical()
        clustered = _read_csv(f.terms_clustered_csv)
        by_term = dict(
            zip(clustered["term"].astype(str), clustered["cluster"].astype(int), strict=True)
        )
        score = dict(
            zip(clustered["term"].astype(str), clustered["global_score"].astype(float), strict=True)
        )
        terms = [str(t) for t in data.terms]
        clusters = _read_csv(f.clusters_csv)
        proto = json.loads(f.proto_subfields_json.read_text(encoding="utf-8"))
        return {
            "term_labels": Labels([by_term[t] for t in terms]),
            "term_scores": Array(np.array([score[t] for t in terms])),
            "clusters": Table(clusters, keys=["cluster"]),
            "proto_subfields": Document(rename_keys(proto, NEUTRAL_KEYS)),
        }

    def layout(self) -> dict[str, Artifact]:
        f = self.files
        self._run_layout()
        _, emb = self._lexical()
        groups = _read_csv(f.layout_groups_csv, dtype={"unit": str})
        if "unit" in groups.columns:
            groups["unit"] = [group_id(g) for g in groups["unit"]]
        diagnostics = json.loads(f.layout_diagnostics_json.read_text(encoding="utf-8"))
        return {
            "person_xy": Array(emb.umap_ind, role="layout"),
            "term_xy": Array(emb.umap_terms, role="layout"),
            "groups": Table(_neutral_frame(groups), keys=[], ordered=True),
            "diagnostics": Document(diagnostics),
            "person_table": HashOnly(Table(_read_csv(f.layout_persons_csv), keys=[], ordered=True)),
            "term_table": HashOnly(Table(_read_csv(f.layout_terms_csv), keys=[], ordered=True)),
            "clustered_table": HashOnly(
                Table(_read_csv(f.terms_clustered_csv), keys=[], ordered=True)
            ),
        }

    def draft(self) -> dict[str, Artifact]:
        self._run_draft()
        doc = json.loads(self.files.subfields_draft_json.read_text(encoding="utf-8"))
        return {"draft": Document(self.neutral_persons(rename_keys(doc, NEUTRAL_KEYS)))}

    def apply(self) -> dict[str, Artifact]:
        f = self.files
        applied = self._run_apply()
        written = json.loads(f.subfields_json.read_text(encoding="utf-8"))
        dtype = {"researcher_id": str}
        weights = _neutral_frame(_read_csv(f.subfield_weights_csv, dtype=dtype))
        ids = self.person_ids()
        weights["person"] = [ids[k] for k in weights["person"]]
        lexicon = _read_csv(f.lexicon_weights_csv)
        return {
            "applied": Document(self.neutral_persons(rename_keys(written, NEUTRAL_KEYS))),
            "returned_matches_file": Document({"value": _plain(applied) == written}),
            "person_weights": Table(weights, keys=["person", "subfield_id"]),
            "lexicon_weights": Table(lexicon, keys=["term_index"]),
        }

    def trajectories(self) -> dict[str, Artifact]:
        import pandas as pd

        f = self.files
        self._run_trajectories()
        points_csv = f.trajectories_csv
        windows_json = f.trajectory_windows_json
        points = (
            _read_csv(points_csv, dtype={"unit": str}) if points_csv.exists() else pd.DataFrame()
        )
        if len(points):
            points["researcher_id"] = [
                person_id(a, b, c)
                for a, b, c in zip(
                    points["last_name"],
                    points["first_name"],
                    points["unit"].fillna(""),
                    strict=True,
                )
            ]
            points = points.drop(columns=["last_name", "first_name", "unit"])
        windows = (
            json.loads(windows_json.read_text(encoding="utf-8")) if windows_json.exists() else {}
        )
        return {
            "points": Table(_neutral_frame(points), keys=["person", "bin_start"]),
            "windows": Document(self.neutral_persons(windows)),
        }

    def plots(self) -> list[str]:
        self._run_plots()
        images = sorted(
            p.name
            for p in self.files.atlas_dir.rglob("*")
            if p.is_file() and p.suffix in (".png", ".svg", ".pdf")
        )
        return images

    def _overlay_sets(self) -> dict[str, list[tuple[str, str]]]:
        """``overlay/<set>/``: items ``(id, text)`` from an index CSV, else one per text file."""
        import pandas as pd

        sets: dict[str, list[tuple[str, str]]] = {}
        root = self._overlay_root()
        if not root.is_dir():
            return sets
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            indexes = sorted(folder.glob("*index*.csv"))
            items: dict[str, list[str]] = {}
            if indexes:
                df = pd.read_csv(indexes[0], dtype=str, keep_default_na=False)
                for _, row in df.iterrows():
                    path = Path(row["txt_path"])
                    path = path if path.is_absolute() else indexes[0].parent / path
                    if not path.exists():
                        path = self.ws / row["txt_path"]
                    if {"last_name", "first_name"} <= set(df.columns):
                        key = person_id(row["last_name"], row["first_name"], row.get("unit", ""))
                    else:
                        key = Path(row["txt_path"]).stem
                    items.setdefault(key, []).append(path.read_text(encoding="utf-8"))
            else:
                for path in sorted(folder.rglob("*.txt")):
                    items.setdefault(path.stem, []).append(path.read_text(encoding="utf-8"))
            sets[folder.name] = [(k, "\n\n".join(v)) for k, v in sorted(items.items())]
        return sets

    def projection(self) -> dict[str, Artifact]:
        import numpy as np

        positioning = _mod("lexicon.positioning")
        concept_svd_centroids = positioning.concept_svd_centroids
        scored_top_terms_for_vector = positioning.scored_top_terms_for_vector
        subfield_svd_centroids = positioning.subfield_svd_centroids
        subfield_weights_for_vector = positioning.subfield_weights_for_vector
        project_text = positioning.project_text

        tfidf, restricted_terms, svd, anchors = self._positioning_models()
        aliases = _read_csv(self.files.term_aliases_csv, dtype=str, keep_default_na=False)
        alias_map = dict(zip(aliases["alias"], aliases["canonical"], strict=True))
        _, emb = self._lexical()
        applied = json.loads(self.files.subfields_json.read_text(encoding="utf-8"))
        sf_centroids = subfield_svd_centroids(
            applied.get("subfields", []), emb.Z_terms, restricted_terms
        )
        c_centroids = concept_svd_centroids(
            applied.get("concepts", []), emb.Z_terms, restricted_terms
        )
        out: dict[str, Artifact] = {}
        for name, items in self._overlay_sets().items():
            if not items:
                continue
            zs, rows = [], []
            for key, text in items:
                z, top = project_text(
                    text,
                    tfidf=tfidf,
                    restricted_terms=restricted_terms,
                    svd=svd,
                    alias_map=alias_map,
                    length_bonus_alpha=self.cfg.length_bonus_alpha,
                    top_k=10,
                    top_n=self.cfg.top_n_researcher,
                )
                zs.append(z)
                rows.append(
                    {
                        "item": key,
                        "keywords": ";".join(t["term"] for t in top),
                        "keyword_scores": [t["score"] for t in top],
                        "near_terms": scored_top_terms_for_vector(
                            z, emb.Z_terms, restricted_terms, k=10
                        ),
                        "subfield_weights": subfield_weights_for_vector(z, sf_centroids),
                        "concept_weights": subfield_weights_for_vector(z, c_centroids),
                    }
                )
            Z = np.vstack(zs)
            xy = anchors.place(Z) if anchors is not None else np.zeros((0, 2))
            out[f"{name}_items"] = Strings([r["item"] for r in rows])
            out[f"{name}_coords"] = Array(Z, role="values")
            out[f"{name}_xy"] = Array(xy, role="values")
            out[f"{name}_details"] = Document(rows)
        return out

    def bundle(self, export_dir: Path | None) -> dict[str, Artifact]:
        import numpy as np

        map_bundle = _mod("atlas.map_bundle")
        build_bundle, read_bundle = map_bundle.build_bundle, map_bundle.read_bundle
        write_bundle = map_bundle.write_bundle
        CohortInput = _mod("atlas.map_merge").CohortInput
        to_dense = _mod("atlas.types").to_dense

        data, _ = self._lexical()
        applied = json.loads(self.files.subfields_json.read_text(encoding="utf-8"))
        terms = [str(t) for t in data.terms]
        ids = self.neutral_persons([str(i) for i in data.individuals])
        units = [group_id(u) for u in data.meta_ind["unit"]]
        taxonomy = {
            "schema": "map_taxonomy/1",
            "concepts": [
                {
                    "id": c["id"],
                    "label": c.get("label", ""),
                    "subfield_id": c.get("subfield_id"),
                    "terms": [terms[i] for i in c.get("term_indices", [])],
                }
                for c in applied.get("concepts", [])
            ],
            "subfields": [
                {"id": s["id"], "label": s.get("label", "")} for s in applied.get("subfields", [])
            ],
        }
        cohort_input = CohortInput(
            cohort_id=self.domain_id,
            terms=terms,
            X_tf=to_dense(data.X_tf),
            researcher_ids=ids,
            units=units,
        )
        bundle = build_bundle(
            cohort_input,
            scores=to_dense(data.X),
            taxonomy=taxonomy,
            profile={"producer": "reference run"},
            build_date="2026-01-01",
            producer="reference",
        )
        with tempfile.TemporaryDirectory() as tmp:
            as_dir = write_bundle(bundle, Path(tmp) / "bundle")
            as_zip = write_bundle(bundle, Path(tmp) / "bundle.zip")
            again = write_bundle(bundle, Path(tmp) / "again.zip")
            zip_doc = _zip_summary(as_zip)
            zip_doc["rewrite_identical"] = as_zip.read_bytes() == again.read_bytes()
            back = read_bundle(as_dir).to_cohort_input()
            back_zip = read_bundle(as_zip).to_cohort_input()
            meta = json.loads((as_dir / "bundle_meta.json").read_text(encoding="utf-8"))
            entity_terms = _read_csv(as_dir / "entity_terms.csv", dtype={"entity_id": str})
            entities = _read_csv(as_dir / "entities.csv", dtype={"entity_id": str, "unit": str})
            vocabulary = _read_csv(as_dir / "vocabulary.csv", dtype={"term": str})
            if export_dir is not None:
                shutil.copytree(as_dir, export_dir)

        def same(a: Any, b: Any) -> bool:
            return (
                a.terms == b.terms
                and a.researcher_ids == b.researcher_ids
                and list(a.units or []) == list(b.units or [])
                and bool(np.array_equal(to_dense(a.X_tf), to_dense(b.X_tf)))
            )

        order = np.argsort(np.array(ids), kind="stable")
        sorted_input = CohortInput(
            cohort_id=self.domain_id,
            terms=terms,
            X_tf=to_dense(data.X_tf)[order],
            researcher_ids=[ids[i] for i in order],
            units=[units[i] for i in order],
        )
        sorted_input = _sort_terms(sorted_input)
        meta.pop("engine_version", None)  # the installed engine's version string
        return {
            "meta": Document(rename_keys(meta, NEUTRAL_KEYS)),
            "entity_terms": Table(entity_terms, keys=["entity_id", "term"]),
            "entities": Table(_neutral_frame(entities), keys=["entity_id"]),
            "vocabulary": Table(vocabulary, keys=["term"]),
            "taxonomy": Document(taxonomy),
            "zip": Document(zip_doc),
            "round_trip": Document(
                {"directory": same(back, sorted_input), "zip": same(back_zip, sorted_input)}
            ),
        }


#: The number of themes of the subfield draft in a run of the engine alone (its default).
def _draft_themes() -> int:
    import inspect

    fn = _mod("lexicon.subfields").draft_subfields
    return int(inspect.signature(fn).parameters["n_subfields"].default)


def set_reference_decisions(project: Any) -> None:
    """Give a project the settings of the workspace run (``ENGINE_SETTINGS``, ``TRIAGE_SETTINGS``).

    The AI clean-up is on, with the model a workspace run uses; the recency
    window is the workspace run's; the themes and topics are the engine's
    default counts (explicit level sizes: the draft's themes over the
    clustering's topics, keywords on the topics as the workspace run leaves them:
    no comb); the first map version takes the layout's default seed.
    """
    from cartolex.project.models import AIIdentity

    defaults = _mod("atlas.driver").DEFAULTS
    settings = _mod("lexicon").KeywordsConfig()
    config = project.config
    if config.identity.domain_title != TRIAGE_SETTINGS["domain_title"]:
        raise SystemExit(
            f"reference: the project's domain title {config.identity.domain_title!r} is not "
            f"the reference's {TRIAGE_SETTINGS['domain_title']!r}"
        )
    identity = config.identity.model_copy(
        update={"ai": AIIdentity(provider="mistral", model=settings.llm_model)}
    )
    project.save_config(config.model_copy(update={"identity": identity}), action="reference")
    params, fp = project.read_params()
    stages = {
        "corpus.assemble": {"recency_years": ENGINE_SETTINGS["keywords"]["kw_recency_years"]},
        "keywords.triage": {"enabled": True},
        "themes.group": {
            "level_sizes": [_draft_themes(), defaults.clustering_n_concepts],
            "comb": False,
        },
    }
    updated = params.model_copy(update={"seed": defaults.umap_random_state, "stages": stages})
    project.save_params(updated, expected=fp, action="reference settings")


class _ResultFiles:
    """The engine's paths over a project's results, resolved when each one is asked for."""

    def __init__(self, root: Path, scratch: Path) -> None:
        self._root, self._scratch = root, scratch

    def paths(self) -> Any:
        from cartolex.build.enginefiles import results_paths

        return results_paths(self._root / "derived", self._scratch, self._root)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.paths(), name)


class ProjectEngine(Engine):
    """Runs the engine through a project build, and reads each stage's results from derived/.

    Each stage of the workspace run builds the project's matching stage with
    ``cartolex.build.build`` (the AI triage answered by the fake model, through
    the injectable client) and then reads the same files, where the build put
    them. What the workspace run checks by calling a stage twice, the stages
    record in their counts (the roster rewritten identically, the applied
    document equal to what the stage returned); the triage is run a second time
    by forcing it.
    """

    def __init__(self, project_dir: Path, *, judge: Callable[[str], Verdict], domain_id: str):
        self.project_dir = project_dir
        self._triage_runs = 0
        super().__init__(project_dir, judge=judge, domain_id=domain_id)

    def _setup(self) -> None:
        from cartolex.build import engine as wiring
        from cartolex.project import Project

        self.project = Project.open(self.project_dir, write=True)
        set_reference_decisions(self.project)
        judge = self.judge

        def client(**kwargs: Any) -> FakeModelClient:
            return FakeModelClient(judge, **kwargs)

        self.registry = wiring.engine_registry(
            wiring.AIAccess(
                client_factory=client, max_concurrent=TRIAGE_SETTINGS["llm_max_concurrent"]
            )
        )
        self.cfg = wiring.keywords_settings(
            self.project.config,
            recency_years=ENGINE_SETTINGS["keywords"]["kw_recency_years"],
            llm_max_concurrent=TRIAGE_SETTINGS["llm_max_concurrent"],
        )
        scratch = self.project_dir.parent / "figures"
        self.files = _ResultFiles(self.project_dir, scratch)

    def _context(self) -> Any:
        from cartolex.context import RunContext

        return RunContext(paths=self.files.paths(), settings=self.cfg, now_year=NOW_YEAR)

    def _build(self, stage: str, *, force: bool = False) -> Any:
        from cartolex.build import build

        result = build(
            self.project,
            [stage],
            registry=self.registry,
            force=[stage] if force else (),
            year=NOW_YEAR,
            budget_mb=1e12,
            consent=lambda request: True,
        )
        if result.outcome != "succeeded" or result.refused:
            raise SystemExit(f"reference: the project build of {stage} failed: {result.summary()}")
        return result

    def _counts(self, stage: str) -> dict[str, int]:
        record = json.loads((self.project_dir / "derived" / stage / "run.json").read_text("utf-8"))
        return record["measures"]["counts"]

    def _run_extract(self) -> None:
        self._build("keywords.extract")

    def _run_triage(self) -> Any:
        self._build("keywords.triage", force=self._triage_runs > 0)
        self._triage_runs += 1
        return json.loads(self.files.triage_decisions_json.read_text(encoding="utf-8"))

    def _run_build(self) -> None:
        self._build("keywords.build")

    def _run_roster(self) -> int:
        counts = self._counts("keywords.build")
        if counts.get("roster_rewrite_identical") != 1:
            raise SystemExit("reference: the roster written again differs from the first")
        return int(counts["roster_people"])

    def _run_space(self) -> None:
        self._build("themes.space")

    def _run_group(self) -> None:
        self._build("themes.group")  # the clustering, and the subfield draft

    def _run_layout(self) -> None:
        self._build("map.layout")  # the draft applied, the layout, the themes placed on it

    def _run_draft(self) -> None:
        return None

    def _run_apply(self) -> Any:
        applied = json.loads(self.files.subfields_json.read_text(encoding="utf-8"))
        same = all(
            self._counts(s).get("applied_matches_file") == 1 for s in ("themes.apply", "map.layout")
        )
        return applied if same else None

    def _run_trajectories(self) -> None:
        self._build("map.trajectories")

    def _run_plots(self) -> None:
        _mod("atlas.driver").run_lexical_plots(self._context())

    def _positioning_models(self) -> tuple:
        self._build("overlays.position")  # the stage runs; the runner projects as a workspace does
        return _mod("lexicon.positioning").load_positioning_models(self._context())

    def _overlay_root(self) -> Path:
        return self.project_dir / "derived" / "corpus.assemble" / "overlays"


def _zip_summary(path: Path) -> dict[str, Any]:
    """Members of a bundle archive: entry metadata and content hashes.

    The manifest member is hashed without the installed engine's version
    string, which differs between two engine builds by design.
    """
    import zipfile

    members = {}
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            data = zf.read(info)
            if info.filename == "bundle_meta.json":
                meta = json.loads(data)
                meta.pop("engine_version", None)
                data = _canonical_json(meta)
            members[info.filename] = {
                "sha256": _sha256(data),
                "date_time": list(info.date_time),
                "compress_type": info.compress_type,
            }
    return {"members": members, "order": list(members)}


def _sort_terms(cohort_input: Any) -> Any:
    """The same inputs with the vocabulary sorted (a bundle read back sorts its terms)."""
    import numpy as np

    CohortInput = _mod("atlas.map_merge").CohortInput

    order = np.argsort(np.array(cohort_input.terms, dtype=object), kind="stable")
    return CohortInput(
        cohort_id=cohort_input.cohort_id,
        terms=[cohort_input.terms[i] for i in order],
        X_tf=cohort_input.X_tf[:, order],
        researcher_ids=list(cohort_input.researcher_ids),
        units=list(cohort_input.units or []),
    )


def merge_stage(bundle_dirs: list[Path]) -> dict[str, Artifact]:
    """Reconcile, pool and embed two cohorts, then compute the merge metrics."""
    import numpy as np
    import pandas as pd

    mm = _mod("atlas.map_metrics")
    to_dense = _mod("atlas.types").to_dense
    read_bundle = _mod("atlas.map_bundle").read_bundle
    map_merge = _mod("atlas.map_merge")
    anchor_concepts = map_merge.anchor_concepts
    assemble_joint_matrix = map_merge.assemble_joint_matrix
    balanced_svd = map_merge.balanced_svd
    procrustes_residual = map_merge.procrustes_residual
    researcher_concept_weights = map_merge.researcher_concept_weights
    weight_matrix = map_merge.weight_matrix
    rec = _mod("atlas.reconcile")
    build_naive_table, build_cohort_senses = rec.build_naive_table, rec.build_cohort_senses
    reconcile = rec.reconcile

    bundles = [read_bundle(p) for p in bundle_dirs]
    inputs = [b.to_cohort_input() for b in bundles]

    taxonomies = {b.cohort_id: b.taxonomy for b in bundles if b.taxonomy}
    senses = []
    for b, s in sorted(
        zip(bundles, inputs, strict=True),
        key=lambda bs: bs[1].cohort_id,
    ):
        concept_of_term = {
            t: c.get("label", "")
            for c in (b.taxonomy or {}).get("concepts", [])
            for t in c["terms"]
        }
        senses.append(
            build_cohort_senses(
                s.cohort_id,
                s.terms,
                s.X_tf,
                concept_of_term=concept_of_term,
            )
        )
    first = reconcile(senses, embedder=hashing_embedder, embedder_fingerprint=HASHING_EMBEDDER_NAME)
    decisions = {
        p["key"]: ("merge" if i % 2 == 0 else "split")
        for i, p in enumerate(sorted(first.pending, key=lambda p: p["key"]))
    }
    table = reconcile(
        senses,
        embedder=hashing_embedder,
        embedder_fingerprint=HASHING_EMBEDDER_NAME,
        decisions=decisions,
    )
    naive = build_naive_table(senses)
    joint = assemble_joint_matrix(inputs, table)
    W = weight_matrix(joint)
    W_macro = weight_matrix(joint, idf_mode="macro")
    emb = balanced_svd(W, joint.cohorts, joint.researcher_ids, n_components=20)
    concept_meta, anchors = anchor_concepts(taxonomies, table, emb.Z_senses, joint.sense_ids)
    weights = researcher_concept_weights(joint, concept_meta)
    domains = np.asarray(joint.cohorts, dtype=object)

    own: dict[str, Any] = {}
    for s in inputs:
        joint_s = assemble_joint_matrix([s], table)
        own[s.cohort_id] = balanced_svd(
            weight_matrix(joint_s),
            joint_s.cohorts,
            joint_s.researcher_ids,
            n_components=20,
        )
    ids_sorted = sorted(own)
    knn_overlap = {
        d: mm.restricted_knn_overlap(emb.Z_ind[domains == d], own[d].Z_ind) for d in ids_sorted
    }
    shared = [j for j, sid in enumerate(joint.sense_ids) if len(joint.sense_cohorts[sid]) >= 2]
    residual = (
        procrustes_residual(
            own[ids_sorted[0]].Z_senses[shared], own[ids_sorted[1]].Z_senses[shared]
        )
        if len(ids_sorted) == 2 and shared
        else None
    )
    integrity = [
        {
            "domain_id": c["cohort_id"],
            "concept_id": c["concept_id"],
            "ratio": mm.integrity_ratio(
                emb.Z_senses[c["sense_cols"]],
                own[c["cohort_id"]].Z_senses[c["sense_cols"]],
            ),
        }
        for c in concept_meta
        if len(c["sense_cols"]) >= 2
    ]
    order_m, mixing = mm.mixing_matrix(emb.Z_ind, domains)
    _, mixing_z = mm.mixing_permutation_zscores(emb.Z_ind, domains, n_permutations=50)
    _, conductance = mm.conductance_matrix(emb.Z_ind, domains)
    _, gaps = mm.gap_statistic(emb.Z_ind, domains)
    entropy = mm.neighbor_cohort_entropy(emb.Z_ind, domains, n_permutations=50)
    _, shared_mass = mm.shared_vocab_mass(joint.T, domains)
    metrics = {
        "domains": order_m,
        "restricted_knn_overlap": knn_overlap,
        "procrustes_residual": residual,
        "integrity": integrity,
        "mixing": mixing,
        "mixing_z": mixing_z,
        "conductance": conductance,
        "gap": gaps,
        "weak_link_ratio": mm.weak_link_ratio(emb.Z_ind, domains),
        "entropy_mean": entropy["mean"],
        "entropy_z_mean": entropy["z_mean"],
        "same_group_auc": mm.same_unit_auc(emb.Z_ind, domains, joint.units),
        "shared_vocab_mass": shared_mass,
        "mean_pairwise_cosine_anchors": mm.mean_pairwise_cosine(anchors) if len(anchors) else None,
        "bridges": mm.bridge_pairs(emb.Z_ind, domains, joint.researcher_ids),
        "explained_variance": emb.explained_variance,
        "n_fit_rows": int(emb.fit_rows.sum()),
        "unmapped_terms": joint.unmapped_terms,
    }
    pooled_rows, pooled_cols = joint.T.nonzero()
    T = pd.DataFrame(
        [
            {"row": int(i), "col": int(j), "tf": float(joint.T[i, j])}
            for i, j in zip(pooled_rows, pooled_cols, strict=True)
        ]
    )
    rows = pd.DataFrame(
        {
            "person": joint.researcher_ids,
            "domain_id": joint.cohorts,
            "group": joint.units,
        }
    )
    entropy_values = np.asarray(entropy["entropy"], dtype=float)
    return {
        "table_first_pass": Document(rename_keys(first.to_json_dict(), NEUTRAL_KEYS)),
        "decisions": Document(decisions),
        "table": Document(rename_keys(table.to_json_dict(), NEUTRAL_KEYS)),
        "naive_table": Document(rename_keys(naive.to_json_dict(), NEUTRAL_KEYS)),
        "senses": Strings(joint.sense_ids),
        "rows": Table(rows, keys=["person"], ordered=True),
        "pooled": Table(T, keys=["row", "col"]),
        "weights_pooled": Array(to_dense(W)),
        "weights_macro": Array(to_dense(W_macro)),
        "singular_values": Array(emb.svd.singular_values_, role="spectrum"),
        "person_coords": Array(emb.Z_ind, role="embedding"),
        "sense_coords": Array(emb.Z_senses, role="embedding"),
        "anchors": Array(anchors),
        "anchor_meta": Document(rename_keys(concept_meta, NEUTRAL_KEYS)),
        "concept_weights": Array(weights),
        "cross_domain_mass": Array(
            mm.cross_cohort_sense_mass(joint.T, joint.sense_cohorts, joint.sense_ids)
        ),
        "entropy": Array(entropy_values),
        "metrics": Document(rename_keys(_json_safe(metrics), NEUTRAL_KEYS)),
    }


def _json_safe(obj: Any) -> Any:
    """Plain JSON values with non-finite floats spelled out (stable text)."""
    obj = _plain(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    return obj


# ════════════════════════════════════════════════════════════════════════════
# ENGINE ADAPTER: END
# ════════════════════════════════════════════════════════════════════════════


# ── Environment record ───────────────────────────────────────────────────────

_LIBRARIES = (
    "numpy",
    "scipy",
    "scikit-learn",
    "pandas",
    "umap-learn",
    "pynndescent",
    "numba",
    "llvmlite",
    "joblib",
    "threadpoolctl",
    "langdetect",
    "matplotlib",
    "pypdf",
    "pdfminer.six",
    "spacy",
    "thinc",
    "en_core_web_md",
    "fr_core_news_md",
    "pt_core_news_md",
)


def environment_record() -> dict[str, Any]:
    """Interpreter, platform, library versions, engine version and thread settings."""
    versions = {}
    for lib in _LIBRARIES:
        try:
            versions[lib] = importlib.metadata.version(lib)
        except importlib.metadata.PackageNotFoundError:
            versions[lib] = None
    blas = []
    with contextlib.suppress(Exception):
        from threadpoolctl import threadpool_info

        blas = sorted({f"{i.get('internal_api')} {i.get('version')}" for i in threadpool_info()})
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": f"{sys.platform}-{platform.machine()}",
        "libraries": versions,
        "native_libraries": blas,
        "engine_version": engine_version(),
        "engine_fingerprint": engine_fingerprint(),
        "settings": {k: os.environ.get(k) for k in sorted(FIXED_ENV)},
    }


def tree_sha256(root: Path) -> str:
    """sha256 of a directory tree: sorted relative paths and file contents."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


# ── Orchestration ────────────────────────────────────────────────────────────


@dataclass
class RunLog:
    """Timings and memory of one run (printed, never part of the reference)."""

    seconds: dict[str, float] = field(default_factory=dict)
    peak_mb_after: dict[str, float] = field(default_factory=dict)

    def timed(self, name: str, fn: Callable[[], Any]) -> Any:
        t0 = time.monotonic()
        try:
            return fn()
        finally:
            self.seconds[name] = round(time.monotonic() - t0, 2)
            self.peak_mb_after[name] = peak_rss_mb()
            log.info(
                "stage %-13s %7.1fs  peak so far %7.0f MB",
                name,
                self.seconds[name],
                self.peak_mb_after[name],
            )


def peak_rss_mb() -> float:
    """Peak resident memory of this process and its finished children, in MB."""
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return round(max(own, children) / 1024.0, 1)


def _prepare_out(out: Path) -> None:
    if out.exists():
        entries = list(out.iterdir())
        if entries and not (out / "manifest.json").exists():
            raise SystemExit(f"reference: {out} is not empty and holds no reference output")
        shutil.rmtree(out)
    out.mkdir(parents=True)


def run_single(
    workspace: Path,
    out: Path,
    *,
    truth: Path | None,
    project: Path | None = None,
    domain_id: str,
    until: str | None,
    export_bundle: Path | None,
    runlog: RunLog,
    keep_work: Path | None,
    work_root: Path | None,
) -> dict[str, Any]:
    """Run every stage on a copy of *workspace*; write artifacts; return the manifest."""
    work = Path(tempfile.mkdtemp(prefix="reference-run-", dir=work_root))
    try:
        ws = work / "workspace"
        shutil.copytree(workspace, ws)
        cwd = work / "cwd"
        cwd.mkdir()
        os.chdir(cwd)  # some engine modules create folders in the current directory
        judge = make_judge(load_truth(truth))
        if project is not None:
            shutil.copytree(project, work / "project")
            engine: Engine = ProjectEngine(work / "project", judge=judge, domain_id=domain_id)
        else:
            engine = Engine(ws, judge=judge, domain_id=domain_id)
        artifacts: dict[str, Any] = {}
        images: list[str] = []
        stages = list(STAGES)
        if until is not None:
            stages = stages[: stages.index(until) + 1]
        for stage in stages:
            if stage == "trajectories":
                artifacts[stage] = write_stage(out, stage, runlog.timed(stage, engine.trajectories))
                images = runlog.timed("plots", engine.plots)
            elif stage == "bundle":
                artifacts[stage] = write_stage(
                    out, stage, runlog.timed(stage, lambda: engine.bundle(export_bundle))
                )
            else:
                artifacts[stage] = write_stage(
                    out, stage, runlog.timed(stage, getattr(engine, stage))
                )
        return {
            "format": FORMAT,
            "mode": "single",
            "stages": stages,
            "artifacts": artifacts,
            "settings": {
                "engine": rename_keys(_plain(ENGINE_SETTINGS), NEUTRAL_KEYS),
                "triage": rename_keys(_plain(TRIAGE_SETTINGS), NEUTRAL_KEYS),
                "now_year": NOW_YEAR,
                "domain_id": domain_id,
            },
            "inputs": {
                "workspace_sha256": tree_sha256(workspace),
                "truth_sha256": _sha256(truth.read_bytes()) if truth else None,
            },
            "plots": {"ran": "plots" in runlog.seconds, "images": len(images)},
            "environment": environment_record(),
        }
    finally:
        opened = locals().get("engine")
        if getattr(opened, "project", None) is not None:
            opened.project.close()
        os.chdir(out.parent if out.parent.exists() else Path.home())
        if keep_work is not None:
            if keep_work.exists():
                shutil.rmtree(keep_work)
            shutil.move(str(work), str(keep_work))
        else:
            shutil.rmtree(work, ignore_errors=True)


def run_merge(
    workspaces: list[Path],
    truths: list[Path | None],
    out: Path,
    *,
    runlog: RunLog,
    work_root: Path | None,
    projects: list[Path | None] | None = None,
) -> dict[str, Any]:
    """Run both workspaces up to the bundle (one process each), then the merge stage."""
    work = Path(tempfile.mkdtemp(prefix="reference-merge-", dir=work_root))
    try:
        bundle_dirs, inputs, children = [], [], []
        # The two cohorts are independent: run them side by side, one process each.
        for i, (ws, truth) in enumerate(zip(workspaces, truths, strict=True)):
            domain = "ab"[i]
            cmd = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--workspace",
                str(ws),
                "--out",
                str(work / f"out-{domain}"),
                "--domain-id",
                domain,
                "--until",
                "bundle",
                "--export-bundle",
                str(work / f"bundle-{domain}"),
                "--work-root",
                str(work),
            ]
            if truth is not None:
                cmd += ["--truth", str(truth)]
            if projects is not None and projects[i] is not None:
                cmd += ["--project", str(projects[i])]
            children.append((domain, subprocess.Popen(cmd), time.monotonic()))
        failed = []
        for domain, proc, t0 in children:
            if proc.wait() != 0:
                failed.append(domain)
            runlog.seconds[f"cohort_{domain}"] = round(time.monotonic() - t0, 2)
        if failed:
            raise SystemExit(f"reference: the run of cohort(s) {failed} failed")
        for domain, _, _ in children:
            manifest = json.loads((work / f"out-{domain}" / "manifest.json").read_text("utf-8"))
            bundle_dirs.append(work / f"bundle-{domain}")
            inputs.append(
                {
                    "domain_id": domain,
                    "workspace_sha256": manifest["inputs"]["workspace_sha256"],
                    "truth_sha256": manifest["inputs"]["truth_sha256"],
                    "bundle": {k: v["sha256"] for k, v in manifest["artifacts"]["bundle"].items()},
                }
            )
        os.chdir(work)
        artifacts = {
            "merge": write_stage(
                out, "merge", runlog.timed("merge", lambda: merge_stage(bundle_dirs))
            )
        }
        return {
            "format": FORMAT,
            "mode": "merge",
            "stages": list(MERGE_STAGES),
            "artifacts": artifacts,
            "settings": {
                "engine": rename_keys(_plain(ENGINE_SETTINGS), NEUTRAL_KEYS),
                "triage": rename_keys(_plain(TRIAGE_SETTINGS), NEUTRAL_KEYS),
                "now_year": NOW_YEAR,
                "embedder": HASHING_EMBEDDER_NAME,
            },
            "inputs": {"cohorts": inputs},
            "environment": environment_record(),
        }
    finally:
        os.chdir(Path.home())
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Run the engine and write a reference output.")
    parser.add_argument("--workspace", required=True, type=Path, help="corpus-contract workspace")
    parser.add_argument("--truth", type=Path, help="truth file of the workspace (fake model rules)")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--merge-with", type=Path, help="second workspace: run the merge stage")
    parser.add_argument("--merge-truth", type=Path, help="truth file of the second workspace")
    parser.add_argument(
        "--project",
        type=Path,
        help="run through this project (the same world written as a project) with the build",
    )
    parser.add_argument("--merge-project", type=Path, help="the second world's project")
    parser.add_argument("--domain-id", default="a", help="identifier of this cohort (default a)")
    parser.add_argument("--until", choices=STAGES, help="stop after this stage")
    parser.add_argument("--export-bundle", type=Path, help="also write the engine bundle here")
    parser.add_argument("--keep-work", type=Path, help="keep the working copy here (debugging)")
    parser.add_argument(
        "--work-root", type=Path, help="parent of the working copies (default: temp)"
    )
    parser.add_argument("--timings", type=Path, help="write timings and peak memory (JSON) here")
    parser.add_argument("--verbose", action="store_true", help="show the engine's log")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log.setLevel(logging.INFO)
    # Advice printed by the layout library about seeds and parallelism: not a finding.
    warnings.filterwarnings("ignore", message=r"n_jobs value .* overridden")
    block_network()
    out = args.out.resolve()
    _prepare_out(out)
    runlog = RunLog()
    work_root = args.work_root.resolve() if args.work_root else None
    if work_root is not None:
        work_root.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    if args.merge_with is not None:
        manifest = run_merge(
            [args.workspace.resolve(), args.merge_with.resolve()],
            [
                args.truth.resolve() if args.truth else None,
                args.merge_truth.resolve() if args.merge_truth else None,
            ],
            out,
            runlog=runlog,
            work_root=work_root,
            projects=[
                args.project.resolve() if args.project else None,
                args.merge_project.resolve() if args.merge_project else None,
            ],
        )
    else:
        manifest = run_single(
            args.workspace.resolve(),
            out,
            truth=args.truth.resolve() if args.truth else None,
            project=args.project.resolve() if args.project else None,
            domain_id=args.domain_id,
            until=args.until,
            export_bundle=args.export_bundle.resolve() if args.export_bundle else None,
            runlog=runlog,
            keep_work=args.keep_work.resolve() if args.keep_work else None,
            work_root=work_root,
        )
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    total = round(time.monotonic() - t0, 1)
    info = {
        "seconds": runlog.seconds,
        "peak_mb_after": runlog.peak_mb_after,
        "total_seconds": total,
        "peak_rss_mb": peak_rss_mb(),
    }
    if args.timings is not None:
        args.timings.write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    print(
        f"reference run: {len(manifest['stages'])} stage(s) in {total}s, peak {info['peak_rss_mb']} MB -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
