# SPDX-License-Identifier: MIT
"""Operator-curated person-name whitelist: loader and triage integration.

All names used here are fabricated; the common-name list of the prefilter
tests is a synthetic addition to the packaged stop words.
"""

from __future__ import annotations

from pathlib import Path

from cartolex.context import EnginePaths, RunContext
from cartolex.lexicon.config import KeywordsConfig
from cartolex.lexicon.stopwords_config import packaged_lists
from cartolex.lexicon.triage_typed import (
    TypedDecision,
    build_typed_prompt,
    post_check_typed,
    prefilter_terms,
    run_typed_triage,
)
from cartolex.lexicon.whitelist import (
    build_whitelist_set,
    load_person_whitelist,
)

#: The packaged lists plus synthetic person names, as a workspace override adds them.
_NAMES = packaged_lists().with_overrides(
    {"add": {"person_names": ["zelmork", "ilse", "quillane", "émerine"]}}
)


def _whitelist_path(workspace: Path) -> Path:
    """The person whitelist file of a workspace (as its run context names it)."""
    return EnginePaths.for_workspace(workspace).person_whitelist_csv


def _write_whitelist(workspace: Path, text: str) -> Path:
    path = _whitelist_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadPersonWhitelist:
    def test_absent_file_yields_empty_set(self, tmp_path):
        assert load_person_whitelist(_whitelist_path(tmp_path)) == set()

    def test_no_path_yields_empty_set(self):
        assert load_person_whitelist(None) == set()

    def test_loads_names_as_written(self, tmp_path):
        path = _write_whitelist(tmp_path, "name\nQuintor\nBrevanne\nYsolde Marrow\n")
        assert load_person_whitelist(path) == {"Quintor", "Brevanne", "Ysolde Marrow"}

    def test_header_row_is_optional(self, tmp_path):
        path = _write_whitelist(tmp_path, "Quintor\nBrevanne\n")
        assert load_person_whitelist(path) == {"Quintor", "Brevanne"}

    def test_blank_lines_and_whitespace_ignored(self, tmp_path):
        path = _write_whitelist(tmp_path, "name\n\n  Quintor  \n   \nBrevanne\n")
        assert load_person_whitelist(path) == {"Quintor", "Brevanne"}

    def test_utf8_bom_tolerated(self, tmp_path):
        path = _write_whitelist(tmp_path, "")
        path.write_bytes(b"\xef\xbb\xbfname\r\nQuintor\r\n")
        assert load_person_whitelist(path) == {"Quintor"}

    def test_undecodable_file_yields_empty_set(self, tmp_path):
        path = _write_whitelist(tmp_path, "")
        path.write_bytes(b"\xff\xfe\x00\x00garbage")
        assert load_person_whitelist(path) == set()


class TestPrefilterBypass:
    """Whitelisted person names must survive the deterministic prefilter."""

    def test_common_name_rejected_without_whitelist(self):
        # Control: "zelmork" sits in the common-name list of these tests.
        _, rej = prefilter_terms(["zelmork"], stopwords=_NAMES)
        assert rej["zelmork"].code == "name_geo"

    def test_whitelisted_common_name_survives(self):
        survivors, rej = prefilter_terms(
            ["zelmork"], person_whitelist={"Zelmork"}, stopwords=_NAMES
        )
        assert survivors == ["zelmork"]
        assert not rej

    def test_whitelisted_full_name_bigram_survives(self):
        # "ilse quillane" is a pure common-name bigram, normally rejected.
        _, rej = prefilter_terms(["ilse quillane"], stopwords=_NAMES)
        assert "ilse quillane" in rej
        survivors, _ = prefilter_terms(
            ["ilse quillane"], person_whitelist={"Ilse Quillane"}, stopwords=_NAMES
        )
        assert survivors == ["ilse quillane"]

    def test_matching_is_case_insensitive(self):
        survivors, _ = prefilter_terms(["ZELMORK"], person_whitelist={"zelmork"}, stopwords=_NAMES)
        assert survivors == ["ZELMORK"]

    def test_matching_is_accent_insensitive(self):
        survivors, _ = prefilter_terms(["émerine"], person_whitelist={"Emerine"}, stopwords=_NAMES)
        assert survivors == ["émerine"]

    def test_non_whitelisted_names_still_rejected(self):
        _, rej = prefilter_terms(["zelmork"], person_whitelist={"Quintor"}, stopwords=_NAMES)
        assert "zelmork" in rej


