# SPDX-License-Identifier: MIT
"""Layering guard: the engine imports only what it is allowed to.

``cartolex.lexicon`` and ``cartolex.atlas`` form the reusable engine (corpus
contract in → lexicon / subfields / atlas out). This is an **allowlist**: every
module they import must be one of

* the standard library;
* a third-party distribution declared in ``[project] dependencies``;
* ``cartolex._data`` (the packaged data), or the other engine package;
* ``cartolex.context`` (the run context), for type annotations or inside a
  function — never at module level, so importing an engine module never
  imports the context (which builds its defaults from the engine, lazily).

Anything else — the demo generator, an application, collection code, a site
builder, a web framework — fails the test, so the engine stays reusable on any
corpus.

The collection package ``cartolex.collect`` has an allowlist of its own: the
project format (``cartolex.project``), the packaged data and two engine
helpers (PDF text and language detection). The demo world and its fake
services are for tests only: collection never imports them, and they never
import collection, so the fakes stay an independent picture of the services. Both directions between the two engine packages exist today (the atlas
reads lexicon helpers at module level; two lexicon modules import atlas
helpers inside functions).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ENGINE_PACKAGES = ("cartolex.lexicon", "cartolex.atlas")

#: Modules inside ``cartolex`` the engine may import (the module or its children).
ALLOWED_INTERNAL = ("cartolex.lexicon", "cartolex.atlas", "cartolex._data")
#: Modules the engine may import only for annotations or inside a function.
ALLOWED_FOR_TYPES = ("cartolex.context",)

#: Import name of a declared distribution, where it differs from the distribution name.
IMPORT_NAMES = {
    "pdfminer.six": "pdfminer",
    "scikit-learn": "sklearn",
    "umap-learn": "umap",
}


def _declared_imports() -> set[str]:
    """Top-level import names of the distributions in ``[project] dependencies``."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if sys.version_info >= (3, 11):
        import tomllib

        deps = tomllib.loads(text)["project"]["dependencies"]
    else:  # pragma: no cover - Python 3.10: read the list without a TOML parser
        block = text.split("\ndependencies = [", 1)[1].split("\n]", 1)[0]
        deps = re.findall(r'^\s*"([^"]+)"', block, flags=re.MULTILINE)
    names = set()
    for dep in deps:
        dist = re.split(r"[<>=!~;\[ ]", dep, maxsplit=1)[0].strip()
        names.add(IMPORT_NAMES.get(dist, dist))
    return names


#: What the collection package may import inside ``cartolex``.
COLLECT_ALLOWED = (
    "cartolex.collect",
    "cartolex.project",
    "cartolex._data",
    "cartolex.lexicon.pdf_text",
    "cartolex.lexicon.lang_utils",
)


