# SPDX-License-Identifier: MIT
"""The parse cache: round trip, keys, invalidation, and parsing only what is new.

No language model is needed: analyses are built by hand, and the parser is
replaced by a counting stand-in where a test needs one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cartolex.lexicon import extract_raw
from cartolex.lexicon import language_models as lm
from cartolex.lexicon.noun_phrases import PATTERN_VERSION, TextAnalysis
from cartolex.lexicon.parse_cache import FORMAT, PART_SIZE, ParseCache, text_key


def analysis(word: str) -> TextAnalysis:
    return TextAnalysis(runs=(((word, "N"),),), lemmas=((word, word, 1),))


def test_round_trip(tmp_path: Path) -> None:
    cache = ParseCache(tmp_path, "en_core_web_md@3.8.0")
    stored = {text_key(w): analysis(w) for w in ("tide", "wave", "sand-bar")}
    stored[text_key("x")] = TextAnalysis(
        runs=((("sand-gravel", "N", ("sand", "gravel")), ("beaches", "N")),),
        lemmas=(("beaches", "beach", 1), ("gravel", "gravel", 1), ("sand", "sand", 1)),
    )
    written = cache.write(stored)
    assert len(written) == 1
    assert cache.dir == tmp_path / "en_core_web_md-3.8.0" / PATTERN_VERSION
    assert ParseCache(tmp_path, "en_core_web_md@3.8.0").read() == stored
    # Only the wanted keys come back.
    one = text_key("tide")
    assert set(cache.read({one})) == {one}


def test_part_files_are_immutable_and_named_by_content(tmp_path: Path) -> None:
    cache = ParseCache(tmp_path, "en_core_web_md@3.8.0")
    first = cache.write({text_key("tide"): analysis("tide")})
    again = cache.write({text_key("tide"): analysis("tide")})
    assert first == again and len(list(cache.dir.iterdir())) == 1
    lines = first[0].read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert header == {
        "format": FORMAT,
        "model": "en_core_web_md@3.8.0",
        "patterns": PATTERN_VERSION,
    }
    assert json.loads(lines[1])["sha256"] == text_key("tide")


def test_large_writes_are_split_into_parts(tmp_path: Path) -> None:
    cache = ParseCache(tmp_path, "en_core_web_md@3.8.0")
    stored = {text_key(f"t{i}"): analysis(f"t{i}") for i in range(PART_SIZE + 5)}
    assert len(cache.write(stored)) == 2
    assert cache.read() == stored


def test_another_model_or_pattern_version_is_never_read(tmp_path: Path) -> None:
    ParseCache(tmp_path, "en_core_web_md@3.8.0").write({text_key("tide"): analysis("tide")})
    assert ParseCache(tmp_path, "en_core_web_md@3.9.0").read() == {}
    assert ParseCache(tmp_path, "en_core_web_md@3.8.0", patterns="np0").read() == {}
    with pytest.raises(ValueError, match="name@version"):
        ParseCache(tmp_path, "en_core_web_md")


def test_foreign_or_damaged_parts_are_skipped(tmp_path: Path, caplog) -> None:
    cache = ParseCache(tmp_path, "en_core_web_md@3.8.0")
    cache.write({text_key("tide"): analysis("tide")})
    (cache.dir / "part-foreign.jsonl").write_text('{"format": "other"}\n{"sha256": "x"}\n')
    (cache.dir / "part-damaged.jsonl").write_text(
        json.dumps({"format": FORMAT, "model": cache.model, "patterns": cache.patterns}) + "\n{oops"
    )
    with caplog.at_level("WARNING"):
        found = cache.read()
    assert found == {text_key("tide"): analysis("tide")}
    assert "part-foreign.jsonl" in caplog.text and "part-damaged.jsonl" in caplog.text


class CountingParser:
    """Stands in for the parser: records the texts it is asked to parse."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, lang, texts, *, n_jobs=1, progress=None, share=None):
        self.calls.append(list(texts))
        return [analysis(t.split()[0].lower()) for t in texts]


@pytest.fixture()
def counting(monkeypatch) -> CountingParser:
    parser = CountingParser()
    monkeypatch.setattr(extract_raw, "parse_texts", parser)
    # The models count as installed at their pinned version.
    monkeypatch.setattr(lm, "installed_version", lambda lang: lm.spec(lang).version)
    return parser


def test_a_rerun_parses_only_new_texts(tmp_path: Path, counting: CountingParser) -> None:
    texts = {"Tide gauges.", "Wave setup."}
    first = extract_raw.analyse_texts("en", texts, cache_dir=tmp_path)
    assert sorted(counting.calls[0]) == sorted(texts)
    again = extract_raw.analyse_texts("en", texts | {"Sand bars."}, cache_dir=tmp_path)
    assert counting.calls[1] == ["Sand bars."]
    assert {k: again[k] for k in first} == first
    extract_raw.analyse_texts("en", texts, cache_dir=tmp_path)
    assert len(counting.calls) == 2  # nothing new: the parser is not called


def test_a_new_model_version_parses_everything_again(
    tmp_path: Path, counting: CountingParser, monkeypatch
) -> None:
    texts = {"Tide gauges.", "Wave setup."}
    extract_raw.analyse_texts("en", texts, cache_dir=tmp_path)
    newer = lm.LanguageModel("en", "en_core_web_md", "3.8.1", "MIT", "0" * 64)
    monkeypatch.setattr(lm, "require", lambda lang: newer)
    extract_raw.analyse_texts("en", texts, cache_dir=tmp_path)
    assert sorted(counting.calls[1]) == sorted(texts)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "en_core_web_md-3.8.0",
        "en_core_web_md-3.8.1",
    ]


def test_without_a_cache_folder_nothing_is_written(tmp_path: Path, counting) -> None:
    extract_raw.analyse_texts("en", {"Tide gauges."}, cache_dir=None)
    extract_raw.analyse_texts("en", {"Tide gauges."}, cache_dir=None)
    assert len(counting.calls) == 2
    assert not any(tmp_path.iterdir())


def test_long_paragraphs_are_cut_at_line_or_sentence_ends() -> None:
    sentence = "Tide gauges record the water level. "
    text = sentence * 1000
    pieces = extract_raw.split_long(text, limit=1000)
    assert all(len(p) <= 1000 for p in pieces)
    assert all(p.endswith(".") for p in pieces)
    assert " ".join(pieces) == text.strip()
    assert extract_raw.person_pieces("a b\n\n\n\nc d\n\n") == ["a b", "c d"]


def test_a_shared_analysis_equals_the_analysis():
    table: dict = {}
    a = TextAnalysis.from_json(
        {"runs": [[["deep", "A"], ["sea", "N", ["sea"]]]], "lemmas": [["sea", "sea", 2]]}
    )
    b = TextAnalysis.from_json(
        {"runs": [[["deep", "A"], ["sea", "N", ["sea"]]]], "lemmas": [["sea", "sea", 2]]}
    )
    sa, sb = a.shared(table), b.shared(table)
    assert sa == a and sb == b and sa.to_json() == a.to_json()
    assert sa.runs[0][0] is sb.runs[0][0]  # one object per distinct unit
    assert sa.lemmas[0] is sb.lemmas[0]
