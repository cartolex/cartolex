# SPDX-License-Identifier: MIT
"""The theme tree: operations, depth changes, rebase and comparison (cartolex.project.themes).

Unit tests pin each rule; property tests run the operations and the rebase on
random trees and vocabularies (see ``themes_random.py``). All data is invented.
"""

from __future__ import annotations

import random

import pytest
from themes_random import (
    leaves_of,
    levels_of,
    next_vocabulary,
    normalized_orders,
    random_edit,
    random_proposals,
    random_tree,
)

from cartolex.project.models import ThemesFile
from cartolex.project.themes import (
    LEVEL_REMOVED,
    NEW_WITHOUT_PLACE,
    SET_ASIDE,
    Change,
    ThemeEditError,
    canonical,
    children,
    compare,
    create_node,
    default_level_names,
    delete_node,
    insert_level,
    keywords_at,
    keywords_under,
    merge_nodes,
    move_keywords,
    move_node,
    new_tree,
    node_level,
    put_back,
    rebase,
    remove_level,
    rename_level,
    rename_node,
    set_aside,
    set_attribution,
    set_review,
    split_node,
    vocabulary_fingerprint,
    vocabulary_gaps,
    vocabulary_of,
)


def _tree() -> ThemesFile:
    """Two themes: hazards (surge, erosion) and ecology (reefs)."""
    return ThemesFile.model_validate(
        {
            "depth": 2,
            "levels": [{"names": n} for n in default_level_names(2)],
            "nodes": [
                {"id": "n1", "parent": None, "names": {"en": "Hazards"}, "order": 1},
                {"id": "n2", "parent": "n1", "names": {"en": "Storm surge"}, "order": 1},
                {"id": "n3", "parent": "n1", "names": {"en": "Erosion"}, "order": 2},
                {"id": "n4", "parent": None, "names": {"en": "Ecology"}, "order": 2},
                {"id": "n5", "parent": "n4", "names": {"en": "Reefs"}, "order": 1},
            ],
            "keywords": {
                "storm surge model": "n2",
                "coastal flooding": "n2",
                "beach erosion": "n3",
                "dune retreat": "n3",
                "coral bleaching": "n5",
            },
            "set_aside": {"numerical results": {"from": "n2", "reason": "too general"}},
            "review": {"dune retreat": "to_check"},
        }
    )


# ── defaults and reading ─────────────────────────────────────────────────────


def test_default_level_names_by_depth():
    en = [[lv["en"] for lv in default_level_names(d)] for d in range(1, 5)]
    assert en == [
        ["Theme"],
        ["Theme", "Topic"],
        ["Field", "Theme", "Topic"],
        ["Domain", "Field", "Theme", "Topic"],
    ]
    assert [lv["fr"] for lv in default_level_names(4)] == ["Domaine", "Champ", "Thème", "Sujet"]
    assert [lv["pt"] for lv in default_level_names(4)] == ["Domínio", "Área", "Tema", "Tópico"]
    default_level_names(2)[0]["en"] = "changed"  # a copy: the defaults stay
    assert default_level_names(2)[0]["en"] == "Theme"
    tree = new_tree(3)
    assert tree.depth == 3 and [lv.names["fr"] for lv in tree.levels] == ["Champ", "Thème", "Sujet"]
    with pytest.raises(ThemeEditError):
        new_tree(5)


def test_reading_helpers():
    tree = _tree()
    assert node_level(tree, "n1") == 1 and node_level(tree, "n5") == 2
    assert [n.id for n in children(tree, None)] == ["n1", "n4"]
    assert [n.id for n in children(tree, "n1")] == ["n2", "n3"]
    assert keywords_under(tree, "n1") == [
        "beach erosion",
        "coastal flooding",
        "dune retreat",
        "storm surge model",
    ]
    assert "numerical results" in vocabulary_of(tree)
    assert vocabulary_gaps(tree, ["coral bleaching", "tidal inlet"]) == (
        ["tidal inlet"],
        [
            "beach erosion",
            "coastal flooding",
            "dune retreat",
            "numerical results",
            "storm surge model",
        ],
    )
    assert vocabulary_fingerprint(["b", "a", "a"]) == vocabulary_fingerprint(["a", "b"])
    assert vocabulary_fingerprint(["a"]) != vocabulary_fingerprint(["a", "b"])
    with pytest.raises(ThemeEditError, match="no node"):
        node_level(tree, "zz")


def test_canonical_form_orders_nodes_and_keywords():
    doc = _tree().model_dump(mode="json", by_alias=True)
    doc["nodes"].reverse()
    doc["keywords"] = dict(reversed(list(doc["keywords"].items())))
    shuffled = ThemesFile.model_validate(doc)
    tree = canonical(shuffled)
    assert [n.id for n in tree.nodes] == ["n1", "n2", "n3", "n4", "n5"]
    assert list(tree.keywords) == sorted(tree.keywords)
    assert tree == canonical(tree) == _tree()


