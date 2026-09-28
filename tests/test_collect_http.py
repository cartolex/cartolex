# SPDX-License-Identifier: MIT
"""The collection HTTP layer against the demo services: faults, retries, cache, cancel, egress."""

from __future__ import annotations

import email.utils
import gzip
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from cartolex.collect.http import (
    CacheMiss,
    Cancelled,
    CursorPaging,
    HttpCache,
    HttpClient,
    IncompleteResults,
    MalformedResponse,
    NotFound,
    RequestRefused,
    ServiceUnavailable,
    TokenBucket,
)
from cartolex.collect.services import RateLimit, RetryPolicy, Timeouts, local_settings
from cartolex.demo import generate
from cartolex.demo.services import DemoServices

PAGING = CursorPaging(
    items=lambda d: d["results"],
    next_cursor=lambda d: d["meta"].get("next_cursor"),
    total=lambda d: d["meta"]["count"],
)


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture()
def demo(services):
    services.faults.clear()
    services.requests.clear()
    return services


class _Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)

    @property
    def total(self) -> float:
        return sum(self.calls)


class _RecordingSession(requests.Session):
    """Records the timeout of every request it sends."""

    def __init__(self) -> None:
        super().__init__()
        self.timeouts: list[object] = []

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        self.timeouts.append(kwargs.get("timeout"))
        return super().request(method, url, *args, **kwargs)


def _client(demo, tmp_path: Path | None = None, **kw) -> HttpClient:
    settings = kw.pop("settings", None) or local_settings(
        demo.endpoints(),
        timeouts=kw.pop("timeouts", Timeouts(connect=2.0, read=5.0)),
        retry=kw.pop("retry", RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.05)),
        contact=kw.pop("contact", None),
        api_keys=kw.pop("api_keys", None),
    )
    kw.setdefault("sleep", _Sleeps())
    return HttpClient(settings, cache_dir=tmp_path / "http" if tmp_path else None, **kw)


def _authors(client: HttpClient, **params) -> object:
    return client.get_json(
        "openalex", "authors", {"search": "Aiko", **params}, kind="person_search", sends=["name"]
    )


def _works(client: HttpClient, author: str, per_page: int = 2) -> object:
    return client.get_all(
        "openalex",
        "works",
        {"filter": f"author.id:{author}", "per_page": per_page},
        kind="works_by_author",
        sends=["identifier"],
        paging=PAGING,
    )


def _prolific(demo) -> str:
    return max(demo.bibliography.authors.values(), key=lambda a: len(a.works)).id


# ── timeouts ──


