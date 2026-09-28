# SPDX-License-Identifier: MIT
"""Collection: bringing people, organisations and texts into a project.

* :mod:`~cartolex.collect.http` — the one HTTP client of a collection job:
  per-host pacing, finite timeouts, retries, the ``cache/http/`` cache, cancel
  and the record of what left the computer;
* :mod:`~cartolex.collect.services` — the services cartolex talks to and the
  settings a job is given;
* :mod:`~cartolex.collect.text` — text hygiene at the boundary;
* :mod:`~cartolex.collect.tables` — the source writers: raw records in each
  slot's ``raw/`` folder, stable ids, and the six source tables rebuilt from them;
* :mod:`~cartolex.collect.people_import` — a list of people, a folder of
  documents, a corpus; duplicates proposed, merges confirmed;
* :mod:`~cartolex.collect.resolve` — who is who: candidates, scores,
  confirmations; :mod:`~cartolex.collect.harvest` — the works of confirmed people;
  :mod:`~cartolex.collect.openalex` and :mod:`~cartolex.collect.orcid` — the requests;
* :mod:`~cartolex.collect.privacy` — what leaves the computer, before and after;
* :mod:`~cartolex.collect.hal` and :mod:`~cartolex.collect.scielo` — the HAL
  and SciELO finders (:func:`~cartolex.collect.hal.collect_hal`,
  :func:`~cartolex.collect.scielo.collect_scielo`), sharing
  :mod:`~cartolex.collect.finders`;
* :mod:`~cartolex.collect.merge` — one text per work found by several finders;
* :mod:`~cartolex.collect.providers` — better texts for works already found
  (:func:`~cartolex.collect.providers.improve_texts`).

The engine never imports this package; this package imports the project format
(:mod:`cartolex.project`) and a few engine helpers (PDF text, language detection).
See ``docs/collection.md`` and ``docs/dev/collection.md``.
"""

from .harvest import HarvestReport, harvest
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
from .people_import import (
    ImportMapping,
    ImportReport,
    confirm_merge,
    find_duplicates,
    import_corpus,
    import_folder,
    import_people,
    propose_mapping,
)
from .privacy import CollectionPlan, plan_collection, record_job
from .resolve import (
    THRESHOLD,
    Candidate,
    Resolution,
    ResolveReport,
    confirm,
    confirm_none,
    confirm_pasted,
    resolve,
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
    "THRESHOLD",
    "Candidate",
    "CollectionPlan",
    "HarvestReport",
    "ImportMapping",
    "ImportReport",
    "Resolution",
    "ResolveReport",
    "confirm",
    "confirm_merge",
    "confirm_none",
    "confirm_pasted",
    "find_duplicates",
    "harvest",
    "import_corpus",
    "import_folder",
    "import_people",
    "plan_collection",
    "propose_mapping",
    "record_job",
    "resolve",
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