def test_the_model_refuses_review_states_of_keywords_it_does_not_hold():
    doc = _tree().model_dump(mode="json", by_alias=True)
    doc["review"]["tidal inlet"] = "to_check"
    with pytest.raises(ValueError, match="review names"):
        ThemesFile.model_validate(doc)


# ── operations ───────────────────────────────────────────────────────────────


def test_rename_node_per_language():
    edit = rename_node(_tree(), "n2", {"fr": "Surcote", "pt": "Maré de tempestade"})
    node = next(n for n in edit.tree.nodes if n.id == "n2")
    assert node.names == {"en": "Storm surge", "fr": "Surcote", "pt": "Maré de tempestade"}
    assert edit.description == "rename n2"
    node = next(
        n for n in rename_node(edit.tree, "n2", {"en": None, "fr": " "}).tree.nodes if n.id == "n2"
    )
    assert node.names == {"pt": "Maré de tempestade"}
    with pytest.raises(ThemeEditError, match="unknown language"):
        rename_node(_tree(), "n2", {"nl": "Stormvloed"})
    with pytest.raises(ThemeEditError, match="no node"):
        rename_node(_tree(), "n9", {"en": "x"})


def test_rename_level_keeps_one_name():
    tree = rename_level(_tree(), 1, {"en": "Axis", "fr": "Axe"}).tree
    assert tree.levels[0].names == {"en": "Axis", "fr": "Axe", "pt": "Tema"}
    with pytest.raises(ThemeEditError, match="at least one"):
        rename_level(tree, 2, {"en": None, "fr": None, "pt": None})
    with pytest.raises(ThemeEditError, match="levels 1 to 2"):
        rename_level(tree, 3, {"en": "x"})


def test_move_keywords():
    edit = move_keywords(_tree(), ["beach erosion", "coastal flooding"], "n5")
    assert edit.tree.keywords["beach erosion"] == "n5" == edit.tree.keywords["coastal flooding"]
    assert edit.description == "move 2 keywords to n5"
    assert [c.kind for c in compare(_tree(), edit.tree)] == ["moved", "moved"]
    broader = move_keywords(_tree(), "coastal flooding", "n1").tree  # onto a theme
    assert broader.keywords["coastal flooding"] == "n1"
    assert keywords_under(broader, "n2") == ["storm surge model"]
    with pytest.raises(ThemeEditError, match="no node"):
        move_keywords(_tree(), "beach erosion", "n9")
    with pytest.raises(ThemeEditError, match="put back instead"):
        move_keywords(_tree(), "numerical results", "n5")
    with pytest.raises(ThemeEditError, match="not in the tree"):
        move_keywords(_tree(), "tidal inlet", "n5")
    with pytest.raises(ThemeEditError, match="no keyword"):
        move_keywords(_tree(), [], "n5")


def test_move_node_keeps_its_level_and_reorders():
    edit = move_node(_tree(), "n3", "n4")
    assert [n.id for n in children(edit.tree, "n4")] == ["n5", "n3"]
    assert edit.description == "move n3 under n4"
    assert edit.tree.keywords["beach erosion"] == "n3"  # keywords travel with their node
    edit = move_node(_tree(), "n4", None, position=0)
    assert [n.id for n in children(edit.tree, None)] == ["n4", "n1"]
    assert [n.order for n in children(edit.tree, None)] == [1, 2]
    assert edit.description == "reorder n4"
    with pytest.raises(ThemeEditError, match="level 1"):
        move_node(_tree(), "n1", "n4")
    with pytest.raises(ThemeEditError, match="only level 1"):
        move_node(_tree(), "n2", None)
    with pytest.raises(ThemeEditError, match="already there"):
        move_node(_tree(), "n2", "n1")


def test_merge_nodes():
    edit = merge_nodes(_tree(), "n2", "n3")
    tree = edit.tree
    assert "n2" not in {n.id for n in tree.nodes}
    assert keywords_under(tree, "n3") == [
        "beach erosion",
        "coastal flooding",
        "dune retreat",
        "storm surge model",
    ]
    assert tree.set_aside["numerical results"].source == "n3"  # the origin follows
    assert edit.description == "merge n2 into n3"
    tree = merge_nodes(_tree(), "n4", "n1").tree
    assert [n.id for n in children(tree, "n1")] == ["n2", "n3", "n5"]
    with pytest.raises(ThemeEditError, match="same level"):
        merge_nodes(_tree(), "n2", "n1")
    with pytest.raises(ThemeEditError, match="itself"):
        merge_nodes(_tree(), "n2", "n2")


