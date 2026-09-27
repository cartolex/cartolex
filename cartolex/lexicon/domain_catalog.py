# SPDX-License-Identifier: MIT
"""Domain catalog loader.

A *domain catalog* is a read-only JSON file listing the research domains a
project recognises — id, short code, title, and the reference keywords used
to anchor the LLM triage prompts (see
``cartolex.lexicon.llm_triage._resolve_domain_anchor``). The loader reads the
first existing file among the candidates it is given — for a workspace, the
context's ``paths.domain_catalog_jsons``. The catalog is optional: without
one, prompts run unanchored.

Format::

    {
      "domains": [
        {
          "domain_id": "d1",
          "code": "D1",
          "title": "Fluid dynamics",
          "keywords": ["turbulence", "vortex"]
        }
      ]
    }

``keywords`` may also be spelled ``reference_keywords``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["DomainCatalogError", "DomainEntry", "get_domain", "list_domains", "load_catalog"]


@dataclass(frozen=True)
class DomainEntry:
    """A single entry (research domain) in the catalog."""

    domain_id: str
    code: str
    title: str
    reference_keywords: tuple[str, ...]


class DomainCatalogError(KeyError):
    """Raised when a domain id is not found in the catalog."""


def _catalog_path(candidates: Sequence[Path]) -> Path | None:
    for candidate in candidates:
        if Path(candidate).exists():
            return Path(candidate)
    return None


def load_catalog(candidates: Sequence[Path]) -> dict[str, DomainEntry]:
    """Load the domain catalog from the first existing file of *candidates*.

    Raises :class:`FileNotFoundError` when no candidate exists and
    :class:`ValueError` when the file lists no domain.
    """
    path = _catalog_path(candidates)
    if path is None:
        raise FileNotFoundError(
            f"Domain catalog not found (looked for {', '.join(str(c) for c in candidates)}). "
            "Ship one with your workspace configuration to enable prompt anchoring."
        )

    with path.open("r", encoding="utf-8") as fh:
        doc = json.load(fh)

    entries = doc.get("domains", []) if isinstance(doc, dict) else []
    if not entries:
        raise ValueError(f"Domain catalog at {path} contains no entries.")

    out: dict[str, DomainEntry] = {}
    for item in entries:
        did = str(item["domain_id"])
        kws = item.get("keywords") or item.get("reference_keywords") or []
        out[did] = DomainEntry(
            domain_id=did,
            code=str(item.get("code", did)),
            title=str(item.get("title", "")),
            reference_keywords=tuple(str(k) for k in kws),
        )
    return out


def get_domain(candidates: Sequence[Path], domain_id: str) -> DomainEntry:
    """Return the catalog entry for *domain_id* (catalog read as :func:`load_catalog`).

    Raises :class:`DomainCatalogError` if not found.
    """
    catalog = load_catalog(candidates)
    did = str(domain_id).strip()
    if did not in catalog:
        raise DomainCatalogError(f"Unknown domain_id: {did!r}")
    return catalog[did]


def list_domains(candidates: Sequence[Path]) -> list[DomainEntry]:
    """Return the catalog as an ordered list (by ``domain_id``)."""
    catalog = load_catalog(candidates)
    return [catalog[k] for k in sorted(catalog.keys())]
