# SPDX-License-Identifier: MIT
"""The manifest: what the interface starts from (``GET /api/app/manifest``).

The shell reads it first: the app's name and version, the branding, the
interface languages and their catalogues, the pages of the navigation, the ES
modules of the extensions, the capabilities, the open project and the CSRF
header. :class:`Manifest` is the contract, versioned by its ``format``
(``cartolex-manifest/1``); its JSON Schema is generated from the model into
``cartolex/app/schemas/manifest.schema.json`` and never edited by hand::

    python -m cartolex.app.schemas --write   # regenerate after a model change
    python -m cartolex.app.schemas --check   # fail when the stored schema is out of date

Within ``/1`` a newer cartolex may add optional keys; an interface ignores the
keys it does not know. ``docs/dev/app-manifest.md`` describes every key.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .auth import Principal
    from .runtime import Runtime

__all__ = [
    "CORE_NAV",
    "FORMAT",
    "SCHEMA_PATH",
    "Manifest",
    "build_manifest",
    "manifest_schema",
]

FORMAT = "cartolex-manifest/1"
SCHEMA_PATH = Path(__file__).with_name("schemas") / "manifest.schema.json"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppInfo(_Model):
    """The application: ``id`` and ``name`` (the host's brand, or cartolex), its version."""

    id: str
    name: str
    version: str


class Accent(_Model):
    """The accent of each theme, ``#rrggbb`` (4.5:1 against the theme's background)."""

    light: str | None = None
    dark: str | None = None


class BrandingInfo(_Model):
    """The name and logo the interface shows, and the accent when a host sets one."""

    name: str
    logo: str
    accent: Accent | None = None


class Locales(_Model):
    """The interface languages, the default, and each one's catalogues, merged in order."""

    available: list[str]
    default: str
    catalogues: dict[str, list[str]]


class NavItem(_Model):
    """A page: its id, label key, route, module, order and placement."""

    id: str
    label: str
    route: str
    module: str
    order: int
    placement: Literal["main", "settings", "hidden"] = "main"


class Capabilities(BaseModel):
    """What this app can do; extensions add flags of their own (extra keys)."""

    model_config = ConfigDict(extra="allow")

    collection: bool = Field(description="collecting texts from bibliographic services")
    ai_api: bool = Field(description="the AI clean-up by API (a key was given)")
    ai_handoff: bool = Field(description="the AI clean-up by handoff (export, then import)")
    hosted: bool = Field(description="a hosted service (many projects, a host's sign-in)")


class ProjectInfo(_Model):
    """The project the requests work on: open or not, its id and name."""

    open: bool
    id: str | None = None
    name: str | None = None


class Security(_Model):
    """The header state-changing requests send, and the cookie holding its value.

    The cookie's name carries the app instance's id (two apps on two loopback
    ports never share a cookie); cartolex always sends it.
    """

    csrf_header: str
    csrf_cookie: str | None = None


class Manifest(_Model):
    """``GET /api/app/manifest``: the contract the interface starts from."""

    format: Literal["cartolex-manifest/1"] = FORMAT
    app: AppInfo
    branding: BrandingInfo
    locales: Locales
    nav: list[NavItem]
    modules: list[str]
    capabilities: Capabilities
    project: ProjectInfo
    security: Security


#: cartolex's own pages: (id, order, placement).
CORE_NAV: tuple[tuple[str, int, str], ...] = (
    ("overview", 10, "main"),
    ("people", 20, "main"),
    ("keywords", 30, "main"),
    ("themes", 40, "main"),
    ("map", 50, "main"),
    ("share", 60, "main"),
    ("settings", 90, "settings"),
)

DEFAULT_LOGO = "/static/brand/logo.svg"
#: The page a nav entry shows when its module is missing.
PLACEHOLDER = "/static/pages/placeholder.js"


def _ext_path(ext_id: str, rel: str) -> str:
    return rel if rel.startswith("/static/") else f"/static/ext/{ext_id}/{rel}"


def _file_of(runtime: Runtime, url: str) -> Path | None:
    """The file a ``/static/…`` address serves, or ``None`` when there is none."""
    from .static_files import PACKAGE_STATIC, safe_file

    rel = url.removeprefix("/static/")
    if rel.startswith("ext/"):
        parts = rel.split("/", 2)  # ext, the extension's id, the path in its folder
        ext = runtime.extensions.by_id(parts[1]) if len(parts) == 3 else None
        if ext is None or ext.static_dir is None:
            return None
        return safe_file(ext.static_dir, parts[2])
    return safe_file(runtime.settings.static_dir or PACKAGE_STATIC, rel)


def module_or_placeholder(runtime: Runtime, entry: str, module: str) -> str:
    """*module* when its file exists, else the placeholder page (a warning is logged once)."""
    if _file_of(runtime, module) is not None:
        return module
    if (entry, module) not in runtime.warned:
        runtime.warned.add((entry, module))
        logging.getLogger("cartolex.app").warning(
            "a page's module is missing; the placeholder page stands in",
            extra={"event": "missing_module", "route": module},
        )
    return PLACEHOLDER


def build_manifest(runtime: Runtime, principal: Principal, project: dict | None) -> Manifest:
    """The manifest of *runtime*'s app, as *principal* sees it, with the open *project*."""
    from cartolex.project.project import cartolex_version

    from .security import CSRF_HEADER

    settings = runtime.settings
    combined = runtime.extensions
    brand = combined.branding
    owner = combined.branding_owner
    name = (brand.name if brand and brand.name else None) or "cartolex"
    logo = _ext_path(owner, brand.logo) if brand and brand.logo and owner else DEFAULT_LOGO
    accent = None
    if brand and (brand.accent or brand.accent_dark):
        accent = Accent(light=brand.accent, dark=brand.accent_dark)
    catalogues: dict[str, list[str]] = {}
    for locale in settings.locales:
        paths = [f"/static/i18n/{locale}.json"]
        for ext in combined.extensions:
            entry = ext.i18n.get(locale)
            if isinstance(entry, str):
                paths.append(_ext_path(ext.id, entry))
            elif entry is not None:
                paths.append(f"/static/ext/{ext.id}/i18n/{locale}.json")
        catalogues[locale] = paths
    nav = [
        NavItem(
            id=page,
            label=f"nav.{page}",
            route=f"/{page}",
            module=module_or_placeholder(runtime, page, f"/static/pages/{page}.js"),
            order=order,
            placement=placement,  # type: ignore[arg-type]
        )
        for page, order, placement in CORE_NAV
    ]
    for ext, entry in combined.nav:
        nav.append(
            NavItem(
                id=entry.id,
                label=entry.label,
                route=entry.route,
                module=module_or_placeholder(runtime, entry.id, _ext_path(ext.id, entry.module)),
                order=entry.order,
                placement=entry.placement,  # type: ignore[arg-type]
            )
        )
    nav.sort(key=lambda n: (n.order, n.id))
    modules = [_ext_path(e.id, m) for e in combined.extensions for m in e.modules]
    ai = runtime.settings.ai_access
    capabilities = Capabilities(
        collection=runtime.collection.available,
        ai_api=bool(ai is not None and (ai.api_key or ai.client_factory)),
        ai_handoff=True,
        hosted=settings.hosted,
        **combined.capabilities,
    )
    return Manifest(
        app=AppInfo(id="cartolex", name=name, version=cartolex_version()),
        branding=BrandingInfo(name=name, logo=logo, accent=accent),
        locales=Locales(
            available=list(settings.locales),
            default=settings.default_locale,
            catalogues=catalogues,
        ),
        nav=nav,
        modules=modules,
        capabilities=capabilities,
        project=ProjectInfo(**project) if project else ProjectInfo(open=False),
        security=Security(csrf_header=CSRF_HEADER, csrf_cookie=runtime.csrf_cookie),
    )


def manifest_schema() -> dict:
    """The JSON Schema (draft 2020-12) of :class:`Manifest`."""
    schema = Manifest.model_json_schema(mode="serialization")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://cartolex.github.io/schemas/app/1/manifest.schema.json",
        **schema,
    }
