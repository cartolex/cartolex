# SPDX-License-Identifier: MIT
"""Unit tests for the typed single-pass triage pipeline."""

from __future__ import annotations

import pytest

from cartolex.lexicon.triage_typed import (
    ACCEPT_CODES,
    REJECT_CODES,
    TypedDecision,
    _parse_typed_lines,
    build_typed_prompt,
    is_citation_artifact,
    post_check_typed,
    prefilter_terms,
)


class TestPrefilter:
    def test_structural_junk(self):
        _, rej = prefilter_terms(["truncated-", "**bold**", "12", "", "ab"])
        assert "truncated-" in rej
        assert "**bold**" in rej
        assert "12" in rej
        assert "ab" in rej

    def test_keeps_real_keywords(self):
        ok = ["soft matter", "atomic force microscopy", "polymer brush"]
        survivors, rej = prefilter_terms(ok)
        assert set(survivors) == set(ok)
        assert not rej

    def test_dedup_preserves_order(self):
        survivors, _ = prefilter_terms(["soft matter", "polymer brush", "soft matter"])
        assert survivors == ["soft matter", "polymer brush"]

    def test_admin_verb_lead_rejected(self):
        _, rej = prefilter_terms(
            ["comprendre origine", "développer une approche", "understand mechanism"]
        )
        assert "comprendre origine" in rej
        assert "développer une approche" in rej
        assert "understand mechanism" in rej
        assert all(r.code == "admin" for r in rej.values())

    def test_bare_generic_noun_rejected(self):
        _, rej = prefilter_terms(["measurement", "simulation", "function", "modèle"])
        for t in ["measurement", "simulation", "function", "modèle"]:
            assert t in rej
            assert rej[t].code == "generic"

    def test_name_geo_rejected(self):
        _, rej = prefilter_terms(["paris", "berlin", "heidelberg"])
        assert "paris" in rej
        assert "berlin" in rej
        assert "heidelberg" in rej

    def test_header_fragment_rejected(self):
        survivors, rej = prefilter_terms(
            ["polymères gels cristaux", "composites nanocomposites hybrides biomatériaux"]
        )
        assert "polymères gels cristaux" in rej
        assert rej["polymères gels cristaux"].code == "header"

    def test_function_word_only_rejected(self):
        _, rej = prefilter_terms(["de la", "of the"])
        assert "de la" in rej
        assert "of the" in rej

    def test_generic_lead_rejected(self):
        _, rej = prefilter_terms(["nouvelle thématique", "main result"])
        assert "nouvelle thématique" in rej
        assert "main result" in rej


class TestPrefilterLanguageParity:
    """PT/ES deterministic prefiltering, at parity with FR/EN (externalized lists)."""

    def test_portuguese_admin_verb_lead_rejected(self):
        _, rej = prefilter_terms(["desenvolver uma abordagem", "compreender origem"])
        assert rej["desenvolver uma abordagem"].code == "admin"
        assert "compreender origem" in rej

    def test_spanish_admin_verb_lead_rejected(self):
        _, rej = prefilter_terms(["desarrollar un enfoque", "comprender origen"])
        assert rej["desarrollar un enfoque"].code == "admin"
        assert "comprender origen" in rej

    def test_portuguese_bare_generic_rejected(self):
        # A bare generic noun not already in the extraction blacklist reaches the
        # bare-noun prefilter and is rejected as generic.
        _, rej = prefilter_terms(["estrutura", "medida"])
        for t in ["estrutura", "medida"]:
            assert t in rej and rej[t].code == "generic"

    def test_spanish_bare_generic_rejected(self):
        _, rej = prefilter_terms(["estructura", "tarea"])
        for t in ["estructura", "tarea"]:
            assert t in rej and rej[t].code == "generic"

    def test_portuguese_discourse_adjective_lead_rejected(self):
        _, rej = prefilter_terms(["nova abordagem experimental"])
        assert rej["nova abordagem experimental"].code == "generic"

    def test_fr_en_prefilter_unchanged(self):
        # The externalization must preserve FR/EN behaviour exactly.
        _, rej = prefilter_terms(["measurement", "mesure", "understand mechanism", "comprendre x"])
        assert rej["measurement"].code == "generic"
        assert rej["mesure"].code == "generic"
        assert rej["understand mechanism"].code == "admin"
        assert rej["comprendre x"].code == "admin"


