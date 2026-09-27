# SPDX-License-Identifier: MIT
"""Tests for the display-label translation pass (labels.fill_missing_label_sides).

The consolidation stage SELECTS the best raw term per display language and,
when a concept has no raw term in a language, echoes the concept string with
``score_<lang> = 0.0`` (the fallback marker).  ``fill_missing_label_sides``
fills exactly those sides via the LLM; corpus-attested labels are never
touched.  All clients here are stubs — no live LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from cartolex.lexicon.labels import fill_missing_label_sides

#: The refined-pairs file name (any name works: the functions take explicit paths).
PAIRS_FILENAME = "keywords_global_refined_pairs.csv"


class StubTranslateClient:
    """Stand-in for MistralClient: translates from a per-language table, records batches.

    ``table`` maps a human language name (e.g. ``"French"``) to a
    ``{concept: translation}`` dict.  The target language is recognised from
    the prompt's instruction sentence ("Translate each keyword into <name>."),
    not from a loose substring — the shipped prompt's examples mention other
    languages by name.  Unknown concepts are simply absent from the response
    (strict-JSON mapping contract).
    """

    model = "stub-translate"
    temperature = 0.0
    last_message = {"content": "{}"}

    def __init__(self, table: dict[str, dict[str, str]]):
        self.table = table
        self.calls: list[tuple[str, list[str]]] = []

    def chat_json(self, system_prompt, user_content, response_schema=None):
        batch = json.loads(user_content)
        self.calls.append((system_prompt, list(batch)))
        out: dict[str, str] = {}
        for lang_name, mapping in self.table.items():
            if f"Translate each keyword into {lang_name}." in system_prompt:
                for concept in batch:
                    if concept in mapping:
                        out[concept] = mapping[concept]
        return out


class RaisingClient:
    """A client that must never be reached (cache must answer instead)."""

    model = "stub-translate"
    temperature = 0.0
    last_message = {"content": "{}"}

    def chat_json(self, system_prompt, user_content, response_schema=None):
        raise AssertionError("LLM client called despite a warm cache")


def _write_pairs(tmp_path: Path, df: pd.DataFrame) -> Path:
    pairs = tmp_path / PAIRS_FILENAME
    df.to_csv(pairs, index=False)
    return pairs


def _fixture_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {  # fully attested in both languages → must never reach the LLM
                "concept": "active matter",
                "term_fr": "matière active",
                "score_fr": 2.5,
                "term_en": "active matter",
                "score_en": 3.0,
                "total_score": 5.5,
            },
            {  # FR side = consolidation fallback (concept echoed, score 0.0)
                "concept": "machine learning",
                "term_fr": "machine learning",
                "score_fr": 0.0,
                "term_en": "machine learning",
                "score_en": 3.0,
                "total_score": 3.0,
            },
            {  # blank FR label (defensive marker) → also needs translation
                "concept": "graphene",
                "term_fr": "",
                "score_fr": 1.0,
                "term_en": "graphene",
                "score_en": 2.0,
                "total_score": 3.0,
            },
        ]
    )


_FR_TABLE = {
    "French": {
        "machine learning": "apprentissage automatique",
        "graphene": "graphène",
    }
}


def test_fills_fallback_rows_and_leaves_attested_untouched(tmp_path: Path) -> None:
    pairs = _write_pairs(tmp_path, _fixture_df())
    client = StubTranslateClient(_FR_TABLE)

    report = fill_missing_label_sides(pairs, languages=("fr", "en"), client=client)

    df = pd.read_csv(pairs)
    # Fallback / blank FR sides got the translation.
    assert df.loc[1, "term_fr"] == "apprentissage automatique"
    assert df.loc[2, "term_fr"] == "graphène"
    # Corpus-attested row untouched.
    assert df.loc[0, "term_fr"] == "matière active"
    assert df.loc[0, "score_fr"] == 2.5
    # score_fr stays 0.0: it now means "translated, not corpus-attested".
    assert df.loc[1, "score_fr"] == 0.0
    # EN side fully attested → no EN call; only the two FR concepts were sent.
    sent = sorted(c for _, batch in client.calls for c in batch)
    assert sent == ["graphene", "machine learning"]
    assert report["filled"] == {"fr": 2, "en": 0}
    assert report["untranslated"] == {"fr": 0, "en": 0}


def test_cache_prevents_retranslation(tmp_path: Path) -> None:
    """Re-running on the same fallback state answers from the on-disk cache."""
    pairs = _write_pairs(tmp_path, _fixture_df())
    cache_dir = tmp_path / "llm_cache" / "labels_translate"
    fill_missing_label_sides(
        pairs, languages=("fr",), client=StubTranslateClient(_FR_TABLE), cache_dir=cache_dir
    )
    # Reset the CSV to the pre-translation state, then re-run with a client
    # that fails on any real call: the cache must supply every response.
    _write_pairs(tmp_path, _fixture_df())
    report = fill_missing_label_sides(
        pairs, languages=("fr",), client=RaisingClient(), cache_dir=cache_dir
    )
    df = pd.read_csv(pairs)
    assert df.loc[1, "term_fr"] == "apprentissage automatique"
    assert report["filled"] == {"fr": 2}


def test_partial_llm_failure_leaves_rows_and_counts_them(tmp_path: Path) -> None:
    """A failing batch is skipped (rows left as-is) — the pass still completes."""

    class FlakyClient(StubTranslateClient):
        def chat_json(self, system_prompt, user_content, response_schema=None):
            if "graphene" in json.loads(user_content):
                raise RuntimeError("simulated API failure")
            return super().chat_json(system_prompt, user_content, response_schema)

    pairs = _write_pairs(tmp_path, _fixture_df())
    report = fill_missing_label_sides(
        pairs, languages=("fr",), client=FlakyClient(_FR_TABLE), batch_size=1
    )
    df = pd.read_csv(pairs)
    assert df.loc[1, "term_fr"] == "apprentissage automatique"
    # The failed row keeps its fallback state untouched.
    assert df.loc[2, "score_fr"] == 1.0
    assert report == {"filled": {"fr": 1}, "untranslated": {"fr": 1}}


def test_concept_missing_from_response_left_untranslated(tmp_path: Path) -> None:
    """A response omitting a concept (or blank) leaves that row as-is, counted."""
    pairs = _write_pairs(tmp_path, _fixture_df())
    table = {"French": {"machine learning": "apprentissage automatique", "graphene": "  "}}
    report = fill_missing_label_sides(pairs, languages=("fr",), client=StubTranslateClient(table))
    df = pd.read_csv(pairs)
    assert df.loc[1, "term_fr"] == "apprentissage automatique"
    assert report == {"filled": {"fr": 1}, "untranslated": {"fr": 1}}


def test_batches_respect_batch_size(tmp_path: Path) -> None:
    concepts = [f"concept {i}" for i in range(5)]
    df = pd.DataFrame(
        {
            "concept": concepts,
            "term_fr": concepts,
            "score_fr": [0.0] * 5,
            "term_en": concepts,
            "score_en": [1.0] * 5,
            "total_score": [1.0] * 5,
        }
    )
    pairs = _write_pairs(tmp_path, df)
    table = {"French": {c: f"concept français {i}" for i, c in enumerate(concepts)}}
    client = StubTranslateClient(table)
    report = fill_missing_label_sides(pairs, languages=("fr",), client=client, batch_size=2)
    assert [len(batch) for _, batch in client.calls] == [2, 2, 1]
    assert report["filled"] == {"fr": 5}


def test_missing_language_columns_are_created(tmp_path: Path) -> None:
    """A language without columns (e.g. added post-run): every row needs a side.

    ``term_<lang>`` is created (translation, else concept fallback) and
    ``score_<lang>`` is created at 0.0 — nothing is corpus-attested in a
    language the run never extracted.
    """
    df = _fixture_df()
    pairs = _write_pairs(tmp_path, df)
    table = {
        "Portuguese": {"active matter": "matéria ativa", "graphene": "grafeno"},
        "French": _FR_TABLE["French"],
    }
    report = fill_missing_label_sides(
        pairs, languages=("fr", "pt"), client=StubTranslateClient(table)
    )
    out = pd.read_csv(pairs)
    assert out.loc[0, "term_pt"] == "matéria ativa"
    assert out.loc[2, "term_pt"] == "grafeno"
    # Untranslated PT side falls back to the concept string, score 0.0 everywhere.
    assert out.loc[1, "term_pt"] == "machine learning"
    assert list(out["score_pt"]) == [0.0, 0.0, 0.0]
    assert report["filled"] == {"fr": 2, "pt": 2}
    assert report["untranslated"] == {"fr": 0, "pt": 1}


def test_rewrite_is_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    pairs = _write_pairs(tmp_path, _fixture_df())
    fill_missing_label_sides(pairs, languages=("fr",), client=StubTranslateClient(_FR_TABLE))
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_write_preserves_original_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pairs = _write_pairs(tmp_path, _fixture_df())
    original = pairs.read_text(encoding="utf-8")

    def _boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_csv", _boom)
    with pytest.raises(OSError):
        fill_missing_label_sides(pairs, languages=("fr",), client=StubTranslateClient(_FR_TABLE))
    assert pairs.read_text(encoding="utf-8") == original


def test_missing_pairs_csv_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Pairs file not found"):
        fill_missing_label_sides(
            tmp_path / PAIRS_FILENAME, languages=("fr",), client=StubTranslateClient({})
        )


def test_translate_prompt_carries_required_conventions() -> None:
    """The shipped prompt must pin register, proper-noun, discipline and century rules."""
    from cartolex.lexicon.prompt_store import load_prompt

    text = load_prompt("labels_translate_system", required_placeholders=("{language_name}",)).text
    lowered = text.lower()
    assert "proper nouns" in lowered
    assert "archéologie" in text  # discipline-name example (archaeology → archéologie)
    assert "siècle" in text  # chronological-convention example (12th century ↔ xiie siècle)
    assert "JSON" in text
