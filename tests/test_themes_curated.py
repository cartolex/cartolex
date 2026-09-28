# SPDX-License-Identifier: MIT
"""Converters between a depth-2 theme tree and the engine's curated document.

The round trip is exact; the engine's own checks accept what :func:`to_curated`
writes, and its apply stage runs on it. All data is invented.
"""

from __future__ import annotations

import json
import random

import numpy as np
import pandas as pd
import pytest
from themes_random import random_tree

from cartolex.lexicon.subfields import (
    concept_term_index,
    term_to_group_maps,
    term_to_subfield_direct,
)
from cartolex.lexicon.subfields_edit import EDIT_SCHEMA_VERSION, validate_doc
from cartolex.project.models import ThemesFile
from cartolex.project.themes import (
    canonical,
    create_node,
    default_level_names,
    new_tree,
    rebase,
    set_aside,
    set_attribution,
    vocabulary_of,
)
from cartolex.project.themes_curated import (
    CURATED_SCHEMA_VERSION,
    NOT_GROUPED,
    THEME_KEYWORDS_OF,
    from_curated,
    to_curated,
)


def test_the_schema_version_is_the_engines():
    assert CURATED_SCHEMA_VERSION == EDIT_SCHEMA_VERSION


def _draft() -> tuple[dict, list[str]]:
    """A deterministic draft as the grouping stage writes it (two subfields, three concepts)."""
    terms = ["storm surge model", "coastal flooding", "beach erosion", "coral bleaching", "reef"]
    doc = {
        "schema_version": "1.0",
        "domain_title": "Coastal systems",
        "status": "draft",
        "curation": "deterministic",
        "subfields": [
            {
                "id": 0,
                "label": "storm surge model",
                "label_fr": "modèle de surcote",
                "keep": True,
                "top_terms": ["storm surge model", "beach erosion"],
                "color": "#d22d3c",
            },
            {"id": 1, "label": "coral bleaching", "label_fr": "coral bleaching", "keep": True},
        ],
        "concepts": [
            {
                "id": 0,
                "label": "storm surge model",
                "subfield_id": 0,
                "term_indices": [0, 1],
                "term_merges": [],
                "top_terms": ["storm surge model"],
            },
            {"id": 1, "label": "beach erosion", "subfield_id": 0, "term_indices": [2]},
            {"id": 2, "label": "coral bleaching", "subfield_id": 1, "term_indices": [3, 4]},
        ],
    }
    return doc, terms


def test_a_draft_becomes_a_tree():
    doc, terms = _draft()
    imported = from_curated(doc, terms)
    tree = imported.tree
    assert imported.notes == ()
    assert tree.depth == 2 and [lv.names for lv in tree.levels] == default_level_names(2)
    assert [(n.id, n.parent, n.order) for n in tree.nodes] == [
        ("s0", None, 1),
        ("c0", "s0", 1),
        ("c1", "s0", 2),
        ("s1", None, 2),
        ("c2", "s1", 1),
    ]
    assert tree.nodes[0].names == {"en": "storm surge model", "fr": "modèle de surcote"}
    assert tree.keywords == {
        "beach erosion": "c1",
        "coastal flooding": "c0",
        "coral bleaching": "c2",
        "reef": "c2",
        "storm surge model": "c0",
    }
    # back to the engine: the same numbers, the same placements
    back = to_curated(tree, terms, domain_title="Coastal systems")
    assert validate_doc(back, n_terms=len(terms), terms_by_idx=terms) == []
    assert [(s["id"], s["label"], s.get("label_fr")) for s in back["subfields"]] == [
        (0, "storm surge model", "modèle de surcote"),
        (1, "coral bleaching", "coral bleaching"),
    ]
    assert [(c["id"], c["subfield_id"], c["term_indices"]) for c in back["concepts"]] == [
        (0, 0, [0, 1]),
        (1, 0, [2]),
        (2, 1, [3, 4]),
    ]
    assert concept_term_index(back)[0] == concept_term_index(doc)[0]