def test_split_node_by_a_partition():
    edit = split_node(
        _tree(),
        "n2",
        [(["coastal flooding"], {"en": "Flooding"})],
    )
    tree = edit.tree
    new = [n for n in tree.nodes if n.id not in {"n1", "n2", "n3", "n4", "n5"}]
    assert len(new) == 1 and new[0].id == "n6" and new[0].parent == "n1"
    assert [n.id for n in children(tree, "n1")] == ["n2", "n6", "n3"]  # right after the split node
    assert tree.keywords["coastal flooding"] == "n6"
    assert tree.keywords["storm surge model"] == "n2"
    assert edit.description == "split n2 into 2"
    tree = split_node(_tree(), "n1", [(["n3"], {"en": "Coasts"})], ids=["coasts"]).tree
    assert [n.id for n in children(tree, None)] == ["n1", "coasts", "n4"]
    assert [n.id for n in children(tree, "coasts")] == ["n3"]
    with pytest.raises(ThemeEditError, match="leaves at least one"):
        split_node(_tree(), "n2", [(["coastal flooding", "storm surge model"], {"en": "All"})])
    with pytest.raises(ThemeEditError, match="not held"):
        split_node(_tree(), "n2", [(["beach erosion"], {"en": "x"})])
    with pytest.raises(ThemeEditError, match="two parts"):
        split_node(
            _tree(),
            "n1",
            [(["n2"], {"en": "a"}), (["n2"], {"en": "b"})],
        )
    with pytest.raises(ThemeEditError, match="needs a name"):
        split_node(_tree(), "n2", [(["coastal flooding"], {})])


def test_split_a_node_holding_keywords_and_nodes():
    tree = move_keywords(_tree(), ["coral bleaching"], "n4").tree  # Ecology holds it and Reefs
    tree = create_node(tree, "n4", {"en": "Plankton"}, node_id="n7").tree
    tree = move_keywords(tree, "dune retreat", "n7").tree
    edit = split_node(tree, "n4", [(["coral bleaching", "n7"], {"en": "Open sea"})])
    new = next(n.id for n in edit.tree.nodes if n.names == {"en": "Open sea"})
    assert keywords_at(edit.tree, new) == ["coral bleaching"]
    assert [n.id for n in children(edit.tree, new)] == ["n7"]
    assert [n.id for n in children(edit.tree, "n4")] == ["n5"]
    clash = create_node(tree, "n4", {"en": "x"}, node_id="reef").tree
    clash = rebase(clash, [*vocabulary_of(clash), "reef"], {"reef": "n4"}).tree
    with pytest.raises(ThemeEditError, match="ambiguous|both a keyword"):
        split_node(clash, "n4", [(["reef"], {"en": "y"})])


def test_create_and_delete_nodes():
    edit = create_node(_tree(), "n4", {"en": "Plankton"}, position=0)
    assert edit.description == "create n6"
    assert [n.id for n in children(edit.tree, "n4")] == ["n6", "n5"]
    tree = delete_node(edit.tree, "n6").tree
    assert [n.id for n in children(tree, "n4")] == ["n5"]
    assert tree.model_copy(update={"nodes": tree.nodes}) == rename_node(tree, "n5", {}).tree
    tree = create_node(_tree(), None, {"fr": "Sociétés"}, node_id="society").tree
    assert [n.id for n in children(tree, None)] == ["n1", "n4", "society"]
    with pytest.raises(ThemeEditError, match="deepest level"):
        create_node(_tree(), "n2", {"en": "x"})  # no node below the deepest level
    with pytest.raises(ThemeEditError, match="already used"):
        create_node(_tree(), None, {"en": "x"}, node_id="n1")
    with pytest.raises(ThemeEditError, match="not a valid node id"):
        create_node(_tree(), None, {"en": "x"}, node_id="a b")
    with pytest.raises(ThemeEditError, match="not empty"):
        delete_node(_tree(), "n5")


def test_new_ids_never_reuse_a_set_aside_origin():
    tree = set_aside(_tree(), "coral bleaching").tree
    tree = merge_nodes(tree, "n5", "n2").tree  # the origin follows the merge: n2
    doc = tree.model_dump(mode="json", by_alias=True)
    doc["set_aside"]["coral bleaching"]["from"] = "n9"  # an origin that no longer exists
    tree = ThemesFile.model_validate(doc)
    assert create_node(tree, None, {"en": "x"}).description == "create n10"


def test_set_aside_and_put_back():
    edit = set_aside(_tree(), ["coral bleaching", "dune retreat"], "a method, not a theme")
    tree = edit.tree
    assert tree.set_aside["coral bleaching"].source == "n5"
    assert tree.set_aside["coral bleaching"].reason == "a method, not a theme"
    assert tree.review["dune retreat"] == "to_check"  # the review state stays
    assert edit.description == "set aside 2 keywords"
    again = set_aside(tree, "coral bleaching", "duplicate").tree  # only the reason changes
    assert again.set_aside["coral bleaching"].source == "n5"
    assert again.set_aside["coral bleaching"].reason == "duplicate"
    back = put_back(tree, ["coral bleaching", "dune retreat"])
    assert back.tree == _tree() and back.description == "put back 2 keywords"
    assert put_back(tree, "coral bleaching", "n2").tree.keywords["coral bleaching"] == "n2"
    assert put_back(tree, "coral bleaching", "n4").tree.keywords["coral bleaching"] == "n4"
    with pytest.raises(ThemeEditError, match="not set aside"):
        put_back(_tree(), "coral bleaching")
    orphan = ThemesFile.model_validate(
        {**_tree().model_dump(by_alias=True), "set_aside": {"numerical results": {"from": "n9"}}}
    )
    with pytest.raises(ThemeEditError, match="name a target"):
        put_back(orphan, "numerical results")
    assert put_back(orphan, "numerical results", "n3").tree.keywords["numerical results"] == "n3"


