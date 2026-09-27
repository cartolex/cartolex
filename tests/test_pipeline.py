# SPDX-License-Identifier: MIT
"""Tests for the keyword extraction pipeline core modules.

Run with: pytest tests/ -v
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cartolex.lexicon.canonicalization import canonical_singular, resolve_canonical
from cartolex.lexicon.config import KeywordsConfig
from cartolex.lexicon.lang_utils import detect_language_term
from cartolex.lexicon.lexical_filters import (
    filter_global_terms,
    is_garbage,
    is_malformed_term,
)
from cartolex.lexicon.lexicon_store import load_canonical_decision_blacklist
from cartolex.lexicon.text_utils import heal_split_words, length_bonus

# ---------------------------------------------------------------------------
# canonicalization.py
# ---------------------------------------------------------------------------


class TestCanonicalSingular:
    @pytest.mark.parametrize(
        "term, expected",
        [
            # English plurals
            ("networks", "network"),
            ("properties", "property"),
            ("processes", "process"),
            ("process", "process"),
            ("neural networks", "neural network"),
            # French plurals
            ("réseaux", "réseau"),
            ("matériaux", "matérial"),
            ("niveaux", "niveau"),
            ("réseaux de neurones", "réseau de neuron"),
            ("réseau de neurone", "réseau de neuron"),
            ("éditions critiques", "édition critiqu"),
            ("édition critique", "édition critiqu"),
            ("critical editions", "critical edition"),
            ("états de surface", "état de surfac"),
            # Short words should NOT be trimmed (len <= 3)
            ("gas", "gas"),
            ("bus", "bus"),
            # Already singular
            ("analysis", "analysi"),  # naive rule, expected
            ("analyses", "analys"),
            ("analyse", "analys"),
            ("quantum", "quantum"),
        ],
    )
    def test_singular(self, term, expected):
        assert canonical_singular(term) == expected

    def test_empty(self):
        assert canonical_singular("") == ""
        assert canonical_singular("   ") == "   "  # whitespace preserved


class TestResolveCanonical:
    def test_no_mapping(self):
        assert resolve_canonical("abc", {}) == "abc"

    def test_single_hop(self):
        assert resolve_canonical("a", {"a": "b"}) == "b"

    def test_chain(self):
        assert resolve_canonical("a", {"a": "b", "b": "c"}) == "c"

    def test_cycle_protection(self):
        # Should not loop forever
        result = resolve_canonical("a", {"a": "b", "b": "a"})
        assert result in ("a", "b")


# ---------------------------------------------------------------------------
# lexical_filters.py
# ---------------------------------------------------------------------------


class TestIsGarbage:
    def test_short_token_rejected(self):
        # "xx" is not a scientific abbreviation
        assert is_garbage("xx spectroscopy") is True

    def test_scientific_short_tokens_kept(self):
        # These should survive the filter
        assert is_garbage("ph measurement") is False
        assert is_garbage("uv spectroscopy") is False
        assert is_garbage("2d materials") is False
        assert is_garbage("3d printing") is False
        assert is_garbage("ab initio") is False
        assert is_garbage("ml models") is False

    def test_normal_terms_kept(self):
        assert is_garbage("machine learning") is False
        assert is_garbage("neural network") is False

    def test_very_short_unknown_rejected(self):
        assert is_garbage("zq analysis") is True


class TestIsMalformedTerm:
    def test_rejects_web_and_url_junk(self):
        for t in (
            "https://en.wikipedia.org/wiki/mutant",
            "en.wikipedia.org/wiki/mutant",  # scheme-less domain
            "www.example.com",
            "see http://foo",
            "nature.com",
        ):
            assert is_malformed_term(t) is True, t

    def test_rejects_encoding_garbage(self):
        for t in (
            "protā¨ines",  # mojibake: Latin-A + standalone diaeresis ¨
            "bad�term",  # Unicode replacement char
            "ctrl\x07char",  # C0 control char
        ):
            assert is_malformed_term(t) is True, t

    def test_keeps_legitimate_scientific_terms(self):
        for t in (
            "h2o",
            "2d materials",
            "β-catenin",  # β-catenin
            "α-helix",  # α-helix
            "e. coli",
            "p53",
            "co-immunoprecipitation",
            "5' utr",
            "t-cell",
            "machine learning",
            "rho1 activity",
        ):
            assert is_malformed_term(t) is False, t


class TestFilterGlobalTerms:
    def test_pure_digit_terms_removed(self):
        df = pd.DataFrame(
            {
                "term": ["machine learning", "2021", "42", "h2o", "covid19"],
                "score": [1.0, 1.0, 1.0, 1.0, 1.0],
            }
        )
        result = filter_global_terms(
            df,
            names=set(),
            blacklist=set(),
            midwords=set(),
            single_blacklist=set(),
            admin_patterns=[],
            junk_patterns=[],
        )
        terms = set(result["term"])
        # Pure digit terms should be removed
        assert "2021" not in terms
        assert "42" not in terms
        # Terms containing digits but also letters should survive
        assert "h2o" in terms
        assert "covid19" in terms
        assert "machine learning" in terms

    def test_malformed_terms_removed(self):
        df = pd.DataFrame(
            {
                "term": [
                    "machine learning",
                    "en.wikipedia.org/wiki/mutant",
                    "protā¨ines",
                ],
                "score": [1.0, 1.0, 1.0],
            }
        )
        result = filter_global_terms(
            df,
            names=set(),
            blacklist=set(),
            midwords=set(),
            single_blacklist=set(),
            admin_patterns=[],
            junk_patterns=[],  # no config patterns — the deterministic gate must still fire
        )
        terms = set(result["term"])
        assert "machine learning" in terms
        assert "en.wikipedia.org/wiki/mutant" not in terms
        assert "protā¨ines" not in terms


# ---------------------------------------------------------------------------
# lexicon_store.py  — decision round-trip
# ---------------------------------------------------------------------------


class TestCanonicalDecisionBlacklist:
    def test_s_means_blacklist(self):
        """'s' decisions blacklist both terms in the pair."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"alpha|||beta": "s"}, f)
            f.flush()
            result = load_canonical_decision_blacklist(Path(f.name))
        assert "alpha" in result
        assert "beta" in result

    def test_n_means_keep_separate(self):
        """'n' decisions should NOT blacklist either term."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"alpha|||beta": "n"}, f)
            f.flush()
            result = load_canonical_decision_blacklist(Path(f.name))
        assert "alpha" not in result
        assert "beta" not in result

    def test_y_means_merge_not_blacklist(self):
        """'y' decisions should NOT blacklist either term."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"alpha|||beta": "y"}, f)
            f.flush()
            result = load_canonical_decision_blacklist(Path(f.name))
        assert "alpha" not in result
        assert "beta" not in result