def test_what_a_tree_cannot_hold_is_set_aside_and_noted():
    terms = [f"term {i}" for i in range(14)]
    doc = {
        "schema_version": "1.1",
        "subfields": [
            {"id": 0, "label": "A", "keep": True, "pinned_color": "#000000"},
            {"id": 1, "label": "B", "keep": False},
        ],
        "concepts": [
            {
                "id": 0,
                "label": "a0",
                "subfield_id": 0,
                "term_indices": [0, 1, 2],
                "term_merges": [[0, 2]],
                "ride_along_terms": ["Term 1 ", "gone term"],
                "subfield_only_terms": ["term 10"],
            },
            {
                "id": 1,
                "label": "b0",
                "subfield_id": 1,
                "term_indices": [3],
                "subfield_only_terms": ["term 3"],
            },
            {
                "id": 2,
                "label": "a1",
                "subfield_id": 0,
                "term_indices": [10, 11, 12],
                "subfield_only_terms": ["term 11"],
                "ride_along_terms": ["term 12"],
            },
        ],
        "stash": {
            "concepts": [
                {
                    "concept": {"id": 5, "term_indices": [4], "ride_along_terms": ["term 4"]},
                    "origin_subfield_id": 0,
                }
            ],
            "terms": [
                {
                    "term_index": 5,
                    "origin_concept_id": 0,
                    "merge_group": [5, 6],
                    "status": "subfield_only",
                }
            ],
        },
        "trash": {
            "subfields": [{"subfield": {"id": 9}, "concepts": [{"id": 7, "term_indices": [7]}]}],
            "concepts": [],
            "terms": [{"term_index": 8, "origin_concept_id": 99, "status": "ride_along"}],
        },
    }
    imported = from_curated(doc, terms)
    tree = imported.tree
    assert vocabulary_of(tree) == set(terms)  # every keyword is placed or set aside
    assert tree.keywords == {
        "term 0": "c0",
        "term 1": "c0",
        "term 10": "c2",
        "term 11": "s0",  # subfield-only: the subfield's own keyword
        "term 12": "c2",
    }
    # "term 10" is subfield-only in another concept: that status does not apply
    assert tree.attribution == {"term 1": 0, "term 12": 0}
    assert {k: e.attribution for k, e in tree.set_aside.items() if e.attribution is not None} == {
        "term 3": 1,
        "term 4": 0,
        "term 5": 1,
        "term 6": 1,
        "term 8": 0,
    }
    assert imported.merges == (("term 2", "term 0"),)
    assert imported.keyword_rows(language="en", decided_at="2026-09-28T10:00:00Z") == [
        {
            "term": "term 2",
            "language": "en",
            "decision": "merge",
            "target": "term 0",
            "reason": "merge variant in the curated document",
            "source": "person",
            "decided_at": "2026-09-28T10:00:00Z",
        }
    ]
    aside = {k: (e.source, e.reason) for k, e in tree.set_aside.items()}
    assert aside["term 2"] == ("c0", "merge variant of 'term 0'")
    assert aside["term 3"] == (None, "its group was not kept")
    assert aside["term 4"] == (None, "stashed in the curated document")
    assert aside["term 5"] == ("c0", "stashed in the curated document")
    assert aside["term 6"] == ("c0", "stashed in the curated document")
    assert aside["term 7"] == (None, "trashed in the curated document")
    assert aside["term 8"] == (None, "trashed in the curated document")
    assert aside["term 9"] == (None, NOT_GROUPED)
    assert aside["term 13"] == (None, NOT_GROUPED)
    assert "term 12" not in aside
    assert imported.notes == (
        "1 group not kept ([1]): 1 keyword set aside",
        "1 merge variant set aside (merges belong in keywords.csv)",
        "3 keywords stashed in the document: set aside",
        "2 keywords trashed in the document: set aside",
        "2 keywords in no group: set aside",
        "1 subfield-only term placed on its subfield's node",
        "2 term statuses naming no keyword of their concept dropped",
        "pinned colours of 1 subfield dropped",
    )


def test_broken_documents_are_refused():
    doc, terms = _draft()
    twice = json.loads(json.dumps(doc))
    twice["concepts"][1]["term_indices"] = [1]
    with pytest.raises(ValueError, match="in both concept 0 and concept 1"):
        from_curated(twice, terms)
    outside = json.loads(json.dumps(doc))
    outside["concepts"][1]["term_indices"] = [42]
    with pytest.raises(ValueError, match="outside the vocabulary"):
        from_curated(outside, terms)
    orphan = json.loads(json.dumps(doc))
    orphan["concepts"][1]["subfield_id"] = 7
    with pytest.raises(ValueError, match="unknown subfield"):
        from_curated(orphan, terms)
    with pytest.raises(ValueError, match="twice"):
        from_curated(doc, [*terms, "reef"])