def test_timeouts_must_be_finite() -> None:
    for bad in (None, math.inf, 0, -1.0, math.nan):
        with pytest.raises(ValueError):
            Timeouts(read=bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            Timeouts(connect=bad)  # type: ignore[arg-type]


def test_every_request_passes_a_finite_timeout(demo, tmp_path) -> None:
    session = _RecordingSession()
    client = _client(demo, tmp_path, session=session)
    demo.faults.add("status", service="openalex", status=503, times=1)
    _authors(client)
    _works(client, _prolific(demo))
    with pytest.raises(NotFound):
        client.get_json("openalex", "authors/A9990000000", kind="author")
    assert len(session.timeouts) >= 5
    for timeout in session.timeouts:
        assert isinstance(timeout, tuple) and len(timeout) == 2
        assert all(isinstance(t, float) and math.isfinite(t) and t > 0 for t in timeout)


# ── retries ──


def test_retry_after_in_seconds_is_honoured(demo) -> None:
    sleeps = _Sleeps()
    client = _client(demo, sleep=sleeps)
    demo.faults.add("status", service="openalex", status=429, retry_after="2", times=1)
    fetched = _authors(client)
    assert fetched.data["results"]
    assert sleeps.total >= 2.0
    assert client.counts["retried"] == 1


def test_retry_after_as_an_http_date_is_honoured(demo) -> None:
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    sleeps = _Sleeps()
    client = _client(demo, sleep=sleeps, now=lambda: now)
    when = email.utils.format_datetime(now + timedelta(seconds=4), usegmt=True)
    demo.faults.add("status", service="openalex", status=503, retry_after=when, times=1)
    _authors(client)
    assert sleeps.total >= 4.0 - 1e-9


def test_a_long_retry_after_stops_with_the_time_to_wait(demo) -> None:
    client = _client(demo)
    demo.faults.add("status", service="openalex", status=429, retry_after="7200", times=None)
    with pytest.raises(ServiceUnavailable) as err:
        _authors(client)
    assert err.value.status == 429 and err.value.retry_after == 7200
    assert "127.0.0.1" in str(err.value) and "7200" in str(err.value)
    assert len(demo.requests) == 1


def test_server_errors_are_retried(demo) -> None:
    client = _client(demo)
    demo.faults.add("status", service="openalex", status=500, times=2)
    assert _authors(client).data["results"]
    assert client.counts["retried"] == 2


def test_attempts_are_bounded_and_the_error_says_what_to_do(demo) -> None:
    client = _client(demo)
    demo.faults.add("status", service="openalex", status=503, times=None)
    with pytest.raises(ServiceUnavailable) as err:
        _authors(client)
    assert len(demo.requests) == 3
    message = str(err.value)
    assert demo.base_url.split("//")[1].split("/")[0] in message
    assert "status 503" in message and "gave up after 3 attempts" in message
    assert "try again later" in message


def test_backoff_grows_with_jitter(demo) -> None:
    sleeps = _Sleeps()
    retry = RetryPolicy(max_attempts=4, base_delay=1.0, max_delay=60.0)
    client = _client(demo, sleep=sleeps, retry=retry)
    demo.faults.add("status", service="openalex", status=502, times=3)
    _authors(client)
    waited = []
    total = 0.0
    for s in sleeps.calls:  # waits are cut in steps of at most 0.25 s
        total += s
        if abs(s - 0.25) > 1e-9:
            waited.append(total)
            total = 0.0
    assert len(waited) == 3
    for k, w in enumerate(waited):
        assert 0.5 * 2**k - 1e-9 <= w <= 2**k + 1e-9


def test_a_request_that_never_answers_times_out_and_is_retried(demo) -> None:
    client = _client(demo, timeouts=Timeouts(connect=1.0, read=0.3))
    demo.faults.add("hang", service="openalex", delay=3.0, times=1)
    assert _authors(client).data["results"]
    demo.faults.add("hang", service="openalex", delay=3.0, times=None)
    with pytest.raises(ServiceUnavailable, match="no answer within the time allowed"):
        _authors(client)


def test_a_dropped_connection_is_retried(demo) -> None:
    client = _client(demo)
    demo.faults.add("drop", service="openalex", times=1)
    assert _authors(client).data["results"]
    demo.faults.add("drop", service="openalex", times=None)
    with pytest.raises(ServiceUnavailable, match="connection failed"):
        _authors(client)


def test_malformed_answers_are_retried_then_refused(demo, tmp_path) -> None:
    client = _client(demo, tmp_path)
    demo.faults.add("malformed", service="openalex", times=1)
    assert _authors(client).data["results"]
    demo.faults.add("malformed", service="openalex", times=None)
    with pytest.raises(MalformedResponse):
        _authors(client, per_page=5)


def test_not_found_and_refused_requests_are_not_retried(demo) -> None:
    client = _client(demo)
    with pytest.raises(NotFound):
        client.get_json("openalex", "authors/A9990000000", kind="author")
    assert len(demo.requests) == 1
    with pytest.raises(RequestRefused) as err:
        client.get_json("openalex", "works", {"per_page": 1000}, kind="works_by_author")
    assert err.value.status == 400
    assert len(demo.requests) == 2


# ── pages ──


def test_every_page_is_read_and_the_total_checked(demo) -> None:
    client = _client(demo)
    author = _prolific(demo)
    fetched = _works(client, author)
    expected = len(demo.bibliography.authors[author].works)
    assert len(fetched.data) == expected > 2
    assert len({w["id"] for w in fetched.data}) == expected


@pytest.mark.parametrize("kind", ["cut_page", "early_end"])
def test_a_page_cut_short_raises_instead_of_a_short_list(demo, tmp_path, kind) -> None:
    client = _client(demo, tmp_path)
    demo.faults.add(kind, service="openalex", path=r"^works\?", skip=1, times=1)
    with pytest.raises(IncompleteResults, match="announced"):
        _works(client, _prolific(demo))
    assert not list((tmp_path / "http").rglob("*.json.gz"))


# ── the cache ──


def _entries(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "http").rglob("*.json.gz"))


