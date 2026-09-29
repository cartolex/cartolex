# SPDX-License-Identifier: MIT
"""The handoff format of the AI clean-up: parts, their files, and answers read back."""

from __future__ import annotations

import io
import json
import zipfile

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


def test_answer_lines_round_trip() -> None:
    items = _items(3)
    lines = [
        handoff.answer_line(1, items[0], "C", "tide gauge"),
        handoff.answer_line(2, items[1], "O", "shoreline"),
        handoff.answer_line(3, items[2], "G"),
    ]
    assert lines == ["1 | C | tide gauge", "2 | O | trait de côte | shoreline", "3 | G | data"]
    parsed = handoff.parse_answer("\n".join(lines), items)
    assert [parsed.verdicts[i].code for i in range(3)] == ["C", "O", "G"]


def test_parts_fit_and_carry_everything() -> None:
    items = _items(200)
    kw = {"domain": "Coastal systems", "description": "A test.", "n_people": 50, "n_texts": 80}
    parts = handoff.split_items(items, max_tokens=2_000, **kw)
    assert len(parts) > 1 and [it for p in parts for it in p] == items
    files = {
        f"part-{k}": handoff.part_files(chunk, part=k, parts=len(parts), **kw)
        for k, chunk in enumerate(parts, 1)
    }
    sizes = [handoff.cautious_tokens(f["prompt.txt"] + f["terms.txt"]) for f in files.values()]
    assert max(sizes) <= 2_000
    first = files["part-1"]
    assert "Coastal systems" in first["prompt.txt"]
    assert f"numbered 1 to {len(parts[0])}" in first["prompt.txt"]
    assert "6 | C | repliement des protéines | protein folding" in first["prompt.txt"]
    assert f"(part 1 of {len(parts)})" in first["terms.txt"]
    assert "1. tide gauge [en] — 3 people, 1 text — in: longer phrase" in first["terms.txt"]
    assert "single-word" not in first["terms.txt"]  # no band in the judge's list
    with zipfile.ZipFile(io.BytesIO(handoff.part_zip(files))) as zf:
        assert "part-1/prompt.txt" in zf.namelist() and len(zf.namelist()) == 3 * len(parts)
    record = handoff.part_record(
        parts[0], name="part-1", domain="Coastal systems", description="", parts=len(parts)
    )
    assert json.loads(json.dumps(record))["items"][0]["number"] == 1
    assert handoff.items_of(record) == parts[0]
    with pytest.raises(ValueError, match="handoff part"):
        handoff.items_of({"format": "other", "items": []})


def test_parts_keep_the_terms_the_same_people_use_together() -> None:
    import numpy as np

    # Two communities of 20 people; each term, and its French twin, is used by one of them.
    # The items come one language after the other, as the best scored first may.
    items, users = [], {}
    for lang, word in (("en", "subject"), ("fr", "sujet")):
        for k in range(60):
            term = f"{word} {k}"
            items.append(handoff.BundleItem(term, lang, "kept", "multiword", 5, 5, 0.5, [], []))
            users[(term, lang)] = (np.arange(8) + k) % 20 + 20 * (k % 2)
    common = {"domain": "d", "description": "", "n_people": 40, "n_texts": 80}
    plain = handoff.split_items(items, max_tokens=2_000, **common)
    grouped = handoff.group_items(items, users, max_tokens=2_000, **common)
    assert len(grouped) == len(plain) > 1
    assert sorted(it.term for p in grouped for it in p) == sorted(it.term for it in items)

    def together(parts) -> int:
        where = {(it.term, it.lang): i for i, p in enumerate(parts) for it in p}
        return sum(where[(f"subject {k}", "en")] == where[(f"sujet {k}", "fr")] for k in range(60))

    assert together(grouped) > together(plain)
    # without users, the plain parts
    assert handoff.group_items(items, {}, max_tokens=2_000, **common) == plain
