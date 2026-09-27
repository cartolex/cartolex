# SPDX-License-Identifier: MIT
"""Model files: the fitted objects a project stores, without pickle.

A project folder may be copied, shared or uploaded to a server, so nothing
the engine stores may run code when it is read. Each model is two files side
by side:

``<name>.json``
    The descriptor: the format, the kind of model, its parameters,
    vocabularies and tables, the library versions that wrote it, and the name,
    sha256 and array list of its array file.
``<name>.npz``
    Its arrays, written without pickling and read with ``allow_pickle=False``.

Loading rebuilds the object: a ``TfidfVectorizer`` from its parameters,
vocabulary and IDF; a ``TruncatedSVD`` with its fitted attributes set; the
lexical data and the embeddings; the 2-D layout model. A UMAP model is
**re-fitted** from its stored inputs, parameters and random seed (see
:class:`~cartolex.atlas.reducers.UmapModel`) and checked against the stored
layout: the same libraries on the same machine give the same model, bit for
bit. When the re-fit differs (other library versions, another processor), the
model is still returned: the stored coordinates stay those of the map, only new
points are placed with the re-fitted model, and a warning gives the measured
deviation and what differs (``model.refit_deviation``). An anchored t-SNE model is rebuilt from its reference set and
2-D coordinates, without running t-SNE again.

Model files of earlier releases (``<name>.joblib``, pickles) are never read:
asking for a model whose descriptor is absent while such a file is present
raises :class:`ModelFileError`, naming the file and the stage that rebuilds
it. Nothing converts them.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import math
import os
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse

from .reducers import AnchoredTSNE, UmapModel
from .types import Embeddings, LexicalData

logger = logging.getLogger(__name__)

__all__ = [
    "FORMAT",
    "ModelFileError",
    "arrays_file",
    "legacy_file",
    "load_embeddings",
    "load_layout_model",
    "load_lexical_data",
    "load_svd",
    "load_vectorizer",
    "reject_legacy",
    "save_embeddings",
    "save_layout_model",
    "save_lexical_data",
    "save_svd",
    "save_vectorizer",
]

#: The format written in every model descriptor.
FORMAT = "cartolex-model/1"

#: Libraries whose versions a descriptor records (those that shape a model).
_LIBRARIES = ("cartolex", "numpy", "scipy", "scikit-learn", "pandas")
_LAYOUT_LIBRARIES = ("umap-learn", "pynndescent", "numba")

#: A fixed time stamp for the members of an array file, so equal models give equal bytes.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


class ModelFileError(RuntimeError):
    """A stored model cannot be used; re-running the stage that writes it rebuilds it.

    Raised for a model file of an earlier release (a pickle, never read), for
    a descriptor and an array file that do not belong together or are not in
    this format, and for a layout model that the installed libraries do not
    reproduce.
    """


def arrays_file(path: Path) -> Path:
    """The array file (``.npz``) that goes with the model descriptor *path*."""
    return Path(path).with_suffix(".npz")


def legacy_file(path: Path) -> Path:
    """The file an earlier release stored in place of the model descriptor *path*."""
    return Path(path).with_suffix(".joblib")


def _legacy_message(legacy: Path, stage: str) -> str:
    return (
        f"{legacy} is a model file of an earlier release (a pickle), which this version "
        f"never reads because loading a pickle can run code. Re-run the {stage} stage to "
        "rebuild it."
    )


def reject_legacy(*paths: Path, stage: str) -> None:
    """Raise :class:`ModelFileError` when a descriptor in *paths* is absent but an old file is present.

    *stage* names the stage that rebuilds the files. Descriptors that exist,
    and absent ones without an old file, pass.
    """
    for path in paths:
        legacy = legacy_file(path)
        if not Path(path).exists() and legacy.exists():
            raise ModelFileError(_legacy_message(legacy, stage))


# ── Reading and writing a descriptor and its arrays ─────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _versions(names: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in names:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = "absent"
    return out


def _write(
    path: Path,
    kind: str,
    meta: Mapping[str, Any],
    arrays: Mapping[str, np.ndarray],
    *,
    libraries: tuple[str, ...] = _LIBRARIES,
) -> None:
    """Write the descriptor *path* and its array file (arrays first, then the descriptor)."""
    path = Path(path)
    npz = arrays_file(path)
    if npz == path:
        raise ValueError(f"a model descriptor needs a .json name, not {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, Any]] = {}
    tmp_npz = npz.with_name(npz.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp_npz, "w", compression=zipfile.ZIP_STORED) as zf:
            for name, value in arrays.items():
                arr = np.asarray(value)
                if arr.dtype.hasobject:
                    raise TypeError(f"array {name!r} holds Python objects and cannot be stored")
                info = zipfile.ZipInfo(f"{name}.npy", date_time=_ZIP_DATE)
                info.compress_type = zipfile.ZIP_STORED
                with zf.open(info, "w", force_zip64=True) as handle:
                    np.lib.format.write_array(handle, arr, allow_pickle=False)
                entries[name] = {"dtype": arr.dtype.str, "shape": list(arr.shape)}
        doc = {
            "format": FORMAT,
            "kind": kind,
            **meta,
            "arrays": {"file": npz.name, "sha256": _sha256(tmp_npz), "entries": entries},
            "libraries": _versions(libraries),
        }
        text = json.dumps(doc, indent=1, ensure_ascii=False, allow_nan=False)
    except BaseException:
        tmp_npz.unlink(missing_ok=True)
        raise
    os.replace(tmp_npz, npz)
    tmp_json = path.with_name(path.name + ".tmp")
    tmp_json.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp_json, path)


def _read(
    path: Path, *, kinds: tuple[str, ...], stage: str
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """The descriptor *path* (of one of *kinds*) and its arrays, checked."""
    path = Path(path)

    def fail(reason: str) -> ModelFileError:
        return ModelFileError(f"{path}: {reason}. Re-run the {stage} stage to rebuild it.")

    if not path.exists():
        reject_legacy(path, stage=stage)
        raise FileNotFoundError(f"{path} not found — run the {stage} stage first.")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise fail(f"not a readable model descriptor ({exc})") from exc
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        found = doc.get("format") if isinstance(doc, dict) else None
        raise fail(f"not a model descriptor of format {FORMAT} (found {found!r})")
    if doc.get("kind") not in kinds:
        raise fail(f"holds a {doc.get('kind')!r} model, not {' or '.join(map(repr, kinds))}")
    spec = doc.get("arrays")
    if not isinstance(spec, dict) or not isinstance(spec.get("entries"), dict):
        raise fail("no array list")
    name = spec.get("file")
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise fail(f"invalid array file name {name!r}")
    npz = path.parent / name
    if not npz.exists():
        raise fail(f"its array file {name} is missing")
    if _sha256(npz) != spec.get("sha256"):
        raise fail(f"its array file {name} was written by another run")
    if not zipfile.is_zipfile(npz):
        raise fail(f"its array file {name} is not an .npz archive")
    arrays: dict[str, np.ndarray] = {}
    try:
        with np.load(npz, allow_pickle=False) as data:
            for key, entry in spec["entries"].items():
                arr = data[key]
                if not isinstance(arr, np.ndarray) or (
                    arr.dtype.str != entry.get("dtype") or list(arr.shape) != entry.get("shape")
                ):
                    raise fail(f"array {key!r} does not match its description")
                arrays[key] = arr
    except ModelFileError:
        raise
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise fail(f"unreadable array file {name} ({exc})") from exc
    return doc, arrays


def _rebuild_error(path: Path, stage: str, exc: Exception) -> ModelFileError:
    return ModelFileError(
        f"{path}: the model cannot be rebuilt with the installed libraries "
        f"({type(exc).__name__}: {exc}). Re-run the {stage} stage to rebuild it."
    )


#: What a descriptor that does not hold what its kind needs raises while it is rebuilt.
_REBUILD_ERRORS = (KeyError, TypeError, ValueError, AttributeError)


def _json_ready(value: Any, what: str) -> Any:
    """*value* when it is plain JSON data (finite numbers), else ``ValueError``."""
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} cannot be stored as JSON: {exc}") from exc
    return value


# ── The TF-IDF vectorizer ────────────────────────────────────────────────────


def save_vectorizer(vectorizer: Any, path: Path) -> None:
    """Store a fitted ``TfidfVectorizer``: its parameters and vocabulary (JSON), its IDF (array).

    Only an IDF-weighted vectorizer with built-in text processing (no custom
    callable) can be stored.
    """
    params = dict(vectorizer.get_params())
    if not params.get("use_idf", False):
        raise ValueError("only an IDF-weighted vectorizer (use_idf=True) can be stored")
    for name in ("analyzer", "preprocessor", "tokenizer"):
        if callable(params.get(name)):
            raise ValueError(f"a vectorizer with a custom {name} cannot be stored")
    params.pop("vocabulary", None)  # stored as the fitted features, in column order
    dtype = np.dtype(params.pop("dtype")).name
    if isinstance(params.get("ngram_range"), tuple):
        params["ngram_range"] = list(params["ngram_range"])
    features = [str(t) for t in vectorizer.get_feature_names_out()]
    meta = {
        "params": _json_ready(params, "the vectorizer's parameters"),
        "dtype": dtype,
        "features": features,
    }
    _write(path, "vectorizer", meta, {"idf": np.asarray(vectorizer.idf_)})


def load_vectorizer(path: Path) -> Any:
    """The ``TfidfVectorizer`` stored at *path*, ready to ``transform``."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    stage = "consolidation"
    doc, arrays = _read(path, kinds=("vectorizer",), stage=stage)
    try:
        params = dict(doc["params"])
        if isinstance(params.get("ngram_range"), list):
            params["ngram_range"] = tuple(params["ngram_range"])
        vectorizer = TfidfVectorizer(
            **params, dtype=np.dtype(doc["dtype"]).type, vocabulary=list(doc["features"])
        )
        vectorizer.idf_ = arrays["idf"]
    except _REBUILD_ERRORS as exc:
        raise _rebuild_error(path, stage, exc) from exc
    return vectorizer


