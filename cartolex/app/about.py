# SPDX-License-Identifier: MIT
"""What the app says about itself: its version and build, the system it runs on, its authors.

A wheel built for people to install carries a **build stamp**, ``cartolex/_data/build.json``
(``{"format": "cartolex-build/1", "commit", "date"}``), written by ``tools/build_stamp.py``
before the wheel is built and never tracked by git: two kits of the same version are told
apart by it. Run from a git checkout, the checkout's last commit is the build.
The authors and the licence come from the package's metadata (``pyproject.toml``).
"""

from __future__ import annotations

import functools
import json
import platform
import re
import subprocess
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path
from typing import Any

__all__ = ["STAMP_FILE", "about", "build_stamp", "platform_text"]

#: Where a built wheel carries its build stamp.
STAMP_FILE = Path(__file__).resolve().parents[1] / "_data" / "build.json"
#: The checkout the package runs from, when it does (an editable install, the tests).
_CHECKOUT = Path(__file__).resolve().parents[2]
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid(stamp: Any) -> dict[str, str] | None:
    if not isinstance(stamp, dict):
        return None
    commit, date = stamp.get("commit"), stamp.get("date")
    if not (isinstance(commit, str) and _SHA.match(commit)):
        return None
    if not (isinstance(date, str) and _DATE.match(date)):
        return None
    return {"commit": commit, "date": date}


def _from_git(root: Path) -> dict[str, str] | None:
    if not (root / ".git").exists() or not (root / "pyproject.toml").is_file():
        return None
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H %cs"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return _valid({"commit": out[0], "date": out[1]}) if len(out) == 2 else None


@functools.lru_cache(maxsize=1)
def build_stamp() -> dict[str, str] | None:
    """``{"commit", "date"}`` of the code running now: the checkout's last commit when it
    runs from one, else the wheel's stamp, else ``None``."""
    found = _from_git(_CHECKOUT)
    if found is not None:
        return found
    try:
        return _valid(json.loads(STAMP_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def platform_text() -> str:
    """The system the app runs on, for a diagnostic (« Linux 6.8.0 x86_64 »)."""
    return " ".join(p for p in (platform.system(), platform.release(), platform.machine()) if p)


def _names(field: str | None) -> list[str]:
    """The names of a metadata field written ``Name <address>, Name <address>``."""
    if not field:
        return []
    return [n for n in (re.sub(r"<[^>]*>", "", part).strip(' "') for part in field.split(",")) if n]


@functools.lru_cache(maxsize=1)
def _package() -> dict[str, Any]:
    try:
        meta = metadata("cartolex")
    except PackageNotFoundError:  # pragma: no cover - a bare checkout, never installed
        return {"authors": [], "licence": "", "urls": {}}
    authors = _names(meta.get("Author-email")) or _names(meta.get("Author"))
    urls = {}
    for line in meta.get_all("Project-URL") or []:
        label, _, url = line.partition(",")
        urls[label.strip().lower()] = url.strip()
    return {
        "authors": authors,
        "licence": meta.get("License-Expression") or meta.get("License") or "",
        "urls": urls,
    }


def about() -> dict[str, Any]:
    """What the About page shows: the version, the build stamp, the authors, the licence, the
    source's address and how to cite cartolex (from the package's metadata)."""
    from cartolex.project.project import cartolex_version

    package = _package()
    version = cartolex_version()
    stamp = build_stamp()
    source = package["urls"].get("source") or package["urls"].get("homepage") or ""
    authors = package["authors"]
    year = (stamp or {}).get("date", "")[:4]
    citation = ", ".join(authors) + (f" ({year})" if year else "")
    citation += f". cartolex, version {version} [software]."
    doi = package["urls"].get("doi", "")
    if doi or source:
        citation += f" Zenodo. {doi}" if doi else f" {source}"
    return {
        "name": "cartolex",
        "version": version,
        "build": stamp,
        "authors": authors,
        "licence": package["licence"],
        "source": source,
        "citation": citation.strip(),
    }
