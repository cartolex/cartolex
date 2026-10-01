# SPDX-License-Identifier: MIT
"""The HTTP layer of collection: one :class:`HttpClient` per collection job.

It sends every request cartolex makes to a bibliographic service, and it is the
only code that does. It:

* waits its turn per service host (a token bucket, :class:`RateLimit`);
* always passes finite connect and read timeouts;
* retries 429, 5xx, timeouts, cut connections and malformed bodies with
  exponential backoff and jitter, honours ``Retry-After`` (seconds or an HTTP
  date), and gives up after a bounded number of attempts with a
  :class:`ServiceError` naming the host, the status and what to do next;
* refuses a page that is cut short: a paged list whose items do not add up to
  the count the service announced raises :class:`IncompleteResults`, never
  returns a short list;
* caches answers in the project's ``cache/http/``, keyed by method, canonical
  URL and the parameters that change the answer (never a secret), each kind of
  request with its own lifetime; failures are never cached. ``refresh``
  ignores cached answers and rewrites them; ``cache_only`` never reaches the
  network, and a miss raises :class:`CacheMiss`;
* reports progress and stops cleanly between requests when cancelled;
* records every host it contacted and the kinds of data it sent (never the
  values) in :attr:`HttpClient.egress`, for the privacy summary.
"""

from __future__ import annotations

import email.utils
import gzip
import hashlib
import json
import random
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from cartolex.project.files import atomic_write_bytes

from .services import CollectSettings, RateLimit, Service

__all__ = [
    "CACHE_FORMAT",
    "CacheMiss",
    "CacheMode",
    "Cancelled",
    "CollectError",
    "CursorPaging",
    "EgressRecord",
    "Fetched",
    "FetchedBytes",
    "HttpCache",
    "HttpClient",
    "IncompleteResults",
    "MalformedResponse",
    "NotFound",
    "Page",
    "RequestRefused",
    "ShortPage",
    "ServiceError",
    "ServiceUnavailable",
    "TokenBucket",
]

CacheMode = Literal["normal", "refresh", "cache_only"]
CACHE_MODES: tuple[str, ...] = ("normal", "refresh", "cache_only")
CACHE_FORMAT = "cartolex-http-cache/1"
#: Cache entries of answers that are not JSON (XML, PDF, archives): a JSON header line, then
#: the body's bytes, gzip-compressed together.
BYTES_CACHE_FORMAT = "cartolex-http-cache-bytes/1"

#: Query parameters that never enter a cache key or a log: they identify the caller,
#: they do not change the answer.
SECRET_PARAMS = frozenset({"api_key", "mailto", "key", "token", "access_token"})
#: Response headers kept with a cached answer.
KEPT_HEADERS = ("content-type", "last-modified", "etag")
_RETRIED_STATUS = frozenset({429, 500, 502, 503, 504})


# ── errors ───────────────────────────────────────────────────────────────────


class CollectError(RuntimeError):
    """Collection could not do what was asked; the message says why and what to do."""


class Cancelled(CollectError):
    """The job was cancelled between two requests; everything written so far is consistent."""


class CacheMiss(CollectError):
    """In ``cache_only`` mode, an answer that is not in the cache (or has expired)."""

    def __init__(self, service: str, kind: str, url: str) -> None:
        self.service, self.kind, self.url = service, kind, url
        super().__init__(
            f"no cached answer for this {kind.replace('_', ' ')} request to {service} "
            f"({url}); collect without --cache-only to fetch it"
        )


class ServiceError(CollectError):
    """A service did not give a usable answer: which host, which status, what to do next."""

    def __init__(
        self,
        host: str,
        status: int | None,
        what: str,
        advice: str,
        *,
        retry_after: float | None = None,
    ) -> None:
        self.host, self.status, self.what, self.advice = host, status, what, advice
        self.retry_after = retry_after
        shown = f"status {status}" if status is not None else "no answer"
        super().__init__(f"{host}: {what} ({shown}); {advice}")


class ServiceUnavailable(ServiceError):
    """Throttled, failing or unreachable after every attempt allowed."""