# ── The SVD ──────────────────────────────────────────────────────────────────

_SVD_ARRAYS = (
    "components_",
    "explained_variance_",
    "explained_variance_ratio_",
    "singular_values_",
)


def save_svd(svd: Any, path: Path) -> None:
    """Store a fitted ``TruncatedSVD``: its parameters (JSON) and fitted arrays."""
    meta = {
        "params": _json_ready(dict(svd.get_params()), "the SVD's parameters"),
        "n_features_in": int(svd.n_features_in_),
    }
    arrays = {name.rstrip("_"): np.asarray(getattr(svd, name)) for name in _SVD_ARRAYS}
    _write(path, "svd", meta, arrays)


def load_svd(path: Path) -> Any:
    """The ``TruncatedSVD`` stored at *path*, with its fitted attributes set."""
    from sklearn.decomposition import TruncatedSVD

    stage = "SVD"
    doc, arrays = _read(path, kinds=("svd",), stage=stage)
    try:
        svd = TruncatedSVD(**doc["params"])
        for name in _SVD_ARRAYS:
            setattr(svd, name, arrays[name.rstrip("_")])
        svd.n_features_in_ = int(doc["n_features_in"])
    except _REBUILD_ERRORS as exc:
        raise _rebuild_error(path, stage, exc) from exc
    return svd


