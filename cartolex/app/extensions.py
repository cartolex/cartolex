# SPDX-License-Identifier: MIT
"""The extension API (V2-002): what a host application adds to cartolex's app.

An :class:`Extension` is a plain dataclass a host passes to
:func:`cartolex.app.create_app` (and to :func:`cartolex.cli.main`); cartolex
never looks for extensions by itself. Each field is optional except ``id``;
``docs/dev/extensions.md`` describes every field with an example.

:func:`combine` checks a list of extensions and merges them into one
:class:`Combined` view: ids are unique, a branding, a settings folder name and
a prompt folder come from one extension at most, nav entries and routes do not
collide, and stage declarations fit cartolex's registry.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import APIRouter
    from starlette.middleware import Middleware

    from cartolex.build import Registry, Stage
    from cartolex.project import Project
    from cartolex.project.models import Overlay, Slot

__all__ = [
    "PLACEMENTS",
    "Branding",
    "CliVerb",
    "Combined",
    "Extension",
    "ExtensionError",
    "NavEntry",
    "NewProject",
    "StatusArea",
    "combine",
    "contrast",
]

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ROUTE = re.compile(r"^/[A-Za-z0-9/_-]*$")
#: Where a nav entry goes: the main navigation, the settings, or nowhere (routed only).
PLACEMENTS = ("main", "settings", "hidden")
#: The backgrounds an accent is shown on, light and dark (the interface's tokens).
LIGHT_BACKGROUNDS = ("#ffffff", "#f4f4f4")
DARK_BACKGROUNDS = ("#121212", "#1d1d1d")
#: The least contrast an accent keeps with each background (WCAG text contrast).
MIN_CONTRAST = 4.5


class ExtensionError(ValueError):
    """An extension, or a set of them, does not fit; the message says which and why."""


@dataclass(frozen=True)
class NavEntry:
    """A page in the navigation.

    ``label`` is a catalogue key (``nav.reports``), ``route`` the page's path
    (``/reports``), ``module`` the ES module that renders it: a path relative
    to the extension's ``static_dir`` (``pages/reports.js``), or an absolute
    ``/static/…`` path. ``order`` sorts the entries (cartolex's own use 10 to
    90); ``placement`` is ``main``, ``settings`` or ``hidden``.
    """

    id: str
    label: str
    route: str
    module: str
    order: int = 100
    placement: str = "main"


@dataclass(frozen=True)
class Branding:
    """The name, logo and accent the interface shows.

    ``logo`` is a path relative to the extension's ``static_dir``, or an
    absolute ``/static/…`` path. ``accent`` (light theme) and ``accent_dark``
    are ``#rrggbb`` colours; each must keep a contrast of 4.5:1 with the
    theme's backgrounds, the interface tokens' limit.
    """

    name: str | None = None
    logo: str | None = None
    accent: str | None = None
    accent_dark: str | None = None


@dataclass(frozen=True)
class StatusArea:
    """An extra area of the project state (``GET /api/project/state``).

    ``stages`` are stage ids whose states the area sums up; ``probe``, when
    given, returns the area's own items (``[{"id", "state", "label", "reasons"}]``,
    states as the build's keys) from the open project.
    """

    id: str
    label: str
    stages: tuple[str, ...] = ()
    probe: Callable[[Project], list[dict[str, Any]]] | None = None


@dataclass(frozen=True)
class CliVerb:
    """A verb of the ``cartolex`` command: ``configure`` adds its arguments, ``run`` runs it."""

    name: str
    help: str
    run: Callable[[argparse.Namespace], int]
    configure: Callable[[argparse.ArgumentParser], None] | None = None


@dataclass(frozen=True)
class NewProject:
    """What a project is created with, as an identity provider sees it."""

    name: str
    domain_title: str
    domain_description: str
    languages: tuple[str, ...]
    principal_id: str


@dataclass(frozen=True)
class Extension:
    """What a host application adds to the app. See ``docs/dev/extensions.md``."""

    id: str
    #: FastAPI routers, mounted under ``/api/ext/<id>/``.
    routers: tuple[APIRouter, ...] = ()
    #: A folder served at ``/static/ext/<id>/``.
    static_dir: Path | None = None
    #: ES modules (paths in ``static_dir``) the shell imports; each exports ``register(api)``.
    modules: tuple[str, ...] = ()
    #: Override catalogues per locale: a file in ``static_dir``, or the messages themselves.
    i18n: Mapping[str, str | Mapping[str, str]] = field(default_factory=dict)
    branding: Branding | None = None
    nav: tuple[NavEntry, ...] = ()
    #: Declarations replacing cartolex's stages of the same id (a runner of one's own).
    stages: tuple[Stage, ...] = ()
    #: Changes to cartolex's stage declarations: ``{"themes.group": {"defaults": {…}}}``.
    stage_patches: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Corpus slots every new project gets.
    corpus_slots: tuple[Slot, ...] = ()
    #: Fills a new project's identity (``domain_title``, ``domain_description``, ``ai``).
    identity_provider: Callable[[NewProject], Mapping[str, Any]] | None = None
    #: Projected sets every new project gets.
    overlay_sets: tuple[Overlay, ...] = ()
    #: Extra areas in the project state.
    status_keys: tuple[StatusArea, ...] = ()
    #: Capabilities the interface reads from the manifest (``{"reports": True}``).
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    #: Starlette middlewares, added inside cartolex's own (after the host and security checks).
    middlewares: tuple[Middleware, ...] = ()
    #: The name of the local settings folder (default ``cartolex``).
    settings_dir_name: str | None = None
    #: A folder of prompt templates replacing the packaged ones.
    prompt_dir: Path | None = None
    #: Function words added or removed on top of every project's: ``{"add": {"en": […]}}``.
    stopword_overlay: Mapping[str, Mapping[str, Sequence[str]]] = field(default_factory=dict)
    #: Verbs of the ``cartolex`` command.
    cli: tuple[CliVerb, ...] = ()
    #: Called each time the app opens a project.
    on_project_open: Callable[[Project], None] | None = None
    #: Called once on a project the app has just created.
    project_init: Callable[[Project], None] | None = None


# ── colours ──────────────────────────────────────────────────────────────────


def _luminance(colour: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """The WCAG contrast ratio of two ``#rrggbb`` colours."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _check_accent(ext: str, name: str, colour: str | None, backgrounds: tuple[str, ...]) -> None:
    if colour is None:
        return
    if not _COLOUR.match(colour):
        raise ExtensionError(f"extension {ext!r}: branding.{name} {colour!r} is not #rrggbb")
    low = min(contrast(colour, bg) for bg in backgrounds)
    if low < MIN_CONTRAST:
        raise ExtensionError(
            f"extension {ext!r}: branding.{name} {colour} has a contrast of {low:.2f}:1 with the "
            f"theme's background; the interface needs at least {MIN_CONTRAST}:1"
        )


