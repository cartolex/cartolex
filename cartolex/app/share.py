# SPDX-License-Identifier: MIT
"""Sharing: the offline sites a project builds, behind a small protocol.

The site builder lands in a later version; :class:`StubSiteBuilder` lists the
builds already in ``outputs/sites/`` and answers a new build with « not
available yet ». A host can pass its own :class:`SiteBuilder` in the settings.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from .errors import ApiError

if TYPE_CHECKING:
    from cartolex.project import Project

    from .jobs import JobControl

__all__ = ["SiteBuilder", "StubSiteBuilder"]


class SiteBuilder(Protocol):
    """Builds the offline site of a project."""

    available: bool

    def builds(self, project: Project) -> list[dict[str, Any]]:
        """The builds in ``outputs/sites/``, newest first: ``[{"id", "latest"}]``."""
        ...

    def build(
        self, project: Project, options: Mapping[str, Any], control: JobControl
    ) -> Mapping[str, Any]:
        """Build a site (runs in a job)."""
        ...


class StubSiteBuilder:
    """Lists earlier builds; building a site is not available yet."""

    available = False

    def builds(self, project: Project) -> list[dict[str, Any]]:
        folder = project.layout.outputs / "sites"
        if not folder.is_dir():
            return []
        latest_file = folder / "latest"
        latest = latest_file.read_text(encoding="utf-8").strip() if latest_file.is_file() else ""
        names = sorted((p.name for p in folder.iterdir() if p.is_dir()), reverse=True)
        return [{"id": n, "latest": n == latest} for n in names]

    def build(
        self, project: Project, options: Mapping[str, Any], control: JobControl
    ) -> Mapping[str, Any]:
        raise ApiError.of("not_available")
