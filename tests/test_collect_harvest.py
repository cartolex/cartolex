# SPDX-License-Identifier: MIT
"""Harvest details: parts and languages, ranks, the year window, cut pages, cancel, re-harvests."""

from __future__ import annotations

import pytest
from _collect_world import client, confirm_truth, demo_project, world_ids

from cartolex.collect.harvest import harvest
from cartolex.collect.http import Cancelled, IncompleteResults
from cartolex.collect.resolve import confirm
from cartolex.collect.tables import raw_folder
from cartolex.demo import generate
from cartolex.demo.services import DemoServices
from cartolex.project.layout import SOURCE_TABLES
from cartolex.project.tables import read_source_table


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture()
def confirmed(services, tmp_path):
    services.faults.clear()
    bib = services.bibliography
    project = demo_project(tmp_path / "p", bib)
    ids = world_ids(project, bib)
    confirm_truth(project, bib, ids)
    yield project, bib, ids
    project.close()


def _table(project, name: str) -> list[dict]:
    return read_source_table(project.layout.table(name), name).to_pylist()


def _bytes(project) -> dict[str, bytes]:
    return {n: project.layout.table(n).read_bytes() for n in SOURCE_TABLES}


def test_texts_have_their_parts_languages_and_types(confirmed, services) -> None:
    project, bib, ids = confirmed
    harvest(project, client(services, project))
    texts = {t["text_id"]: t for t in _table(project, "texts")}
    parts = _table(project, "text_parts")
    by_title = {w.title: w for w in bib.world.works}
    for p in parts:
        world = by_title[texts[p["text_id"]]["title"]]
        assert p["language"] == world.language and p["provider"] == "openalex"
        if p["part"] == "abstract":
            assert p["content"] == " ".join(world.abstract.split())
    kinds = {t["doc_type"] for t in texts.values()}
    assert "communication" in kinds and "proceedings" not in kinds
    assert all(t["ids"] and t["ids"][0][0] == "openalex" for t in texts.values())
    assert {t["source"] for t in texts.values()} <= {"openalex", "orcid"}


def test_authorships_carry_rank_last_and_corresponding(confirmed, services) -> None:
    project, bib, ids = confirmed
    harvest(project, client(services, project))
    texts = {t["text_id"]: t for t in _table(project, "texts")}
    for a in _table(project, "authorships"):
        n = texts[a["text_id"]]["n_authors"]
        assert 1 <= a["position"] <= n
        if n < 4:
            assert a["last"] is (a["position"] == n)
    flagged = [a for a in _table(project, "authorships") if a["corresponding"] is not None]
    assert flagged and any(a["corresponding"] for a in flagged)
    assert all(a["orgs"] for a in _table(project, "authorships"))


def test_the_year_window_is_kept(confirmed, services) -> None:
    project, _bib, _ids = confirmed
    harvest(project, client(services, project), years=(2020, 2024))
    years = {t["year"] for t in _table(project, "texts")}
    assert years and min(years) >= 2020 and max(years) <= 2024


def test_a_page_cut_short_stops_the_harvest_and_changes_nothing(confirmed, services) -> None:
    project, _bib, _ids = confirmed
    before = _bytes(project)
    services.faults.add("cut_page", service="openalex", path=r"^works\?", times=None)
    with pytest.raises(IncompleteResults):
        harvest(project, client(services, project))
    services.faults.clear()
    assert _bytes(project) == before
    assert not (raw_folder(project.layout, "collected") / "openalex").exists() or not list(
        (raw_folder(project.layout, "collected") / "openalex").glob("*.jsonl")
    )


def test_a_cancel_keeps_the_people_harvested_before_it(confirmed, services) -> None:
    project, bib, ids = confirmed
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 12

    with pytest.raises(Cancelled, match="their works are kept"):
        harvest(project, client(services, project, cancel=cancel))
    partial = {a["person_id"] for a in _table(project, "authorships")}
    assert partial
    harvest(project, client(services, project))
    complete = {a["person_id"] for a in _table(project, "authorships")}
    assert partial < complete
    # What the cancelled job cached is whole: harvesting again offline gives the same tables.
    before = _bytes(project)
    harvest(project, client(services, project, mode="cache_only"))
    assert _bytes(project) == before


def test_a_new_harvest_of_a_person_replaces_their_earlier_one(confirmed, services) -> None:
    project, bib, ids = confirmed
    split = next(p for p, w in ids.items() if w == bib.specials["split"])
    http = client(services, project)
    harvest(project, http)
    ids_before = {t["title"]: t["text_id"] for t in _table(project, "texts")}
    mine = lambda: {a["text_id"] for a in _table(project, "authorships") if a["person_id"] == split}  # noqa: E731
    both = mine()
    main = f"openalex:{bib.world.person(bib.specials['split']).openalex_id}"
    confirm(project, split, [main])
    harvest(project, http, people=[split])
    fewer = mine()
    assert fewer < both
    after = {t["title"]: t["text_id"] for t in _table(project, "texts")}
    assert all(ids_before[t] == i for t, i in after.items() if t in ids_before)


def test_a_harvest_sends_identifiers_never_names(confirmed, services) -> None:
    project, _bib, _ids = confirmed
    http = client(services, project)
    harvest(project, http)
    sent = {e["service"]: set(e["sends"]) for e in http.egress.summary()}
    assert sent["openalex"] <= {"identifier", "DOI"}
    assert sent["orcid"] == {"identifier"}
