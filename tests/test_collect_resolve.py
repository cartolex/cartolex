# SPDX-License-Identifier: MIT
"""Resolution scenarios on the demo services: each rule learnt in real use has its test."""

from __future__ import annotations

import pytest
from _collect_world import client, demo_project, identities, world_ids

from cartolex.collect.harvest import harvest
from cartolex.collect.openalex import search_authors
from cartolex.collect.resolve import (
    THRESHOLD,
    confirm,
    confirm_none,
    confirm_pasted,
    parse_record,
    resolve,
)
from cartolex.demo import generate
from cartolex.demo.services import DemoServices
from cartolex.project.tables import read_source_table


@pytest.fixture(scope="module")
def services():
    with DemoServices(generate("XS", 0)) as svc:
        yield svc


@pytest.fixture(scope="module")
def resolved(services, tmp_path_factory):
    """One resolution with automatic acceptance over the whole list."""
    bib = services.bibliography
    project = demo_project(tmp_path_factory.mktemp("resolve") / "p", bib)
    ids = world_ids(project, bib)
    report = resolve(project, client(services, project), auto=True)
    by_special = {}
    for res in report.resolutions:
        special = bib.truth[ids[res.person_id]].special
        if special:
            by_special[special] = res
    yield project, bib, ids, report, by_special
    project.close()


@pytest.fixture()
def fresh(services, tmp_path):
    bib = services.bibliography
    project = demo_project(tmp_path / "p", bib)
    yield project, bib, world_ids(project, bib)
    project.close()


def _pid(ids: dict[str, str], bib, special: str) -> str:
    return next(p for p, w in ids.items() if w == bib.specials[special])


def _records(res) -> list[str]:
    return [c.record for c in res.candidates]


def _texts_of(project, pid: str) -> list[dict]:
    layout = project.layout
    mine = {
        a["text_id"]
        for a in read_source_table(layout.table("authorships"), "authorships").to_pylist()
        if a["person_id"] == pid
    }
    return [
        t
        for t in read_source_table(layout.table("texts"), "texts").to_pylist()
        if t["text_id"] in mine
    ]


# ── affiliation ranks, never filters ──


def test_the_affiliation_trap_keeps_the_right_person(resolved, services) -> None:
    _project, bib, _ids, _report, by = resolved
    res = by["trap"]
    person = bib.world.person(bib.specials["trap"])
    right = f"openalex:{person.openalex_id}"
    twin = next(
        f"openalex:{a.id}"
        for a in bib.authors.values()
        if a.display_name == bib.authors[person.openalex_id].display_name and a.person_id is None
    )
    assert right in _records(res) and twin in _records(res)
    assert res.status == "pending"
    # Filtering by the lab's own institution record would have kept the homonym only.
    http = client(services)
    lab = bib.lab_of[person.group]
    full = f"{person.first_name} {person.last_name}"
    only = [
        short["id"].rsplit("/", 1)[-1]
        for short in search_authors(http, full, institution_ids=[lab])
    ]
    assert only == [twin.split(":")[1]]


def test_a_lab_alone_still_ranks_the_right_person(services, tmp_path) -> None:
    bib = services.bibliography
    rows = [dict(r, institution="") for r in bib.people_rows()]
    project = demo_project(tmp_path / "p", bib, rows)
    ids = world_ids(project, bib)
    pid = _pid(ids, bib, "trap")
    report = resolve(project, client(services, project), people=[pid])
    (res,) = report.resolutions
    person = bib.world.person(bib.specials["trap"])
    right = next(c for c in res.candidates if c.record == f"openalex:{person.openalex_id}")
    assert right.score >= THRESHOLD
    assert any("belongs to" in why for why, _ in right.evidence)
    project.close()


def test_homonyms_are_told_apart(resolved) -> None:
    _project, bib, ids, _report, by = resolved
    res = by["homonym"]
    truth = bib.truth[bib.specials["homonym"]]
    assert res.status == "auto" and tuple(res.records) == truth.records
    first, second = res.candidates[:2]
    assert first.name == second.name and first.score > second.score
    assert first.institutions != second.institutions and first.topics != second.topics


# ── the registry separates a merged record ──


def test_the_mixed_record_is_separated_through_the_registry(resolved, services, fresh) -> None:
    _project, bib, _ids, _report, by = resolved
    res = by["mixed"]
    person = bib.world.person(bib.specials["mixed"])
    assert res.status == "pending"
    assert any("may mix two people" in n for n in res.notes)
    (count,) = [r for r in res.registry if r.orcid == person.orcid]
    assert count.with_doi == len(bib.registry[person.orcid].works)

    project, bib, ids = fresh
    pid = _pid(ids, bib, "mixed")
    http = client(services, project)
    confirm(project, pid, [f"openalex:{person.openalex_id}"])
    harvest(project, http, people=[pid])
    through_record = {t["title"] for t in _texts_of(project, pid)}
    confirm(project, pid, [f"orcid:{person.orcid}"])
    harvest(project, http, people=[pid])
    through_registry = {t["title"] for t in _texts_of(project, pid)}
    foreign = {w.title for w in bib.works.values() if w.world_work is None}
    declared = {w.title for w in bib.registry[person.orcid].works}
    indexed = {w.title for w in bib.works.values() if w.doi}
    assert through_record & foreign, "the index record mixes another person's works in"
    assert not through_registry & foreign
    assert through_registry == declared & indexed


# ── several records, none, pasted ids ──