class RequestRefused(ServiceError):
    """The service refused the request itself (a 4xx other than 429); retrying would not help."""


class NotFound(RequestRefused):
    """The service has no such record (404)."""


class MalformedResponse(ServiceError):
    """The answer could not be read (cut, not JSON, or not the documented shape)."""


class IncompleteResults(ServiceError):
    """A paged list ended before the count the service announced: a page was cut short."""


class ShortPage(ValueError):
    """A page of a paged list holds fewer items than the count announced leaves for it (it was
    cut short, or the list said it ended too early): asked again, as a malformed answer is."""


# ── small pieces ─────────────────────────────────────────────────────────────


class TokenBucket:
    """Per-host pacing: *rate* tokens a second, at most *burst* saved up.

    :meth:`reserve` takes one token and returns how long to wait before using it
    (0 when one is available), so the caller sleeps outside the bucket.
    """

    def __init__(self, rate: RateLimit, clock: Callable[[], float]) -> None:
        self.rate = rate
        self._clock = clock
        self._tokens = float(rate.burst)
        self._last = clock()

    def reserve(self) -> float:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(float(self.rate.burst), self._tokens + elapsed * self.rate.per_second)
        self._tokens -= 1.0
        if self._tokens >= 0:
            return 0.0
        return -self._tokens / self.rate.per_second


@dataclass
class EgressRecord:
    """Every host contacted and the kinds of data sent to it: hosts and kinds, never values."""

    requests: dict[tuple[str, str], int] = field(default_factory=dict)
    kinds: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    def add(self, service: str, host: str, sends: Iterable[str]) -> None:
        key = (service, host)
        self.requests[key] = self.requests.get(key, 0) + 1
        self.kinds.setdefault(key, set()).update(sends)

    def summary(self) -> list[dict[str, Any]]:
        """One entry per (service, host): the number of requests and the kinds of data sent."""
        return [
            {
                "service": service,
                "host": host,
                "requests": self.requests[(service, host)],
                "sends": sorted(self.kinds.get((service, host), ())),
            }
            for service, host in sorted(self.requests)
        ]

    def merge(self, other: EgressRecord) -> None:
        for key, n in other.requests.items():
            self.requests[key] = self.requests.get(key, 0) + n
            self.kinds.setdefault(key, set()).update(other.kinds.get(key, ()))


@dataclass(frozen=True)
class Fetched:
    """A JSON answer: the data, when it was retrieved, and whether it came from the cache."""

    data: Any
    retrieved_at: datetime
    from_cache: bool
    status: int = 200


@dataclass(frozen=True)
class FetchedBytes:
    """An answer read as bytes (XML, a PDF, an archive): its content type, when, and from where."""

    content: bytes
    content_type: str
    retrieved_at: datetime
    from_cache: bool
    url: str


@dataclass(frozen=True)
class CursorPaging:
    """How a service pages a list with a cursor.

    *items*, *next_cursor* and *total* read one page; *total* may return ``None``
    when the service announces no count. The first request sends
    ``{cursor_param: first}``. With *confirm_empty*, an empty page is asked for
    once more before the list is taken as complete: a service that announces no
    count cannot otherwise tell a cut answer from the end of the list.
    """

    items: Callable[[Any], Sequence[Any]]
    next_cursor: Callable[[Any], str | None]
    total: Callable[[Any], int | None]
    cursor_param: str = "cursor"
    first: str = "*"
    max_pages: int = 10_000
    confirm_empty: bool = False


@dataclass(frozen=True)
class Page:
    """One page of a cursor-paged list, read by :meth:`HttpClient.pages`.

    *cursor* is the cursor that asked for it; *next_cursor* the one that asks
    for the next page (``None`` on the last); *total* the count the service
    announced; *read* the items read up to and including this page.
    """

    items: list[Any]
    cursor: str
    next_cursor: str | None
    total: int | None
    read: int
    retrieved_at: datetime