# ── The lexical data ─────────────────────────────────────────────────────────


def _matrix_arrays(name: str, X: Any) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    if sparse.issparse(X):
        csr = X.tocsr()
        info = {"format": "csr", "shape": [int(n) for n in csr.shape]}
        arrays = {
            f"{name}_data": csr.data,
            f"{name}_indices": csr.indices,
            f"{name}_indptr": csr.indptr,
        }
        return info, arrays
    return {"format": "dense"}, {name: np.asarray(X)}


def _matrix(name: str, info: dict[str, Any], arrays: dict[str, np.ndarray]) -> Any:
    if info["format"] == "csr":
        return sparse.csr_matrix(
            (arrays[f"{name}_data"], arrays[f"{name}_indices"], arrays[f"{name}_indptr"]),
            shape=tuple(info["shape"]),
        )
    return arrays[name]


def _string_list(values: Any, what: str) -> list[str]:
    out = list(values)
    if not all(isinstance(v, str) for v in out):
        raise ValueError(f"{what} must be strings to be stored")
    return out


def _frame_doc(df: pd.DataFrame) -> dict[str, Any]:
    """A table as JSON columns (name, dtype, values; a missing value is ``null``)."""
    index = df.index
    if not (isinstance(index, pd.RangeIndex) and index.start == 0 and index.step == 1):
        raise ValueError("the person table must have the default row index to be stored")
    if not df.columns.is_unique:
        raise ValueError("the person table has duplicate column names")
    columns = []
    for name in df.columns:
        if not isinstance(name, str):
            raise ValueError(f"column name {name!r} is not a string")
        col = df[name]
        dtype = col.dtype
        kind = dtype.kind if isinstance(dtype, np.dtype) else ""
        if isinstance(dtype, pd.StringDtype) or kind == "O":
            values = [None if _missing(v) else v for v in col.tolist()]
        elif kind == "f":
            values = [_float_cell(v) for v in col.tolist()]
        elif kind in ("b", "i", "u"):
            values = col.tolist()
        else:
            raise ValueError(f"column {name!r} of type {dtype} cannot be stored")
        columns.append({"name": name, "dtype": str(dtype), "values": values})
    return {"n_rows": len(df), "columns": _json_ready(columns, "the person table")}


