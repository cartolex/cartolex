# SPDX-License-Identifier: MIT
"""The keys of the AI answer caches never change.

A workspace keeps every AI triage answer it has paid for in two caches: one
entry per batch of terms (``llm_cache.json``) and one per term
(``llm_term_cache.json``). A changed key is a cache miss, and a cache miss is a
new paid call for an answer the user already has. The expected keys in
``fixtures/ai_cache_keys.json`` were produced by the released engine; this test
asserts that the current code reproduces every one of them exactly. Never
regenerate the fixture to make this test pass.
"""

from __future__ import annotations

import json
import sys
import types
import unicodedata
from pathlib import Path

import pytest

from cartolex.lexicon.llm_filter import TermDecisionCache
from cartolex.lexicon.mistral_client import _cache_key
from cartolex.lexicon.triage_typed import run_typed_triage

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ai_cache_keys.json").read_text(encoding="utf-8")
)


def test_fixture_covers_the_awkward_inputs() -> None:
    """The samples exercise several models, accents, case, spacing, punctuation and edge batches."""
    batches = FIXTURE["batch_keys"]
    assert len({s["model"] for s in batches}) >= 3
    all_terms = [t for s in batches for t in s["terms"]]
    assert any(t != t.lower() for t in all_terms)
    assert any(any(ord(c) > 127 for c in t) for t in all_terms)
    assert any("  " in t or t != t.strip() for t in all_terms)
    assert any(any(c in t for c in "'();:-+") for t in all_terms)
    assert any(unicodedata.normalize("NFC", t) != t for t in all_terms)
    assert any(s["terms"] == [] for s in batches)
    assert any(len(s["terms"]) != len(set(s["terms"])) for s in batches)
    assert any(s["domain"] == "12 — Coastal and marine systems" for s in batches)


@pytest.mark.parametrize("sample", FIXTURE["batch_keys"], ids=lambda s: s["key"][:12])
def test_batch_key_is_unchanged(sample: dict) -> None:
    """Batch key: phase, sorted terms, domain title and model hashed together."""
    key = _cache_key(sample["phase"], list(sample["terms"]), sample["domain"], sample["model"])
    assert key == sample["key"]


def test_term_keys_are_unchanged(tmp_path: Path) -> None:
    """Per-term keys, as written to disk and as looked up."""
    path = tmp_path / "term_cache.json"
    cache = TermDecisionCache(path)
    for s in FIXTURE["term_keys"]:
        cache.put(s["phase"], s["term"], s["domain"], s["model"], {"verdict": "C"})
    cache.flush()
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        s["key"] for s in FIXTURE["term_keys"]
    }
    reloaded = TermDecisionCache(path)
    for s in FIXTURE["term_keys"]:
        assert reloaded.get(s["phase"], s["term"], s["domain"], s["model"]) == {"verdict": "C"}


class _FakeClient:
    """Answers every term as an accepted concept, in the typed triage line format."""

    def __init__(self, **_: object) -> None:
        self.chat = self

    def complete(self, *, model: str, messages: list[dict], temperature: float, **_: object):
        terms = json.loads(messages[-1]["content"])
        content = "\n".join(f"C en {t}={t}" for t in terms)
        message = types.SimpleNamespace(content=content, model_dump=lambda: {"content": content})
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)], usage=None)


def test_triage_writes_the_frozen_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: the typed triage stores its answers under exactly the frozen keys."""
    fake_sdk = types.ModuleType("mistralai")
    fake_sdk.Mistral = _FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mistralai", fake_sdk)
    sample = FIXTURE["triage_sample"]
    batch_cache, term_cache = tmp_path / "batch.json", tmp_path / "terms.json"
    run_typed_triage(
        global_terms=list(sample["terms"]),
        domain_title=sample["domain"],
        reference_keywords=[],
        api_key="test-key",
        model=sample["model"],
        batch_size=100,
        cache_path=batch_cache,
        term_cache_path=term_cache,
        max_concurrent=1,
    )
    assert sorted(json.loads(batch_cache.read_text(encoding="utf-8"))) == sample["batch_keys"]
    assert sorted(json.loads(term_cache.read_text(encoding="utf-8"))) == sample["term_keys"]