def test_review_states():
    edit = set_review(_tree(), ["coral bleaching", "numerical results"], "reviewed")
    assert edit.tree.review == {
        "coral bleaching": "reviewed",
        "dune retreat": "to_check",
        "numerical results": "reviewed",
    }
    assert edit.description == "mark 2 keywords reviewed"
    cleared = set_review(edit.tree, "dune retreat", None)
    assert "dune retreat" not in cleared.tree.review
    assert cleared.description == "clear the review of 1 keyword"
    with pytest.raises(ThemeEditError, match="unknown review state"):
        set_review(_tree(), "dune retreat", "done")
    with pytest.raises(ThemeEditError, match="not in the tree"):
        set_review(_tree(), "tidal inlet", "reviewed")


def test_operations_never_change_their_argument():
    tree = _tree()
    before = tree.model_dump_json(by_alias=True)
    merge_nodes(tree, "n2", "n3")
    set_aside(tree, "coral bleaching")
    insert_level(tree, 3)
    remove_level(tree, 1)
    rebase(tree, ["coral bleaching"], {})
    assert tree.model_dump_json(by_alias=True) == before


# ── attribution ──────────────────────────────────────────────────────────────


def _attributed() -> ThemesFile:
    """_tree() with coastal flooding counting toward level 1 only and storm surge model nowhere."""
    tree = set_attribution(_tree(), "coastal flooding", 1).tree
    return set_attribution(tree, "storm surge model", 0).tree


def test_attribution_is_checked_by_the_model():
    doc = _tree().model_dump(mode="json", by_alias=True)
    with pytest.raises(ValueError, match="not placed"):
        ThemesFile.model_validate({**doc, "attribution": {"numerical results": 0}})
    with pytest.raises(ValueError, match="fewer levels than the keyword's node"):
        ThemesFile.model_validate({**doc, "attribution": {"coastal flooding": 2}})
    on_theme = {**doc["keywords"], "coastal flooding": "n1"}
    with pytest.raises(ValueError, match="fewer levels than the keyword's node"):
        ThemesFile.model_validate(
            {**doc, "keywords": on_theme, "attribution": {"coastal flooding": 1}}
        )
    ok = ThemesFile.model_validate(
        {**doc, "keywords": on_theme, "attribution": {"coastal flooding": 0}}
    )
    assert ok.attribution == {"coastal flooding": 0}
    aside = {"numerical results": {"from": "n2", "reason": "", "attribution": 2}}
    with pytest.raises(ValueError, match="0 to 1 level"):
        ThemesFile.model_validate({**doc, "set_aside": aside})
    # an entry without an attribution is written without the key
    assert "attribution" not in _tree().model_dump(by_alias=True)["set_aside"]["numerical results"]


def test_set_attribution():
    edit = set_attribution(_tree(), ["coastal flooding", "beach erosion"], 1)
    assert edit.tree.attribution == {"beach erosion": 1, "coastal flooding": 1}
    assert edit.description == "count 2 keywords down to level 1"
    assert set_attribution(edit.tree, "beach erosion", 0).description == "count 1 keyword nowhere"
    cleared = set_attribution(edit.tree, ["beach erosion", "coastal flooding"], None)
    assert cleared.tree.attribution == {}
    assert cleared.description == "count 2 keywords at their node's level"
    on_theme = move_keywords(_tree(), "beach erosion", "n1").tree
    assert set_attribution(on_theme, "beach erosion", 0).tree.attribution == {"beach erosion": 0}
    with pytest.raises(ThemeEditError, match="below the level of the keyword's node"):
        set_attribution(on_theme, ["beach erosion", "dune retreat"], 1)
    with pytest.raises(ThemeEditError, match="None or 0 to 1"):
        set_attribution(_tree(), "beach erosion", 2)
    with pytest.raises(ThemeEditError, match="None or 0 to 1"):
        set_attribution(_tree(), "beach erosion", True)
    with pytest.raises(ThemeEditError, match="put back first"):
        set_attribution(_tree(), "numerical results", 0)
    with pytest.raises(ThemeEditError, match="not in the tree"):
        set_attribution(_tree(), "tidal inlet", 0)


def test_the_carry_rule_on_moves():
    tree = _attributed()
    same = move_keywords(tree, ["coastal flooding", "storm surge model"], "n2").tree
    assert same.attribution == tree.attribution  # the same node: nothing changes
    sibling = move_keywords(tree, ["coastal flooding", "storm surge model"], "n3").tree
    assert sibling.attribution == {"storm surge model": 0}  # another node: dropped; 0 stays
    up = move_keywords(tree, ["coastal flooding", "storm surge model"], "n1").tree
    assert up.attribution == {"storm surge model": 0}
    moved = move_node(tree, "n2", "n4").tree  # the keywords keep their node
    assert moved.attribution == tree.attribution
    assert [c.as_dict() for c in compare(tree, sibling) if c.kind == "attribution"] == [
        {"kind": "attribution", "keyword": "coastal flooding", "before": 1}
    ]