def _missing(value: Any) -> bool:
    return value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value))


def _float_cell(value: float) -> float | str | None:
    if math.isnan(value):
        return None
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return value


def _column_dtype(name: str) -> Any:
    dtype = pd.api.types.pandas_dtype(name)
    if getattr(dtype, "kind", "") in "SU":  # "str" without pandas' string type: plain objects
        return np.dtype(object)
    return dtype


def _frame(doc: dict[str, Any]) -> pd.DataFrame:
    index = pd.RangeIndex(int(doc["n_rows"]))
    columns: dict[str, pd.Series] = {}
    for col in doc["columns"]:
        dtype = _column_dtype(col["dtype"])
        values = col["values"]
        kind = getattr(dtype, "kind", "")
        if isinstance(dtype, pd.StringDtype):
            array: Any = pd.array(values, dtype=dtype)
        elif kind == "O":
            array = np.array([np.nan if v is None else v for v in values], dtype=object)
        elif kind == "f":
            array = np.array([np.nan if v is None else float(v) for v in values], dtype=dtype)
        else:
            array = np.array(values, dtype=dtype)
        # An explicit dtype: a plain object column must not be read as a string column.
        columns[col["name"]] = pd.Series(array, index=index, dtype=dtype)
    return pd.DataFrame(columns, index=index)


def save_lexical_data(data: LexicalData, path: Path) -> None:
    """Store the lexical data: the matrices (arrays), the term and person lists and the person table."""
    x_info, arrays = _matrix_arrays("X", data.X)
    matrices = {"X": x_info}
    x_tf = getattr(data, "X_tf", None)
    if x_tf is not None:
        tf_info, tf_arrays = _matrix_arrays("X_tf", x_tf)
        matrices["X_tf"] = tf_info
        arrays.update(tf_arrays)
    meta = {
        "terms": _string_list(data.terms, "terms"),
        "individuals": _string_list(data.individuals, "person identifiers"),
        "matrices": matrices,
        "persons": _frame_doc(data.meta_ind),
    }
    _write(path, "lexical_data", meta, arrays)


def load_lexical_data(path: Path) -> LexicalData:
    """The lexical data stored at *path*."""
    stage = "SVD"
    doc, arrays = _read(path, kinds=("lexical_data",), stage=stage)
    try:
        matrices = doc["matrices"]
        return LexicalData(
            X=_matrix("X", matrices["X"], arrays),
            terms=list(doc["terms"]),
            individuals=list(doc["individuals"]),
            meta_ind=_frame(doc["persons"]),
            X_tf=_matrix("X_tf", matrices["X_tf"], arrays) if "X_tf" in matrices else None,
        )
    except _REBUILD_ERRORS as exc:
        raise _rebuild_error(path, stage, exc) from exc


# ── The embeddings ───────────────────────────────────────────────────────────

_EMBEDDING_ARRAYS = ("Z_ind", "Z_terms", "umap_ind", "umap_terms")


def save_embeddings(emb: Embeddings, path: Path) -> None:
    """Store the embeddings: SVD coordinates, and layout coordinates once computed."""
    arrays = {name: getattr(emb, name) for name in _EMBEDDING_ARRAYS}
    arrays = {name: value for name, value in arrays.items() if value is not None}
    _write(path, "embeddings", {}, arrays)


def load_embeddings(path: Path) -> Embeddings:
    """The embeddings stored at *path* (layout coordinates ``None`` before the layout stage)."""
    _, arrays = _read(path, kinds=("embeddings",), stage="SVD")
    if "Z_ind" not in arrays or "Z_terms" not in arrays:
        raise _rebuild_error(path, "SVD", KeyError("Z_ind, Z_terms"))
    return Embeddings(**{name: arrays.get(name) for name in _EMBEDDING_ARRAYS})


# ── The layout model ─────────────────────────────────────────────────────────