def test_to_curated_needs_a_depth_2_tree_rebased_on_the_vocabulary():
    doc, terms = _draft()
    tree = from_curated(doc, terms).tree
    with pytest.raises(ValueError, match="two levels"):
        to_curated(new_tree(3), [])
    with pytest.raises(ValueError, match="neither placed nor set aside"):
        to_curated(tree, [*terms, "tidal inlet"])
    with pytest.raises(ValueError, match="not in the vocabulary"):
        to_curated(tree, terms[:-1])
    with pytest.raises(ValueError, match="unknown language"):
        to_curated(tree, terms, reference_language="de")


def test_attributions_are_the_engines_term_statuses():
    tree = create_node(new_tree(), None, {"en": "Hazards"}).tree
    tree = create_node(tree, "n1", {"en": "Surge"}).tree
    vocab = ["storm surge", "numerical model", "field survey", "coastal flooding", "Storm Surge"]
    tree = rebase(tree, vocab[:4], dict.fromkeys(vocab[:4], "n2")).tree
    tree = set_attribution(tree, "numerical model", 0).tree
    tree = set_attribution(tree, "field survey", 1).tree
    tree = set_aside(set_attribution(tree, "coastal flooding", 1).tree, "coastal flooding").tree
    doc = to_curated(tree, vocab[:4])
    concept = doc["concepts"][0]
    assert concept["ride_along_terms"] == ["numerical model"]
    assert concept["subfield_only_terms"] == ["field survey"]
    assert [t["status"] for t in doc["trash"]["terms"]] == ["subfield_only"]
    assert "attribution" not in doc["theme_tree"]
    assert "attribution" not in doc["trash"]["terms"][0]["set_aside"]
    assert from_curated(doc, vocab[:4]).tree == tree
    plain = to_curated(
        set_attribution(tree, ["numerical model", "field survey"], None).tree, vocab[:4]
    )
    assert "ride_along_terms" not in plain["concepts"][0]
    assert "subfield_only_terms" not in plain["concepts"][0]
    # two keywords the engine cannot tell apart must share their attribution
    clash = rebase(tree, vocab, {"Storm Surge": "n2"}).tree
    assert to_curated(clash, vocab)  # both count at every level: fine
    clash = set_attribution(clash, "Storm Surge", 0).tree
    with pytest.raises(ValueError, match="differ only by case"):
        to_curated(clash, vocab)


def test_keywords_on_a_theme_go_to_a_concept_of_their_own():
    tree = create_node(new_tree(), None, {"en": "Hazards", "fr": "Aléas"}, node_id="s3").tree
    tree = create_node(tree, "s3", {"en": "Surge"}, node_id="c5").tree
    tree = create_node(tree, None, {"en": "Coasts"}).tree  # n1: keywords, no topic
    vocab = ["storm surge", "sea level", "extreme events", "shoreline", "coastal zone"]
    places = {
        "storm surge": "c5",
        "sea level": "s3",
        "extreme events": "s3",
        "shoreline": "n1",
        "coastal zone": "n1",
    }
    tree = rebase(tree, vocab, places).tree
    tree = set_attribution(tree, ["extreme events", "coastal zone"], 0).tree
    doc = to_curated(tree, vocab)
    assert [(c["id"], c["subfield_id"], c["label"]) for c in doc["concepts"]] == [
        (5, 3, "Surge"),
        (6, 3, "Hazards"),  # the theme's own keywords, numbered after every concept
        (7, 4, "Coasts"),
    ]
    own = doc["concepts"][1]
    assert own[THEME_KEYWORDS_OF] == "s3" and own["label_fr"] == "Aléas"
    assert own["subfield_only_terms"] == ["sea level"]
    assert own["ride_along_terms"] == ["extreme events"]
    assert "theme_node" not in own
    assert doc["concepts"][2]["subfield_only_terms"] == ["shoreline"]
    assert validate_doc(doc, n_terms=len(vocab), terms_by_idx=vocab) == []
    assert term_to_subfield_direct(doc, vocab) == {"sea level": 3, "shoreline": 4}
    assert from_curated(doc, vocab).tree == tree