def test_answers_are_cached_and_reused(demo, tmp_path) -> None:
    client = _client(demo, tmp_path)
    first = _authors(client)
    assert not first.from_cache and len(_entries(tmp_path)) == 1
    again = _client(demo, tmp_path)
    second = _authors(again)
    assert second.from_cache and second.data == first.data
    assert second.retrieved_at == first.retrieved_at
    assert len(demo.requests) == 1


def test_failures_are_never_cached(demo, tmp_path) -> None:
    client = _client(demo, tmp_path)
    demo.faults.add("status", service="openalex", status=503, times=None)
    with pytest.raises(ServiceUnavailable):
        _authors(client)
    demo.faults.clear()
    with pytest.raises(NotFound):
        client.get_json("openalex", "authors/A9990000000", kind="author")
    assert _entries(tmp_path) == []
    assert not _authors(client).from_cache


def test_cache_only_never_reaches_the_network(demo, tmp_path) -> None:
    _authors(_client(demo, tmp_path))
    offline = _client(demo, tmp_path, mode="cache_only")
    demo.requests.clear()
    assert _authors(offline).from_cache
    with pytest.raises(CacheMiss) as err:
        _authors(offline, per_page=7)
    assert "person search" in str(err.value) and "/openalex/authors" in str(err.value)
    with pytest.raises(CacheMiss):
        _works(offline, _prolific(demo))
    assert demo.requests == []


def test_refresh_rewrites_cached_answers(demo, tmp_path) -> None:
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    first = _authors(_client(demo, tmp_path, now=lambda: t0))
    later = t0 + timedelta(hours=1)
    refreshed = _authors(_client(demo, tmp_path, mode="refresh", now=lambda: later))
    assert not refreshed.from_cache and refreshed.retrieved_at == later
    reread = _authors(_client(demo, tmp_path, now=lambda: later))
    assert reread.from_cache and reread.retrieved_at == later != first.retrieved_at


def test_expired_answers_are_fetched_again(demo, tmp_path) -> None:
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    _authors(_client(demo, tmp_path, now=lambda: t0))
    fresh = _authors(_client(demo, tmp_path, now=lambda: t0 + timedelta(days=1)))
    assert fresh.from_cache
    stale = _authors(_client(demo, tmp_path, now=lambda: t0 + timedelta(days=4)))
    assert not stale.from_cache
    # Offline, an expired answer is still better than none.
    offline = _client(demo, tmp_path, mode="cache_only", now=lambda: t0 + timedelta(days=90))
    assert _authors(offline).from_cache


def test_a_corrupt_entry_is_dropped_and_fetched_again(demo, tmp_path) -> None:
    _authors(_client(demo, tmp_path))
    (entry,) = _entries(tmp_path)
    entry.write_bytes(b"not gzip at all")
    assert not _authors(_client(demo, tmp_path)).from_cache
    (entry,) = _entries(tmp_path)
    assert json.loads(gzip.decompress(entry.read_bytes()))["status"] == 200


def test_secrets_never_reach_the_cache(demo, tmp_path) -> None:
    client = _client(demo, tmp_path, api_keys={"openalex": "SECRET-KEY-123"}, contact="me@x.test")
    _authors(client)
    (entry,) = _entries(tmp_path)
    text = gzip.decompress(entry.read_bytes()).decode()
    assert "SECRET-KEY-123" not in text and "me@x.test" not in text
    # The key is sent as a bearer header, never in the URL.
    seen = demo.requests[-1]
    assert seen.headers["authorization"] == "Bearer SECRET-KEY-123"
    assert "api_key" not in seen.query and seen.query["mailto"] == "me@x.test"
    # Without a key the same request hits the same cache entry.
    assert _authors(_client(demo, tmp_path)).from_cache
    assert HttpCache.key("GET", "http://h/x", {"a": 1, "api_key": "k"}) == HttpCache.key(
        "GET", "HTTP://H/x/", {"a": "1"}
    )