def test_the_carry_rule_on_merges_and_splits():
    tree = _attributed()
    assert merge_nodes(tree, "n2", "n3").tree.attribution == {"storm surge model": 0}
    # merging level-1 nodes: the keywords of their children keep their nodes
    assert merge_nodes(tree, "n1", "n4").tree.attribution == tree.attribution
    split = split_node(tree, "n2", [(["coastal flooding"], {"en": "Flooding"})]).tree
    assert split.attribution == {"storm surge model": 0}
    kept = split_node(tree, "n1", [(["n3"], {"en": "Coasts"})]).tree  # Surge stays in Hazards
    assert kept.attribution == tree.attribution
    moved = split_node(
        tree, "n1", [(["n2"], {"en": "Storms"})]
    ).tree  # Surge moves, keeps its keywords
    assert moved.attribution == tree.attribution


def test_set_aside_keeps_the_attribution_and_put_back_carries_it():
    tree = set_aside(_attributed(), ["coastal flooding", "storm surge model"], "later").tree
    assert tree.attribution == {}
    assert tree.set_aside["coastal flooding"].attribution == 1
    assert tree.set_aside["storm surge model"].attribution == 0
    back = put_back(tree, ["coastal flooding", "storm surge model"]).tree
    assert back == _attributed()
    elsewhere = put_back(tree, ["coastal flooding", "storm surge model"], "n3").tree
    assert elsewhere.attribution == {"storm surge model": 0}
    up = put_back(tree, ["coastal flooding", "storm surge model"], "n1").tree
    assert up.attribution == {"storm surge model": 0}


def test_depth_changes_shift_attributions():
    tree = _attributed()  # on n2 (level 2): coastal flooding 1, storm surge model 0
    top = insert_level(tree, 1).tree  # a new level above the counted one
    assert top.attribution == {"coastal flooding": 2, "storm surge model": 0}
    middle = insert_level(tree, 2).tree  # just below the counted level
    assert middle.attribution == tree.attribution
    bottom = insert_level(tree, 3).tree
    assert bottom.attribution == tree.attribution
    for at in (1, 2, 3):
        assert remove_level(insert_level(tree, at).tree, at).tree == tree
    # removing the level it counted toward: it counts toward no remaining level
    assert remove_level(tree, 1).tree.attribution == {
        "coastal flooding": 0,
        "storm surge model": 0,
    }
    # removing its node's level: on the parent, it counts at that node's level again
    assert remove_level(tree, 2).tree.attribution == {"storm surge model": 0}
    aside = set_aside(tree, "coastal flooding").tree
    assert insert_level(aside, 1).tree.set_aside["coastal flooding"].attribution == 2
    assert remove_level(aside, 2).tree.set_aside["coastal flooding"].attribution is None


def test_rebase_keeps_the_attribution_of_surviving_keywords():
    tree = _attributed()
    result = rebase(tree, vocabulary_of(tree) - {"storm surge model"}, {})
    assert result.tree.attribution == {"coastal flooding": 1}
    assert {c.kind for c in result.changes} == {"removed"}


# ── depth ────────────────────────────────────────────────────────────────────


def _paths(tree: ThemesFile) -> dict[str, list[dict[str, str]]]:
    """Each placed keyword's chain of node names, top first."""
    by_id = {n.id: n for n in tree.nodes}
    out = {}
    for k, nid in tree.keywords.items():
        chain = []
        while nid is not None:
            chain.append(dict(by_id[nid].names))
            nid = by_id[nid].parent
        out[k] = chain[::-1]
    return out


def _broad() -> ThemesFile:
    """_tree() with keywords on the theme Hazards (n1) itself."""
    tree = move_keywords(_tree(), ["coastal flooding"], "n1").tree
    return set_attribution(tree, "coastal flooding", 0).tree


def test_insert_a_level_at_the_bottom_adds_an_empty_level():
    edit = insert_level(_broad(), 3)
    tree = edit.tree
    assert tree.depth == 3 and edit.description == "insert level 3"
    assert [lv.names["en"] for lv in tree.levels] == ["Field", "Theme", "Topic"]
    assert [lv.names["fr"] for lv in tree.levels] == ["Champ", "Thème", "Sujet"]
    assert tree.nodes == _broad().nodes  # keywords stay on their nodes; no node yet below
    assert tree.keywords == _broad().keywords
    assert tree.set_aside == _broad().set_aside
    assert _paths(tree) == _paths(_broad())
    deeper = create_node(tree, "n2", {"en": "Surge models"}).tree  # room for finer topics
    deeper = move_keywords(deeper, "storm surge model", "n6").tree
    assert keywords_under(deeper, "n2") == ["storm surge model"]
    assert keywords_at(deeper, "n2") == []