def _module_of(py_file: Path, root: Path) -> str:
    parts = list(py_file.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _deferred_nodes(tree: ast.AST) -> set[int]:
    """Ids of the nodes run later than import: in a function, or in ``if TYPE_CHECKING:``."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        blocks: list[ast.AST] = []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            blocks = list(node.body)
        elif isinstance(node, ast.If):
            test = node.test
            name = test.id if isinstance(test, ast.Name) else getattr(test, "attr", None)
            if name == "TYPE_CHECKING":
                blocks = list(node.body)
        for child in blocks:
            inside.update(id(n) for n in ast.walk(child))
    return inside


def _imports(py_file: Path, root: Path = REPO_ROOT, *, at_import: bool = False) -> set[str]:
    """Absolute names of every module *py_file* imports (relative imports resolved).

    With *at_import*, only the imports run when the module is imported (not those
    inside a function or an ``if TYPE_CHECKING:`` block).
    """
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    module = _module_of(py_file, root)
    package = module if py_file.name == "__init__.py" else module.rpartition(".")[0]
    type_only = _deferred_nodes(tree) if at_import else set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in type_only:
            continue
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            if node.module is None:
                # ``from . import x``: each imported name is a module of the package.
                names.update(f"{base}.{alias.name}" for alias in node.names)
            else:
                names.add(base)
    return names


def _within(name: str, modules: tuple[str, ...]) -> bool:
    return any(name == ok or name.startswith(ok + ".") for ok in modules)


def _allowed(name: str, declared: set[str], *, for_types: bool = False) -> bool:
    root = name.split(".")[0]
    if root == "cartolex":
        return _within(name, ALLOWED_INTERNAL) or (for_types and _within(name, ALLOWED_FOR_TYPES))
    return root in sys.stdlib_module_names or root in declared


def _engine_files() -> list[Path]:
    files: list[Path] = []
    for package in ENGINE_PACKAGES:
        pkg_dir = REPO_ROOT.joinpath(*package.split("."))
        assert pkg_dir.is_dir(), f"engine package missing: {package} (guard would be vacuous)"
        files.extend(sorted(pkg_dir.rglob("*.py")))
    return files


def test_engine_imports_only_allowed_modules() -> None:
    declared = _declared_imports()
    violations = [
        f"{py_file.relative_to(REPO_ROOT)} imports {name}"
        for py_file in _engine_files()
        for name in sorted(_imports(py_file))
        if not _allowed(name, declared, for_types=True)
    ]
    assert not violations, "Engine layering violations:\n" + "\n".join(violations)


def test_the_run_context_is_never_imported_at_module_level() -> None:
    declared = _declared_imports()
    violations = [
        f"{py_file.relative_to(REPO_ROOT)} imports {name} at module level"
        for py_file in _engine_files()
        for name in sorted(_imports(py_file, at_import=True))
        if not _allowed(name, declared)
    ]
    assert not violations, "Engine layering violations:\n" + "\n".join(violations)


def test_the_guard_rejects_what_it_must() -> None:
    declared = _declared_imports()
    for name in (
        "cartolex.demo",
        "cartolex.demo.generator",
        "cartolex.build",
        "cartolex.project",
        "cartolex.collect",
        "cartolex.collect.http",
        "cartolex",
        "fastapi",
        "playwright",
    ):
        assert not _allowed(name, declared), name
    for name in ("json", "numpy", "sklearn.decomposition", "umap", "cartolex._data"):
        assert _allowed(name, declared), name
    assert _allowed("cartolex.context", declared, for_types=True)
    assert not _allowed("cartolex.context", declared)


def test_relative_imports_are_resolved(tmp_path: Path) -> None:
    """``from ..demo import x`` inside an engine module is seen as ``cartolex.demo``."""
    pkg = tmp_path / "cartolex" / "lexicon"
    pkg.mkdir(parents=True)
    probe = pkg / "probe.py"
    probe.write_text(
        "from typing import TYPE_CHECKING\n"
        "from ..demo import generator\n"
        "from . import utils\n"
        "if TYPE_CHECKING:\n"
        "    from ..context import RunContext\n",
        encoding="utf-8",
    )
    assert _imports(probe, root=tmp_path) == {
        "typing",
        "cartolex.demo",
        "cartolex.lexicon.utils",
        "cartolex.context",
    }
    assert "cartolex.context" not in _imports(probe, root=tmp_path, at_import=True)


def _package_files(package: str) -> list[Path]:
    pkg_dir = REPO_ROOT.joinpath(*package.split("."))
    assert pkg_dir.is_dir(), f"package missing: {package} (guard would be vacuous)"
    return sorted(pkg_dir.rglob("*.py"))


def test_collection_imports_only_what_it_may() -> None:
    declared = _declared_imports()
    violations = []
    for py_file in _package_files("cartolex.collect"):
        for name in sorted(_imports(py_file)):
            root = name.split(".")[0]
            if root == "cartolex":
                ok = _within(name, COLLECT_ALLOWED)
            else:
                ok = root in sys.stdlib_module_names or root in declared
            if not ok:
                violations.append(f"{py_file.relative_to(REPO_ROOT)} imports {name}")
    assert not violations, "Collection layering violations:\n" + "\n".join(violations)


def test_the_demo_never_imports_collection() -> None:
    violations = [
        f"{py_file.relative_to(REPO_ROOT)} imports {name}"
        for py_file in _package_files("cartolex.demo")
        for name in sorted(_imports(py_file))
        if _within(name, ("cartolex.collect",))
    ]
    assert not violations, "\n".join(violations)
