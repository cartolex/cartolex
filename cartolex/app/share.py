# SPDX-License-Identifier: MIT
"""Sharing: the offline sites a project builds, behind a small protocol.

The app builds sites with :class:`cartolex.site.OfflineSiteBuilder` (the
default); a host can pass its own :class:`SiteBuilder` in the settings.
:class:`StubSiteBuilder` lists the builds already in ``outputs/sites/`` and
answers a new build with « not available » (a host that shares elsewhere).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .errors import ApiError

if TYPE_CHECKING:
    from cartolex.project import Project

    from .jobs import JobControl

__all__ = ["SiteBuilder", "StubSiteBuilder", "default_site_builder"]


class SiteBuilder(Protocol):
    """Builds the offline site of a project."""

    available: bool

    def builds(self, project: Project) -> list[dict[str, Any]]:
        """The builds in ``outputs/sites/``, newest first: ``[{"id", "latest", "stale"…}]``."""
        ...

    def folder(self, project: Project, build_id: str) -> Path | None:
        """The folder of a build (to open it, to zip it), or ``None``."""
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

    def folder(self, project: Project, build_id: str) -> Path | None:
        if not build_id or build_id.startswith(".") or "/" in build_id or "\\" in build_id:
            return None
        path = project.layout.outputs / "sites" / build_id
        return path if path.is_dir() else None

    def build(
        self, project: Project, options: Mapping[str, Any], control: JobControl
    ) -> Mapping[str, Any]:
        raise ApiError.of("not_available")


def default_site_builder() -> SiteBuilder:
    """cartolex's own site builder (imported when the app starts, not before)."""
    from cartolex.site import OfflineSiteBuilder

    return OfflineSiteBuilder()
