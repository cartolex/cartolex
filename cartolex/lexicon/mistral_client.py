# SPDX-License-Identifier: MIT
"""Thin wrapper around the Mistral AI SDK for keyword triage.

Handles API key loading, structured JSON output, retry/backoff and response
caching — and the three properties an unattended batch job depends on:

* **Every call is bounded in time** (:data:`DEFAULT_TIMEOUT_S`).  The SDK builds
  its own ``httpx`` client and then passes an explicit ``timeout=None`` on every
  request, which httpx reads as *no timeout at all*: a connection held open by a
  filtering proxy parked a worker thread forever — no log line, no error, a whole
  triage frozen at 5 % for hours.  Passing ``timeout_ms`` to the SDK constructor
  is what turns that dangling socket into an ordinary, retryable failure.
* **Errors are classified by status code** (:class:`LLMError`,
  :func:`classify_error`), not by substring-matching the message.  A refused key
  or an empty wallet now fails at once, with a nameable reason, instead of being
  mistaken for a transient 5xx — or the reverse, a request id that happens to
  contain ``500`` turning a hard error into a quarter-hour of backoff.
* **Waits are visible and interruptible**: every retry says why and for how long,
  and ``should_cancel`` lets the caller stop a run mid-backoff.

:class:`AdaptiveThrottle` adds the matching back-pressure: on a 429 the whole
worker fleet parks for the provider's cooldown and the in-flight ceiling halves,
recovering one slot at a time — instead of each thread discovering the closed
door on its own.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .llm_usage import UsageRecorder

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "mistral-small-latest"
DEFAULT_API_URL = "https://api.mistral.ai"
DEFAULT_API_KEY_NAME = "mistral_api_key"

#: Per-request ceiling, in seconds, applied to connect/read/write/pool alike (the
#: SDK takes a single number).  Generous enough for a long holistic completion,
#: short enough that a stalled socket surfaces as an error the same minute.
DEFAULT_TIMEOUT_S = 180.0

#: Ceiling for one backoff wait (seconds).  Mistral resets its rate-limit window
#: on the minute, so the 429 ladder starts there; nothing waits longer than this.
MAX_BACKOFF_S = 300.0


def load_api_key(
    path: Path | None = None,
    key_name: str = DEFAULT_API_KEY_NAME,
    *,
    interactive: bool = True,
) -> str:
    """Load Mistral API key via the unified credential chain.

    Resolution order: env var ``MISTRAL_API_KEY`` → the JSON file *path* (the
    context's ``paths.api_key_json``) → interactive prompt.
    """
    from cartolex.lexicon.credentials import resolve_mistral_key

    return resolve_mistral_key(path, interactive=interactive)


def _record_usage(resp: Any, recorder: UsageRecorder | None) -> None:
    """Tally a live response's token usage into *recorder* (the run's), when given.

    Best-effort: silently does nothing when the SDK response carries no usage
    block. Cached responses never reach here, so counts reflect real spend.
    """
    usage = getattr(resp, "usage", None)
    if usage is None or recorder is None:
        return
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    recorder.record(prompt, completion)


def _cache_key(phase: str, terms: list[str], domain_title: str, model: str) -> str:
    """Deterministic hash key for caching LLM responses.

    The hashed payload is frozen: existing workspaces hold paid answers under
    these keys. Its key ``"section"`` is an old name of the domain title and
    stays as it is — renaming it would change every key and make users pay
    again for answers they already have (see ``tests/test_ai_cache_keys.py``).
    """
    payload = json.dumps(
        {"v": 5, "phase": phase, "terms": sorted(terms), "section": domain_title, "model": model},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Error model ───────────────────────────────────────────────────────────────


class LLMError(RuntimeError):
    """A Mistral call that could not be completed, with its cause named.

    ``kind`` is the stable identifier consuming applications branch on (to phrase
    an operator-facing message, or to decide whether re-running could help):

    ``auth``
        The key was refused (401/403) — wrong, revoked, or not yet active.
    ``payment``
        The account cannot pay for the call (402) — no credit left.
    ``rate_limit``
        Too many requests for the plan's ceiling (429) — retryable, after a wait.
    ``model``
        The requested model does not exist or is not open to this account (404).
    ``request``
        The payload was rejected (400/409/422) — a bug or an impossible prompt.
    ``server``
        Provider-side failure (500/502/503) — retryable.
    ``gateway_timeout``
        The completion outlived the gateway window (504) — retryable *once*; the
        cure is a smaller request, not a longer wait.
    ``timeout``
        No answer within :data:`DEFAULT_TIMEOUT_S` — retryable.
    ``network``
        The host could not be reached at all: DNS, TLS, proxy, firewall.
    ``response``
        A 200 whose body could not be parsed as expected.
    ``cancelled``
        The caller asked the run to stop (see :class:`LLMCancelled`).
    ``missing_package``
        The optional ``mistralai`` package is not installed (the ``llm`` extra).
    ``unknown``
        Anything unrecognised — treated as non-retryable on purpose.
    """

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retryable = retryable


class LLMCancelled(LLMError):
    """Raised when ``should_cancel`` turned true — never retried, never swallowed."""

    def __init__(self, message: str = "Cancelled by the caller") -> None:
        super().__init__("cancelled", message, retryable=False)


#: HTTP status → (kind, retryable).  Anything absent is non-retryable by design:
#: an unknown failure repeated five times is five times the damage, not a fix.
_STATUS_KINDS: dict[int, tuple[str, bool]] = {
    400: ("request", False),
    401: ("auth", False),
    403: ("auth", False),
    402: ("payment", False),
    404: ("model", False),
    409: ("request", False),
    422: ("request", False),
    429: ("rate_limit", True),
    500: ("server", True),
    502: ("server", True),
    503: ("server", True),
    504: ("gateway_timeout", True),
}

#: Last-resort status extraction from an exception *message*, for SDK/stub errors
#: that carry no attribute.  Anchored on a status marker ("Status 401", "HTTP
#: 429") so a request id containing "500" can no longer pass for a server error —
#: the exact confusion that turned a hard failure into hours of silent backoff.
_STATUS_RE = re.compile(r"(?:status(?:[ _]?code)?|HTTP)\D{0,3}(\d{3})", re.IGNORECASE)


def _status_of(exc: BaseException) -> int | None:
    """HTTP status carried by *exc* (attribute, nested response, or message)."""
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and 100 <= value <= 599:
        return value
    match = _STATUS_RE.search(str(exc))
    if match:
        code = int(match.group(1))
        if 100 <= code <= 599:
            return code
    return None


def _transport_kind(exc: BaseException) -> tuple[str, bool] | None:
    """``(kind, retryable)`` when *exc* is a transport failure, else ``None``.

    Recognised by class rather than by ``isinstance``: ``httpx`` reaches us only
    through the SDK, and the engine does not declare it as a direct dependency.
    """
    if isinstance(exc, TimeoutError):
        return ("timeout", True)
    module = type(exc).__module__.split(".")[0]
    name = type(exc).__name__
    if module not in ("httpx", "httpcore", "ssl", "socket", "urllib3"):
        return None
    if "Timeout" in name:
        return ("timeout", True)
    return ("network", True)


def classify_error(exc: BaseException) -> LLMError:
    """Turn any exception raised by an API call into a typed :class:`LLMError`."""
    if isinstance(exc, LLMError):
        return exc
    detail = str(exc).strip() or type(exc).__name__
    transport = _transport_kind(exc)
    if transport is not None:
        kind, retryable = transport
        return LLMError(kind, f"{kind}: {detail}"[:500], retryable=retryable)
    status = _status_of(exc)
    if status is None:
        return LLMError("unknown", f"unknown: {detail}"[:500])
    kind, retryable = _STATUS_KINDS.get(status, ("server" if status >= 500 else "request", False))
    return LLMError(
        kind, f"{kind} (HTTP {status}): {detail}"[:500], status=status, retryable=retryable
    )


# ── Adaptive back-pressure ────────────────────────────────────────────────────


class AdaptiveThrottle:
    """In-flight limiter that shrinks under rate-limiting and recovers slowly.

    The ceiling starts at *max_concurrent*; every reported 429 halves it (never
    below *min_limit*) and parks **every** worker until the provider's cooldown
    has elapsed, so a fleet of threads stops queueing more refusals behind the
    one that just bounced.  Each run of *recover_after* consecutive successes
    gives one slot back, up to the original ceiling.

    Thread-safe; a throttle instance is meant to be shared by all workers of one
    batch run.
    """

    def __init__(self, max_concurrent: int, *, min_limit: int = 1, recover_after: int = 12) -> None:
        self._ceiling = max(1, int(max_concurrent))
        self._limit = self._ceiling
        self._min_limit = max(1, min(int(min_limit), self._ceiling))
        self._recover_after = max(1, int(recover_after))
        self._inflight = 0
        self._streak = 0
        self._paused_until = 0.0
        self._cv = threading.Condition()

    @property
    def limit(self) -> int:
        """Current ceiling on simultaneous in-flight requests."""
        with self._cv:
            return self._limit

    @contextlib.contextmanager
    def slot(self, *, should_cancel: Callable[[], bool] | None = None) -> Iterator[None]:
        """Hold one in-flight slot, blocking while the fleet is full or parked."""
        with self._cv:
            while True:
                if should_cancel is not None and should_cancel():
                    raise LLMCancelled()
                now = time.monotonic()
                if self._inflight < self._limit and now >= self._paused_until:
                    break
                wait = 0.25 if now >= self._paused_until else min(0.25, self._paused_until - now)
                self._cv.wait(timeout=max(0.05, wait))
            self._inflight += 1
        try:
            yield
        finally:
            with self._cv:
                self._inflight -= 1
                self._cv.notify_all()

    def penalise(self, wait_s: float) -> float:
        """Report a 429: halve the ceiling, park every worker, return the wait applied."""
        with self._cv:
            self._streak = 0
            previous = self._limit
            self._limit = max(self._min_limit, self._limit // 2)
            self._paused_until = max(self._paused_until, time.monotonic() + wait_s)
            if self._limit != previous:
                logger.warning(
                    "Rate-limited: lowering parallel requests %d → %d", previous, self._limit
                )
            self._cv.notify_all()
            return wait_s

    def reward(self) -> None:
        """Report a success: give a slot back after a long enough clean streak."""
        with self._cv:
            self._streak += 1
            if self._streak >= self._recover_after and self._limit < self._ceiling:
                self._streak = 0
                self._limit += 1
                logger.info("Recovered: raising parallel requests to %d", self._limit)
                self._cv.notify_all()


class LLMCache:
    """Simple file-backed JSON cache for LLM responses (thread-safe)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._dirty = True

    def flush(self) -> None:
        """Write pending changes to disk."""
        with self._lock:
            if self._dirty:
                self._save_unlocked()
                self._dirty = False

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def clear(self) -> None:
        with self._lock:
            self._data = {}
            self._save_unlocked()