def test_a_cancel_between_pages_leaves_the_cache_consistent(demo, tmp_path) -> None:
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return len(demo.requests) >= 2

    client = _client(demo, tmp_path, cancel=cancel)
    with pytest.raises(Cancelled):
        _works(client, _prolific(demo))
    assert _entries(tmp_path) == []
    complete = _works(_client(demo, tmp_path), _prolific(demo))
    assert len(_entries(tmp_path)) == 1
    assert _works(_client(demo, tmp_path, mode="cache_only"), _prolific(demo)).data == complete.data


def test_a_cancel_interrupts_a_wait(demo) -> None:
    sleeps = _Sleeps()
    client = _client(
        demo,
        sleep=sleeps,
        cancel=lambda: len(sleeps.calls) >= 3,
        retry=RetryPolicy(max_attempts=3, base_delay=30.0, max_delay=60.0),
    )
    demo.faults.add("status", service="openalex", status=503, times=None)
    with pytest.raises(Cancelled):
        _authors(client)
    assert sleeps.total < 1.0


# ── egress, pacing, progress ──


def test_the_egress_record_names_hosts_and_kinds_never_values(demo) -> None:
    client = _client(demo, contact="me@x.test")
    _authors(client)
    client.get_json(
        "orcid",
        f"{next(iter(demo.bibliography.registry))}/works",
        kind="registry_works",
        sends=["identifier"],
    )
    summary = client.egress.summary()
    assert [e["service"] for e in summary] == ["openalex", "orcid"]
    assert summary[0]["sends"] == ["contact address", "name"]
    assert summary[1]["sends"] == ["contact address", "identifier"]
    assert "Aiko" not in json.dumps(summary) and "me@x.test" not in json.dumps(summary)


def test_the_registry_is_asked_for_json(demo) -> None:
    client = _client(demo)
    orcid = next(iter(demo.bibliography.registry))
    fetched = client.get_json("orcid", f"{orcid}/works", kind="registry_works")
    assert "group" in fetched.data
    assert demo.requests[-1].headers["accept"] == "application/json"


def test_token_bucket_paces_requests() -> None:
    now = [0.0]
    bucket = TokenBucket(RateLimit(per_second=2.0, burst=1), clock=lambda: now[0])
    assert bucket.reserve() == 0.0
    assert bucket.reserve() == pytest.approx(0.5)
    assert bucket.reserve() == pytest.approx(1.0)
    now[0] = 10.0
    assert bucket.reserve() == 0.0


def test_requests_wait_their_turn_per_service(demo) -> None:
    sleeps = _Sleeps()
    settings = local_settings(demo.endpoints())
    settings = type(settings)(
        endpoints=settings.endpoints,
        rates={"openalex": RateLimit(per_second=1 / 3, burst=1)},
        use_system_proxy=False,
    )
    now = [0.0]

    def sleep(seconds: float) -> None:
        sleeps(seconds)
        now[0] += seconds

    client = HttpClient(settings, sleep=sleep, clock=lambda: now[0])
    _authors(client)
    _authors(client, per_page=3)
    assert sleeps.total == pytest.approx(3.0)


def test_progress_never_goes_backwards(demo) -> None:
    seen: list[tuple[float, str]] = []
    client = _client(demo, progress=lambda f, m: seen.append((f, m)))
    client.progress(0.5, "half")
    client.progress(0.2, "less")
    assert [f for f, _ in seen] == [0.5, 0.5]


def test_unknown_mode_and_cache_only_without_cache_are_refused(demo) -> None:
    with pytest.raises(ValueError):
        HttpClient(local_settings(demo.endpoints()), mode="offline")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        HttpClient(local_settings(demo.endpoints()), mode="cache_only")


def test_cached_answers_of_local_services_survive_a_new_port(tmp_path) -> None:
    world = generate("XS", 0)
    with DemoServices(world) as first:
        _authors(_client(first, tmp_path))
    with DemoServices(world) as second:
        assert _authors(_client(second, tmp_path, mode="cache_only")).from_cache
    with DemoServices(generate("XS", 1)) as other:
        with pytest.raises(CacheMiss):
            _authors(_client(other, tmp_path, mode="cache_only"))