# ---------------------------------------------------------------------------
# config.py  — KeywordsConfig
# ---------------------------------------------------------------------------


class TestKeywordsConfig:
    def test_defaults_match_protocol(self):
        cfg = KeywordsConfig()
        assert cfg.min_df == 3
        assert cfg.max_df == 0.6
        assert cfg.length_bonus_alpha == 2.0
        assert cfg.ngram_range == (1, 4)

    def test_min_df_is_int(self):
        cfg = KeywordsConfig()
        assert isinstance(cfg.min_df, int)


# ---------------------------------------------------------------------------
# text_utils.py
# ---------------------------------------------------------------------------


class TestLengthBonus:
    def test_unigram_no_bonus(self):
        scores, lengths = length_bonus(["word"], np.array([1.0]), alpha=2.0)
        assert lengths[0] == 1.0
        assert scores[0] == pytest.approx(1.0)  # 1 * (1 + 2*(1-1)) = 1

    def test_bigram_gets_bonus(self):
        scores, lengths = length_bonus(["two words"], np.array([1.0]), alpha=2.0)
        assert lengths[0] == 2.0
        assert scores[0] == pytest.approx(3.0)  # 1 * (1 + 2*(2-1)) = 3


class TestHealSplitWords:
    """Rejoin words PDF extraction split mid-token (pypdf spurious spaces)."""

    def _corpus(self) -> list[str]:
        # 'adhesion' and 'cellules' appear >= min_real times as whole words.
        return [
            "cell adhesion molecule",
            "adhesion of cells",
            "membrane adhesion forces",
            "adhesion measured here",
            "cellules vivantes observed",
            "les cellules cibles",
            "cellules souches study",
            "imaging the cellules clearly",
        ]

    def test_rejoins_orphan_fragment(self):
        docs = [*self._corpus(), "the adh esion was strong", "of cell ules imaged"]
        healed, n = heal_split_words(docs, min_real=4)
        assert "adhesion" in healed[-2] and "adh esion" not in healed[-2]
        assert "cellules" in healed[-1] and "cell ules" not in healed[-1]
        assert n == 2

    def test_preserves_two_real_words(self):
        # Both sides real → never merged, even if the join is a real word.
        docs = [*self._corpus(), "data set analysis", "data set again", "the data set"]
        # 'data' and 'set' both occur >= min_real; 'dataset' never appears.
        healed, _ = heal_split_words(docs, min_real=2)
        assert all("data set" in h for h in healed[-3:])

    def test_case_and_surroundings_preserved(self):
        docs = [*self._corpus(), "Cell ules, observed."]
        healed, n = heal_split_words(docs, min_real=4)
        assert healed[-1] == "Cellules, observed."
        assert n == 1

    def test_no_merge_without_real_join(self):
        docs = ["foo bar baz", "qux foo bar"]  # no frequent join target
        healed, n = heal_split_words(docs, min_real=2)
        assert healed == docs and n == 0


# ---------------------------------------------------------------------------
# lang_utils.py
# ---------------------------------------------------------------------------


class TestDetectLanguageTerm:
    def test_french_markers(self):
        assert detect_language_term("analyse des données") == "fr"

    def test_english_markers(self):
        assert detect_language_term("analysis of data") == "en"

    def test_unknown_fallback(self):
        assert detect_language_term("xyz") == "unknown"


# ---------------------------------------------------------------------------
# stopwords_config.py  — the single-word blacklist is part of the packaged lists
# ---------------------------------------------------------------------------


class TestStopwordsConfig:
    def test_single_blacklist_exists(self):
        from cartolex.lexicon.stopwords_config import packaged_lists

        assert isinstance(packaged_lists().single_blacklist, frozenset)


class TestFoldNumberVariants:
    def test_variants_collapse_on_the_heaviest(self):
        from cartolex.lexicon.canonicalization import fold_number_variants

        weights = {
            "critical edition": 3.0,
            "critical editions": 5.0,
            "éditions critiques": 1.0,
            "édition critique": 2.0,
            "quantum": 1.0,
        }
        fold = fold_number_variants(weights)
        assert fold["critical edition"] == "critical editions"
        assert fold["critical editions"] == "critical editions"
        assert fold["éditions critiques"] == "édition critique"
        assert fold["quantum"] == "quantum"  # FR and EN are not merged here

    def test_ties_are_deterministic(self):
        from cartolex.lexicon.canonicalization import fold_number_variants

        fold = fold_number_variants({"networks": 1.0, "network": 1.0})
        assert fold["networks"] == fold["network"] == "network"