def test_insert_a_level_at_the_top_makes_one_root():
    tree = insert_level(_broad(), 1).tree
    roots = children(tree, None)
    assert len(roots) == 1 and roots[0].names["en"] == "Field"
    assert [n.id for n in children(tree, roots[0].id)] == ["n1", "n4"]
    assert tree.keywords == _broad().keywords  # on their nodes, one level lower
    assert node_level(tree, "n1") == 2 and keywords_at(tree, "n1") == ["coastal flooding"]
    named = insert_level(_tree(), 1, {"en": "Marine sciences"}).tree
    assert children(named, None)[0].names == {"en": "Marine sciences"}
    with pytest.raises(ThemeEditError, match="new root"):
        insert_level(_tree(), 2, {"en": "x"})


def test_insert_a_middle_level():
    tree = insert_level(_broad(), 2).tree
    assert [lv.names["en"] for lv in tree.levels] == ["Field", "Theme", "Topic"]
    mids = children(tree, "n1")
    assert len(mids) == 1 and mids[0].names == {"en": "Hazards"}
    assert [n.id for n in children(tree, mids[0].id)] == ["n2", "n3"]
    assert tree.keywords == _broad().keywords  # Hazards keeps its own keyword
    assert keywords_at(tree, "n1") == ["coastal flooding"]
    assert keywords_at(tree, mids[0].id) == []
    assert node_level(tree, "n2") == 3
    assert _paths(tree)["storm surge model"][0] == {"en": "Hazards"}


def test_remove_a_level():
    edit = remove_level(_broad(), 2)
    tree = edit.tree
    assert tree.depth == 1 and [lv.names["en"] for lv in tree.levels] == ["Theme"]
    assert tree.keywords["coastal flooding"] == "n1" and tree.keywords["coral bleaching"] == "n4"
    assert tree.keywords["storm surge model"] == "n1"
    assert tree.set_aside["numerical results"].source == "n1"
    assert edit.description == "remove level 2"
    edit = remove_level(_broad(), 1)
    tree = edit.tree
    assert [lv.names["en"] for lv in tree.levels] == ["Theme"]  # Topic became the only level
    assert [(n.id, n.order) for n in children(tree, None)] == [("n2", 1), ("n3", 2), ("n5", 3)]
    # the keyword of the removed top-level node has nowhere to go: set aside
    entry = tree.set_aside["coastal flooding"]
    assert (entry.source, entry.reason, entry.attribution) == ("n1", LEVEL_REMOVED, 0)
    assert [c.kind for c in compare(_broad(), tree) if c.keyword == "coastal flooding"] == ["moved"]
    with pytest.raises(ThemeEditError, match="at least one level"):
        remove_level(tree, 1)
    four = insert_level(insert_level(_tree(), 1).tree, 1).tree
    with pytest.raises(ThemeEditError, match="already has 4"):
        insert_level(four, 1)


def test_keywords_on_intermediate_levels_through_depth_changes():
    tree = insert_level(_broad(), 1).tree  # Field › Theme › Topic; coastal flooding on a theme
    assert node_level(tree, tree.keywords["coastal flooding"]) == 2
    assert remove_level(tree, 1).tree == _broad()
    flat = remove_level(tree, 2).tree  # the themes dissolve into the field
    root = children(tree, None)[0].id
    assert flat.keywords["coastal flooding"] == root
    assert flat.attribution["coastal flooding"] == 0
    assert flat.keywords["storm surge model"] == "n2" and node_level(flat, "n2") == 2


def test_renamed_levels_keep_their_names_when_the_depth_changes():
    tree = rename_level(_tree(), 1, {"en": "Axis"}).tree  # fr and pt still the defaults
    deeper = insert_level(tree, 3).tree
    assert deeper.levels[0].names == {"en": "Axis", "fr": "Champ", "pt": "Área"}
    assert [lv.names["en"] for lv in deeper.levels] == ["Axis", "Theme", "Topic"]
    assert remove_level(deeper, 3).tree.levels == tree.levels


@pytest.mark.parametrize("seed", range(120))
def test_inserting_then_removing_a_level_gives_the_tree_back(seed):
    rng = random.Random(seed)
    tree = normalized_orders(random_tree(rng, rng.randint(1, 3)))
    at = rng.randint(1, tree.depth + 1)
    deeper = insert_level(tree, at).tree
    assert deeper.depth == tree.depth + 1
    assert vocabulary_of(deeper) == vocabulary_of(tree)
    assert remove_level(deeper, at).tree == tree


# ── every operation, on random trees ─────────────────────────────────────────


@pytest.mark.parametrize("seed", range(150))
def test_random_operations_keep_trees_valid_and_keywords_held(seed):
    rng = random.Random(1000 + seed)
    tree = random_tree(rng)
    held = vocabulary_of(tree)
    for _ in range(12):
        before = tree.model_dump_json(by_alias=True)
        edit = random_edit(rng, tree)
        if edit is None:
            continue
        assert tree.model_dump_json(by_alias=True) == before  # the argument is untouched
        result = edit.tree
        assert ThemesFile.model_validate(result.model_dump(by_alias=True)) == result
        assert canonical(result) == result
        assert vocabulary_of(result) == held  # no operation adds or loses a keyword
        assert edit.description and len(edit.description) <= 60
        assert set(result.review) <= held
        for k, n in result.attribution.items():  # below the level of the keyword's node
            assert n < levels_of(result)[result.keywords[k]], k
        _check_attribution_carried(tree, edit)
        tree = result


