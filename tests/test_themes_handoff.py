# SPDX-License-Identifier: MIT
"""The theme handoff: the parts a person gives an assistant, and its answer read back."""

from __future__ import annotations

import json

import pytest

from cartolex.project.models import ThemesFile
from cartolex.project.themes import create_node, new_tree, rebase, set_aside
from cartolex.project.themes_handoff import (
    HANDOFF_FORMAT,
    PROBLEMS,
    answer_line,
    bundle_parts,
    parse_answer,
    part_files,
    tree_of,
)


def _tree() -> ThemesFile:
    tree = new_tree(depth=2)
    for parent, names in [
        (None, {"en": "Coastal hazards", "fr": "Aléas côtiers"}),
        (None, {"en": "Fisheries"}),
        ("n1", {"en": "Storm surge"}),
        ("n1", {"en": "Erosion"}),
        ("n2", {"en": "Stocks"}),
    ]:
        tree = create_node(tree, parent, names).tree
    places = {
        "storm surge model": "n3",
        "tide gauge": "n3",
        "coastal flooding": "n3",
        "beach erosion": "n4",
        "érosion des plages": "n4",
        "cliff retreat": "n4",
        "stock assessment": "n5",
        "hake recruitment": "n5",
        "extreme events": "n1",
        "further work": "n2",
    }
    tree = rebase(tree, list(places), places).tree
    return set_aside(tree, ["further work"], "too general").tree


USAGE = {
    "storm surge model": [9, 1.2],
    "tide gauge": [4, 0.5],
    "coastal flooding": [7, 0.9],
    "beach erosion": [6, 0.8],
    "érosion des plages": [3, 0.3],
    "cliff retreat": [2, 0.2],
    "stock assessment": [8, 1.1],
    "hake recruitment": [5, 0.6],
    "extreme events": [11, 1.5],
    "further work": [12, 1.0],
}


def _part(**kw):
    return bundle_parts(
        _tree(), USAGE, domain="Coastal systems", description="An invented field.", **kw
    )


def test_a_small_tree_is_one_part_with_its_outline_keywords_and_tray():
    [part] = _part()
    text = part["files"]["tree.txt"]
    assert part["parts"] == 1 and part["nodes"] == 5 and part["keywords"] == 9
    assert "[n1] Coastal hazards — Theme, 7 keywords" in text
    assert "  [n3] Storm surge — Topic, 3 keywords" in text
    assert "storm surge model (9); coastal flooding (7); tide gauge (4)" in text
    assert "keywords on it: extreme events (11)" in text
    assert "Set aside (1 keyword;" in text and "further work (12) — too general" in text
    assert "Context for the assistant" in text and "An invented field." in text
    prompt = part["files"]["prompt.txt"]
    assert "2 levels (Theme › Topic, from the top)" in prompt
    assert "<number> | MERGE | <node id> | <node id it goes into> | <reason>" in prompt
    assert "levels above only (1 to 1)" in prompt and "<levels, 0 to 1>" in prompt
    record = part["bundle"]
    assert record["format"] == HANDOFF_FORMAT and tree_of(record) == _tree()
    files = part_files(part)
    assert set(files) == {"prompt.txt", "tree.txt", "expected-answer.txt", "bundle.json"}
    assert json.loads(files["bundle.json"])["tree"] == record["tree"]


def test_a_one_level_tree_asks_only_for_counting_nowhere():
    tree = new_tree(depth=1)
    tree = create_node(tree, None, {"en": "Hazards"}).tree
    tree = rebase(tree, ["storm surge model"], {"storm surge model": "n1"}).tree
    [part] = bundle_parts(tree, {}, domain="d", description="")
    prompt = part["files"]["prompt.txt"]
    assert "count its usage nowhere (0)" in prompt and "<levels: 0>" in prompt
    assert "1 to 0" not in prompt


def test_a_part_never_holds_people_or_texts():
    [part] = _part()
    for text in part["files"].values():
        assert "person_id" not in text and "p0001" not in text


def test_a_large_tree_is_cut_by_top_level_nodes_under_the_budget():
    parts = _part(max_tokens=4_000, top=3)
    assert len(parts) == 1  # small enough
    big = new_tree(depth=1)
    for i in range(40):
        big = create_node(big, None, {"en": f"theme {i}"}).tree
    vocabulary = {f"keyword {i} of a long list of words": f"n{1 + i % 40}" for i in range(2400)}
    big = rebase(big, list(vocabulary), vocabulary).tree
    usage = {k: [3, 0.1] for k in vocabulary}
    parts = bundle_parts(big, usage, domain="d", description="", max_tokens=6_000, top=20)
    assert len(parts) > 1 and all(p["tokens"] <= 6_000 for p in parts)
    assert sum(p["nodes"] for p in parts) == 40
    # every part shows the whole outline; the tray only in the first
    for p in parts:
        assert p["files"]["tree.txt"].count("[n") == 40
        assert f"(part {p['part']} of {len(parts)})" in p["files"]["prompt.txt"]


