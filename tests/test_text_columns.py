# SPDX-License-Identifier: MIT
"""The texts as columns (cartolex.project.text_columns) and the app's view of them
(cartolex.app.texts_view): the same facts as the tables, for everyone or one person."""

from __future__ import annotations

import numpy as np
import pytest

from cartolex.app.texts_view import texts_view
from cartolex.project import Project
from cartolex.project.tables import write_source_rows
from cartolex.project.text_columns import read_text_columns

TEXTS = [
    # text, year, version_of, source
    ("t1", 2020, None, "openalex"),
    ("t2", 2021, None, "openalex"),
    ("t3", None, None, "hal"),
    ("t4", 2022, "t2", "openalex"),  # a preprint of t2
    ("t5", 2019, None, "folder"),
]
PARTS = [
    # text, part, language, provider
    ("t1", "title", "en", "openalex"),
    ("t1", "abstract", "en", "openalex"),
    ("t2", "title", "fr", "openalex"),
    ("t3", "title", "fr", "hal"),
    ("t3", "abstract", "en", "hal"),
    ("t3", "abstract", "fr", "hal"),
    ("t4", "title", "en", "openalex"),
    ("t5", "full", "en", "folder"),
]
AUTHORS = [("t1", "p1"), ("t1", "p2"), ("t2", "p2"), ("t3", "p3"), ("t4", "p2"), ("t5", "p1")]


@pytest.fixture()
def project(tmp_path):
    project = Project.init(tmp_path / "p", name="Columns", domain_title="Columns")
    table = project.layout.table
    write_source_rows(
        table("texts"),
        "texts",
        (
            {"text_id": t, "slot": "s", "position": i, "year": y, "doc_type": "article",
             "title": f"Title {t}", "version_of": v, "source": s, "n_authors": 2}
            for i, (t, y, v, s) in enumerate(TEXTS)
        ),
    )  # fmt: skip
    write_source_rows(
        table("text_parts"),
        "text_parts",
        (
            {"text_id": t, "part": part, "language": lang, "provider": prov,
             "format": "plain", "content": f"{part} of {t}"}
            for t, part, lang, prov in sorted(PARTS)
        ),
    )  # fmt: skip
    write_source_rows(
        table("authorships"),
        "authorships",
        ({"text_id": t, "person_id": p, "position": 1} for t, p in AUTHORS),
    )
    yield project
    project.close()


def _facts(columns, tid):
    i = int(columns.lookup([tid])[0])
    codes, sets = columns.language_sets()
    providers = [
        p for b, p in enumerate(columns.provider_names) if int(columns.providers[i]) >> b & 1
    ]
    return (
        int(columns.year[i]) if columns.has_year[i] else None,
        bool(columns.superseded[i]),
        int(columns.richness[i]),
        providers,
        sets[codes[i]],
        columns.sources[columns.source[i]],
    )


def test_the_columns_hold_each_texts_facts_for_everyone_or_one_person(project):
    every = read_text_columns(project.layout)
    assert every.n == 5
    assert _facts(every, "t1") == (2020, False, 1, ["openalex"], ["en"], "openalex")
    assert _facts(every, "t2") == (2021, False, 0, ["openalex"], ["fr"], "openalex")
    assert _facts(every, "t3") == (None, False, 1, ["hal"], ["en", "fr"], "hal")
    assert _facts(every, "t4")[1] is True  # its published version is in the tables
    assert _facts(every, "t5")[2] == 2
    by_person = {
        p: sorted(every.tid(r) for r in rows) for p, rows in every.texts_by_person().items()
    }
    assert by_person == {"p1": ["t1", "t5"], "p2": ["t1", "t2"], "p3": ["t3"]}
    # One person's texts: the same facts, and only their authorships.
    mine = read_text_columns(project.layout, people=["p2"])
    assert sorted(mine.tid(i) for i in range(mine.n)) == ["t1", "t2", "t4"]
    for tid in ("t1", "t2", "t4"):
        assert _facts(mine, tid) == _facts(every, tid)
    assert mine.person_ids == ["p2"]
    assert {p: sorted(mine.tid(r) for r in rows) for p, rows in mine.texts_by_person().items()} == {
        "p2": ["t1", "t2"]
    }


def test_the_view_filters_sorts_and_pages_the_texts(project):
    view = texts_view(project)
    assert view.n == 5 and (project.layout.cache / "views").is_dir()
    assert view.counts()["content"] == {"title": 2, "abstract": 2, "full": 1}
    rows, total = view.page(None, "-year", 0, 10)
    years = [r["year"] for r in view.rows(rows, {})]
    assert total == 5 and years == [None, 2022, 2021, 2020, 2019]  # a missing year: last, reversed
    rows, total = view.page(view.select(language="fr"), "title", 0, 10)
    assert [r["text_id"] for r in view.rows(rows, {})] == ["t2", "t3"]
    assert view.select(q="TITLE T5").sum() == 1 and view.select(provider="hal").sum() == 1
    mask = view.of_people(["p2"])
    assert sorted(np.flatnonzero(mask).tolist()) == [0, 1, 3]
    row = view.rows(np.array([2]), {"t3": "t1"})[0]
    assert row["copy_of"] == "t1" and row["languages"] == ["en", "fr"] and row["people"] == 1
    # Opened again from the cache: the same view.
    again = texts_view(project)
    assert again.languages == view.languages and np.array_equal(again.people, view.people)
