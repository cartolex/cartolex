# SPDX-License-Identifier: MIT
"""Failure handling of the Mistral layer (no network — stubbed SDK).

The field incident behind this file (2026-09-17): a triage of 469 batches sat at
5 % for over an hour with nothing in the log and nothing billed. Three code
properties made that possible, and each is pinned here:

* the SDK was handed no timeout, so a stalled socket was a permanent one;
* failures were sorted by substring (``"500" in str(e)``), so a hard error could
  be mistaken for a transient one — and a transient one for a hard failure;
* the first failure could not stop the run: the pool's ``__exit__`` waited for
  every queued batch to walk its own retry ladder first.
"""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from cartolex.lexicon.mistral_client import (
    AdaptiveThrottle,
    LLMCancelled,
    LLMError,
    MistralClient,
    check_key,
    classify_error,
)
from cartolex.lexicon.triage_typed import run_typed_triage

# ── Error classification ──────────────────────────────────────────────


class _SDKish(Exception):
    """Stand-in for the SDK error type: carries a status code attribute."""

    def __init__(self, status_code: int, message: str = "boom"):
        super().__init__(message)
        self.status_code = status_code


class TestClassifyError:
    @pytest.mark.parametrize(
        ("status", "kind", "retryable"),
        [
            (401, "auth", False),
            (403, "auth", False),
            (402, "payment", False),
            (404, "model", False),
            (422, "request", False),
            (429, "rate_limit", True),
            (500, "server", True),
            (503, "server", True),
            (504, "gateway_timeout", True),
        ],
    )
    def test_status_attribute_decides(self, status, kind, retryable):
        err = classify_error(_SDKish(status))
        assert (err.kind, err.retryable, err.status) == (kind, retryable, status)

    def test_status_read_from_the_message_when_there_is_no_attribute(self):
        err = classify_error(RuntimeError("API error occurred: Status 401\n{...}"))
        assert err.kind == "auth" and not err.retryable

    def test_request_id_containing_500_is_not_a_server_error(self):
        # The old substring test ("500" in str(e)) turned this hard failure into
        # four backoff waits per batch — the difference between a 2-second error
        # message and an afternoon of silence.
        err = classify_error(_SDKish(401, 'Unauthorized {"request_id":"a5003f429b"}'))
        assert err.kind == "auth" and not err.retryable

    def test_timeouts_and_transport_failures_are_retryable(self):
        class ConnectTimeout(Exception):
            pass

        ConnectTimeout.__module__ = "httpx"

        class ConnectError(Exception):
            pass

        ConnectError.__module__ = "httpx"

        assert classify_error(ConnectTimeout("timed out")).kind == "timeout"
        assert classify_error(ConnectError("proxy refused")).kind == "network"
        assert classify_error(ConnectError("proxy refused")).retryable is True

    def test_unrecognised_failure_is_not_retried(self):
        err = classify_error(ValueError("something else entirely"))
        assert err.kind == "unknown" and not err.retryable


# ── Client behaviour ──────────────────────────────────────────────────


def _stub_client(monkeypatch, outcomes, **kwargs):
    """A MistralClient whose SDK stub pops one outcome per call."""
    client = MistralClient(api_key="k", model="m", **kwargs)
    calls = {"n": 0}

    def complete(**_kw):
        out = outcomes[min(calls["n"], len(outcomes) - 1)]
        calls["n"] += 1
        if isinstance(out, Exception):
            raise out
        msg = types.SimpleNamespace(content=out, model_dump=lambda: {"content": out})
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=None)

    client._client = types.SimpleNamespace(chat=types.SimpleNamespace(complete=complete))
    monkeypatch.setattr("cartolex.lexicon.mistral_client.time.sleep", lambda *_: None)
    return client, calls