def test_several_records_are_united(resolved, services, fresh) -> None:
    _project, bib, _ids, _report, by = resolved
    res = by["split"]
    person = bib.world.person(bib.specials["split"])
    records = set(bib.truth[bib.specials["split"]].records)
    assert {r for r in records if r.startswith("openalex:")} <= set(_records(res))
    assert res.status == "pending"
    assert any("another record may hold them" in n for n in res.notes)
    initials = next(c for c in res.candidates if c.name.startswith(f"{person.first_name[0]}."))
    assert any(f.startswith(f"name « {person.first_name[0]} ") for f in initials.found_by)

    project, bib, ids = fresh
    pid = _pid(ids, bib, "split")
    confirm(project, pid, sorted(r for r in records if r.startswith("openalex:")))
    assert identities(project)[pid]["records"].count("openalex:") == 2
    harvest(project, client(services, project), people=[pid])
    texts = _texts_of(project, pid)
    world = {w.work_id for w in bib.world.works_of(person.person_id) if "openalex" in w.sources}
    assert len(texts) == len(world)  # the copy without its DOI joined its original
    assert len({t["title"] for t in texts}) == len(texts)


def test_none_is_an_answer(resolved, fresh, services) -> None:
    _project, _bib, _ids, _report, by = resolved
    assert by["none"].status == "pending" and not by["none"].candidates
    assert any("no record found" in n for n in by["none"].notes)
    project, bib, ids = fresh
    pid = _pid(ids, bib, "none")
    confirm_none(project, pid)
    assert identities(project)[pid]["identity"] == "none"
    report = harvest(project, client(services, project), people=[pid])
    assert report.people == 0


def test_a_pasted_id_is_confirmed(fresh, services) -> None:
    project, bib, ids = fresh
    http = client(services, project)
    pid = next(iter(ids))
    person = bib.world.person(ids[pid])
    assert confirm_pasted(project, http, pid, f"https://openalex.org/{person.openalex_id}") == (
        f"openalex:{person.openalex_id}"
    )
    assert identities(project)[pid]["identity"] == "confirmed"
    orcid = next(o for o, r in bib.registry.items() if r.works)
    assert confirm_pasted(project, http, pid, f"https://orcid.org/{orcid}") == f"orcid:{orcid}"
    with pytest.raises(ValueError, match="does not exist"):
        confirm_pasted(project, http, pid, "A9990000001")
    with pytest.raises(ValueError, match="holds no"):
        confirm_pasted(project, http, pid, "a name, not an id")
    assert parse_record("orcid:0000-0002-1825-0097") == "orcid:0000-0002-1825-0097"
    assert parse_record("0000-0002-1825-0097") == "orcid:0000-0002-1825-0097"
    assert parse_record("openalex:https://openalex.org/A9991234567") == "openalex:A9991234567"


# ── automatic acceptance ──


def test_a_single_clear_match_is_accepted_and_flagged(resolved) -> None:
    project, bib, ids, report, _by = resolved
    autos = [r for r in report.resolutions if r.status == "auto"]
    assert autos
    decisions = identities(project)
    for res in autos:
        assert [c.score >= THRESHOLD for c in res.candidates].count(True) == 1
        row = decisions[res.person_id]
        assert row["identity"] == "auto" and "to review" in row["note"]
        assert row["records"] == ";".join(res.records)


def test_two_candidates_above_the_threshold_wait(resolved) -> None:
    project, bib, ids, _report, by = resolved
    res = by["trap"]
    assert [c.score >= THRESHOLD for c in res.candidates].count(True) == 2
    assert identities(project)[res.person_id]["identity"] == "pending"


def test_name_variants_find_compound_and_accented_names(resolved) -> None:
    _project, bib, _ids, _report, by = resolved
    for special in ("compound", "diacritics"):
        res = by[special]
        person = bib.world.person(bib.specials[special])
        right = next(c for c in res.candidates if c.record == f"openalex:{person.openalex_id}")
        listed = bib.truth[person.person_id]
        exact = f"name « {listed.first_name} {listed.last_name} »"
        assert right.found_by and exact not in right.found_by, special


def test_candidates_show_their_evidence(resolved) -> None:
    _project, _bib, _ids, report, _by = resolved
    cand = next(c for r in report.resolutions for c in r.candidates if c.institutions)
    assert cand.works > 0 and cand.first_year <= cand.last_year
    assert cand.topics and cand.found_by
    inst = cand.institutions[0]
    assert inst["name"] and inst["first_year"] <= inst["last_year"]
    assert cand.score == pytest.approx(max(0.0, min(1.0, sum(p for _, p in cand.evidence))))
    assert "score:" in cand.describe()


def test_an_orcid_in_the_list_counts(resolved) -> None:
    _project, bib, ids, report, _by = resolved
    listed = {
        (r["last_name"], r["first_name"]): r["orcid"] for r in bib.people_rows() if r["orcid"]
    }
    seen = 0
    for res in report.resolutions:
        truth = bib.truth[ids[res.person_id]]
        orcid = listed.get((truth.last_name, truth.first_name))
        for cand in res.candidates:
            if orcid and cand.orcid == orcid:
                assert ("the same ORCID", 0.4) in cand.evidence
                assert "the ORCID in the list" in cand.found_by
                seen += 1
    assert seen


def test_decided_people_are_left_alone(fresh, services) -> None:
    project, bib, ids = fresh
    pid = next(iter(ids))
    confirm_none(project, pid)
    report = resolve(project, client(services, project), auto=True)
    assert pid not in {r.person_id for r in report.resolutions}
    assert report.skipped >= 1
    named = resolve(project, client(services, project), auto=True, people=[pid])
    assert [r.person_id for r in named.resolutions] == [pid]
    assert identities(project)[pid]["identity"] == "none"  # candidates shown, decision kept
