# SPDX-License-Identifier: MIT
"""cartolex's app: the web API and the interface, for one local user or a hosted service.

::

    from cartolex.app import AppSettings, create_app

    app = create_app(AppSettings(project=folder))          # an ASGI app

``cartolex`` (or ``cartolex app FOLDER``) runs it on a loopback port and opens
the browser; ``cartolex api`` serves it for hosting. A host application adds
pages, routes, stages and more with :class:`Extension` (``docs/dev/extensions.md``).
The engine packages never import this package.
"""

from .app import create_app
from .auth import ANONYMOUS, LOCAL_USER, Decision, Principal, Resource
from .collection import CollectionService, DemoCollection, UnavailableCollection
from .errors import ApiError
from .extensions import (
    Branding,
    CliVerb,
    Extension,
    ExtensionError,
    NavEntry,
    NewProject,
    StatusArea,
)
from .jobs import JobControl, JobInfo, JobRunner, LocalJobRunner
from .manifest import Manifest
from .settings import AppSettings

__all__ = [
    "ANONYMOUS",
    "LOCAL_USER",
    "ApiError",
    "AppSettings",
    "Branding",
    "CliVerb",
    "CollectionService",
    "Decision",
    "DemoCollection",
    "Extension",
    "ExtensionError",
    "JobControl",
    "JobInfo",
    "JobRunner",
    "LocalJobRunner",
    "Manifest",
    "NavEntry",
    "NewProject",
    "Principal",
    "Resource",
    "StatusArea",
    "UnavailableCollection",
    "create_app",
]
