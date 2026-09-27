# SPDX-License-Identifier: MIT
"""Tests for the domain catalog loader — fully synthetic catalog data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cartolex.context import EnginePaths
from cartolex.lexicon.domain_catalog import (
    DomainCatalogError,
    get_domain,
    list_domains,
    load_catalog,
)

SYNTHETIC_CATALOG = {
    "domains": [
        {
            "domain_id": "01",
            "code": "01",
            "title": "Dynamique des fluides imaginaires",
            "keywords": ["turbulence", "vortex", "écoulement granulaire"],
        },
        {
            "domain_id": "02",
            "code": "02",
            "title": "Sociologie des mondes synthétiques",
            "keywords": ["stratification", "mobilité sociale"],
        },
        {
            "domain_id": "50",
            "code": "X 50",
            "title": "Interfaces fictives",
            "reference_keywords": ["interdisciplinarité"],
        },
    ],
}


def _candidates(workspace: Path) -> tuple[Path, ...]:
    """The catalogue files a workspace's run context looks for, preferred first."""
    return EnginePaths.for_workspace(workspace).domain_catalog_jsons


def _write(config_dir: Path, payload: dict, filename: str = "domain_catalog.json") -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / filename
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_catalog(tmp_path: Path):
    _write(tmp_path / "config", SYNTHETIC_CATALOG)
    cat = load_catalog(_candidates(tmp_path))
    assert set(cat) == {"01", "02", "50"}
    assert cat["01"].reference_keywords == ("turbulence", "vortex", "écoulement granulaire")
    assert cat["02"].title.startswith("Sociologie")
    # ``reference_keywords`` is accepted for ``keywords``.
    assert cat["50"].reference_keywords == ("interdisciplinarité",)
    assert cat["50"].code == "X 50"


def test_first_existing_candidate_wins(tmp_path: Path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    other = {"domains": [{"domain_id": "07", "code": "07", "title": "Informatique inventée"}]}
    second.write_text(json.dumps(other), encoding="utf-8")
    assert set(load_catalog([first, second])) == {"07"}
    first.write_text(json.dumps(SYNTHETIC_CATALOG), encoding="utf-8")
    assert "07" not in load_catalog([first, second])


def test_get_domain_known(tmp_path: Path):
    _write(tmp_path / "config", SYNTHETIC_CATALOG)
    d = get_domain(_candidates(tmp_path), "02")
    assert d.code == "02"
    assert d.title.startswith("Sociologie")


def test_get_domain_unknown_raises(tmp_path: Path):
    _write(tmp_path / "config", SYNTHETIC_CATALOG)
    with pytest.raises(DomainCatalogError):
        get_domain(_candidates(tmp_path), "99")


def test_missing_catalog_raises_filenotfound(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_catalog(_candidates(tmp_path))


def test_empty_catalog_raises_valueerror(tmp_path: Path):
    _write(tmp_path / "config", {"domains": []})
    with pytest.raises(ValueError):
        load_catalog(_candidates(tmp_path))


def test_list_domains_sorted(tmp_path: Path):
    _write(tmp_path / "config", SYNTHETIC_CATALOG)
    ids = [e.domain_id for e in list_domains(_candidates(tmp_path))]
    assert ids == sorted(ids)
