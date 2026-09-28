# SPDX-License-Identifier: MIT
"""Scoring candidates: counting units, votes, part weights and bands, on hand-made analyses.

No language model is needed: each text is a :class:`TextAnalysis` written by
hand (runs of word units with their classes).
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer

from cartolex.lexicon.config import KeywordsConfig, SettingsError
from cartolex.lexicon.noun_phrases import TextAnalysis, lemma_table, occurrences
from cartolex.lexicon.scoring import (
    RAW_COLUMNS,
    BandRules,
    ScoringOptions,
    TextUnit,
    score_units,
)


def text(*phrases: str) -> TextAnalysis:
    """An analysis of noun phrases, each ``word/CLASS word/CLASS …``, one run each."""
    runs = []
    for phrase in phrases:
        runs.append(tuple((w.split("/")[0], w.split("/")[1]) for w in phrase.split()))
    return TextAnalysis(runs=tuple(runs), lemmas=())


def unit(person: int, text_id: str, *parts, org: str = "G1") -> TextUnit:
    return TextUnit(person, org, text_id, tuple((name, (a,)) for name, a in parts))


ST = "sediment/N transport/N"
TG = "tide/N gauge/N"


def corpus() -> list[TextUnit]:
    """Six people; person 0 and 1 share a text; people 4 and 5 are in another group."""
    return [
        unit(0, "t1", ("full", text(ST, ST, TG, "wave/N"))),
        unit(1, "t1", ("full", text(ST, ST, TG, "wave/N"))),
        unit(1, "t2", ("full", text(ST, "beach/N"))),
        unit(2, "t3", ("full", text(TG, TG, TG, "beach/N"))),
        unit(3, "t4", ("full", text(ST, "wave/N", "beach/N"))),
        unit(4, "t5", ("full", text(TG, "wave/N")), org="G2"),
        unit(5, "t6", ("full", text("beach/N", "dune/N")), org="G2"),
    ]


def scores(result) -> dict[str, float]:
    return dict(zip(result.table["term"], result.table["score"], strict=True))


def test_the_default_is_the_historical_person_tfidf() -> None:
    units = corpus()
    result = score_units("en", units, 6, min_df=2, max_df=1.0)
    person_analyses: list[list[TextAnalysis]] = [[] for _ in range(6)]
    for u in units:
        person_analyses[u.person] += [a for _, part in u.parts for a in part]
    lemmas = lemma_table(a for p in person_analyses for a in p)
    docs = []
    for p in person_analyses:
        c = Counter(k for a in p for k, _ in occurrences([a], "en", lemmas))
        docs.append(list(c.elements()))
    v = TfidfVectorizer(analyzer=lambda d: d, min_df=2, max_df=1.0)
    features = v.fit(docs).get_feature_names_out()
    expected = dict(zip(features, v.transform(docs).sum(axis=0).A1, strict=True))
    got = scores(result)
    assert set(got) == set(expected)
    assert all(got[k] == expected[k] for k in got)  # bit for bit
    assert list(result.table.columns) == RAW_COLUMNS
    row = result.table.set_index("term").loc["sediment transport"]
    assert (row["people"], row["texts"], row["len"]) == (3, 3, 2)
    assert row["score_len"] == pytest.approx(row["score"] * 3)


def test_counting_units_change_what_a_document_is() -> None:
    units = corpus()
    by_text = score_units(
        "en", units, 6, min_df=2, max_df=1.0, options=ScoringOptions(counting_unit="text")
    )
    by_org = score_units(
        "en", units, 6, min_df=2, max_df=1.0, options=ScoringOptions(counting_unit="organisation")
    )
    by_person = score_units("en", units, 6, min_df=2, max_df=1.0)
    # A text two people wrote counts once; each organisation is one document.
    assert (by_person.n_documents, by_text.n_documents, by_org.n_documents) == (6, 6, 2)
    # The window still counts people: the same candidates are kept.
    assert set(scores(by_text)) == set(scores(by_person)) == set(scores(by_org))
    # With one document per organisation, the sum of L2-normalised rows is at most 2.
    assert sum(np.square(list(scores(by_org).values()))) > 0
    assert max(scores(by_org).values()) <= 2.0 + 1e-12


def test_votes() -> None:
    units = [
        unit(0, "a", ("full", text(TG, TG, TG, TG, "beach/N"))),
        unit(1, "b", ("full", text(TG, "beach/N", "beach/N", "beach/N", "beach/N"))),
        unit(2, "c", ("full", text(TG, "beach/N"))),
    ]
    raw = score_units("en", units, 3, min_df=1, max_df=1.0)
    presence = score_units(
        "en", units, 3, min_df=1, max_df=1.0, options=ScoringOptions(vote="presence")
    )
    sub = score_units(
        "en", units, 3, min_df=1, max_df=1.0, options=ScoringOptions(vote="sublinear")
    )
    # Presence: every text votes 1 for both, so the two words weigh the same.
    p = scores(presence)
    assert p["beach"] == pytest.approx(p["tide gauge"])
    # Frequency and its logarithm keep the order of the counts, the logarithm less steeply.
    r, s = scores(raw), scores(sub)
    assert r["tide gauge"] > 0 and s["tide gauge"] > 0
    assert abs(s["beach"] - s["tide gauge"]) <= abs(r["beach"] - r["tide gauge"])


def test_part_weights() -> None:
    def units():
        return [
            TextUnit(i, "G1", f"t{i}", (("title", (text(ST),)), ("body", (text(TG, TG, TG),))))
            for i in range(3)
        ]

    equal = scores(score_units("en", units(), 3, min_df=1, max_df=1.0))
    light = scores(
        score_units(
            "en",
            units(),
            3,
            min_df=1,
            max_df=1.0,
            options=ScoringOptions(part_weights={"body": 0.1}),
        )
    )
    assert equal["tide gauge"] > equal["sediment transport"]
    assert light["tide gauge"] < light["sediment transport"]


def test_the_of_complement_is_a_switch() -> None:
    phrase = "degrees/N of/P freedom/N"
    units = [unit(i, f"t{i}", ("full", text(phrase))) for i in range(3)]
    off = scores(score_units("en", units, 3, min_df=1, max_df=1.0))
    on = scores(
        score_units(
            "en", units, 3, min_df=1, max_df=1.0, options=ScoringOptions(of_complement=True)
        )
    )
    assert "degrees of freedom" in on and "degrees of freedom" not in off
    assert {"degrees", "freedom"} <= set(off)


def test_bands_and_reasons() -> None:
    units = []
    for i in range(10):
        phrases = ["support/N vector/N machine/N", "recent/A approach/N", "recent/A model/N"]
        if i < 5:
            phrases.append("data/N")
        if i < 3:
            phrases += ["coral/N reef/N"] * 3
        units.append(unit(i, f"t{i}", ("full", text(*phrases))))
    result = score_units("en", units, 10, min_df=3, max_df=1.0)
    bands = result.table.set_index("term")[["band", "reason"]]
    # By default nothing is set aside for its score.
    assert "low-score" not in set(bands["reason"])
    assert tuple(bands.loc["support vector machine"]) == ("kept", "multiword")
    assert tuple(bands.loc["coral reef"]) == ("kept", "multiword")
    # Always inside the longer phrase: a fragment of it.
    assert tuple(bands.loc["vector machine"]) == ("aside", "part-of: support vector machine")
    assert tuple(bands.loc["data"]) == ("check", "single-word")
    # By default a widespread edge adjective changes nothing: the phrase is kept.
    assert tuple(bands.loc["recent approach"]) == ("kept", "multiword")
    # With the lab's switch, an edge adjective every person uses makes it common.
    common = score_units(
        "en",
        units,
        10,
        min_df=3,
        max_df=1.0,
        options=ScoringOptions(bands=BandRules(generic_spread=0.2)),
    )
    common_bands = common.table.set_index("term")[["band", "reason"]]
    assert tuple(common_bands.loc["recent approach"]) == ("check", "common-modifier: recent")
    # The least specific tail is set aside.
    strict = score_units(
        "en",
        units,
        10,
        min_df=3,
        max_df=1.0,
        options=ScoringOptions(bands=BandRules(drop_share=0.5, fragment_share=None)),
    )
    reasons = set(strict.table["reason"])
    assert "low-score" in reasons and not any(r.startswith("part-of") for r in reasons)


def test_names_are_set_aside_when_known() -> None:
    a = text("Port/R Aurel/R", "tide/N gauge/N")
    units = [TextUnit(i, "G1", f"t{i}", (("full", (a,)),)) for i in range(3)]
    names = {id(a): {"port aurel": "place"}}
    result = score_units("en", units, 3, min_df=1, max_df=1.0, names=names)
    bands = result.table.set_index("term")
    assert tuple(bands.loc["Port Aurel", ["band", "reason"]]) == ("aside", "name: place")
    assert bands.loc["tide gauge", "band"] == "kept"


def test_options_and_settings_are_checked() -> None:
    with pytest.raises(ValueError, match="counting unit"):
        ScoringOptions(counting_unit="word")
    with pytest.raises(ValueError, match="vote"):
        ScoringOptions(vote="max")
    assert KeywordsConfig(counting_unit="organisation").counting_unit == "organisation"
    with pytest.raises(SettingsError, match="counting_unit"):
        KeywordsConfig(counting_unit="word")


def test_no_candidate_in_the_window_gives_an_empty_table() -> None:
    units = [unit(0, "t0", ("full", text(ST)))]
    result = score_units("en", units, 1, min_df=3, max_df=1.0)
    assert result.empty and list(result.table.columns) == RAW_COLUMNS