# ── combining ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Combined:
    """Every extension merged: what the app, the manifest and the build read."""

    extensions: tuple[Extension, ...] = ()
    branding: Branding | None = None
    branding_owner: str | None = None
    settings_dir_name: str = "cartolex"
    prompt_dir: Path | None = None
    capabilities: dict[str, bool] = field(default_factory=dict)
    stopword_overlay: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def by_id(self, ext_id: str) -> Extension | None:
        return next((e for e in self.extensions if e.id == ext_id), None)

    @property
    def nav(self) -> list[tuple[Extension, NavEntry]]:
        return [(e, n) for e in self.extensions for n in e.nav]

    @property
    def status_areas(self) -> list[StatusArea]:
        return [a for e in self.extensions for a in e.status_keys]

    def registry(self, base: Registry) -> Registry:
        """*base* with every extension's stage declarations and patches applied."""
        return patched_registry(base, self.extensions)


def _one(extensions: Sequence[Extension], attr: str) -> tuple[Any, str | None]:
    owners = [e for e in extensions if getattr(e, attr) not in (None, "")]
    if len(owners) > 1:
        names = ", ".join(e.id for e in owners)
        raise ExtensionError(f"only one extension may set {attr}; set by: {names}")
    return (getattr(owners[0], attr), owners[0].id) if owners else (None, None)