class TestClientPolicy:
    def test_the_sdk_is_always_given_a_request_timeout(self, monkeypatch):
        """The whole point: the SDK's own default is to wait forever."""
        seen: dict = {}

        class FakeMistral:
            def __init__(self, **kwargs):
                seen.update(kwargs)

        monkeypatch.setattr("mistralai.Mistral", FakeMistral)
        MistralClient(api_key="k", timeout_s=42.0)._get_client()
        assert seen["timeout_ms"] == 42_000

    def test_without_the_sdk_the_error_names_the_extra_to_install(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "mistralai", None)  # as if it were not installed
        with pytest.raises(LLMError) as excinfo:
            MistralClient(api_key="k")._get_client()
        assert excinfo.value.kind == "missing_package"
        assert "cartolex[llm]" in str(excinfo.value)

    def test_auth_failure_raises_a_typed_error_at_once(self, monkeypatch):
        client, calls = _stub_client(monkeypatch, [_SDKish(401)])
        with pytest.raises(LLMError) as excinfo:
            client.chat_text("sys", "user")
        assert excinfo.value.kind == "auth"
        assert calls["n"] == 1

    def test_rate_limit_is_retried_then_succeeds(self, monkeypatch):
        client, calls = _stub_client(monkeypatch, [_SDKish(429), "ok"])
        assert client.chat_text("sys", "user") == "ok"
        assert calls["n"] == 2

    def test_cancellation_interrupts_the_backoff_wait(self, monkeypatch):
        flag = {"stop": False}
        client, calls = _stub_client(
            monkeypatch, [_SDKish(429)], should_cancel=lambda: flag["stop"]
        )
        # The first failure schedules a 60 s wait; the caller cancels during it.
        monkeypatch.setattr(
            "cartolex.lexicon.mistral_client.time.sleep",
            lambda *_: flag.__setitem__("stop", True),
        )
        with pytest.raises(LLMCancelled):
            client.chat_text("sys", "user")
        assert calls["n"] == 1

    def test_cancellation_before_the_first_call_sends_nothing(self, monkeypatch):
        client, calls = _stub_client(monkeypatch, ["ok"], should_cancel=lambda: True)
        with pytest.raises(LLMCancelled):
            client.chat_text("sys", "user")
        assert calls["n"] == 0


# ── Adaptive throttle ─────────────────────────────────────────────────


class TestAdaptiveThrottle:
    def test_a_429_halves_the_ceiling_and_a_clean_streak_restores_it(self):
        throttle = AdaptiveThrottle(8, recover_after=2)
        assert throttle.limit == 8
        throttle.penalise(0.0)
        assert throttle.limit == 4
        throttle.penalise(0.0)
        assert throttle.limit == 2
        for _ in range(4):
            throttle.reward()
        assert throttle.limit == 4

    def test_the_ceiling_never_drops_below_one(self):
        throttle = AdaptiveThrottle(2)
        for _ in range(5):
            throttle.penalise(0.0)
        assert throttle.limit == 1

    def test_slots_beyond_the_ceiling_wait_for_a_release(self):
        throttle = AdaptiveThrottle(1)
        entered = threading.Event()
        released = threading.Event()

        def hold():
            with throttle.slot():
                entered.set()
                released.wait(2.0)

        worker = threading.Thread(target=hold, daemon=True)
        worker.start()
        assert entered.wait(2.0)

        got = threading.Event()

        def second():
            with throttle.slot():
                got.set()

        other = threading.Thread(target=second, daemon=True)
        other.start()
        assert not got.wait(0.3)  # the ceiling is full
        released.set()
        assert got.wait(2.0)  # released → the queued worker proceeds
        worker.join(2.0)
        other.join(2.0)

    def test_a_parked_fleet_refuses_new_slots_until_the_cooldown(self):
        throttle = AdaptiveThrottle(4)
        throttle.penalise(0.4)
        started = time.monotonic()
        with throttle.slot():
            waited = time.monotonic() - started
        assert waited >= 0.3

    def test_a_cancelled_run_does_not_queue_for_a_slot(self):
        throttle = AdaptiveThrottle(1)
        throttle.penalise(30.0)
        with pytest.raises(LLMCancelled):
            with throttle.slot(should_cancel=lambda: True):
                pass


# ── End-to-end runner behaviour ───────────────────────────────────────


