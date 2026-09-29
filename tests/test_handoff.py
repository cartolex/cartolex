# SPDX-License-Identifier: MIT
"""Answers to a keyword handoff an earlier version imported: still read back."""

from __future__ import annotations

import pytest

from cartolex.project import handoff


def _items(n: int) -> list[handoff.BundleItem]:
    base = [
        handoff.BundleItem(
            "tide gauge", "en", "kept", "multiword", 3, 1, 0.9, [], ["longer phrase"]
        ),
        handoff.BundleItem("trait de côte", "fr", "kept", "multiword", 5, 7, 0.8, [], []),
        handoff.BundleItem("data", "en", "check", "single-word", 9, 12, 0.1, [], []),
        handoff.BundleItem("sea level", "en", "kept", "multiword", 4, 4, 0.7, [], []),
        handoff.BundleItem("recent decades", "en", "check", "multiword", 6, 6, 0.4, [], []),
        handoff.BundleItem("wave", "en", "check", "single-word", 8, 9, 0.2, ["waves"], []),
    ]
    return [
        base[i]
        if i < len(base)
        else handoff.BundleItem(f"term {i}", "en", "check", "multiword", 3, 3, 0.5, [], [])
        for i in range(n)
    ]


def test_answers_are_read_in_every_form() -> None:
    items = _items(6)
    text = "\n".join(
        [
            "Here are my answers:",
            "```",
            "1 | C | tide gauge",
            "2 | O | Trait de cote | shoreline",  # case and accents may differ
            "| 3 | G | data |",  # a Markdown table row
            "99 | C | sea level | sea level",  # a wrong number, the term decides
            "5\tK\trecent decades",  # tab-separated
            "5 | G | recent decades",  # a second answer: the first counts
            "7 | C | nothing like it",  # fits no item
            "```",
            "M en wave=ocean wave",  # the triage's line format
        ]
    )
    parsed = handoff.parse_answer(text, items)
    v = parsed.verdicts
    assert v[0] == handoff.Verdict("C", "tide gauge")  # the English form defaults to the term
    assert v[1] == handoff.Verdict("O", "shoreline")
    assert v[2].code == "G" and not v[2].accept
    assert v[3] == handoff.Verdict("C", "sea level")
    assert v[4].code == "K"
    assert v[5] == handoff.Verdict("M", "ocean wave")
    assert (parsed.renumbered, parsed.duplicates, parsed.unmatched, parsed.ignored) == (1, 1, 1, 1)
    assert parsed.missing(len(items)) == 0
    odd = handoff.parse_answer("2 | C | côte | coast", items)
    assert odd.verdicts == {1: handoff.Verdict("C", "coast")} and odd.term_mismatch == 1


def test_a_record_of_another_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        handoff.items_of({"format": "other", "items": []})