def _effective(tree: ThemesFile, keyword: str) -> int | None:
    if keyword in tree.set_aside:
        return tree.set_aside[keyword].attribution
    return tree.attribution.get(keyword)


def _check_attribution_carried(before: ThemesFile, edit) -> None:
    """Only set_attribution and depth changes set an attribution; others keep it or drop it.

    The carry rule: a keyword that keeps its node keeps its attribution; one that
    gets another node keeps only ``0``.
    """
    after = edit.tree
    if edit.description.split()[0] in {"count", "insert", "remove"}:
        return
    for k in vocabulary_of(before):
        old, new = _effective(before, k), _effective(after, k)
        assert new in (old, None), (edit.description, k, old, new)
        if old == 0:
            assert new == 0, (edit.description, k)  # shown only: survives every move
        if k in before.keywords and k in after.keywords:
            if before.keywords[k] == after.keywords[k]:
                assert new == old, (edit.description, k)
            elif old:
                assert new is None, (edit.description, k)


def _chain(tree: ThemesFile, node_id: str) -> list[str]:
    by_id = {n.id: n for n in tree.nodes}
    chain = [node_id]
    while by_id[chain[-1]].parent is not None:
        chain.append(by_id[chain[-1]].parent)
    return chain[::-1]


# ── rebase ───────────────────────────────────────────────────────────────────


def test_rebase_rules():
    vocab = ["storm surge model", "beach erosion", "dune retreat", "tidal inlet", "sea level"]
    result = rebase(_tree(), vocab, {"tidal inlet": "n3", "sea level": None}, run="r2")
    tree = result.tree
    assert tree.keywords == {
        "beach erosion": "n3",
        "dune retreat": "n3",
        "storm surge model": "n2",
        "tidal inlet": "n3",
    }
    assert tree.set_aside["sea level"].reason == NEW_WITHOUT_PLACE
    assert tree.review == {
        "dune retreat": "to_check",
        "sea level": "to_check",
        "tidal inlet": "to_check",
    }
    assert {n.id for n in tree.nodes} == {"n1", "n2", "n3"}  # Ecology and Reefs emptied: removed
    assert tree.based_on.run == "r2"
    assert tree.based_on.vocabulary == vocabulary_fingerprint(vocab)
    assert [c.as_dict() for c in result.changes] == [
        {"kind": "node_removed", "node": "n4"},
        {"kind": "node_removed", "node": "n5", "before": "n4"},
        {"kind": "removed", "keyword": "coastal flooding", "before": "n2"},
        {"kind": "removed", "keyword": "coral bleaching", "before": "n5"},
        {"kind": "removed", "keyword": "numerical results", "before": SET_ASIDE},
        {"kind": "added", "keyword": "sea level", "after": SET_ASIDE},
        {"kind": "added", "keyword": "tidal inlet", "after": "n3"},
    ]
    assert result.description == "rebase: 2 new, 3 gone, 2 nodes removed"


def test_rebase_keeps_nodes_that_were_already_empty_and_dangling_origins():
    tree = create_node(_tree(), "n4", {"en": "Plankton"}).tree  # n6, empty
    tree = create_node(tree, None, {"en": "Societies"}).tree  # n7, empty
    tree = set_aside(tree, "coral bleaching").tree
    result = rebase(tree, vocabulary_of(tree) - {"storm surge model", "coastal flooding"}, {})
    ids = {n.id for n in result.tree.nodes}
    assert "n2" not in ids  # emptied by the rebase
    assert {"n5", "n6", "n7", "n4"} <= ids  # n5 was already empty (its keyword set aside)
    assert result.tree.set_aside["numerical results"].source == "n2"  # left as it was
    gone = rebase(_tree(), ["beach erosion"], {}).tree
    assert {n.id for n in gone.nodes} == {"n1", "n3"}


def test_rebase_removes_a_parent_emptied_with_its_already_empty_children():
    tree = create_node(_tree(), "n4", {"en": "Plankton"}).tree  # n6 under Ecology, empty
    result = rebase(tree, vocabulary_of(tree) - {"coral bleaching"}, {})
    assert {"n4", "n5", "n6"}.isdisjoint({n.id for n in result.tree.nodes})
    assert {c.node for c in result.changes if c.node} == {"n4", "n5", "n6"}


def test_rebase_forces_nothing():
    with pytest.raises(ThemeEditError, match="without a proposal"):
        rebase(_tree(), [*vocabulary_of(_tree()), "tidal inlet"], {})
    with pytest.raises(ThemeEditError, match="not nodes of the tree"):
        rebase(_tree(), ["tidal inlet"], {"tidal inlet": "n9"})
    broad = rebase(_tree(), [*vocabulary_of(_tree()), "tidal inlet"], {"tidal inlet": "n1"})
    assert broad.tree.keywords["tidal inlet"] == "n1"  # any level may be proposed
    with pytest.raises(ThemeEditError, match="non-empty text"):
        rebase(_tree(), ["", "tidal inlet"], {"tidal inlet": None})