def _relative_file(ext: Extension, rel: str, what: str) -> None:
    if rel.startswith("/static/"):
        return
    if ext.static_dir is None:
        raise ExtensionError(f"extension {ext.id!r}: {what} {rel!r} needs a static_dir")
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts or rel.startswith("."):
        raise ExtensionError(f"extension {ext.id!r}: {what} {rel!r} must stay in static_dir")


def _check(ext: Extension) -> None:
    if not _ID.match(ext.id):
        raise ExtensionError(
            f"extension id {ext.id!r}: lower-case letters, digits, '-' and '_', a letter first"
        )
    if ext.static_dir is not None and not Path(ext.static_dir).is_dir():
        raise ExtensionError(f"extension {ext.id!r}: static_dir {ext.static_dir} is not a folder")
    for module in ext.modules:
        _relative_file(ext, module, "module")
    for locale, catalogue in ext.i18n.items():
        if isinstance(catalogue, str):
            _relative_file(ext, catalogue, f"i18n[{locale}]")
        elif not all(isinstance(k, str) and isinstance(v, str) for k, v in catalogue.items()):
            raise ExtensionError(f"extension {ext.id!r}: i18n[{locale}] maps keys to texts")
    for entry in ext.nav:
        if entry.placement not in PLACEMENTS:
            raise ExtensionError(
                f"extension {ext.id!r}: nav {entry.id!r} placement {entry.placement!r} is not "
                f"one of {list(PLACEMENTS)}"
            )
        if not _ROUTE.match(entry.route) or entry.route.startswith(("/api", "/static", "/launch")):
            raise ExtensionError(
                f"extension {ext.id!r}: nav {entry.id!r} route {entry.route!r} is not a page path"
            )
        _relative_file(ext, entry.module, f"nav {entry.id!r} module")
    if ext.branding is not None:
        if ext.branding.logo:
            _relative_file(ext, ext.branding.logo, "branding.logo")
        _check_accent(ext.id, "accent", ext.branding.accent, LIGHT_BACKGROUNDS)
        _check_accent(ext.id, "accent_dark", ext.branding.accent_dark, DARK_BACKGROUNDS)
    for key, value in ext.capabilities.items():
        if not isinstance(value, bool) or not re.match(r"^[a-z][a-z0-9_]*$", key):
            raise ExtensionError(f"extension {ext.id!r}: capability {key!r} must be a flag")
    for block, per_lang in ext.stopword_overlay.items():
        if block not in ("add", "remove"):
            raise ExtensionError(f"extension {ext.id!r}: stopword_overlay has add and remove only")
        for lang, words in per_lang.items():
            if not re.match(r"^[a-z]{2}$", lang) or isinstance(words, str):
                raise ExtensionError(f"extension {ext.id!r}: stopword_overlay.{block}.{lang}")
    if ext.prompt_dir is not None and not Path(ext.prompt_dir).is_dir():
        raise ExtensionError(f"extension {ext.id!r}: prompt_dir {ext.prompt_dir} is not a folder")
    for verb in ext.cli:
        if not re.match(r"^[a-z][a-z0-9-]*$", verb.name):
            raise ExtensionError(f"extension {ext.id!r}: command verb {verb.name!r}")


#: Capabilities cartolex sets itself; an extension cannot switch them.
CORE_CAPABILITIES = ("collection", "ai_api", "ai_handoff", "hosted")