class TestParser:
    def test_accept_line(self):
        out = _parse_typed_lines("C en soft matter=soft matter", ["soft matter"])
        d = out["soft matter"]
        assert d.verdict == "C"
        assert d.lang == "en"
        assert d.canonical_en == "soft matter"

    def test_accept_line_third_source_language(self):
        # A Portuguese-source accept line must parse, not fall through to the
        # conservative "F" reject (the old regex only matched en|fr).
        out = _parse_typed_lines("C pt materia mole=soft matter", ["materia mole"])
        d = out["materia mole"]
        assert d.verdict == "C"
        assert d.lang == "pt"
        assert d.canonical_en == "soft matter"

    def test_accept_with_translation(self):
        out = _parse_typed_lines("O fr matière molle=soft matter", ["matière molle"])
        d = out["matière molle"]
        assert d.verdict == "O"
        assert d.canonical_en == "soft matter"

    def test_method_line(self):
        out = _parse_typed_lines(
            "M en atomic force microscopy=atomic force microscopy",
            ["atomic force microscopy"],
        )
        assert out["atomic force microscopy"].verdict == "M"

    def test_reject_lines(self):
        out = _parse_typed_lines(
            "N paris\nK team leader\nG measurement\nF mesu",
            ["paris", "team leader", "measurement", "mesu"],
        )
        assert out["paris"].verdict == "N"
        assert out["team leader"].verdict == "K"
        assert out["measurement"].verdict == "G"
        assert out["mesu"].verdict == "F"

    def test_missing_term_defaults_to_F(self):
        out = _parse_typed_lines("C en a=a", ["a", "b"])
        assert out["b"].verdict == "F"

    def test_blank_lines_ignored(self):
        out = _parse_typed_lines("\n\nC en x=x\n\n", ["x"])
        assert out["x"].verdict == "C"


class TestPromptLanguage:
    def test_build_typed_prompt_defaults_to_english_reference(self):
        system, _user = build_typed_prompt(["x"], "Some domain", [])
        assert "English" in system

    def test_build_typed_prompt_uses_reference_language(self):
        system, _user = build_typed_prompt(["x"], "Some domain", [], reference_language="pt")
        assert "Portuguese" in system


class TestCitationArtifact:
    """Pure deterministic detector for citation artefacts ("vellmoor (ed.)")."""

    @pytest.mark.parametrize(
        "term",
        [
            "vellmoor (ed.)",
            "smith (eds.)",
            "durand (dir.)",
            "martin (dirs.)",
            "lefebvre (éd.)",
            "lefebvre (éds.)",
            "Vellmoor (Ed.)",
            "garcia (ed)",
        ],
    )
    def test_citation_artifacts_detected(self, term):
        assert is_citation_artifact(term)

    @pytest.mark.parametrize(
        "term",
        [
            "soft matter",
            "quintor",
            "ysolde marrow",
            "critical edition",
            "editor theory",
            "direction of magnetization",
            "",
        ],
    )
    def test_regular_terms_pass(self, term):
        assert not is_citation_artifact(term)

    def test_prefilter_rejects_citation_artifact(self):
        _, rej = prefilter_terms(["vellmoor (ed.)", "durand (dir.)"])
        assert rej["vellmoor (ed.)"].code == "citation"
        assert rej["durand (dir.)"].code == "citation"

    def test_post_check_demotes_citation_artifact_accept(self):
        # Defense-in-depth: even if the LLM accepts one, demote it to N.
        d = TypedDecision("O", "fr", "vellmoor (ed.)")
        out = post_check_typed("vellmoor (ed.)", d)
        assert out.verdict == "N"

    def test_post_check_demotes_citation_artifact_in_canonical(self):
        # Artefact introduced on the canonical side only.
        d = TypedDecision("O", "fr", "vellmoor (ed.)")
        out = post_check_typed("vellmoor", d)
        assert out.verdict == "N"