def test_labels_follow_the_reference_language_and_scores_order_top_terms():
    tree = create_node(new_tree(), None, {"fr": "Aléas", "pt": "Riscos"}).tree
    tree = create_node(tree, "n1", {"en": "Surge"}).tree
    tree = rebase(tree, ["a", "b", "c"], {"a": "n2", "b": "n2", "c": "n2"}).tree
    doc = to_curated(tree, ["a", "b", "c"], reference_language="fr", scores={"c": 3.0, "a": 1.0})
    subfield, concept = doc["subfields"][0], doc["concepts"][0]
    assert (subfield["label"], subfield["label_pt"]) == ("Aléas", "Riscos")
    assert "label_fr" not in subfield
    assert (concept["label"], concept["label_en"]) == ("Surge", "Surge")  # no French name
    assert concept["top_terms"] == ["c", "a", "b"]
    assert subfield["top_terms"] == ["c", "a", "b"]
    assert to_curated(tree, ["a", "b", "c"])["concepts"][0]["top_terms"] == ["a", "b", "c"]
    assert from_curated(doc, ["a", "b", "c"], reference_language="fr").tree == tree


@pytest.mark.parametrize("seed", range(120))
def test_the_round_trip_is_exact(seed):
    rng = random.Random(seed)
    tree = random_tree(rng, depth=2)
    terms = sorted(vocabulary_of(tree))
    rng.shuffle(terms)
    ref = rng.choice(["en", "fr", "pt"])
    doc = to_curated(tree, terms, reference_language=ref)
    doc = json.loads(json.dumps(doc))  # as written to a file
    imported = from_curated(doc, terms, reference_language=ref)
    assert imported.tree == canonical(tree)
    assert imported.notes == ()
    assert to_curated(imported.tree, terms, reference_language=ref) == doc
    # the engine reads the same placements
    assert validate_doc(doc, n_terms=len(terms), terms_by_idx=terms) == []
    term_to_concept, concept_to_subfield, _, _ = term_to_group_maps(doc, terms)
    node_of_concept = {
        c["id"]: c["theme_node"]["id"] if "theme_node" in c else c[THEME_KEYWORDS_OF]
        for c in doc["concepts"]
    }
    node_of_subfield = {s["id"]: s["theme_node"]["id"] for s in doc["subfields"]}
    parent = {n.id: n.parent for n in tree.nodes}
    # a keyword on a topic counting at its level is its concept's; one on a topic
    # counting toward level 1, or on a theme, is its subfield's; one counting
    # nowhere is neither
    assert {k: node_of_concept[c] for k, c in term_to_concept.items()} == {
        k.lower(): n
        for k, n in tree.keywords.items()
        if parent[n] is not None and k not in tree.attribution
    }
    theme_of = {
        k.lower(): parent[n] or n
        for k, n in tree.keywords.items()
        if tree.attribution.get(k) != 0 and (parent[n] is None or tree.attribution.get(k) == 1)
    }
    assert {
        k: node_of_subfield[s] for k, s in term_to_subfield_direct(doc, terms).items()
    } == theme_of
    for cid, sid in concept_to_subfield.items():
        node = node_of_concept[cid]
        assert (parent[node] or node) == node_of_subfield[sid]


def _tiny_workspace(tmp_path):
    """Two orthogonal keyword blocks; persons 0-1 use block A, 2-3 block B."""
    from cartolex.atlas.model_files import save_embeddings, save_lexical_data
    from cartolex.atlas.types import Embeddings, LexicalData

    terms = ["a1", "a2", "b1", "b2", "noise"]
    X = np.array(
        [
            [1.0, 1.0, 0.0, 0.0, 0.1],
            [1.0, 1.0, 0.0, 0.0, 0.1],
            [0.0, 0.0, 1.0, 1.0, 0.1],
            [0.0, 0.0, 1.0, 1.0, 0.1],
        ]
    )
    meta_ind = pd.DataFrame({"researcher_id": ["p0", "p1", "p2", "p3"]})
    data = LexicalData(
        X=X, terms=terms, individuals=list(meta_ind["researcher_id"]), meta_ind=meta_ind, X_tf=X
    )
    Z_terms = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0], [0.5, 0.5]])
    Z_ind = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    emb = Embeddings(Z_ind=Z_ind, Z_terms=Z_terms, umap_ind=None, umap_terms=None)
    save_lexical_data(data, tmp_path / "lexical_data.json")
    save_embeddings(emb, tmp_path / "embeddings.json")
    return terms