_TERMS = [
    f"{adjective} {noun}"
    for adjective in (
        "soft",
        "hard",
        "dark",
        "active",
        "granular",
        "quantum",
        "thermal",
        "magnetic",
    )
    for noun in ("matter", "optics", "transport", "dynamics", "imaging")
]  # 40 distinct terms → 40 batches at batch_size=1


class _FailingClient:
    """Stand-in whose calls fail the way a refused key does."""

    def __init__(self, **kwargs):
        self.calls = 0
        _FailingClient.instance = self

    def chat_text(self, system, user, cache_key=None):
        type(self).seen += 1
        time.sleep(0.02)  # let the abort reach the workers, as a real call would
        raise LLMError("auth", "auth (HTTP 401): Unauthorized")

    seen = 0
    instance: object = None


class TestRunnerFailsFast:
    def test_a_refused_key_aborts_the_run_without_walking_every_batch(self, tmp_path, monkeypatch):
        import cartolex.lexicon.triage_typed as tt

        _FailingClient.seen = 0
        monkeypatch.setattr(tt, "MistralClient", _FailingClient)

        started = time.monotonic()
        with pytest.raises(LLMError) as excinfo:
            run_typed_triage(
                global_terms=_TERMS,
                domain_title="Synthetic domain",
                api_key="k",
                batch_size=1,
                cache_path=tmp_path / "llm_cache.json",
                term_cache_path=tmp_path / "term_cache.json",
                max_concurrent=4,
            )
        assert excinfo.value.kind == "auth"
        assert time.monotonic() - started < 10.0
        # 40 batches were queued; the abort stops the run long before they all run.
        assert _FailingClient.seen < len(set(_TERMS))

    def test_cancellation_stops_the_run(self, tmp_path, monkeypatch):
        import cartolex.lexicon.triage_typed as tt

        class _SlowClient:
            def __init__(self, should_cancel=None, **kwargs):
                self.should_cancel = should_cancel

            def chat_text(self, system, user, cache_key=None):
                type(self).seen += 1
                if self.should_cancel and self.should_cancel():
                    raise LLMCancelled()
                time.sleep(0.02)
                return "\n".join(f"N {t}" for t in __import__("json").loads(user))

            seen = 0

        monkeypatch.setattr(tt, "MistralClient", _SlowClient)
        stop = {"v": False}

        def should_cancel():
            if _SlowClient.seen >= 2:
                stop["v"] = True
            return stop["v"]

        with pytest.raises(LLMCancelled):
            run_typed_triage(
                global_terms=_TERMS,
                domain_title="Synthetic domain",
                api_key="k",
                batch_size=1,
                cache_path=tmp_path / "llm_cache.json",
                term_cache_path=tmp_path / "term_cache.json",
                max_concurrent=2,
                should_cancel=should_cancel,
            )


# ── Key probe ─────────────────────────────────────────────────────────


class TestCheckKey:
    def test_a_working_key_reports_ok_and_a_latency(self, monkeypatch):
        class FakeMistral:
            def __init__(self, **kwargs):
                msg = types.SimpleNamespace(content="pong", model_dump=lambda: {})
                self.chat = types.SimpleNamespace(
                    complete=lambda **_kw: types.SimpleNamespace(
                        choices=[types.SimpleNamespace(message=msg)], usage=None
                    )
                )

        monkeypatch.setattr("mistralai.Mistral", FakeMistral)
        out = check_key("k", model="m")
        assert out["ok"] is True and out["kind"] == "ok" and out["model"] == "m"
        assert isinstance(out["latency_ms"], int)

    def test_a_refused_key_reports_the_reason_instead_of_raising(self, monkeypatch):
        class FakeMistral:
            def __init__(self, **kwargs):
                def complete(**_kw):
                    raise _SDKish(401, "Unauthorized")

                self.chat = types.SimpleNamespace(complete=complete)

        monkeypatch.setattr("mistralai.Mistral", FakeMistral)
        out = check_key("k")
        assert out["ok"] is False and out["kind"] == "auth" and out["status"] == 401