class MistralClient:
    """Wrapper around the Mistral AI chat completion API.

    Usage::

        client = MistralClient(api_key="...", model="mistral-small-latest")
        result = client.classify(
            system_prompt="You are ...",
            user_content="term1, term2, ...",
            response_schema={...},
        )

    Parameters beyond the obvious ones:

    ``timeout_s``
        Ceiling on one request (connect/read/write/pool alike).  **Never set this
        to ``None``**: the SDK's own default is "wait forever".
    ``throttle``
        Optional :class:`AdaptiveThrottle` shared across the worker threads of a
        batch run; the client reports 429s and successes to it.
    ``should_cancel``
        Optional predicate polled before each attempt and during every backoff
        wait; raises :class:`LLMCancelled` as soon as it turns true.
    ``usage``
        Optional :class:`~cartolex.lexicon.llm_usage.UsageRecorder` (the run's)
        that live calls report their token counts to.
    ``client_factory``
        Optional replacement of the SDK's client class, called with the same
        arguments (``api_key``, ``server_url``, ``timeout_ms``).
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        api_url: str = DEFAULT_API_URL,
        temperature: float = 0.1,
        max_retries: int = 5,
        cache: LLMCache | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        throttle: AdaptiveThrottle | None = None,
        should_cancel: Callable[[], bool] | None = None,
        usage: UsageRecorder | None = None,
        client_factory: Callable[..., Any] | None = None,
    ):
        self.api_key = api_key
        self.client_factory = client_factory
        self.model = model
        self.api_url = api_url
        self.temperature = temperature
        self.max_retries = max_retries
        self.cache = cache
        self.timeout_s = float(timeout_s)
        self.throttle = throttle
        self.should_cancel = should_cancel
        self.usage = usage
        self.last_message: dict = {}
        self._client = None

    def _get_client(self):
        """Lazy-initialize the Mistral SDK client (with a real request timeout)."""
        if self._client is None:
            if self.client_factory is not None:
                Mistral = self.client_factory  # noqa: N806 - called like the SDK's class
            else:
                try:
                    from mistralai import Mistral
                except ImportError as exc:
                    raise LLMError(
                        "missing_package",
                        "the AI clean-up by API needs the optional mistralai package: "
                        "pip install 'cartolex[llm]'",
                    ) from exc

            self._client = Mistral(
                api_key=self.api_key,
                server_url=self.api_url,
                timeout_ms=int(self.timeout_s * 1000),
            )
        return self._client

    # ── retry plumbing ────────────────────────────────────────────────

    def _raise_if_cancelled(self) -> None:
        if self.should_cancel is not None and self.should_cancel():
            raise LLMCancelled()

    def _sleep(self, seconds: float) -> None:
        """Interruptible sleep: wakes every 0.5 s to honour ``should_cancel``.

        Counted in slices rather than against a wall-clock deadline, so a test
        that stubs :func:`time.sleep` out still finishes in no time.
        """
        remaining = float(seconds)
        while remaining > 0:
            self._raise_if_cancelled()
            step = min(0.5, remaining)
            time.sleep(step)
            remaining -= step
        self._raise_if_cancelled()

    @staticmethod
    def _backoff_wait(err: LLMError, attempt: int, backoff: float) -> float:
        """Seconds to wait before retrying *err*'s attempt number *attempt*."""
        if err.kind == "rate_limit":
            # Mistral resets its rate-limit window on the minute — start there.
            return min(60.0 * (2.0**attempt), MAX_BACKOFF_S)
        return min(backoff, MAX_BACKOFF_S)

    def _complete(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """One chat completion with the shared retry/backoff/cancel policy.

        Raises :class:`LLMError` (typed) on failure — never a bare SDK exception,
        so every caller can name the cause without parsing a message.
        """
        client = self._get_client()
        extra: dict[str, Any] = {}
        if response_format is not None:
            extra["response_format"] = response_format
        if max_tokens is not None:
            extra["max_tokens"] = max_tokens

        backoff = 2.0
        last_error: LLMError | None = None
        for attempt in range(self.max_retries):
            self._raise_if_cancelled()
            try:
                if self.throttle is not None:
                    with self.throttle.slot(should_cancel=self.should_cancel):
                        resp = client.chat.complete(
                            model=self.model,
                            messages=messages,
                            temperature=self.temperature,
                            **extra,
                        )
                else:
                    resp = client.chat.complete(
                        model=self.model,
                        messages=messages,
                        temperature=self.temperature,
                        **extra,
                    )
            except LLMCancelled:
                raise
            except Exception as exc:
                err = classify_error(exc)
                last_error = err
                retryable = err.retryable and attempt < self.max_retries - 1
                if err.kind == "gateway_timeout":
                    # A 504 usually means the completion itself exceeds the gateway
                    # window (~15 min) — retrying the same payload mostly burns
                    # another window. One retry covers load blips; beyond that the
                    # caller must shrink the request (for example by bisecting its batch).
                    retryable = retryable and attempt == 0
                if not retryable:
                    logger.error("Mistral call failed — %s", err)
                    raise err from exc
                wait = self._backoff_wait(err, attempt, backoff)
                if err.kind == "rate_limit" and self.throttle is not None:
                    wait = self.throttle.penalise(wait)
                logger.warning(
                    "Mistral %s — retrying in %.0f s (attempt %d/%d): %s",
                    err.kind,
                    wait,
                    attempt + 1,
                    self.max_retries,
                    err,
                )
                backoff = min(backoff * 2.0, MAX_BACKOFF_S)
                self._sleep(wait)
                continue
            if self.throttle is not None:
                self.throttle.reward()
            _record_usage(resp, self.usage)
            return resp

        raise last_error or LLMError(
            "unknown", f"Mistral API failed after {self.max_retries} attempts"
        )

    # ── public calls ──────────────────────────────────────────────────

    def chat_json(
        self,
        system_prompt: str,
        user_content: str,
        response_schema: dict | None = None,
        *,
        cache_key: str | None = None,
    ) -> dict | list:
        """Send a chat completion request and parse the JSON response.

        If *response_schema* is provided, uses Mistral's structured output
        mode (``response_format=json_object`` with schema enforcement).
        Otherwise, requests plain JSON mode.

        Returns the parsed JSON (dict or list).
        """
        # Check cache first
        if cache_key and self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        if response_schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "keyword_response",
                    "schema": response_schema,
                    "strict": True,
                },
            }
        else:
            response_format = {"type": "json_object"}

        self.last_message = {}  # full message of the most recent call (incl. reasoning), for audit
        resp = self._complete(messages, response_format=response_format)
        msg = resp.choices[0].message
        content = msg.content
        try:
            self.last_message = msg.model_dump()
        except Exception:
            self.last_message = {"content": content}
        try:
            result = json.loads(content)
        except (TypeError, ValueError) as exc:
            raise LLMError("response", f"response: not JSON ({exc})") from exc

        if cache_key and self.cache:
            self.cache.put(cache_key, result)
        return result

    def chat_text(
        self,
        system_prompt: str,
        user_content: str,
        *,
        cache_key: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send a chat completion and return the raw text response.

        Unlike :meth:`chat_json`, no ``response_format`` is set and the
        raw string content is returned (and cached) as-is.  Use this for
        compressed line-based output formats that are parsed by the caller.
        """
        if cache_key and self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        resp = self._complete(messages, max_tokens=max_tokens)
        content = resp.choices[0].message.content

        if cache_key and self.cache:
            self.cache.put(cache_key, content)
        return content


def check_key(
    api_key: str,
    *,
    model: str = DEFAULT_MODEL,
    api_url: str = DEFAULT_API_URL,
    timeout_s: float = 15.0,
    usage: UsageRecorder | None = None,
) -> dict[str, Any]:
    """Probe an API key with one minimal live call → a verdict dict.

    Returns ``{ok, kind, status, detail, latency_ms, model}``.  ``kind`` is
    ``"ok"`` on success, else the :class:`LLMError` kind — so a caller can tell a
    refused key from an empty wallet from a firewall, in seconds, instead of
    launching a batch of hundreds of calls to find out.

    The call asks for a single token, so the cost is negligible; it is a *live*
    completion on purpose — listing models would not prove the account can pay
    for one, which is exactly the failure this is meant to catch.  Only the word
    "ping" is transmitted.
    """
    started = time.monotonic()
    client = MistralClient(
        api_key=api_key,
        model=model,
        api_url=api_url,
        max_retries=1,
        timeout_s=timeout_s,
        usage=usage,
    )
    try:
        client.chat_text("", "ping", max_tokens=1)
    except LLMError as err:
        return {
            "ok": False,
            "kind": err.kind,
            "status": err.status,
            "detail": str(err),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "model": model,
        }
    return {
        "ok": True,
        "kind": "ok",
        "status": 200,
        "detail": f"{model} answered",
        "latency_ms": int((time.monotonic() - started) * 1000),
        "model": model,
    }
