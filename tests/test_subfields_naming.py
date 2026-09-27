# SPDX-License-Identifier: MIT
"""Draft post-processing kept from the LLM era: label distinctness, generality, LLM cache."""

from __future__ import annotations

import numpy as np

from cartolex.lexicon.subfields import _enforce_label_distinctness


def _dup_hierarchy() -> dict:
    return {
        "subfields": [
            {
                "id": 0,
                "label": "Complex Fluid Dynamics",
                "label_fr": "Dynamique des fluides",
                "top_terms": ["turbulence", "rheology"],
            },
            {
                "id": 1,
                "label": "Complex Fluid Dynamics",
                "label_fr": "Dynamique des fluides",
                "top_terms": ["granular", "sand dune"],
            },
        ],
    }


def test_distinctness_dedups_sibling_subfields():
    h = _dup_hierarchy()
    _enforce_label_distinctness(h)
    labels = [s["label"] for s in h["subfields"]]
    assert len(set(labels)) == 2  # collision resolved
    assert labels[0] == "Complex Fluid Dynamics"  # first kept
    assert "granular" in labels[1]  # disambiguated by a distinguishing top-term
    assert len({s["label_fr"] for s in h["subfields"]}) == 2  # FR distinct too


def test_recompute_generality_never_flags_an_empty_subfield_general():
    from cartolex.lexicon import subfields as sf

    Zn = np.eye(3)
    h = {
        "concepts": [{"id": 0, "term_indices": [0, 1]}],
        "subfields": [
            {"id": 0, "concept_ids": [0], "general": False},
            {"id": 1, "concept_ids": [], "general": False},  # empty: must never be "general"
        ],
    }
    sf._recompute_generality(h, Zn)
    empty = next(s for s in h["subfields"] if not s["concept_ids"])
    assert empty["general"] is False
    assert h["subfields"][0]["general"] is True  # the real subfield gets the flag instead


def test_cached_chat_json_writes_dated_audit_with_prompt_and_thinking(tmp_path):
    import json
    import re

    from cartolex.lexicon import subfields as sf

    class FakeClient:
        # the real client stashes the full message (incl. reasoning) on `last_message`
        last_message = {"content": '{"ok": 1}', "reasoning_content": "because X"}

        def chat_json(self, system_prompt, user_content, response_schema=None):
            return {"ok": 1}

    out = sf._cached_chat_json(
        FakeClient(),
        "SYS",
        "USER",
        response_schema={},
        model="mistral-medium-3.5",
        temperature=0.2,
        cache_dir=tmp_path,
        label="pass2-name-subfields",
    )
    assert out == {"ok": 1}
    files = list((tmp_path / "audit").glob("*.json"))
    assert len(files) == 1  # one dated audit record for the fresh call
    assert re.match(r"\d{4}-\d{2}-\d{2}_\d{6}__pass2-name-subfields__", files[0].name)
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["system_prompt"] == "SYS" and rec["user_content"] == "USER"
    assert rec["response"] == {"ok": 1} and "timestamp" in rec
    assert rec["message"]["reasoning_content"] == "because X"  # thinking captured
