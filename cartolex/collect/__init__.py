# SPDX-License-Identifier: MIT
"""Collection: bringing people, organisations and texts into a project.

* :mod:`~cartolex.collect.http` — the one HTTP client of a collection job:
  per-host pacing, finite timeouts, retries, the ``cache/http/`` cache, cancel
  and the record of what left the computer;
* :mod:`~cartolex.collect.services` — the services cartolex talks to and the
  settings a job is given;
* :mod:`~cartolex.collect.text` — text hygiene at the boundary;
* :mod:`~cartolex.collect.tables` — the source writers: raw records in each
  slot's ``raw/`` folder, stable ids, and the six source tables rebuilt from them.

The engine never imports this package; this package imports the project format
(:mod:`cartolex.project`) and a few engine helpers (PDF text, language detection).
See ``docs/collection.md`` and ``docs/dev/collection.md``.
"""

from .http import (
    CacheMiss,
    Cancelled,
    CollectError,
    CursorPaging,
    EgressRecord,
    Fetched,
    HttpCache,
    HttpClient,
    IncompleteResults,
    MalformedResponse,
    NotFound,
    RequestRefused,
    ServiceError,
    ServiceUnavailable,
)
from .services import (
    SERVICES,
    CollectSettings,
    RateLimit,
    RetryPolicy,
    Service,
    Timeouts,
    local_settings,
)
from .tables import IdRegistry, RawWriter, SourceBuilder, read_runs, rebuild_sources

__all__ = [
    "SERVICES",
    "CacheMiss",
    "Cancelled",
    "CollectError",
    "CollectSettings",
    "CursorPaging",
    "EgressRecord",
    "Fetched",
    "HttpCache",
    "HttpClient",
    "IdRegistry",
    "IncompleteResults",
    "MalformedResponse",
    "NotFound",
    "RateLimit",
    "RawWriter",
    "RequestRefused",
    "RetryPolicy",
    "Service",
    "ServiceError",
    "ServiceUnavailable",
    "SourceBuilder",
    "Timeouts",
    "local_settings",
    "read_runs",
    "rebuild_sources",
]
