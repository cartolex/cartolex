# SPDX-License-Identifier: MIT
"""Apply must not silently zero the researcher assignment when a curated doc
carries concepts but no subfield ``top_terms`` seeds (schema-1.0 docs written
by external curation tools)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from cartolex.atlas.model_files import save_embeddings, save_lexical_data
from cartolex.atlas.types import Embeddings, LexicalData


def _tiny_workspace(tmp_path):
    """Two orthogonal term blocks; researchers 0-1 use block A, 2-3 block B."""
    terms = ["a1", "a2", "b1", "b2"]
    X = np.array(
        [
            [1.0, 1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 1.0],
            [0.0, 0.0, 1.0, 1.0],
        ]
    )
    meta_ind = pd.DataFrame({"researcher_id": ["r0", "r1", "r2", "r3"]})
    data = LexicalData(
        X=X, terms=terms, individuals=list(meta_ind["researcher_id"]), meta_ind=meta_ind, X_tf=X
    )
    Z_terms = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    Z_ind = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    emb = Embeddings(Z_ind=Z_ind, Z_terms=Z_terms, umap_ind=None, umap_terms=None)
    lex_p = tmp_path / "lexical_data.json"
    emb_p = tmp_path / "embeddings.json"
    save_lexical_data(data, lex_p)
    save_embeddings(emb, emb_p)
    return lex_p, emb_p


def test_apply_without_subfield_seeds_assigns_researchers_by_concepts(tmp_path) -> None:
    from cartolex.lexicon.subfields import apply_subfield_files

    lex_p, emb_p = _tiny_workspace(tmp_path)
    curated = tmp_path / "subfields.json"
    curated.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "domain_title": "Domain X",
                "subfields": [
                    {"id": 1, "label": "A", "keep": True},
                    {"id": 2, "label": "B", "keep": True},
                ],
                "concepts": [
                    {
                        "id": 0,
                        "label": "cA",
                        "subfield_id": 1,
                        "term_indices": [0, 1],
                        "top_terms": ["a1", "a2"],
                    },
                    {
                        "id": 1,
                        "label": "cB",
                        "subfield_id": 2,
                        "term_indices": [2, 3],
                        "top_terms": ["b1", "b2"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    applied = apply_subfield_files(
        curated_json=curated,
        lexical_data_json=lex_p,
        embeddings_json=emb_p,
        final_json_out=tmp_path / "out.json",
        weights_csv_out=tmp_path / "weights.csv",
    )

    members = {
        sf["label"]: {m["researcher_id"] for m in sf["member_researcher_ids"]}
        for sf in applied["subfields"]
    }
    assert members["A"] == {"r0", "r1"}
    assert members["B"] == {"r2", "r3"}

    # The soft weights must discriminate too: r0 mostly A, r3 mostly B.
    w = pd.read_csv(tmp_path / "weights.csv")
    piv = w.pivot(index="researcher_id", columns="subfield_id", values="weight").fillna(0.0)
    assert piv.loc["r0", 1] > piv.loc["r0", 2]
    assert piv.loc["r3", 2] > piv.loc["r3", 1]
