# SPDX-License-Identifier: MIT
"""Each person's row of the space holds every keyword of the lexicon they use, unless a
number of keywords per person is set; the display list stays short."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse

from cartolex.atlas.io import build_lexical_matrix
from cartolex.atlas.model_files import save_person_terms
from cartolex.lexicon.consolidation import researcher_ids
from cartolex.lexicon.tfidf_utils import listed_keywords, researcher_matrices

TERMS = np.array(["tide gauge", "sediment", "wave", "Coastal Dune", "sea level"], dtype=object)


def _people() -> pd.DataFrame:
    return pd.DataFrame(
        {"last_name": ["Abel", "Brun", "Cole"], "first_name": ["Ana", "Ben", "Cy"], "unit": "U1"}
    )


def _X() -> sparse.csr_matrix:
    return sparse.csr_matrix(
        np.array(
            [
                [0.5, 0.1, 0.0, 0.2, 0.05],
                [0.0, 0.0, 0.0, 0.0, 0.0],
                [0.0, 0.3, 0.4, 0.0, 0.0],
            ]
        )
    )


def test_every_keyword_a_person_uses_enters_unless_a_number_is_set():
    X = _X()
    tf = X * 2.0
    score, score_tf, lengths = researcher_matrices(X, TERMS, set(), None, 1.0, X_tf=tf)
    assert score.nnz == X.nnz  # nothing cut
    # the length bonus (two words: twice the score), the plain counts beside it
    assert score[0, 0] == 0.5 * 2.0 and score_tf[0, 0] == 1.0
    assert list(lengths) == [2, 1, 1, 2, 2]
    two, _, _ = researcher_matrices(X, TERMS, {"sea level"}, 2, 1.0)
    assert np.diff(two.indptr).tolist() == [3, 0, 2]  # two best, and the whitelisted one
    assert two[0, 4] > 0 and two[0, 1] == 0


def test_the_display_list_is_short_and_the_space_reads_the_whole_matrix(tmp_path):
    X, people = _X(), _people()
    score, score_tf, lengths = researcher_matrices(X, TERMS, set(), None, 0.0, X_tf=X)
    listed = listed_keywords(score, score_tf, lengths, TERMS, people, limit=2)
    assert listed.groupby("last_name").size().to_dict() == {"Abel": 2, "Cole": 2}
    assert listed["term"].tolist()[:2] == ["tide gauge", "Coastal Dune"]  # the best first
    stored = tmp_path / "person_terms.json"
    save_person_terms(
        stored,
        score,
        score_tf,
        [str(t) for t in TERMS],
        researcher_ids(people),
        keywords_per_person=None,
    )
    roster = tmp_path / "roster.csv"
    people.to_csv(roster, index=False)
    listed.to_csv(tmp_path / "people.csv", index=False)
    data = build_lexical_matrix(tmp_path / "people.csv", roster, person_terms_json=stored)
    assert data.terms == sorted(t.lower() for t in TERMS)  # every used keyword, lower case
    assert data.X.nnz == X.nnz and len(data.individuals) == 2  # Brun uses none
    old = build_lexical_matrix(tmp_path / "people.csv", roster)  # an earlier run: the list
    assert old.X.nnz == 4