def test_an_answer_becomes_operations_in_the_api_form():
    tree = _tree()
    answer = "\n".join(
        [
            "```",
            answer_line(1, "RENAME", "n3", "Storm surges and flooding", "its keywords are floods"),
            answer_line(2, "MOVE", "tide gauge", "n4", "an instrument"),
            answer_line(3, "MERGE", "n4", "n3", "one theme"),
            answer_line(4, "SPLIT", "n4", "Beaches", "beach erosion; érosion des plages", "two"),
            answer_line(5, "SET ASIDE", "extreme events", "too broad"),
            answer_line(6, "ATTRIBUTION", "cliff retreat", "0", "generic"),
            answer_line(7, "MOVE", "further work", "n2", "back"),
            "```",
        ]
    )
    parsed = parse_answer(answer, tree)
    assert parsed.unreadable == [] and parsed.ignored == 0 and parsed.lines == 7
    ops = [i.op for i in parsed.items]
    assert ops == [
        {"op": "rename_node", "node_id": "n3", "names": {"en": "Storm surges and flooding"}},
        {"op": "move_keywords", "keywords": ["tide gauge"], "node_id": "n4"},
        {"op": "merge_nodes", "source": "n4", "target": "n3"},
        {
            "op": "split_node",
            "node_id": "n4",
            "parts": [
                {"members": ["beach erosion", "érosion des plages"], "names": {"en": "Beaches"}}
            ],
        },
        {"op": "set_aside", "keywords": ["extreme events"], "reason": "AI: too broad"},
        {"op": "set_attribution", "keywords": ["cliff retreat"], "levels": 0},
        {"op": "put_back", "keywords": ["further work"], "node_id": "n2"},
    ]
    assert [i.reason for i in parsed.items][:2] == ["its keywords are floods", "an instrument"]
    assert all(i.refused == "" for i in parsed.items)


def test_an_answer_is_read_tolerantly():
    tree = _tree()
    answer = """Here are my proposals:

| # | Action | Target | Value | Reason |
|---|--------|--------|-------|--------|
| 1 | rename | [n3] | "Storm surges" | clearer |
| 2 | Set_Aside | Extreme Events | too broad |
- 3 | move | EROSION DES PLAGES (3) | Erosion | already there
4.\tmerge\tnode n5\tinto Fisheries\tsame theme
5 | ATTRIBUTE | tide gauge | none | an instrument
I hope this helps.
"""
    parsed = parse_answer(answer, tree)
    ops = {i.number: i.op for i in parsed.items}
    assert ops[1] == {"op": "rename_node", "node_id": "n3", "names": {"en": "Storm surges"}}
    assert ops[2]["op"] == "set_aside" and ops[2]["keywords"] == ["extreme events"]
    assert ops[3] == {"op": "move_keywords", "keywords": ["érosion des plages"], "node_id": "n4"}
    assert ops[4] == {"op": "merge_nodes", "source": "n5", "target": "n2"}
    assert ops[5] == {"op": "set_attribution", "keywords": ["tide gauge"], "levels": 0}
    assert (
        parse_answer("1 | ATTRIBUTION | tide gauge | levels: 0 | x", tree).items[0].op["levels"]
        == 0
    )
    # the header row, the separator and the sentences are ignored
    assert parsed.unreadable == [] and parsed.ignored == 4
    # a merge across levels is kept, with the reason it cannot be applied
    assert parsed.items[3].refused and "not on the same level" in parsed.items[3].refused


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("1 | RENAME | n9 | Something | no such node", "unknown_node"),
        ("2 | MOVE | sea serpents | n3 | no such keyword", "unknown_keyword"),
        ("3 | ATTRIBUTION | tide gauge | 7 | too many levels", "bad_levels"),
        ("4 | RENAME | n3 |  | empty", "empty_name"),
        ("5 | MERGE | n3 | n3 | itself", "same_node"),
        ("6 | SPLIT | n3 | Only a name", "missing_fields"),
        ("7 | PROMOTE | n3 | up | unknown", "unknown_action"),
        ('8 | RENAME | n3 | "" | empty name', "empty_name"),
    ],
)
def test_an_unreadable_line_says_why(line, problem):
    parsed = parse_answer(line, _tree())
    assert parsed.items == [] and [u.problem for u in parsed.unreadable] == [problem]
    assert parsed.unreadable[0].line == 1 and problem in PROBLEMS


def test_a_bundle_of_another_kind_is_refused():
    with pytest.raises(ValueError, match="not a theme handoff part"):
        tree_of({"format": "cartolex-handoff/1", "items": []})