_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def _canonical_url(url: str) -> str:
    """Scheme and host in lower case, no default port, no trailing slash, no query.

    A service on this computer (the demo services) is ``loopback`` whatever its
    port, which changes from one run to the next.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port
    default = {"http": 80, "https": 443}.get(parts.scheme.lower())
    if host in _LOOPBACK:
        netloc = "loopback"
    else:
        netloc = host if port in (None, default) else f"{host}:{port}"
    path = quote(parts.path.rstrip("/") or "/", safe="/:@-._~!$&'()*+,;=%")
    return urlunsplit((parts.scheme.lower(), netloc, path, "", ""))


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, str]:
    """Parameters as sorted strings, without secrets and without empty values."""
    out: dict[str, str] = {}
    for key, value in sorted((params or {}).items()):
        if value is None or key.lower() in SECRET_PARAMS:
            continue
        out[str(key)] = str(value)
    return out


def _retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds asked for by a ``Retry-After`` header (seconds or an HTTP date)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - now).total_seconds())


# ── the cache ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CachedAnswer:
    """One cache entry: what the service answered, and when."""

    status: int
    headers: dict[str, str]
    body: str
    retrieved_at: datetime


class HttpCache:
    """Service answers in ``cache/http/<service>/<xx>/<key>.json.gz``, written atomically.

    Freshness is decided when an entry is read, from its retrieval time and the
    lifetime of its kind of request, so changing a lifetime applies to answers
    already stored. An entry that cannot be read is removed and fetched again.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def key(method: str, url: str, params: Mapping[str, Any] | None, *, variant: str = "") -> str:
        """The cache key: method, canonical URL, the answer-changing parameters (sorted)."""
        material = json.dumps(
            [method.upper(), _canonical_url(url), _clean_params(params), variant],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def path(self, service: str, key: str) -> Path:
        return self.root / service / key[:2] / f"{key}.json.gz"

    def read(self, service: str, key: str) -> CachedAnswer | None:
        """The stored answer, or ``None``; a corrupt entry is removed."""
        path = self.path(service, key)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            entry = json.loads(gzip.decompress(raw).decode("utf-8"))
            if entry.get("format") != CACHE_FORMAT:
                raise ValueError("unknown format")
            answer = CachedAnswer(
                status=int(entry["status"]),
                headers={str(k): str(v) for k, v in entry.get("headers", {}).items()},
                body=str(entry["body"]),
                retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            )
            if answer.retrieved_at.tzinfo is None:
                raise ValueError("retrieval time without a time zone")
            return answer
        except (OSError, EOFError, ValueError, KeyError, TypeError, UnicodeDecodeError):
            path.unlink(missing_ok=True)
            return None

    def bytes_path(self, service: str, key: str) -> Path:
        return self.root / service / key[:2] / f"{key}.bin.gz"

    def read_bytes(self, service: str, key: str) -> tuple[dict[str, Any], bytes] | None:
        """A stored bytes answer: its header (status, headers, retrieval time) and body."""
        path = self.bytes_path(service, key)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            data = gzip.decompress(raw)
            head, sep, body = data.partition(b"\n")
            header = json.loads(head.decode("utf-8"))
            if not sep or header.get("format") != BYTES_CACHE_FORMAT:
                raise ValueError("unknown format")
            when = datetime.fromisoformat(header["retrieved_at"])
            if when.tzinfo is None:
                raise ValueError("retrieval time without a time zone")
            header["retrieved_at"] = when
            return header, body
        except (OSError, EOFError, ValueError, KeyError, TypeError, UnicodeDecodeError):
            path.unlink(missing_ok=True)
            return None

    def write_bytes(
        self,
        service: str,
        key: str,
        *,
        kind: str,
        url: str,
        params: Mapping[str, Any] | None,
        status: int,
        headers: Mapping[str, str],
        body: bytes,
        retrieved_at: datetime,
    ) -> None:
        header = {
            "format": BYTES_CACHE_FORMAT,
            "service": service,
            "kind": kind,
            "method": "GET",
            "url": _canonical_url(url),
            "params": _clean_params(params),
            "status": status,
            "headers": dict(headers),
            "retrieved_at": retrieved_at.isoformat(),
        }
        head = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        atomic_write_bytes(
            self.bytes_path(service, key), gzip.compress(head + b"\n" + body, mtime=0)
        )

    def write(
        self,
        service: str,
        key: str,
        *,
        kind: str,
        method: str,
        url: str,
        params: Mapping[str, Any] | None,
        answer: CachedAnswer,
        durable: bool = True,
    ) -> None:
        entry = {
            "format": CACHE_FORMAT,
            "service": service,
            "kind": kind,
            "method": method.upper(),
            "url": _canonical_url(url),
            "params": _clean_params(params),
            "status": answer.status,
            "headers": answer.headers,
            "retrieved_at": answer.retrieved_at.isoformat(),
            "body": answer.body,
        }
        data = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        atomic_write_bytes(self.path(service, key), gzip.compress(data, mtime=0), durable=durable)


# ── the client ───────────────────────────────────────────────────────────────


class HttpClient:
    """Every request of one collection job goes through one client.

    *settings* says who calls, with which keys and where; *cache_dir* is the
    project's ``cache/http/`` (``None``: no cache); *mode* is ``normal``,
    ``refresh`` or ``cache_only``. *progress* receives ``(fraction, message)``
    and *cancel* is asked, between requests and during waits, whether to stop.
    *session*, *clock*, *sleep*, *now* and *rng* can be replaced (tests).
    """

    def __init__(
        self,
        settings: CollectSettings | None = None,
        *,
        cache_dir: Path | None = None,
        mode: CacheMode = "normal",
        progress: Callable[[float, str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if mode not in CACHE_MODES:
            raise ValueError(f"unknown cache mode {mode!r}; expected one of {CACHE_MODES}")
        self.settings = settings or CollectSettings()
        self.cache = HttpCache(cache_dir) if cache_dir is not None else None
        if mode == "cache_only" and self.cache is None:
            raise ValueError("cache_only needs a cache folder")
        self.mode: CacheMode = mode
        self._progress_cb = progress
        self._cancel = cancel
        self._session = session or requests.Session()
        self._session.trust_env = self.settings.use_system_proxy
        self._clock = clock
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._rng = rng or random.Random()
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._fraction = 0.0
        self.egress = EgressRecord()
        #: Requests sent, answers read from the cache, retries made.
        self.counts: dict[str, int] = {"sent": 0, "cached": 0, "retried": 0}

    # ── progress and cancel ──
    def progress(self, fraction: float, message: str = "", **detail: Any) -> None:
        """Report how far the job is (never backwards) and what it is doing; *detail*
        (``eta_s``, ``code``, ``params``…) goes to the callback when given."""
        self._fraction = max(self._fraction, min(1.0, max(0.0, float(fraction))))
        if self._progress_cb is not None:
            if detail:
                self._progress_cb(self._fraction, message, **detail)
            else:
                self._progress_cb(self._fraction, message)

    def check_cancel(self) -> None:
        """Raise :class:`Cancelled` when the job was asked to stop."""
        if self._cancel is not None and self._cancel():
            raise Cancelled("collection was cancelled; what was collected so far is kept")

    def _wait(self, seconds: float, reason: str) -> None:
        if seconds <= 0:
            return
        if seconds >= 1:
            self.progress(self._fraction, reason)
        remaining = seconds
        while remaining > 0:
            self.check_cancel()
            step = min(remaining, 0.25)
            self._sleep(step)
            remaining -= step
        self.check_cancel()

    # ── requests ──
    def service(self, name: str) -> Service:
        return self.settings.service(name)

    def _bucket(self, service: Service, host: str) -> TokenBucket:
        key = (service.name, host)
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(service.rate, self._clock)
        return self._buckets[key]

    def _headers(self, service: Service, extra: Mapping[str, str] | None) -> dict[str, str]:
        agent = self.settings.user_agent
        if self.settings.contact:
            agent = f"{agent} (mailto:{self.settings.contact})"
        headers = {"Accept": service.accept, "User-Agent": agent}
        key = self.settings.api_key(service.name)
        if key and service.bearer_key:
            headers["Authorization"] = f"Bearer {key}"
        headers.update(extra or {})
        return headers

    def _params(self, service: Service, params: Mapping[str, Any] | None) -> dict[str, str]:
        out = {str(k): str(v) for k, v in (params or {}).items() if v is not None}
        if service.contact_param and self.settings.contact:
            out[service.contact_param] = self.settings.contact
        key = self.settings.api_key(service.name)
        if key and not service.bearer_key:
            out["api_key"] = key
        return out

    def _sends(self, service: Service, sends: Iterable[str]) -> list[str]:
        kinds = list(sends)
        if self.settings.contact:
            kinds.append("contact address")
        if self.settings.api_key(service.name):
            kinds.append("API key")
        return kinds

    # ── one cached answer per item (a batched request answers many) ──
    def _item_key(self, svc: Service, kind: str, ident: str) -> str:
        return HttpCache.key("ITEM", svc.base_url, {"id": ident}, variant=kind)

    def cached_item(self, service: str, kind: str, ident: str) -> tuple[bool, Any]:
        """``(True, value)`` when the answer about one item (*ident*, e.g. a DOI) is in the
        cache and fresh, else ``(False, None)``. A batched request stores what it learnt
        per item (:meth:`store_item`), so a job cut short, or asking another mix of
        items, reuses it."""
        svc = self.service(service)
        if self.cache is None or self.mode == "refresh":
            return False, None
        key = self._item_key(svc, kind, ident)
        hit = self.cache.read(svc.name, key)
        if hit is None:
            return False, None
        age = (self._now() - hit.retrieved_at).total_seconds()
        if age > svc.lifetime(kind) and self.mode != "cache_only":
            return False, None
        try:
            value = json.loads(hit.body)
        except ValueError:
            self.cache.path(svc.name, key).unlink(missing_ok=True)
            return False, None
        self.counts["cached"] += 1
        return True, value

    def store_item(self, service: str, kind: str, ident: str, value: Any) -> None:
        """Keep what a service answered about one item (``None``: it has nothing)."""
        if self.cache is None:
            return
        svc = self.service(service)
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        self.cache.write(
            svc.name,
            self._item_key(svc, kind, ident),
            kind=kind,
            method="ITEM",
            url=svc.base_url,
            params={"id": ident},
            answer=CachedAnswer(200, {"content-type": "application/json"}, body, self._now()),
            durable=False,  # one per text: a lost entry is only asked again
        )

    def get_json(
        self,
        service: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        kind: str,
        sends: Iterable[str] = (),
        validate: Callable[[Any], None] | None = None,
        headers: Mapping[str, str] | None = None,
        cache: bool = True,
    ) -> Fetched:
        """GET ``<base>/<path>`` and return its JSON body, from the cache when fresh.

        *kind* names the kind of request (its cache lifetime and the message of a
        miss); *sends* the kinds of data the request carries (``name``, ``DOI``…),
        for the egress record. *validate* checks the body's shape and raises
        ``ValueError`` when it is not the documented one: such an answer is
        retried, never cached. A 404 raises :class:`NotFound`.
        """
        svc = self.service(service)
        url = svc.base_url + "/" + path.lstrip("/")
        key = HttpCache.key("GET", url, params)
        if cache and self.cache is not None and self.mode != "refresh":
            hit = self.cache.read(svc.name, key)
            if hit is not None:
                age = (self._now() - hit.retrieved_at).total_seconds()
                if age <= svc.lifetime(kind) or self.mode == "cache_only":
                    try:
                        data = json.loads(hit.body)
                        if validate is not None:
                            validate(data)
                    except ValueError:
                        self.cache.path(svc.name, key).unlink(missing_ok=True)
                    else:
                        self.counts["cached"] += 1
                        return Fetched(data, hit.retrieved_at, True, hit.status)
        if self.mode == "cache_only":
            raise CacheMiss(svc.name, kind, _canonical_url(url))
        status, body, kept, retrieved = self._send(svc, url, params, sends, headers, validate)
        data = json.loads(body)
        if cache and self.cache is not None:
            self.cache.write(
                svc.name,
                key,
                kind=kind,
                method="GET",
                url=url,
                params=params,
                answer=CachedAnswer(status, kept, body, retrieved),
            )
        return Fetched(data, retrieved, False, status)

    def get_all(
        self,
        service: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        kind: str,
        sends: Iterable[str] = (),
        paging: CursorPaging,
        validate: Callable[[Any], None] | None = None,
    ) -> Fetched:
        """Every item of a cursor-paged list, cached whole once complete.

        Pages are fetched until the service gives no next cursor or an empty
        page. The items must add up to the count the service announced, or
        :class:`IncompleteResults` is raised: a page cut short never yields a
        short list. Only a complete list is cached (as one entry), so a job
        cancelled halfway leaves nothing partial behind.
        """
        svc = self.service(service)
        url = svc.base_url + "/" + path.lstrip("/")
        base_params = dict(params or {})
        key = HttpCache.key("GET", url, base_params, variant="all-pages")
        if self.cache is not None and self.mode != "refresh":
            hit = self.cache.read(svc.name, key)
            if hit is not None:
                age = (self._now() - hit.retrieved_at).total_seconds()
                if age <= svc.lifetime(kind) or self.mode == "cache_only":
                    try:
                        items = json.loads(hit.body)["items"]
                        if not isinstance(items, list):
                            raise ValueError("not a list")
                    except (ValueError, KeyError, TypeError):
                        self.cache.path(svc.name, key).unlink(missing_ok=True)
                    else:
                        self.counts["cached"] += 1
                        return Fetched(items, hit.retrieved_at, True)
        if self.mode == "cache_only":
            raise CacheMiss(svc.name, kind, _canonical_url(url))

        host = urlsplit(url).netloc
        cursor: str | None = paging.first
        seen: set[str] = set()
        items: list[Any] = []
        announced: int | None = None
        first_retrieved: datetime | None = None

        def check(data: Any) -> None:
            if validate is not None:
                validate(data)
            paging.items(data)
            paging.next_cursor(data)
            paging.total(data)

        for _page in range(paging.max_pages):
            page_params = {**base_params, paging.cursor_param: cursor}
            status, body, _kept, retrieved = self._send(svc, url, page_params, sends, None, check)
            data = json.loads(body)
            first_retrieved = first_retrieved or retrieved
            page_items = list(paging.items(data))
            if not page_items and paging.confirm_empty:
                status, body, _kept, retrieved = self._send(
                    svc, url, page_params, sends, None, check
                )
                data = json.loads(body)
                page_items = list(paging.items(data))
            total = paging.total(data)
            if announced is None:
                announced = total
            items.extend(page_items)
            nxt = paging.next_cursor(data)
            if not nxt or not page_items:
                break
            if nxt in seen:
                raise MalformedResponse(
                    host, status, "the list's cursor repeats itself", "collect again later"
                )
            seen.add(nxt)
            cursor = nxt
        else:
            raise MalformedResponse(
                host, None, f"the list did not end after {paging.max_pages} pages", "report it"
            )
        if announced is not None and len(items) != announced:
            raise IncompleteResults(
                host,
                200,
                f"the list ended after {len(items)} of the {announced} items announced "
                "(a page was cut short)",
                "nothing was kept; collect again, and if it happens again, later",
            )
        retrieved_at = first_retrieved or self._now()
        if self.cache is not None:
            body = json.dumps({"items": items}, ensure_ascii=False, separators=(",", ":"))
            self.cache.write(
                svc.name,
                key,
                kind=kind,
                method="GET",
                url=url,
                params=base_params,
                answer=CachedAnswer(200, {"content-type": "application/json"}, body, retrieved_at),
            )
        return Fetched(items, retrieved_at, False)

    def pages(
        self,
        service: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        kind: str,
        sends: Iterable[str] = (),
        paging: CursorPaging,
        validate: Callable[[Any], None] | None = None,
        cursor: str | None = None,
        read: int = 0,
        per_page: int | None = None,
    ) -> Iterator[Page]:
        """The pages of a cursor-paged list one at a time, from *cursor* (the first page when
        ``None``); nothing is cached and nothing is kept between pages.

        *read* is how many items the pages before *cursor* held (a resumed list).
        A page that holds fewer items than *per_page* while the count announced
        says more remain, or that ends the list before that count, is cut short:
        it is asked again like a malformed answer, and after the last attempt
        :class:`IncompleteResults` is raised. The caller keeps what it needs of
        each page and, to go on later, the page's ``next_cursor``.
        """
        svc = self.service(service)
        url = svc.base_url + "/" + path.lstrip("/")
        if self.mode == "cache_only":
            raise CacheMiss(svc.name, kind, _canonical_url(url))
        host = urlsplit(url).netloc
        base_params = dict(params or {})
        current = cursor or paging.first
        seen: set[str] = set()
        count = read

        def check(data: Any) -> None:
            if validate is not None:
                validate(data)
            items = paging.items(data)
            nxt = paging.next_cursor(data)
            total = paging.total(data)
            if total is None or not items and nxt is None:
                return
            left = total - count
            if nxt is not None and per_page and len(items) < min(per_page, left):
                raise ShortPage(f"a page holds {len(items)} items of the {left} still announced")
            if nxt is None and len(items) < left:
                raise ShortPage(
                    f"the list ended after {count + len(items)} of the {total} items announced"
                )

        for _page in range(10 * paging.max_pages):
            page_params = {**base_params, paging.cursor_param: current}
            _status, body, _kept, retrieved = self._send(svc, url, page_params, sends, None, check)
            data = json.loads(body)
            items = list(paging.items(data))
            nxt = paging.next_cursor(data)
            count += len(items)
            total = paging.total(data)
            # The last page: no cursor, nothing on it, or every item announced read.
            last = not nxt or not items or (total is not None and count >= total)
            yield Page(items, current, None if last else nxt, total, count, retrieved)
            if last:
                return
            if nxt in seen:
                raise MalformedResponse(
                    host, 200, "the list's cursor repeats itself", "collect again later"
                )
            seen.add(nxt)
            current = nxt
        raise MalformedResponse(
            host, None, f"the list did not end after {10 * paging.max_pages} pages", "report it"
        )

    def get_bytes(
        self,
        service: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        kind: str,
        sends: Iterable[str] = (),
        validate: Callable[[bytes], None] | None = None,
        headers: Mapping[str, str] | None = None,
        cache: bool = True,
    ) -> FetchedBytes:
        """GET an answer that is not JSON (XML, a PDF, an archive) and return its bytes.

        *path* is relative to the service's base URL, or a whole ``http(s)`` URL
        (a file a service links to: pacing still applies per host). *validate*
        receives the bytes and raises ``ValueError`` when they are not what was
        asked for (a cut file): such an answer is retried and never cached. The
        cache, the modes, *kind* and *sends* work as for :meth:`get_json`.
        """
        svc = self.service(service)
        if path.startswith(("http://", "https://")):
            url = path
        else:
            url = svc.base_url + "/" + path.lstrip("/")
        key = HttpCache.key("GET", url, params, variant="bytes")
        if cache and self.cache is not None and self.mode != "refresh":
            hit = self.cache.read_bytes(svc.name, key)
            if hit is not None:
                header, body = hit
                age = (self._now() - header["retrieved_at"]).total_seconds()
                if age <= svc.lifetime(kind) or self.mode == "cache_only":
                    try:
                        if validate is not None:
                            validate(body)
                    except ValueError:
                        self.cache.bytes_path(svc.name, key).unlink(missing_ok=True)
                    else:
                        self.counts["cached"] += 1
                        kept = header.get("headers") or {}
                        return FetchedBytes(
                            body,
                            str(kept.get("content-type", "")),
                            header["retrieved_at"],
                            True,
                            url,
                        )
        if self.mode == "cache_only":
            raise CacheMiss(svc.name, kind, _canonical_url(url))
        status, body, kept, retrieved = self._send(
            svc, url, params, sends, headers, validate, raw=True
        )
        assert isinstance(body, bytes)
        if cache and self.cache is not None:
            self.cache.write_bytes(
                svc.name,
                key,
                kind=kind,
                url=url,
                params=params,
                status=status,
                headers=kept,
                body=body,
                retrieved_at=retrieved,
            )
        return FetchedBytes(body, kept.get("content-type", ""), retrieved, False, url)

    def _send(
        self,
        svc: Service,
        url: str,
        params: Mapping[str, Any] | None,
        sends: Iterable[str],
        headers: Mapping[str, str] | None,
        validate: Callable[[Any], None] | None,
        *,
        raw: bool = False,
    ) -> tuple[int, Any, dict[str, str], datetime]:
        """One request with pacing and retries; returns status, body, kept headers, time.

        The body is the text of a JSON answer, or its bytes when *raw* is true."""
        host = urlsplit(url).netloc
        policy = self.settings.retry
        kinds = self._sends(svc, sends)
        # The last failure: its class, status, what happened and what to do.
        failure: tuple[type[ServiceError], int | None, str, str, float | None] | None = None
        for attempt in range(1, policy.max_attempts + 1):
            self.check_cancel()
            self._wait(self._bucket(svc, host).reserve(), f"pacing requests to {host}")
            self.egress.add(svc.name, host, kinds)
            self.counts["sent"] += 1
            asked: float | None = None
            try:
                response = self._session.get(
                    url,
                    params=self._params(svc, params),
                    headers=self._headers(svc, headers),
                    timeout=self.settings.timeouts.as_tuple(),
                    allow_redirects=True,
                )
            except requests.exceptions.Timeout:
                failure = (
                    ServiceUnavailable,
                    None,
                    "no answer within the time allowed",
                    "the service is slow or unreachable; try again later",
                    None,
                )
            except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError):
                failure = (
                    ServiceUnavailable,
                    None,
                    "the connection failed",
                    "check the network connection (and any proxy), then try again",
                    None,
                )
            else:
                status = response.status_code
                if status in _RETRIED_STATUS:
                    asked = _retry_after(response.headers.get("Retry-After"), self._now())
                    if asked is not None and asked > policy.max_retry_after:
                        raise ServiceUnavailable(
                            host,
                            status,
                            f"the service asks to wait {asked:.0f} s",
                            "its rate or daily budget is spent; try again after that time",
                            retry_after=asked,
                        )
                    failure = (
                        ServiceUnavailable,
                        status,
                        "too many requests" if status == 429 else "the service failed",
                        "the service's rate or daily budget is spent; wait, or set an API key "
                        "if the service offers one"
                        if status == 429
                        else "the service is having trouble; try again later",
                        asked,
                    )
                elif status == 404:
                    raise NotFound(host, status, "no such record", "check the identifier")
                elif 400 <= status < 600:
                    raise RequestRefused(
                        host,
                        status,
                        "the service refused the request",
                        "this looks like a bug in cartolex or a change in the service; report it",
                    )
                else:
                    body = response.content if raw else response.text
                    try:
                        data = body if raw else json.loads(body)
                        if validate is not None:
                            validate(data)
                    except ShortPage as exc:
                        failure = (
                            IncompleteResults,
                            status,
                            f"a page was cut short ({str(exc)[:80]})",
                            "try again later",
                            None,
                        )
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        failure = (
                            MalformedResponse,
                            status,
                            f"the answer could not be read ({str(exc)[:80]})",
                            "try again later; if it persists, the service changed its format",
                            None,
                        )
                    else:
                        kept = {
                            h: response.headers[h] for h in KEPT_HEADERS if h in response.headers
                        }
                        return status, body, kept, self._now()
            if attempt == policy.max_attempts:
                break
            self.counts["retried"] += 1
            delay = min(policy.max_delay, policy.base_delay * 2 ** (attempt - 1))
            delay *= 0.5 + 0.5 * self._rng.random()
            if asked is not None:
                delay = max(delay, asked)
            self._wait(delay, f"{host}: {failure[2] if failure else 'retrying'}; waiting")
        assert failure is not None
        cls, status, what, advice, asked = failure
        raise cls(
            host,
            status,
            what,
            f"gave up after {policy.max_attempts} attempts; {advice}",
            retry_after=asked,
        )
