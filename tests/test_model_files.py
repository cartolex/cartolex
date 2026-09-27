# SPDX-License-Identifier: MIT
"""Model files: no pickle anywhere the engine stores or reads a model.

Every stored model is a JSON descriptor and an ``.npz`` array file read with
``allow_pickle=False``; loading rebuilds the object, and a UMAP model is
re-fitted and checked against its stored layout. Synthetic data only.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from cartolex.atlas import model_files, reducers
from cartolex.atlas.model_files import ModelFileError
from cartolex.atlas.types import Embeddings, LexicalData

PACKAGE = Path(__file__).resolve().parent.parent / "cartolex"

#: Modules that serialise Python objects (running code when they read them back).
PICKLING_MODULES = {"pickle", "_pickle", "cPickle", "joblib", "dill", "cloudpickle", "shelve"}
#: Calls that read or write pickles whatever the module is called.
PICKLING_CALLS = {"read_pickle", "to_pickle"}


# ── The package never uses pickle persistence ────────────────────────────────


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        where = f"{path.name}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in PICKLING_MODULES:
                    found.append(f"{where} imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in PICKLING_MODULES:
                found.append(f"{where} imports from {node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            owner = func.value if isinstance(func, ast.Attribute) else None
            owner_name = owner.id if isinstance(owner, ast.Name) else ""
            if name in PICKLING_CALLS:
                found.append(f"{where} calls {name}")
            if owner_name in PICKLING_MODULES:
                found.append(f"{where} calls {owner_name}.{name}")
            for kw in node.keywords:
                if kw.arg == "allow_pickle" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False
                ):
                    found.append(f"{where} passes allow_pickle={ast.unparse(kw.value)}")
            if name == "load" and owner_name in {"np", "numpy"}:
                explicit = any(
                    kw.arg == "allow_pickle"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False
                    for kw in node.keywords
                )
                if not explicit:
                    found.append(f"{where} calls {owner_name}.load without allow_pickle=False")
    return found


def test_no_module_uses_pickle_persistence() -> None:
    files = sorted(PACKAGE.rglob("*.py"))
    assert files
    problems = [v for path in files for v in _violations(path)]
    assert problems == []


def test_the_scan_catches_what_it_looks_for(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    source = (
        "import joblib\n"
        "import pickle as p\n"
        "from joblib import load\n"
        "import numpy as np\n"
        "joblib.dump(1, 'x')\n"
        "np.load('x.npy', allow_pickle=True)\n"
        "np.load('x.npy')\n"
        "np.load('x.npy', allow_pickle=False)\n"
        "frame.to_pickle('x')\n"
    )
    probe.write_text(source, encoding="utf-8")
    found = "\n".join(_violations(probe))
    for expected in (
        "probe.py:1 imports joblib",
        "probe.py:2 imports pickle",
        "probe.py:3 imports from joblib",
        "probe.py:5 calls joblib.dump",
        "probe.py:6 passes allow_pickle=True",
        "probe.py:7 calls np.load without allow_pickle=False",
        "probe.py:9 calls to_pickle",
    ):
        assert expected in found
    assert "probe.py:8" not in found


# ── Round trips ─────────────────────────────────────────────────────────────

DOCS = [
    "alpha beta gamma and delta waves in alpha beta systems",
    "gamma delta rays, gamma delta decay; alpha particles",
    "beta waves and gamma bursts under beta blockers",
    "epsilon zeta eta theta over alpha beta gamma delta",
]
NEW_DOCS = ["alpha beta waves near gamma delta", "theta and eta, zeta; beta gamma"]
VOCAB = sorted({"alpha beta", "gamma delta", "beta", "gamma", "waves", "theta", "zeta eta"})


def _same_sparse(a, b) -> None:
    a, b = a.tocsr(), b.tocsr()
    assert a.shape == b.shape and a.dtype == b.dtype
    assert np.array_equal(a.indptr, b.indptr) and np.array_equal(a.indices, b.indices)
    assert np.array_equal(a.data, b.data)


def test_vectorizer_round_trip(tmp_path: Path) -> None:
    from sklearn.feature_extraction.text import TfidfVectorizer

    vec = TfidfVectorizer(lowercase=True, vocabulary=VOCAB, ngram_range=(1, 4))
    vec.fit_transform(DOCS)
    path = tmp_path / "vectorizer.json"
    model_files.save_vectorizer(vec, path)
    back = model_files.load_vectorizer(path)

    assert list(back.get_feature_names_out()) == list(vec.get_feature_names_out())
    assert back.idf_.dtype == vec.idf_.dtype and np.array_equal(back.idf_, vec.idf_)
    assert back.get_params()["ngram_range"] == (1, 4)
    _same_sparse(back.transform(NEW_DOCS + DOCS), vec.transform(NEW_DOCS + DOCS))
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["format"] == model_files.FORMAT and doc["kind"] == "vectorizer"
    assert model_files.arrays_file(path).exists()


def test_vectorizer_with_a_custom_callable_is_refused(tmp_path: Path) -> None:
    from sklearn.feature_extraction.text import TfidfVectorizer

    vec = TfidfVectorizer(vocabulary=VOCAB, tokenizer=str.split, token_pattern=None).fit(DOCS)
    with pytest.raises(ValueError, match="custom tokenizer"):
        model_files.save_vectorizer(vec, tmp_path / "vectorizer.json")


def _lexical(dense: bool = False) -> LexicalData:
    rng = np.random.default_rng(3)
    X = sparse.random(6, 9, density=0.4, random_state=4, format="csr")
    X_tf = X.copy()
    X_tf.data = rng.random(X_tf.nnz)
    meta = pd.DataFrame(
        {
            "last_name": ["Ames", "Bell", "Cole", "Dunn", "Egan", "Ford"],
            "unit": ["U1", "U1", "U2", "U2", "U3", "NA"],
            "start_year": [2001.0, np.nan, 1999.5, float("inf"), 2010.0, 2011.0],
            "n_docs": np.arange(6, dtype=np.int64),
            "active": [True, False, True, True, False, True],
            "note": pd.Series(["x", np.nan, "", "y", np.nan, "z"], dtype=object),
            "tag": pd.Series(["a", None, "b", "c", None, "d"], dtype="string"),
        }
    )
    meta["id"] = meta["last_name"].str.lower()
    return LexicalData(
        X=X.toarray() if dense else X,
        terms=[f"term {i}" for i in range(9)],
        individuals=list(meta["id"]),
        meta_ind=meta,
        X_tf=X_tf.toarray() if dense else X_tf,
    )


@pytest.mark.parametrize("dense", [False, True])
def test_lexical_data_round_trip(tmp_path: Path, dense: bool) -> None:
    data = _lexical(dense)
    path = tmp_path / "lexical_data.json"
    model_files.save_lexical_data(data, path)
    back = model_files.load_lexical_data(path)

    if dense:
        assert np.array_equal(back.X, data.X) and back.X.dtype == data.X.dtype
        assert np.array_equal(back.X_tf, data.X_tf)
    else:
        assert isinstance(back.X, sparse.csr_matrix)
        _same_sparse(back.X, data.X)
        _same_sparse(back.X_tf, data.X_tf)
    assert back.terms == data.terms and back.individuals == data.individuals
    pd.testing.assert_frame_equal(back.meta_ind, data.meta_ind, check_exact=True)
    assert list(back.meta_ind.dtypes) == list(data.meta_ind.dtypes)


def test_person_table_read_from_a_file_keeps_its_types(tmp_path: Path) -> None:
    """A roster read from CSV (string columns of the installed pandas) round-trips."""
    csv = tmp_path / "persons.csv"
    csv.write_text("last_name,first_name,unit,rank\nAmes,Ada,U1,\nBell,Bo,U2,r2\n", "utf-8")
    meta = pd.read_csv(csv, dtype={"unit": str})
    data = LexicalData(
        X=sparse.csr_matrix(np.eye(2)), terms=["a", "b"], individuals=["p1", "p2"], meta_ind=meta
    )
    model_files.save_lexical_data(data, tmp_path / "lexical_data.json")
    back = model_files.load_lexical_data(tmp_path / "lexical_data.json")
    pd.testing.assert_frame_equal(back.meta_ind, meta, check_exact=True)
    assert back.X_tf is None


def test_embeddings_round_trip_keeps_values_and_memory_order(tmp_path: Path) -> None:
    rng = np.random.default_rng(5)
    Z_terms = np.asfortranarray(rng.normal(size=(7, 3)))
    emb = Embeddings(Z_ind=rng.normal(size=(4, 3)), Z_terms=Z_terms, umap_ind=None, umap_terms=None)
    path = tmp_path / "embeddings.json"
    model_files.save_embeddings(emb, path)
    back = model_files.load_embeddings(path)
    assert back.umap_ind is None and back.umap_terms is None
    assert np.array_equal(back.Z_ind, emb.Z_ind) and np.array_equal(back.Z_terms, Z_terms)
    assert back.Z_terms.flags.f_contiguous

    emb.umap_ind = rng.normal(size=(4, 2)).astype(np.float32)
    emb.umap_terms = rng.normal(size=(7, 2)).astype(np.float32)
    model_files.save_embeddings(emb, path)
    back = model_files.load_embeddings(path)
    assert back.umap_ind.dtype == np.float32 and np.array_equal(back.umap_ind, emb.umap_ind)
    assert np.array_equal(back.umap_terms, emb.umap_terms)


def test_equal_models_give_equal_files(tmp_path: Path) -> None:
    data = _lexical()
    model_files.save_lexical_data(data, tmp_path / "a.json")
    model_files.save_lexical_data(data, tmp_path / "b.json")
    assert (tmp_path / "a.npz").read_bytes() == (tmp_path / "b.npz").read_bytes()


def test_svd_round_trip(tmp_path: Path) -> None:
    from sklearn.decomposition import TruncatedSVD
    from sklearn.preprocessing import normalize

    data = _lexical()
    fitted = TruncatedSVD(n_components=3, random_state=42).fit(normalize(data.X))
    path = tmp_path / "svd.json"
    model_files.save_svd(fitted, path)
    back = model_files.load_svd(path)
    for name in (
        "components_",
        "singular_values_",
        "explained_variance_",
        "explained_variance_ratio_",
    ):
        assert np.array_equal(getattr(back, name), getattr(fitted, name))
    assert back.get_params() == fitted.get_params()
    X_new = normalize(sparse.random(3, 9, density=0.5, random_state=8, format="csr"))
    assert np.array_equal(back.transform(X_new), fitted.transform(X_new))
    assert np.array_equal(back.transform(X_new.toarray()), fitted.transform(X_new.toarray()))


def test_svd_stage_stores_its_model(tmp_path: Path) -> None:
    path = tmp_path / "svd.json"
    emb = reducers.compute_svd_embeddings(_lexical(), n_components=3, model_path=path)
    back = model_files.load_svd(path)
    assert np.array_equal(back.components_.T * back.singular_values_, emb.Z_terms)


def _umap_kw() -> dict:
    return {"n_neighbors": 5, "min_dist": 0.1, "n_components": 2, "metric": "cosine"}


@pytest.mark.parametrize("layout", ["researcher", "researcher_concepts", "joint"])
def test_umap_model_is_refitted_to_the_same_map(tmp_path: Path, layout: str) -> None:
    pytest.importorskip("umap")
    rng = np.random.default_rng(6)
    Z_ind, Z_terms = rng.normal(size=(24, 6)), rng.normal(size=(40, 6))
    kw = {**_umap_kw(), "random_state": 3}
    if layout == "researcher":
        ind, terms, model = reducers.fit_researcher_umap(Z_ind, Z_terms, **kw)
    elif layout == "researcher_concepts":
        anchors = rng.normal(size=(4, 6))
        ind, terms, model = reducers.fit_anchored_umap(Z_ind, Z_terms, anchors, **kw)
    else:
        labels = rng.integers(-1, 3, size=40)
        ind, terms, model = reducers.fit_joint_umap(
            Z_ind, Z_terms, term_cluster_labels=labels, **kw
        )
    path = tmp_path / "umap.json"
    model_files.save_layout_model(model, path)
    back = model_files.load_layout_model(path)

    assert isinstance(back, reducers.UmapModel) and back is not model
    assert back.params == model.params
    assert back.embedding.dtype == model.embedding.dtype
    assert np.array_equal(back.embedding[: len(Z_ind)], ind)
    if layout != "joint":
        assert np.array_equal(back.replay_output, terms)
    new = rng.normal(size=(5, 6))
    assert np.array_equal(back.transform(new), model.transform(new))


def test_layout_stage_stores_a_model_that_reloads(tmp_path: Path) -> None:
    pytest.importorskip("umap")
    rng = np.random.default_rng(10)
    emb = Embeddings(
        Z_ind=rng.normal(size=(20, 5)),
        Z_terms=rng.normal(size=(30, 5)),
        umap_ind=None,
        umap_terms=None,
    )
    path = tmp_path / "umap.json"
    out = reducers.compute_umap(emb, **_umap_kw(), random_state=4, model_path=path)
    back = model_files.load_layout_model(path)
    assert np.array_equal(back.embedding, out.umap_ind)
    assert np.array_equal(back.transform(emb.Z_terms), out.umap_terms)


def test_umap_model_that_is_not_reproduced_exactly_is_kept_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pytest.importorskip("umap")
    rng = np.random.default_rng(7)
    params = {**_umap_kw(), "random_state": 3}
    model = reducers.UmapModel.fit(
        params, rng.normal(size=(20, 5)), replay_input=rng.normal(size=(6, 5))
    )
    model.embedding = model.embedding.copy()
    model.embedding[0, 0] += 1.0  # a stored map the fit here does not give exactly
    stored = model.embedding.copy()
    path = tmp_path / "umap.json"
    model_files.save_layout_model(model, path)
    with caplog.at_level("WARNING", logger="cartolex.atlas.model_files"):
        back = model_files.load_layout_model(path)
    assert "does not reproduce the stored map exactly" in caplog.text
    np.testing.assert_array_equal(back.embedding, stored)  # the stored map stays the map
    assert back.refit_deviation["relative_max_displacement"] > 0
    assert back.transform(rng.normal(size=(2, 5))).shape == (2, 2)


def test_umap_model_reproduced_exactly_has_no_deviation(tmp_path: Path) -> None:
    pytest.importorskip("umap")
    rng = np.random.default_rng(7)
    params = {**_umap_kw(), "random_state": 3}
    model = reducers.UmapModel.fit(params, rng.normal(size=(20, 5)))
    path = tmp_path / "umap.json"
    model_files.save_layout_model(model, path)
    assert model_files.load_layout_model(path).refit_deviation is None


def test_anchored_tsne_round_trip(tmp_path: Path) -> None:
    rng = np.random.default_rng(9)
    Z_ind, Z_terms = rng.normal(size=(25, 8)), rng.normal(size=(60, 8))
    ind, terms, model = reducers.fit_anchored_tsne(
        Z_ind, Z_terms, rng.normal(size=(5, 8)), n_neighbors=5, metric="cosine", random_state=7
    )
    path = tmp_path / "umap.json"
    model_files.save_layout_model(model, path)
    back = model_files.load_layout_model(path)

    assert isinstance(back, reducers.AnchoredTSNE) and back.get_params() == model.get_params()
    assert np.array_equal(back.embedding_, model.embedding_)
    assert np.array_equal(back.reference_, model.reference_)
    assert np.array_equal(back.embedding_[: len(Z_ind)], ind)
    assert np.array_equal(back.transform(Z_terms, jitter=True), terms)
    new = rng.normal(size=(4, 8))
    assert np.array_equal(back.transform(new), model.transform(new))


# ── Old files, damaged files, crafted files ─────────────────────────────────


def test_old_model_file_is_named_and_never_read(tmp_path: Path) -> None:
    legacy = tmp_path / "svd.joblib"
    legacy.write_bytes(b"not read")
    with pytest.raises(ModelFileError) as info:
        model_files.load_svd(tmp_path / "svd.json")
    message = str(info.value)
    assert str(legacy) in message and "SVD stage" in message
    with pytest.raises(FileNotFoundError):
        model_files.load_svd(tmp_path / "other.json")


def test_a_stage_names_an_old_model_file(tmp_path: Path) -> None:
    from cartolex.atlas import driver
    from cartolex.context import RunContext

    ctx = RunContext.for_workspace(tmp_path)
    ctx.paths.models_dir.mkdir(parents=True)
    old = model_files.legacy_file(ctx.paths.lexical_data_json)
    old.write_bytes(b"old")
    with pytest.raises(ModelFileError, match="lexical_data.joblib"):
        driver.run_umap(ctx)
    with pytest.raises(ModelFileError, match="lexical_data.joblib"):
        driver.run_clustering(ctx)


class _Payload:
    """Unpickling this object opens (creates) the file named at construction."""

    def __init__(self, target: Path) -> None:
        self.target = str(target)

    def __reduce__(self):
        return (open, (self.target, "w"))


def _descriptor_for(path: Path, npz_bytes: bytes, entries: dict) -> None:
    npz = model_files.arrays_file(path)
    npz.write_bytes(npz_bytes)
    doc = {
        "format": model_files.FORMAT,
        "kind": "svd",
        "params": {"n_components": 2},
        "n_features_in": 3,
        "arrays": {
            "file": npz.name,
            "sha256": hashlib.sha256(npz_bytes).hexdigest(),
            "entries": entries,
        },
    }
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_a_crafted_pickle_is_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    payload = pickle.dumps(_Payload(marker))
    pickle.loads(payload)  # the control: unpickling it does run code
    assert marker.exists()
    marker.unlink()

    # 1. As the model file of an earlier release.
    (tmp_path / "svd.joblib").write_bytes(payload)
    with pytest.raises(ModelFileError):
        model_files.load_svd(tmp_path / "svd.json")

    # 2. As the array file, with a descriptor that vouches for it.
    entry = {"dtype": "|O", "shape": []}
    _descriptor_for(tmp_path / "svd.json", payload, {"components": entry})
    with pytest.raises(ModelFileError):
        model_files.load_svd(tmp_path / "svd.json")

    # 3. As an object array inside a well-formed array file.
    buffer = io.BytesIO()
    np.savez(buffer, components=np.array([_Payload(marker)], dtype=object))
    entry = {"dtype": "|O", "shape": [1]}
    _descriptor_for(tmp_path / "svd.json", buffer.getvalue(), {"components": entry})
    with pytest.raises(ModelFileError):
        model_files.load_svd(tmp_path / "svd.json")

    assert not marker.exists()


def test_arrays_from_another_run_are_refused(tmp_path: Path) -> None:
    model_files.save_lexical_data(_lexical(), tmp_path / "a.json")
    model_files.save_lexical_data(_lexical(dense=True), tmp_path / "b.json")
    (tmp_path / "a.npz").write_bytes((tmp_path / "b.npz").read_bytes())
    with pytest.raises(ModelFileError, match="another run"):
        model_files.load_lexical_data(tmp_path / "a.json")


def test_a_descriptor_of_another_kind_is_refused(tmp_path: Path) -> None:
    model_files.save_lexical_data(_lexical(), tmp_path / "lexical_data.json")
    with pytest.raises(ModelFileError, match="lexical_data"):
        model_files.load_embeddings(tmp_path / "lexical_data.json")