def test_rebase_onto_the_same_vocabulary_changes_nothing():
    tree = rebase(_tree(), vocabulary_of(_tree()), {}, run="r1").tree
    again = rebase(tree, sorted(vocabulary_of(tree)), {"coral bleaching": "n2"}, run="r1")
    assert again.tree is tree and again.changes == () and again.description == "rebase: no change"


@pytest.mark.parametrize("seed", range(200))
def test_rebase_properties(seed):
    rng = random.Random(seed)
    tree = random_tree(rng, depth=seed % 4 + 1)  # every depth
    vocab, new = next_vocabulary(rng, tree)
    proposals = random_proposals(rng, tree, new)
    result = rebase(tree, vocab, proposals, run="themes.group/20260928T120000Z-abcd")
    out = result.tree
    old_vocab, new_vocab = vocabulary_of(tree), set(vocab)
    assert ThemesFile.model_validate(out.model_dump(by_alias=True)) == out
    assert vocabulary_of(out) == new_vocab
    # every surviving keyword keeps its place, set-aside entries included
    for k in old_vocab & new_vocab:
        if k in tree.keywords:
            assert out.keywords[k] == tree.keywords[k]
        else:
            assert out.set_aside[k] == tree.set_aside[k]
        assert out.review.get(k) == tree.review.get(k)
    # new keywords go where proposed, to check
    for k in new:
        if proposals[k] is None:
            assert out.set_aside[k].reason == NEW_WITHOUT_PLACE
        else:
            assert out.keywords[k] == proposals[k]
        assert out.review[k] == "to_check"
    # the reconciliation list names exactly the changed keywords and the removed nodes
    removed_nodes = {n.id for n in tree.nodes} - {n.id for n in out.nodes}
    assert {c.keyword for c in result.changes if c.keyword} == old_vocab ^ new_vocab
    assert {c.node for c in result.changes if c.node} == removed_nodes
    assert {c.kind for c in result.changes} <= {"added", "removed", "node_removed"}
    assert len(result.changes) == len(old_vocab ^ new_vocab) + len(removed_nodes)
    assert result.changes == compare(tree, out)
    # surviving nodes are untouched; removed nodes had keywords and lost them all
    kept = {n.id: n for n in out.nodes}
    for n in tree.nodes:
        if n.id in kept:
            assert kept[n.id] == n
    before_has = {nid for nid in levels_of(tree) if keywords_under(tree, nid)}
    for nid in removed_nodes:
        chain = [nid]
        by_id = {n.id: n for n in tree.nodes}
        while by_id[chain[-1]].parent is not None:
            chain.append(by_id[chain[-1]].parent)
        assert any(a in before_has for a in chain)
    for nid in kept:
        assert nid not in before_has or keywords_under(out, nid)
    # the same vocabulary again changes nothing
    again = rebase(out, vocab, proposals, run="themes.group/20260928T120000Z-abcd")
    assert again.tree == out and again.changes == ()
    # the vocabulary fingerprint names the vocabulary
    assert out.based_on.vocabulary == vocabulary_fingerprint(vocab)
    assert out.based_on.run == "themes.group/20260928T120000Z-abcd"


@pytest.mark.parametrize("seed", range(60))
def test_rebase_onto_its_own_vocabulary_only_records_the_basis(seed):
    rng = random.Random(500 + seed)
    tree = random_tree(rng)
    result = rebase(tree, vocabulary_of(tree), {}, run=tree.based_on.run)
    assert result.changes == ()
    assert result.tree.model_copy(update={"based_on": tree.based_on}) == tree


# ── comparison ───────────────────────────────────────────────────────────────


def test_compare_names_every_kind_of_change():
    before = _tree()
    after = rename_node(before, "n2", {"en": "Surge"}).tree
    after = move_node(after, "n3", "n4").tree
    after = move_node(after, "n4", None, position=0).tree
    after = set_aside(after, "coral bleaching", "duplicate").tree
    after = set_aside(after, "numerical results", "a method").tree
    after = set_review(after, "dune retreat", "reviewed").tree
    after = create_node(after, "n1", {"en": "Flooding"}).tree
    after = rename_level(after, 2, {"en": "Subject"}).tree
    kinds = {(c.kind, c.node or c.keyword or c.level) for c in compare(before, after)}
    assert kinds == {
        ("level_renamed", 2),
        ("node_renamed", "n2"),
        ("node_moved", "n3"),
        ("node_reordered", "n4"),
        ("node_reordered", "n1"),
        ("node_added", "n6"),
        ("moved", "coral bleaching"),
        ("set_aside_changed", "numerical results"),
        ("review", "dune retreat"),
    }
    assert compare(before, before) == ()
    deeper = insert_level(before, 3).tree
    assert Change("depth", before=2, after=3) in compare(before, deeper)


def test_leaves_of_helper_agrees_with_the_module():
    tree = _tree()
    assert sorted(leaves_of(tree)) == ["n2", "n3", "n5"]
