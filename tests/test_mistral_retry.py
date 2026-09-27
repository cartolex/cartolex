# SPDX-License-Identifier: MIT
"""Retry policy of the Mistral client wrapper (no network — stubbed SDK).

The incident behind these tests: a 150-concept holistic call outgrew Mistral's
~15-min gateway window and 504'd; the old policy treated 504 as non-transient (raise
immediately, no retry), and a blind retry of the same payload would just burn another
window. Policy now: one retry for gateway timeouts, normal backoff for other 5xx/429.
"""

from __future__ import annotations

import types

import pytest

from cartolex.lexicon.mistral_client import MistralClient


def _client_with_stub(monkeypatch, outcomes):
    """A MistralClient whose SDK stub pops one outcome per call (Exception, or JSON str)."""
    client = MistralClient(api_key="k", model="m", max_retries=5)
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


def test_gateway_504_gets_exactly_one_retry(monkeypatch):
    err = RuntimeError('Status 504 ... "The upstream server is timing out"')
    client, calls = _client_with_stub(monkeypatch, [err])
    with pytest.raises(RuntimeError, match="504"):
        client.chat_json("sys", "user")
    assert calls["n"] == 2  # initial attempt + exactly one retry, then the caller splits


def test_gateway_504_blip_recovers_on_the_single_retry(monkeypatch):
    err = RuntimeError("Status 504")
    client, calls = _client_with_stub(monkeypatch, [err, '{"ok": 1}'])
    assert client.chat_json("sys", "user") == {"ok": 1}
    assert calls["n"] == 2


def test_other_5xx_keep_the_backoff_retries(monkeypatch):
    err = RuntimeError("Status 502 Bad Gateway")
    client, calls = _client_with_stub(monkeypatch, [err, err, '{"ok": 1}'])
    assert client.chat_json("sys", "user") == {"ok": 1}
    assert calls["n"] == 3


def test_non_transient_error_fails_immediately(monkeypatch):
    err = RuntimeError("Status 401 Unauthorized")
    client, calls = _client_with_stub(monkeypatch, [err])
    with pytest.raises(RuntimeError, match="401"):
        client.chat_json("sys", "user")
    assert calls["n"] == 1
