# SPDX-License-Identifier: MIT
"""The keyword space fitted on the texts, and the demo's people with two unrelated subjects."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse

from cartolex.atlas.reducers import compute_svd_embeddings, compute_text_svd_embeddings
from cartolex.atlas.types import LexicalData


def _cos(Z: np.ndarray, a: int, b: int) -> float:
    return float(Z[a] @ Z[b] / np.linalg.norm(Z[a]) / np.linalg.norm(Z[b]))


def test_the_text_space_keeps_a_persons_two_subjects_apart(tmp_path):
    # keywords 0-2: subject A, 3-5: B, 6-8: C, 9-11: D. Half the people work on A
    # and B, the other half on C and D, each text on one subject only.
    rng = np.random.default_rng(0)
    texts, people = [], []
    for p in range(12):
        for subject in (0, 3) if p < 6 else (6, 9):
            for _ in range(3):
                row = np.zeros(12)
                row[subject + rng.choice(3, 2, replace=False)] = 1
                texts.append(row)
        people.append(np.sum(texts[-6:], axis=0))
    D = sparse.csr_matrix(np.array(texts))
    X = sparse.csr_matrix(np.array(people))
    data = LexicalData(
        X=X,
        terms=[f"k{i}" for i in range(12)],
        individuals=[f"p{i}" for i in range(12)],
        meta_ind=pd.DataFrame({"id": [f"p{i}" for i in range(12)]}),
    )
    by_people = compute_svd_embeddings(data, n_components=4, model_path=tmp_path / "p.json")
    by_texts = compute_text_svd_embeddings(data, D, n_components=4, model_path=tmp_path / "t.json")
    # used by the same people, A and B collapse in the people's space ...
    assert _cos(by_people.Z_terms, 0, 3) > 0.8
    # ... and stay apart in the texts', where each subject's keywords stay together
    assert _cos(by_texts.Z_terms, 0, 3) < 0.2
    assert _cos(by_texts.Z_terms, 0, 1) > 0.9
    # the people are placed through the fitted space, one row each
    assert by_texts.Z_ind.shape == (12, 4)
    assert (tmp_path / "t.json").exists()


def test_a_demo_world_can_give_people_two_unrelated_subjects():
    from cartolex.demo import generate
    from cartolex.demo.vocabulary import THEME_BY_ID

    world = generate("S", 0, two_subjects=0.3)
    assert not world.is_default
    from cartolex.demo.generator import FIRST_SUBJECT

    two = [p for p in world.cohort if next(iter(p.themes.values())) == FIRST_SUBJECT]
    assert len(two) >= 5
    for p in two:
        first, second = p.themes
        assert second not in THEME_BY_ID[first].neighbours
        assert first not in THEME_BY_ID[second].neighbours
    for p in two:
        for w in (w for w in world.works if p.person_id in w.authors):
            assert not set(p.themes) <= set(w.themes), "a text never holds both subjects"
    assert generate("S", 0).two_subjects == 0.0


def test_a_text_space_with_many_keywords_of_another_language_says_so():
    from cartolex.build.engine import SPACE_LANGUAGE_SHARE, space_languages_apart

    n = 1000
    many = {"terms": n, "terms_other_language": round(n * SPACE_LANGUAGE_SHARE)}
    assert space_languages_apart(many, "text") == SPACE_LANGUAGE_SHARE
    assert space_languages_apart(many, "person") is None
    few = {"terms": n, "terms_other_language": round(n * SPACE_LANGUAGE_SHARE) - 1}
    assert space_languages_apart(few, "text") is None
    assert space_languages_apart({}, "text") is None