class TestPersonNamePolicy:
    """The triage prompt must carry the person-name keyword policy."""

    def _system(self) -> str:
        system, _user = build_typed_prompt(["x"], "Some domain", [])
        return system

    def test_prompt_has_person_names_rule_block(self):
        assert "PERSON NAMES" in self._system()

    def test_prompt_requires_research_object_fame(self):
        system = self._system()
        assert "research object" in system

    def test_prompt_requires_complete_recognizable_name(self):
        system = self._system()
        assert "complete, unambiguously recognizable name" in system
        assert "lone surname" in system

    def test_prompt_rejects_citation_artifacts(self):
        system = self._system()
        assert "citation artefact" in system
        assert "(ed.)" in system

    def test_prompt_excludes_contemporary_researchers(self):
        system = self._system()
        assert "Contemporary researchers" in system
        assert "co-author" in system

    def test_prompt_defaults_to_reject_when_in_doubt(self):
        assert "When in doubt about a person name, REJECT" in self._system()


class TestSubstantiveCanonicalPolicy:
    """The canonical-form instructions must prefer the discipline noun."""

    def _system(self) -> str:
        system, _user = build_typed_prompt(["x"], "Some domain", [])
        return system

    def test_prompt_prefers_substantive_over_adjective(self):
        system = self._system()
        assert "Prefer the substantive" in system
        assert '"hagiographic"' in system
        assert '"hagiography"' in system

    def test_prompt_keeps_adjective_derived_discipline_names(self):
        system = self._system()
        assert '"numismatics"' in system
        assert '"ceramics"' in system

    def test_prompt_falls_back_to_dominant_surface_form(self):
        assert "dominant surface form" in self._system()


class TestPostCheck:
    def _accept(self, canonical: str) -> TypedDecision:
        return TypedDecision("C", "fr", canonical)

    def test_reorder_with_connective_demoted(self):
        # input "eau air" --> LLM rescues as "air-water interface"
        d = self._accept("air water interface")
        out = post_check_typed("eau air", d)
        assert out.verdict == "F"

    def test_legitimate_fr_to_en_flip_preserved(self):
        # FR adj-after-noun -> EN adj-before-noun, no new word
        d = self._accept("soft matter")
        out = post_check_typed("matière molle", d)
        # token "matière"->"matter", "molle"->"soft"; no connective injected
        # multiset differs but no _CONNECTIVES introduced
        assert out.verdict == "C"

    def test_singular_drop_allowed(self):
        d = self._accept("polymer")
        out = post_check_typed("polymers", d)
        assert out.verdict == "C"

    def test_reject_passthrough(self):
        d = TypedDecision("N", "en", "freiburg")
        out = post_check_typed("freiburg", d)
        assert out.verdict == "N"

    def test_huge_length_growth_demoted(self):
        d = self._accept("a b c d e f")
        out = post_check_typed("ab", d)
        assert out.verdict == "F"


class TestPromptBuilder:
    def test_subfields_inlined(self):
        sys_prompt, user = build_typed_prompt(
            ["soft matter"], "08 — Physique", ["sub one", "sub two"]
        )
        assert "Physique" in sys_prompt
        assert "sub one" in sys_prompt
        assert "sub two" in sys_prompt
        assert "soft matter" in user

    def test_no_subfields_block(self):
        sys_prompt, _ = build_typed_prompt(["x"], "Test domain", [])
        assert "no reference subfields" in sys_prompt

    def test_response_codes_documented(self):
        sys_prompt, _ = build_typed_prompt(["x"], "s", ["k"])
        for code in ACCEPT_CODES | REJECT_CODES:
            assert f" {code} " in sys_prompt or f"\n  {code}" in sys_prompt


@pytest.mark.parametrize(
    "term,should_survive",
    [
        ("soft matter", True),
        ("atomic force microscopy", True),
        ("polymer brush", True),
        ("budding yeast", True),
        ("matière molle", True),
        ("measurement", False),
        ("simulation", False),
        ("nouvelle thématique", False),
        ("comprendre origine", False),
        ("polymères gels cristaux", False),
        ("paris", False),
        ("**bold**", False),
        ("de la", False),
    ],
)
def test_prefilter_smoke(term, should_survive):
    survivors, rejected = prefilter_terms([term])
    if should_survive:
        assert term in survivors, f"expected survivor: {term!r}, rejected as {rejected.get(term)}"
    else:
        assert term in rejected, f"expected reject: {term!r}"
