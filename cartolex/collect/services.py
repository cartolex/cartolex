# SPDX-License-Identifier: MIT
"""The services collection talks to, and the settings a collection job runs with.

Each :class:`Service` declares where it lives, how fast cartolex may call it
(its per-host rate limit), how long each kind of answer stays fresh in the
cache, and what its access policy said when it was last checked (see
``docs/dev/collection.md``). :class:`CollectSettings` carries what a job is
given explicitly: the contact address, API keys, other endpoints (the demo
services), timeouts and the retry policy. Nothing here reads an environment
variable: the command line reads them at the edge and passes them in.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

__all__ = [
    "DAY",
    "SERVICES",
    "CollectSettings",
    "RateLimit",
    "RetryPolicy",
    "Service",
    "Timeouts",
    "local_settings",
]

DAY = 86_400.0


@dataclass(frozen=True)
class RateLimit:
    """A token bucket: *per_second* requests on average, at most *burst* at once."""

    per_second: float
    burst: int = 1

    def __post_init__(self) -> None:
        if not (math.isfinite(self.per_second) and self.per_second > 0):
            raise ValueError(f"a rate limit needs a positive rate, not {self.per_second!r}")
        if self.burst < 1:
            raise ValueError(f"a rate limit needs a burst of at least 1, not {self.burst!r}")


@dataclass(frozen=True)
class Timeouts:
    """Connect and read timeouts in seconds; both always finite (``None`` would wait forever)."""

    connect: float = 10.0
    read: float = 60.0

    def __post_init__(self) -> None:
        for name in ("connect", "read"):
            value = getattr(self, name)
            if value is None or not isinstance(value, int | float):
                raise ValueError(f"the {name} timeout must be a number of seconds, not {value!r}")
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"the {name} timeout must be finite and positive, not {value!r}")

    def as_tuple(self) -> tuple[float, float]:
        """``(connect, read)``, as ``requests`` takes them."""
        return (float(self.connect), float(self.read))


@dataclass(frozen=True)
class RetryPolicy:
    """How failures are retried: bounded attempts, exponential backoff with jitter.

    ``max_retry_after`` caps the wait a service may ask for with ``Retry-After``;
    a longer wait (a daily budget spent, a maintenance window) ends the job with
    an error saying when to try again instead of blocking it.
    """

    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    max_retry_after: float = 300.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("a retry policy needs at least one attempt")
        for name in ("base_delay", "max_delay", "max_retry_after"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= 0):
                raise ValueError(f"{name} must be finite and non-negative, not {value!r}")


@dataclass(frozen=True)
class Service:
    """One bibliographic service: where it is, how to be polite to it, how long answers last.

    *lifetimes* gives, for each kind of request, how many seconds a cached
    answer stays fresh; a kind not listed uses ``lifetimes["default"]``.
    *policy* says what the service's access policy was when last checked.
    """

    name: str
    label: str
    base_url: str
    rate: RateLimit
    lifetimes: Mapping[str, float]
    purpose: str
    policy: str = ""
    accept: str = "application/json"
    #: Query parameter carrying the contact address, when the service documents one.
    contact_param: str | None = None
    #: Whether an API key, when given, goes in an ``Authorization: Bearer`` header.
    bearer_key: bool = False

    def lifetime(self, kind: str) -> float:
        """Seconds an answer of this *kind* stays fresh in the cache."""
        return float(self.lifetimes.get(kind, self.lifetimes.get("default", DAY)))


def _frozen(mapping: Mapping[str, float]) -> Mapping[str, float]:
    return MappingProxyType(dict(mapping))


#: Every service cartolex knows. Finders that are not written yet keep a conservative
#: rate; the piece that writes a finder checks the service's policy and updates its entry.
SERVICES: Mapping[str, Service] = MappingProxyType(
    {
        "openalex": Service(
            name="openalex",
            label="OpenAlex",
            base_url="https://api.openalex.org",
            # Documented ceiling: 100 requests per second (help.openalex.org, 2026-08-19);
            # checked again 2026-10-01: at most 100 per page, cursor paging with no stated
            # limit or expiry, the snapshot advised for bulk downloads; a free key's $1 a day
            # is 10,000 list requests (10⁶ works), $0.10 without a key is 1,000 (10⁵ works).
            # the real limit is the daily budget, so cartolex stays far below it.
            rate=RateLimit(per_second=10.0, burst=10),
            lifetimes=_frozen(
                {
                    "default": 7 * DAY,
                    "person_search": 3 * DAY,
                    "institution_search": 30 * DAY,
                    "institution": 30 * DAY,
                    "institution_units": 30 * DAY,
                    "works_by_institution": 7 * DAY,
                    "author": 14 * DAY,
                    "authors_by_orcid": 7 * DAY,
                    "works_by_author": 7 * DAY,
                    "works_by_doi": 90 * DAY,
                }
            ),
            purpose="find people's author records and their works",
            policy=(
                "checked 2026-09-28: keyless use allowed with a budget of $0.10 a day, a free "
                "API key raises it to $1 a day; a lookup by id is free, a list or filter costs "
                "$0.10 and a search $1 per 1,000 calls; more than 100 requests a second or a "
                "spent budget gives 429"
            ),
            contact_param="mailto",
            bearer_key=True,
        ),
        "orcid": Service(
            name="orcid",
            label="ORCID public API",
            base_url="https://pub.orcid.org/v3.0",
            # Documented: 12 requests a second, bursts of 40; 25,000 reads a day per address
            # without registration (info.orcid.org, checked 2026-09-28).
            rate=RateLimit(per_second=8.0, burst=8),
            lifetimes=_frozen(
                {"default": 7 * DAY, "registry_works": 7 * DAY, "registry_record": 30 * DAY}
            ),
            purpose="read the works and employments a person declared in the ORCID registry",
            policy=(
                "checked 2026-09-28: anonymous public API, 12 requests a second with bursts "
                "of 40 and 25,000 reads a day per address; beyond the rate the service "
                "answers 503; JSON needs `Accept: application/json`"
            ),
        ),
        "hal": Service(
            name="hal",
            label="HAL",
            base_url="https://api.archives-ouvertes.fr",
            # The API pages state no rate limit (api.archives-ouvertes.fr/docs, checked
            # 2026-09-28): cartolex stays gentle.
            rate=RateLimit(per_second=2.0, burst=2),
            lifetimes=_frozen(
                {
                    "default": 7 * DAY,
                    "hal_works": 7 * DAY,
                    "hal_name_search": 3 * DAY,
                    "hal_structures": 30 * DAY,
                    "hal_record": 30 * DAY,
                    "hal_file": 90 * DAY,
                }
            ),
            purpose="find texts deposited in the HAL open archive, and their files",
            policy=(
                "checked 2026-09-28: the search API (Solr) states no rate limit and no key; "
                "30 rows by default, at most 10,000; cursor paging needs a sort on a unique "
                "field (`docid asc`) and `cursorMark=*`, and ends when `nextCursorMark` equals "
                "the cursor sent; metadata are under CC0 (HAL reuse conditions)"
            ),
        ),
        "scielo": Service(
            name="scielo",
            label="SciELO",
            base_url="https://articlemeta.scielo.org",
            # No rate limit stated (ArticleMeta docs, checked 2026-09-28): one a second.
            rate=RateLimit(per_second=1.0, burst=1),
            lifetimes=_frozen(
                {
                    "default": 7 * DAY,
                    "scielo_identifiers": 1 * DAY,
                    "scielo_article": 30 * DAY,
                    "scielo_fulltext": 90 * DAY,
                }
            ),
            purpose="find texts published in SciELO journals, with their abstracts in every language",
            policy=(
                "checked 2026-09-28: ArticleMeta API, no key and no rate limit stated; "
                "`article/identifiers` lists a collection or a journal (ISSN) by processing "
                "date, at most 1,000 per request with `offset`; `article` returns one article "
                "by its PID (`code`), as JSON or as SciELO PS XML (`format=xmlrsps`); the "
                "documentation recommends the monthly dumps for whole-collection loads"
            ),
        ),
        "arxiv": Service(
            name="arxiv",
            label="arXiv",
            base_url="https://export.arxiv.org/api",
            # Documented: no more than one request every three seconds, one connection.
            rate=RateLimit(per_second=1 / 3, burst=1),
            lifetimes=_frozen({"default": 30 * DAY}),
            purpose="read the abstract and the LaTeX source of preprints",
            policy=(
                "checked 2026-09-28: no more than one request every three seconds, a single "
                "connection, for every machine of the user together; e-print sources "
                "(`/e-print/<id>`: one gzipped file, or a gzipped tar) may be retrieved for "
                "personal or research use, not served again"
            ),
            accept="application/atom+xml",
        ),
        "biorxiv": Service(
            name="biorxiv",
            label="bioRxiv and medRxiv",
            base_url="https://api.biorxiv.org",
            rate=RateLimit(per_second=1.0, burst=1),
            lifetimes=_frozen({"default": 30 * DAY}),
            purpose="read the abstract and the JATS full text of life-science preprints",
            policy=(
                "checked 2026-09-28: `details/<server>/<DOI>/na/json` gives a preprint's "
                "versions with their abstract, the path of their JATS XML and the DOI of the "
                "published version; no key and no rate limit stated"
            ),
        ),
        "europepmc": Service(
            name="europepmc",
            label="Europe PMC",
            base_url="https://www.ebi.ac.uk/europepmc/webservices/rest",
            # Rapid requests are reported to be throttled with 503: one a second.
            rate=RateLimit(per_second=1.0, burst=1),
            lifetimes=_frozen({"default": 30 * DAY}),
            purpose="read the abstract and the JATS full text of open-access articles",
            policy=(
                "checked 2026-09-28: the REST documentation page did not answer (403); the "
                "service's support list (2024-12-11) says the limit applies per address; no "
                "key; `search` with `resultType=core` gives the abstract and the PMCID, "
                "`<PMCID>/fullTextXML` the JATS of open-access articles"
            ),
        ),
        "files": Service(
            name="files",
            label="open-access copies",
            base_url="",
            # Publishers and repositories each have their own rules: one request a second
            # per host, the pacing being kept per host.
            rate=RateLimit(per_second=1.0, burst=1),
            lifetimes=_frozen({"default": 90 * DAY}),
            purpose="download the open-access copy of a text that OpenAlex links to",
            policy=(
                "checked 2026-09-28: OpenAlex gives `best_oa_location.pdf_url` and each "
                "location's `pdf_url` (help pages of 2026-08-11); its own cached PDFs "
                "(content.openalex.org) need a key and are metered, and are not used"
            ),
            accept="application/pdf",
        ),
    }
)


@dataclass(frozen=True)
class CollectSettings:
    """What a collection job is given: who is calling, with which keys, where, how patiently.

    * *contact* — an e-mail address sent to the services that ask for one (the
      OpenAlex ``mailto`` parameter) and in the ``User-Agent`` header;
    * *api_keys* — per service (``{"openalex": "…"}``); never cached, never logged;
    * *endpoints* — per service, another base URL (the demo services);
    * *rates* — per service, another rate limit (the demo services answer at once);
    * *use_system_proxy* — whether the proxy settings of the system apply.
    """

    contact: str | None = None
    api_keys: Mapping[str, str] = field(default_factory=dict)
    endpoints: Mapping[str, str] = field(default_factory=dict)
    rates: Mapping[str, RateLimit] = field(default_factory=dict)
    timeouts: Timeouts = field(default_factory=Timeouts)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    use_system_proxy: bool = True
    user_agent: str = "cartolex"

    def service(self, name: str) -> Service:
        """The service *name*, with this job's endpoint and rate overrides applied."""
        try:
            base = SERVICES[name]
        except KeyError:
            raise KeyError(f"unknown service {name!r}; known: {sorted(SERVICES)}") from None
        changes: dict[str, object] = {}
        if name in self.endpoints:
            changes["base_url"] = self.endpoints[name].rstrip("/")
        if name in self.rates:
            changes["rate"] = self.rates[name]
        return replace(base, **changes) if changes else base

    def api_key(self, name: str) -> str | None:
        """The API key given for service *name*, if any."""
        key = self.api_keys.get(name)
        return key or None


def local_settings(
    endpoints: Mapping[str, str],
    *,
    timeouts: Timeouts | None = None,
    retry: RetryPolicy | None = None,
    contact: str | None = None,
    api_keys: Mapping[str, str] | None = None,
) -> CollectSettings:
    """Settings for services on this computer (the demo services): no pacing, no proxy,
    short timeouts and quick retries."""
    return CollectSettings(
        contact=contact,
        api_keys=dict(api_keys or {}),
        endpoints=dict(endpoints),
        rates={name: RateLimit(per_second=1000.0, burst=1000) for name in endpoints},
        timeouts=timeouts or Timeouts(connect=2.0, read=10.0),
        retry=retry or RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.05),
        use_system_proxy=False,
    )
