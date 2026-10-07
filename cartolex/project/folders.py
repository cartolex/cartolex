# SPDX-License-Identifier: MIT
"""Measuring and removing what a project holds on disk, never following a link.

Used where the app deletes files a person asked to delete (a project's folder, a site
build, an exported file): a link is measured and removed as a link, so what it points
to, perhaps outside the project, is never read for its size nor removed.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

__all__ = ["is_link", "remove_entry", "tree_size"]


def is_link(path: Path) -> bool:
    """A symbolic link, or a Windows junction (which most tools follow as a folder)."""
    junction = getattr(os.path, "isjunction", None)
    return path.is_symlink() or bool(junction and junction(path))


def tree_size(path: Path) -> tuple[int, int]:
    """The bytes and files under *path*, links counted as links (never followed)."""
    path = Path(path)
    if is_link(path) or not path.is_dir():
        try:
            return path.lstat().st_size, 1
        except OSError:
            return 0, 0
    total = count = 0
    for folder, dirs, files in os.walk(path, followlinks=False):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(folder, d))]:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
                count += 1
            except OSError:
                continue
    return total, count


def remove_entry(path: Path, container: Path) -> None:
    """Remove *path*, an entry directly inside *container*: a link or a file is unlinked (what
    a link points to is never touched), a folder removed with what it holds (its links
    unlinked, never followed). Refuses (``ValueError``) anything not directly inside
    *container*."""
    path = Path(path)
    if path.name in ("", ".", "..") or path.parent.resolve() != Path(container).resolve():
        raise ValueError(f"{path} is not inside {container}")
    if is_link(path):
        if path.is_dir() and not path.is_symlink():
            os.rmdir(path)  # a junction: the link goes, its target stays
        else:
            path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
