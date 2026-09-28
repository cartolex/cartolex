# SPDX-License-Identifier: MIT
"""The theme handoff's lab check: the scores of proposed operations against known themes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from cartolex.project.themes import create_node, new_tree, rebase
from cartolex.project.themes_handoff import parse_answer

ROOT = Path(__file__).resolve().parent.parent


def _lab():
    name = "themes_handoff_lab"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _tree():
    tree = new_tree(depth=1)
    for name in ("Floods", "Fish", "Mixed"):
        tree = create_node(tree, None, {"en": name}).tree
    places = {
        "storm surge": "n1", "coastal flooding": "n1", "hake stock": "n2", "cod landings": "n2",
        "sea wall": "n3", "trawl bycatch": "n3", "need": "n3",
    }  # fmt: skip
    return rebase(tree, list(places), places).tree


THEME = {
    "storm surge": "hazards", "coastal flooding": "hazards", "sea wall": "hazards",
    "hake stock": "fisheries", "cod landings": "fisheries", "trawl bycatch": "fisheries",
}  # fmt: skip
GOLD = {
    "theme": THEME,
    "share": {**dict.fromkeys(THEME, 1.0), "need": 0.2},
    "names": {"hazards": {"coastal", "hazards", "floods"}, "fisheries": {"fisheries", "fish"}},
}


def test_bcubed_rewards_grouping_each_theme_in_one_node():
    lab = _lab()
    tree = _tree()
    assert 0 < lab.bcubed(tree, THEME) < 1
    perfect = parse_answer(
        "1 | MOVE | sea wall | n1 | a defence\n2 | MOVE | trawl bycatch | n2 | a fishery", tree
    )
    for item in perfect.items:
        tree = lab._apply(tree, item.op)
    assert lab.bcubed(tree, THEME) == 1.0


def test_each_operation_is_judged_alone():
    lab = _lab()
    tree = _tree()
    answer = "\n".join(
        [
            "1 | MOVE | sea wall | n1 | a flood defence",
            "2 | MOVE | storm surge | n2 | wrong",
            "3 | RENAME | n3 | Coastal hazards | clearer",
            "4 | SET ASIDE | need | too generic",
            "5 | SET ASIDE | hake stock | wrong",
            "6 | MERGE | n2 | n1 | not one theme",
            "7 | MOVE | cod landings | n9 | no such node",
        ]
    )
    parsed = parse_answer(answer, tree)
    verdicts = [lab.judge(item, tree, GOLD) for item in parsed.items]
    assert verdicts == ["improves", "worsens", "improves", "improves", "worsens", "worsens"]
    assert [u.problem for u in parsed.unreadable] == ["unknown_node"]