class TestPostCheckForceAccept:
    """Whitelisted person names must survive the deterministic post-check."""

    def test_llm_person_reject_flipped_to_object_accept(self):
        d = TypedDecision("N", "en", "brevanne")
        out = post_check_typed("brevanne", d, person_whitelist={"Brevanne"})
        assert out.verdict == "O"
        assert out.canonical_en == "brevanne"

    def test_flip_is_case_insensitive(self):
        d = TypedDecision("N", "fr", "ysolde marrow")
        out = post_check_typed("ysolde marrow", d, person_whitelist={"YSOLDE MARROW"})
        assert out.verdict == "O"

    def test_llm_accept_kept_with_its_canonical(self):
        d = TypedDecision("O", "en", "Quintor Magnus")
        out = post_check_typed("quintor", d, person_whitelist={"Quintor"})
        assert out.verdict == "O"
        assert out.canonical_en == "Quintor Magnus"

    def test_llm_accept_with_empty_canonical_repaired(self):
        d = TypedDecision("O", "en", "  ")
        out = post_check_typed("quintor", d, person_whitelist={"Quintor"})
        assert out.verdict == "O"
        assert out.canonical_en == "quintor"

    def test_non_whitelisted_reject_unchanged(self):
        d = TypedDecision("N", "en", "brevanne")
        out = post_check_typed("brevanne", d, person_whitelist={"Quintor"})
        assert out.verdict == "N"


class TestPromptMention:
    """The triage prompt must tell the LLM the whitelist is force-accepted."""

    def test_prompt_lists_whitelisted_names(self):
        system, _ = build_typed_prompt(
            ["x"], "Some domain", [], person_whitelist={"Quintor", "Ysolde Marrow"}
        )
        assert "names on the provided whitelist are accepted as research" in system
        assert "Quintor" in system
        assert "Ysolde Marrow" in system

    def test_prompt_without_whitelist_has_no_block(self):
        system, _ = build_typed_prompt(["x"], "Some domain", [])
        assert "provided whitelist" not in system

    def test_triage_stage_reads_the_workspace_whitelist(self, tmp_path, caplog):
        import logging

        from cartolex.lexicon.llm_triage import run_pipeline_stage_2_llm

        _write_whitelist(tmp_path, "name\nYsolde Marrow\n")
        ctx = RunContext.for_workspace(tmp_path, KeywordsConfig(domain_title="Some domain"))
        ctx.paths.global_terms_csv.parent.mkdir(parents=True, exist_ok=True)
        ctx.paths.global_terms_csv.write_text("term,score_len\nsoft matter,1.0\n")
        with caplog.at_level(logging.INFO, logger="cartolex.lexicon.llm_triage"):
            run_pipeline_stage_2_llm(ctx, dry_run=True)
        assert "Ysolde Marrow" in caplog.text


class _StubClient:
    """Offline stand-in for MistralClient: rejects every term with N."""

    last_system: str = ""

    def __init__(self, **kwargs):
        pass

    def chat_text(self, system: str, user: str, cache_key: str | None = None) -> str:
        import json

        _StubClient.last_system = system
        terms = json.loads(user)
        return "\n".join(f"N {t}" for t in terms)


class TestRunTypedTriageWiring:
    def test_whitelisted_name_force_accepted_end_to_end(self, tmp_path, monkeypatch):
        import cartolex.lexicon.triage_typed as tt

        path = _write_whitelist(tmp_path, "name\nQuintor\n")
        monkeypatch.setattr(tt, "MistralClient", _StubClient)

        result = run_typed_triage(
            global_terms=["quintor", "soft matter"],
            domain_title="Synthetic domain",
            reference_keywords=[],
            api_key="k",
            cache_path=tmp_path / "llm_cache.json",
            term_cache_path=tmp_path / "term_cache.json",
            max_concurrent=1,
            person_whitelist=load_person_whitelist(path),
        )

        assert "quintor" in result["accepted"]
        assert result["typed"]["quintor"]["verdict"] == "O"
        assert result["canonical_map"]["quintor"] == "quintor"
        assert "quintor" not in result["reject_reasons"]
        # The LLM stub still saw the whitelist mention in its system prompt.
        assert "Quintor" in _StubClient.last_system
        # The non-whitelisted term keeps the LLM's reject.
        assert "soft matter" in result["rejected"]


class TestBuildWhitelistSet:
    """Consolidation's whitelist_set = axis whitelist ∪ person whitelist."""

    def test_union_is_lowercased(self):
        out = build_whitelist_set({"Active Matter"}, {"Quintor", "Ysolde Marrow"})
        assert out == {"active matter", "quintor", "ysolde marrow"}

    def test_empty_person_whitelist_reproduces_axis_set(self):
        assert build_whitelist_set({"soft matter"}) == {"soft matter"}

    def test_blank_entries_dropped(self):
        assert build_whitelist_set({" ", ""}, {"", "  Quintor "}) == {"quintor"}