def test_the_engines_apply_stage_runs_on_a_converted_tree(tmp_path):
    from cartolex.lexicon.subfields import apply_subfield_files

    terms = _tiny_workspace(tmp_path)
    tree = ThemesFile.model_validate(
        {
            "depth": 2,
            "levels": [{"names": n} for n in default_level_names(2)],
            "nodes": [
                {"id": "n1", "parent": None, "names": {"en": "Block A", "fr": "Bloc A"}},
                {"id": "n2", "parent": "n1", "names": {"en": "A"}},
                {"id": "n3", "parent": None, "names": {"en": "Block B"}, "order": 1},
                {"id": "n4", "parent": "n3", "names": {"en": "B"}},
            ],
            "keywords": {"a1": "n2", "a2": "n2", "b1": "n4", "b2": "n4"},
            "attribution": {"a2": 1, "b2": 0},
            "set_aside": {"noise": {"from": "n2", "reason": "too general"}},
        }
    )
    curated = tmp_path / "subfields.json"
    curated.write_text(json.dumps(to_curated(tree, terms), ensure_ascii=False), encoding="utf-8")
    applied = apply_subfield_files(
        curated_json=curated,
        lexical_data_json=tmp_path / "lexical_data.json",
        embeddings_json=tmp_path / "embeddings.json",
        final_json_out=tmp_path / "applied.json",
        weights_csv_out=tmp_path / "weights.csv",
        lexicon_weights_csv_out=tmp_path / "lexicon.csv",
    )
    members = {
        sf["label"]: {m["researcher_id"] for m in sf["member_researcher_ids"]}
        for sf in applied["subfields"]
    }
    assert members == {"Block A": {"p0", "p1"}, "Block B": {"p2", "p3"}}
    lexicon = pd.read_csv(tmp_path / "lexicon.csv")
    # the set-aside keyword carries no weight
    assert set(lexicon["term"]) == {"a1", "a2", "b1", "b2"}
    weight = {c["label"]: c["weight"] for c in applied["concepts"]}
    weight |= {s["label"]: s["weight"] for s in applied["subfields"]}
    a1, a2, b1 = (
        float(lexicon.loc[lexicon["term"] == t, "weight"].iloc[0]) for t in ("a1", "a2", "b1")
    )
    assert weight["A"] == pytest.approx(a1)  # a2 counts toward level 1 only
    assert weight["Block A"] == pytest.approx(a1 + a2)
    assert weight["B"] == weight["Block B"] == pytest.approx(b1)  # b2 counts nowhere


def _apply(tmp_path, name: str, doc: dict) -> tuple[dict, pd.DataFrame]:
    from cartolex.lexicon.subfields import apply_subfield_files

    curated = tmp_path / f"{name}.json"
    curated.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    applied = apply_subfield_files(
        curated_json=curated,
        lexical_data_json=tmp_path / "lexical_data.json",
        embeddings_json=tmp_path / "embeddings.json",
        final_json_out=tmp_path / f"{name}-applied.json",
        lexicon_weights_csv_out=tmp_path / f"{name}-lexicon.csv",
    )
    return applied, pd.read_csv(tmp_path / f"{name}-lexicon.csv")


def test_a_keyword_on_a_theme_weighs_as_a_subfield_only_term(tmp_path):
    """The engine gives a theme's own keyword the weights of the old subfield-only status."""
    terms = _tiny_workspace(tmp_path)
    old = {
        "schema_version": "1.0",
        "subfields": [
            {"id": 0, "label": "Block A", "keep": True},
            {"id": 1, "label": "Block B", "keep": True},
        ],
        "concepts": [
            {
                "id": 0,
                "label": "A",
                "subfield_id": 0,
                "term_indices": [0, 1, 4],
                "top_terms": ["a1", "a2"],
                "subfield_only_terms": ["noise"],
            },
            {"id": 1, "label": "B", "subfield_id": 1, "term_indices": [2, 3], "top_terms": ["b1"]},
        ],
    }
    imported = from_curated(old, terms)
    assert imported.tree.keywords["noise"] == "s0"  # the subfield's own keyword
    was, was_lexicon = _apply(tmp_path, "old", old)
    now, now_lexicon = _apply(tmp_path, "tree", to_curated(imported.tree, terms))
    for key in ("subfields", "concepts"):
        before = {c["label"]: (c["weight"], c["share"]) for c in was[key]}
        after = {
            c["label"]: (c["weight"], c["share"]) for c in now[key] if not c.get(THEME_KEYWORDS_OF)
        }
        assert after == before, key
    own = next(c for c in now["concepts"] if c.get(THEME_KEYWORDS_OF))
    assert own["weight"] == 0.0 and own["label"] == "Block A"
    weights = dict(zip(was_lexicon["term"], was_lexicon["weight"], strict=True))
    assert dict(zip(now_lexicon["term"], now_lexicon["weight"], strict=True)) == weights
