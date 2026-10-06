# SPDX-License-Identifier: MIT
"""Fingerprints of what a stage reads, and of the code that runs it.

Decision files and other small files are fingerprinted by the SHA-256 of their
bytes. A Parquet table larger than :data:`TABLE_FULL_HASH_LIMIT` is
fingerprinted by its size and its footer, which holds the schema and, for every
row group and column, the row counts, byte sizes, offsets and statistics
(minimum, maximum, null count): a check stays fast on tables of any size, and a
rewritten table is seen as changed. The code fingerprint covers the package's
source and data files; it is computed once per process.
"""

from __future__ import annotations

import functools
import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..project.files import fingerprint as file_fingerprint

if TYPE_CHECKING:
    from ..project.project import Project
    from .stages import Stage

__all__ = [
    "TABLE_FULL_HASH_LIMIT",
    "FingerprintMemo",
    "InputFile",
    "code_fingerprint",
    "input_files",
    "table_fingerprint",
]

#: Parquet files up to this size are hashed whole; larger ones by their footer.
TABLE_FULL_HASH_LIMIT = 64 * 1024 * 1024

_PARQUET_MAGIC = b"PAR1"


def table_fingerprint(path: Path) -> str | None:
    """``sha256:<hex>`` of a Parquet table: its bytes, or its size and footer when large.

    ``None`` when the file does not exist. A file too short or without the
    Parquet trailer is hashed whole.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if size <= TABLE_FULL_HASH_LIMIT:
        return file_fingerprint(path)
    with open(path, "rb") as fh:
        fh.seek(size - 8)
        trailer = fh.read(8)
        footer_len = int.from_bytes(trailer[:4], "little")
        if trailer[4:] != _PARQUET_MAGIC or footer_len + 12 > size:
            return file_fingerprint(path)
        fh.seek(size - 8 - footer_len)
        footer = fh.read(footer_len)
    h = hashlib.sha256()
    h.update(b"cartolex-parquet-footer/1\0")
    h.update(str(size).encode("ascii") + b"\0")
    h.update(footer)
    return "sha256:" + h.hexdigest()


@dataclass(frozen=True)
class InputFile:
    """A file a stage reads: its kind in ``run.json``, the path recorded there, where it is."""

    kind: str  # "source", "decision", "overlay", "base", "cache"
    path: str  # relative to the project root, with "/" separators
    location: Path
    #: For an input that is a part of a file (the merges of ``people.csv``): its own
    #: fingerprint of the file, ``None`` when the part is empty.
    digest: Callable[[Path], str | None] | None = field(default=None, compare=False)

    def fingerprint(self) -> str | None:
        if self.digest is not None:
            return self.digest(self.location)
        if self.location.suffix == ".parquet":
            return table_fingerprint(self.location)
        return file_fingerprint(self.location)


def _recorded_path(project_root: Path, location: Path) -> str:
    """The path stored in a record: relative to the project when possible."""
    try:
        return Path(os.path.relpath(location, project_root)).as_posix()
    except ValueError:  # another drive (Windows): an absolute root declared on purpose
        return location.as_posix()


def input_files(project: Project, stage: Stage) -> list[InputFile]:
    """Every file *stage* declares it reads, whether it exists or not."""
    layout = project.layout
    found = [
        InputFile("source", f"sources/tables/{name}.parquet", layout.table(name))
        for name in stage.sources
    ]
    found += [InputFile("decision", path, layout.root / path) for path in stage.decisions]
    if stage.extra_inputs is not None:
        for extra in stage.extra_inputs(project):
            if isinstance(extra, InputFile):
                found.append(extra)
                continue
            kind, location = extra
            found.append(InputFile(kind, _recorded_path(layout.root, location), Path(location)))
    return found


class FingerprintMemo:
    """Fingerprints computed once per path within one status check or build step."""

    def __init__(self) -> None:
        self._seen: dict[tuple[Path, str], str | None] = {}

    def __call__(self, item: InputFile) -> str | None:
        key = (item.location.resolve(), item.path if item.digest is not None else "")
        if key not in self._seen:
            self._seen[key] = item.fingerprint()
        return self._seen[key]

    def forget(self) -> None:
        self._seen.clear()


_CODE_SUFFIXES = (".py", ".json", ".txt", ".md")


@functools.lru_cache(maxsize=8)
def code_fingerprint(
    package: str = "cartolex", exclude: tuple[str, ...] = ("cartolex.demo",)
) -> str:
    """``sha256:<hex>`` over the source and data files of *package*, minus *exclude*.

    Computed once per process and arguments.
    """
    import importlib

    root = Path(importlib.import_module(package).__file__).resolve().parent  # type: ignore[arg-type]
    skipped = [root.joinpath(*name.split(".")[1:]) for name in exclude]
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.suffix not in _CODE_SUFFIXES or not path.is_file():
            continue
        if "__pycache__" in path.parts or any(s in path.parents for s in skipped):
            continue
        h.update(f"{package}/{path.relative_to(root).as_posix()}\0".encode())
        h.update(path.read_bytes())
    return "sha256:" + h.hexdigest()