def combine(extensions: Sequence[Extension]) -> Combined:
    """Check *extensions* and merge them (raises :class:`ExtensionError`)."""
    from cartolex.build import STAGES

    exts = tuple(extensions)
    seen: set[str] = set()
    for ext in exts:
        if not isinstance(ext, Extension):
            raise ExtensionError(f"not an Extension: {ext!r}")
        _check(ext)
        if ext.id in seen:
            raise ExtensionError(f"two extensions have the id {ext.id!r}")
        seen.add(ext.id)
    routes: dict[str, str] = {}
    ids: dict[str, str] = {}
    for ext in exts:
        for entry in ext.nav:
            for key, table in ((entry.route, routes), (entry.id, ids)):
                if key in table or key in CORE_NAV_KEYS:
                    owner = table.get(key, "cartolex")
                    raise ExtensionError(
                        f"extension {ext.id!r}: nav {key!r} is already used by {owner}"
                    )
                table[key] = ext.id
    capabilities: dict[str, bool] = {}
    for ext in exts:
        for key, value in ext.capabilities.items():
            if key in CORE_CAPABILITIES:
                raise ExtensionError(f"extension {ext.id!r}: capability {key!r} is cartolex's")
            capabilities[key] = capabilities.get(key, False) or value
    overlay: dict[str, dict[str, list[str]]] = {}
    for ext in exts:
        for block, per_lang in ext.stopword_overlay.items():
            for lang, words in per_lang.items():
                bucket = overlay.setdefault(block, {}).setdefault(lang, [])
                bucket.extend(w for w in words if w not in bucket)
    branding, owner = _one(exts, "branding")
    settings_dir, _ = _one(exts, "settings_dir_name")
    if settings_dir is not None and not _ID.match(settings_dir):
        raise ExtensionError(f"settings_dir_name {settings_dir!r} is not a plain folder name")
    prompt_dir, _ = _one(exts, "prompt_dir")
    combined = Combined(
        extensions=exts,
        branding=branding,
        branding_owner=owner,
        settings_dir_name=settings_dir or "cartolex",
        prompt_dir=Path(prompt_dir) if prompt_dir else None,
        capabilities=capabilities,
        stopword_overlay=overlay,
    )
    combined.registry(STAGES)  # the stage declarations must fit cartolex's registry
    return combined


#: Page ids and routes cartolex's own navigation uses (see :mod:`cartolex.app.manifest`).
CORE_NAV_KEYS = frozenset(
    {
        "overview",
        "people",
        "keywords",
        "themes",
        "map",
        "share",
        "settings",
        "gallery",
        "/",
        "/overview",
        "/people",
        "/keywords",
        "/themes",
        "/map",
        "/share",
        "/settings",
        "/gallery",
    }
)


def patched_registry(base: Registry, extensions: Sequence[Extension]) -> Registry:
    """*base* with the extensions' stage declarations and patches, checked.

    A stage in ``Extension.stages`` replaces cartolex's declaration of the same
    id. A new stage id is refused: the project format lists the build's stages,
    and host stages are an open question of the format. A patch changes fields
    of a declaration; its ``defaults`` key sets parameter defaults.
    """
    registry = base
    for ext in extensions:
        for stage in ext.stages:
            if stage.id not in registry:
                raise ExtensionError(
                    f"extension {ext.id!r}: stage {stage.id!r} is not one of the build's stages; "
                    "an extension replaces a stage's declaration (host stages of their own "
                    "are not part of the project format yet)"
                )
            registry = registry.replace(
                stage.id,
                **{f.name: getattr(stage, f.name) for f in dataclasses.fields(stage)},
            )
        for stage_id, changes in ext.stage_patches.items():
            if stage_id not in registry:
                raise ExtensionError(f"extension {ext.id!r}: no stage {stage_id!r} to patch")
            changes = dict(changes)
            if "id" in changes:
                raise ExtensionError(f"extension {ext.id!r}: a patch cannot change a stage's id")
            defaults = changes.pop("defaults", None) or {}
            if defaults:
                stage = registry[stage_id]
                known = {p.name: p for p in stage.params}
                unknown = sorted(set(defaults) - set(known))
                if unknown:
                    raise ExtensionError(
                        f"extension {ext.id!r}: {stage_id} has no parameter(s) {unknown}"
                    )
                params = []
                for spec in stage.params:
                    if spec.name in defaults:
                        try:
                            spec = dataclasses.replace(spec, default=defaults[spec.name], rule=None)
                        except ValueError as exc:
                            raise ExtensionError(f"extension {ext.id!r}: {exc}") from exc
                    params.append(spec)
                changes["params"] = tuple(params)
            try:
                registry = registry.replace(stage_id, **changes)
            except (TypeError, ValueError) as exc:
                raise ExtensionError(f"extension {ext.id!r}: patch of {stage_id}: {exc}") from exc
    return registry