def save_layout_model(model: UmapModel | AnchoredTSNE, path: Path) -> None:
    """Store a layout model: a UMAP's fit record, or an anchored t-SNE's fitted arrays."""
    if isinstance(model, UmapModel):
        arrays = {"fit_input": model.fit_input, "embedding": model.embedding}
        if model.fit_target is not None:
            arrays["fit_target"] = model.fit_target
        if model.replay_input is not None:
            arrays["replay_input"] = model.replay_input
            arrays["replay_output"] = model.replay_output
        meta = {"params": _json_ready(dict(model.params), "the UMAP parameters")}
        _write(path, "umap", meta, arrays, libraries=_LIBRARIES + _LAYOUT_LIBRARIES)
    elif isinstance(model, AnchoredTSNE):
        if model.reference_ is None or model.embedding_ is None:
            raise ValueError("an anchored t-SNE model must be fitted before it is stored")
        meta = {"params": _json_ready(model.get_params(), "the t-SNE parameters")}
        arrays = {"reference": model.reference_, "embedding": model.embedding_}
        _write(path, "anchored_tsne", meta, arrays)
    else:
        raise TypeError(f"not a layout model: {type(model).__name__}")


def load_layout_model(path: Path) -> UmapModel | AnchoredTSNE:
    """The layout model stored at *path*, ready to ``transform`` new points.

    A UMAP model is re-fitted from its stored inputs, parameters and seed. When it
    reproduces the stored layout exactly (the fit's coordinates and the points
    transformed after it), ``model.refit_deviation`` is ``None``. Otherwise the
    stored coordinates are kept on the model, a warning names what differs from
    the environment that wrote it, and ``model.refit_deviation`` records the
    largest displacement, relative to the extent of the stored map.
    """
    stage = "UMAP layout"
    doc, arrays = _read(path, kinds=("umap", "anchored_tsne"), stage=stage)
    try:
        if doc["kind"] == "anchored_tsne":
            return AnchoredTSNE.from_fitted(
                arrays["reference"], arrays["embedding"], **doc["params"]
            )
        model = UmapModel.fit(
            dict(doc["params"]),
            arrays["fit_input"],
            fit_target=arrays.get("fit_target"),
            replay_input=arrays.get("replay_input"),
        )
        stored = arrays["embedding"]
    except _REBUILD_ERRORS as exc:
        raise _rebuild_error(path, stage, exc) from exc
    same = _identical(model.embedding, stored)
    if same and "replay_output" in arrays:
        same = _identical(model.replay_output, arrays["replay_output"])
    model.refit_deviation = None
    if not same:
        recorded = doc.get("libraries", {})
        installed = _versions(tuple(recorded) or _LIBRARIES + _LAYOUT_LIBRARIES)
        changed = [
            f"{name} {recorded[name]} → {installed.get(name)}"
            for name in sorted(recorded)
            if recorded[name] != installed.get(name)
        ]
        detail = (
            "library versions differ: " + ", ".join(changed)
            if changed
            else "same library versions: the machine or its settings differ"
        )
        deviation = _relative_displacement(model.embedding, stored)
        if "replay_output" in arrays and model.replay_output is not None:
            deviation = max(
                deviation, _relative_displacement(model.replay_output, arrays["replay_output"])
            )
        # The stored map stays the map: only points placed from now on use the re-fit.
        model.embedding = stored
        if "replay_output" in arrays:
            model.replay_output = arrays["replay_output"]
        model.refit_deviation = {"relative_max_displacement": deviation, "detail": detail}
        logger.warning(
            "%s: re-fitting the layout here does not reproduce the stored map exactly (%s); "
            "largest displacement %.3g of the map's extent. The stored map is kept; new points "
            "are placed with the re-fitted model.",
            path,
            detail,
            deviation,
        )
    return model


def _relative_displacement(a: np.ndarray, b: np.ndarray) -> float:
    """Largest point displacement between *a* and *b*, relative to the extent of *b*."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.shape != b.shape:
        return math.inf
    extent = float(np.ptp(b, axis=0).max()) if b.size else 0.0
    shift = float(np.sqrt(((a - b) ** 2).sum(axis=1)).max()) if b.size else 0.0
    return shift / extent if extent > 0 else shift


def _identical(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    a, b = np.asarray(a), np.asarray(b)
    return a.dtype == b.dtype and a.shape == b.shape and bool(np.array_equal(a, b))
